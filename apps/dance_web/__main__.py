"""手机网页控制 Q1 的行走 / 舞蹈状态机。

默认只运行 MuJoCo 仿真。真机必须同时给 ``--backend real --enable-real``；首次联调再加
``--dry-run``，指令只发到假话题，电机不会接收。
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
from apps.teleop.command import FWD  # noqa: E402
from apps.dance_web.control import WebControl  # noqa: E402
from apps.dance_web.server import open_server  # noqa: E402
from apps.teleop.machine import Teleop  # noqa: E402
from apps.teleop.voice import open_voice  # noqa: E402
from runtime import MuJoCoBackend, Package  # noqa: E402

DEFAULT_BASE = PROJECT / "weights" / "dual_v3b"
DEFAULT_SKILL = PROJECT / "weights" / "getup_vpd_ft"
DEFAULT_DANCE = PROJECT / "weights" / "q1_smooth_dance"
DRY_TOPIC = "/kabutack/dry_run/joint/command"


def package(path: Path, label: str) -> Package:
    if not (path / "deploy.yaml").exists():
        raise SystemExit(f"{label}权重包不存在：{path}")
    return Package.load(path)


def status_of(tele: Teleop) -> dict:
    rows = dict(tele.status())
    out = {
        "mode": tele.mode,
        "message": tele.msg,
        "vx": float(tele.cmd[0]),
        "vy": float(tele.cmd[1]),
        "wz": float(tele.cmd[2]),
        "upright": float(tele.robot.upright()),
    }
    if tele.mode == "DANCE":
        out["dance_seconds"] = round(tele.i * tele.dt, 2)
    if "t" in rows:
        out["progress"] = rows["t"]
    return out


def main() -> None:
    p = argparse.ArgumentParser(description="Q1 行走 / 舞蹈手机网页")
    p.add_argument("--backend", choices=("sim", "real"), default="sim")
    p.add_argument("--enable-real", action="store_true", help="真机二次确认；--backend real 时必须给出")
    p.add_argument("--dry-run", action="store_true", help=f"真机演练：只发到 {DRY_TOPIC}")
    p.add_argument("--package", type=Path, default=DEFAULT_BASE)
    p.add_argument("--skill", type=Path, default=DEFAULT_SKILL)
    p.add_argument("--dance", type=Path, default=DEFAULT_DANCE)
    p.add_argument("--getup-ref", default="getup_facedown")
    p.add_argument("--liedown-ref", default="liedown_facedown")
    p.add_argument("--folded-skill", type=Path, default=None,
                   help="折叠趴卧→站立专用策略包；未给出时检测到折叠姿态会安全拒绝")
    p.add_argument("--folded-ref", type=Path, default=None,
                   help="折叠起身参考；缺省使用 <folded-skill>/motion.npz")
    p.add_argument("--host", default="0.0.0.0")
    p.add_argument("--port", type=int, default=8767)
    p.add_argument("--token", default=None, help="网页控制令牌；缺省每次启动随机生成")
    p.add_argument("--drive-timeout", type=float, default=0.5)
    p.add_argument("--safety-profile", choices=("off", "getup", "walk", "full"), default="off",
                   help="断联阻尼的真机安全配置：getup 只起身，walk 只前后低速，full 全功能")
    p.add_argument("--heartbeat-timeout", type=float, default=0.3, help="网页控制心跳超时后切阻尼")
    p.add_argument("--speed-scale", type=float, default=0.5)
    p.add_argument("--sensor-timeout", type=float, default=10.0)
    p.add_argument("--sim-start-standing", action="store_true", help="仿真直接从站姿开始，跳过起身")
    a = p.parse_args()

    if a.backend == "real" and not a.enable_real:
        raise SystemExit("真机入口默认关闭；确认吊带、急停与低层模式后显式加 --enable-real")
    if a.backend == "sim" and a.dry_run:
        raise SystemExit("--dry-run 只用于 --backend real")
    if a.backend == "real" and not a.dry_run and a.safety_profile == "off":
        raise SystemExit("真机网页控制必须选择 --safety-profile getup/walk/full，不允许关闭断联阻尼")
    if a.safety_profile == "walk":
        a.speed_scale = min(a.speed_scale, 0.08 / FWD)  # 首测前进上限 0.08 m/s

    base, skill, dance = package(a.package, "基础"), package(a.skill, "起身"), package(a.dance, "舞蹈")
    dance_ref = a.dance / "motion.npz"
    if not dance_ref.exists():
        raise SystemExit(f"舞蹈参考不存在：{dance_ref}")
    getup_ref, liedown_ref = ref.find(a.getup_ref), ref.find(a.liedown_ref)
    folded_skill = None
    folded_ref = a.folded_ref
    if a.folded_skill is not None:
        folded_skill = package(a.folded_skill, "折叠起身")
        folded_ref = folded_ref or a.folded_skill / "motion.npz"
        if not folded_ref.exists() or not (a.folded_skill / "policy.onnx").exists():
            raise SystemExit(f"折叠起身技能包不完整：{a.folded_skill}")
    elif folded_ref is not None:
        raise SystemExit("--folded-ref 必须与 --folded-skill 一起使用")
    voice = open_voice("off")

    if a.backend == "real":
        from runtime import PrimeBotBackend
        from runtime.primebot_io import vendor_mc_running

        if vendor_mc_running():
            if not a.dry_run:
                raise SystemExit("厂商运控 mc_app_main/mc_main 正在运行；不能并发启动低层控制器")
            print(f"[dance_web] 注意：厂商 MC 正在运行；dry-run 只发布到假话题 {DRY_TOPIC}，不会写电机话题。", flush=True)
        robot = PrimeBotBackend(base, **({"cmd_topic": DRY_TOPIC, "ramp_track_lim": float("inf")} if a.dry_run else {}))
        robot.wait_sensors(a.sensor_timeout)
    else:
        robot = MuJoCoBackend(base)

    tele = Teleop(base, skill, robot, getup_ref, liedown_ref, speed_scale=a.speed_scale,
                  voice=voice, dance=dance, dance_ref=dance_ref,
                  folded_skill=folded_skill, folded_ref=folded_ref)
    if a.backend == "sim" and a.sim_start_standing:
        tele.place_standing()

    token = a.token or secrets.token_urlsafe(9)
    control = WebControl(a.drive_timeout, a.safety_profile, a.heartbeat_timeout)
    server = open_server(control, a.host, a.port, token)
    shown_host = "<本机或机器人IP>" if a.host in ("0.0.0.0", "::") else a.host
    print(f"[dance_web] 打开 http://{shown_host}:{server.server_port}/?token={token}", flush=True)
    print("[dance_web] 正常停止会减速归零；阻尼是独立处置。网页不是独立硬件急停。", flush=True)
    if control.safety_enabled:
        print(f"[dance_web] 安全配置 {a.safety_profile}：网页断开或 {a.heartbeat_timeout:.2f}s 无心跳即切阻尼。", flush=True)

    def interrupt(signum, frame):
        raise KeyboardInterrupt

    signal.signal(signal.SIGINT, interrupt)
    signal.signal(signal.SIGTERM, interrupt)
    t_next = time.perf_counter()
    try:
        while True:
            pad, edges = control.sample()
            tele.tick(pad, edges)
            control.set_status(status_of(tele))
            if a.backend == "sim":
                t_next += tele.dt
                time.sleep(max(0.0, t_next - time.perf_counter()))
    except KeyboardInterrupt:
        print("[dance_web] 退出，切阻尼", flush=True)
    finally:
        server.shutdown()
        if a.backend == "real":
            robot.close()
        else:
            robot.damp()
        voice.close()


if __name__ == "__main__":
    main()
