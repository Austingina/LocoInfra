#!/usr/bin/env python3
"""Read-only low-level and controller-exclusion preflight for the new service."""

from __future__ import annotations

import subprocess
from pathlib import Path


ARM_FILE = Path.home() / ".config/q1-new-motion-web/armed-boot-id"
BOOT_ID_FILE = Path("/proc/sys/kernel/random/boot_id")


def require_current_boot_arm() -> int:
    try:
        expected = BOOT_ID_FILE.read_text(encoding="utf-8").strip()
        armed = ARM_FILE.read_text(encoding="utf-8").strip()
    except OSError as exc:
        print(f"[new-motion-boot] 缺少本次开机的现场授权：{exc}")
        print("[new-motion-boot] 请在现场运行 /home/run/start_new_motion_web")
        return 4
    if not expected or armed != expected:
        print("[new-motion-boot] 现场授权不属于本次开机，拒绝自动取得低层控制权")
        print("[new-motion-boot] 请在现场运行 /home/run/start_new_motion_web")
        return 4
    return 0


def main() -> int:
    armed = require_current_boot_arm()
    if armed:
        return armed
    other = subprocess.run(
        ["systemctl", "--user", "is-active", "q1-line-b-web.service"],
        check=False, text=True, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
    ).stdout.strip()
    if other in ("active", "activating", "deactivating"):
        print(f"[new-motion-boot] 原线路 B 服务为 {other}，拒绝并发控制")
        return 3
    checker = Path("/home/run/q1_line_b_boot_preflight.py")
    if not checker.is_file():
        print(f"[new-motion-boot] 缺少低层门禁：{checker}")
        return 2
    return subprocess.run([str(checker)], check=False).returncode


if __name__ == "__main__":
    raise SystemExit(main())
