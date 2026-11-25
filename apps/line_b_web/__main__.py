"""线路 B 专用手机网页。

默认只运行 MuJoCo；真机必须同时给 ``--backend real --enable-real``。端口默认 8768，
与线路 A 的 8767 分开。
"""

from __future__ import annotations

import argparse
import secrets
import signal
import sys
import time
from pathlib import Path

PROJECT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(PROJECT))

from apps import ref  # noqa: E402
from apps.line_b_web.control import LineBControl  # noqa: E402
from apps.line_b_web.machine import DAMPED, LYING, HandoffConfig, LineBMachine  # noqa: E402
from apps.line_b_web.server import open_server  # noqa: E402
from apps.teleop.command import ACC, BACK, FWD, TURN  # noqa: E402
from apps.teleop.recorder import Recorder  # noqa: E402
from apps.teleop.voice import SwitchableVoice  # noqa: E402
from runtime import MuJoCoBackend, Package  # noqa: E402

DEFAULT_BASE = PROJECT / "weights" / "dual_v3b"
DEFAULT_SKILL = PROJECT / "weights" / "getup_vpd_ft"
DEFAULT_DANCE = PROJECT / "weights" / "q1_smooth_dance"
DRY_TOPIC = "/kabutack/dry_run/joint/command"
FIRST_INCREASED_MAX_FORWARD = 0.30
FIRST_INCREASED_MAX_BACKWARD = 0.20
FIRST_INCREASED_MAX_TURN = 0.50
LOCOMOTION_PROFILE = "first_increased"
REC_REAL = Path("/robot/persist/log/q1_line_b_web/rec")
REC_SIM = PROJECT / "infra" / "jobs" / "line_b_web_rec"


def package(path: Path, label: str, needs_scene: bool = False) -> Package:
    required = ("deploy.yaml", "scene.xml") if needs_scene else ("deploy.yaml",)
    if any(not (path / name).exists() for name in required):
        raise SystemExit(f"{label}权重包不完整：{path}")
    return Package.load(path)


def status_of(machine: LineBMachine, max_forward: float, max_backward: float,
              max_turn: float, backend: str) -> dict:
    rows = dict(machine.status())
    out = {
        "route": "B",
        "mode": machine.mode,
        "message": machine.msg,
        "vx": float(machine.cmd[0]),
        "wz": float(machine.cmd[2]),
        "upright": float(machine.robot.upright()),
        "locomotion_profile": LOCOMOTION_PROFILE,
        "configured_max_forward": float(max_forward),
        "configured_max_backward": float(max_backward),
        "configured_max_turn": float(max_turn),
        "max_forward": float(machine.speed * FWD),
        "max_backward": float(machine.backward_speed * BACK),
        "max_turn": float(machine.turn_speed * TURN),
        "speed_level": int(machine.speed_level),
        "speed_factor": float(machine.speed_factor),
        "height_target": float(machine.height_target),
        "height_command": float(machine.cmd[3]),
        "voice_enabled": bool(machine.voice_enabled),
        "linear_accel": float(ACC[0]),
        "turn_accel": float(ACC[2]),
        "control_hz": float(1.0 / machine.dt),
        "dance_duration_s": float((machine.dance_ref.T - 1) * machine.dt),
        "backend": backend,
        "handoff_candidate_enabled": machine.handoff is not None,
        "pose_check": dict(machine.pose_check_result),
    }
    if "t" in rows:
        out["progress"] = rows["t"]
    if machine.handoff is not None:
        out["handoff"] = machine.handoff_metrics.as_dict()
    return out


def main() -> None:
    p = argparse.ArgumentParser(description="Q1 线路 B：摆姿检查 / 标准平趴起身 / 人工行走和转向 / 完整舞蹈 / 趴下")
    p.add_argument("--backend", choices=("sim", "real"), default="sim")
    p.add_argument("--enable-real", action="store_true", help="真机二次确认")
    p.add_argument("--dry-run", action="store_true", help=f"真机演练：命令只发到 {DRY_TOPIC}")
    p.add_argument("--package", type=Path, default=DEFAULT_BASE)
    p.add_argument("--skill", type=Path, default=DEFAULT_SKILL)
    p.add_argument("--dance", type=Path, default=DEFAULT_DANCE)
    p.add_argument("--getup-ref", default="getup_facedown")
    p.add_argument("--liedown-ref", default="liedown_facedown")
    p.add_argument("--host", default="0.0.0.0")
    p.add_argument("--port", type=int, default=8768)
    p.add_argument("--token", default=None)
    p.add_argument("--drive-timeout", type=float, default=0.25)
    p.add_argument("--heartbeat-timeout", type=float, default=1.0)
    p.add_argument("--max-forward", type=float, default=FIRST_INCREASED_MAX_FORWARD,
                   help=f"第一次提速候选的前进上限；不得超过 {FIRST_INCREASED_MAX_FORWARD:.2f} m/s")
    p.add_argument("--max-backward", type=float, default=FIRST_INCREASED_MAX_BACKWARD,
                   help=f"第一次提速候选的后退上限；不得超过 {FIRST_INCREASED_MAX_BACKWARD:.2f} m/s")
    p.add_argument("--max-turn", type=float, default=FIRST_INCREASED_MAX_TURN,
                   help=f"转向上限；不得超过 {FIRST_INCREASED_MAX_TURN:.2f} rad/s")
    p.add_argument("--sensor-timeout", type=float, default=10.0)
    p.add_argument("--voice-default", choices=("on", "off"), default="off",
                   help="语音提示的开机默认值；网页中可随时开关")
    p.add_argument("--record-root", type=Path, default=None,
                   help="运行记录根目录；真机/仿真各有默认持久目录")
    p.add_argument("--no-record", action="store_true", help="关闭逐周期运行记录和网页下载")
    p.add_argument("--sim-start-standing", action="store_true")
    p.add_argument("--experimental-sim-handoff", action="store_true",
                   help="仅 MuJoCo：启用 10 s 起身→零速行走交接候选；真机禁止")
    p.add_argument("--enable-manual-drive", action="store_true",
                   help="显式启用线路 B 按住前进/后退和转向；舞蹈由 --enable-dance 独立控制")
    p.add_argument("--enable-dance", action="store_true",
                   help="显式启用线路 B 完整舞蹈；必须与 --enable-manual-drive 同时使用")
    a = p.parse_args()

    if a.backend == "real" and not a.enable_real:
        raise SystemExit("线路 B 真机入口默认关闭；确认吊带、独立急停和低层模式后加 --enable-real")
    if a.backend == "sim" and a.dry_run:
        raise SystemExit("--dry-run 只用于 --backend real")
    if a.experimental_sim_handoff and a.backend != "sim":
        raise SystemExit("--experimental-sim-handoff 只允许 MuJoCo，真机和真机演练均禁止")
    if a.experimental_sim_handoff and a.sim_start_standing:
        raise SystemExit("交接实验必须从平趴起身开始，不能与 --sim-start-standing 同用")
    if a.experimental_sim_handoff and a.enable_manual_drive:
        raise SystemExit("--experimental-sim-handoff 与 --enable-manual-drive 不能同时使用")
    if a.enable_dance and not a.enable_manual_drive:
        raise SystemExit("--enable-dance 必须与 --enable-manual-drive 同时使用，确保先完成零速交接进入 WALK")
    if not 0 < a.max_forward <= FIRST_INCREASED_MAX_FORWARD:
        raise SystemExit(f"--max-forward 必须在 (0, {FIRST_INCREASED_MAX_FORWARD:.2f}] m/s")
    if not 0 < a.max_backward <= FIRST_INCREASED_MAX_BACKWARD:
        raise SystemExit(f"--max-backward 必须在 (0, {FIRST_INCREASED_MAX_BACKWARD:.2f}] m/s")
    if not 0 < a.max_turn <= FIRST_INCREASED_MAX_TURN:
        raise SystemExit(f"--max-turn 必须在 (0, {FIRST_INCREASED_MAX_TURN:.2f}] rad/s")

    base = package(a.package, "基础", needs_scene=True)
    skill = package(a.skill, "起身 / 趴下")
    dance = package(a.dance, "舞蹈")
    dance_ref = a.dance / "motion.npz"
    if not dance_ref.exists():
        raise SystemExit(f"舞蹈参考不存在：{dance_ref}")

    voice = SwitchableVoice("robot" if a.backend == "real" else "local", enabled=a.voice_default == "on")
    if a.backend == "real":
        from runtime import PrimeBotBackend
        from runtime.primebot_io import vendor_mc_running

        if vendor_mc_running() and not a.dry_run:
            raise SystemExit("厂商运控 mc 正在运行；必须先切换到「低层运控开发」")
        kwargs = {"cmd_topic": DRY_TOPIC, "ramp_track_lim": float("inf")} if a.dry_run else {}
        robot = PrimeBotBackend(base, **kwargs)
        robot.wait_sensors(a.sensor_timeout)
    else:
        robot = MuJoCoBackend(base)

    mobility_enabled = a.experimental_sim_handoff or a.enable_manual_drive
    dance_enabled = a.experimental_sim_handoff or a.enable_dance
    machine = LineBMachine(
        base,
        skill,
        robot,
        ref.find(a.getup_ref),
        ref.find(a.liedown_ref),
        dance,
        dance_ref,
        speed_scale=a.max_forward / FWD,
        backward_speed_scale=a.max_backward / BACK,
        turn_speed_scale=a.max_turn / TURN,
        voice=voice,
        experimental_handoff=HandoffConfig() if mobility_enabled else None,
        allow_real_manual_drive=a.backend == "real" and a.enable_manual_drive,
        dance_enabled=dance_enabled,
    )
    if a.backend == "sim" and a.sim_start_standing:
        machine.place_standing()

    token = a.token or secrets.token_urlsafe(9)
    control = LineBControl(a.drive_timeout, a.heartbeat_timeout,
                           mobility_enabled=mobility_enabled, dance_enabled=dance_enabled)
    recorder = None
    if not a.no_record:
        rec_root = a.record_root or (REC_REAL if a.backend == "real" else REC_SIM)
        recorder = Recorder(
            rec_root,
            base.joint_names,
            base.control_dt,
            {"base": base.root, "skill": skill.root, "dance": dance.root},
            {
                "route": "B", "backend": a.backend, "dry_run": bool(a.dry_run),
                "max_forward": a.max_forward, "max_backward": a.max_backward,
                "max_turn": a.max_turn, "voice_default": a.voice_default,
            },
        )
    server = open_server(control, a.host, a.port, token, logs=recorder)
    shown_host = "<机器人IP>" if a.host in ("0.0.0.0", "::") else a.host
    print(f"[line-b] 打开 http://{shown_host}:{server.server_port}/?token={token}", flush=True)
    if a.enable_manual_drive and a.enable_dance:
        dance_seconds = (machine.dance_ref.T - 1) * machine.dt
        mode_note = (f"人工行走、转向和完整舞蹈已启用（第一次提速配置；"
                     f"前进 ≤ {a.max_forward:.2f}、后退 ≤ {a.max_backward:.2f} m/s、"
                     f"转向 ≤ {a.max_turn:.2f} rad/s；"
                     f"舞蹈 {dance_seconds:.1f} s，只允许从 WALK 零速站稳后开始）")
    elif a.enable_manual_drive:
        mode_note = (f"人工行走和转向已启用（第一次提速配置；"
                     f"前进 ≤ {a.max_forward:.2f}、后退 ≤ {a.max_backward:.2f} m/s、"
                     f"转向 ≤ {a.max_turn:.2f} rad/s）；"
                     "舞蹈未启用")
    elif a.experimental_sim_handoff:
        mode_note = "仅 MuJoCo 交接实验已启用"
    else:
        mode_note = "事故锁定：行走/舞蹈关闭"
    print(f"[line-b] 独立端口 {server.server_port}；{mode_note}；不含线路 A 折叠动作", flush=True)
    print(f"[line-b] 网页断开或 {a.heartbeat_timeout:.2f}s 无心跳即速度归零并保持当前控制状态；"
          "只有显式急停才切阻尼，网页不能代替独立硬件急停", flush=True)

    def interrupt(signum, frame):
        raise KeyboardInterrupt

    signal.signal(signal.SIGINT, interrupt)
    signal.signal(signal.SIGTERM, interrupt)
    t_next = time.perf_counter()
    last_idle_record = 0.0
    exit_reason = "normal"
    try:
        while True:
            pad, edges = control.sample()
            machine.tick(pad, edges)
            if recorder is not None:
                now = time.monotonic()
                # 常驻服务在平趴/阻尼空闲时只每秒留一帧；有动作时仍完整记录 50 Hz。
                if machine.mode not in (LYING, DAMPED) or edges or now - last_idle_record >= 1.0:
                    recorder.step(machine, pad, edges)
                    last_idle_record = now
            control.set_status(status_of(machine, a.max_forward, a.max_backward, a.max_turn, a.backend))
            if a.backend == "sim":
                t_next += machine.dt
                time.sleep(max(0.0, t_next - time.perf_counter()))
    except KeyboardInterrupt:
        exit_reason = "signal"
        print("[line-b] 退出，切阻尼", flush=True)
    finally:
        server.shutdown()
        server.server_close()
        if recorder is not None:
            recorder.close(exit_reason)
        if a.backend == "real":
            robot.close()
        else:
            robot.damp()
        voice.close()


if __name__ == "__main__":
    main()
