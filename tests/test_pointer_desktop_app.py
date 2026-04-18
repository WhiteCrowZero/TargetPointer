import sys
import unittest
from pathlib import Path
from types import SimpleNamespace


sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

from pointer_desktop_app import (
    build_history_point,
    format_model_display_name,
    latest_non_none,
)


class PointerDesktopAppTests(unittest.TestCase):
    def test_format_model_display_name_keeps_basename_only(self) -> None:
        self.assertEqual(format_model_display_name(r".\models\yolov8n.pt"), "yolov8n.pt")
        self.assertEqual(format_model_display_name("/tmp/models/yolov8s.pt"), "yolov8s.pt")

    def test_latest_non_none_returns_most_recent_value(self) -> None:
        self.assertEqual(latest_non_none([None, 1, None, 3]), 3)
        self.assertIsNone(latest_non_none([None, None]))

    def test_build_history_point_extracts_runtime_metrics(self) -> None:
        snapshot = SimpleNamespace(
            tracking_state="locked",
            target_angle=104,
            output_angle=97,
            pending_detections=[object(), object()],
            missed_frames=2,
            last_match=SimpleNamespace(score=0.87),
        )

        point = build_history_point(snapshot, 12.5)

        self.assertEqual(point.timestamp, 12.5)
        self.assertEqual(point.tracking_state, "locked")
        self.assertEqual(point.target_angle, 104)
        self.assertEqual(point.output_angle, 97)
        self.assertEqual(point.detection_count, 2)
        self.assertEqual(point.missed_frames, 2)
        self.assertAlmostEqual(point.match_score, 0.87)


if __name__ == "__main__":
    unittest.main()
