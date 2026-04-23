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
    format_voice_timestamp,
    format_model_display_name,
    latest_non_none,
    snapshot_has_report_target,
)
from targetpointer.ui.realtime_chat import (
    RealtimeVoiceConfig,
    RealtimeVoiceSessionConfig,
    build_realtime_voice_session_payload,
    format_voice_session_details,
    realtime_chat_api_base_url,
)


class PointerDesktopAppTests(unittest.TestCase):
    def test_format_model_display_name_keeps_basename_only(self) -> None:
        self.assertEqual(format_model_display_name(r".\models\yolov8n.pt"), "yolov8n.pt")
        self.assertEqual(format_model_display_name("/tmp/models/yolov8s.pt"), "yolov8s.pt")

    def test_format_voice_timestamp_uses_absolute_24_hour_format(self) -> None:
        from datetime import datetime

        self.assertEqual(format_voice_timestamp(datetime(2026, 4, 24, 19, 7, 35)), "2026-04-24 19:07")

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
        self.assertIn("class PolishedComboBox", source)
        self.assertIn("configure_combo_box(self.camera_input", source)
        self.assertIn("QListView#ComboPopupList::item:selected", source)

    def test_camera_scan_runs_in_isolated_process(self) -> None:
        source = (Path(__file__).resolve().parents[1] / "targetpointer" / "ui" / "desktop_app.py").read_text()

        self.assertIn("QtCore.QProcess", source)
        self.assertIn("targetpointer.runtime.camera_scan", source)
        self.assertIn("process.setWorkingDirectory(str(self.repo_root))", source)
        self.assertIn('environment.insert("PYTHONPATH"', source)
        self.assertIn("Camera scan timed out", source)

    def test_desktop_separates_default_port_display_from_auto_connect(self) -> None:
        source = (Path(__file__).resolve().parents[1] / "targetpointer" / "ui" / "desktop_app.py").read_text()

        self.assertIn('self.initial_port = initial_port or "COM4"', source)
        self.assertIn("self.auto_connect_serial = auto_connect_serial", source)
        self.assertIn("if self.auto_connect_serial and self.initial_port:", source)
        self.assertIn("auto_connect_serial=args.auto_connect or args.port is not None", source)

    def test_realtime_chat_base_url_defaults_and_trims_trailing_slash(self) -> None:
        self.assertEqual(realtime_chat_api_base_url({}), "http://127.0.0.1:8000")
        self.assertEqual(
            realtime_chat_api_base_url({"REALTIME_CHAT_API_BASE_URL": "http://localhost:9000/"}),
            "http://localhost:9000",
        )

    def test_realtime_voice_session_payload_targets_pipeline_backend(self) -> None:
        payload = build_realtime_voice_session_payload(
            RealtimeVoiceConfig(
                tts_voice="voice-custom",
            ),
            user_identity="targetpointer-test-operator",
            extra_vars={"tracking_state": "locked"},
            attachments=[
                {
                    "kind": "image",
                    "source": "user_upload",
                    "title": "时序画面 1/3",
                    "mime_type": "image/jpeg",
                    "uri": "data:image/jpeg;base64,abc",
                }
            ],
        )

        self.assertNotIn("ai_mode", payload)
        self.assertEqual(payload["agent_id"], "multimodal")
        self.assertEqual(payload["input_modes"], ["audio", "text", "image"])
        self.assertEqual(payload["output_modes"], ["audio", "text"])
        self.assertEqual(payload["user_identity"], "targetpointer-test-operator")
        self.assertEqual(payload["extra_vars"]["tracking_state"], "locked")
        self.assertEqual(payload["model_settings"]["tts_voice"], "voice-custom")
        self.assertEqual(len(payload["attachments"]), 1)
        self.assertEqual(payload["attachments"][0]["kind"], "image")

    def test_realtime_voice_session_payload_can_include_ai_mode_when_backend_allows_it(self) -> None:
        payload = build_realtime_voice_session_payload(
            RealtimeVoiceConfig(),
            allow_client_ai_mode=True,
        )

        self.assertEqual(payload["ai_mode"], "pipeline")
        self.assertNotIn("tts_voice", payload["model_settings"])

    def test_voice_session_details_include_session_and_backend_summary(self) -> None:
        details = format_voice_session_details(
            RealtimeVoiceSessionConfig(
                api_base_url="http://127.0.0.1:8000",
                session_id="sess-123",
                conversation_id="conv-456",
                room="room-789",
                livekit_url="ws://localhost:7880",
                user_identity="targetpointer-test-operator",
                user_token="header.payload.signature",
                status="activate",
            )
        )

        self.assertIn("Backend: http://127.0.0.1:8000", details)
        self.assertIn("Session: sess-123", details)
        self.assertIn("Conversation: conv-456", details)
        self.assertIn("Room: room-789", details)
        self.assertIn("User: targetpointer-test-operator", details)
        self.assertIn("Status: activate", details)

    def test_desktop_voice_flow_uses_realtime_chat_backend_without_worker_command(self) -> None:
        source = (Path(__file__).resolve().parents[1] / "targetpointer" / "ui" / "desktop_app.py").read_text()

        self.assertIn("RealtimeChatApiClient", source)
        self.assertIn("VoiceEventLog", source)
        self.assertIn("build_realtime_voice_session_payload", source)
        self.assertIn("self.voice_image_history: deque[VoiceImageSnapshot] = deque(maxlen=6)", source)
        self.assertIn("self.voice_image_sample_interval_s = 5.0", source)
        self.assertIn("self._capture_voice_image_snapshot(snapshot)", source)
        self.assertIn("VOICE_IMAGE_ATTACHMENT_LIMIT = 3", source)
        self.assertIn("recent_images = list(self.voice_image_history)[-VOICE_IMAGE_ATTACHMENT_LIMIT:]", source)
        self.assertIn('self.transcript_log.setMinimumHeight(360)', source)
        self.assertIn('title": f"时序画面 {index}/{total} · {item.captured_at_label}"', source)
        self.assertNotIn("复制命令", source)
        self.assertNotIn("worker 命令并手动运行", source)

    def test_embedded_livekit_client_publishes_local_audio_as_microphone(self) -> None:
        source = (Path(__file__).resolve().parents[1] / "targetpointer" / "ui" / "voice_client.py").read_text()

        self.assertIn("rtc.TrackPublishOptions(source=rtc.TrackSource.SOURCE_MICROPHONE)", source)
        self.assertIn('room.on("data_received", self._on_data_received)', source)
        self.assertIn("reconnect_requested = QtCore.Signal()", source)
        self.assertIn('self._caption_segments: dict[str, dict[str, tuple[float, str, bool]]]', source)
        self.assertIn('combined_text = " ".join(text for _start, text, _final in ordered_segments).strip()', source)
        self.assertIn("self.live_caption_changed.emit(role, combined_text, is_final)", source)

    def test_voice_ui_keeps_streaming_caption_and_activity_decay_logic(self) -> None:
        source = (Path(__file__).resolve().parents[1] / "targetpointer" / "ui" / "desktop_app.py").read_text()

        self.assertIn('self.voice_activity_until: dict[str, float] = {"user": 0.0, "assistant": 0.0}', source)
        self.assertIn('self.voice_live_caption_timestamps: dict[str, str] = {"user": "", "assistant": ""}', source)
        self.assertIn("self.voice_activity_decay_timers: dict[str, QtCore.QTimer] = {}", source)
        self.assertIn("self.voice_live_captions[role] = cleaned", source)
        self.assertIn("self.voice_live_caption_timestamps[role] = format_voice_timestamp()", source)
        self.assertIn("clear_timer.start(700)", source)
        self.assertIn('prefix = f"[{timestamp}] " if timestamp else ""', source)
        self.assertIn('formatted.append(f"{prefix}{role}: {text}")', source)
        self.assertIn('if self.voice_live_captions.get("user") or now < self.voice_activity_until.get("user", 0.0):', source)
        self.assertIn('if self.voice_live_captions.get("assistant") or now < self.voice_activity_until.get("assistant", 0.0):', source)
        self.assertIn('if agent_state == "speaking":', source)
        self.assertIn('elif user_muted:', source)


if __name__ == "__main__":
    unittest.main()
