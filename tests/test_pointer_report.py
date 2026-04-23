import json
import sys
import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace

import numpy as np


sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

from pointer_report import (
    ReportStatus,
    TargetReportAnalysis,
    build_report_prompt,
    build_report_images,
    default_report_path,
    generate_target_report_pdf,
    parse_target_report_analysis,
    request_target_report_analysis,
)


class FakeResponses:
    def __init__(self) -> None:
        self.last_kwargs = None

    def create(self, **kwargs):
        self.last_kwargs = kwargs
        return SimpleNamespace(
            output_text=json.dumps(
                {
                    "overall_description": "裁剪图中可以看到一个被选中的人物。",
                    "visible_features": ["深色上衣", "站立姿态"],
                    "position_and_pose": "人物位于画面中心附近。",
                    "environment_and_activity": "室内背景中系统正在跟踪目标。",
                    "confidence": "中等；画面质量足够但仍有细节限制。",
                    "cautions": ["未进行身份识别。"],
                }
            )
        )


class PointerReportTests(unittest.TestCase):
    def _status(self) -> ReportStatus:
        return ReportStatus(
            timestamp=datetime(2026, 4, 22, 10, 30, 45),
            tracking_state="locked",
            bbox=(20, 10, 50, 80),
            target_angle=96,
            output_angle=94,
            missed_frames=0,
            detection_count=1,
            camera_source="0",
            camera_backend="msmf",
            serial_connected=True,
            serial_port="COM4",
        )

    def test_build_report_images_crops_and_encodes_data_urls(self) -> None:
        frame = np.zeros((120, 180, 3), dtype=np.uint8)
        frame[10:90, 20:70] = (255, 255, 255)

        images = build_report_images(frame, (20, 10, 50, 80))

        self.assertGreater(images.target_crop.shape[0], 80)
        self.assertGreater(images.target_crop.shape[1], 50)
        self.assertTrue(images.target_crop_data_url.startswith("data:image/jpeg;base64,"))
        self.assertTrue(images.full_frame_data_url.startswith("data:image/jpeg;base64,"))

    def test_request_target_report_analysis_sends_two_images_and_schema(self) -> None:
        frame = np.zeros((120, 180, 3), dtype=np.uint8)
        images = build_report_images(frame, (20, 10, 50, 80))
        responses = FakeResponses()
        client = SimpleNamespace(responses=responses)

        analysis = request_target_report_analysis(images, self._status(), client=client, model="test-model")

        self.assertEqual(analysis.overall_description, "裁剪图中可以看到一个被选中的人物。")
        self.assertEqual(responses.last_kwargs["model"], "test-model")
        content = responses.last_kwargs["input"][0]["content"]
        self.assertEqual(content[1]["type"], "input_image")
        self.assertEqual(content[2]["type"], "input_image")
        self.assertEqual(responses.last_kwargs["text"]["format"]["name"], "target_person_report")

    def test_build_report_prompt_requires_strict_visual_evidence(self) -> None:
        prompt = build_report_prompt(self._status())

        self.assertIn("只使用目标裁剪图", prompt)
        self.assertIn("只返回符合 schema 的 JSON 对象", prompt)
        self.assertIn("所有字段都必须使用中文", prompt)

    def test_parse_target_report_analysis_rejects_missing_text(self) -> None:
        with self.assertRaises(ValueError):
            parse_target_report_analysis(SimpleNamespace(output=[]))

    def test_generate_target_report_pdf_writes_expected_file(self) -> None:
        frame = np.zeros((120, 180, 3), dtype=np.uint8)
        images = build_report_images(frame, (20, 10, 50, 80))
        analysis = TargetReportAnalysis(
            overall_description="裁剪图中可以看到一个被选中的人物。",
            visible_features=["深色上衣", "站立姿态"],
            position_and_pose="人物位于画面中心附近。",
            environment_and_activity="室内背景中系统正在跟踪目标。",
            confidence="中等；画面质量足够但仍有细节限制。",
            cautions=["未进行身份识别。"],
        )

        with tempfile.TemporaryDirectory() as tmp_dir:
            report_path = default_report_path(self._status().timestamp, Path(tmp_dir))
            generated = generate_target_report_pdf(report_path, images, self._status(), analysis)

            self.assertEqual(generated, report_path)
            self.assertTrue(report_path.exists())
            self.assertGreater(report_path.stat().st_size, 1000)


if __name__ == "__main__":
    unittest.main()
