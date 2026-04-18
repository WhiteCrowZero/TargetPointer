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

    def test_runtime_keeps_last_output_angle_when_unsent_change_is_below_threshold(self) -> None:
        runtime = PointerRuntime(
            detector=object(),
            model_name="test-model",
            detect_every=10,
            angle_step_threshold=2,
            angle_hold_threshold=2,
        )
        fake_frame = type("FakeFrame", (), {"shape": (480, 640, 3)})()
        runtime.capture = type("FakeCapture", (), {"read": lambda self: (True, fake_frame), "release": lambda self: None})()
        runtime._run_detection_cycle = lambda frame: None
        runtime.frame_index = 1
        runtime.force_detection = False
        runtime.tracked_bbox = (10, 10, 50, 100)
        runtime.smoothed_target_center = (274.0, 60.0)
        runtime.state.last_match_success = True
        runtime.last_output_angle = 80
        runtime.state.pending_detections = []
        runtime.state.tracking_state = pointer_runtime.STATE_LOCKED

        original_compute_target = pointer_runtime.compute_target_servo_angle
        original_compute_output = pointer_runtime.compute_servo_angle
        try:
            pointer_runtime.compute_target_servo_angle = lambda center, width, args, current_output_angle=None: 81
            pointer_runtime.compute_servo_angle = lambda center, width, last_angle, args: 81
            snapshot = runtime.process_next_frame()
        finally:
            pointer_runtime.compute_target_servo_angle = original_compute_target
            pointer_runtime.compute_servo_angle = original_compute_output

        self.assertEqual(snapshot.output_angle, 80)
        self.assertEqual(runtime.last_output_angle, 80)
        self.assertEqual(snapshot.target_angle, 81)


if __name__ == "__main__":
    unittest.main()
