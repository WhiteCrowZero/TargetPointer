import sys
import unittest
from pathlib import Path


sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

import pointer_runtime
from pointer_runtime import DetectionCandidate, PointerRuntime


class FakePort:
    def __init__(self, device: str) -> None:
        self.device = device


class PointerRuntimeTests(unittest.TestCase):
    def test_list_serial_ports_sorts_results(self) -> None:
        original_comports = pointer_runtime.list_ports.comports
        pointer_runtime.list_ports.comports = lambda: [FakePort("COM7"), FakePort("COM3")]
        try:
            ports = pointer_runtime.list_serial_ports()
        finally:
            pointer_runtime.list_ports.comports = original_comports

        self.assertEqual(ports, ["COM3", "COM7"])

    def test_select_target_at_queues_matching_detection(self) -> None:
        runtime = PointerRuntime(detector=object(), model_name="test-model")
        runtime.state.pending_detections = [
            DetectionCandidate((10, 10, 50, 100), 0.9),
            DetectionCandidate((120, 10, 50, 100), 0.8),
        ]

        matched = runtime.select_target_at(30, 40)

        self.assertTrue(matched)
        self.assertEqual(runtime.state.pending_selection, (10, 10, 50, 100))


if __name__ == "__main__":
    unittest.main()
