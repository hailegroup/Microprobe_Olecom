import unittest

from tools.run_adaptive_bias_sweep import _backfill_pending_summary


class _FakeEngine:
    def __init__(self, mapping):
        self.mapping = dict(mapping)
        self.calls = []

    def finalize_completed_point(self, point_id, *, timeout_s=None):
        self.calls.append((point_id, timeout_s))
        return dict(self.mapping[point_id])


class RunAdaptiveBiasSweepSummaryTests(unittest.TestCase):
    def test_backfill_refreshes_pending_finalize_entries(self):
        engine = _FakeEngine(
            {
                "row0001_demo": {
                    "point_id": "row0001_demo",
                    "analysis_ready_within_wait": True,
                    "state": "recommendation_ready",
                    "analysis_result": {"peis_only_sufficient": True},
                    "full_processing_state": "completed",
                    "remeasurement_request": None,
                }
            }
        )
        summary = {
            "points": [
                {
                    "point_id": "row0001_demo",
                    "analysis_finalize": {
                        "point_id": "row0001_demo",
                        "analysis_ready_within_wait": False,
                        "state": "analysis_pending",
                        "analysis_result": None,
                        "full_processing_state": "processing",
                        "remeasurement_request": None,
                    },
                }
            ]
        }

        updated = _backfill_pending_summary(engine, summary, timeout_s=0.0)

        self.assertEqual(updated, 1)
        self.assertEqual(len(engine.calls), 1)
        self.assertTrue(summary["points"][0]["analysis_finalize"]["analysis_ready_within_wait"])
        self.assertEqual(
            summary["points"][0]["analysis_finalize"]["state"],
            "recommendation_ready",
        )

    def test_backfill_skips_points_without_point_id(self):
        engine = _FakeEngine({})
        summary = {"points": [{"analysis_finalize": {"state": "analysis_pending"}}]}

        updated = _backfill_pending_summary(engine, summary, timeout_s=0.0)

        self.assertEqual(updated, 0)
        self.assertEqual(engine.calls, [])


if __name__ == "__main__":
    unittest.main()
