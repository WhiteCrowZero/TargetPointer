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
        "You are TargetPointer's visual intelligence module. Produce a concise, cinematic, tactical-style JSON report "
        "about the currently selected person in a fixed-camera demo. Keep the tone cool and high-signal, but never invent "
        "facts for style.\n\n"
        "Strict evidence rules:\n"
        "- Use only visible evidence from the selected target crop, the full scene image, and the system status below.\n"
        "- The first image is the selected target crop and has priority for person details.\n"
        "- The second image is full-scene context and is only for surroundings, relative location, and activity context.\n"
        "- If a detail is not clearly visible, say that it is unclear instead of guessing.\n"
        "- Do not identify the person or compare them to a known person.\n"
        "- Do not infer race, ethnicity, nationality, religion, health, wealth, occupation, or other sensitive traits.\n"
        "- If mentioning age or gender, phrase it as a cautious visual estimate only.\n"
        "- Do not describe hidden body features or anything outside the images.\n"
        "- visible_features must be short bullet-like strings grounded in visible clothing, posture, carried objects, "
        "or clearly visible appearance details.\n"
        "- confidence must explain which visual factors increase or reduce certainty.\n"
        "- cautions must list uncertainty, safety, privacy, and image-quality limitations.\n\n"
        "Return only the JSON object matching the schema. No markdown, no prose outside JSON.\n\n"
        f"Timestamp: {status.timestamp.isoformat(timespec='seconds')}\n"
        f"Tracking state: {status.tracking_state}\n"
        f"Bounding box: {status.bbox}\n"
        f"Target angle: {status.target_angle}\n"
        f"Servo output angle: {status.output_angle}\n"
        f"Missed frames: {status.missed_frames}\n"
        f"Visible detections: {status.detection_count}\n"
        f"Camera: {status.camera_source or 'unknown'} ({status.camera_backend or 'unknown'})\n"
        f"Serial: {'connected to ' + status.serial_port if status.serial_connected and status.serial_port else 'not connected'}"
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
                    {"type": "input_image", "image_url": images.full_frame_data_url, "detail": "low"},
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
    from reportlab.platypus import Image, Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    styles = getSampleStyleSheet()
    body_style = ParagraphStyle("TargetPointerBody", parent=styles["BodyText"], fontName="Helvetica", fontSize=9.5, leading=13)
    heading_style = ParagraphStyle("TargetPointerHeading", parent=styles["Heading2"], fontName="Helvetica-Bold", fontSize=13)

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
            Paragraph("TargetPointer Selected Person Report", styles["Title"]),
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
            ("Overall Description", _escape_paragraph_text(analysis.overall_description)),
            ("Visible Features", _bullet_text(analysis.visible_features)),
            ("Position and Pose", _escape_paragraph_text(analysis.position_and_pose)),
            ("Environment and Current Activity", _escape_paragraph_text(analysis.environment_and_activity)),
            ("Confidence", _escape_paragraph_text(analysis.confidence)),
            ("Cautions", _bullet_text(analysis.cautions)),
            ("System Status", _status_text(status)),
        ]
        for title, text in sections:
            story.append(Paragraph(title, heading_style))
            story.append(Paragraph(text, body_style))
            story.append(Spacer(1, 3 * mm))

        doc.build(story, onFirstPage=_draw_footer, onLaterPages=_draw_footer)

    return output_path


def _bullet_text(items: list[str]) -> str:
    if not items:
        return "None reported."
    return "<br/>".join(f"- {_escape_paragraph_text(item)}" for item in items)


def _status_text(status: ReportStatus) -> str:
    camera_source = _escape_paragraph_text(status.camera_source or "unknown")
    camera_backend = _escape_paragraph_text(status.camera_backend or "unknown")
    serial = _escape_paragraph_text(status.serial_port if status.serial_connected and status.serial_port else "not connected")
    return (
        f"Tracking: {_escape_paragraph_text(status.tracking_state)}<br/>"
        f"BBox: {status.bbox}<br/>"
        f"Target angle: {status.target_angle if status.target_angle is not None else 'unknown'}<br/>"
        f"Servo output: {status.output_angle if status.output_angle is not None else 'unknown'}<br/>"
        f"Missed frames: {status.missed_frames}; detections: {status.detection_count}<br/>"
        f"Camera: {camera_source} / {camera_backend}<br/>"
        f"Serial: {serial}"
    )


def _escape_paragraph_text(text: str) -> str:
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;").replace("\n", "<br/>")


def _draw_footer(canvas, doc) -> None:
    from reportlab.lib import colors
    from reportlab.lib.units import mm

    del doc
    canvas.saveState()
    canvas.setFont("Helvetica", 8)
    canvas.setFillColor(colors.HexColor("#6f6f78"))
    canvas.drawString(16 * mm, 9 * mm, "Generated by TargetPointer. Visible information only; not an identity report.")
    canvas.restoreState()
