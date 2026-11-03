import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
import runtime_state
import json
import os
import threading
import time


class RuntimeStateTests(unittest.TestCase):
    def test_invalid_control_shapes_fail_closed(self):
        with tempfile.TemporaryDirectory(dir=os.getcwd()) as folder:
            path = Path(folder) / 'control.json'
            with patch.object(runtime_state, 'CONTROL_FILE', path):
                for value in ([], False, 'enabled', {'enabled': 'true'}, {'enabled': 1}):
                    path.write_text(json.dumps(value), encoding='utf-8')
                    self.assertFalse(runtime_state.is_enabled())

    def test_atomic_reports_survive_concurrent_readers(self):
        with tempfile.TemporaryDirectory(dir=os.getcwd()) as folder:
            path = Path(folder) / 'status.json'
            runtime_state.write_json(path, {'counter': 0})
            stopped = threading.Event()
            def reader():
                while not stopped.is_set():
                    runtime_state.read_json(path, {})
                    time.sleep(.0005)
            readers = [threading.Thread(target=reader) for _ in range(4)]
            for thread in readers: thread.start()
            try:
                for counter in range(30): runtime_state.write_json(path, {'counter': counter})
            finally:
                stopped.set()
                for thread in readers: thread.join()
            self.assertEqual(runtime_state.read_json(path, {})['counter'], 29)

    def test_manual_switch(self):
        with tempfile.TemporaryDirectory() as folder:
            with patch.object(runtime_state, "CONTROL_FILE", Path(folder) / "control.json"):
                self.assertTrue(runtime_state.is_enabled())
                runtime_state.set_enabled(False)
                self.assertFalse(runtime_state.is_enabled())
                runtime_state.set_enabled(True)
                self.assertTrue(runtime_state.is_enabled())

