from __future__ import annotations

import unittest

from apps.line_b_web.start_check import CheckFailed, status_url, validate_zero_action_status


class StartCheckTest(unittest.TestCase):
    def good_status(self) -> dict:
        return {
            "route": "B",
            "backend": "real",
            "mode": "LYING",
            "control_online": False,
            "mobility_enabled": True,
            "dance_enabled": True,
            "vx": 0.0,
            "wz": 0.0,
            "drive_input_axis": 0.0,
            "drive_input_turn_axis": 0.0,
            "drive_input_direction": "stop",
        }

    def test_accepts_real_lying_offline_zero_status(self):
        validate_zero_action_status(self.good_status())

    def test_rejects_any_motion_or_online_browser(self):
        for key, value in (("vx", 0.01), ("wz", -0.01), ("drive_input_axis", 0.2),
                           ("drive_input_turn_axis", -0.2), ("control_online", True)):
            status = self.good_status()
            status[key] = value
            with self.subTest(key=key), self.assertRaises(CheckFailed):
                validate_zero_action_status(status)

    def test_rejects_wrong_route_backend_or_mode(self):
        for key, value in (("route", "A"), ("backend", "sim"), ("mode", "STAND_HOLD"),
                           ("mobility_enabled", False), ("dance_enabled", False)):
            status = self.good_status()
            status[key] = value
            with self.subTest(key=key), self.assertRaises(CheckFailed):
                validate_zero_action_status(status)

    def test_status_url_is_robot_local_and_tokenized(self):
        self.assertEqual(
            status_url("abc_DEF-123", 8768),
            "http://127.0.0.1:8768/api/status?token=abc_DEF-123",
        )


if __name__ == "__main__":
    unittest.main()
