# -*- coding: utf-8 -*-
import os
import tempfile
import unittest
from pathlib import Path
import sys

PROJECT_DIR = Path(__file__).resolve().parent.parent
if str(PROJECT_DIR) not in sys.path:
    sys.path.insert(0, str(PROJECT_DIR))

from vision.electrode_z_seed import ElectrodeZCalibrationStore


class RecordContactTests(unittest.TestCase):
    def test_updates_seed_with_no_parallax_sample(self):
        store = ElectrodeZCalibrationStore()
        fit_happened = store.record_contact(0, 12.34, source='auto_contact')
        self.assertFalse(fit_happened)
        self.assertAlmostEqual(store.get_seed(0), 12.34, places=6)
        self.assertEqual(store.parallax_samples, [])
        self.assertIsNone(store.z_parallax)

    def test_latest_contact_overwrites_seed(self):
        store = ElectrodeZCalibrationStore()
        store.record_contact(0, 12.00, source='auto_contact')
        store.record_contact(0, 12.05, source='auto_contact')
        self.assertAlmostEqual(store.get_seed(0), 12.05, places=6)

    def test_unknown_electrode_returns_none(self):
        store = ElectrodeZCalibrationStore()
        self.assertIsNone(store.get_seed(7))

    def test_full_sample_appends_parallax_sample(self):
        store = ElectrodeZCalibrationStore()
        # parallax_sample = (z_start, pixel_start, z_end, pixel_end) -- note
        # z_end (pre-engage contact Z) is deliberately different from the
        # z_mm seed argument (post-engage measure_z) to exercise that they
        # are NOT conflated.
        store.record_contact(
            0, 12.35, source='auto_contact',
            parallax_sample=(12.00, (500.0, 300.0), 12.30, (506.4, 296.7)),
        )
        self.assertAlmostEqual(store.get_seed(0), 12.35, places=6)
        self.assertEqual(len(store.parallax_samples), 1)
        sample = store.parallax_samples[0]
        self.assertAlmostEqual(sample.delta_z_mm, 0.30, places=6)
        self.assertAlmostEqual(sample.delta_pixel_x, 6.4, places=6)
        self.assertAlmostEqual(sample.delta_pixel_y, -3.3, places=6)

    def test_refit_triggers_at_third_sample(self):
        store = ElectrodeZCalibrationStore()
        true_du, true_dv = 4.0, -2.0
        for i, delta_z in enumerate([0.10, 0.25, 0.40]):
            fit_happened = store.record_contact(
                i, 12.0 + delta_z + 0.1, source='auto_contact',
                parallax_sample=(
                    12.0, (0.0, 0.0),
                    12.0 + delta_z, (delta_z * true_du, delta_z * true_dv),
                ),
            )
            if i < 2:
                self.assertFalse(fit_happened)
                self.assertIsNone(store.z_parallax)
            else:
                self.assertTrue(fit_happened)
                self.assertIsNotNone(store.z_parallax)
                self.assertAlmostEqual(store.z_parallax.du_per_mm, true_du, places=4)
                self.assertAlmostEqual(store.z_parallax.dv_per_mm, true_dv, places=4)

    def test_no_parallax_sample_does_not_append(self):
        store = ElectrodeZCalibrationStore()
        fit_happened = store.record_contact(0, 12.30, source='auto_contact', parallax_sample=None)
        self.assertFalse(fit_happened)
        self.assertEqual(store.parallax_samples, [])


class XYBiasSampleTests(unittest.TestCase):
    def test_record_xy_bias_sample_appends_delta(self):
        store = ElectrodeZCalibrationStore()
        fit_happened = store.record_xy_bias_sample(
            observed_px=100.2, observed_py=199.9, expected_px=100.0, expected_py=200.0,
        )
        # No temperature_c given -> can't belong to any setpoint bucket yet.
        self.assertFalse(fit_happened)
        self.assertEqual(len(store.xy_bias_samples), 1)
        sample = store.xy_bias_samples[0]
        self.assertAlmostEqual(sample.delta_x_px, 0.2, places=6)
        self.assertAlmostEqual(sample.delta_y_px, -0.1, places=6)
        self.assertIsNone(sample.temperature_c)
        self.assertIsNone(store.get_xy_bias(300.0))

    def test_refit_triggers_at_third_sample_for_same_temperature(self):
        store = ElectrodeZCalibrationStore()
        for i in range(3):
            fit_happened = store.record_xy_bias_sample(
                observed_px=100.2, observed_py=199.9, expected_px=100.0, expected_py=200.0,
                temperature_c=300.0,
            )
            if i < 2:
                self.assertFalse(fit_happened)
                self.assertIsNone(store.get_xy_bias(300.0))
            else:
                self.assertTrue(fit_happened)
                bias = store.get_xy_bias(300.0)
                self.assertIsNotNone(bias)
                self.assertAlmostEqual(bias.bias_x_px, 0.2, places=6)
                self.assertAlmostEqual(bias.bias_y_px, -0.1, places=6)

    def test_get_xy_bias_uses_nearest_other_bucket_when_current_has_too_few(self):
        store = ElectrodeZCalibrationStore()
        for _ in range(3):
            store.record_xy_bias_sample(
                observed_px=10.2, observed_py=5.0, expected_px=10.0, expected_py=5.0,
                temperature_c=100.0,
            )
        for _ in range(3):
            store.record_xy_bias_sample(
                observed_px=20.3, observed_py=8.0, expected_px=20.0, expected_py=8.0,
                temperature_c=200.0,
            )
        # 160 has no samples of its own -- 200 is closer to 160 than 100 is.
        bias = store.get_xy_bias(160.0)
        self.assertAlmostEqual(bias.bias_x_px, 0.3, places=6)
        # 110 has no samples of its own either -- 100 is closer.
        bias = store.get_xy_bias(110.0)
        self.assertAlmostEqual(bias.bias_x_px, 0.2, places=6)

    def test_get_xy_bias_falls_back_to_pooled_samples_when_nothing_bucketed(self):
        store = ElectrodeZCalibrationStore()
        # No temperature_c on any sample -- none of them can ever form a
        # setpoint bucket, so the only possible fit is the pooled fallback.
        for _ in range(3):
            store.record_xy_bias_sample(
                observed_px=10.2, observed_py=5.0, expected_px=10.0, expected_py=5.0,
            )
        bias = store.get_xy_bias(300.0)
        self.assertIsNotNone(bias)
        self.assertAlmostEqual(bias.bias_x_px, 0.2, places=6)

    def test_get_xy_bias_none_below_three_samples_total(self):
        store = ElectrodeZCalibrationStore()
        store.record_xy_bias_sample(
            observed_px=10.2, observed_py=5.0, expected_px=10.0, expected_py=5.0,
            temperature_c=100.0,
        )
        self.assertIsNone(store.get_xy_bias(100.0))
        self.assertIsNone(store.get_xy_bias(None))

    def test_revisiting_a_temperature_never_drops_earlier_history(self):
        # Samples at every temperature ever visited are kept forever, even
        # across a later visit to a completely different setpoint -- this
        # is the whole point of bucketing instead of resetting.
        store = ElectrodeZCalibrationStore()
        for _ in range(3):
            store.record_xy_bias_sample(
                observed_px=10.2, observed_py=5.0, expected_px=10.0, expected_py=5.0,
                temperature_c=100.0,
            )
        for _ in range(3):
            store.record_xy_bias_sample(
                observed_px=20.3, observed_py=8.0, expected_px=20.0, expected_py=8.0,
                temperature_c=200.0,
            )
        self.assertEqual(len(store.xy_bias_samples), 6)
        # 100's own bucket still fits correctly, undiluted by 200's data.
        bias_100 = store.get_xy_bias(100.0)
        self.assertAlmostEqual(bias_100.bias_x_px, 0.2, places=6)
        bias_200 = store.get_xy_bias(200.0)
        self.assertAlmostEqual(bias_200.bias_x_px, 0.3, places=6)

    def test_xy_bias_sample_count_near_reports_own_bucket_size(self):
        store = ElectrodeZCalibrationStore()
        store.record_xy_bias_sample(
            observed_px=10.2, observed_py=5.0, expected_px=10.0, expected_py=5.0,
            temperature_c=100.0,
        )
        store.record_xy_bias_sample(
            observed_px=10.3, observed_py=5.0, expected_px=10.0, expected_py=5.0,
            temperature_c=100.0,
        )
        self.assertEqual(store.xy_bias_sample_count_near(100.0), 2)
        self.assertEqual(store.xy_bias_sample_count_near(200.0), 0)
        self.assertEqual(store.xy_bias_sample_count_near(None), 0)

    def test_record_contact_is_independent_of_xy_bias_samples(self):
        # record_contact (Z-seed/parallax) and record_xy_bias_sample are
        # separate accumulators fed from the same touch, but neither should
        # affect the other's state.
        store = ElectrodeZCalibrationStore()
        store.record_contact(0, 12.0, source='auto_contact')
        self.assertEqual(store.xy_bias_samples, [])
        store.record_xy_bias_sample(observed_px=1.0, observed_py=1.0, expected_px=0.0, expected_py=0.0)
        self.assertEqual(len(store.parallax_samples), 0)
        self.assertAlmostEqual(store.get_seed(0), 12.0, places=6)


class ZPlaneFitTests(unittest.TestCase):
    def test_get_seed_or_estimate_returns_none_before_three_points(self):
        store = ElectrodeZCalibrationStore()
        store.record_contact(0, 10.00, source='auto_contact', xy_mm=(0.0, 0.0))
        store.record_contact(1, 10.20, source='auto_contact', xy_mm=(10.0, 0.0))
        self.assertIsNone(store.z_plane)
        self.assertIsNone(store.get_seed_or_estimate(5, xy_mm=(5.0, 5.0)))

    def test_recovers_known_tilted_plane_from_three_points(self):
        # True plane: Z = 10.0 + 0.02*u - 0.01*v
        store = ElectrodeZCalibrationStore()
        points = [(0, (0.0, 0.0)), (1, (10.0, 0.0)), (2, (0.0, 10.0))]
        for layout_index, (u, v) in points:
            z = 10.0 + 0.02 * u - 0.01 * v
            store.record_contact(layout_index, z, source='auto_contact', xy_mm=(u, v))

        self.assertIsNotNone(store.z_plane)
        self.assertAlmostEqual(store.z_plane.a, 0.02, places=6)
        self.assertAlmostEqual(store.z_plane.b, -0.01, places=6)
        self.assertAlmostEqual(store.z_plane.c, 10.0, places=6)
        self.assertEqual(store.z_plane.n_points, 3)

        # Electrode 9 was never measured -- estimate at its layout position.
        estimated = store.get_seed_or_estimate(9, xy_mm=(4.0, 6.0))
        self.assertAlmostEqual(estimated, 10.0 + 0.02 * 4.0 - 0.01 * 6.0, places=6)

    def test_exact_measurement_wins_over_plane_estimate(self):
        store = ElectrodeZCalibrationStore()
        for layout_index, (u, v) in [(0, (0.0, 0.0)), (1, (10.0, 0.0)), (2, (0.0, 10.0))]:
            store.record_contact(layout_index, 10.0 + 0.02 * u, source='auto_contact', xy_mm=(u, v))
        # Electrode 1 also has its own exact seed -- must be returned as-is,
        # not the plane's prediction at its position, even though both exist.
        result = store.get_seed_or_estimate(1, xy_mm=(10.0, 0.0))
        self.assertAlmostEqual(result, store.get_seed(1), places=6)

    def test_seeds_without_xy_mm_are_excluded_from_plane_fit(self):
        store = ElectrodeZCalibrationStore()
        store.record_contact(0, 10.0, source='auto_contact')  # no xy_mm
        store.record_contact(1, 10.2, source='auto_contact', xy_mm=(10.0, 0.0))
        store.record_contact(2, 10.4, source='auto_contact', xy_mm=(0.0, 10.0))
        self.assertIsNone(store.z_plane)

    def test_collinear_points_do_not_produce_a_degenerate_fit(self):
        store = ElectrodeZCalibrationStore()
        for layout_index, u in enumerate([0.0, 5.0, 10.0]):
            store.record_contact(layout_index, 10.0 + 0.1 * u, source='auto_contact', xy_mm=(u, 0.0))
        self.assertIsNone(store.z_plane)

    def test_bad_refit_does_not_clobber_previous_good_plane(self):
        store = ElectrodeZCalibrationStore()
        for layout_index, (u, v) in [(0, (0.0, 0.0)), (1, (10.0, 0.0)), (2, (0.0, 10.0))]:
            store.record_contact(layout_index, 10.0 + 0.02 * u, source='auto_contact', xy_mm=(u, v))
        good_plane = store.z_plane
        self.assertIsNotNone(good_plane)
        # A 4th point collinear with two existing ones still leaves >=3
        # independent points overall, so the fit should simply update, not
        # break -- this test just confirms z_plane is never wiped by a call
        # that doesn't itself fail.
        store.record_contact(3, 10.5, source='auto_contact', xy_mm=(5.0, 5.0))
        self.assertIsNotNone(store.z_plane)


class PersistenceTests(unittest.TestCase):
    def test_round_trip_save_load(self):
        store = ElectrodeZCalibrationStore()
        for i, delta_z in enumerate([0.10, 0.25, 0.40]):
            store.record_contact(
                i, 12.0 + delta_z, source='auto_contact',
                parallax_sample=(12.0, (0.0, 0.0), 12.0 + delta_z, (delta_z * 3.0, delta_z * -1.5)),
            )
        with tempfile.TemporaryDirectory() as tmpdir:
            path = os.path.join(tmpdir, 'z_seed.json')
            store.save(path)
            loaded = ElectrodeZCalibrationStore.load(path)

        self.assertEqual(set(loaded.electrode_z_seeds.keys()), {0, 1, 2})
        self.assertAlmostEqual(loaded.get_seed(2), 12.40, places=6)
        self.assertEqual(len(loaded.parallax_samples), 3)
        self.assertIsNotNone(loaded.z_parallax)
        self.assertAlmostEqual(loaded.z_parallax.du_per_mm, 3.0, places=4)
        self.assertAlmostEqual(loaded.z_parallax.dv_per_mm, -1.5, places=4)

    def test_load_missing_file_returns_empty_store(self):
        store = ElectrodeZCalibrationStore.load('/nonexistent/path/z_seed.json')
        self.assertEqual(store.electrode_z_seeds, {})
        self.assertEqual(store.parallax_samples, [])
        self.assertIsNone(store.z_parallax)

    def test_load_corrupt_file_returns_empty_store(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            path = os.path.join(tmpdir, 'z_seed.json')
            with open(path, 'w', encoding='utf-8') as fh:
                fh.write('{not valid json')
            store = ElectrodeZCalibrationStore.load(path)
        self.assertEqual(store.electrode_z_seeds, {})
        self.assertEqual(store.parallax_samples, [])
        self.assertIsNone(store.z_parallax)

    def test_round_trip_preserves_xy_mm_and_refits_z_plane(self):
        store = ElectrodeZCalibrationStore()
        for layout_index, (u, v) in [(0, (0.0, 0.0)), (1, (10.0, 0.0)), (2, (0.0, 10.0))]:
            store.record_contact(layout_index, 10.0 + 0.02 * u, source='auto_contact', xy_mm=(u, v))
        with tempfile.TemporaryDirectory() as tmpdir:
            path = os.path.join(tmpdir, 'z_seed.json')
            store.save(path)
            loaded = ElectrodeZCalibrationStore.load(path)

        self.assertEqual(loaded.electrode_z_seeds[1].xy_mm, (10.0, 0.0))
        self.assertIsNotNone(loaded.z_plane)
        self.assertAlmostEqual(loaded.z_plane.a, 0.02, places=6)
        self.assertAlmostEqual(
            loaded.get_seed_or_estimate(9, xy_mm=(4.0, 6.0)),
            store.get_seed_or_estimate(9, xy_mm=(4.0, 6.0)),
            places=6,
        )

    def test_old_records_without_xy_mm_load_without_plane(self):
        payload = {
            'electrode_z_seeds': {
                '0': {'z_mm': 10.0, 'source': 'auto_contact', 'updated_at': ''},
                '1': {'z_mm': 10.2, 'source': 'auto_contact', 'updated_at': ''},
                '2': {'z_mm': 10.4, 'source': 'auto_contact', 'updated_at': ''},
            },
            'parallax_samples': [],
        }
        store = ElectrodeZCalibrationStore.from_dict(payload)
        self.assertIsNone(store.z_plane)
        self.assertIsNone(store.electrode_z_seeds[0].xy_mm)
        self.assertAlmostEqual(store.get_seed(0), 10.0, places=6)

    def test_old_file_without_xy_bias_samples_key_loads_fine(self):
        # Files saved before xy_bias tracking existed have no such key at
        # all -- must load tolerantly, same as the xy_mm/z_plane case above.
        payload = {
            'electrode_z_seeds': {
                '0': {'z_mm': 10.0, 'source': 'auto_contact', 'updated_at': ''},
            },
            'parallax_samples': [],
        }
        store = ElectrodeZCalibrationStore.from_dict(payload)
        self.assertEqual(store.xy_bias_samples, [])
        self.assertIsNone(store.get_xy_bias(None))

    def test_round_trip_preserves_xy_bias_samples_and_refits(self):
        store = ElectrodeZCalibrationStore()
        for _ in range(3):
            store.record_xy_bias_sample(
                observed_px=100.2, observed_py=199.9, expected_px=100.0, expected_py=200.0,
                temperature_c=300.0,
            )
        with tempfile.TemporaryDirectory() as tmpdir:
            path = os.path.join(tmpdir, 'z_seed.json')
            store.save(path)
            loaded = ElectrodeZCalibrationStore.load(path)

        self.assertEqual(len(loaded.xy_bias_samples), 3)
        self.assertEqual(loaded.xy_bias_samples[0].temperature_c, 300.0)
        bias = loaded.get_xy_bias(300.0)
        self.assertIsNotNone(bias)
        self.assertAlmostEqual(bias.bias_x_px, 0.2, places=6)
        self.assertAlmostEqual(bias.bias_y_px, -0.1, places=6)


class ClearAndRefitZPlaneTests(unittest.TestCase):
    def test_clear_resets_everything(self):
        store = ElectrodeZCalibrationStore()
        store.record_contact(0, 1.0, source='auto_contact', xy_mm=(0.0, 0.0))
        store.record_contact(1, 2.0, source='auto_contact', xy_mm=(1.0, 0.0))
        store.record_contact(2, 3.0, source='auto_contact', xy_mm=(0.0, 1.0))
        store.record_xy_bias_sample(observed_px=1.0, observed_py=1.0, expected_px=0.0, expected_py=0.0)
        self.assertIsNotNone(store.z_plane)

        store.clear()

        self.assertEqual(store.electrode_z_seeds, {})
        self.assertEqual(store.parallax_samples, [])
        self.assertIsNone(store.z_parallax)
        self.assertIsNone(store.z_plane)
        self.assertEqual(store.xy_bias_samples, [])
        self.assertIsNone(store.get_xy_bias(None))

    def test_refit_z_plane_public_method_matches_automatic_fit(self):
        store = ElectrodeZCalibrationStore()
        store.record_contact(0, 1.0, source='auto_contact', xy_mm=(0.0, 0.0))
        store.record_contact(1, 2.0, source='auto_contact', xy_mm=(1.0, 0.0))
        store.record_contact(2, 3.0, source='auto_contact', xy_mm=(0.0, 1.0))
        # record_contact already refits automatically -- refit_z_plane() is
        # a manually-triggerable equivalent, e.g. for a GUI button after a
        # Clear + reseed.
        self.assertIsNotNone(store.z_plane)
        store.z_plane = None

        result = store.refit_z_plane()

        self.assertTrue(result)
        self.assertIsNotNone(store.z_plane)
        self.assertEqual(store.z_plane.n_points, 3)

    def test_refit_z_plane_returns_false_with_fewer_than_three_points(self):
        store = ElectrodeZCalibrationStore()
        store.record_contact(0, 1.0, source='auto_contact', xy_mm=(0.0, 0.0))
        self.assertFalse(store.refit_z_plane())
        self.assertIsNone(store.z_plane)


if __name__ == "__main__":
    unittest.main()
