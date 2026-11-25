from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest import mock

from apps.line_b_web.q1_line_b_boot_preflight import run_preflight
from apps.line_b_web.robot_service import (
    SERVICE,
    _paths,
    _read_environment,
    access_urls,
    environment_text,
    local_controller_mode,
    unit_text,
)


class RobotServiceTest(unittest.TestCase):
    def test_unit_is_robot_hosted_manual_and_non_restarting(self):
        text = unit_text(
            Path("/home/run/LocoInfra_Q1_Dance"),
            Path("/home/run/LocoInfra_Q1_Dance/apps/line_b_web/start_robot_service"),
            Path("/home/run/.config/q1-line-b-web/environment"),
            Path("/home/run/q1_line_b_boot_preflight.py"),
        )
        self.assertIn("WorkingDirectory=/home/run/LocoInfra_Q1_Dance", text)
        self.assertIn("EnvironmentFile=/home/run/.config/q1-line-b-web/environment", text)
        self.assertIn("ExecStart=/bin/bash", text)
        self.assertIn("ExecStartPre=/home/run/q1_line_b_boot_preflight.py", text)
        self.assertIn("Restart=no", text)
        self.assertIn("TimeoutStartSec=120", text)
        self.assertNotIn("Restart=always", text)
        self.assertNotIn("WantedBy=multi-user.target", text)

    def test_environment_round_trip_and_permissions_location(self):
        with tempfile.TemporaryDirectory() as td:
            home = Path(td)
            _, env_file = _paths(home)
            env_file.parent.mkdir(parents=True)
            env_file.write_text(environment_text(Path("/home/run/LocoInfra_Q1_Dance"), "abc_DEF-123", 8768))
            values = _read_environment(env_file)
            self.assertEqual(values["LINE_B_PROJECT"], "/home/run/LocoInfra_Q1_Dance")
            self.assertEqual(values["LINE_B_TOKEN"], "abc_DEF-123")
            self.assertEqual(values["LINE_B_PORT"], "8768")
            self.assertEqual(_paths(home)[0].name, SERVICE)

    def test_urls_use_robot_interface_addresses_not_mac_address(self):
        addresses = [("p2p0", "192.168.88.88"), ("eth0", "10.1.1.100")]
        self.assertEqual(
            access_urls("secret", 8768, addresses),
            [
                "http://192.168.88.88:8768/?token=secret",
                "http://10.1.1.100:8768/?token=secret",
            ],
        )

    def test_rejects_unsafe_token(self):
        with self.assertRaisesRegex(ValueError, "URL-safe"):
            environment_text(Path("/home/run/project"), "bad token", 8768)

    def test_start_check_wrapper_is_present(self):
        wrapper = Path(__file__).with_name("q1_line_b_start_check.py")
        self.assertTrue(wrapper.is_file())
        self.assertIn("apps.line_b_web.start_check", wrapper.read_text())

    def test_robot_runner_enables_nounset_only_after_vendor_environment(self):
        runner = Path(__file__).with_name("start_robot_service")
        text = runner.read_text()
        self.assertIn("set -eo pipefail", text)
        self.assertLess(text.index("source /robot/etc/basic_env"), text.index("set -u"))
        self.assertLess(text.index("source /robot/software/interface/local_setup.bash"), text.index("set -u"))

    def test_boot_preflight_is_present(self):
        preflight = Path(__file__).with_name("q1_line_b_boot_preflight.py")
        self.assertTrue(preflight.is_file())
        self.assertIn("q1_lowlevel_check.py", preflight.read_text())

    def test_boot_preflight_retries_then_passes(self):
        with tempfile.TemporaryDirectory() as td:
            checker = Path(td) / "check"
            checker.touch()
            results = [mock.Mock(returncode=1), mock.Mock(returncode=0)]
            with mock.patch("apps.line_b_web.q1_line_b_boot_preflight.subprocess.run", side_effect=results) as run:
                with mock.patch("apps.line_b_web.q1_line_b_boot_preflight.time.sleep") as sleep:
                    self.assertEqual(run_preflight(3, 0.1, 2.0, checker), 0)
            self.assertEqual(run.call_count, 2)
            sleep.assert_called_once_with(0.1)

    def test_boot_preflight_persistent_failure_rejects_start(self):
        with tempfile.TemporaryDirectory() as td:
            checker = Path(td) / "check"
            checker.touch()
            with mock.patch(
                "apps.line_b_web.q1_line_b_boot_preflight.subprocess.run",
                return_value=mock.Mock(returncode=1),
            ) as run:
                with mock.patch("apps.line_b_web.q1_line_b_boot_preflight.time.sleep") as sleep:
                    self.assertEqual(run_preflight(2, 0.1, 2.0, checker), 2)
            self.assertEqual(run.call_count, 2)
            sleep.assert_called_once_with(0.1)

    def test_uninstalled_local_controller_has_no_mode(self):
        with tempfile.TemporaryDirectory() as td:
            self.assertIsNone(local_controller_mode(Path(td) / "missing-environment"))


if __name__ == "__main__":
    unittest.main()
