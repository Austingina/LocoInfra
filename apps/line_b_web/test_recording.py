from __future__ import annotations

import io
import tempfile
import unittest
import zipfile
from pathlib import Path
from types import SimpleNamespace

import numpy as np

from apps.teleop.gamepad import Pad
from apps.teleop.recorder import Recorder


class RecordingTest(unittest.TestCase):
    def test_running_session_can_be_downloaded_with_live_tail(self):
        with tempfile.TemporaryDirectory() as td:
            rec = Recorder(Path(td), ["joint"], 0.02, {"base": None}, {"route": "B"})
            sensed = SimpleNamespace(
                joint_pos=np.array([0.1]), joint_vel=np.array([0.2]),
                base_ang_vel=np.zeros(3), projected_gravity=np.array([0.0, 0.0, -1.0]),
            )
            machine = SimpleNamespace(
                mode="WALK", msg="test", speed=0.5, i=0, cmd=np.zeros(6),
                target=np.array([0.3]), kp=np.array([1.0]),
                robot=SimpleNamespace(sense=lambda: sensed),
            )
            rec.step(machine, Pad(), {"SPEED_LEVEL_2"})
            status = rec.web_status()
            self.assertEqual(status["frames"], 1)
            filename, body = rec.web_download()
            self.assertTrue(filename.endswith(".zip"))
            with zipfile.ZipFile(io.BytesIO(body)) as archive:
                names = archive.namelist()
                self.assertTrue(any(name.endswith("meta.json") for name in names))
                live_name = next(name for name in names if name.endswith("steps_live.npz"))
                with np.load(io.BytesIO(archive.read(live_name))) as live:
                    self.assertEqual(len(live["t"]), 1)
                    self.assertAlmostEqual(float(live["cmd"][0, 0]), 0.0)
            rec.close("test")


if __name__ == "__main__":
    unittest.main()
