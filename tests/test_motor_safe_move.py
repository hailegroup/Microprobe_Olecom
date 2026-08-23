# -*- coding: utf-8 -*-
import unittest
from pathlib import Path
from unittest import mock
import sys

PROJECT_DIR = Path(__file__).resolve().parent.parent
if str(PROJECT_DIR) not in sys.path:
    sys.path.insert(0, str(PROJECT_DIR))

from driver_motor import MDriveMotor, UnsafeStageMoveError


class MotorSafeMoveTests(unittest.TestCase):
    """
    All safe_move dicts here explicitly set backlash_overshoot_mm=0 so these
    stay focused on the Z-lift-before-XY behavior -- backlash compensation
    is covered separately by BacklashCompensationTests below.
    """

    def test_disabled_safe_move_keeps_direct_axis_order(self):
        moves = MDriveMotor.plan_safe_xyz_move(
            {"X": 0.0, "Y": 0.0, "Z": 0.0},
            {"X": 1.0, "Y": 2.0, "Z": -0.5},
            safe_move={"enabled": False, "backlash_overshoot_mm": 0},
        )
        self.assertEqual(moves, [("X", 1.0), ("Y", 2.0), ("Z", -0.5)])

    def test_enabled_safe_move_lifts_z_before_xy_then_descends(self):
        moves = MDriveMotor.plan_safe_xyz_move(
            {"X": 0.0, "Y": 0.0, "Z": 0.2},
            {"X": 1.0, "Y": 2.0, "Z": -0.3},
            safe_move={
                "enabled": True,
                "clearance_z_mm": 1.5,
                "backlash_overshoot_mm": 0,
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
                "backlash_overshoot_mm": 0,
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
                    "backlash_overshoot_mm": 0,
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
            safe_move={"enabled": True, "clearance_z_mm": 1.5, "settle_s": 0.0, "backlash_overshoot_mm": 0},
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


class BacklashCompensationTests(unittest.TestCase):
    def test_default_config_applies_0_3mm_overshoot_to_xy_only(self):
        # Z's own overshoot ('z_backlash_overshoot_mm') defaults to 0 --
        # a Z target can sit right at the sample surface, so it isn't safe
        # to force every Z move through a guaranteed-minimum final descent
        # the way X/Y moves are. See BacklashCompensationTests below for
        # Z overshoot explicitly configured on.
        moves = MDriveMotor.plan_safe_xyz_move(
            {"X": 0.0, "Y": 0.0, "Z": 0.2},
            {"X": 1.0, "Y": 2.0, "Z": -0.3},
            safe_move={"enabled": True, "clearance_z_mm": 1.5},
        )
        self.assertEqual(
            moves,
            [
                ("Z", 1.5),          # transient travel clearance -- not compensated
                ("X", 0.7), ("X", 1.0),
                ("Y", 1.7), ("Y", 2.0),
                ("Z", -0.3),          # Z overshoot is 0 by default
            ],
        )

    def test_overshoot_disabled_with_zero_matches_uncompensated_moves(self):
        moves = MDriveMotor.plan_safe_xyz_move(
            {"X": 0.0, "Y": 0.0, "Z": 0.0},
            {"X": 1.0, "Y": 2.0, "Z": -0.5},
            safe_move={"enabled": False, "backlash_overshoot_mm": 0},
        )
        self.assertEqual(moves, [("X", 1.0), ("Y", 2.0), ("Z", -0.5)])

    def test_xy_overshoot_applies_even_when_z_lift_safety_is_disabled(self):
        moves = MDriveMotor.plan_safe_xyz_move(
            {"X": 0.0, "Y": 0.0, "Z": 0.0},
            {"X": 1.0, "Y": 2.0, "Z": -0.5},
            safe_move={"enabled": False, "backlash_overshoot_mm": 0.3},
        )
        self.assertEqual(
            moves,
            [
                ("X", 0.7), ("X", 1.0),
                ("Y", 1.7), ("Y", 2.0),
                ("Z", -0.5),          # Z overshoot is a separate, 0-by-default setting
            ],
        )

    def test_z_overshoot_is_independent_of_xy_overshoot(self):
        moves = MDriveMotor.plan_safe_xyz_move(
            {"X": 0.0, "Y": 0.0, "Z": 0.0},
            {"X": 1.0, "Y": 2.0, "Z": -0.5},
            safe_move={
                "enabled": False,
                "backlash_overshoot_mm": 0,
                "z_backlash_overshoot_mm": 0.3,
            },
        )
        self.assertEqual(
            moves,
            [
                ("X", 1.0),
                ("Y", 2.0),
                ("Z", -0.8), ("Z", -0.5),
            ],
        )

    def test_overshoot_final_leg_always_approaches_from_the_same_direction(self):
        """Whether the axis needs to move up or down to reach the target,
        the last leg is always a positive-direction move onto the target --
        the whole point of the compensation."""
        moves_up = MDriveMotor.plan_safe_xyz_move(
            {"X": 0.0}, {"X": 5.0},
            safe_move={"enabled": False, "backlash_overshoot_mm": 0.3},
        )
        moves_down = MDriveMotor.plan_safe_xyz_move(
            {"X": 5.0}, {"X": 0.0},
            safe_move={"enabled": False, "backlash_overshoot_mm": 0.3},
        )
        for moves in (moves_up, moves_down):
            (_, overshoot_target), (_, final_target) = moves
            self.assertGreater(final_target, overshoot_target)
            self.assertAlmostEqual(final_target - overshoot_target, 0.3)

    def test_move_xyz_safe_executes_overshoot_legs_in_order(self):
        motor = MDriveMotor(port="COM_TEST")
        executed = []

        def fake_move_abs_wait(axis, target, **kwargs):
            executed.append((axis, target))

        motor.move_abs_wait = fake_move_abs_wait
        moves = motor.move_xyz_safe(
            x_mm=1.0,
            current_positions={"X": 0.0},
            safe_move={"enabled": False, "backlash_overshoot_mm": 0.3},
        )

        self.assertEqual(moves, [("X", 0.7), ("X", 1.0)])
        self.assertEqual(executed, moves)


class StopCommandTests(unittest.TestCase):
    def test_stop_sends_stop_not_sl_0(self):
        # "SL 0" was tried first (a common shorthand in reference material
        # for this command set) but confirmed on hardware to NOT preempt
        # an in-progress MA/MR position move -- the axis just ran the move
        # to completion and ignored it. "STOP" is the command that
        # actually aborts in-progress motion.
        motor = MDriveMotor(port="COM_TEST")
        with mock.patch.object(motor, "_send") as mocked_send:
            motor.stop("Z")
        mocked_send.assert_called_once_with("Z", "STOP")


if __name__ == "__main__":
    unittest.main()
