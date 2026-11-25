"""Independent Q1 mobile web: verified continuous walking plus 4x dance."""

from __future__ import annotations

import argparse
from pathlib import Path
import secrets
import signal
import sys
import time

PROJECT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(PROJECT))

from apps import ref  # noqa: E402
from apps.line_b_web.__main__ import status_of  # noqa: E402
from apps.line_b_web.control import LineBControl  # noqa: E402
from apps.line_b_web.machine import DAMPED, LYING, HandoffConfig, LineBMachine  # noqa: E402
from apps.line_b_web.server import open_server  # noqa: E402
from apps.new_motion_web.gate import BUNDLE_ROOT, DANCE_ROOT, validate_real_gate  # noqa: E402
from apps.new_motion_web.web import index_html  # noqa: E402
from apps.teleop.command import BACK, FWD, TURN  # noqa: E402
from apps.teleop.recorder import Recorder  # noqa: E402
from apps.teleop.voice import SwitchableVoice  # noqa: E402
from runtime import MuJoCoBackend, Package  # noqa: E402


DRY_TOPIC = "/kabutack/dry_run/new_motion/joint/command"
REC_REAL = Path("/robot/persist/log/q1_new_motion_web/rec")
REC_SIM = PROJECT / "infra/jobs/new_motion_web_rec"
MAX_FORWARD = 0.30
MAX_BACKWARD = 0.20
MAX_TURN = 0.50
DANCE_TARGET_STEP_MAX = 0.05


def load_package(path: Path, label: str, scene: bool = False) -> Package:
    required = ["deploy.yaml"] + (["scene.xml"] if scene else [])
    missing = [name for name in required if not (path / name).is_file()]
    if missing:
        raise SystemExit(f"{label}包不完整 {path}：缺少 {missing}")
    return Package.load(path)


def main() -> None:
    parser = argparse.ArgumentParser(description="Q1 独立 4 倍速舞蹈 + 连续摇杆行走网页")
    parser.add_argument("--backend", choices=("sim", "real"), default="sim")
    parser.add_argument("--enable-real", action="store_true")
    parser.add_argument("--dry-run", action="store_true", help=f"只发布到假话题 {DRY_TOPIC}")
    parser.add_argument("--release", type=Path, required=True)
    parser.add_argument("--dynamic-report", type=Path, required=True)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8769)
    parser.add_argument("--token", default=None)
    parser.add_argument("--sensor-timeout", type=float, default=10.0)
    parser.add_argument("--drive-timeout", type=float, default=0.25)
    parser.add_argument("--heartbeat-timeout", type=float, default=1.0)
    parser.add_argument("--voice-default", choices=("on", "off"), default="off")
    parser.add_argument("--record-root", type=Path, default=None)
    parser.add_argument("--no-record", action="store_true")
    parser.add_argument("--sim-start-standing", action="store_true")
    args = parser.parse_args()

    if args.backend == "real" and not args.enable_real:
        raise SystemExit("真机入口默认关闭；需要 --backend real --enable-real")
    if args.backend == "sim" and args.dry_run:
        raise SystemExit("--dry-run 只允许与 --backend real 一起使用")

    release = args.release.resolve()
    gate = validate_real_gate(
        release,
        args.dynamic_report.resolve(),
        PROJECT / "apps/line_b_web/machine.py",
    )
    base = load_package(release / BUNDLE_ROOT / "weights/dual_v3b", "基础行走", scene=True)
    skill = load_package(release / BUNDLE_ROOT / "weights/getup_vpd_ft", "起身/趴下")
    dance_root = release / DANCE_ROOT
    dance = load_package(dance_root, "4 倍速舞蹈")
    dance_ref = dance_root / "motion.npz"
    voice = SwitchableVoice("robot" if args.backend == "real" else "local",
                            enabled=args.voice_default == "on")

    if args.backend == "real":
        from runtime import PrimeBotBackend
        from runtime.primebot_io import vendor_mc_running

        if vendor_mc_running() and not args.dry_run:
            raise SystemExit("厂商运控 mc 正在运行；必须先切换到低层运控开发")
        kwargs = {"cmd_topic": DRY_TOPIC, "ramp_track_lim": float("inf")} if args.dry_run else {}
        robot = PrimeBotBackend(base, **kwargs)
        robot.wait_sensors(args.sensor_timeout)
    else:
        robot = MuJoCoBackend(base)

    machine = LineBMachine(
        base, skill, robot,
        ref.find("getup_facedown"), ref.find("liedown_facedown"),
        dance, dance_ref,
        speed_scale=MAX_FORWARD / FWD,
        backward_speed_scale=MAX_BACKWARD / BACK,
        turn_speed_scale=MAX_TURN / TURN,
        voice=voice,
        experimental_handoff=HandoffConfig(),
        allow_real_manual_drive=args.backend == "real",
        dance_enabled=True,
        dance_target_step_max=DANCE_TARGET_STEP_MAX,
    )
    if args.backend == "sim" and args.sim_start_standing:
        machine.place_standing()

    token = args.token or secrets.token_urlsafe(18)
    control = LineBControl(args.drive_timeout, args.heartbeat_timeout,
                           mobility_enabled=True, dance_enabled=True)
    recorder = None
    if not args.no_record:
        record_root = args.record_root or (REC_REAL if args.backend == "real" else REC_SIM)
        recorder = Recorder(
            record_root, base.joint_names, base.control_dt,
            {"base": base.root, "skill": skill.root, "dance": dance.root},
            {"route": "Q1_4X", "backend": args.backend, "dry_run": bool(args.dry_run),
             "max_forward": MAX_FORWARD, "max_backward": MAX_BACKWARD, "max_turn": MAX_TURN,
             "dance_target_step_max": DANCE_TARGET_STEP_MAX},
        )
    server = open_server(control, args.host, args.port, token, logs=recorder, index_html=index_html())
    shown_host = "<机器人IP>" if args.host in ("0.0.0.0", "::") else args.host
    print(f"[new-motion] 打开 http://{shown_host}:{server.server_port}/?token={token}", flush=True)
    print(f"[new-motion] 连续行走=dual_v3b；新舞蹈=Q1 4x；舞蹈步长上限={DANCE_TARGET_STEP_MAX:.3f} rad", flush=True)
    print("[new-motion] 4 倍速舞蹈约 30.1 秒；原线路 B 代码与端口保留", flush=True)

    def interrupt(signum, frame):
        raise KeyboardInterrupt

    signal.signal(signal.SIGINT, interrupt)
    signal.signal(signal.SIGTERM, interrupt)
    next_tick = time.perf_counter()
    last_idle_record = 0.0
    exit_reason = "normal"
    try:
        while True:
            pad, edges = control.sample()
            machine.tick(pad, edges)
            if recorder is not None:
                now = time.monotonic()
                if machine.mode not in (LYING, DAMPED) or edges or now - last_idle_record >= 1.0:
                    recorder.step(machine, pad, edges)
                    last_idle_record = now
            status = status_of(machine, MAX_FORWARD, MAX_BACKWARD, MAX_TURN, args.backend)
            status.update(
                controller_variant="Q1_4X",
                activation_gate=gate,
                dance_target_step_max_rad=DANCE_TARGET_STEP_MAX,
            )
            control.set_status(status)
            if args.backend == "sim":
                next_tick += machine.dt
                time.sleep(max(0.0, next_tick - time.perf_counter()))
    except KeyboardInterrupt:
        exit_reason = "signal"
        print("[new-motion] 退出，切阻尼", flush=True)
    finally:
        server.shutdown()
        server.server_close()
        if recorder is not None:
            recorder.close(exit_reason)
        robot.close() if args.backend == "real" else robot.damp()
        voice.close()


if __name__ == "__main__":
    main()
