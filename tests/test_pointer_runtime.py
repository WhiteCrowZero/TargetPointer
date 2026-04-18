import sys
import unittest
from pathlib import Path


sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

import pointer_runtime
from pointer_runtime import DetectionCandidate, PointerRuntime, parse_status_fields


class FakePort:
    def __init__(self, device: str) -> None:
        self.device = device


class PointerRuntimeTests(unittest.TestCase):
    def test_parse_status_fields_extracts_tokens(self) -> None:
        fields = parse_status_fields(
            ["BOOT", "STATUS:ANGLE=90,TARGET=120,ATTACHED=1,LED=ON,LAST=ANGLE,RESULT=OK:ANGLE"]
        )

        self.assertEqual(fields["ANGLE"], "90")
        self.assertEqual(fields["TARGET"], "120")
        self.assertEqual(fields["ATTACHED"], "1")
        self.assertEqual(fields["LED"], "ON")

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

    def test_runtime_sends_tracking_indicator_only_on_stage_transition(self) -> None:
        runtime = PointerRuntime(detector=object(), model_name="test-model", detect_every=10)
        fake_frame = type("FakeFrame", (), {"shape": (480, 640, 3)})()
        runtime.capture = type("FakeCapture", (), {"read": lambda self: (True, fake_frame), "release": lambda self: None})()
        runtime._run_detection_cycle = lambda frame: None
        runtime.state.pending_selection = (10, 10, 50, 100)
        runtime.serial_client = object()
        runtime.serial_port = "COM4"

        commands: list[str] = []
        original_send = pointer_runtime.send_control_command
        original_compute_target = pointer_runtime.compute_target_servo_angle
        original_compute_output = pointer_runtime.compute_servo_angle
        try:
            pointer_runtime.send_control_command = lambda client, command, **kwargs: commands.append(command) or [f"OK:{command}"]
            pointer_runtime.compute_target_servo_angle = (
                lambda center, width, args, current_output_angle=None: 96
            )
            pointer_runtime.compute_servo_angle = lambda center, width, last_angle, args: 96

            runtime.process_next_frame()
            self.assertIn("LED:ON", commands)
            self.assertIn("ANGLE:96", commands)

            commands.clear()
            runtime.process_next_frame()
        finally:
            pointer_runtime.send_control_command = original_send
            pointer_runtime.compute_target_servo_angle = original_compute_target
            pointer_runtime.compute_servo_angle = original_compute_output

        self.assertEqual(commands, [])

    def test_runtime_turns_indicator_off_when_target_is_lost(self) -> None:
        runtime = PointerRuntime(detector=object(), model_name="test-model", detect_every=1, on_loss="stop")
        fake_frame = type("FakeFrame", (), {"shape": (480, 640, 3)})()
        runtime.capture = type("FakeCapture", (), {"read": lambda self: (True, fake_frame), "release": lambda self: None})()
        runtime._run_detection_cycle = lambda frame: None
        runtime.serial_client = object()
        runtime.serial_port = "COM4"
        runtime.tracked_bbox = (10, 10, 50, 100)
        runtime.smoothed_target_center = (35.0, 60.0)
        runtime.state.tracking_state = pointer_runtime.STATE_REACQUIRING
        runtime.state.last_match_success = False
        runtime.missed_frames = runtime.args.reacquire_frames - 1
        runtime.tracking_indicator_active = True

        commands: list[str] = []
        original_send = pointer_runtime.send_control_command
        original_attempt_match = pointer_runtime.attempt_match
        try:
            pointer_runtime.send_control_command = lambda client, command, **kwargs: commands.append(command) or [f"OK:{command}"]
            pointer_runtime.attempt_match = lambda tracked_bbox, candidate_bboxes, args: (None, False)
            snapshot = runtime.process_next_frame()
        finally:
            pointer_runtime.send_control_command = original_send
            pointer_runtime.attempt_match = original_attempt_match

        self.assertEqual(snapshot.tracking_state, pointer_runtime.STATE_LOST)
        self.assertIn("STOP", commands)
        self.assertIn("LED:OFF", commands)

    def test_connect_serial_queries_status_without_centering(self) -> None:
        runtime = PointerRuntime(detector=object(), model_name="test-model")

        original_client = pointer_runtime.PointerSerialClient
        original_send = pointer_runtime.send_control_command

        class FakeClient:
            def __init__(self, port, baud, timeout) -> None:
                self.port = port
                self.baud = baud
                self.timeout = timeout

            def read_startup(self, response_timeout, idle_timeout):
                return ["BOOT", "OK:IDLE"]

            def close(self) -> None:
                return None

        commands: list[str] = []

        try:
            pointer_runtime.PointerSerialClient = FakeClient
            pointer_runtime.send_control_command = (
                lambda client, command, **kwargs: commands.append(command)
                or ["STATUS:ANGLE=90,TARGET=90,ATTACHED=0,LED=OFF,LAST=BOOT,RESULT=OK:IDLE"]
            )
            responses = runtime.connect_serial("COM4")
        finally:
            pointer_runtime.PointerSerialClient = original_client
            pointer_runtime.send_control_command = original_send

        self.assertEqual(commands, ["STATUS?", "LED:OFF"])
        self.assertIn("BOOT", responses)
        self.assertIsNone(runtime.last_output_angle)


if __name__ == "__main__":
    unittest.main()
