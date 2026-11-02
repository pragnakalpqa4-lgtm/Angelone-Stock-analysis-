import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
import runtime_state


class RuntimeStateTests(unittest.TestCase):
    def test_manual_switch(self):
        with tempfile.TemporaryDirectory() as folder:
            with patch.object(runtime_state, "CONTROL_FILE", Path(folder) / "control.json"):
                self.assertTrue(runtime_state.is_enabled())
                runtime_state.set_enabled(False)
                self.assertFalse(runtime_state.is_enabled())
                runtime_state.set_enabled(True)
                self.assertTrue(runtime_state.is_enabled())
