"""安装和管理线路 B 的机器人本机手机网页服务。

这是一次安装后不依赖 Mac 的入口：HTTP、状态机、推理和 ROS 2 控制都运行在
机器人本机。手机只需与机器人处在同一可互访热点，访问机器人的 Wi-Fi IPv4。

安全约束：可选择每次开机先执行只读低层门禁，通过后常驻。安装命令
本身不会立即启动服务；服务异常退出后不自动重启。
"""

from __future__ import annotations

import argparse
import json
import os
import secrets
import subprocess
import sys
import urllib.error
import urllib.request
from pathlib import Path


SERVICE = "q1-line-b-web.service"
CONFIG_DIRNAME = "q1-line-b-web"
DEFAULT_PORT = 8768
PROJECT = Path(__file__).resolve().parents[2]


def _systemd_path(value: str | Path) -> str:
    """Validate a robot path for directives parsed by the Q1 systemd version."""
    text = str(value)
    if "\n" in text or "\r" in text or "\0" in text:
        raise ValueError("systemd 路径含非法字符")
    if not text.startswith("/") or any(ch.isspace() for ch in text):
        raise ValueError("systemd 路径必须是无空白的绝对路径")
    return text


def _environment_quote(value: str | Path) -> str:
    text = str(value)
    if "\n" in text or "\r" in text or "\0" in text:
        raise ValueError("环境变量含非法字符")
    return '"' + text.replace("\\", "\\\\").replace('"', '\\"') + '"'


def unit_text(project: Path, runner: Path, env_file: Path, preflight: Path | None = None) -> str:
    preflight = preflight or Path("/home/run/q1_line_b_boot_preflight.py")
    return f"""[Unit]
Description=Q1 Line B robot-hosted mobile control
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
WorkingDirectory={_systemd_path(project)}
EnvironmentFile={_systemd_path(env_file)}
ExecStartPre={_systemd_path(preflight)}
ExecStart=/bin/bash {_systemd_path(runner)}
Restart=no
KillSignal=SIGTERM
TimeoutStartSec=120
TimeoutStopSec=10
CPUAffinity=6
CPUSchedulingPolicy=fifo
CPUSchedulingPriority=60

[Install]
WantedBy=default.target
"""


def environment_text(project: Path, token: str, port: int) -> str:
    if not token or any(ch not in "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-_" for ch in token):
        raise ValueError("令牌必须是非空 URL-safe 字符串")
    if not 1 <= int(port) <= 65535:
        raise ValueError("端口必须在 1..65535")
    return (
        f"LINE_B_PROJECT={_environment_quote(project)}\n"
        f"LINE_B_TOKEN={token}\n"
        f"LINE_B_PORT={int(port)}\n"
        "LINE_B_LOG_DIR=/tmp/q1-line-b-web/ros\n"
    )


def _paths(home: Path) -> tuple[Path, Path]:
    return (
        home / ".config" / "systemd" / "user" / SERVICE,
        home / ".config" / CONFIG_DIRNAME / "environment",
    )


def _write_atomic(path: Path, content: str, mode: int) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(content)
    tmp.chmod(mode)
    os.replace(tmp, path)


def _read_environment(env_file: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    if not env_file.exists():
        return values
    for raw in env_file.read_text().splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] == '"':
            value = value[1:-1].replace('\\"', '"').replace("\\\\", "\\")
        values[key.strip()] = value
    return values


def _run_systemctl(*args: str, check: bool = True) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["systemctl", "--user", *args],
        check=check,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
    )


def install(home: Path, project: Path, rotate_token: bool, port: int,
            enable_at_boot: bool | None = None) -> tuple[Path, Path, str]:
    unit_file, env_file = _paths(home)
    old = _read_environment(env_file)
    token = secrets.token_urlsafe(24) if rotate_token or not old.get("LINE_B_TOKEN") else old["LINE_B_TOKEN"]
    runner = project / "apps" / "line_b_web" / "start_robot_service"
    check_source = project / "apps" / "line_b_web" / "q1_line_b_start_check.py"
    preflight_source = project / "apps" / "line_b_web" / "q1_line_b_boot_preflight.py"
    if not runner.exists():
        raise SystemExit(f"缺少机器人服务入口：{runner}")
    if not check_source.exists():
        raise SystemExit(f"缺少一键无动作验收入口：{check_source}")
    if not preflight_source.exists():
        raise SystemExit(f"缺少开机低层门禁：{preflight_source}")
    preflight_target = home / "q1_line_b_boot_preflight.py"
    _write_atomic(env_file, environment_text(project, token, port), 0o600)
    _write_atomic(unit_file, unit_text(project, runner, env_file, preflight_target), 0o644)
    _write_atomic(home / "q1_line_b_start_check.py", check_source.read_text(), 0o755)
    _write_atomic(preflight_target, preflight_source.read_text(), 0o755)
    _run_systemctl("daemon-reload")
    if enable_at_boot is True:
        _run_systemctl("enable", SERVICE)
    elif enable_at_boot is False:
        _run_systemctl("disable", SERVICE, check=False)
    return unit_file, env_file, token


def interface_ipv4_addresses() -> list[tuple[str, str]]:
    """Return active non-loopback IPv4 addresses using Linux iproute2 JSON."""
    try:
        result = subprocess.run(
            ["ip", "-j", "-4", "address", "show", "up"],
            check=True,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        rows = json.loads(result.stdout)
    except (FileNotFoundError, subprocess.CalledProcessError, json.JSONDecodeError):
        return []
    found: list[tuple[str, str]] = []
    for row in rows:
        name = str(row.get("ifname", ""))
        if name == "lo":
            continue
        for info in row.get("addr_info", []):
            address = str(info.get("local", ""))
            if info.get("family") == "inet" and address and not address.startswith("127."):
                found.append((name, address))
    # Wi-Fi client/AP interfaces first, then deterministic interface/address order.
    return sorted(set(found), key=lambda item: (not item[0].startswith(("wl", "wlan", "p2p")), item))


def access_urls(token: str, port: int, addresses: list[tuple[str, str]] | None = None) -> list[str]:
    addresses = interface_ipv4_addresses() if addresses is None else addresses
    return [f"http://{address}:{port}/?token={token}" for _, address in addresses]


def show_urls(env_file: Path) -> int:
    env = _read_environment(env_file)
    token = env.get("LINE_B_TOKEN", "")
    port = int(env.get("LINE_B_PORT", str(DEFAULT_PORT)))
    if not token:
        print("尚未安装：找不到固定控制令牌", file=sys.stderr)
        return 2
    addresses = interface_ipv4_addresses()
    if not addresses:
        print("未发现可用的非回环 IPv4；请先让机器人连接热点", file=sys.stderr)
        return 3
    for (name, _), url in zip(addresses, access_urls(token, port, addresses)):
        print(f"{name}: {url}")
    return 0


def local_controller_mode(env_file: Path) -> str | None:
    """Read the robot-local controller state; never send an action."""
    env = _read_environment(env_file)
    token = env.get("LINE_B_TOKEN", "")
    port = int(env.get("LINE_B_PORT", str(DEFAULT_PORT)))
    if not token:
        return None
    url = f"http://127.0.0.1:{port}/api/status?token={token}"
    try:
        with urllib.request.urlopen(url, timeout=1.0) as response:
            value = json.loads(response.read())
    except (OSError, urllib.error.URLError, json.JSONDecodeError):
        return None
    mode = value.get("mode") if isinstance(value, dict) else None
    return str(mode) if mode else None


def main() -> int:
    p = argparse.ArgumentParser(description="线路 B：机器人本机手机网页服务")
    sub = p.add_subparsers(dest="command", required=True)

    install_p = sub.add_parser("install", help="安装/更新服务（不会立即启动）")
    install_p.add_argument("--project", type=Path, default=PROJECT)
    install_p.add_argument("--port", type=int, default=DEFAULT_PORT)
    install_p.add_argument("--rotate-token", action="store_true")
    boot = install_p.add_mutually_exclusive_group()
    boot.add_argument("--enable-at-boot", action="store_true", help="每次开机门禁通过后常驻")
    boot.add_argument("--disable-at-boot", action="store_true", help="取消开机启动")

    sub.add_parser("url", help="显示手机可访问的机器人热点地址")
    sub.add_parser("status", help="显示机器人本机服务状态")
    stop_p = sub.add_parser("stop", help="仅在 LYING/DAMPED 时停止服务")
    stop_p.add_argument(
        "--confirm-hardware-safe",
        action="store_true",
        help="仅当状态 API 不可读且已用独立急停确认完全无扭矩时使用",
    )
    start_p = sub.add_parser("start", help="安全确认后手动启动服务")
    start_p.add_argument("--confirm-not-charging", action="store_true")
    start_p.add_argument("--confirm-low-level", action="store_true")
    start_p.add_argument("--confirm-protected", action="store_true")
    a = p.parse_args()

    home = Path.home()
    unit_file, env_file = _paths(home)
    if a.command == "install":
        active = _run_systemctl("is-active", SERVICE, check=False).stdout.strip()
        if active in ("active", "activating", "deactivating"):
            print(
                f"拒绝安装：{SERVICE} 当前为 {active}。请先在原网页趴下到 LYING，再安全停止旧服务。",
                file=sys.stderr,
            )
            return 7
        boot_mode = True if a.enable_at_boot else False if a.disable_at_boot else None
        unit_file, env_file, _ = install(home, a.project.resolve(), a.rotate_token, a.port, boot_mode)
        print(f"已安装 {unit_file}")
        print(f"固定令牌保存在 {env_file}（权限 600）")
        print(f"一键无动作验收入口：{home / 'q1_line_b_start_check.py'}")
        print(f"开机低层门禁：{home / 'q1_line_b_boot_preflight.py'}")
        enabled = _run_systemctl("is-enabled", SERVICE, check=False).stdout.strip()
        print(f"开机状态：{enabled or 'unknown'}；安装命令本身不会启动或驱动机器人。")
        return show_urls(env_file)
    if a.command == "url":
        return show_urls(env_file)
    if a.command == "status":
        result = _run_systemctl("status", SERVICE, "--no-pager", check=False)
        print(result.stdout, end="")
        return result.returncode
    if a.command == "stop":
        active = _run_systemctl("is-active", SERVICE, check=False).stdout.strip()
        if active not in ("active", "activating", "deactivating"):
            print(f"{SERVICE} 已停止（状态：{active or 'unknown'}）。")
            return 0
        mode = local_controller_mode(env_file)
        if mode not in ("LYING", "DAMPED") and not (mode is None and a.confirm_hardware_safe):
            shown = mode or "无法读取"
            print(
                f"拒绝停止：当前状态 {shown}；服务退出会切阻尼。请先在网页正常趴下到 LYING。",
                file=sys.stderr,
            )
            return 6
        result = _run_systemctl("stop", SERVICE, check=False)
        if result.stdout:
            print(result.stdout, end="")
        print(f"线路 B 机器人本机服务已请求停止（停止前状态：{mode or '硬件已确认安全'}）。")
        return result.returncode
    if a.command == "start":
        confirmations = (a.confirm_not_charging, a.confirm_low_level, a.confirm_protected)
        if not all(confirmations):
            print(
                "拒绝启动：必须同时确认机器人未充电、已切低层模式、吊带/独立急停/场地就位。",
                file=sys.stderr,
            )
            return 4
        if not unit_file.exists() or not env_file.exists():
            print("服务尚未安装，请先执行 install。", file=sys.stderr)
            return 2
        _run_systemctl("start", SERVICE)
        active = _run_systemctl("is-active", SERVICE, check=False)
        if active.stdout.strip() != "active":
            print(active.stdout, end="", file=sys.stderr)
            print(f"启动失败；请执行 systemctl --user status {SERVICE}", file=sys.stderr)
            return 5
        print("线路 B 服务已在机器人本机运行；Mac 网线可以断开，前提是手机与机器人热点网络可互访。")
        return show_urls(env_file)
    raise AssertionError(a.command)


if __name__ == "__main__":
    raise SystemExit(main())
