import json
import sys
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

import numpy as np


sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

from pointer_voice_agent import (
    DEFAULT_STT_LANGUAGE,
    DEFAULT_STT_MODEL,
    DEFAULT_TTS_MODEL,
    DEFAULT_TTS_VOICE,
    DEFAULT_VOICE_LLM_MODEL,
    VoiceAssistantConfig,
    build_frame_context_text,
    load_latest_voice_frame,
    missing_voice_env_vars,
    optional_float_env,
    optional_int_env,
    should_sample_frame,
    write_latest_voice_frame,
)
from targetpointer.voice.voices import PERSON_VOICE_ID_MAP, voice_choices, voice_name_for_id


class PointerVoiceAgentTests(unittest.TestCase):
    def test_missing_voice_env_vars_reports_all_required_values(self) -> None:
        missing = missing_voice_env_vars({"OPENAI_API_KEY": "ok"})

        self.assertEqual(missing, ["ELEVEN_API_KEY", "LIVEKIT_URL", "LIVEKIT_API_KEY", "LIVEKIT_API_SECRET"])

    def test_voice_assistant_config_exports_process_env(self) -> None:
        config = VoiceAssistantConfig(
            stt_model="scribe_v2_realtime",
            stt_language="zh",
            llm_model="gpt-test",
            temperature=0.3,
            max_output_tokens=512,
            tts_model="tts-test",
            tts_voice="verse",
            tts_speed=1.1,
        )

        self.assertEqual(
            config.process_env(),
            {
                "TARGETPOINTER_STT_MODEL": "scribe_v2_realtime",
                "TARGETPOINTER_STT_LANGUAGE": "zh",
                "TARGETPOINTER_VOICE_LLM_MODEL": "gpt-test",
                "TARGETPOINTER_TTS_MODEL": "tts-test",
                "TARGETPOINTER_TTS_VOICE": "verse",
                "TARGETPOINTER_TTS_SPEED": "1.100",
                "TARGETPOINTER_VOICE_TEMPERATURE": "0.300",
                "TARGETPOINTER_VOICE_MAX_OUTPUT_TOKENS": "512",
            },
        )

    def test_voice_assistant_config_defaults_match_pipeline(self) -> None:
        config = VoiceAssistantConfig()

        self.assertEqual(config.stt_model, DEFAULT_STT_MODEL)
        self.assertEqual(config.stt_language, DEFAULT_STT_LANGUAGE)
        self.assertEqual(config.llm_model, DEFAULT_VOICE_LLM_MODEL)
        self.assertEqual(config.tts_model, DEFAULT_TTS_MODEL)
        self.assertEqual(config.tts_voice, DEFAULT_TTS_VOICE)

    def test_person_voice_map_exposes_named_voice_choices(self) -> None:
        self.assertIn("默认人物音色", PERSON_VOICE_ID_MAP)
        voice_id = PERSON_VOICE_ID_MAP["默认人物音色"]

        self.assertEqual(voice_name_for_id(voice_id), "默认人物音色")
        self.assertIn(("默认人物音色", voice_id), voice_choices())
        self.assertEqual(voice_choices("custom-id")[0], ("环境默认音色", "custom-id"))

    def test_optional_env_parsers_ignore_empty_values(self) -> None:
        original = dict()
        import os

        for key in ("TARGETPOINTER_TEST_FLOAT", "TARGETPOINTER_TEST_INT"):
            original[key] = os.environ.get(key)
        try:
            os.environ["TARGETPOINTER_TEST_FLOAT"] = ""
            os.environ["TARGETPOINTER_TEST_INT"] = ""
            self.assertIsNone(optional_float_env("TARGETPOINTER_TEST_FLOAT"))
            self.assertIsNone(optional_int_env("TARGETPOINTER_TEST_INT"))

            os.environ["TARGETPOINTER_TEST_FLOAT"] = "0.25"
            os.environ["TARGETPOINTER_TEST_INT"] = "42"
            self.assertEqual(optional_float_env("TARGETPOINTER_TEST_FLOAT"), 0.25)
            self.assertEqual(optional_int_env("TARGETPOINTER_TEST_INT"), 42)
        finally:
            for key, value in original.items():
                if value is None:
                    os.environ.pop(key, None)
                else:
                    os.environ[key] = value

    def test_should_sample_frame_uses_interval(self) -> None:
        self.assertTrue(should_sample_frame(None, 10.0, interval_seconds=5.0))
        self.assertFalse(should_sample_frame(10.0, 14.9, interval_seconds=5.0))
        self.assertTrue(should_sample_frame(10.0, 15.0, interval_seconds=5.0))

    def test_write_and_load_latest_voice_frame_keeps_only_current_payload(self) -> None:
        frame = np.zeros((100, 200, 3), dtype=np.uint8)

        with tempfile.TemporaryDirectory() as tmp_dir:
            path = Path(tmp_dir) / "latest_frame.json"
            write_latest_voice_frame(
                path,
                frame,
                tracking_state="locked",
                bbox=(1, 2, 30, 40),
                target_angle=95,
                output_angle=93,
                timestamp=datetime(2026, 4, 22, 2, 0, tzinfo=timezone.utc),
            )
            first_payload = json.loads(path.read_text(encoding="utf-8"))

            write_latest_voice_frame(
                path,
                frame,
                tracking_state="reacquiring",
                bbox=None,
                target_angle=None,
                output_angle=90,
                timestamp=datetime(2026, 4, 22, 2, 1, tzinfo=timezone.utc),
            )
            loaded = load_latest_voice_frame(path)

            self.assertIsNotNone(loaded)
            assert loaded is not None
            self.assertNotEqual(first_payload["timestamp"], loaded.timestamp)
            self.assertTrue(loaded.data_url.startswith("data:image/jpeg;base64,"))
            self.assertEqual(loaded.tracking_state, "reacquiring")
            self.assertIsNone(loaded.bbox)
            self.assertEqual(loaded.output_angle, 90)

    def test_build_frame_context_text_includes_metadata(self) -> None:
        frame = write_latest_voice_frame(
            Path(tempfile.gettempdir()) / "targetpointer_test_voice_frame.json",
            np.zeros((20, 20, 3), dtype=np.uint8),
            tracking_state="locked",
            bbox=(1, 2, 3, 4),
            target_angle=88,
            output_angle=90,
            timestamp=datetime(2026, 4, 22, 2, 0, tzinfo=timezone.utc),
        )

        text = build_frame_context_text(frame)

        self.assertIn("Tracking state: locked", text)
        self.assertIn("Selected target bbox: (1, 2, 3, 4)", text)
        self.assertIn("Servo output angle: 90", text)


if __name__ == "__main__":
    unittest.main()
