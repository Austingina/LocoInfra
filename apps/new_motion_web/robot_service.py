"""Install/manage the robot-hosted Q1 4x dance + walking service."""

from __future__ import annotations

import argparse
import json
import secrets
import subprocess
import sys
import urllib.error
import urllib.request
from pathlib import Path

from apps.line_b_web.robot_service import (
    _environment_quote,
    _read_environment,
    _run_systemctl,
    _systemd_path,
    _write_atomic,
    interface_ipv4_addresses,
)
from apps.new_motion_web.gate import validate_real_gate


SERVICE = "q1-new-motion-web.service"
OTHER_SERVICE = "q1-line-b-web.service"
CONFIG_DIRNAME = "q1-new-motion-web"
DEFAULT_PORT = 8769
PROJECT = Path(__file__).resolve().parents[2]
DEFAULT_RELEASE = Path("/home/run/q1_4x_packages/current")
DEFAULT_REPORT = PROJECT / "apps/new_motion_web/q1_4x_lineb_dynamic_report.json"
BOOT_ID_PATH = Path("/proc/sys/kernel/random/boot_id")
ARM_FILENAME = "armed-boot-id"


def unit_text(project: Path, runner: Path, env_file: Path, preflight: Path) -> str:
    return f"""[Unit]
Description=Q1 4x dance and walking mobile control
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


def environment_text(project: Path, release: Path, report: Path, token: str, port: int) -> str:
    if not token or any(ch not in "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-_" for ch in token):
        raise ValueError("令牌必须是非空 URL-safe 字符串")
    if not 1 <= int(port) <= 65535:
        raise ValueError("端口必须在 1..65535")
    return (
        f"NEW_MOTION_PROJECT={_environment_quote(project)}\n"
        f"NEW_MOTION_RELEASE={_environment_quote(release)}\n"
        f"NEW_MOTION_DYNAMIC_REPORT={_environment_quote(report)}\n"
        f"NEW_MOTION_TOKEN={token}\n"
        f"NEW_MOTION_PORT={int(port)}\n"
        "NEW_MOTION_LOG_DIR=/tmp/q1-new-motion-web/ros\n"
        "NEW_MOTION_DRY_RUN=0\n"
    )


def paths(home: Path) -> tuple[Path, Path]:
    return (
        home / ".config/systemd/user" / SERVICE,
        home / ".config" / CONFIG_DIRNAME / "environment",
    )


def current_boot_id(path: Path = BOOT_ID_PATH) -> str:
    value = path.read_text(encoding="utf-8").strip()
    if not value or any(ch not in "0123456789abcdef-" for ch in value.lower()):
        raise ValueError("无法读取有效的 Linux boot_id")
    return value


def arm_current_boot(home: Path) -> Path:
    """Record that an onsite operator confirmed physical safety for this boot."""
    target = home / ".config" / CONFIG_DIRNAME / ARM_FILENAME
    _write_atomic(target, current_boot_id() + "\n", 0o600)
    return target


def other_active() -> str:
    return _run_systemctl("is-active", OTHER_SERVICE, check=False).stdout.strip()


def other_enabled() -> str:
    return _run_systemctl("is-enabled", OTHER_SERVICE, check=False).stdout.strip()


def urls(env_file: Path) -> int:
    env = _read_environment(env_file)
    token = env.get("NEW_MOTION_TOKEN", "")
    port = int(env.get("NEW_MOTION_PORT", str(DEFAULT_PORT)))
    if not token:
        print("尚未安装：找不到固定控制令牌", file=sys.stderr)
        return 2
    addresses = interface_ipv4_addresses()
    if not addresses:
        print("未发现非回环 IPv4", file=sys.stderr)
        return 3
    for name, address in addresses:
        print(f"{name}: http://{address}:{port}/?token={token}")
    return 0


def local_mode(env_file: Path) -> str | None:
    env = _read_environment(env_file)
    token = env.get("NEW_MOTION_TOKEN", "")
    port = int(env.get("NEW_MOTION_PORT", str(DEFAULT_PORT)))
    if not token:
        return None
    try:
        with urllib.request.urlopen(
            f"http://127.0.0.1:{port}/api/status?token={token}", timeout=1.0
        ) as response:
            value = json.loads(response.read())
    except (OSError, urllib.error.URLError, json.JSONDecodeError):
        return None
    mode = value.get("mode") if isinstance(value, dict) else None
    return str(mode) if mode else None


def install(home: Path, project: Path, release: Path, report: Path, port: int,
            rotate_token: bool, enable_at_boot: bool | None) -> int:
    active = _run_systemctl("is-active", SERVICE, check=False).stdout.strip()
    if active in ("active", "activating", "deactivating"):
        print(f"拒绝覆盖：{SERVICE} 当前为 {active}", file=sys.stderr)
        return 7
    validate_real_gate(release, report, project / "apps/line_b_web/machine.py")
    if enable_at_boot is True and other_enabled() in ("enabled", "enabled-runtime"):
        print(f"拒绝同时开机启动：请先 systemctl --user disable {OTHER_SERVICE}", file=sys.stderr)
        return 8
    unit_file, env_file = paths(home)
    old = _read_environment(env_file)
    token = secrets.token_urlsafe(24) if rotate_token or not old.get("NEW_MOTION_TOKEN") \
        else old["NEW_MOTION_TOKEN"]
    runner = project / "apps/new_motion_web/start_robot_service"
    preflight_source = project / "apps/new_motion_web/q1_new_motion_boot_preflight.py"
    launcher_source = project / "apps/new_motion_web/start_new_motion_web"
    for required in (runner, preflight_source, launcher_source):
        if not required.is_file():
            raise SystemExit(f"缺少服务文件：{required}")
    preflight_target = home / "q1_new_motion_boot_preflight.py"
    launcher_target = home / "start_new_motion_web"
    _write_atomic(env_file, environment_text(project, release, report, token, port), 0o600)
    _write_atomic(unit_file, unit_text(project, runner, env_file, preflight_target), 0o644)
    _write_atomic(preflight_target, preflight_source.read_text(encoding="utf-8"), 0o755)
    _write_atomic(launcher_target, launcher_source.read_text(encoding="utf-8"), 0o755)
    _run_systemctl("daemon-reload")
    if enable_at_boot is True:
        _run_systemctl("enable", SERVICE)
    elif enable_at_boot is False:
        _run_systemctl("disable", SERVICE, check=False)
    print(f"已安装 {unit_file}；安装不会启动或发送动作。")
    print(f"后续一键入口：{launcher_target}")
    return urls(env_file)


def main() -> int:
    parser = argparse.ArgumentParser(description="Q1 4 倍速舞蹈与连续行走真机服务")
    sub = parser.add_subparsers(dest="command", required=True)
    install_p = sub.add_parser("install")
    install_p.add_argument("--project", type=Path, default=PROJECT)
    install_p.add_argument("--release", type=Path, default=DEFAULT_RELEASE)
    install_p.add_argument("--dynamic-report", type=Path, default=DEFAULT_REPORT)
    install_p.add_argument("--port", type=int, default=DEFAULT_PORT)
    install_p.add_argument("--rotate-token", action="store_true")
    boot = install_p.add_mutually_exclusive_group()
    boot.add_argument("--enable-at-boot", action="store_true")
    boot.add_argument("--disable-at-boot", action="store_true")
    sub.add_parser("url")
    sub.add_parser("status")
    stop_p = sub.add_parser("stop")
    stop_p.add_argument("--confirm-hardware-safe", action="store_true")
    start_p = sub.add_parser("start")
    start_p.add_argument("--confirm-not-charging", action="store_true")
    start_p.add_argument("--confirm-low-level", action="store_true")
    start_p.add_argument("--confirm-protected", action="store_true")
    args = parser.parse_args()
    home = Path.home()
    unit_file, env_file = paths(home)

    if args.command == "install":
        boot_mode = True if args.enable_at_boot else False if args.disable_at_boot else None
        return install(home, args.project.resolve(), args.release.resolve(),
                       args.dynamic_report.resolve(), args.port, args.rotate_token, boot_mode)
    if args.command == "url":
        return urls(env_file)
    if args.command == "status":
        result = _run_systemctl("status", SERVICE, "--no-pager", check=False)
        print(result.stdout, end="")
        return result.returncode
    if args.command == "stop":
        active = _run_systemctl("is-active", SERVICE, check=False).stdout.strip()
        if active not in ("active", "activating", "deactivating"):
            print(f"{SERVICE} 已停止（{active or 'unknown'}）")
            return 0
        mode = local_mode(env_file)
        if mode not in ("LYING", "DAMPED") and not (mode is None and args.confirm_hardware_safe):
            print(f"拒绝停止：当前状态 {mode or '无法读取'}；请先正常趴下", file=sys.stderr)
            return 6
        return _run_systemctl("stop", SERVICE, check=False).returncode
    if args.command == "start":
        if not all((args.confirm_not_charging, args.confirm_low_level, args.confirm_protected)):
            print("拒绝启动：必须确认未充电、低层模式、吊带/急停/场地就位", file=sys.stderr)
            return 4
        conflict = other_active()
        if conflict in ("active", "activating", "deactivating"):
            print(f"拒绝启动：{OTHER_SERVICE} 为 {conflict}；必须先在 LYING/DAMPED 安全停止", file=sys.stderr)
            return 9
        if not unit_file.is_file() or not env_file.is_file():
            print("服务未安装", file=sys.stderr)
            return 2
        arm_file = arm_current_boot(home)
        print(f"已写入本次开机的现场安全授权：{arm_file}")
        _run_systemctl("start", SERVICE)
        active = _run_systemctl("is-active", SERVICE, check=False).stdout.strip()
        if active != "active":
            print(f"启动失败：{active}", file=sys.stderr)
            return 5
        return urls(env_file)
    raise AssertionError(args.command)


if __name__ == "__main__":
    raise SystemExit(main())
