from __future__ import annotations

import base64
from dataclasses import dataclass
from datetime import datetime
import json
import os
from pathlib import Path
import tempfile
from typing import Any

try:
    import cv2
except ImportError as exc:
    raise SystemExit(
        "Missing dependency: opencv-python. Run `uv sync` in the repository root, then retry with `uv run python ...`."
    ) from exc

from targetpointer.runtime.host_logic import BBox


DEFAULT_REPORT_MODEL = "gpt-5.4-mini"
REPORT_MODEL_ENV = "TARGETPOINTER_REPORT_MODEL"


@dataclass(frozen=True)
class ReportImageBundle:
    full_frame: object
    target_crop: object
    full_frame_jpeg: bytes
    target_crop_jpeg: bytes
    full_frame_data_url: str
    target_crop_data_url: str


@dataclass(frozen=True)
class ReportStatus:
    timestamp: datetime
    tracking_state: str
    bbox: BBox
    target_angle: int | None
    output_angle: int | None
    missed_frames: int
    detection_count: int
    camera_source: str | None
    camera_backend: str | None
    serial_connected: bool
    serial_port: str | None


@dataclass(frozen=True)
class TargetReportAnalysis:
    overall_description: str
    visible_features: list[str]
    position_and_pose: str
    environment_and_activity: str
    confidence: str
    cautions: list[str]


@dataclass(frozen=True)
class GeneratedReport:
    path: Path
    analysis: TargetReportAnalysis
    status: ReportStatus | None = None
    target_crop_jpeg: bytes | None = None
    full_frame_jpeg: bytes | None = None


def clamp_bbox(bbox: BBox, frame_shape: tuple[int, ...]) -> BBox:
    frame_height, frame_width = int(frame_shape[0]), int(frame_shape[1])
    x, y, width, height = bbox
    x = max(0, min(int(x), max(0, frame_width - 1)))
    y = max(0, min(int(y), max(0, frame_height - 1)))
    right = max(x + 1, min(int(x + width), frame_width))
    bottom = max(y + 1, min(int(y + height), frame_height))
    return x, y, right - x, bottom - y


def padded_bbox(bbox: BBox, frame_shape: tuple[int, ...], padding_ratio: float = 0.12) -> BBox:
    x, y, width, height = clamp_bbox(bbox, frame_shape)
    pad_x = int(round(width * padding_ratio))
    pad_y = int(round(height * padding_ratio))
    return clamp_bbox((x - pad_x, y - pad_y, width + pad_x * 2, height + pad_y * 2), frame_shape)


def crop_frame(frame, bbox: BBox, padding_ratio: float = 0.12):
    x, y, width, height = padded_bbox(bbox, frame.shape, padding_ratio)
    return frame[y : y + height, x : x + width].copy()


def encode_jpeg(frame, *, max_side: int = 1280, quality: int = 82) -> bytes:
    output = frame
    height, width = frame.shape[:2]
    largest_side = max(height, width)
    if largest_side > max_side:
        scale = max_side / largest_side
        output = cv2.resize(frame, (int(width * scale), int(height * scale)), interpolation=cv2.INTER_AREA)

    ok, encoded = cv2.imencode(".jpg", output, [int(cv2.IMWRITE_JPEG_QUALITY), int(quality)])
    if not ok:
        raise RuntimeError("JPEG encoding failed")
    return bytes(encoded)


def jpeg_data_url(jpeg_bytes: bytes) -> str:
    return "data:image/jpeg;base64," + base64.b64encode(jpeg_bytes).decode("ascii")


def build_report_images(frame, bbox: BBox) -> ReportImageBundle:
    full_frame = frame.copy()
    target_crop = crop_frame(frame, bbox)
    full_frame_jpeg = encode_jpeg(full_frame, max_side=1280, quality=82)
    target_crop_jpeg = encode_jpeg(target_crop, max_side=768, quality=86)
    return ReportImageBundle(
        full_frame=full_frame,
        target_crop=target_crop,
        full_frame_jpeg=full_frame_jpeg,
        target_crop_jpeg=target_crop_jpeg,
        full_frame_data_url=jpeg_data_url(full_frame_jpeg),
        target_crop_data_url=jpeg_data_url(target_crop_jpeg),
    )


def target_report_schema() -> dict[str, Any]:
    return {
        "type": "json_schema",
        "name": "target_person_report",
        "strict": True,
        "schema": {
            "type": "object",
            "additionalProperties": False,
            "properties": {
                "overall_description": {"type": "string"},
                "visible_features": {"type": "array", "items": {"type": "string"}},
                "position_and_pose": {"type": "string"},
                "environment_and_activity": {"type": "string"},
                "confidence": {"type": "string"},
                "cautions": {"type": "array", "items": {"type": "string"}},
            },
            "required": [
                "overall_description",
                "visible_features",
                "position_and_pose",
                "environment_and_activity",
                "confidence",
                "cautions",
            ],
        },
    }


def build_report_prompt(status: ReportStatus) -> str:
    return (
        "你是 TargetPointer 的视觉报告模块。请根据固定摄像头画面，为当前选中的人物生成一份中文 JSON 报告。"
        "报告要比简短摘要更完整，尽可能覆盖背景环境、人物穿着、颜色、姿态、朝向、相对位置、可见随身物品、"
        "正在发生的动作、画面质量和系统跟踪状态；但必须严格基于可见证据，不能为了完整而猜测。\n\n"
        "证据规则：\n"
        "- 只使用目标裁剪图、全景图和下面的系统状态。\n"
        "- 第一张图是选中目标裁剪图，人物细节以它为准。\n"
        "- 第二张图是全景上下文，只用于环境、相对位置、活动背景和遮挡关系。\n"
        "- 看不清的内容必须明确写“无法确认”或“不清楚”，不要推断。\n"
        "- 不要识别身份，不要和名人或已知人物比较。\n"
        "- 不要推断民族、国籍、宗教、健康、财富、职业等敏感属性。\n"
        "- 如需提到年龄或性别，只能写成谨慎的视觉估计；不确定时不要写。\n"
        "- 不要描述图像外、遮挡内或不可见的身体特征。\n"
        "- visible_features 应包含多条短句，优先写清楚衣物、颜色、姿态、可见物品、遮挡和明显外观细节。\n"
        "- environment_and_activity 要写出背景环境、人物所处位置、周围物体、光照/画面条件和可能动作。\n"
        "- confidence 要说明哪些可见因素提高或降低可信度。\n"
        "- cautions 要列出不确定性、安全、隐私和图像质量限制。\n\n"
        "只返回符合 schema 的 JSON 对象，不要 Markdown，不要 JSON 之外的说明。所有字段都必须使用中文。\n\n"
        f"时间：{status.timestamp.isoformat(timespec='seconds')}\n"
        f"跟踪状态：{status.tracking_state}\n"
        f"目标框：{status.bbox}\n"
        f"目标角：{status.target_angle}\n"
        f"舵机输出角：{status.output_angle}\n"
        f"丢失帧数：{status.missed_frames}\n"
        f"当前人物检测数：{status.detection_count}\n"
        f"摄像头：{status.camera_source or 'unknown'} ({status.camera_backend or 'unknown'})\n"
        f"串口：{'已连接 ' + status.serial_port if status.serial_connected and status.serial_port else '未连接'}"
    )


def request_target_report_analysis(
    images: ReportImageBundle,
    status: ReportStatus,
    *,
    client: Any | None = None,
    model: str | None = None,
) -> TargetReportAnalysis:
    if client is None:
        from openai import OpenAI

        client = OpenAI()

    response = client.responses.create(
        model=model or os.getenv(REPORT_MODEL_ENV) or DEFAULT_REPORT_MODEL,
        input=[
            {
                "role": "user",
                "content": [
                    {"type": "input_text", "text": build_report_prompt(status)},
                    {"type": "input_image", "image_url": images.target_crop_data_url, "detail": "high"},
                    {"type": "input_image", "image_url": images.full_frame_data_url, "detail": "high"},
                ],
            }
        ],
        text={"format": target_report_schema()},
    )
    return parse_target_report_analysis(response)


def parse_target_report_analysis(response: Any) -> TargetReportAnalysis:
    output_text = getattr(response, "output_text", None)
    if output_text is None and isinstance(response, dict):
        output_text = response.get("output_text")
    if output_text is None:
        output_text = _extract_text_from_response_output(getattr(response, "output", None))
    if output_text is None:
        raise ValueError("OpenAI response did not contain text output")

    payload = json.loads(output_text)
    return TargetReportAnalysis(
        overall_description=str(payload["overall_description"]),
        visible_features=[str(item) for item in payload["visible_features"]],
        position_and_pose=str(payload["position_and_pose"]),
        environment_and_activity=str(payload["environment_and_activity"]),
        confidence=str(payload["confidence"]),
        cautions=[str(item) for item in payload["cautions"]],
    )


def _extract_text_from_response_output(output: Any) -> str | None:
    if output is None:
        return None
    for item in output:
        content = getattr(item, "content", None)
        if content is None and isinstance(item, dict):
            content = item.get("content")
        if not content:
            continue
        for content_item in content:
            text = getattr(content_item, "text", None)
            if text is None and isinstance(content_item, dict):
                text = content_item.get("text")
            if text:
                return str(text)
    return None


def default_report_path(timestamp: datetime, reports_dir: Path | str = "reports") -> Path:
    return Path(reports_dir) / f"{timestamp.strftime('%Y%m%d_%H%M%S')}_target_report.pdf"


def generate_target_report_pdf(
    output_path: Path | str,
    images: ReportImageBundle,
    status: ReportStatus,
    analysis: TargetReportAnalysis,
) -> Path:
    from reportlab.lib import colors
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
    from reportlab.lib.units import mm
    from reportlab.pdfbase import pdfmetrics
    from reportlab.pdfbase.cidfonts import UnicodeCIDFont
    from reportlab.platypus import Image, Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    pdfmetrics.registerFont(UnicodeCIDFont("STSong-Light"))
    chinese_font = "STSong-Light"
    styles = getSampleStyleSheet()
    title_style = ParagraphStyle("TargetPointerTitle", parent=styles["Title"], fontName=chinese_font, fontSize=17, leading=22)
    body_style = ParagraphStyle("TargetPointerBody", parent=styles["BodyText"], fontName=chinese_font, fontSize=9.5, leading=14)
    heading_style = ParagraphStyle("TargetPointerHeading", parent=styles["Heading2"], fontName=chinese_font, fontSize=13, leading=17)

    with tempfile.TemporaryDirectory(prefix="targetpointer_report_") as tmp_dir:
        tmp = Path(tmp_dir)
        target_path = tmp / "target.jpg"
        context_path = tmp / "context.jpg"
        target_path.write_bytes(images.target_crop_jpeg)
        context_path.write_bytes(images.full_frame_jpeg)

        doc = SimpleDocTemplate(
            str(output_path),
            pagesize=A4,
            rightMargin=16 * mm,
            leftMargin=16 * mm,
            topMargin=15 * mm,
            bottomMargin=14 * mm,
            pageCompression=0,
        )
        story: list[Any] = [
            Paragraph("TargetPointer 选中人物报告", title_style),
            Paragraph(status.timestamp.strftime("%Y-%m-%d %H:%M:%S"), body_style),
            Spacer(1, 5 * mm),
            Table(
                [
                    [
                        Image(str(target_path), width=74 * mm, height=74 * mm, kind="bound"),
                        Image(str(context_path), width=100 * mm, height=74 * mm, kind="bound"),
                    ]
                ],
                colWidths=[80 * mm, 104 * mm],
            ),
            Spacer(1, 5 * mm),
        ]

        sections = [
            ("总体描述", _escape_paragraph_text(analysis.overall_description)),
            ("可见特征", _bullet_text(analysis.visible_features)),
            ("位置与姿态", _escape_paragraph_text(analysis.position_and_pose)),
            ("背景环境与当前活动", _escape_paragraph_text(analysis.environment_and_activity)),
            ("可信度说明", _escape_paragraph_text(analysis.confidence)),
            ("注意事项", _bullet_text(analysis.cautions)),
            ("系统状态", _status_text(status)),
        ]
        for title, text in sections:
            story.append(Paragraph(title, heading_style))
            story.append(Paragraph(text, body_style))
            story.append(Spacer(1, 3 * mm))

        doc.build(story, onFirstPage=_draw_footer, onLaterPages=_draw_footer)

    return output_path


def _bullet_text(items: list[str]) -> str:
    if not items:
        return "未报告。"
    return "<br/>".join(f"- {_escape_paragraph_text(item)}" for item in items)


def _status_text(status: ReportStatus) -> str:
    camera_source = _escape_paragraph_text(status.camera_source or "未知")
    camera_backend = _escape_paragraph_text(status.camera_backend or "未知")
    serial = _escape_paragraph_text(status.serial_port if status.serial_connected and status.serial_port else "未连接")
    return (
        f"跟踪状态：{_escape_paragraph_text(status.tracking_state)}<br/>"
        f"目标框：{status.bbox}<br/>"
        f"目标角：{status.target_angle if status.target_angle is not None else '未知'}<br/>"
        f"舵机输出：{status.output_angle if status.output_angle is not None else '未知'}<br/>"
        f"丢失帧数：{status.missed_frames}；检测数：{status.detection_count}<br/>"
        f"摄像头：{camera_source} / {camera_backend}<br/>"
        f"串口：{serial}"
    )


def _escape_paragraph_text(text: str) -> str:
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;").replace("\n", "<br/>")


def _draw_footer(canvas, doc) -> None:
    from reportlab.lib import colors
    from reportlab.lib.units import mm

    del doc
    canvas.saveState()
    from reportlab.pdfbase import pdfmetrics
    from reportlab.pdfbase.cidfonts import UnicodeCIDFont

    pdfmetrics.registerFont(UnicodeCIDFont("STSong-Light"))
    canvas.setFont("STSong-Light", 8)
    canvas.setFillColor(colors.HexColor("#6f6f78"))
    canvas.drawString(16 * mm, 9 * mm, "由 TargetPointer 生成。仅描述可见信息，不作为身份识别报告。")
    canvas.restoreState()
