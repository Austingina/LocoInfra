"""Full zero-speed WALK -> Q1 4x dance -> WALK MuJoCo acceptance."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

import numpy as np

PROJECT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(PROJECT))

from apps import ref  # noqa: E402
from apps.line_b_web.machine import DAMPED, DANCE, WALK, HandoffConfig, LineBMachine  # noqa: E402
from apps.teleop.gamepad import Pad  # noqa: E402
from runtime import MuJoCoBackend, Package  # noqa: E402
from apps.new_motion_web.gate import BUNDLE_ROOT, DANCE_ROOT, sha256  # noqa: E402


ZERO_PAD = Pad(connected=True)


def run(release: Path) -> dict:
    base = Package.load(release / BUNDLE_ROOT / "weights/dual_v3b")
    skill = Package.load(release / BUNDLE_ROOT / "weights/getup_vpd_ft")
    dance_root = release / DANCE_ROOT
    dance = Package.load(dance_root)
    robot = MuJoCoBackend(base)
    machine = LineBMachine(
        base, skill, robot,
        ref.find("getup_facedown"), ref.find("liedown_facedown"),
        dance, dance_root / "motion.npz",
        speed_scale=0.30 / 0.6,
        backward_speed_scale=0.20 / 0.4,
        turn_speed_scale=0.50 / 1.0,
        experimental_handoff=HandoffConfig(),
        dance_enabled=True,
        dance_target_step_max=0.05,
    )
    machine.place_standing()
    min_upright = 1.0
    max_gyro = 0.0
    max_tracking = 0.0
    max_target_step = 0.0
    max_dance_target_step = 0.0
    previous_target = None
    seen_dance = False
    returned = False
    frames = 0
    max_frames = int(round(50.0 / machine.dt))
    for frame in range(max_frames):
        edges = {"DANCE"} if frame == int(round(2.0 / machine.dt)) else set()
        machine.tick(ZERO_PAD, edges)
        sensed = robot.sense()
        min_upright = min(min_upright, float(robot.upright()))
        max_gyro = max(max_gyro, float(np.linalg.norm(sensed.base_ang_vel)))
        if machine.target is not None:
            target = np.asarray(machine.target, dtype=np.float64)
            max_tracking = max(max_tracking, float(np.abs(target - sensed.joint_pos).max()))
            if previous_target is not None:
                step = float(np.abs(target - previous_target).max())
                max_target_step = max(max_target_step, step)
                if machine.dance_transition_active or machine.mode == DANCE:
                    max_dance_target_step = max(max_dance_target_step, step)
            previous_target = target.copy()
        frames = frame + 1
        seen_dance = seen_dance or machine.mode == DANCE
        if machine.mode == DAMPED:
            break
        if seen_dance and machine.mode == WALK:
            returned = True
            break
    if returned:
        for _ in range(int(round(2.0 / machine.dt))):
            machine.tick(ZERO_PAD, set())
            sensed = robot.sense()
            min_upright = min(min_upright, float(robot.upright()))
            max_gyro = max(max_gyro, float(np.linalg.norm(sensed.base_ang_vel)))
            frames += 1
    metrics = {
        "min_upright": round(min_upright, 6),
        "max_gyro_rad_s": round(max_gyro, 6),
        "max_tracking_error_rad": round(max_tracking, 6),
        "max_target_step_rad": round(max_target_step, 6),
        "max_dance_target_step_rad": round(max_dance_target_step, 6),
        "frames": frames,
        "duration_s": round(frames * machine.dt, 3),
    }
    passed = bool(seen_dance and returned and machine.mode == WALK
                  and min_upright >= 0.65 and max_gyro <= 7.5 and max_tracking <= 0.65
                  and max_dance_target_step <= 0.050001)
    return {
        "schema": 1,
        "candidate": "q1_4x_line_b_transition",
        "backend": "mujoco",
        "real_robot_enabled": False,
        "motor_commands_sent": False,
        "passed": passed,
        "seen_dance": seen_dance,
        "returned_to_walk": returned,
        "final_mode": machine.mode,
        "message": machine.msg,
        "metrics": metrics,
        "source_hashes": {
            "line_b_machine.py": sha256(PROJECT / "apps/line_b_web/machine.py"),
            "dance_4x_deploy.yaml": sha256(dance_root / "deploy.yaml"),
            "dance_4x_policy.onnx": sha256(dance_root / "policy.onnx"),
            "dance_4x_motion.npz": sha256(dance_root / "motion.npz"),
        },
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--release", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    report = run(args.release.resolve())
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report["passed"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
