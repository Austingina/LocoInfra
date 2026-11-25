#!/usr/bin/env python3
"""线路 B 开机只读门禁；允许底层状态服务在开机阶段短暂延迟。"""

from __future__ import annotations

import argparse
import subprocess
import time
from pathlib import Path


LOWLEVEL_CHECK = Path("/home/run/q1_lowlevel_check.py")


def run_preflight(
    attempts: int,
    delay: float,
    seconds: float,
    checker: Path = LOWLEVEL_CHECK,
) -> int:
    if not checker.is_file():
        print(f"[line-b-boot] 缺少低层检查：{checker}")
        return 2
    for attempt in range(1, attempts + 1):
        print(f"[line-b-boot] 低层只读门禁 {attempt}/{attempts}", flush=True)
        result = subprocess.run(
            [str(checker), "--seconds", str(seconds)],
            check=False,
        )
        if result.returncode == 0:
            print("[line-b-boot] 低层门禁通过，允许启动常驻网页服务", flush=True)
            return 0
        if attempt < attempts:
            time.sleep(delay)
    print("[line-b-boot] 低层门禁持续失败，拒绝启动常驻网页服务", flush=True)
    return 2


def main() -> int:
    parser = argparse.ArgumentParser(description="线路 B 开机低层只读门禁")
    parser.add_argument("--attempts", type=int, default=6)
    parser.add_argument("--delay", type=float, default=5.0)
    parser.add_argument("--seconds", type=float, default=3.0)
    args = parser.parse_args()
    if not 1 <= args.attempts <= 12:
        parser.error("--attempts 必须在 1..12")
    if not 0.0 <= args.delay <= 10.0:
        parser.error("--delay 必须在 0..10 秒")
    return run_preflight(args.attempts, args.delay, args.seconds)


if __name__ == "__main__":
    raise SystemExit(main())
