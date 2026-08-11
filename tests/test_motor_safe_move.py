# -*- coding: utf-8 -*-
import unittest
from pathlib import Path
import sys

PROJECT_DIR = Path(__file__).resolve().parent.parent
if str(PROJECT_DIR) not in sys.path:
    sys.path.insert(0, str(PROJECT_DIR))

from driver_motor import MDriveMotor, UnsafeStageMoveError


class MotorSafeMoveTests(unittest.TestCase):
    def test_disabled_safe_move_keeps_direct_axis_order(self):
        moves = MDriveMotor.plan_safe_xyz_move(
            {"X": 0.0, "Y": 0.0, "Z": 0.0},
            {"X": 1.0, "Y": 2.0, "Z": -0.5},
            safe_move={"enabled": False},
        )
        self.assertEqual(moves, [("X", 1.0), ("Y", 2.0), ("Z", -0.5)])

    def test_enabled_safe_move_lifts_z_before_xy_then_descends(self):
        moves = MDriveMotor.plan_safe_xyz_move(
            {"X": 0.0, "Y": 0.0, "Z": 0.2},
            {"X": 1.0, "Y": 2.0, "Z": -0.3},
            safe_move={
                "enabled": True,
                "clearance_z_mm": 1.5,
            },
        )
        self.assertEqual(moves, [("Z", 1.5), ("X", 1.0), ("Y", 2.0), ("Z", -0.3)])

    def test_enabled_safe_move_with_xy_only_uses_relative_lift_when_no_absolute_clearance(self):
        moves = MDriveMotor.plan_safe_xyz_move(
            {"X": 0.0, "Y": 0.0, "Z": 0.2},
            {"X": 1.0, "Y": 2.0, "Z": 0.2},
            safe_move={
                "enabled": True,
                "clearance_z_mm": None,
                "lift_delta_mm": 0.5,
                "positive_z_is_up": True,
            },
        )
        self.assertEqual(moves, [("Z", 0.7), ("X", 1.0), ("Y", 2.0), ("Z", 0.2)])

    def test_enabled_safe_move_rejects_xy_travel_when_current_z_is_unknown_and_no_clearance_is_set(self):
        with self.assertRaises(UnsafeStageMoveError):
            MDriveMotor.plan_safe_xyz_move(
                {"X": 0.0, "Y": 0.0, "Z": None},
                {"X": 1.0, "Y": 2.0, "Z": 0.2},
                safe_move={
                    "enabled": True,
                    "clearance_z_mm": None,
                    "lift_delta_mm": 0.5,
                    "positive_z_is_up": True,
                },
            )

    def test_move_xyz_safe_executes_planned_moves_and_logs_them(self):
        motor = MDriveMotor(port="COM_TEST")
        executed = []
        log_lines = []

        def fake_move_abs_wait(axis, target, **kwargs):
            executed.append((axis, target))

        motor.move_abs_wait = fake_move_abs_wait
        moves = motor.move_xyz_safe(
            x_mm=1.0,
            y_mm=2.0,
            z_mm=-0.3,
            current_positions={"X": 0.0, "Y": 0.0, "Z": 0.2},
            safe_move={"enabled": True, "clearance_z_mm": 1.5, "settle_s": 0.0},
            log_fn=log_lines.append,
        )

        self.assertEqual(moves, [("Z", 1.5), ("X", 1.0), ("Y", 2.0), ("Z", -0.3)])
        self.assertEqual(executed, moves)
        self.assertEqual(
            log_lines,
            [
                "  Moving Z: 0.200 -> 1.500 mm",
                "  Moving X: 0.000 -> 1.000 mm",
                "  Moving Y: 0.000 -> 2.000 mm",
                "  Moving Z: 1.500 -> -0.300 mm",
            ],
        )


if __name__ == "__main__":
    unittest.main()
