import sys
import unittest
from pathlib import Path
from types import SimpleNamespace


sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

from pointer_desktop_app import (
    build_history_point,
    build_desktop_button_state,
    build_desktop_flow_state,
    compute_plot_range,
    format_model_display_name,
    latest_non_none,
    snapshot_has_report_target,
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

    def test_compute_plot_range_prefers_fixed_bounds(self) -> None:
        minimum, maximum = compute_plot_range([10, 20, 30], fixed_min=0, fixed_max=100)

        self.assertEqual((minimum, maximum), (0, 100))

    def test_compute_plot_range_expands_flat_series(self) -> None:
        minimum, maximum = compute_plot_range([5, 5, 5])

        self.assertLess(minimum, 5)
        self.assertGreater(maximum, 5)

    def test_build_desktop_button_state_gates_controls_by_readiness(self) -> None:
        state = build_desktop_button_state(
            has_camera_source=True,
            camera_open=False,
            has_serial_port=True,
            serial_connected=False,
        )

        self.assertTrue(state.open_camera_enabled)
        self.assertFalse(state.close_camera_enabled)
        self.assertTrue(state.connect_enabled)
        self.assertFalse(state.disconnect_enabled)
        self.assertFalse(state.redetect_enabled)
        self.assertFalse(state.center_enabled)
        self.assertFalse(state.stop_enabled)
        self.assertFalse(state.report_enabled)
        self.assertFalse(state.voice_enabled)

    def test_build_desktop_button_state_keeps_manual_controls_available_when_serial_connected(self) -> None:
        state = build_desktop_button_state(
            has_camera_source=False,
            camera_open=False,
            has_serial_port=True,
            serial_connected=True,
        )

        self.assertFalse(state.open_camera_enabled)
        self.assertFalse(state.redetect_enabled)
        self.assertTrue(state.center_enabled)
        self.assertTrue(state.stop_enabled)
        self.assertTrue(state.disconnect_enabled)

    def test_build_desktop_button_state_enables_report_and_voice_when_ready(self) -> None:
        state = build_desktop_button_state(
            has_camera_source=True,
            camera_open=True,
            has_serial_port=False,
            serial_connected=False,
            has_report_target=True,
            voice_running=False,
        )

        self.assertTrue(state.report_enabled)
        self.assertTrue(state.voice_enabled)

    def test_build_desktop_button_state_keeps_voice_stop_available_after_camera_closes(self) -> None:
        state = build_desktop_button_state(
            has_camera_source=False,
            camera_open=False,
            has_serial_port=False,
            serial_connected=False,
            voice_running=True,
        )

        self.assertFalse(state.report_enabled)
        self.assertTrue(state.voice_enabled)

    def test_snapshot_has_report_target_requires_tracked_bbox(self) -> None:
        self.assertFalse(snapshot_has_report_target(None))
        self.assertFalse(snapshot_has_report_target(SimpleNamespace(tracked_bbox=None, tracking_state="locked")))
        self.assertFalse(snapshot_has_report_target(SimpleNamespace(tracked_bbox=(1, 2, 3, 4), tracking_state="lost")))
        self.assertTrue(snapshot_has_report_target(SimpleNamespace(tracked_bbox=(1, 2, 3, 4), tracking_state="locked")))

    def test_build_desktop_flow_state_guides_setup_order(self) -> None:
        self.assertEqual(
            build_desktop_flow_state(camera_open=False, serial_connected=False, tracking_state=None).text,
            "Step 1 · Connect device",
        )
        self.assertEqual(
            build_desktop_flow_state(camera_open=False, serial_connected=True, tracking_state="selecting").text,
            "Step 2 · Open camera",
        )
        self.assertEqual(
            build_desktop_flow_state(camera_open=True, serial_connected=True, tracking_state="selecting").text,
            "Step 3 · Click a detected person or drag a box",
        )

    def test_build_desktop_flow_state_describes_live_tracking_states(self) -> None:
        self.assertEqual(
            build_desktop_flow_state(camera_open=True, serial_connected=True, tracking_state="locked").text,
            "Live · Tracking selected person",
        )
        self.assertEqual(
            build_desktop_flow_state(camera_open=True, serial_connected=True, tracking_state="reacquiring").text,
            "Live · Reacquiring selected person",
        )
        self.assertEqual(
            build_desktop_flow_state(camera_open=True, serial_connected=True, tracking_state="centering").text,
            "Device · Centering",
        )
        self.assertEqual(
            build_desktop_flow_state(camera_open=True, serial_connected=True, tracking_state="lost").text,
            "Ready · Select a target again",
        )

    def test_desktop_entry_uses_single_workbench_window(self) -> None:
        script_source = (Path(__file__).resolve().parents[1] / "scripts" / "pointer_desktop_app.py").read_text()
        launcher_source = (Path(__file__).resolve().parents[1] / "targetpointer" / "ui" / "launcher.py").read_text()

        self.assertIn("from targetpointer.ui.desktop_app import main", script_source)
        self.assertIn("live_window.show()", launcher_source)
        self.assertNotIn("launcher.show()", launcher_source)

    def test_workbench_declares_sidebar_and_stacked_pages(self) -> None:
        source = (Path(__file__).resolve().parents[1] / "targetpointer" / "ui" / "desktop_app.py").read_text()

        self.assertIn("class SidebarNavButton", source)
        self.assertIn("self.page_stack = QtWidgets.QStackedWidget()", source)
        self.assertIn('"voice": self.voice_window', source)
        self.assertIn('"report": self.report_window', source)
        self.assertIn('"insights": self.insights_window', source)


if __name__ == "__main__":
    unittest.main()
