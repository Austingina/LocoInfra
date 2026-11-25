#!/usr/bin/env python3
"""机器人用户入口；安装后复制为 /home/run/q1_line_b_start_check.py。"""

from __future__ import annotations

import os
import sys
from pathlib import Path


PROJECT = Path(os.environ.get("LINE_B_PROJECT", "/home/run/LocoInfra_Q1_Dance"))
sys.path.insert(0, str(PROJECT))

from apps.line_b_web.start_check import main  # noqa: E402


if __name__ == "__main__":
    raise SystemExit(main())
