import importlib
import sys
import types
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))


def load_pointer_vision_app():
    fake_cv2 = types.SimpleNamespace(
        CAP_ANY=0,
        CAP_DSHOW=700,
        CAP_MSMF=1400,
    )
    fake_ultralytics = types.SimpleNamespace(YOLO=object)
    fake_serial = types.SimpleNamespace(SerialException=RuntimeError, Serial=object)

    original_modules = {
        "cv2": sys.modules.get("cv2"),
        "ultralytics": sys.modules.get("ultralytics"),
        "serial": sys.modules.get("serial"),
        "pointer_vision_app": sys.modules.get("pointer_vision_app"),
    }

    sys.modules["cv2"] = fake_cv2
    sys.modules["ultralytics"] = fake_ultralytics
    sys.modules["serial"] = fake_serial
    sys.modules.pop("pointer_vision_app", None)

    try:
        module = importlib.import_module("pointer_vision_app")
    finally:
        for name, value in original_modules.items():
            if value is None:
                sys.modules.pop(name, None)
            else:
                sys.modules[name] = value

    return module


class PointerVisionAppTests(unittest.TestCase):
    def test_compute_servo_angle_limits_initial_jump_from_center(self) -> None:
        module = load_pointer_vision_app()
        args = types.SimpleNamespace(
            min_angle=20,
            center_angle=90,
            max_angle=160,
            center_deadzone=2,
            angle_hold_threshold=2,
            angle_small_error_threshold=4,
            angle_medium_error_threshold=18,
            angle_small_step=1,
            angle_medium_step=3,
            angle_large_step=6,
        )

        angle = module.compute_servo_angle((0.0, 0.0), 640, 90, args)

        self.assertEqual(angle, 96)

    def test_compute_servo_angle_uses_smaller_steps_near_target(self) -> None:
        module = load_pointer_vision_app()
        args = types.SimpleNamespace(
            min_angle=20,
            center_angle=90,
            max_angle=160,
            center_deadzone=2,
            angle_hold_threshold=2,
            angle_small_error_threshold=4,
            angle_medium_error_threshold=18,
            angle_small_step=1,
            angle_medium_step=3,
            angle_large_step=6,
        )

        angle = module.compute_servo_angle((274.0, 0.0), 640, 82, args)

        self.assertEqual(angle, 85)

    def test_attempt_match_falls_back_to_relaxed_reacquire(self) -> None:
        module = load_pointer_vision_app()
        args = types.SimpleNamespace(
            match_min_iou=0.2,
            match_max_center_ratio=1.0,
            match_max_area_change=0.5,
            reacquire_center_ratio_multiplier=3.0,
            reacquire_area_change_multiplier=3.0,
        )

        match, used_relaxed = module.attempt_match(
            (100, 100, 50, 100),
            [(140, 110, 55, 105)],
            args,
        )

        self.assertIsNotNone(match)
        self.assertTrue(used_relaxed)

    def test_attempt_match_rejects_relaxed_candidate_with_zero_iou(self) -> None:
        module = load_pointer_vision_app()
        args = types.SimpleNamespace(
            match_min_iou=0.2,
            match_max_center_ratio=1.0,
            match_max_area_change=0.5,
            reacquire_center_ratio_multiplier=3.0,
            reacquire_area_change_multiplier=3.0,
        )

        match, used_relaxed = module.attempt_match(
            (100, 100, 50, 100),
            [(150, 110, 55, 105)],
            args,
        )

        self.assertIsNone(match)
        self.assertFalse(used_relaxed)

    def test_update_angles_from_status_fields_uses_attached_status(self) -> None:
        module = load_pointer_vision_app()

        output_angle, target_angle, attached = module.update_angles_from_status_fields(
            {"ANGLE": "135", "TARGET": "120", "ATTACHED": "1"},
            current_output_angle=None,
            current_target_angle=None,
        )

        self.assertTrue(attached)
        self.assertEqual(output_angle, 135)
        self.assertEqual(target_angle, 120)

    def test_safe_shutdown_serial_centers_then_stops_when_attached(self) -> None:
        module = load_pointer_vision_app()
        original_send = module.send_control_command
        original_sleep = module.time.sleep
        commands: list[str] = []
        status_payloads = iter(
            [
                ["STATUS:ANGLE=135,TARGET=135,ATTACHED=1,LED=ON,LAST=ANGLE,RESULT=OK:ANGLE"],
                ["STATUS:ANGLE=90,TARGET=90,ATTACHED=1,LED=OFF,LAST=CENTER,RESULT=OK:CENTER"],
            ]
        )

        try:
            module.time.sleep = lambda _seconds: None

            def fake_send(serial_client, command, **kwargs):
                commands.append(command)
                if command == "STATUS?":
                    return next(status_payloads)
                return [f"OK:{command}"]

            module.send_control_command = fake_send
            module.safe_shutdown_serial(
                object(),
                center_angle=90,
                response_timeout=0.25,
                idle_timeout=0.05,
                settle_timeout=0.01,
                poll_interval=0.0,
            )
        finally:
            module.send_control_command = original_send
            module.time.sleep = original_sleep

        self.assertEqual(commands, ["STATUS?", "STATE:IDLE", "CENTER", "STATUS?", "STOP"])

    def test_sync_device_state_does_not_fallback_to_deprecated_led_commands(self) -> None:
        module = load_pointer_vision_app()
        original_send = module.send_control_command
        commands: list[str] = []

        try:
            def fake_send(serial_client, command, **kwargs):
                commands.append(command)
                raise module.PointerSerialError(f"{command} -> ERR:BAD_CMD")

            module.send_control_command = fake_send
            responses, active_mode, supported = module.sync_device_state(
                object(),
                mode="LOCK",
                active_mode=None,
                state_supported=True,
                response_timeout=0.25,
                idle_timeout=0.05,
            )
        finally:
            module.send_control_command = original_send

        self.assertEqual(responses, [])
        self.assertIsNone(active_mode)
        self.assertFalse(supported)
        self.assertEqual(commands, ["STATE:LOCK"])

    def test_build_camera_candidates_prefers_windows_backends_for_indices(self) -> None:
        module = load_pointer_vision_app()
        original_platform = module.sys.platform
        module.sys.platform = "win32"
        try:
            candidates = module.build_camera_candidates(0, "auto")
        finally:
            module.sys.platform = original_platform

        self.assertEqual(
            candidates,
            [
                (0, module.cv2.CAP_MSMF, "msmf"),
                (0, module.cv2.CAP_DSHOW, "dshow"),
                (0, module.cv2.CAP_ANY, "any"),
            ],
        )

    def test_build_camera_candidates_uses_any_for_urls(self) -> None:
        module = load_pointer_vision_app()
        candidates = module.build_camera_candidates("rtsp://camera", "auto")
        self.assertEqual(candidates, [("rtsp://camera", module.cv2.CAP_ANY, "any")])

    def test_open_camera_capture_falls_back_to_next_backend(self) -> None:
        module = load_pointer_vision_app()

        class FakeCapture:
            def __init__(self, opened: bool) -> None:
                self.opened = opened
                self.released = False

            def isOpened(self) -> bool:
                return self.opened

            def release(self) -> None:
                self.released = True

        attempts: list[tuple[object, int]] = []
        outcomes = {
            module.cv2.CAP_MSMF: FakeCapture(True),
        }

        def fake_video_capture(source, backend):
            attempts.append((source, backend))
            return outcomes.get(backend, FakeCapture(False))

        original_platform = module.sys.platform
        original_factory = getattr(module.cv2, "VideoCapture", None)
        module.sys.platform = "win32"
        module.cv2.VideoCapture = fake_video_capture
        try:
            capture, backend_name = module.open_camera_capture(0, "auto")
        finally:
            module.sys.platform = original_platform
            if original_factory is None:
                delattr(module.cv2, "VideoCapture")
            else:
                module.cv2.VideoCapture = original_factory

        self.assertIs(capture, outcomes[module.cv2.CAP_MSMF])
        self.assertEqual(backend_name, "msmf")
        self.assertEqual(
            attempts,
            [
                (0, module.cv2.CAP_MSMF),
            ],
        )

    def test_list_available_cameras_reports_only_successful_indices(self) -> None:
        module = load_pointer_vision_app()

        class FakeCapture:
            def __init__(self, opened: bool, read_ok: bool) -> None:
                self.opened = opened
                self.read_ok = read_ok
                self.released = False

            def isOpened(self) -> bool:
                return self.opened

            def read(self):
                return self.read_ok, object() if self.read_ok else None

            def release(self) -> None:
                self.released = True

        outcomes = {
            (0, module.cv2.CAP_MSMF): FakeCapture(True, True),
            (1, module.cv2.CAP_MSMF): FakeCapture(True, False),
        }

        def fake_video_capture(source, backend):
            return outcomes.get((source, backend), FakeCapture(False, False))

        original_platform = module.sys.platform
        original_factory = getattr(module.cv2, "VideoCapture", None)
        module.sys.platform = "win32"
        module.cv2.VideoCapture = fake_video_capture
        try:
            cameras = module.list_available_cameras(2, "auto", probe_frames=True)
        finally:
            module.sys.platform = original_platform
            if original_factory is None:
                delattr(module.cv2, "VideoCapture")
            else:
                module.cv2.VideoCapture = original_factory

        self.assertEqual(
            cameras,
            [
                (0, "msmf", True),
                (1, "msmf", False),
            ],
        )


if __name__ == "__main__":
    unittest.main()
