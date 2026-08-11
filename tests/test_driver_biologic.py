# -*- coding: utf-8 -*-
import sys
import unittest
import inspect
from pathlib import Path
from collections import namedtuple
from types import SimpleNamespace
from unittest import mock


PROJECT_DIR = Path(__file__).resolve().parent.parent
if str(PROJECT_DIR) not in sys.path:
    sys.path.insert(0, str(PROJECT_DIR))

import driver_biologic
from driver_biologic import BioLogicController


class _DummyValues:
    State = 1
    MemFilled = 56
    ElapsedTime = 28.1
    Freq = 0.2013
    Ewe = 0.2926
    I = 2.72e-5
    Saturation = 0


class _DummyDatum:
    def __init__(self):
        self.frequency = 0.2013
        self.impedance_modulus = 1234.5
        self.impedance_phase = -0.42


class _OpaqueDatum:
    pass


class _InvalidEisDatum:
    def __init__(self, frequency, modulus, phase):
        self.frequency = frequency
        self.impedance_modulus = modulus
        self.impedance_phase = phase


class DriverBiologicDebugTests(unittest.TestCase):
    def test_ca_methods_default_to_fast_read_interval_for_stop_ready_polling(self):
        hold_default = inspect.signature(BioLogicController.run_ca_hold).parameters["read_interval"].default
        sequence_default = inspect.signature(BioLogicController.run_ca_sequence).parameters["read_interval"].default
        perturb_default = inspect.signature(BioLogicController.run_ca_perturbation).parameters["read_interval"].default

        self.assertEqual(hold_default, 0.05)
        self.assertEqual(sequence_default, 0.05)
        self.assertEqual(perturb_default, 0.05)

    def test_describe_segment_values_lists_known_scalar_fields(self):
        ctrl = BioLogicController(ip="0.0.0.0")
        text = ctrl._describe_segment_values(_DummyValues())
        self.assertIn("MemFilled=56", text)
        self.assertIn("Freq=0.2013", text)
        self.assertIn("Ewe=0.2926", text)

    def test_describe_first_eis_datum_uses_impedance_fields_when_present(self):
        ctrl = BioLogicController(ip="0.0.0.0")
        text = ctrl._describe_first_eis_datum([_DummyDatum()])
        self.assertIn("frequency=0.2013", text)
        self.assertIn("impedance_modulus=1234.5", text)
        self.assertIn("impedance_phase=-0.42", text)

    def test_describe_first_eis_datum_falls_back_to_type_and_attrs(self):
        ctrl = BioLogicController(ip="0.0.0.0")
        text = ctrl._describe_first_eis_datum([_OpaqueDatum()])
        self.assertIn("type=_OpaqueDatum", text)
        self.assertIn("attrs=", text)

    def test_parse_eis_skips_non_finite_and_absurd_impedance_rows(self):
        ctrl = BioLogicController(ip="0.0.0.0")
        parsed = ctrl._parse_eis(
            [
                _DummyDatum(),
                _InvalidEisDatum(10.0, float("inf"), 0.2),
                _InvalidEisDatum(5.0, 1.0e20, 0.1),
                _InvalidEisDatum(float("nan"), 100.0, 0.1),
            ]
        )
        self.assertEqual(parsed.shape, (1, 3))
        self.assertAlmostEqual(parsed[0, 0], 0.2013, places=4)

    def test_format_buffered_data_payload_parses_peis_process1_rows(self):
        ctrl = BioLogicController(ip="0.0.0.0")
        ctrl.dev = SimpleNamespace(kind=driver_biologic.ecl.DeviceCodes.KBIO_DEV_SP200)
        Datum = namedtuple(
            "Datum",
            [
                "frequency",
                "abs_voltage",
                "abs_current",
                "impedance_phase",
                "voltage",
                "current",
                "empty1",
                "abs_voltage_ce",
                "abs_current_ce",
                "impedance_ce_phase",
                "voltage_ce",
                "empty2",
                "empty3",
                "time",
                "current_range",
            ],
        )
        info = SimpleNamespace(
            TechniqueID=driver_biologic.ecl.TechniqueId.PEIS.value,
            ProcessIndex=1,
            NbRows=1,
            NbCols=15,
            StartTime=0.0,
        )
        values = SimpleNamespace(MemFilled=56, Freq=0.2013, Ewe=0.2926, I=2.72e-5)
        with mock.patch.object(
            driver_biologic.eparser,
            "parse",
            return_value=[
                Datum(
                    0.2013,
                    0.01,
                    0.001,
                    -0.42,
                    0.2926,
                    2.72e-5,
                    0,
                    0.01,
                    0.001,
                    -0.42,
                    0.2926,
                    0,
                    0,
                    1.0,
                    1.0,
                )
            ],
        ):
            payload = ctrl._format_buffered_data_payload([1] * 15, info, values)

        self.assertEqual(payload["technique"], "PEIS")
        self.assertEqual(payload["process_index"], 1)
        self.assertEqual(payload["parsed_array"].shape, (1, 3))
        self.assertAlmostEqual(payload["parsed_array"][0, 0], 0.2013, places=4)
        self.assertEqual(payload["values"]["buffer_bytes"], 56)

    def test_get_buffered_data_uses_low_level_ec_lib_pull(self):
        ctrl = BioLogicController(ip="0.0.0.0")
        ctrl.dev = SimpleNamespace(
            idn=123,
            kind=driver_biologic.ecl.DeviceCodes.KBIO_DEV_SP200,
        )
        info = SimpleNamespace(
            TechniqueID=driver_biologic.ecl.TechniqueId.PEIS.value,
            ProcessIndex=1,
            NbRows=0,
            NbCols=0,
            StartTime=0.0,
        )
        values = SimpleNamespace(MemFilled=0)
        with mock.patch.object(driver_biologic.ecl, "get_data", return_value=([], info, values)) as mocked_get_data:
            payload = ctrl.get_buffered_data(channel=1)

        mocked_get_data.assert_called_once_with(123, 0)
        self.assertEqual(payload["technique"], "PEIS")
        self.assertEqual(payload["nb_rows"], 0)


if __name__ == "__main__":
    unittest.main()
