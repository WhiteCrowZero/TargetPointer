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
    fake_serial = types.SimpleNamespace(SerialException=RuntimeError)

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
            [(150, 110, 55, 105)],
            args,
        )

        self.assertIsNotNone(match)
        self.assertTrue(used_relaxed)

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
            cameras = module.list_available_cameras(2, "auto")
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
