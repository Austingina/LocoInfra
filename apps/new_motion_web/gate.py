"""Fail-closed package and MuJoCo evidence gate for the Q1 4x dance service."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path


BUNDLE_ROOT = "Q1_4x_20261117"
DANCE_ROOT = f"{BUNDLE_ROOT}/weights/dance_4x"
EXPECTED_ARCHIVE_SHA256 = "6b5b0f8c46aae82e1d9f5f274c99c5d1913a32b8405539a2ff38c54362f52eb3"
EXPECTED_POLICY_SHA256 = "abe25161d6bd066fcabb6b35aa7bdbb3098c16de19e6834fcf2ed1b8d449a2ae"


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _json(path: Path, label: str) -> dict:
    if not path.is_file():
        raise ValueError(f"缺少{label}：{path}")
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"{label}必须是 JSON 对象")
    return value


def validate_real_gate(release: Path, dynamic_report: Path, machine_source: Path) -> dict:
    """Validate immutable package identity and the exact transition implementation tested in MuJoCo."""
    release = release.resolve()
    deployment = _json(release / "DEPLOYMENT.json", "DEPLOYMENT.json")
    if deployment.get("status") != "SOFTWARE_DEPLOYED_NOT_ACTIVATED":
        raise ValueError("策略 release 不是已校验未激活状态")
    if deployment.get("archive_sha256") != EXPECTED_ARCHIVE_SHA256:
        raise ValueError("策略 ZIP SHA256 与锁定版本不一致")

    package = release / DANCE_ROOT
    required = ("deploy.yaml", "policy.onnx", "motion.npz")
    missing = [name for name in required if not (package / name).is_file()]
    if missing:
        raise ValueError(f"4 倍速舞蹈包不完整：{missing}")
    if sha256(package / "policy.onnx") != EXPECTED_POLICY_SHA256:
        raise ValueError("4 倍速舞蹈策略 SHA256 与锁定版本不一致")

    report = _json(dynamic_report, "4 倍速舞蹈 MuJoCo 报告")
    if report.get("schema") != 1 or report.get("candidate") != "q1_4x_line_b_transition":
        raise ValueError("4 倍速舞蹈 MuJoCo 报告类型不匹配")
    if report.get("backend") != "mujoco" or report.get("real_robot_enabled") is not False:
        raise ValueError("4 倍速舞蹈 MuJoCo 报告后端标识不安全")
    if report.get("passed") is not True or report.get("final_mode") != "WALK":
        raise ValueError("4 倍速舞蹈进入/播放/退出未通过")
    hashes = report.get("source_hashes", {})
    actual_hashes = {
        "line_b_machine.py": sha256(machine_source),
        "dance_4x_deploy.yaml": sha256(package / "deploy.yaml"),
        "dance_4x_policy.onnx": sha256(package / "policy.onnx"),
        "dance_4x_motion.npz": sha256(package / "motion.npz"),
    }
    if hashes != actual_hashes:
        raise ValueError("MuJoCo 报告与当前代码/策略文件不是同一版本")
    metrics = report.get("metrics", {})
    if float(metrics.get("min_upright", 0.0)) < 0.65:
        raise ValueError("MuJoCo 报告最低 upright 未达门限")
    if float(metrics.get("max_gyro_rad_s", 999.0)) > 7.5:
        raise ValueError("MuJoCo 报告最大角速度超限")
    if float(metrics.get("max_dance_target_step_rad", 999.0)) > 0.050001:
        raise ValueError("MuJoCo 报告舞蹈目标步长超限")
    return {
        "release_id": deployment.get("release_id"),
        "archive_sha256": EXPECTED_ARCHIVE_SHA256,
        "dance_policy_sha256": EXPECTED_POLICY_SHA256,
        "dynamic_passed": True,
        "min_upright": metrics.get("min_upright"),
        "max_gyro_rad_s": metrics.get("max_gyro_rad_s"),
        "max_dance_target_step_rad": metrics.get("max_dance_target_step_rad"),
        "walk_policy": f"{BUNDLE_ROOT}/weights/dual_v3b",
        "dance_policy": str(package),
    }
