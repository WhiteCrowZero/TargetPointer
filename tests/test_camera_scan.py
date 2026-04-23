import unittest

from targetpointer.runtime import camera_scan


class CameraScanTests(unittest.TestCase):
    def test_scan_camera_indices_releases_opened_and_failed_captures(self) -> None:
        class FakeCapture:
            def __init__(self, opened: bool) -> None:
                self.opened = opened
                self.released = False

            def isOpened(self) -> bool:
                return self.opened

            def release(self) -> None:
                self.released = True

        captures: list[FakeCapture] = []

        def fake_video_capture(index: int, backend: int) -> FakeCapture:
            capture = FakeCapture(index == 1 and backend == camera_scan.cv2.CAP_ANY)
            captures.append(capture)
            return capture

        original_video_capture = camera_scan.cv2.VideoCapture
        camera_scan.cv2.VideoCapture = fake_video_capture
        try:
            results = camera_scan.scan_camera_indices(2, "any")
        finally:
            camera_scan.cv2.VideoCapture = original_video_capture

        self.assertEqual([item.to_json() for item in results], [{"index": 1, "backend": "any", "read_ok": True}])
        self.assertTrue(all(capture.released for capture in captures))


if __name__ == "__main__":
    unittest.main()
