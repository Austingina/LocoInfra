from __future__ import annotations

import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from apps.new_motion_web.gate import (
    BUNDLE_ROOT,
    DANCE_ROOT,
    EXPECTED_ARCHIVE_SHA256,
    EXPECTED_POLICY_SHA256,
    sha256,
    validate_real_gate,
)
from apps.new_motion_web.robot_service import environment_text, unit_text
from apps.new_motion_web.web import index_html


class NewMotionWebTest(unittest.TestCase):
    def _fixture(self, root: Path, step: float = 0.05):
        release = root / "release"
        package = release / DANCE_ROOT
        package.mkdir(parents=True)
        for name, body in {
            "deploy.yaml": b"deploy",
            "policy.onnx": b"policy",
            "motion.npz": b"motion",
        }.items():
            (package / name).write_bytes(body)
        (release / "DEPLOYMENT.json").write_text(json.dumps({
            "status": "SOFTWARE_DEPLOYED_NOT_ACTIVATED",
            "release_id": "fixture",
            "archive_sha256": EXPECTED_ARCHIVE_SHA256,
        }))
        machine = root / "machine.py"
        machine.write_text("exact transition")
        report = root / "report.json"
        report.write_text(json.dumps({
            "schema": 1,
            "candidate": "q1_4x_line_b_transition",
            "backend": "mujoco",
            "real_robot_enabled": False,
            "passed": True,
            "final_mode": "WALK",
            "metrics": {
                "min_upright": 0.92,
                "max_gyro_rad_s": 1.2,
                "max_dance_target_step_rad": step,
            },
            "source_hashes": {
                "line_b_machine.py": sha256(machine),
                "dance_4x_deploy.yaml": sha256(package / "deploy.yaml"),
                "dance_4x_policy.onnx": sha256(package / "policy.onnx"),
                "dance_4x_motion.npz": sha256(package / "motion.npz"),
            },
        }))
        return release, report, machine

    def test_gate_accepts_exact_evidence(self):
        with tempfile.TemporaryDirectory() as tmp:
            fixture = self._fixture(Path(tmp))
            policy_hash = sha256(fixture[0] / DANCE_ROOT / "policy.onnx")
            with patch("apps.new_motion_web.gate.EXPECTED_POLICY_SHA256", policy_hash):
                gate = validate_real_gate(*fixture)
        self.assertTrue(gate["dynamic_passed"])
        self.assertEqual(gate["archive_sha256"], EXPECTED_ARCHIVE_SHA256)

    def test_gate_rejects_oversized_dance_step(self):
        with tempfile.TemporaryDirectory() as tmp:
            fixture = self._fixture(Path(tmp), step=0.051)
            policy_hash = sha256(fixture[0] / DANCE_ROOT / "policy.onnx")
            with patch("apps.new_motion_web.gate.EXPECTED_POLICY_SHA256", policy_hash):
                with self.assertRaisesRegex(ValueError, "步长超限"):
                    validate_real_gate(*fixture)

    def test_new_page_keeps_controls_but_is_distinct(self):
        page = index_html().decode()
        self.assertIn("Q1 4×舞蹈与行走", page)
        self.assertIn("4×新舞蹈", page)
        self.assertIn("连续行走摇杆", page)
        self.assertIn("完整舞蹈约 30.1 秒", page)
        self.assertIn("status.route!=='B'", page)

    def test_service_unit_never_restarts_automatically(self):
        unit = unit_text(Path("/home/run/LocoInfra_Q1_Dance"), Path("/x/start"),
                         Path("/x/env"), Path("/x/preflight"))
        self.assertIn("Restart=no", unit)
        self.assertIn("ExecStartPre=/x/preflight", unit)
        self.assertNotIn("Conflicts=", unit)

    def test_environment_uses_independent_port(self):
        env = environment_text(Path("/project"), Path("/release"), Path("/report"), "Token_1", 8769)
        self.assertIn("NEW_MOTION_PORT=8769", env)
        self.assertIn("NEW_MOTION_DRY_RUN=0", env)


if __name__ == "__main__":
    unittest.main()
