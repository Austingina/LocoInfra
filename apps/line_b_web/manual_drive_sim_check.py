"""线路 B 人工前进/后退/转向完整 MuJoCo 验收，不连接真机。"""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np

from apps import ref
from apps.line_b_web.control import LineBControl
from apps.line_b_web.machine import HandoffConfig, LIEDOWN, LYING, STAND_HOLD, WALK, LineBMachine
from apps.teleop.command import BACK, FWD, TURN
from runtime import MuJoCoBackend, Package

PROJECT = Path(__file__).resolve().parents[2]


def run_until(machine: LineBMachine, control: LineBControl, target: str, timeout_s: float,
              seen: set[str]) -> bool:
    for _ in range(int(round(timeout_s / machine.dt))):
        control.set_status({"mode": machine.mode})
        pad, edges = control.sample()
        machine.tick(pad, edges)
        seen.add(machine.mode)
        if machine.mode == target:
            return True
    return False


def yaw(robot: MuJoCoBackend) -> float:
    w, x, y, z = robot.data.qpos[robot.fq + 3:robot.fq + 7]
    return float(np.arctan2(2.0 * (w * z + x * y), 1.0 - 2.0 * (y * y + z * z)))


def drive_for(machine: LineBMachine, control: LineBControl,
              ly: float, rx: float, duration_s: float) -> dict:
    samples = []
    yaw0 = yaw(machine.robot)
    for i in range(int(round(duration_s / machine.dt))):
        control.set_status({"mode": machine.mode})
        # 网页每 100 ms 重发一次“仍在按住”，与真机页面一致。
        if i % max(1, int(round(0.1 / machine.dt))) == 0:
            control.drive(ly=ly, rx=rx)
        pad, edges = control.sample()
        machine.tick(pad, edges)
        samples.append((machine.cmd[0], machine.cmd[2], machine.robot.base_velocity()[0],
                        machine.robot.sense().base_ang_vel[2], machine.robot.upright()))
        if machine.mode != WALK:
            break
    values = np.asarray(samples, dtype=np.float64)
    return {
        "frames": len(samples),
        "cmd_vx_min": float(values[:, 0].min()),
        "cmd_vx_max": float(values[:, 0].max()),
        "cmd_vx_final": float(values[-1, 0]),
        "cmd_wz_min": float(values[:, 1].min()),
        "cmd_wz_max": float(values[:, 1].max()),
        "cmd_wz_final": float(values[-1, 1]),
        "velocity_x_mean_last_1s": float(values[-min(50, len(values)):, 2].mean()),
        "angular_z_mean_last_1s": float(values[-min(50, len(values)):, 3].mean()),
        "yaw_delta_rad": float(np.arctan2(np.sin(yaw(machine.robot) - yaw0),
                                           np.cos(yaw(machine.robot) - yaw0))),
        "upright_min": float(values[:, 4].min()),
    }


def settle_height(machine: LineBMachine, control: LineBControl, seconds: float = 1.0) -> dict:
    rows = []
    for _ in range(int(round(seconds / machine.dt))):
        control.set_status({"mode": machine.mode})
        pad, edges = control.sample()
        machine.tick(pad, edges)
        rows.append((machine.cmd[3], machine.robot.pelvis_height(), machine.robot.upright()))
    values = np.asarray(rows)
    return {
        "height_target_m": machine.height_target,
        "height_command_m": float(values[-1, 0]),
        "pelvis_height_m": float(values[-10:, 1].mean()),
        "upright_min": float(values[:, 2].min()),
    }


def main() -> None:
    base = Package.load(PROJECT / "weights/dual_v3b")
    skill = Package.load(PROJECT / "weights/getup_vpd_ft")
    dance = Package.load(PROJECT / "weights/q1_smooth_dance")
    robot = MuJoCoBackend(base)
    machine = LineBMachine(
        base,
        skill,
        robot,
        ref.find("getup_facedown"),
        ref.find("liedown_facedown"),
        dance,
        dance.root / "motion.npz",
        speed_scale=0.30 / FWD,
        backward_speed_scale=0.20 / BACK,
        turn_speed_scale=0.50 / TURN,
        experimental_handoff=HandoffConfig(),
        dance_enabled=False,
    )
    control = LineBControl(drive_timeout=0.25, heartbeat_timeout=100.0,
                           mobility_enabled=True, dance_enabled=False)
    control.session(True)
    control.set_status({"mode": machine.mode})
    seen = {machine.mode}

    control.action("pose_check")
    run_until(machine, control, LYING, 1.0, seen)
    pose_check = dict(machine.pose_check_result)
    control.action("getup")
    reached_hold = run_until(machine, control, STAND_HOLD, 12.0, seen)
    if reached_hold:
        control.action("walk")
    reached_walk = reached_hold and run_until(machine, control, WALK, 16.0, seen)

    dance_rejected = False
    if reached_walk:
        try:
            control.action("dance")
        except ValueError:
            dance_rejected = True

    speed_levels = {}
    if reached_walk:
        for level in (1, 2, 3):
            control.settings(speed_level=level)
            run_until(machine, control, WALK, 0.1, seen)
            speed_levels[str(level)] = {
                "factor": machine.speed_factor,
                "forward_mps": machine.speed * FWD,
                "backward_mps": machine.backward_speed * BACK,
                "turn_radps": machine.turn_speed * TURN,
            }

    standing_height = robot.pelvis_height() if reached_walk else 0.0
    squat = rise = {}
    if reached_walk:
        for _ in range(4):
            control.action("squat")
            squat = settle_height(machine, control, 0.7)
        for _ in range(4):
            control.action("rise")
            rise = settle_height(machine, control, 0.7)

    x0 = float(robot.data.qpos[robot.fq])
    forward = drive_for(machine, control, 1.0, 0.0, 3.0) if reached_walk else {}
    control.drive(ly=0.0, rx=0.0)
    stopped_after_forward = drive_for(machine, control, 0.0, 0.0, 1.0) if reached_walk else {}
    x1 = float(robot.data.qpos[robot.fq])
    backward = drive_for(machine, control, -1.0, 0.0, 3.0) if reached_walk else {}
    control.drive(ly=0.0, rx=0.0)
    stopped_after_backward = drive_for(machine, control, 0.0, 0.0, 1.0) if reached_walk else {}
    x2 = float(robot.data.qpos[robot.fq])

    left = drive_for(machine, control, 0.0, 1.0, 2.0) if reached_walk else {}
    control.drive(ly=0.0, rx=0.0)
    stopped_after_left = drive_for(machine, control, 0.0, 0.0, 1.0) if reached_walk else {}
    right = drive_for(machine, control, 0.0, -1.0, 2.0) if reached_walk else {}
    control.drive(ly=0.0, rx=0.0)
    stopped_after_right = drive_for(machine, control, 0.0, 0.0, 1.0) if reached_walk else {}
    forward_left = drive_for(machine, control, 1.0, 1.0, 2.0) if reached_walk else {}
    control.drive(ly=0.0, rx=0.0)
    stopped_after_forward_left = drive_for(machine, control, 0.0, 0.0, 1.0) if reached_walk else {}
    forward_right = drive_for(machine, control, 1.0, -1.0, 2.0) if reached_walk else {}
    control.drive(ly=0.0, rx=0.0)
    stopped_after_forward_right = drive_for(machine, control, 0.0, 0.0, 1.0) if reached_walk else {}
    # 圆形摇杆的 3-4-5 斜向输入：后退 0.6，转向 0.8，幅值正好为 1。
    backward_left = drive_for(machine, control, -0.6, 0.8, 2.0) if reached_walk else {}
    control.drive(ly=0.0, rx=0.0)
    stopped_after_backward_left = drive_for(machine, control, 0.0, 0.0, 1.0) if reached_walk else {}
    backward_right = drive_for(machine, control, -0.6, -0.8, 2.0) if reached_walk else {}
    control.drive(ly=0.0, rx=0.0)
    stopped_after_backward_right = drive_for(machine, control, 0.0, 0.0, 1.0) if reached_walk else {}

    if reached_walk:
        control.action("liedown")
    reached_liedown = reached_walk and run_until(machine, control, LIEDOWN, 2.0, seen)
    reached_lying = reached_liedown and run_until(machine, control, LYING, 12.0, seen)

    passed = bool(
        reached_walk
        and pose_check["passed"]
        and dance_rejected
        and np.isclose(speed_levels["1"]["forward_mps"], 0.15, atol=1e-9)
        and np.isclose(speed_levels["2"]["forward_mps"], 0.225, atol=1e-9)
        and np.isclose(speed_levels["3"]["forward_mps"], 0.30, atol=1e-9)
        and np.isclose(squat["height_target_m"], -0.12, atol=1e-9)
        and np.isclose(squat["height_command_m"], -0.12, atol=1e-6)
        and squat["pelvis_height_m"] < standing_height - 0.05
        and squat["upright_min"] >= 0.75
        and np.isclose(rise["height_target_m"], 0.0, atol=1e-9)
        and np.isclose(rise["height_command_m"], 0.0, atol=1e-6)
        and rise["pelvis_height_m"] > squat["pelvis_height_m"] + 0.05
        and np.isclose(forward["cmd_vx_max"], 0.30, atol=1e-9)
        and np.isclose(backward["cmd_vx_min"], -0.20, atol=1e-9)
        and np.isclose(left["cmd_wz_max"], 0.50, atol=1e-9)
        and np.isclose(right["cmd_wz_min"], -0.50, atol=1e-9)
        and np.isclose(forward_left["cmd_vx_max"], 0.30, atol=1e-9)
        and np.isclose(forward_left["cmd_wz_max"], 0.50, atol=1e-9)
        and np.isclose(forward_right["cmd_vx_max"], 0.30, atol=1e-9)
        and np.isclose(forward_right["cmd_wz_min"], -0.50, atol=1e-9)
        and np.isclose(backward_left["cmd_vx_min"], -0.12, atol=1e-9)
        and np.isclose(backward_left["cmd_wz_max"], 0.40, atol=1e-9)
        and np.isclose(backward_right["cmd_vx_min"], -0.12, atol=1e-9)
        and np.isclose(backward_right["cmd_wz_min"], -0.40, atol=1e-9)
        and all(abs(stop["cmd_vx_final"]) < 1e-9 and abs(stop["cmd_wz_final"]) < 1e-9
                for stop in (stopped_after_forward, stopped_after_backward, stopped_after_left,
                             stopped_after_right, stopped_after_forward_left, stopped_after_forward_right,
                             stopped_after_backward_left, stopped_after_backward_right))
        and x1 > x0
        and x2 < x1
        and left["yaw_delta_rad"] > 0.1
        and right["yaw_delta_rad"] < -0.1
        and forward_left["yaw_delta_rad"] > 0.1
        and forward_right["yaw_delta_rad"] < -0.1
        and backward_left["yaw_delta_rad"] > 0.1
        and backward_right["yaw_delta_rad"] < -0.1
        and min(x["upright_min"] for x in (forward, backward, left, right,
                                            forward_left, forward_right,
                                            backward_left, backward_right)) >= 0.75
        and reached_lying
    )
    result = {
        "offline_only": True,
        "passed": passed,
        "seen_modes": sorted(seen),
        "reached_walk": reached_walk,
        "pose_check": pose_check,
        "dance_rejected": dance_rejected,
        "speed_levels": speed_levels,
        "standing_height_m": standing_height,
        "squat": squat,
        "rise": rise,
        "forward": forward,
        "stopped_after_forward": stopped_after_forward,
        "backward": backward,
        "stopped_after_backward": stopped_after_backward,
        "left": left,
        "stopped_after_left": stopped_after_left,
        "right": right,
        "stopped_after_right": stopped_after_right,
        "forward_left": forward_left,
        "stopped_after_forward_left": stopped_after_forward_left,
        "forward_right": forward_right,
        "stopped_after_forward_right": stopped_after_forward_right,
        "backward_left": backward_left,
        "stopped_after_backward_left": stopped_after_backward_left,
        "backward_right": backward_right,
        "stopped_after_backward_right": stopped_after_backward_right,
        "forward_dx_m": round(x1 - x0, 6),
        "backward_dx_m": round(x2 - x1, 6),
        "final_mode": machine.mode,
        "handoff": machine.handoff_metrics.as_dict(),
    }
    print(json.dumps(result, ensure_ascii=False, indent=2))
    if not passed:
        raise SystemExit("线路 B 人工前进/后退/转向 MuJoCo 验收失败")


if __name__ == "__main__":
    main()
