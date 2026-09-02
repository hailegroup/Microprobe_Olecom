# -*- coding: utf-8 -*-
"""
WatlowController's _io_lock guards every self._w call -- self._w wraps a
raw pyserial port, read/written from both the temperature-safety-monitor
background thread (runs for the full duration of every automated run) and
the run thread itself (set_temperature/wait_stable during a ramp),
concurrently. A Win7 faulthandler crash dump caught the safety-monitor
thread faulting (0xc0000005) inside this driver's own read path while the
run thread was concurrently active; driver_motor.py already had the
matching lock for the identical shared-serial-port-across-threads hazard,
this driver didn't until now. These tests prove the lock actually
synchronizes -- not just decorative -- matching the pattern used elsewhere
in this project for the analogous gui.py/vision-tracking lock.
"""
import threading
import unittest

import driver_temp


class _FakeWatlow:
    def __init__(self):
        self.serial = _FakeSerial()

    def read(self, _name):
        return {'data': 100.0, 'error': None}

    def readParam(self, _param, _type):
        return {'data': 5.0, 'error': None}

    def write(self, _value):
        return {'data': 1.0, 'error': None}

    def writeParam(self, _param, _value, _type):
        return {'data': 1.0, 'error': None}


class _FakeSerial:
    def close(self):
        pass


class WatlowControllerLockTests(unittest.TestCase):
    def setUp(self):
        self.controller = driver_temp.WatlowController(port='COM_FAKE', baud=9600, addr=1)
        self.controller._w = _FakeWatlow()

    def test_get_temperature_blocks_while_io_lock_is_held(self):
        result_holder = []
        self.controller._io_lock.acquire()
        try:
            thread = threading.Thread(
                target=lambda: result_holder.append(self.controller.get_temperature())
            )
            thread.start()
            thread.join(timeout=0.3)
            self.assertTrue(thread.is_alive())
            self.assertEqual(result_holder, [])
        finally:
            self.controller._io_lock.release()
        thread.join(timeout=5)
        self.assertFalse(thread.is_alive())
        self.assertEqual(len(result_holder), 1)

    def test_get_temperature_converts_fahrenheit_reading_to_celsius(self):
        # _FakeWatlow.read returns 100.0 (raw pywatlow units, Fahrenheit).
        self.assertAlmostEqual(self.controller.get_temperature(), (100.0 - 32.0) * 5.0 / 9.0)

    def test_set_ramp_rate_is_reentrant_with_its_own_readback(self):
        # set_ramp_rate calls self.get_ramp_rate() for its readback while
        # already holding _io_lock -- must not deadlock (RLock, same
        # thread re-acquiring).
        self.controller.set_ramp_rate(5.0)

    def test_set_temperature_is_reentrant_with_its_own_readback(self):
        # set_temperature calls self.get_setpoint() for its readback while
        # already holding _io_lock -- must not deadlock (RLock, same
        # thread re-acquiring).
        self.controller.set_temperature(600.0)


if __name__ == '__main__':
    unittest.main()
