"""线路 B 机器人本机服务一键启动与无动作网页验收。"""

from __future__ import annotations

import argparse
import json
import os
import shlex
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

from apps.line_b_web.robot_service import (
    DEFAULT_PORT,
    SERVICE,
    _paths,
    _read_environment,
    access_urls,
    interface_ipv4_addresses,
)


PROJECT = Path(__file__).resolve().parents[2]
LOWLEVEL_CHECK = Path("/home/run/q1_lowlevel_check.py")
COMMAND_TOPIC = "/aima/hal/joint/command"
CONFIRMATION = "我已确认线路B人工安全条件"


class CheckFailed(RuntimeError):
    pass


def ensure_robot_environment() -> None:
    """Load ROS 2/aimdk environment once before importing rclpy."""
    if os.environ.get("Q1_LINE_B_START_CHECK_ENV") == "1":
        return
    setups = (Path("/opt/ros/humble/setup.bash"), Path("/robot/software/interface/local_setup.bash"))
    if not all(path.is_file() for path in setups):
        raise CheckFailed("缺少机器人 ROS 2 / PrimeBot 环境")
    argv = [sys.executable, str(Path(sys.argv[0]).resolve()), *sys.argv[1:]]
    command = " && ".join(
        [*(f"source {shlex.quote(str(path))}" for path in setups),
         "export Q1_LINE_B_START_CHECK_ENV=1", f"exec {shlex.join(argv)}"]
    )
    os.execv("/bin/bash", ["/bin/bash", "-lc", command])


def systemctl(*args: str, check: bool = False) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["systemctl", "--user", *args],
        check=check,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
    )


def service_state() -> str:
    return systemctl("is-active", SERVICE).stdout.strip() or "unknown"


def status_url(token: str, port: int) -> str:
    return f"http://127.0.0.1:{port}/api/status?token={token}"


def fetch_json(url: str, timeout: float = 1.0) -> dict:
    with urllib.request.urlopen(url, timeout=timeout) as response:
        value = json.loads(response.read())
    if not isinstance(value, dict):
        raise CheckFailed("状态 API 没有返回 JSON 对象")
    return value


def validate_zero_action_status(status: dict) -> None:
    expected = {
        "route": "B",
        "backend": "real",
        "mode": "LYING",
        "control_online": False,
        "mobility_enabled": True,
        "dance_enabled": True,
    }
    bad = [f"{key}={status.get(key)!r}（需要 {value!r}）" for key, value in expected.items()
           if status.get(key) != value]
    for key in ("vx", "wz", "drive_input_axis", "drive_input_turn_axis"):
        try:
            if abs(float(status.get(key, 0.0))) > 1e-9:
                bad.append(f"{key}={status.get(key)!r}（需要 0）")
        except (TypeError, ValueError):
            bad.append(f"{key}={status.get(key)!r}（不是数值）")
    if status.get("drive_input_direction") not in (None, "stop"):
        bad.append(f"drive_input_direction={status.get('drive_input_direction')!r}（需要 stop）")
    if bad:
        raise CheckFailed("无动作状态不符合要求：" + "；".join(bad))


def wait_for_lying(token: str, port: int, timeout_s: float) -> dict:
    deadline = time.monotonic() + timeout_s
    last_error = "服务尚未响应"
    while time.monotonic() < deadline:
        state = service_state()
        if state in ("failed", "inactive"):
            detail = systemctl("status", SERVICE, "--no-pager").stdout.strip()
            raise CheckFailed(f"服务状态 {state}\n{detail[-2000:]}")
        try:
            status = fetch_json(status_url(token, port))
            if status.get("mode") == "LYING":
                validate_zero_action_status(status)
                return status
            if status.get("mode") not in (None, "STARTING"):
                raise CheckFailed(f"未发送任何动作时进入了意外状态 {status.get('mode')}")
            last_error = f"网页已响应，但仍为 {status.get('mode')}"
        except CheckFailed:
            raise
        except (OSError, urllib.error.URLError, json.JSONDecodeError) as exc:
            last_error = str(exc)
        time.sleep(0.2)
    raise CheckFailed(f"{timeout_s:.0f} 秒内未进入无动作 LYING：{last_error}")


def verify_http_page(token: str, port: int) -> None:
    root = f"http://127.0.0.1:{port}"
    with urllib.request.urlopen(f"{root}/?token={token}", timeout=2.0) as response:
        html = response.read().decode("utf-8")
    required = ("Q1 线路 B 控制", 'id="joystick"', "joystickDeadzone=.12")
    missing = [marker for marker in required if marker not in html]
    if missing:
        raise CheckFailed(f"手机网页内容不完整，缺少 {missing}")
    try:
        urllib.request.urlopen(f"{root}/api/status?token={token}x", timeout=2.0)
    except urllib.error.HTTPError as exc:
        if exc.code != 403:
            raise CheckFailed(f"错误令牌返回 HTTP {exc.code}，需要 403") from exc
    else:
        raise CheckFailed("错误令牌仍能读取状态 API")


def observe_command_topic(seconds: float) -> tuple[list[str], int]:
    """Observe the real command topic without publishing or calling services."""
    import rclpy
    from aimdk_msgs.msg import JointCommandArray
    from rclpy.qos import DurabilityPolicy, HistoryPolicy, QoSProfile, ReliabilityPolicy

    qos = QoSProfile(
        history=HistoryPolicy.KEEP_LAST,
        depth=20,
        reliability=ReliabilityPolicy.BEST_EFFORT,
        durability=DurabilityPolicy.VOLATILE,
    )
    rclpy.init()
    node = rclpy.create_node("q1_line_b_no_action_check")
    count = [0]

    def on_command(_msg) -> None:
        count[0] += 1

    subscription = node.create_subscription(JointCommandArray, COMMAND_TOPIC, on_command, qos)
    try:
        deadline = time.monotonic() + seconds
        while time.monotonic() < deadline:
            rclpy.spin_once(node, timeout_sec=0.05)
        publishers = sorted({info.node_name for info in node.get_publishers_info_by_topic(COMMAND_TOPIC)})
    finally:
        node.destroy_subscription(subscription)
        node.destroy_node()
        rclpy.shutdown()
    return publishers, count[0]


def stop_after_failed_check(token: str, port: int, ever_lying: bool) -> str:
    """Stop only when the script can prove no action was entered."""
    current = None
    try:
        current = fetch_json(status_url(token, port)).get("mode")
    except Exception:
        pass
    # Before the HTTP service appears, PrimeBot remains IDLE and reset() has not been called.
    if current == "LYING" or (current is None and not ever_lying):
        systemctl("stop", SERVICE)
        return "失败后已安全停止服务"
    return f"未自动停止：当前状态 {current!r}，请使用独立急停并人工处置"


def confirm_manual_safety(flag: bool) -> None:
    if flag:
        return
    print("\n请人工确认：标准平趴、稳定支撑、吊带保护、独立硬件急停可用。")
    if not sys.stdin.isatty():
        raise CheckFailed("非交互运行必须显式加 --confirm-manual-safety")
    answer = input(f"确认后输入“{CONFIRMATION}”：").strip()
    if answer != CONFIRMATION:
        raise CheckFailed("人工安全条件未确认")


def main() -> int:
    try:
        ensure_robot_environment()
        parser = argparse.ArgumentParser(description="线路 B 本机服务一键启动与无动作网页验收")
        parser.add_argument("--confirm-manual-safety", action="store_true",
                            help="确认标准平趴、稳定支撑、吊带和独立急停均已人工检查")
        parser.add_argument("--observe-seconds", type=float, default=3.0)
        parser.add_argument("--startup-timeout", type=float, default=30.0)
        args = parser.parse_args()
        if not 1.0 <= args.observe_seconds <= 10.0:
            parser.error("--observe-seconds 必须在 1..10 秒")
        if not 5.0 <= args.startup_timeout <= 60.0:
            parser.error("--startup-timeout 必须在 5..60 秒")

        if service_state() in ("active", "activating", "deactivating"):
            raise CheckFailed(f"{SERVICE} 已在运行；请勿重复执行启动验收")
        if not LOWLEVEL_CHECK.is_file():
            raise CheckFailed(f"缺少低层只读检查：{LOWLEVEL_CHECK}")
        print("[1/5] 运行低层只读软件门禁……", flush=True)
        result = subprocess.run([str(LOWLEVEL_CHECK), "--seconds", "3"], check=False)
        if result.returncode != 0:
            raise CheckFailed(f"低层只读门禁失败，退出码 {result.returncode}")
        confirm_manual_safety(args.confirm_manual_safety)

        _, env_file = _paths(Path.home())
        env = _read_environment(env_file)
        token = env.get("LINE_B_TOKEN", "")
        port = int(env.get("LINE_B_PORT", str(DEFAULT_PORT)))
        if not token:
            raise CheckFailed("机器人本机服务尚未安装或固定令牌缺失")

        print("[2/5] 启动机器人本机服务（不发送网页动作）……", flush=True)
        started = systemctl("start", SERVICE)
        if started.returncode != 0:
            raise CheckFailed(started.stdout.strip() or "systemctl start 失败")
        ever_lying = False
        try:
            status = wait_for_lying(token, port, args.startup_timeout)
            ever_lying = True
            print("[3/5] LYING / 零速度 / 网页离线门禁通过", flush=True)
            verify_http_page(token, port)
            print("[4/5] 网页、圆形摇杆资源和令牌鉴权通过", flush=True)
            publishers, messages = observe_command_topic(args.observe_seconds)
            status = fetch_json(status_url(token, port))
            validate_zero_action_status(status)
            expected_publisher = any(name.endswith("kabutack_primebot_pub") for name in publishers)
            if not expected_publisher:
                raise CheckFailed(f"未发现预期真机后端 Publisher，当前 {publishers or '无'}")
            if messages:
                raise CheckFailed(f"无动作观察期收到 {messages} 帧真实关节命令")
            print(f"[5/5] {args.observe_seconds:.1f} 秒无动作观察通过：Publisher={publishers}，消息=0", flush=True)
        except Exception as exc:
            cleanup = stop_after_failed_check(token, port, ever_lying)
            raise CheckFailed(f"{exc}；{cleanup}") from exc

        addresses = interface_ipv4_addresses()
        print("\n结论：机器人本机服务已启动，无动作网页连接验收通过。")
        print("服务保持运行；尚未执行摆姿检查、起身、行走或舞蹈。")
        for (name, _), url in zip(addresses, access_urls(token, port, addresses)):
            print(f"{name}: {url}")
        return 0
    except CheckFailed as exc:
        print(f"\n结论：一键启动/无动作验收失败：{exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
