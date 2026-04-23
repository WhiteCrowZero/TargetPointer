#!/usr/bin/env python3

from __future__ import annotations

import argparse
from collections import deque
from dataclasses import dataclass
from datetime import datetime
import json
import os
from pathlib import Path
import sys
import time

try:
    import cv2
except ImportError as exc:
    raise SystemExit(
        "Missing dependency: opencv-python. Run `uv sync` in the repository root, then retry with `uv run python ...`."
    ) from exc

try:
    from ultralytics import YOLO
except ImportError as exc:
    raise SystemExit(
        "Missing dependency: ultralytics. Run `uv sync` in the repository root, then retry with `uv run python ...`."
    ) from exc

try:
    from PySide6 import QtCore, QtGui, QtWidgets
except ImportError as exc:
    raise SystemExit(
        "Missing dependency: PySide6. Run `uv sync` in the repository root, then retry with `uv run python ...`."
    ) from exc

import serial

from dotenv import load_dotenv

from targetpointer.runtime.runtime import PointerRuntime, RuntimeSnapshot, list_serial_ports
from targetpointer.ui.realtime_chat import (
    DEFAULT_TARGETPOINTER_USER_IDENTITY,
    RealtimeChatApiClient,
    RealtimeChatApiError,
    RealtimeVoiceConfig,
    RealtimeVoiceSessionConfig,
    build_realtime_voice_session_payload,
    format_voice_session_details,
    realtime_chat_api_base_url,
)
from targetpointer.ui.voice_client import DesktopLiveKitClientThread
from targetpointer.reporting.report import (
    GeneratedReport,
    ReportStatus,
    build_report_images,
    default_report_path,
    encode_jpeg,
    generate_target_report_pdf,
    jpeg_data_url,
    request_target_report_analysis,
)
from targetpointer.voice.voices import voice_choices


WINDOW_TITLE = "TargetPointer Console"
WINDOW_ICON_TEXT = "➜"
INSIGHTS_ICON_TEXT = "↗"
VOICE_IMAGE_ATTACHMENT_LIMIT = 3

TRACKING_LABELS = {
    "selecting": "Selecting",
    "locked": "Locked",
    "reacquiring": "Reacquiring",
    "centering": "Centering",
    "lost": "Lost",
}

TRACKING_TONES = {
    "selecting": "soft",
    "locked": "good",
    "reacquiring": "warm",
    "centering": "warm",
    "lost": "danger",
}


class PolishedComboBox(QtWidgets.QComboBox):
    def paintEvent(self, event: QtGui.QPaintEvent) -> None:
        super().paintEvent(event)
        painter = QtGui.QPainter(self)
        painter.setRenderHint(QtGui.QPainter.Antialiasing)
        pen = QtGui.QPen(QtGui.QColor("#28608f"))
        pen.setWidthF(1.8)
        pen.setCapStyle(QtCore.Qt.RoundCap)
        pen.setJoinStyle(QtCore.Qt.RoundJoin)
        painter.setPen(pen)

        center_x = self.width() - 22
        center_y = self.height() / 2
        points = QtGui.QPolygonF(
            [
                QtCore.QPointF(center_x - 4, center_y - 2),
                QtCore.QPointF(center_x, center_y + 2),
                QtCore.QPointF(center_x + 4, center_y - 2),
            ]
        )
        painter.drawPolyline(points)


class ComboItemDelegate(QtWidgets.QStyledItemDelegate):
    def sizeHint(self, option: QtWidgets.QStyleOptionViewItem, index: QtCore.QModelIndex) -> QtCore.QSize:
        size = super().sizeHint(option, index)
        size.setHeight(max(size.height(), 38))
        return size


def configure_combo_box(combo: QtWidgets.QComboBox, *, min_popup_width: int = 260, max_visible_items: int = 8) -> None:
    combo.setMinimumHeight(42)
    combo.setMaxVisibleItems(max_visible_items)
    combo.setSizeAdjustPolicy(QtWidgets.QComboBox.AdjustToMinimumContentsLengthWithIcon)

    popup_view = QtWidgets.QListView(combo)
    popup_view.setObjectName("ComboPopupList")
    popup_view.setMinimumWidth(min_popup_width)
    popup_view.setSpacing(4)
    popup_view.setTextElideMode(QtCore.Qt.ElideMiddle)
    popup_view.setVerticalScrollMode(QtWidgets.QAbstractItemView.ScrollPerPixel)
    popup_view.setHorizontalScrollBarPolicy(QtCore.Qt.ScrollBarAlwaysOff)
    popup_view.setFrameShape(QtWidgets.QFrame.NoFrame)
    combo.setView(popup_view)
    combo.setItemDelegate(ComboItemDelegate(combo))


def repolish_widget(widget: QtWidgets.QWidget) -> None:
    style = widget.style()
    style.unpolish(widget)
    style.polish(widget)
    widget.update()


@dataclass(frozen=True)
class DesktopButtonState:
    open_camera_enabled: bool
    close_camera_enabled: bool
    connect_enabled: bool
    disconnect_enabled: bool
    redetect_enabled: bool
    center_enabled: bool
    stop_enabled: bool
    report_enabled: bool
    voice_enabled: bool


@dataclass(frozen=True)
class DesktopFlowState:
    text: str
    tone: str


@dataclass(frozen=True)
class VoiceTranscriptLine:
    timestamp: str
    role: str
    text: str


@dataclass(frozen=True)
class VoiceImageSnapshot:
    captured_at: float
    captured_at_label: str
    frame: object


def format_model_display_name(model_name: str) -> str:
    normalized = model_name.replace("\\", "/").rstrip("/")
    if not normalized:
        return model_name
    return normalized.split("/")[-1]


def format_metric(value: int | float | None, precision: int = 0) -> str:
    if value is None:
        return "—"
    if isinstance(value, float):
        return f"{value:.{precision}f}"
    return str(value)


def format_voice_timestamp(moment: datetime | None = None) -> str:
    active_moment = moment or datetime.now()
    return active_moment.strftime("%Y-%m-%d %H:%M")


@dataclass(frozen=True)
class HistoryPoint:
    timestamp: float
    tracking_state: str
    target_angle: int | None
    output_angle: int | None
    detection_count: int
    missed_frames: int
    match_score: float | None


def build_history_point(snapshot: RuntimeSnapshot, timestamp: float) -> HistoryPoint:
    return HistoryPoint(
        timestamp=timestamp,
        tracking_state=snapshot.tracking_state,
        target_angle=snapshot.target_angle,
        output_angle=snapshot.output_angle,
        detection_count=len(snapshot.pending_detections),
        missed_frames=snapshot.missed_frames,
        match_score=None if snapshot.last_match is None else snapshot.last_match.score,
    )


def latest_non_none(values: list[int | float | None]) -> int | float | None:
    for value in reversed(values):
        if value is not None:
            return value
    return None


def compute_plot_range(
    values: list[int | float | None],
    *,
    fixed_min: float | None = None,
    fixed_max: float | None = None,
) -> tuple[float, float]:
    valid_values = [float(value) for value in values if value is not None]
    minimum = fixed_min if fixed_min is not None else (min(valid_values) if valid_values else 0.0)
    maximum = fixed_max if fixed_max is not None else (max(valid_values) if valid_values else 1.0)

    if minimum == maximum:
        if minimum == 0.0:
            maximum = 1.0
        else:
            delta = abs(minimum) * 0.1 or 1.0
            minimum -= delta
            maximum += delta

    return minimum, maximum


def format_axis_value(value: float, precision: int = 0) -> str:
    if precision > 0:
        return f"{value:.{precision}f}"
    return str(int(round(value)))


def build_desktop_button_state(
    *,
    has_camera_source: bool,
    camera_open: bool,
    has_serial_port: bool,
    serial_connected: bool,
    has_report_target: bool = False,
    voice_running: bool = False,
) -> DesktopButtonState:
    return DesktopButtonState(
        open_camera_enabled=has_camera_source and not camera_open,
        close_camera_enabled=camera_open,
        connect_enabled=has_serial_port and not serial_connected,
        disconnect_enabled=serial_connected,
        redetect_enabled=camera_open,
        center_enabled=serial_connected,
        stop_enabled=serial_connected,
        report_enabled=camera_open and has_report_target,
        voice_enabled=camera_open or voice_running,
    )


def build_desktop_flow_state(
    *,
    camera_open: bool,
    serial_connected: bool,
    tracking_state: str | None,
) -> DesktopFlowState:
    if not serial_connected:
        return DesktopFlowState("Step 1 · Connect device", "soft")
    if not camera_open:
        return DesktopFlowState("Step 2 · Open camera", "soft")
    if tracking_state == "locked":
        return DesktopFlowState("Live · Tracking selected person", "good")
    if tracking_state == "reacquiring":
        return DesktopFlowState("Live · Reacquiring selected person", "warm")
    if tracking_state == "centering":
        return DesktopFlowState("Device · Centering", "warm")
    if tracking_state == "lost":
        return DesktopFlowState("Ready · Select a target again", "soft")
    return DesktopFlowState("Step 3 · Click a detected person or drag a box", "soft")


def snapshot_has_report_target(snapshot: RuntimeSnapshot | None) -> bool:
    return snapshot is not None and snapshot.tracked_bbox is not None and snapshot.tracking_state in {
        "locked",
        "reacquiring",
    }


def build_report_status(snapshot: RuntimeSnapshot, timestamp: datetime | None = None) -> ReportStatus:
    if snapshot.tracked_bbox is None:
        raise ValueError("Snapshot does not contain a selected target")
    return ReportStatus(
        timestamp=timestamp or datetime.now(),
        tracking_state=snapshot.tracking_state,
        bbox=snapshot.tracked_bbox,
        target_angle=snapshot.target_angle,
        output_angle=snapshot.output_angle,
        missed_frames=snapshot.missed_frames,
        detection_count=len(snapshot.pending_detections),
        camera_source=snapshot.camera_source,
        camera_backend=snapshot.camera_backend,
        serial_connected=snapshot.serial_connected,
        serial_port=snapshot.serial_port,
    )


def build_arrow_icon(
    glyph: str,
    *,
    size: int = 128,
    background: str = "#eef5ff",
    foreground: str = "#0071e3",
) -> QtGui.QIcon:
    pixmap = QtGui.QPixmap(size, size)
    pixmap.fill(QtCore.Qt.transparent)

    painter = QtGui.QPainter(pixmap)
    painter.setRenderHint(QtGui.QPainter.Antialiasing, True)
    painter.setPen(QtCore.Qt.NoPen)
    painter.setBrush(QtGui.QColor(background))
    painter.drawRoundedRect(QtCore.QRectF(6, 6, size - 12, size - 12), 28, 28)

    font = QtGui.QFont("Segoe UI Emoji")
    if not QtGui.QFontInfo(font).exactMatch():
        font = QtGui.QFont("Segoe UI Symbol")
    font.setPixelSize(int(size * 0.48))
    font.setBold(True)
    painter.setFont(font)
    painter.setPen(QtGui.QColor(foreground))
    painter.drawText(QtCore.QRectF(0, 0, size, size), QtCore.Qt.AlignCenter, glyph)
    painter.end()
    return QtGui.QIcon(pixmap)


def frame_to_qpixmap(frame) -> QtGui.QPixmap:
    rgb_frame = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
    height, width, channels = rgb_frame.shape
    bytes_per_line = channels * width
    image = QtGui.QImage(rgb_frame.data, width, height, bytes_per_line, QtGui.QImage.Format_RGB888).copy()
    return QtGui.QPixmap.fromImage(image)


def render_preview_frame(snapshot: RuntimeSnapshot):
    frame = snapshot.frame.copy()
    for detection in snapshot.pending_detections:
        x, y, width, height = detection.bbox
        cv2.rectangle(frame, (x, y), (x + width, y + height), (208, 139, 92), 2)

    if snapshot.tracked_bbox is not None:
        x, y, width, height = snapshot.tracked_bbox
        cv2.rectangle(frame, (x, y), (x + width, y + height), (74, 127, 103), 3)
        if snapshot.smoothed_target_center is not None:
            cv2.circle(
                frame,
                (int(snapshot.smoothed_target_center[0]), int(snapshot.smoothed_target_center[1])),
                5,
                (177, 96, 71),
                -1,
            )

    return frame


class VideoFrameWidget(QtWidgets.QLabel):
    point_selected = QtCore.Signal(int, int)
    bbox_selected = QtCore.Signal(int, int, int, int)

    def __init__(self) -> None:
        super().__init__()
        self.setMinimumSize(920, 560)
        self.setAlignment(QtCore.Qt.AlignCenter)
        self._pixmap: QtGui.QPixmap | None = None
        self._frame_size: QtCore.QSize | None = None
        self._drag_origin: QtCore.QPoint | None = None
        self._dragging = False
        self._rubber_band = QtWidgets.QRubberBand(QtWidgets.QRubberBand.Rectangle, self)
        self._rubber_band.setObjectName("SelectionBand")
        self.setText("Open a camera to start the stage preview")

    def set_frame(self, frame) -> None:
        self._pixmap = frame_to_qpixmap(frame)
        self._frame_size = QtCore.QSize(frame.shape[1], frame.shape[0])
        self._update_scaled_pixmap()

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        self._update_scaled_pixmap()

    def mousePressEvent(self, event: QtGui.QMouseEvent) -> None:
        if event.button() != QtCore.Qt.LeftButton or self._pixmap is None or self._frame_size is None:
            return
        display_rect = self._display_rect()
        if display_rect is None or not display_rect.contains(event.position().toPoint()):
            return
        self._drag_origin = event.position().toPoint()
        self._dragging = False
        self._rubber_band.setGeometry(QtCore.QRect(self._drag_origin, QtCore.QSize()))
        self._rubber_band.show()

    def mouseMoveEvent(self, event: QtGui.QMouseEvent) -> None:
        if self._drag_origin is None:
            return
        display_rect = self._display_rect()
        if display_rect is None:
            return
        current_point = self._clamp_to_display(event.position().toPoint(), display_rect)
        drag_rect = QtCore.QRect(self._drag_origin, current_point).normalized()
        if drag_rect.width() > 6 or drag_rect.height() > 6:
            self._dragging = True
        self._rubber_band.setGeometry(drag_rect)

    def mouseReleaseEvent(self, event: QtGui.QMouseEvent) -> None:
        if event.button() != QtCore.Qt.LeftButton or self._drag_origin is None:
            return

        display_rect = self._display_rect()
        release_point = event.position().toPoint()
        self._rubber_band.hide()

        if display_rect is None:
            self._drag_origin = None
            self._dragging = False
            return

        release_point = self._clamp_to_display(release_point, display_rect)
        drag_rect = QtCore.QRect(self._drag_origin, release_point).normalized()

        if self._dragging and drag_rect.width() >= 12 and drag_rect.height() >= 12:
            top_left = self._map_widget_to_frame(drag_rect.topLeft(), display_rect)
            bottom_right = self._map_widget_to_frame(drag_rect.bottomRight(), display_rect)
            bbox_x = max(0, min(top_left[0], bottom_right[0]))
            bbox_y = max(0, min(top_left[1], bottom_right[1]))
            bbox_w = max(1, abs(bottom_right[0] - top_left[0]))
            bbox_h = max(1, abs(bottom_right[1] - top_left[1]))
            self.bbox_selected.emit(bbox_x, bbox_y, bbox_w, bbox_h)
        else:
            frame_x, frame_y = self._map_widget_to_frame(release_point, display_rect)
            self.point_selected.emit(frame_x, frame_y)

        self._drag_origin = None
        self._dragging = False

    def clear_preview(self, text: str) -> None:
        self._pixmap = None
        self._frame_size = None
        self.clear()
        self.setText(text)
        self._rubber_band.hide()

    def _update_scaled_pixmap(self) -> None:
        if self._pixmap is None:
            return

        scaled = self._pixmap.scaled(
            self.contentsRect().size(),
            QtCore.Qt.KeepAspectRatio,
            QtCore.Qt.SmoothTransformation,
        )
        self.setPixmap(scaled)

    def _display_rect(self) -> QtCore.QRect | None:
        if self._pixmap is None:
            return None
        content_rect = self.contentsRect()
        scaled_size = self._pixmap.size().scaled(content_rect.size(), QtCore.Qt.KeepAspectRatio)
        offset_x = content_rect.x() + (content_rect.width() - scaled_size.width()) // 2
        offset_y = content_rect.y() + (content_rect.height() - scaled_size.height()) // 2
        return QtCore.QRect(offset_x, offset_y, scaled_size.width(), scaled_size.height())

    def _clamp_to_display(self, point: QtCore.QPoint, display_rect: QtCore.QRect) -> QtCore.QPoint:
        clamped_x = min(max(point.x(), display_rect.left()), display_rect.right())
        clamped_y = min(max(point.y(), display_rect.top()), display_rect.bottom())
        return QtCore.QPoint(clamped_x, clamped_y)

    def _map_widget_to_frame(
        self,
        point: QtCore.QPoint,
        display_rect: QtCore.QRect,
    ) -> tuple[int, int]:
        if self._frame_size is None:
            return 0, 0
        local_x = point.x() - display_rect.left()
        local_y = point.y() - display_rect.top()
        frame_x = int(local_x * self._frame_size.width() / max(1, display_rect.width()))
        frame_y = int(local_y * self._frame_size.height() / max(1, display_rect.height()))
        return frame_x, frame_y


class StatusBadge(QtWidgets.QLabel):
    def __init__(self, text: str = "") -> None:
        super().__init__(text)
        self.setAlignment(QtCore.Qt.AlignCenter)
        self.setProperty("tone", "soft")
        self.setMinimumHeight(34)

    def set_badge(self, text: str, tone: str) -> None:
        self.setText(text)
        self.setProperty("tone", tone)
        self.style().unpolish(self)
        self.style().polish(self)
        self.update()


class SidebarNavButton(QtWidgets.QPushButton):
    def __init__(self, title: str, subtitle: str) -> None:
        super().__init__()
        self.setObjectName("SidebarNavButton")
        self.setProperty("active", False)
        self.setCursor(QtCore.Qt.PointingHandCursor)
        self.setCheckable(True)
        self.setMinimumHeight(74)

        layout = QtWidgets.QVBoxLayout(self)
        layout.setContentsMargins(16, 12, 16, 12)
        layout.setSpacing(3)

        title_label = QtWidgets.QLabel(title)
        title_label.setObjectName("SidebarNavTitle")
        subtitle_label = QtWidgets.QLabel(subtitle)
        subtitle_label.setObjectName("SidebarNavSubtitle")
        subtitle_label.setWordWrap(True)

        layout.addWidget(title_label)
        layout.addWidget(subtitle_label)

    def set_active(self, active: bool) -> None:
        self.setChecked(active)
        self.setProperty("active", active)
        self.style().unpolish(self)
        self.style().polish(self)
        self.update()


class GuidanceCard(QtWidgets.QFrame):
    def __init__(self) -> None:
        super().__init__()
        self.setObjectName("GuidanceCard")
        self.setProperty("tone", "soft")
        layout = QtWidgets.QVBoxLayout(self)
        layout.setContentsMargins(16, 14, 16, 14)
        layout.setSpacing(4)

        title = QtWidgets.QLabel("Next Step")
        title.setObjectName("TileLabel")
        self.value_label = QtWidgets.QLabel("Step 1 · Connect device")
        self.value_label.setObjectName("GuidanceValue")
        self.value_label.setWordWrap(True)

        layout.addWidget(title)
        layout.addWidget(self.value_label)

    def set_guidance(self, text: str, tone: str) -> None:
        self.value_label.setText(text)
        self.setProperty("tone", tone)
        self.style().unpolish(self)
        self.style().polish(self)
        self.update()


class StatTile(QtWidgets.QFrame):
    def __init__(self, title: str, value: str = "—", *, featured: bool = False) -> None:
        super().__init__()
        self.setObjectName("StatTile")
        self.setProperty("featured", featured)
        layout = QtWidgets.QVBoxLayout(self)
        layout.setContentsMargins(14, 12, 14, 12)
        layout.setSpacing(4)

        title_label = QtWidgets.QLabel(title)
        title_label.setObjectName("TileLabel")
        self.value_label = QtWidgets.QLabel(value)
        self.value_label.setObjectName("TileValue")
        self.value_label.setWordWrap(True)

        layout.addWidget(title_label)
        layout.addWidget(self.value_label)

    def set_value(self, value: str) -> None:
        self.value_label.setText(value)


class ActivityDialog(QtWidgets.QWidget):
    def __init__(self, parent: QtWidgets.QWidget | None = None) -> None:
        super().__init__(parent)

        layout = QtWidgets.QVBoxLayout(self)
        layout.setContentsMargins(24, 22, 24, 22)
        layout.setSpacing(18)

        shell = QtWidgets.QFrame()
        shell.setObjectName("DialogShell")
        shell_layout = QtWidgets.QVBoxLayout(shell)
        shell_layout.setContentsMargins(22, 22, 22, 22)
        shell_layout.setSpacing(12)

        title = QtWidgets.QLabel("Activity")
        title.setObjectName("DrawerTitle")
        subtitle = QtWidgets.QLabel("Recent device and runtime events.")
        subtitle.setObjectName("SubtleLabel")

        close_button = QtWidgets.QPushButton("Close")
        close_button.setObjectName("GhostButton")
        close_button.setAutoDefault(False)
        close_button.hide()

        top_row = QtWidgets.QHBoxLayout()
        top_row.setSpacing(12)
        title_stack = QtWidgets.QVBoxLayout()
        title_stack.setSpacing(2)
        title_stack.addWidget(title)
        title_stack.addWidget(subtitle)
        top_row.addLayout(title_stack)
        top_row.addStretch(1)
        top_row.addWidget(close_button)

        self.log_output = QtWidgets.QPlainTextEdit()
        self.log_output.setObjectName("ActivityLog")
        self.log_output.setReadOnly(True)
        self.log_output.setMaximumBlockCount(300)
        self.log_output.setPlaceholderText("Recent activity appears here.")
        self.log_output.setLineWrapMode(QtWidgets.QPlainTextEdit.NoWrap)
        self.log_output.setVerticalScrollBarPolicy(QtCore.Qt.ScrollBarAlwaysOn)
        self.log_output.setHorizontalScrollBarPolicy(QtCore.Qt.ScrollBarAsNeeded)

        shell_layout.addLayout(top_row)
        shell_layout.addWidget(self.log_output, stretch=1)
        layout.addWidget(shell)


class ToastMessage(QtWidgets.QFrame):
    def __init__(self, parent: QtWidgets.QWidget) -> None:
        super().__init__(parent)
        self.setObjectName("ToastMessage")
        self.setProperty("tone", "danger")
        self.setAttribute(QtCore.Qt.WA_TransparentForMouseEvents, True)
        self.hide()

        layout = QtWidgets.QHBoxLayout(self)
        layout.setContentsMargins(14, 12, 14, 12)
        layout.setSpacing(8)

        self.message_label = QtWidgets.QLabel("")
        self.message_label.setObjectName("ToastLabel")
        self.message_label.setWordWrap(True)
        layout.addWidget(self.message_label)

        self._opacity_effect = QtWidgets.QGraphicsOpacityEffect(self)
        self._opacity_effect.setOpacity(1.0)
        self.setGraphicsEffect(self._opacity_effect)

        self._hide_timer = QtCore.QTimer(self)
        self._hide_timer.setSingleShot(True)
        self._hide_timer.timeout.connect(self._start_fade_out)

        self._fade_animation = QtCore.QPropertyAnimation(self._opacity_effect, b"opacity", self)
        self._fade_animation.setDuration(350)
        self._fade_animation.setStartValue(1.0)
        self._fade_animation.setEndValue(0.0)
        self._fade_animation.finished.connect(self.hide)

    def show_toast(self, message: str, duration_ms: int = 5000) -> None:
        self._hide_timer.stop()
        self._fade_animation.stop()
        self._opacity_effect.setOpacity(1.0)
        self.message_label.setText(message)
        self.adjustSize()
        self._reposition()
        self.show()
        self.raise_()
        self._hide_timer.start(duration_ms)

    def _start_fade_out(self) -> None:
        self._fade_animation.stop()
        self._fade_animation.start()

    def _reposition(self) -> None:
        parent = self.parentWidget()
        if parent is None:
            return
        margin = 18
        available_width = min(360, max(220, parent.width() - margin * 2))
        self.setMaximumWidth(available_width)
        self.adjustSize()
        toast_width = min(self.width(), available_width)
        self.resize(toast_width, self.height())
        x = max(margin, parent.width() - toast_width - margin)
        y = margin
        self.move(x, y)


class ReportWorker(QtCore.QObject):
    finished = QtCore.Signal(object)
    failed = QtCore.Signal(str)

    def __init__(self, snapshot: RuntimeSnapshot, reports_dir: Path | str = "reports") -> None:
        super().__init__()
        self.snapshot = snapshot
        self.reports_dir = Path(reports_dir)

    @QtCore.Slot()
    def run(self) -> None:
        try:
            timestamp = datetime.now()
            status = build_report_status(self.snapshot, timestamp)
            images = build_report_images(self.snapshot.frame.copy(), status.bbox)
            analysis = request_target_report_analysis(images, status)
            report_path = generate_target_report_pdf(default_report_path(timestamp, self.reports_dir), images, status, analysis)
            self.finished.emit(
                GeneratedReport(
                    path=report_path,
                    analysis=analysis,
                    status=status,
                    target_crop_jpeg=images.target_crop_jpeg,
                    full_frame_jpeg=images.full_frame_jpeg,
                )
            )
        except Exception as exc:
            self.failed.emit(str(exc))


def pixmap_from_jpeg(jpeg_bytes: bytes | None) -> QtGui.QPixmap:
    pixmap = QtGui.QPixmap()
    if jpeg_bytes:
        pixmap.loadFromData(jpeg_bytes, "JPG")
    return pixmap


class ScaledImageLabel(QtWidgets.QLabel):
    def __init__(self, placeholder: str) -> None:
        super().__init__(placeholder)
        self.setAlignment(QtCore.Qt.AlignCenter)
        self.setMinimumHeight(220)
        self.setObjectName("ReportImage")
        self._source_pixmap: QtGui.QPixmap | None = None

    def set_source_pixmap(self, pixmap: QtGui.QPixmap) -> None:
        self._source_pixmap = pixmap if not pixmap.isNull() else None
        self._update_pixmap()

    def resizeEvent(self, event: QtGui.QResizeEvent) -> None:
        super().resizeEvent(event)
        self._update_pixmap()

    def _update_pixmap(self) -> None:
        if self._source_pixmap is None:
            return
        self.setPixmap(
            self._source_pixmap.scaled(
                self.contentsRect().size(),
                QtCore.Qt.KeepAspectRatio,
                QtCore.Qt.SmoothTransformation,
            )
        )


class ReportWindow(QtWidgets.QWidget):
    generate_requested = QtCore.Signal()

    def __init__(self) -> None:
        super().__init__(None)
        self.setWindowTitle("TargetPointer 报告")
        self._build_ui()
        self._apply_styles()

    def _build_ui(self) -> None:
        root = QtWidgets.QVBoxLayout(self)
        root.setContentsMargins(24, 22, 24, 22)
        root.setSpacing(16)

        header = QtWidgets.QFrame()
        header.setObjectName("ReportHeader")
        header_layout = QtWidgets.QHBoxLayout(header)
        header_layout.setContentsMargins(20, 16, 20, 16)
        title_stack = QtWidgets.QVBoxLayout()
        title_stack.setSpacing(2)
        title = QtWidgets.QLabel("目标报告")
        title.setObjectName("ReportTitle")
        self.subtitle = QtWidgets.QLabel("生成报告后查看选中人物和环境描述。")
        self.subtitle.setObjectName("ReportSubtitle")
        title_stack.addWidget(title)
        title_stack.addWidget(self.subtitle)
        self.path_label = QtWidgets.QLabel("尚未生成 PDF")
        self.path_label.setObjectName("ReportPath")
        self.path_label.setTextInteractionFlags(QtCore.Qt.TextSelectableByMouse)
        self.generate_button = QtWidgets.QPushButton("生成报告")
        self.generate_button.setObjectName("PrimaryButton")
        self.generate_button.setEnabled(False)
        self.generate_button.clicked.connect(self.generate_requested.emit)
        header_layout.addLayout(title_stack)
        header_layout.addStretch(1)
        header_layout.addWidget(self.path_label)
        header_layout.addWidget(self.generate_button)

        image_row = QtWidgets.QHBoxLayout()
        image_row.setSpacing(14)
        self.target_image = ScaledImageLabel("目标裁剪")
        self.context_image = ScaledImageLabel("场景上下文")
        image_row.addWidget(self.target_image, stretch=1)
        image_row.addWidget(self.context_image, stretch=2)

        content = QtWidgets.QScrollArea()
        content.setWidgetResizable(True)
        content.setObjectName("ReportScroll")
        content_shell = QtWidgets.QWidget()
        content_layout = QtWidgets.QVBoxLayout(content_shell)
        content_layout.setContentsMargins(0, 0, 0, 0)
        content_layout.setSpacing(12)
        self.sections: dict[str, QtWidgets.QLabel] = {}
        for key, label in [
            ("overall", "总体描述"),
            ("features", "可见特征"),
            ("pose", "位置与姿态"),
            ("environment", "背景环境与当前活动"),
            ("confidence", "可信度说明"),
            ("cautions", "注意事项"),
            ("status", "系统状态"),
        ]:
            card = QtWidgets.QFrame()
            card.setObjectName("ReportSection")
            card_layout = QtWidgets.QVBoxLayout(card)
            card_layout.setContentsMargins(16, 14, 16, 14)
            heading = QtWidgets.QLabel(label)
            heading.setObjectName("ReportSectionTitle")
            body = QtWidgets.QLabel("—")
            body.setObjectName("ReportBody")
            body.setWordWrap(True)
            body.setTextInteractionFlags(QtCore.Qt.TextSelectableByMouse)
            card_layout.addWidget(heading)
            card_layout.addWidget(body)
            content_layout.addWidget(card)
            self.sections[key] = body
        content_layout.addStretch(1)
        content.setWidget(content_shell)

        root.addWidget(header)
        root.addLayout(image_row)
        root.addWidget(content, stretch=1)

    def _apply_styles(self) -> None:
        self.setStyleSheet(
            """
            QWidget {
                background: transparent;
                color: #0f2746;
                font-family: "Segoe UI", "Microsoft YaHei UI", sans-serif;
                font-size: 14px;
            }
            QFrame#ReportHeader,
            QFrame#ReportSection {
                background: rgba(255, 255, 255, 188);
                border: none;
                border-radius: 24px;
            }
            QLabel#ReportTitle {
                font-size: 24px;
                font-weight: 700;
                color: #0756a6;
            }
            QLabel#ReportSubtitle,
            QLabel#ReportPath {
                color: #5e84a9;
                font-size: 12px;
            }
            QLabel#ReportImage {
                background: #08243d;
                color: #eaf5ff;
                border: none;
                border-radius: 24px;
                padding: 8px;
            }
            QScrollArea#ReportScroll {
                border: none;
                background: transparent;
            }
            QLabel#ReportSectionTitle {
                color: #6b96bd;
                font-size: 12px;
                font-weight: 700;
                letter-spacing: 0;
            }
            QLabel#ReportBody {
                color: #0f2746;
                line-height: 1.55;
            }
            QPushButton {
                border-radius: 18px;
                padding: 10px 18px;
                font-weight: 700;
            }
            QPushButton#PrimaryButton {
                background: #1d73d4;
                color: #ffffff;
                border: none;
            }
            QPushButton:disabled {
                background: rgba(219, 234, 254, 130);
                color: #8aaac8;
                border: none;
            }
            QScrollBar:vertical {
                background: rgba(219, 237, 255, 115);
                width: 12px;
                margin: 8px 2px 8px 2px;
                border-radius: 6px;
            }
            QScrollBar::handle:vertical {
                background: #8ec5ff;
                border-radius: 6px;
                min-height: 36px;
            }
            QScrollBar::add-line:vertical,
            QScrollBar::sub-line:vertical,
            QScrollBar::add-page:vertical,
            QScrollBar::sub-page:vertical {
                background: transparent;
                border: none;
                border-radius: 6px;
            }
            """
        )

    def set_generate_enabled(self, enabled: bool) -> None:
        self.generate_button.setEnabled(enabled)

    def set_generating(self, generating: bool) -> None:
        self.generate_button.setText("生成中..." if generating else "生成报告")
        self.generate_button.setEnabled(not generating)

    def update_report(self, report: GeneratedReport) -> None:
        analysis = report.analysis
        self.subtitle.setText(
            report.status.timestamp.strftime("%Y-%m-%d %H:%M:%S") if report.status else "Generated report"
        )
        self.path_label.setText(str(report.path))
        self.target_image.set_source_pixmap(pixmap_from_jpeg(report.target_crop_jpeg))
        self.context_image.set_source_pixmap(pixmap_from_jpeg(report.full_frame_jpeg))
        self.sections["overall"].setText(analysis.overall_description)
        self.sections["features"].setText("\n".join(f"- {item}" for item in analysis.visible_features) or "—")
        self.sections["pose"].setText(analysis.position_and_pose)
        self.sections["environment"].setText(analysis.environment_and_activity)
        self.sections["confidence"].setText(analysis.confidence)
        self.sections["cautions"].setText("\n".join(f"- {item}" for item in analysis.cautions) or "—")
        self.sections["status"].setText(self._format_status(report.status))

    def _format_status(self, status: ReportStatus | None) -> str:
        if status is None:
            return "—"
        return (
            f"跟踪状态：{status.tracking_state}\n"
            f"目标框：{status.bbox}\n"
            f"目标角：{format_metric(status.target_angle)}\n"
            f"舵机输出：{format_metric(status.output_angle)}\n"
            f"丢失帧数：{status.missed_frames}；检测数：{status.detection_count}\n"
            f"摄像头：{status.camera_source or '未知'} / {status.camera_backend or '未知'}\n"
            f"串口：{status.serial_port if status.serial_connected and status.serial_port else '未连接'}"
        )


class VoiceWaveform(QtWidgets.QWidget):
    def __init__(self, tone: str = "agent") -> None:
        super().__init__()
        self.tone = tone
        self.state = "idle"
        self.muted = False
        self.phase = 0
        self.pattern = [0.42, 0.58, 0.74, 0.88, 0.7, 0.5, 0.36, 0.52, 0.68, 0.86, 0.78, 0.6]
        self.setMinimumHeight(150)

    def set_state(self, state: str) -> None:
        self.state = state
        self.update()

    def set_muted(self, muted: bool) -> None:
        self.muted = muted
        self.update()

    def advance(self) -> None:
        self.phase = (self.phase + 1) % 120
        self.update()

    def paintEvent(self, event: QtGui.QPaintEvent) -> None:
        del event
        painter = QtGui.QPainter(self)
        painter.setRenderHint(QtGui.QPainter.Antialiasing, True)
        rect = self.rect().adjusted(2, 2, -2, -2)
        painter.setPen(QtCore.Qt.NoPen)
        painter.setBrush(QtGui.QColor("#f8fbff"))
        painter.drawRoundedRect(rect, 22, 22)

        active = self.state in {"listening", "thinking", "speaking"} and not self.muted
        base_color = QtGui.QColor("#0d9488" if self.tone == "user" else "#2563eb")
        muted_color = QtGui.QColor("#aab6c8" if not self.muted else "#d16b6b")
        if self.state == "speaking":
            glow_color = QtGui.QColor(base_color)
            glow_color.setAlpha(42)
            painter.setBrush(glow_color)
            painter.drawRoundedRect(rect.adjusted(8, 10, -8, -10), 20, 20)
        bar_width = 12
        gap = 9
        total_width = len(self.pattern) * bar_width + (len(self.pattern) - 1) * gap
        start_x = rect.center().x() - total_width / 2
        bottom = rect.bottom() - 14

        for index, factor in enumerate(self.pattern):
            wave = 0.5 + 0.5 * abs(((self.phase + index * 7) % 24) - 12) / 12
            if self.muted:
                multiplier = 0.12
            elif self.state == "idle":
                multiplier = 0.38 + wave * 0.08
            elif self.state == "listening":
                multiplier = 0.78 + wave * 0.18
            elif self.state == "thinking":
                multiplier = 0.92 + wave * 0.24
            elif self.state == "speaking":
                multiplier = 1.08 + wave * 0.5
            else:
                multiplier = 0.72 + wave * 0.12
            height = int((38 + factor * 72) * multiplier)
            x = int(start_x + index * (bar_width + gap))
            y = bottom - height
            color = QtGui.QColor(base_color if active else muted_color)
            color.setAlpha(220 if active else 120)
            painter.setPen(QtCore.Qt.NoPen)
            painter.setBrush(color)
            painter.drawRoundedRect(QtCore.QRectF(x, y, bar_width, height), 7, 7)


class VoiceAssistantWindow(QtWidgets.QWidget):
    start_requested = QtCore.Signal(object)
    stop_requested = QtCore.Signal()
    mute_changed = QtCore.Signal(bool)

    def __init__(self) -> None:
        super().__init__(None)
        self.setWindowTitle("TargetPointer Voice Assistant")
        self.running = False
        self._build_ui()
        self._apply_styles()
        self.animation_timer = QtCore.QTimer(self)
        self.animation_timer.setInterval(80)
        self.animation_timer.timeout.connect(self._advance_animation)
        self.animation_timer.start()

    def _build_ui(self) -> None:
        outer = QtWidgets.QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)

        scroll = QtWidgets.QScrollArea()
        scroll.setObjectName("VoiceScrollArea")
        scroll.setWidgetResizable(True)
        outer.addWidget(scroll)

        content = QtWidgets.QWidget()
        scroll.setWidget(content)

        root = QtWidgets.QVBoxLayout(content)
        root.setContentsMargins(24, 22, 24, 22)
        root.setSpacing(16)

        header = QtWidgets.QFrame()
        header.setObjectName("VoiceHeader")
        header_layout = QtWidgets.QHBoxLayout(header)
        header_layout.setContentsMargins(20, 16, 20, 16)
        title_stack = QtWidgets.QVBoxLayout()
        title_stack.setSpacing(2)
        title = QtWidgets.QLabel("AI 实时对话")
        title.setObjectName("VoiceTitle")
        self.detail_label = QtWidgets.QLabel("启动后会检查 RealtimeAIChat 后端，并自动创建语音会话。")
        self.detail_label.setObjectName("VoiceSubtitle")
        title_stack.addWidget(title)
        title_stack.addWidget(self.detail_label)
        self.status_badge = StatusBadge("Stopped")
        header_layout.addLayout(title_stack)
        header_layout.addStretch(1)
        header_layout.addWidget(self.status_badge)

        stage = QtWidgets.QFrame()
        stage.setObjectName("VoiceStage")
        stage_layout = QtWidgets.QVBoxLayout(stage)
        stage_layout.setContentsMargins(18, 16, 18, 16)
        stage_layout.setSpacing(12)
        channel_row = QtWidgets.QHBoxLayout()
        channel_row.setSpacing(12)
        self.user_wave = VoiceWaveform("user")
        self.agent_wave = VoiceWaveform("agent")
        channel_row.addWidget(self._build_channel("用户", self.user_wave), stretch=1)
        channel_row.addWidget(self._build_channel("AI", self.agent_wave), stretch=1)
        stage_layout.addLayout(channel_row)

        transcript_header = QtWidgets.QHBoxLayout()
        transcript_header.setSpacing(10)
        transcript_label = QtWidgets.QLabel("实时字幕")
        transcript_label.setObjectName("VoiceChannelTitle")
        transcript_header.addWidget(transcript_label)
        transcript_header.addStretch(1)

        self.transcript_log = QtWidgets.QPlainTextEdit()
        self.transcript_log.setObjectName("VoiceTranscriptLog")
        self.transcript_log.setReadOnly(True)
        self.transcript_log.setMaximumBlockCount(220)
        self.transcript_log.setMinimumHeight(360)
        self.transcript_log.setPlaceholderText("实时字幕会显示在这里。")
        stage_layout.addLayout(transcript_header)
        stage_layout.addWidget(self.transcript_log, stretch=1)

        handoff = QtWidgets.QFrame()
        handoff.setObjectName("VoiceHandoff")
        handoff_layout = QtWidgets.QHBoxLayout(handoff)
        handoff_layout.setContentsMargins(18, 16, 18, 16)
        handoff_layout.setSpacing(12)

        session_card = QtWidgets.QFrame()
        session_card.setObjectName("VoiceFooterCard")
        session_layout = QtWidgets.QVBoxLayout(session_card)
        session_layout.setContentsMargins(0, 0, 0, 0)
        session_layout.setSpacing(10)
        session_label = QtWidgets.QLabel("会话摘要")
        session_label.setObjectName("VoiceChannelTitle")
        self.session_info = QtWidgets.QPlainTextEdit()
        self.session_info.setObjectName("VoiceSessionInfo")
        self.session_info.setReadOnly(True)
        self.session_info.setMinimumHeight(104)
        self.session_info.setMaximumHeight(132)
        self.session_info.setPlaceholderText("启动后会显示后端地址、session、room 和连接状态。")
        session_layout.addWidget(session_label)
        session_layout.addWidget(self.session_info)

        event_card = QtWidgets.QFrame()
        event_card.setObjectName("VoiceFooterCard")
        event_layout = QtWidgets.QVBoxLayout(event_card)
        event_layout.setContentsMargins(0, 0, 0, 0)
        event_layout.setSpacing(10)
        event_label = QtWidgets.QLabel("系统日志")
        event_label.setObjectName("VoiceChannelTitle")
        self.event_log = QtWidgets.QPlainTextEdit()
        self.event_log.setObjectName("VoiceEventLog")
        self.event_log.setReadOnly(True)
        self.event_log.setMaximumBlockCount(120)
        self.event_log.setMinimumHeight(104)
        self.event_log.setMaximumHeight(132)
        self.event_log.setPlaceholderText("系统通知、重连状态和后端错误会显示在这里。")
        event_layout.addWidget(event_label)
        event_layout.addWidget(self.event_log)

        handoff_layout.addWidget(session_card, stretch=1)
        handoff_layout.addWidget(event_card, stretch=1)

        config = QtWidgets.QFrame()
        config.setObjectName("VoiceConfig")
        config_layout = QtWidgets.QGridLayout(config)
        config_layout.setContentsMargins(18, 16, 18, 16)
        config_layout.setHorizontalSpacing(14)
        config_layout.setVerticalSpacing(12)

        self.default_config = RealtimeVoiceConfig()
        self.tts_voice_combo = PolishedComboBox()
        self.tts_voice_combo.setObjectName("VoiceSelect")
        for voice_name, voice_id in voice_choices(self.default_config.tts_voice):
            self.tts_voice_combo.addItem(voice_name, voice_id)
        configure_combo_box(self.tts_voice_combo, min_popup_width=300)
        voice_index = self.tts_voice_combo.findData(self.default_config.tts_voice)
        if voice_index >= 0:
            self.tts_voice_combo.setCurrentIndex(voice_index)

        fields = [
            ("人物音色", self.tts_voice_combo),
        ]
        for index, (label, widget) in enumerate(fields):
            row = index // 2
            col = (index % 2) * 2
            config_layout.addWidget(self._field_label(label), row, col)
            config_layout.addWidget(widget, row, col + 1)
        config_layout.setColumnStretch(1, 1)
        config_layout.setColumnStretch(3, 1)

        actions = QtWidgets.QHBoxLayout()
        actions.setSpacing(10)
        self.start_button = QtWidgets.QPushButton("启动会话")
        self.start_button.setObjectName("PrimaryButton")
        self.start_button.setMinimumHeight(48)
        self.mute_button = QtWidgets.QPushButton("麦克风开启")
        self.mute_button.setObjectName("SecondaryToggleButton")
        self.mute_button.setCheckable(True)
        self.mute_button.setMinimumHeight(48)
        self.mute_button.toggled.connect(self._on_mute_toggled)
        self.stop_button = QtWidgets.QPushButton("断开")
        self.stop_button.setObjectName("WarnButton")
        self.stop_button.setMinimumHeight(48)
        self.stop_button.setEnabled(False)
        self.start_button.clicked.connect(lambda: self.start_requested.emit(self.config()))
        self.stop_button.clicked.connect(self.stop_requested.emit)
        self._sync_mute_button(False)
        actions.addWidget(self.start_button, stretch=2)
        actions.addWidget(self.mute_button, stretch=1)
        actions.addWidget(self.stop_button, stretch=1)

        top_row = QtWidgets.QHBoxLayout()
        top_row.setSpacing(12)
        top_row.addWidget(config, stretch=1)

        action_frame = QtWidgets.QFrame()
        action_frame.setObjectName("VoiceActionStrip")
        action_layout = QtWidgets.QVBoxLayout(action_frame)
        action_layout.setContentsMargins(18, 16, 18, 16)
        action_layout.addLayout(actions)
        top_row.addWidget(action_frame, stretch=2)

        root.addWidget(header)
        root.addLayout(top_row)
        root.addWidget(stage, stretch=1)
        root.addWidget(handoff)

    def _build_channel(self, title: str, waveform: VoiceWaveform) -> QtWidgets.QFrame:
        card = QtWidgets.QFrame()
        card.setObjectName("VoiceChannel")
        layout = QtWidgets.QVBoxLayout(card)
        layout.setContentsMargins(14, 12, 14, 14)
        label = QtWidgets.QLabel(title)
        label.setObjectName("VoiceChannelTitle")
        layout.addWidget(label)
        layout.addWidget(waveform)
        return card

    def _field_label(self, text: str) -> QtWidgets.QLabel:
        label = QtWidgets.QLabel(text)
        label.setObjectName("VoiceFieldLabel")
        return label

    def _apply_styles(self) -> None:
        self.setStyleSheet(
            """
            QWidget {
                background: transparent;
                color: #0f2746;
                font-family: "Segoe UI", "Microsoft YaHei UI", sans-serif;
                font-size: 14px;
            }
            QFrame#VoiceHeader,
            QFrame#VoiceStage,
            QFrame#VoiceConfig,
            QFrame#VoiceHandoff,
            QFrame#VoiceChannel,
            QFrame#VoiceActionStrip {
                background: rgba(255, 255, 255, 188);
                border: none;
                border-radius: 24px;
            }
            QLabel#VoiceTitle {
                font-size: 24px;
                font-weight: 700;
                color: #0756a6;
            }
            QLabel#VoiceSubtitle {
                color: #5e84a9;
                font-size: 12px;
            }
            QLabel#VoiceChannelTitle,
            QLabel#VoiceFieldLabel {
                color: #6b96bd;
                font-size: 12px;
                font-weight: 700;
                letter-spacing: 0;
            }
            QLineEdit,
            QComboBox {
                background: rgba(255, 255, 255, 205);
                border: none;
                border-radius: 18px;
                min-height: 22px;
                padding: 7px 34px 7px 10px;
                color: #0f2746;
                placeholder-text-color: #8aaac8;
                selection-background-color: #dbeafe;
            }
            QComboBox::drop-down {
                border: none;
                width: 32px;
                background: transparent;
            }
            QComboBox::down-arrow {
                image: none;
                width: 0px;
                height: 0px;
            }
            QListView#ComboPopupList,
            QComboBox QAbstractItemView {
                background: #ffffff;
                border: none;
                border-radius: 18px;
                padding: 8px;
                color: #173b61;
                outline: 0;
                selection-background-color: #e0f2fe;
                selection-color: #0756a6;
            }
            QListView#ComboPopupList::item {
                min-height: 34px;
                padding: 8px 10px;
                border-radius: 14px;
            }
            QListView#ComboPopupList::item:hover {
                background: #eff8ff;
                color: #0756a6;
            }
            QListView#ComboPopupList::item:selected {
                background: #dbeafe;
                color: #0756a6;
            }
            QPushButton {
                border-radius: 18px;
                padding: 9px 16px;
                font-weight: 700;
            }
            QScrollArea#VoiceScrollArea {
                background: transparent;
                border: none;
            }
            QScrollArea#VoiceScrollArea > QWidget > QWidget {
                background: transparent;
            }
            QPlainTextEdit#VoiceTranscriptLog {
                background: rgba(8, 36, 61, 218);
                color: #eaf5ff;
                border: none;
                border-radius: 22px;
                font-family: "Microsoft YaHei UI", "Segoe UI", sans-serif;
                font-size: 13px;
                padding: 12px;
                selection-background-color: #1d73d4;
            }
            QPlainTextEdit#VoiceSessionInfo,
            QPlainTextEdit#VoiceEventLog {
                background: rgba(255, 255, 255, 208);
                color: #123a63;
                border: none;
                border-radius: 18px;
                font-family: "Cascadia Mono", "Consolas", monospace;
                font-size: 12px;
                padding: 10px;
                selection-background-color: #dbeafe;
            }
            QPushButton#PrimaryButton {
                background: #1d73d4;
                color: #ffffff;
                border: none;
            }
            QPushButton#SecondaryToggleButton {
                background: rgba(255, 255, 255, 198);
                color: #17466f;
                border: none;
            }
            QPushButton#SecondaryToggleButton[muted="true"] {
                background: #fff1f2;
                color: #b42318;
                border: none;
            }
            QPushButton:disabled {
                background: rgba(219, 234, 254, 130);
                color: #8aaac8;
                border: none;
            }
            QLabel[ tone="soft" ] {
                background: rgba(239, 248, 255, 210);
                color: #28608f;
                border: none;
                border-radius: 999px;
                padding: 7px 13px;
                font-size: 12px;
                font-weight: 700;
            }
            QLabel[ tone="good" ] {
                background: rgba(219, 237, 255, 230);
                color: #0756a6;
                border: none;
                border-radius: 999px;
                padding: 7px 13px;
                font-size: 12px;
                font-weight: 700;
            }
            QLabel[ tone="warm" ] {
                background: #fff7ed;
                color: #b45309;
                border: none;
                border-radius: 999px;
                padding: 7px 13px;
                font-size: 12px;
                font-weight: 700;
            }
            """
        )

    def config(self) -> RealtimeVoiceConfig:
        return RealtimeVoiceConfig(
            tts_voice=str(self.tts_voice_combo.currentData() or self.default_config.tts_voice),
        )

    def _on_mute_toggled(self, muted: bool) -> None:
        self._sync_mute_button(muted)
        self.mute_changed.emit(muted)

    def _sync_mute_button(self, muted: bool) -> None:
        self.mute_button.setText("麦克风已静音" if muted else "麦克风开启")
        self.mute_button.setProperty("muted", muted)
        repolish_widget(self.mute_button)

    def is_user_muted(self) -> bool:
        return self.mute_button.isChecked()

    def set_running(self, running: bool) -> None:
        self.running = running
        self.start_button.setEnabled(not running)
        self.stop_button.setEnabled(running)
        self.mute_button.setEnabled(running)
        self.status_badge.set_badge("运行中" if running else "已停止", "good" if running else "soft")
        if not running:
            self.user_wave.set_state("idle")
            self.agent_wave.set_state("idle")
            if self.mute_button.isChecked():
                self.mute_button.blockSignals(True)
                self.mute_button.setChecked(False)
                self.mute_button.blockSignals(False)
            self._sync_mute_button(False)
        self.detail_label.setText(
            "语音会话已启动，桌面端会直接连接 RealtimeAIChat 返回的 LiveKit 会话。"
            if running
            else "启动后会检查后端、创建 session 并自动接入 LiveKit。"
        )

    def set_connection_detail(self, detail: str) -> None:
        self.detail_label.setText(detail)

    def set_session_config(self, session: RealtimeVoiceSessionConfig | None) -> None:
        self.session_info.setPlainText(format_voice_session_details(session))

    def set_event_lines(self, lines: list[str]) -> None:
        next_text = "\n".join(lines)
        if self.event_log.toPlainText() == next_text:
            return
        self.event_log.setPlainText(next_text)
        self.event_log.moveCursor(QtGui.QTextCursor.End)

    def set_runtime_state(self, *, user_state: str, agent_state: str, user_muted: bool) -> None:
        if not self.running:
            return
        if self.mute_button.isChecked() != user_muted:
            self.mute_button.blockSignals(True)
            self.mute_button.setChecked(user_muted)
            self.mute_button.blockSignals(False)
        self._sync_mute_button(user_muted)
        self.user_wave.set_muted(user_muted)
        self.user_wave.set_state("idle" if user_muted else user_state)
        self.agent_wave.set_state(agent_state)

        if agent_state == "speaking":
            label = "AI 正在回答"
        elif agent_state == "thinking":
            label = "AI 正在思考"
        elif user_muted:
            label = "用户麦克风已静音"
        elif user_state == "speaking":
            label = "正在接收用户语音"
        else:
            label = "正在监听"
        tone = "good" if agent_state == "speaking" else "warm" if user_muted else "good"
        self.status_badge.set_badge(label, tone)

    def set_transcript_lines(
        self,
        lines: list[object],
        *,
        live_captions: dict[str, str] | None = None,
        live_caption_timestamps: dict[str, str] | None = None,
    ) -> None:
        formatted: list[str] = []
        role_names = {"user": "用户", "assistant": "AI", "system": "系统"}
        for line in lines:
            role = role_names.get(getattr(line, "role", "system"), str(getattr(line, "role", "system")))
            text = str(getattr(line, "text", "")).strip()
            if text:
                timestamp = str(getattr(line, "timestamp", "")).strip()
                prefix = f"[{timestamp}] " if timestamp else ""
                formatted.append(f"{prefix}{role}: {text}")
        if live_captions:
            for role_key in ("user", "assistant"):
                text = str(live_captions.get(role_key, "")).strip()
                if not text:
                    continue
                label = role_names.get(role_key, role_key)
                timestamp = ""
                if live_caption_timestamps is not None:
                    timestamp = str(live_caption_timestamps.get(role_key, "")).strip()
                prefix = f"[{timestamp}] " if timestamp else ""
                candidate = f"{prefix}{label}: {text}"
                if formatted and formatted[-1] == candidate:
                    continue
                formatted.append(candidate)
        next_text = "\n".join(formatted)
        if self.transcript_log.toPlainText() == next_text:
            return
        self.transcript_log.setPlainText(next_text)
        self.transcript_log.moveCursor(QtGui.QTextCursor.End)

    def _advance_animation(self) -> None:
        self.user_wave.advance()
        self.agent_wave.advance()


class TrendPlot(QtWidgets.QWidget):
    def __init__(
        self,
        color: str,
        *,
        y_label: str,
        x_label: str = "t",
        fixed_min: float | None = None,
        fixed_max: float | None = None,
        tick_precision: int = 0,
        parent: QtWidgets.QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.color = QtGui.QColor(color)
        self.values: list[int | float | None] = []
        self.y_label = y_label
        self.x_label = x_label
        self.fixed_min = fixed_min
        self.fixed_max = fixed_max
        self.tick_precision = tick_precision
        self.setMinimumHeight(128)

    def set_values(self, values: list[int | float | None]) -> None:
        self.values = values
        self.update()

    def paintEvent(self, event: QtGui.QPaintEvent) -> None:
        del event
        painter = QtGui.QPainter(self)
        painter.setRenderHint(QtGui.QPainter.Antialiasing, True)

        valid_points = [(index, value) for index, value in enumerate(self.values) if value is not None]
        minimum, maximum = compute_plot_range(self.values, fixed_min=self.fixed_min, fixed_max=self.fixed_max)

        left_margin = 38
        right_margin = 16
        top_margin = 14
        bottom_margin = 24
        plot_rect = self.rect().adjusted(left_margin, top_margin, -right_margin, -bottom_margin)
        if plot_rect.width() <= 10 or plot_rect.height() <= 10:
            return

        axis_pen = QtGui.QPen(QtGui.QColor("#d9d9e1"), 1)
        guide_pen = QtGui.QPen(QtGui.QColor("#efeff4"), 1)
        text_pen = QtGui.QPen(QtGui.QColor("#86868f"))

        painter.setPen(guide_pen)
        painter.drawLine(plot_rect.topLeft(), plot_rect.topRight())
        painter.drawLine(plot_rect.bottomLeft(), plot_rect.bottomRight())

        painter.setPen(axis_pen)
        painter.drawLine(plot_rect.bottomLeft(), plot_rect.bottomRight())
        painter.drawLine(plot_rect.bottomLeft(), plot_rect.topLeft())

        painter.setPen(text_pen)
        tick_font = QtGui.QFont("Segoe UI", 9)
        painter.setFont(tick_font)
        max_label = format_axis_value(maximum, self.tick_precision)
        min_label = format_axis_value(minimum, self.tick_precision)
        painter.drawText(
            QtCore.QRectF(0, plot_rect.top() - 10, left_margin - 8, 18),
            QtCore.Qt.AlignRight | QtCore.Qt.AlignTop,
            max_label,
        )
        painter.drawText(
            QtCore.QRectF(0, plot_rect.bottom() - 9, left_margin - 8, 18),
            QtCore.Qt.AlignRight | QtCore.Qt.AlignBottom,
            min_label,
        )
        painter.drawText(
            QtCore.QRectF(plot_rect.left() - 6, 0, 46, 18),
            QtCore.Qt.AlignLeft | QtCore.Qt.AlignTop,
            self.y_label,
        )
        painter.drawText(
            QtCore.QRectF(plot_rect.right() - 10, plot_rect.bottom() + 4, 24, 16),
            QtCore.Qt.AlignRight | QtCore.Qt.AlignVCenter,
            self.x_label,
        )

        if len(valid_points) < 2:
            return

        numeric_values = [float(value) for _, value in valid_points]
        span = maximum - minimum
        if span < 1e-6:
            span = 1.0

        last_index = max(1, len(self.values) - 1)
        path = QtGui.QPainterPath()
        first_index, first_value = valid_points[0]
        first_x = plot_rect.left() + plot_rect.width() * first_index / last_index
        first_y = plot_rect.bottom() - plot_rect.height() * ((float(first_value) - minimum) / span)
        path.moveTo(first_x, first_y)

        for index, value in valid_points[1:]:
            x = plot_rect.left() + plot_rect.width() * index / last_index
            y = plot_rect.bottom() - plot_rect.height() * ((float(value) - minimum) / span)
            path.lineTo(x, y)

        painter.setPen(QtGui.QPen(self.color, 2.2))
        painter.drawPath(path)

        final_x, final_y = path.currentPosition().x(), path.currentPosition().y()
        painter.setBrush(self.color)
        painter.setPen(QtCore.Qt.NoPen)
        painter.drawEllipse(QtCore.QPointF(final_x, final_y), 3.5, 3.5)


class TrendCard(QtWidgets.QFrame):
    def __init__(
        self,
        title: str,
        color: str,
        *,
        y_label: str,
        suffix: str = "",
        fixed_min: float | None = None,
        fixed_max: float | None = None,
        precision: int = 0,
        plot_tick_precision: int = 0,
        parent: QtWidgets.QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.setObjectName("TrendCard")
        self.precision = precision
        self.suffix = suffix
        range_min, range_max = compute_plot_range([], fixed_min=fixed_min, fixed_max=fixed_max)
        self.axis_caption = (
            f"{y_label} · {format_axis_value(range_min, plot_tick_precision)}–{format_axis_value(range_max, plot_tick_precision)} · x:t"
        )
        layout = QtWidgets.QVBoxLayout(self)
        layout.setContentsMargins(16, 14, 16, 14)
        layout.setSpacing(6)

        heading_row = QtWidgets.QHBoxLayout()
        heading_row.setSpacing(10)
        self.title_label = QtWidgets.QLabel(title)
        self.title_label.setObjectName("TrendTitle")
        self.value_label = QtWidgets.QLabel("—")
        self.value_label.setObjectName("TrendValue")
        heading_row.addWidget(self.title_label)
        heading_row.addStretch(1)
        heading_row.addWidget(self.value_label)

        self.axis_label = QtWidgets.QLabel(self.axis_caption)
        self.axis_label.setObjectName("TrendAxisLabel")

        self.plot = TrendPlot(
            color,
            y_label=y_label,
            fixed_min=fixed_min,
            fixed_max=fixed_max,
            tick_precision=plot_tick_precision,
        )
        layout.addLayout(heading_row)
        layout.addWidget(self.axis_label)
        layout.addWidget(self.plot)

    def set_series(self, values: list[int | float | None]) -> None:
        latest_value = latest_non_none(values)
        if latest_value is None:
            self.value_label.setText("—")
        elif isinstance(latest_value, float):
            self.value_label.setText(f"{latest_value:.{self.precision}f}{self.suffix}")
        else:
            self.value_label.setText(f"{latest_value}{self.suffix}")
        self.plot.set_values(values)


class InsightsWindow(QtWidgets.QWidget):
    def __init__(self) -> None:
        super().__init__(None)
        self.setWindowTitle("TargetPointer Insights")
        self._build_ui()
        self._apply_styles()

    def _build_ui(self) -> None:
        root_layout = QtWidgets.QVBoxLayout(self)
        root_layout.setContentsMargins(24, 22, 24, 22)
        root_layout.setSpacing(18)

        header = QtWidgets.QFrame()
        header.setObjectName("InsightsHeader")
        header_layout = QtWidgets.QHBoxLayout(header)
        header_layout.setContentsMargins(20, 16, 20, 16)
        header_layout.setSpacing(16)

        icon_label = QtWidgets.QLabel(INSIGHTS_ICON_TEXT)
        icon_label.setObjectName("InsightsIcon")
        icon_label.setAlignment(QtCore.Qt.AlignCenter)

        title_stack = QtWidgets.QVBoxLayout()
        title_stack.setSpacing(2)
        title_label = QtWidgets.QLabel("Insights")
        title_label.setObjectName("InsightsTitle")
        subtitle_label = QtWidgets.QLabel("A richer view of what the system is doing right now.")
        subtitle_label.setObjectName("InsightsSubtitle")
        title_stack.addWidget(title_label)
        title_stack.addWidget(subtitle_label)

        self.state_badge = StatusBadge("Idle")
        self.device_badge = StatusBadge("Device Offline")

        header_layout.addWidget(icon_label)
        header_layout.addLayout(title_stack)
        header_layout.addStretch(1)
        header_layout.addWidget(self.device_badge)
        header_layout.addWidget(self.state_badge)

        summary_grid = QtWidgets.QGridLayout()
        summary_grid.setHorizontalSpacing(12)
        summary_grid.setVerticalSpacing(12)
        self.summary_tiles = {
            "target_angle": StatTile("Target Angle", featured=True),
            "output_angle": StatTile("Servo Output"),
            "tracking": StatTile("Tracking"),
            "detections": StatTile("Detections"),
            "missed_frames": StatTile("Missed Frames"),
            "match": StatTile("Match"),
        }
        ordered_summary = [
            self.summary_tiles["target_angle"],
            self.summary_tiles["output_angle"],
            self.summary_tiles["tracking"],
            self.summary_tiles["detections"],
            self.summary_tiles["missed_frames"],
            self.summary_tiles["match"],
        ]
        for index, tile in enumerate(ordered_summary):
            row = index // 3
            column = index % 3
            summary_grid.addWidget(tile, row, column)

        trends_grid = QtWidgets.QGridLayout()
        trends_grid.setHorizontalSpacing(14)
        trends_grid.setVerticalSpacing(14)
        self.trend_cards = {
            "output_angle": TrendCard(
                "Servo Output Trend",
                "#0071e3",
                y_label="deg",
                suffix="°",
                fixed_min=20,
                fixed_max=160,
            ),
            "target_angle": TrendCard(
                "Target Angle Trend",
                "#0ea5e9",
                y_label="deg",
                suffix="°",
                fixed_min=20,
                fixed_max=160,
            ),
            "detection_count": TrendCard(
                "Detections Trend",
                "#38bdf8",
                y_label="count",
                fixed_min=0,
                fixed_max=6,
            ),
            "match_score": TrendCard(
                "Match Quality Trend",
                "#1d4ed8",
                y_label="score",
                fixed_min=0.0,
                fixed_max=1.0,
                precision=2,
                plot_tick_precision=2,
            ),
        }
        trends_grid.addWidget(self.trend_cards["output_angle"], 0, 0)
        trends_grid.addWidget(self.trend_cards["target_angle"], 0, 1)
        trends_grid.addWidget(self.trend_cards["detection_count"], 1, 0)
        trends_grid.addWidget(self.trend_cards["match_score"], 1, 1)

        self.empty_state = QtWidgets.QLabel(
            "Open a camera and lock a target to populate the insights dashboard."
        )
        self.empty_state.setObjectName("InsightsEmptyState")
        self.empty_state.setAlignment(QtCore.Qt.AlignCenter)

        root_layout.addWidget(header)
        root_layout.addLayout(summary_grid)
        root_layout.addLayout(trends_grid, stretch=1)
        root_layout.addWidget(self.empty_state)

    def _apply_styles(self) -> None:
        self.setStyleSheet(
            """
            QWidget {
                background: transparent;
                color: #0f2746;
                font-family: "Segoe UI", "Microsoft YaHei UI", sans-serif;
                font-size: 14px;
            }
            QLabel {
                background: transparent;
                border: none;
            }
            QFrame#InsightsHeader,
            QFrame#TrendCard,
            QFrame#StatTile {
                background: rgba(255, 255, 255, 188);
                border: none;
                border-radius: 24px;
            }
            QFrame#TrendCard {
                border-radius: 24px;
            }
            QLabel#InsightsIcon {
                background: rgba(219, 237, 255, 230);
                border: none;
                border-radius: 24px;
                color: #0756a6;
                font-size: 38px;
                font-weight: 800;
                min-width: 66px;
                min-height: 66px;
            }
            QLabel#InsightsTitle {
                font-size: 24px;
                font-weight: 700;
                color: #0756a6;
            }
            QLabel#InsightsSubtitle {
                font-size: 12px;
                color: #5e84a9;
            }
            QLabel#TrendTitle {
                font-size: 13px;
                font-weight: 700;
                color: #123a63;
            }
            QLabel#TrendAxisLabel {
                color: #6b96bd;
                font-size: 11px;
                font-weight: 500;
                padding-bottom: 2px;
            }
            QLabel#TrendValue {
                font-size: 18px;
                font-weight: 700;
                color: #0f2746;
            }
            QLabel#InsightsEmptyState {
                font-size: 13px;
                color: #5e84a9;
                padding: 20px 0 2px 0;
            }
            QFrame#StatTile[featured="true"] {
                background: rgba(219, 237, 255, 220);
                border: none;
            }
            QFrame#StatTile[featured="true"] QLabel#TileValue {
                color: #0756a6;
            }
            QLabel[ tone="soft" ] {
                background: rgba(239, 248, 255, 210);
                color: #28608f;
                border: none;
                border-radius: 999px;
                padding: 7px 13px;
                font-size: 12px;
                font-weight: 700;
            }
            QLabel[ tone="good" ] {
                background: rgba(219, 237, 255, 230);
                color: #0756a6;
                border: none;
                border-radius: 999px;
                padding: 7px 13px;
                font-size: 12px;
                font-weight: 700;
            }
            QLabel[ tone="warm" ] {
                background: #eef7ff;
                color: #1d5f99;
                border: none;
                border-radius: 999px;
                padding: 7px 13px;
                font-size: 12px;
                font-weight: 700;
            }
            QLabel[ tone="danger" ] {
                background: #fff1f2;
                color: #b42318;
                border: none;
                border-radius: 999px;
                padding: 7px 13px;
                font-size: 12px;
                font-weight: 700;
            }
            QLabel#TileLabel {
                color: #6b96bd;
                font-size: 11px;
                font-weight: 700;
                letter-spacing: 0;
            }
            QLabel#TileValue {
                color: #0f2746;
                font-size: 20px;
                font-weight: 700;
            }
            """
        )

    def update_from_snapshot(
        self,
        snapshot: RuntimeSnapshot | None,
        history: list[HistoryPoint],
    ) -> None:
        if snapshot is None:
            self.summary_tiles["target_angle"].set_value("—")
            self.summary_tiles["output_angle"].set_value("—")
            self.summary_tiles["tracking"].set_value("idle")
            self.summary_tiles["detections"].set_value("0")
            self.summary_tiles["missed_frames"].set_value("0")
            self.summary_tiles["match"].set_value("none")
            self.state_badge.set_badge("Idle", "soft")
            self.device_badge.set_badge("Device Offline", "danger")
            self.empty_state.show()
            for card in self.trend_cards.values():
                card.set_series([])
            return

        tracking_label = TRACKING_LABELS.get(snapshot.tracking_state, snapshot.tracking_state.title())
        tracking_tone = TRACKING_TONES.get(snapshot.tracking_state, "soft")
        self.summary_tiles["target_angle"].set_value(format_metric(snapshot.target_angle))
        self.summary_tiles["output_angle"].set_value(format_metric(snapshot.output_angle))
        self.summary_tiles["tracking"].set_value(tracking_label)
        self.summary_tiles["detections"].set_value(str(len(snapshot.pending_detections)))
        self.summary_tiles["missed_frames"].set_value(str(snapshot.missed_frames))
        self.summary_tiles["match"].set_value(
            "none" if snapshot.last_match is None else f"{snapshot.last_match.score:.2f}"
        )
        self.state_badge.set_badge(tracking_label, tracking_tone)
        if snapshot.serial_connected and snapshot.serial_port:
            self.device_badge.set_badge(snapshot.serial_port, "good")
        else:
            self.device_badge.set_badge("Device Offline", "danger")

        has_history = bool(history)
        self.empty_state.setVisible(not has_history)
        self.trend_cards["output_angle"].set_series([point.output_angle for point in history])
        self.trend_cards["target_angle"].set_series([point.target_angle for point in history])
        self.trend_cards["detection_count"].set_series([point.detection_count for point in history])
        self.trend_cards["match_score"].set_series([point.match_score for point in history])


class PointerDesktopWindow(QtWidgets.QMainWindow):
    def __init__(
        self,
        runtime: PointerRuntime,
        initial_camera: str | None,
        initial_port: str | None,
        *,
        auto_connect_serial: bool = False,
    ) -> None:
        super().__init__()
        self.runtime = runtime
        self.initial_camera = initial_camera
        self.initial_port = initial_port or "COM4"
        self.auto_connect_serial = auto_connect_serial
        self.latest_snapshot: RuntimeSnapshot | None = None
        self.history_points: deque[HistoryPoint] = deque(maxlen=240)
        self.report_thread: QtCore.QThread | None = None
        self.report_worker: ReportWorker | None = None
        self.camera_scan_process: QtCore.QProcess | None = None
        self.camera_scan_timer: QtCore.QTimer | None = None
        self.camera_scan_previous_data: str | None = None
        self.voice_client_thread: DesktopLiveKitClientThread | None = None
        self.voice_session_config: RealtimeVoiceSessionConfig | None = None
        self.voice_api_client = RealtimeChatApiClient(base_url=realtime_chat_api_base_url())
        self.voice_client_state = "idle"
        self.voice_client_detail = "启动后会检查 RealtimeAIChat 后端，并自动创建语音会话。"
        self.voice_agent_present = False
        self.voice_live_captions: dict[str, str] = {"user": "", "assistant": ""}
        self.voice_live_caption_timestamps: dict[str, str] = {"user": "", "assistant": ""}
        self.voice_activity_until: dict[str, float] = {"user": 0.0, "assistant": 0.0}
        self.voice_transcript_lines: list[VoiceTranscriptLine] = []
        self.voice_event_lines: list[str] = []
        self.voice_room: str | None = None
        self.voice_user_identity: str | None = None
        self.voice_image_history: deque[VoiceImageSnapshot] = deque(maxlen=6)
        self.voice_image_sample_interval_s = 5.0
        self.voice_last_image_sample_at = 0.0
        self.voice_caption_clear_timers: dict[str, QtCore.QTimer] = {}
        self.voice_activity_decay_timers: dict[str, QtCore.QTimer] = {}
        self.repo_root = Path(__file__).resolve().parents[2]

        self.setWindowTitle(WINDOW_TITLE)
        self.resize(1560, 980)
        self.setWindowIcon(build_arrow_icon(WINDOW_ICON_TEXT))
        self.activity_dialog = ActivityDialog(self)
        self.insights_window = InsightsWindow()
        self.report_window = ReportWindow()
        self.voice_window = VoiceAssistantWindow()
        self.nav_buttons: dict[str, SidebarNavButton] = {}

        self.refresh_timer = QtCore.QTimer(self)
        self.refresh_timer.setInterval(33)
        self.refresh_timer.timeout.connect(self._tick)

        self._build_ui()
        self._apply_styles()
        self._wire_events()
        for role in ("user", "assistant"):
            timer = QtCore.QTimer(self)
            timer.setSingleShot(True)
            timer.timeout.connect(lambda active_role=role: self._clear_voice_live_caption(active_role))
            self.voice_caption_clear_timers[role] = timer
            decay_timer = QtCore.QTimer(self)
            decay_timer.setSingleShot(True)
            decay_timer.timeout.connect(self._refresh_voice_ui)
            self.voice_activity_decay_timers[role] = decay_timer
        self.voice_window.set_session_config(None)
        self.voice_window.set_connection_detail(self.voice_client_detail)
        self.voice_window.set_event_lines(self.voice_event_lines)
        self._refresh_serial_ports()

        if self.initial_camera:
            self.camera_input.clear()
            self.camera_input.addItem(f"Camera {self.initial_camera}", self.initial_camera)
            self.camera_input.setCurrentIndex(0)

        QtCore.QTimer.singleShot(0, self._apply_startup_intent)

    def closeEvent(self, event: QtGui.QCloseEvent) -> None:
        self.refresh_timer.stop()
        self._cancel_camera_scan(log_event=False)
        self._stop_voice_assistant(log_event=False)
        self.runtime.close_camera()
        self.runtime.disconnect_serial()
        super().closeEvent(event)

    def _build_ui(self) -> None:
        central = QtWidgets.QWidget(self)
        central.setObjectName("WorkbenchRoot")
        root_layout = QtWidgets.QHBoxLayout(central)
        root_layout.setContentsMargins(18, 18, 18, 18)
        root_layout.setSpacing(18)

        self.page_stack = QtWidgets.QStackedWidget()
        self.page_stack.setObjectName("PageStack")

        self.live_page = self._build_live_page()
        self.pages = {
            "live": self.live_page,
            "voice": self.voice_window,
            "report": self.report_window,
            "insights": self.insights_window,
            "activity": self.activity_dialog,
        }
        for page in self.pages.values():
            self.page_stack.addWidget(page)

        root_layout.addWidget(self._build_sidebar())
        root_layout.addWidget(self.page_stack, stretch=1)

        self.setCentralWidget(central)
        self.toast_message = ToastMessage(central)
        self._switch_page("live")

    def _build_live_page(self) -> QtWidgets.QWidget:
        page = QtWidgets.QWidget()
        page.setObjectName("LiveControlPage")
        root_layout = QtWidgets.QVBoxLayout(page)
        root_layout.setContentsMargins(0, 0, 0, 0)
        root_layout.setSpacing(18)

        root_layout.addWidget(self._build_header())

        body_layout = QtWidgets.QHBoxLayout()
        body_layout.setSpacing(18)
        body_layout.addWidget(self._build_stage_shell(), stretch=11)
        body_layout.addWidget(self._build_control_shell(), stretch=4)
        root_layout.addLayout(body_layout, stretch=1)
        return page

    def _build_sidebar(self) -> QtWidgets.QFrame:
        sidebar = QtWidgets.QFrame()
        sidebar.setObjectName("Sidebar")
        sidebar.setFixedWidth(248)

        layout = QtWidgets.QVBoxLayout(sidebar)
        layout.setContentsMargins(14, 16, 14, 16)
        layout.setSpacing(14)

        brand = QtWidgets.QLabel("TargetPointer")
        brand.setObjectName("SidebarBrand")
        tagline = QtWidgets.QLabel("Operator Workbench")
        tagline.setObjectName("SidebarTagline")

        layout.addWidget(brand)
        layout.addWidget(tagline)

        nav_scroll = QtWidgets.QScrollArea()
        nav_scroll.setObjectName("SidebarScroll")
        nav_scroll.setWidgetResizable(True)
        nav_scroll.setHorizontalScrollBarPolicy(QtCore.Qt.ScrollBarAlwaysOff)
        nav_scroll.setFrameShape(QtWidgets.QFrame.NoFrame)

        nav_content = QtWidgets.QWidget()
        nav_layout = QtWidgets.QVBoxLayout(nav_content)
        nav_layout.setContentsMargins(0, 4, 4, 4)
        nav_layout.setSpacing(10)

        nav_items = [
            ("live", "Live Control", "Camera, target, device"),
            ("voice", "Voice Assistant", "Speech and vision dialog"),
            ("report", "Target Report", "PDF report workspace"),
            ("insights", "Data Analysis", "Tracking trends"),
            ("activity", "Activity", "Runtime event log"),
        ]
        for key, title, subtitle in nav_items:
            button = SidebarNavButton(title, subtitle)
            button.clicked.connect(lambda _checked=False, page_key=key: self._switch_page(page_key))
            self.nav_buttons[key] = button
            nav_layout.addWidget(button)

        nav_layout.addStretch(1)
        nav_scroll.setWidget(nav_content)
        layout.addWidget(nav_scroll, stretch=1)
        return sidebar

    def _switch_page(self, key: str) -> None:
        page = self.pages.get(key)
        if page is None:
            return
        self.page_stack.setCurrentWidget(page)
        for page_key, button in self.nav_buttons.items():
            button.set_active(page_key == key)
        if key == "voice":
            self.voice_window.set_running(
                self._voice_assistant_running()
                or self.voice_session_config is not None
                or self.voice_client_state in {"checking_backend", "creating_session", "reconnecting", "closing"}
            )
            self.voice_window.set_connection_detail(self.voice_client_detail)
            self.voice_window.set_session_config(self.voice_session_config)
            self.voice_window.set_event_lines(self.voice_event_lines)

    def _build_header(self) -> QtWidgets.QFrame:
        header = QtWidgets.QFrame()
        header.setObjectName("TopBar")
        layout = QtWidgets.QHBoxLayout(header)
        layout.setContentsMargins(22, 14, 22, 14)
        layout.setSpacing(16)

        brand_stack = QtWidgets.QVBoxLayout()
        brand_stack.setSpacing(0)
        brand_label = QtWidgets.QLabel("TargetPointer")
        brand_label.setObjectName("BrandLabel")
        subtitle_label = QtWidgets.QLabel("Fixed-camera person pointing console")
        subtitle_label.setObjectName("SubtitleLabel")

        brand_stack.addWidget(brand_label)
        brand_stack.addWidget(subtitle_label)

        self.device_badge = StatusBadge("Device Offline")
        self.activity_button = self._make_button("Activity", "HeaderPillButton")
        self.header_status = StatusBadge("Selecting")

        layout.addLayout(brand_stack)
        layout.addStretch(1)
        layout.addWidget(self.device_badge)
        layout.addWidget(self.activity_button)
        layout.addWidget(self.header_status)
        return header

    def _build_stage_shell(self) -> QtWidgets.QFrame:
        shell = QtWidgets.QFrame()
        shell.setObjectName("StageShell")
        layout = QtWidgets.QVBoxLayout(shell)
        layout.setContentsMargins(22, 22, 22, 18)
        layout.setSpacing(14)

        title_row = QtWidgets.QHBoxLayout()
        title_row.setSpacing(10)

        stage_title = QtWidgets.QLabel("Live Stage")
        stage_title.setObjectName("SectionTitle")
        self.stage_caption = QtWidgets.QLabel("Click a detected person, or drag a box to initialize target.")
        self.stage_caption.setObjectName("SubtitleLabel")
        self.camera_hint = StatusBadge("Camera Closed")

        title_group = QtWidgets.QVBoxLayout()
        title_group.setSpacing(2)
        title_group.addWidget(stage_title)
        title_group.addWidget(self.stage_caption)

        title_row.addLayout(title_group)
        title_row.addStretch(1)
        title_row.addWidget(self.camera_hint, alignment=QtCore.Qt.AlignTop)

        self.video_widget = VideoFrameWidget()
        self.video_widget.setObjectName("VideoSurface")

        self.hint_label = QtWidgets.QLabel("Click a detected person, or drag a box when you want manual initialization.")
        self.hint_label.setWordWrap(True)
        self.hint_label.setObjectName("HintLabel")

        layout.addLayout(title_row)
        layout.addWidget(self.video_widget, stretch=1)
        layout.addWidget(self.hint_label)
        return shell

    def _build_control_shell(self) -> QtWidgets.QFrame:
        shell = QtWidgets.QFrame()
        shell.setObjectName("ControlShell")
        shell.setMinimumWidth(380)
        shell.setMaximumWidth(420)
        layout = QtWidgets.QVBoxLayout(shell)
        layout.setContentsMargins(24, 24, 24, 24)
        layout.setSpacing(20)

        intro_title = QtWidgets.QLabel("Operator Panel")
        intro_title.setObjectName("PanelTitle")
        layout.addWidget(intro_title)

        self.guidance_card = GuidanceCard()
        layout.addWidget(self.guidance_card)
        layout.addWidget(self._build_connection_section())
        layout.addWidget(self._build_action_section())
        layout.addWidget(self._build_status_section())
        layout.addWidget(self._build_meta_strip())
        layout.addStretch(1)
        return shell

    def _build_connection_section(self) -> QtWidgets.QFrame:
        section = self._make_section("Connections")
        layout = section.layout()

        self.camera_input = PolishedComboBox()
        self.camera_input.setEditable(False)
        configure_combo_box(self.camera_input, min_popup_width=320)
        self.camera_input.addItem("Scan cameras first", None)

        self.serial_combo = PolishedComboBox()
        configure_combo_box(self.serial_combo, min_popup_width=260)

        self.backend_value = QtWidgets.QLabel(self.runtime.camera_backend_preference.upper())
        self.backend_value.setObjectName("InlineValue")
        self.model_label = QtWidgets.QLabel(format_model_display_name(self.runtime.model_name))
        self.model_label.setObjectName("InlineValue")

        form = QtWidgets.QGridLayout()
        form.setHorizontalSpacing(12)
        form.setVerticalSpacing(12)
        form.addWidget(self._field_label("Camera Source"), 0, 0)
        form.addWidget(self.camera_input, 0, 1)
        form.addWidget(self._field_label("Serial Port"), 1, 0)
        form.addWidget(self.serial_combo, 1, 1)
        form.setColumnStretch(1, 1)
        layout.addLayout(form)

        button_row_one = QtWidgets.QHBoxLayout()
        button_row_one.setSpacing(10)
        self.scan_cameras_button = self._make_button("Scan", "GhostButton")
        self.open_camera_button = self._make_button("Open Camera", "PrimaryButton")
        self.close_camera_button = self._make_button("Close", "GhostButton")
        button_row_one.addWidget(self.scan_cameras_button)
        button_row_one.addWidget(self.open_camera_button)
        button_row_one.addWidget(self.close_camera_button)

        button_row_two = QtWidgets.QHBoxLayout()
        button_row_two.setSpacing(10)
        self.refresh_serial_button = self._make_button("Refresh", "GhostButton")
        self.connect_serial_button = self._make_button("Connect", "PrimaryButton")
        self.disconnect_serial_button = self._make_button("Disconnect", "GhostButton")
        button_row_two.addWidget(self.refresh_serial_button)
        button_row_two.addWidget(self.connect_serial_button)
        button_row_two.addWidget(self.disconnect_serial_button)

        layout.addLayout(button_row_one)
        layout.addLayout(button_row_two)
        return section

    def _build_action_section(self) -> QtWidgets.QFrame:
        section = self._make_section("Actions")
        layout = section.layout()

        button_row = QtWidgets.QHBoxLayout()
        button_row.setSpacing(10)
        self.redetect_button = self._make_button("Re-detect", "PrimaryButton")
        self.center_button = self._make_button("Center", "GhostButton")
        self.stop_button = self._make_button("Stop", "WarnButton")
        button_row.addWidget(self.redetect_button)
        button_row.addWidget(self.center_button)
        button_row.addWidget(self.stop_button)
        layout.addLayout(button_row)

        return section

    def _build_status_section(self) -> QtWidgets.QFrame:
        section = self._make_section("Snapshot")
        layout = section.layout()

        grid = QtWidgets.QGridLayout()
        grid.setHorizontalSpacing(12)
        grid.setVerticalSpacing(12)

        self.tiles = {
            "tracking_state": StatTile("Tracking"),
            "angle": StatTile("Servo Angle", featured=True),
            "detections": StatTile("Detections"),
            "missed_frames": StatTile("Missed Frames"),
            "last_match": StatTile("Match"),
        }

        ordered_tiles = [
            self.tiles["angle"],
            self.tiles["tracking_state"],
            self.tiles["detections"],
            self.tiles["missed_frames"],
            self.tiles["last_match"],
        ]
        for index, tile in enumerate(ordered_tiles):
            row = index // 2
            column = index % 2
            if index == 0:
                grid.addWidget(tile, 0, 0, 1, 2)
            else:
                adjusted_index = index - 1
                row = adjusted_index // 2 + 1
                column = adjusted_index % 2
                grid.addWidget(tile, row, column)

        layout.addLayout(grid)
        return section

    def _build_meta_strip(self) -> QtWidgets.QFrame:
        section = QtWidgets.QFrame()
        section.setObjectName("MetaStrip")
        layout = QtWidgets.QVBoxLayout(section)
        layout.setContentsMargins(14, 12, 14, 12)
        layout.setSpacing(6)

        first_row = QtWidgets.QHBoxLayout()
        first_row.setSpacing(8)
        first_row.addWidget(self._field_label("Model"))
        first_row.addWidget(self.model_label, stretch=1)

        second_row = QtWidgets.QHBoxLayout()
        second_row.setSpacing(8)
        second_row.addWidget(self._field_label("Backend"))
        second_row.addWidget(self.backend_value, stretch=1)

        layout.addLayout(first_row)
        layout.addLayout(second_row)
        return section

    def _make_section(self, title: str) -> QtWidgets.QFrame:
        section = QtWidgets.QFrame()
        section.setObjectName("SectionShell")
        layout = QtWidgets.QVBoxLayout(section)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(10)

        heading = QtWidgets.QLabel(title)
        heading.setObjectName("SectionTitle")
        layout.addWidget(heading)
        return section

    def _make_button(self, text: str, object_name: str) -> QtWidgets.QPushButton:
        button = QtWidgets.QPushButton(text)
        button.setObjectName(object_name)
        button.setAutoDefault(False)
        button.setDefault(False)
        return button

    def _field_label(self, text: str) -> QtWidgets.QLabel:
        label = QtWidgets.QLabel(text)
        label.setObjectName("FieldLabel")
        return label

    def _wire_events(self) -> None:
        self.camera_input.currentIndexChanged.connect(lambda _index: self._refresh_interaction_state())
        self.serial_combo.currentIndexChanged.connect(lambda _index: self._refresh_interaction_state())
        self.scan_cameras_button.clicked.connect(self._refresh_cameras)
        self.open_camera_button.clicked.connect(self._open_camera)
        self.close_camera_button.clicked.connect(self._close_camera)
        self.refresh_serial_button.clicked.connect(self._refresh_serial_ports)
        self.connect_serial_button.clicked.connect(self._connect_serial)
        self.disconnect_serial_button.clicked.connect(self._disconnect_serial)
        self.redetect_button.clicked.connect(self._request_redetect)
        self.center_button.clicked.connect(self._center_device)
        self.stop_button.clicked.connect(self._stop_device)
        self.video_widget.point_selected.connect(self._select_target)
        self.video_widget.bbox_selected.connect(self._select_target_bbox)
        self.activity_button.clicked.connect(self._toggle_activity)
        self.report_window.generate_requested.connect(self._generate_report)
        self.voice_window.start_requested.connect(self._start_voice_assistant)
        self.voice_window.stop_requested.connect(self._stop_voice_assistant)
        self.voice_window.mute_changed.connect(self._set_voice_user_muted)

    def _apply_styles(self) -> None:
        self.setStyleSheet(
            """
            QWidget {
                background: transparent;
                color: #0f2746;
                font-family: "Segoe UI", "Microsoft YaHei UI", sans-serif;
                font-size: 14px;
            }
            QWidget#WorkbenchRoot {
                background: qlineargradient(x1:0, y1:0, x2:1, y2:1,
                    stop:0 #eaf5ff, stop:0.52 #f7fbff, stop:1 #d9ecff);
            }
            QLabel {
                background: transparent;
                border: none;
            }
            QFrame#Sidebar {
                background: rgba(255, 255, 255, 178);
                border: none;
                border-radius: 24px;
            }
            QLabel#SidebarBrand {
                color: #0756a6;
                font-size: 24px;
                font-weight: 750;
            }
            QLabel#SidebarTagline {
                color: #4b7fac;
                font-size: 12px;
                padding-bottom: 8px;
            }
            QScrollArea#SidebarScroll {
                background: transparent;
                border: none;
                border-radius: 18px;
            }
            QPushButton#SidebarNavButton {
                text-align: left;
                background: rgba(255, 255, 255, 110);
                color: #17466f;
                border: none;
                border-radius: 22px;
                padding: 0;
            }
            QPushButton#SidebarNavButton:hover {
                background: rgba(239, 248, 255, 210);
                border: none;
            }
            QPushButton#SidebarNavButton[active="true"],
            QPushButton#SidebarNavButton:checked {
                background: rgba(219, 237, 255, 235);
                border: none;
            }
            QLabel#SidebarNavTitle {
                color: #0b5cad;
                font-size: 14px;
                font-weight: 750;
            }
            QLabel#SidebarNavSubtitle {
                color: #5e84a9;
                font-size: 11px;
            }
            QStackedWidget#PageStack {
                background: transparent;
                border: none;
                border-radius: 28px;
            }
            QFrame#TopBar,
            QFrame#StageShell,
            QFrame#ControlShell,
            QFrame#MetaStrip,
            QFrame#GuidanceCard,
            QFrame#DialogShell {
                background: rgba(255, 255, 255, 188);
                border: none;
                border-radius: 24px;
            }
            QFrame#SectionShell {
                background: transparent;
                border: none;
            }
            QFrame#StatTile {
                background: rgba(247, 251, 255, 180);
                border: none;
                border-radius: 20px;
            }
            QFrame#StatTile[featured="true"] {
                background: rgba(219, 237, 255, 220);
                border: none;
                border-radius: 22px;
            }
            QFrame#GuidanceCard[tone="soft"] {
                background: rgba(239, 248, 255, 185);
                border: none;
            }
            QFrame#GuidanceCard[tone="good"] {
                background: rgba(219, 237, 255, 225);
                border: none;
            }
            QFrame#GuidanceCard[tone="warm"] {
                background: rgba(232, 242, 255, 215);
                border: none;
            }
            QLabel#BrandLabel {
                font-size: 24px;
                font-weight: 750;
                color: #0756a6;
            }
            QLabel#SubtitleLabel {
                color: #5e84a9;
                font-size: 12px;
                padding-top: 2px;
            }
            QLabel#PanelTitle {
                font-size: 19px;
                font-weight: 700;
                color: #0f2746;
            }
            QLabel#SectionTitle,
            QLabel#DrawerTitle {
                font-size: 15px;
                font-weight: 700;
                color: #123a63;
            }
            QLabel#SubtleLabel {
                color: #5e84a9;
                font-size: 12px;
            }
            QLabel#BodyCopy,
            QLabel#HintLabel {
                color: #5e84a9;
                font-size: 12px;
            }
            QLabel#FieldLabel {
                color: #6b96bd;
                font-size: 11px;
                font-weight: 700;
                letter-spacing: 0;
                padding-bottom: 2px;
            }
            QLabel#InlineValue {
                color: #173b61;
                font-size: 13px;
                padding: 2px 0;
            }
            QLabel#TileLabel {
                color: #6b96bd;
                font-size: 11px;
                font-weight: 700;
                letter-spacing: 0;
            }
            QLabel#TileValue {
                color: #0f2746;
                font-size: 17px;
                font-weight: 700;
            }
            QLabel#GuidanceValue {
                color: #0f2746;
                font-size: 15px;
                font-weight: 700;
            }
            QFrame#StatTile[featured="true"] QLabel#TileValue {
                color: #0756a6;
                font-size: 24px;
                font-weight: 700;
            }
            QLabel#VideoSurface {
                background: #08243d;
                color: #eaf5ff;
                border: none;
                border-radius: 28px;
                padding: 10px;
            }
            QLabel[ tone="soft" ] {
                background: rgba(239, 248, 255, 210);
                color: #28608f;
                border: none;
                border-radius: 999px;
                padding: 7px 13px;
                font-size: 12px;
                font-weight: 700;
            }
            QLabel[ tone="good" ] {
                background: rgba(219, 237, 255, 230);
                color: #0756a6;
                border: none;
                border-radius: 999px;
                padding: 7px 13px;
                font-size: 12px;
                font-weight: 700;
            }
            QLabel[ tone="warm" ] {
                background: #eef7ff;
                color: #1d5f99;
                border: none;
                border-radius: 999px;
                padding: 7px 13px;
                font-size: 12px;
                font-weight: 700;
            }
            QLabel[ tone="danger" ] {
                background: #fff1f2;
                color: #b42318;
                border: none;
                border-radius: 999px;
                padding: 7px 13px;
                font-size: 12px;
                font-weight: 700;
            }
            QComboBox,
            QLineEdit,
            QPlainTextEdit {
                background: rgba(255, 255, 255, 205);
                border: none;
                border-radius: 18px;
                min-height: 22px;
                padding: 8px 34px 8px 11px;
                color: #0f2746;
                selection-background-color: #bfdbfe;
            }
            QPlainTextEdit#ActivityLog {
                background: rgba(8, 36, 61, 215);
                color: #eaf5ff;
                border: none;
                border-radius: 24px;
                font-family: "Cascadia Mono", "Consolas", monospace;
                font-size: 12px;
                padding: 14px;
                selection-background-color: #1d73d4;
            }
            QComboBox::drop-down {
                border: none;
                width: 32px;
                background: transparent;
            }
            QComboBox::down-arrow {
                image: none;
                width: 0px;
                height: 0px;
            }
            QListView#ComboPopupList,
            QComboBox QAbstractItemView {
                background: #ffffff;
                border: none;
                border-radius: 18px;
                padding: 8px;
                color: #173b61;
                outline: 0;
                selection-background-color: #e0f2fe;
                selection-color: #0756a6;
            }
            QListView#ComboPopupList::item {
                min-height: 34px;
                padding: 8px 10px;
                border-radius: 14px;
            }
            QListView#ComboPopupList::item:hover {
                background: #eff8ff;
                color: #0756a6;
            }
            QListView#ComboPopupList::item:selected {
                background: #dbeafe;
                color: #0756a6;
            }
            QRubberBand#SelectionBand {
                background: rgba(37, 99, 235, 0.14);
                border: none;
                border-radius: 18px;
            }
            QPushButton {
                border-radius: 18px;
                padding: 11px 16px;
                font-weight: 700;
                border: none;
                background: rgba(255, 255, 255, 210);
                color: #0f2746;
            }
            QPushButton#PrimaryButton {
                background: #1d73d4;
                color: #ffffff;
                border: none;
            }
            QPushButton#PrimaryButton:hover {
                background: #2563eb;
                border: none;
            }
            QPushButton#PrimaryButton:pressed {
                background: #0756a6;
                border: none;
            }
            QPushButton#GhostButton {
                background: rgba(255, 255, 255, 190);
                color: #17466f;
                border: none;
            }
            QPushButton#GhostButton:hover {
                background: rgba(239, 248, 255, 230);
            }
            QPushButton#GhostButton:pressed {
                background: #dbeafe;
            }
            QPushButton#WarnButton {
                background: #fff7ed;
                color: #c23b32;
                border: none;
            }
            QPushButton#WarnButton:hover {
                background: #ffedd5;
            }
            QPushButton#WarnButton:pressed {
                background: #fed7aa;
            }
            QPushButton#HeaderPillButton {
                background: rgba(255, 255, 255, 190);
                color: #17466f;
                border: none;
                border-radius: 999px;
                padding: 8px 16px;
                min-height: 34px;
            }
            QPushButton#HeaderPillButton:hover {
                background: #eff8ff;
            }
            QPushButton#HeaderPillButton:pressed {
                background: #dbeafe;
            }
            QPushButton:disabled {
                background: rgba(219, 234, 254, 130);
                color: #8aaac8;
                border: none;
            }
            QFrame#ToastMessage {
                background: #fff1f2;
                border: none;
                border-radius: 20px;
            }
            QLabel#ToastLabel {
                color: #b42318;
                font-size: 13px;
                font-weight: 700;
            }
            QPushButton:focus,
            QComboBox:focus,
            QLineEdit:focus,
            QPlainTextEdit:focus {
                border: none;
                outline: none;
            }
            QScrollBar:vertical {
                background: rgba(219, 237, 255, 115);
                width: 12px;
                margin: 8px 2px 8px 2px;
                border-radius: 6px;
            }
            QScrollBar::handle:vertical {
                background: #8ec5ff;
                border-radius: 6px;
                min-height: 36px;
            }
            QScrollBar::handle:vertical:hover {
                background: #60a5fa;
            }
            QScrollBar::add-line:vertical,
            QScrollBar::sub-line:vertical,
            QScrollBar::add-page:vertical,
            QScrollBar::sub-page:vertical {
                background: transparent;
                border: none;
                border-radius: 6px;
            }
            QScrollBar:horizontal {
                background: rgba(219, 237, 255, 115);
                height: 12px;
                margin: 2px 8px 2px 8px;
                border-radius: 6px;
            }
            QScrollBar::handle:horizontal {
                background: #8ec5ff;
                border-radius: 6px;
                min-width: 36px;
            }
            QScrollBar::add-line:horizontal,
            QScrollBar::sub-line:horizontal,
            QScrollBar::add-page:horizontal,
            QScrollBar::sub-page:horizontal {
                background: transparent;
                border: none;
                border-radius: 6px;
            }
            """
        )

    def _apply_startup_intent(self) -> None:
        if self.initial_port:
            index = self.serial_combo.findText(self.initial_port)
            if index >= 0:
                self.serial_combo.setCurrentIndex(index)
            else:
                self.serial_combo.addItem(self.initial_port)
                self.serial_combo.setCurrentText(self.initial_port)
        if self.auto_connect_serial and self.initial_port:
            self._connect_serial()

        if self.initial_camera:
            self._open_camera()

        self._refresh_interaction_state()

    def _refresh_serial_ports(self) -> None:
        current = self.serial_combo.currentText()
        ports = list_serial_ports()
        for fallback_port in (current, self.initial_port, "COM4"):
            if fallback_port and fallback_port not in ports:
                ports.append(fallback_port)
        self.serial_combo.clear()
        self.serial_combo.addItems(ports)
        if current and current in ports:
            self.serial_combo.setCurrentText(current)
        self._log(f"Serial ports refreshed: {', '.join(ports) if ports else 'none'}")
        self._refresh_interaction_state()

    def _refresh_cameras(self) -> None:
        if self.camera_scan_process is not None:
            self._log("Camera scan already running")
            return

        self.camera_scan_previous_data = self.camera_input.currentData()
        self.camera_input.clear()
        self.camera_input.addItem("Scanning cameras...", None)
        self.scan_cameras_button.setEnabled(False)
        self._log("Camera scan started")
        self._refresh_interaction_state()

        process = QtCore.QProcess(self)
        process.setProgram(sys.executable)
        process.setWorkingDirectory(str(self.repo_root))
        environment = QtCore.QProcessEnvironment.systemEnvironment()
        existing_pythonpath = environment.value("PYTHONPATH", "")
        pythonpath_items = [str(self.repo_root)]
        if existing_pythonpath:
            pythonpath_items.append(existing_pythonpath)
        environment.insert("PYTHONPATH", os.pathsep.join(pythonpath_items))
        process.setProcessEnvironment(environment)
        process.setArguments(
            [
                "-m",
                "targetpointer.runtime.camera_scan",
                "--backend",
                self.runtime.camera_backend_preference,
                "--max-index",
                "4",
            ]
        )
        process.finished.connect(self._handle_camera_scan_finished)
        process.errorOccurred.connect(self._handle_camera_scan_error)

        timer = QtCore.QTimer(self)
        timer.setSingleShot(True)
        timer.setInterval(8000)
        timer.timeout.connect(self._handle_camera_scan_timeout)

        self.camera_scan_process = process
        self.camera_scan_timer = timer
        process.start()
        timer.start()

    def _handle_camera_scan_finished(self, exit_code: int, exit_status: QtCore.QProcess.ExitStatus) -> None:
        process = self.camera_scan_process
        if process is None:
            return

        stdout = bytes(process.readAllStandardOutput()).decode("utf-8", errors="replace").strip()
        stderr = bytes(process.readAllStandardError()).decode("utf-8", errors="replace").strip()
        self._clear_camera_scan_process()

        if exit_status != QtCore.QProcess.NormalExit or exit_code != 0:
            self._set_camera_scan_results([])
            self._log(f"Camera scan failed: {stderr or 'scanner exited unexpectedly'}")
            self._show_error_toast("Camera scan failed. Try another backend.")
            return

        try:
            raw_items = json.loads(stdout or "[]")
            cameras = [
                (int(item["index"]), str(item["backend"]), bool(item.get("read_ok", True)))
                for item in raw_items
                if isinstance(item, dict)
            ]
        except (TypeError, ValueError, KeyError) as exc:
            self._set_camera_scan_results([])
            self._log(f"Camera scan returned invalid data: {exc}")
            self._show_error_toast("Camera scan failed. Try another backend.")
            return

        self._set_camera_scan_results(cameras)
        self._log("Camera scan completed")

    def _handle_camera_scan_error(self, _error: QtCore.QProcess.ProcessError) -> None:
        self._clear_camera_scan_process()
        self._set_camera_scan_results([])
        self._log("Camera scan failed to start")
        self._show_error_toast("Camera scan failed. Check Python environment.")

    def _handle_camera_scan_timeout(self) -> None:
        self._cancel_camera_scan(log_event=True)
        self._set_camera_scan_results([])
        self._show_error_toast("Camera scan timed out. Try another backend.")

    def _cancel_camera_scan(self, *, log_event: bool) -> None:
        process = self.camera_scan_process
        if process is None:
            return
        if log_event:
            self._log("Camera scan timed out")
        process.kill()
        process.waitForFinished(1000)
        self._clear_camera_scan_process()

    def _clear_camera_scan_process(self) -> None:
        if self.camera_scan_timer is not None:
            self.camera_scan_timer.stop()
            self.camera_scan_timer.deleteLater()
            self.camera_scan_timer = None
        if self.camera_scan_process is not None:
            self.camera_scan_process.deleteLater()
            self.camera_scan_process = None
        self.scan_cameras_button.setEnabled(True)

    def _set_camera_scan_results(self, cameras: list[tuple[int, str, bool]]) -> None:
        current_data = self.camera_scan_previous_data
        self.camera_scan_previous_data = None
        self.camera_input.clear()
        if not cameras:
            self.camera_input.addItem("No cameras found", None)
            self._refresh_interaction_state()
            return
        for index, backend_name, read_ok in cameras:
            status = "ready" if read_ok else "open_no_frame"
            label = f"Camera {index} · {backend_name.upper()} · {status}"
            self.camera_input.addItem(label, str(index))
        if current_data is not None:
            matched_index = self.camera_input.findData(current_data)
            if matched_index >= 0:
                self.camera_input.setCurrentIndex(matched_index)
        self._refresh_interaction_state()

    def _selected_camera_source(self) -> str:
        data = self.camera_input.currentData()
        if data is None:
            return ""
        return str(data)

    def _open_camera(self) -> None:
        source = self._selected_camera_source()
        if not source:
            self._log("Camera source is empty")
            self._show_error_toast("Select a camera source before opening.")
            return

        try:
            _source, backend_name = self.runtime.open_camera(source)
        except Exception as exc:
            self._log(f"Open camera failed: {exc}")
            self._show_error_toast("Open camera failed. Check camera selection or backend.")
            self._refresh_interaction_state()
            return

        self._clear_history()
        self.voice_image_history.clear()
        self.voice_last_image_sample_at = 0.0
        self.backend_value.setText(backend_name)
        self.camera_hint.set_badge(f"Camera {source}", "soft")
        self.refresh_timer.start()
        self._push_insights_snapshot(None)
        self._log(f"Camera opened: source={source} backend={backend_name}")
        self._refresh_interaction_state()

    def _close_camera(self) -> None:
        self.refresh_timer.stop()
        self._stop_voice_assistant()
        self.runtime.close_camera()
        self.latest_snapshot = None
        self._clear_history()
        self.voice_image_history.clear()
        self.voice_last_image_sample_at = 0.0
        self.video_widget.clear_preview("Open a camera to start the stage preview")
        self.camera_hint.set_badge("Camera Closed", "soft")
        self._update_status_labels(None, force_idle=False)
        self._push_insights_snapshot(None)
        self._log("Camera closed")
        self._refresh_interaction_state()

    def _connect_serial(self) -> None:
        port = self.serial_combo.currentText().strip()
        if not port:
            self._log("Serial port is empty")
            self._show_error_toast("Select a serial port before connecting.")
            return

        try:
            responses = self.runtime.connect_serial(port)
        except (serial.SerialException, Exception) as exc:
            self._log(f"Connect device failed: {exc}")
            self.device_badge.set_badge("Device Offline", "danger")
            self._show_error_toast("Connect device failed. Check COM port and firmware power.")
            self._refresh_interaction_state()
            return

        self._log(f"Serial connected: {port}")
        for line in responses:
            self._log(line)
        self._update_status_labels()

    def _disconnect_serial(self) -> None:
        self.runtime.disconnect_serial()
        self._log("Serial disconnected")
        self._update_status_labels()

    def _request_redetect(self) -> None:
        self.runtime.request_redetect()
        self._log("Manual re-detect requested")

    def _center_device(self) -> None:
        try:
            responses = self.runtime.center_device()
        except Exception as exc:
            self._log(f"Center failed: {exc}")
            self._show_error_toast("Center failed. Device did not respond.")
            self._refresh_interaction_state()
            return
        self._log("Center command sent")
        for line in responses:
            self._log(line)
        self._update_status_labels()

    def _stop_device(self) -> None:
        try:
            responses = self.runtime.stop_device()
        except Exception as exc:
            self._log(f"Stop failed: {exc}")
            self._show_error_toast("Stop failed. Device did not respond.")
            self._refresh_interaction_state()
            return
        self._log("Stop command sent")
        for line in responses:
            self._log(line)
        self._update_status_labels()

    def _generate_report(self) -> None:
        if self.report_thread is not None:
            self._log("Report generation is already running")
            return
        if not snapshot_has_report_target(self.latest_snapshot):
            self._show_error_toast("Select and track a person before generating a report.")
            return
        if not os.getenv("OPENAI_API_KEY"):
            self._show_error_toast("OPENAI_API_KEY is required to generate a report.")
            self._log("Generate report blocked: OPENAI_API_KEY is missing")
            return

        assert self.latest_snapshot is not None
        snapshot = self.latest_snapshot
        self.report_window.set_generating(True)
        self._log("Report generation started")

        self.report_thread = QtCore.QThread(self)
        self.report_worker = ReportWorker(snapshot)
        self.report_worker.moveToThread(self.report_thread)
        self.report_thread.started.connect(self.report_worker.run)
        self.report_worker.finished.connect(self._on_report_finished)
        self.report_worker.failed.connect(self._on_report_failed)
        self.report_worker.finished.connect(self.report_thread.quit)
        self.report_worker.failed.connect(self.report_thread.quit)
        self.report_thread.finished.connect(self._cleanup_report_worker)
        self.report_thread.start()

    def _on_report_finished(self, report: GeneratedReport) -> None:
        self._log(f"Report generated: {report.path}")
        self.report_window.update_report(report)
        self.show_report_window()
        self.report_window.set_generating(False)

    def _on_report_failed(self, message: str) -> None:
        self._log(f"Report generation failed: {message}")
        self._show_error_toast("Report generation failed. Check OPENAI_API_KEY and network access.")
        self.report_window.set_generating(False)

    def _cleanup_report_worker(self) -> None:
        if self.report_worker is not None:
            self.report_worker.deleteLater()
        if self.report_thread is not None:
            self.report_thread.deleteLater()
        self.report_worker = None
        self.report_thread = None
        self._refresh_interaction_state()

    def _build_voice_extra_vars(self) -> dict[str, object]:
        snapshot = self.latest_snapshot
        payload: dict[str, object] = {
            "client": "TargetPointer",
            "serial_connected": bool(self.runtime.serial_client is not None),
            "serial_port": str(self.runtime.serial_port or ""),
            "camera_open": snapshot is not None,
            "image_mode": "recent_snapshots",
        }
        if snapshot is None:
            return payload
        payload["tracking_state"] = snapshot.tracking_state
        payload["target_angle"] = snapshot.target_angle
        payload["output_angle"] = snapshot.output_angle
        payload["detections"] = len(snapshot.pending_detections)
        payload["missed_frames"] = snapshot.missed_frames
        if snapshot.tracked_bbox is not None:
            payload["tracked_bbox"] = list(snapshot.tracked_bbox)
        return payload

    def _capture_voice_image_snapshot(self, snapshot: RuntimeSnapshot, *, force: bool = False) -> None:
        now = time.monotonic()
        if not force and now - self.voice_last_image_sample_at < self.voice_image_sample_interval_s:
            return

        frame = render_preview_frame(snapshot)
        label = datetime.now().strftime("%H:%M:%S")
        cv2.rectangle(frame, (18, 18), (252, 74), (7, 34, 58), -1)
        cv2.putText(
            frame,
            f"Captured {label}",
            (32, 54),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.75,
            (234, 245, 255),
            2,
            cv2.LINE_AA,
        )
        self.voice_image_history.append(
            VoiceImageSnapshot(
                captured_at=now,
                captured_at_label=label,
                frame=frame,
            )
        )
        self.voice_last_image_sample_at = now

    def _build_voice_attachments(self) -> list[dict[str, object]]:
        if self.latest_snapshot is not None and not self.voice_image_history:
            self._capture_voice_image_snapshot(self.latest_snapshot, force=True)

        recent_images = list(self.voice_image_history)[-VOICE_IMAGE_ATTACHMENT_LIMIT:]
        attachments: list[dict[str, object]] = []
        total = len(recent_images)
        for index, item in enumerate(recent_images, start=1):
            frame = item.frame.copy()
            cv2.rectangle(frame, (frame.shape[1] - 280, 18), (frame.shape[1] - 18, 74), (29, 115, 212), -1)
            cv2.putText(
                frame,
                f"Seq {index}/{total}  {item.captured_at_label}",
                (frame.shape[1] - 260, 54),
                cv2.FONT_HERSHEY_SIMPLEX,
                0.65,
                (255, 255, 255),
                2,
                cv2.LINE_AA,
            )
            jpeg_bytes = encode_jpeg(frame, max_side=960, quality=72)
            attachments.append(
                {
                    "kind": "image",
                    "source": "user_upload",
                    "title": f"时序画面 {index}/{total} · {item.captured_at_label}",
                    "mime_type": "image/jpeg",
                    "uri": jpeg_data_url(jpeg_bytes),
                    "text": (
                        f"第 {index} 张时序画面，捕获时间 {item.captured_at_label}。"
                        "图片中矩形框表示当前锁定人物。"
                    ),
                    "metadata": {
                        "sequence_index": index,
                        "sequence_total": total,
                        "captured_at": item.captured_at_label,
                        "source": "targetpointer_recent_snapshot",
                    },
                }
            )
        return attachments

    def _append_voice_event(self, message: str) -> None:
        timestamp = format_voice_timestamp()
        self.voice_event_lines.append(f"{timestamp} {message}")
        self.voice_event_lines = self.voice_event_lines[-120:]
        self.voice_window.set_event_lines(self.voice_event_lines)

    def _append_voice_transcript(self, role: str, text: str) -> None:
        cleaned = text.strip()
        if not cleaned:
            return
        line = VoiceTranscriptLine(timestamp=format_voice_timestamp(), role=role, text=cleaned)
        if self.voice_transcript_lines and self.voice_transcript_lines[-1] == line:
            return
        self.voice_transcript_lines.append(line)
        self.voice_transcript_lines = self.voice_transcript_lines[-80:]

    def _mark_voice_activity(self, role: str, duration_s: float) -> None:
        if role not in self.voice_activity_until:
            return
        duration_ms = max(0, int(duration_s * 1000))
        self.voice_activity_until[role] = max(self.voice_activity_until[role], time.monotonic() + max(0.0, duration_s))
        decay_timer = self.voice_activity_decay_timers.get(role)
        if decay_timer is not None:
            decay_timer.stop()
            decay_timer.start(duration_ms + 60)

    def _clear_voice_live_caption(self, role: str) -> None:
        if role not in self.voice_live_captions:
            return
        if self.voice_live_captions.get(role):
            self.voice_live_captions[role] = ""
            self.voice_live_caption_timestamps[role] = ""
            self._refresh_voice_ui()

    def _attach_voice_client(self, session_config: RealtimeVoiceSessionConfig) -> None:
        self.voice_client_thread = DesktopLiveKitClientThread(
            session_config,
            start_muted=self.voice_window.is_user_muted(),
            parent=self,
        )
        self.voice_client_thread.state_changed.connect(self._on_voice_client_state_changed)
        self.voice_client_thread.system_message.connect(self._on_voice_client_system_message)
        self.voice_client_thread.live_caption_changed.connect(self._on_voice_live_caption_changed)
        self.voice_client_thread.agent_availability_changed.connect(self._on_voice_agent_availability_changed)
        self.voice_client_thread.failure_reported.connect(self._on_voice_client_failure)
        self.voice_client_thread.reconnect_requested.connect(self._on_voice_client_reconnect_requested)
        self.voice_client_thread.finished.connect(self._on_voice_client_thread_finished)
        self.voice_client_thread.start()

    def _teardown_voice_client(self) -> None:
        thread = self.voice_client_thread
        self.voice_client_thread = None
        if thread is None:
            return
        thread.stop_client()
        thread.wait(5000)
        thread.deleteLater()

    def _start_voice_assistant(self, config: RealtimeVoiceConfig | None = None) -> None:
        if self._voice_assistant_running() or self.voice_client_state in {
            "checking_backend",
            "creating_session",
            "reconnecting",
        }:
            return

        config = config or self.voice_window.config()
        self.voice_window.set_running(True)
        self.voice_transcript_lines = []
        self.voice_event_lines = []
        self.voice_live_captions = {"user": "", "assistant": ""}
        self.voice_live_caption_timestamps = {"user": "", "assistant": ""}
        self.voice_activity_until = {"user": 0.0, "assistant": 0.0}
        self.voice_agent_present = False
        self.voice_session_config = None
        self.voice_room = None
        self.voice_user_identity = None
        self.voice_client_state = "checking_backend"
        self.voice_client_detail = "正在检查 RealtimeAIChat 后端。"
        self._refresh_voice_ui()

        try:
            self.voice_api_client.health_check()
            self._append_voice_event("RealtimeAIChat 后端健康检查通过。")
            capabilities = self.voice_api_client.get_capabilities()
            allow_client_ai_mode = bool(
                ((capabilities.get("defaults") or {}) if isinstance(capabilities, dict) else {}).get(
                    "allow_client_ai_mode",
                    False,
                )
            )
            self.voice_client_state = "creating_session"
            self.voice_client_detail = "正在创建语音会话。"
            self._refresh_voice_ui()
            attachments = self._build_voice_attachments()
            payload = build_realtime_voice_session_payload(
                config,
                user_identity=DEFAULT_TARGETPOINTER_USER_IDENTITY,
                extra_vars=self._build_voice_extra_vars(),
                attachments=attachments,
                allow_client_ai_mode=allow_client_ai_mode,
            )
            session_config = self.voice_api_client.create_session(payload)
        except RealtimeChatApiError as exc:
            self.voice_client_state = "error"
            self.voice_client_detail = f"语音后端不可用：{exc}"
            self._append_voice_event(str(exc))
            self.voice_window.set_running(False)
            self._show_error_toast("RealtimeAIChat 后端不可用。")
            self._refresh_voice_ui()
            self._refresh_interaction_state()
            return

        self.voice_session_config = session_config
        self.voice_room = session_config.room
        self.voice_user_identity = session_config.user_identity
        self.voice_client_state = "connecting"
        self.voice_client_detail = "桌面端正在接入 LiveKit 语音会话。"
        self._append_voice_event(
            f"会话已创建：session={session_config.session_id} room={session_config.room}"
        )
        if attachments:
            self._append_voice_event(
                f"已发送最近 {len(attachments)} 张时序图片到 RealtimeAIChat（最多 {VOICE_IMAGE_ATTACHMENT_LIMIT} 张）。"
            )
        else:
            self._append_voice_event("当前没有可用图片，已按纯音频模式创建会话。")
        self._log(
            f"Voice session created: session={session_config.session_id} room={session_config.room} "
            f"tts_voice={config.tts_voice} image_attachments={len(attachments)}"
        )
        self._attach_voice_client(session_config)
        self._refresh_voice_ui()
        self._refresh_interaction_state()

    def _stop_voice_assistant(self, *, log_event: bool = True) -> None:
        session_id = self.voice_session_config.session_id if self.voice_session_config is not None else None
        self.voice_client_state = "closing" if session_id else "idle"
        self.voice_client_detail = "正在关闭语音会话。" if session_id else "语音会话已停止。"
        self._teardown_voice_client()
        if session_id is not None:
            try:
                self.voice_api_client.close_session(session_id)
                self._append_voice_event(f"语音会话已关闭：{session_id}")
            except RealtimeChatApiError as exc:
                self._append_voice_event(f"关闭远端会话失败：{exc}")
        self.voice_room = None
        self.voice_user_identity = None
        self.voice_session_config = None
        self.voice_client_state = "idle"
        self.voice_client_detail = "启动后会检查 RealtimeAIChat 后端，并自动创建语音会话。"
        self.voice_agent_present = False
        self.voice_live_captions = {"user": "", "assistant": ""}
        self.voice_live_caption_timestamps = {"user": "", "assistant": ""}
        self.voice_activity_until = {"user": 0.0, "assistant": 0.0}
        for timer in self.voice_caption_clear_timers.values():
            timer.stop()
        for timer in self.voice_activity_decay_timers.values():
            timer.stop()
        self.voice_window.set_running(False)
        self._refresh_voice_ui()
        if log_event:
            self._log("Voice assistant stopped")
        self._refresh_interaction_state()

    def _voice_assistant_running(self) -> bool:
        return self.voice_client_thread is not None and self.voice_client_thread.isRunning()

    def _set_voice_user_muted(self, muted: bool) -> None:
        if self.voice_client_thread is not None:
            self.voice_client_thread.set_microphone_muted(muted)
        self._append_voice_event("用户麦克风已静音" if muted else "用户麦克风已开启")
        self._log("Voice user microphone muted" if muted else "Voice user microphone unmuted")
        self._refresh_voice_ui()

    def _on_voice_client_state_changed(self, state: str, detail: str) -> None:
        self.voice_client_state = state
        self.voice_client_detail = detail
        self.voice_window.set_connection_detail(detail)
        self._refresh_voice_ui()

    def _on_voice_client_system_message(self, message: str) -> None:
        self._append_voice_event(message)
        self._log(message)
        self._refresh_voice_ui()

    def _on_voice_live_caption_changed(self, role: str, text: str, is_final: bool) -> None:
        cleaned = text.strip()
        if role not in self.voice_live_captions or not cleaned:
            return
        clear_timer = self.voice_caption_clear_timers.get(role)
        if clear_timer is not None:
            clear_timer.stop()
        self.voice_live_captions[role] = cleaned
        self.voice_live_caption_timestamps[role] = format_voice_timestamp()
        if is_final:
            self._append_voice_transcript(role, cleaned)
            self._mark_voice_activity(role, 0.9)
            if clear_timer is not None:
                clear_timer.start(700)
        else:
            self._mark_voice_activity(role, 1.4)
        self._refresh_voice_ui()

    def _on_voice_agent_availability_changed(self, available: bool) -> None:
        self.voice_agent_present = available
        self._refresh_voice_ui()

    def _on_voice_client_failure(self, message: str) -> None:
        self._append_voice_event(f"LiveKit client 失败：{message}")
        self._log(f"Voice client failed: {message}")
        self._show_error_toast("LiveKit client failed. Check audio device and room settings.")

    def _on_voice_client_reconnect_requested(self) -> None:
        session = self.voice_session_config
        if session is None:
            return
        self._append_voice_event("连接中断，正在向后端申请新的重连 token。")
        self._teardown_voice_client()
        try:
            session_config = self.voice_api_client.reconnect_session(session.session_id)
        except RealtimeChatApiError as exc:
            self.voice_client_state = "error"
            self.voice_client_detail = f"会话恢复失败：{exc}"
            self.voice_session_config = None
            self.voice_room = None
            self.voice_user_identity = None
            self.voice_agent_present = False
            self.voice_window.set_running(False)
            self._append_voice_event(str(exc))
            self._show_error_toast("语音会话重连失败。")
            self._refresh_voice_ui()
            self._refresh_interaction_state()
            return
        self.voice_session_config = session_config
        self.voice_room = session_config.room
        self.voice_user_identity = session_config.user_identity
        self.voice_client_state = "connecting"
        self.voice_client_detail = "已拿到新的 token，正在重新接入语音会话。"
        self._append_voice_event(f"重连 token 已下发：session={session_config.session_id}")
        self._attach_voice_client(session_config)
        self.voice_window.set_running(True)
        self._refresh_voice_ui()
        self._refresh_interaction_state()

    def _on_voice_client_thread_finished(self) -> None:
        sender = self.sender()
        if self.voice_client_thread is not None and sender is self.voice_client_thread:
            self.voice_client_thread.deleteLater()
            self.voice_client_thread = None
        if not self._voice_assistant_running() and self.voice_client_state in {"idle", "error"}:
            self.voice_window.set_running(False)
            self._refresh_interaction_state()

    def _refresh_voice_ui(self) -> None:
        user_muted = self.voice_window.is_user_muted()
        user_state = "idle"
        agent_state = "idle"
        now = time.monotonic()
        if self.voice_client_state in {"checking_backend", "creating_session", "connecting", "closing"}:
            agent_state = "initializing"
        elif self.voice_client_state in {"waiting_agent", "ready", "reconnecting"}:
            user_state = "idle" if user_muted else "listening"
            agent_state = "listening" if self.voice_agent_present else "initializing"

        if self.voice_live_captions.get("user") or now < self.voice_activity_until.get("user", 0.0):
            user_state = "idle" if user_muted else "speaking"
        if self.voice_live_captions.get("assistant") or now < self.voice_activity_until.get("assistant", 0.0):
            agent_state = "speaking"

        self.voice_window.set_runtime_state(
            user_state=user_state,
            agent_state=agent_state,
            user_muted=user_muted,
        )
        self.voice_window.set_connection_detail(self.voice_client_detail)
        self.voice_window.set_session_config(self.voice_session_config)
        self.voice_window.set_event_lines(self.voice_event_lines)
        self.voice_window.set_transcript_lines(
            self.voice_transcript_lines,
            live_captions=self.voice_live_captions,
            live_caption_timestamps=self.voice_live_caption_timestamps,
        )

    def _select_target(self, point_x: int, point_y: int) -> None:
        if self.runtime.select_target_at(point_x, point_y):
            self._log(f"Target selected at ({point_x}, {point_y})")
        else:
            self._log("Click did not hit a detected person")

    def _select_target_bbox(self, x: int, y: int, width: int, height: int) -> None:
        self.runtime.select_target_bbox((x, y, width, height))
        self._log(f"Manual bbox selected: ({x}, {y}, {width}, {height})")

    def _toggle_activity(self) -> None:
        self._switch_page("activity")

    def _toggle_insights(self) -> None:
        self.show_insights_window()

    def _toggle_voice_window(self) -> None:
        self.show_voice_window()

    def show_insights_window(self) -> None:
        self._switch_page("insights")

    def show_report_window(self) -> None:
        self._switch_page("report")

    def show_voice_window(self) -> None:
        self._switch_page("voice")

    def _position_aux_window(self, window: QtWidgets.QWidget, horizontal_gap: int = 22) -> None:
        main_geometry = self.frameGeometry()
        screen = self.screen() or QtGui.QGuiApplication.primaryScreen()
        if screen is None:
            return

        available = screen.availableGeometry()
        target_x = main_geometry.right() + horizontal_gap
        target_y = main_geometry.top() + 36

        if target_x + window.width() > available.right():
            target_x = max(available.left() + 20, main_geometry.left() - window.width() - horizontal_gap)

        if target_y + window.height() > available.bottom():
            target_y = max(available.top() + 20, available.bottom() - window.height() - 20)

        window.move(target_x, target_y)

    def _clear_history(self) -> None:
        self.history_points.clear()

    def _push_insights_snapshot(self, snapshot: RuntimeSnapshot | None) -> None:
        self.insights_window.update_from_snapshot(snapshot, list(self.history_points))

    def _tick(self) -> None:
        try:
            snapshot = self.runtime.process_next_frame()
        except Exception as exc:
            self.refresh_timer.stop()
            self.runtime.close_camera()
            self._log(f"Runtime stopped: {exc}")
            self._show_error_toast("Runtime stopped. Check camera or serial connection.")
            self._refresh_interaction_state()
            return

        self.latest_snapshot = snapshot
        self.history_points.append(build_history_point(snapshot, time.monotonic()))
        self.video_widget.set_frame(render_preview_frame(snapshot))
        self._capture_voice_image_snapshot(snapshot)
        self._update_status_labels(snapshot)
        self._push_insights_snapshot(snapshot)

    def _update_status_labels(
        self,
        snapshot: RuntimeSnapshot | None = None,
        *,
        force_idle: bool = True,
    ) -> None:
        active_snapshot = snapshot or self.latest_snapshot
        if active_snapshot is None:
            self.tiles["tracking_state"].set_value("idle")
            self.tiles["angle"].set_value("—")
            self.tiles["detections"].set_value("0")
            self.tiles["missed_frames"].set_value("0")
            self.tiles["last_match"].set_value("none")
            self.header_status.set_badge("Idle", "soft")
            if not force_idle and self.runtime.serial_client is not None and self.runtime.serial_port:
                self.device_badge.set_badge(self.runtime.serial_port, "good")
            else:
                self.device_badge.set_badge("Device Offline", "danger")
            self.camera_hint.set_badge("Camera Closed", "soft")
            self._refresh_interaction_state()
            return

        tracking_state = active_snapshot.tracking_state
        tracking_label = TRACKING_LABELS.get(tracking_state, tracking_state.title())
        tracking_tone = TRACKING_TONES.get(tracking_state, "soft")

        self.tiles["tracking_state"].set_value(tracking_label)
        self.tiles["angle"].set_value("—" if active_snapshot.output_angle is None else str(active_snapshot.output_angle))
        self.tiles["detections"].set_value(str(len(active_snapshot.pending_detections)))
        self.tiles["missed_frames"].set_value(str(active_snapshot.missed_frames))
        if active_snapshot.last_match is None:
            self.tiles["last_match"].set_value("none")
        else:
            self.tiles["last_match"].set_value(f"{active_snapshot.last_match.score:.2f}")

        self.header_status.set_badge(tracking_label, tracking_tone)
        if active_snapshot.serial_connected and active_snapshot.serial_port:
            self.device_badge.set_badge(active_snapshot.serial_port, "good")
        else:
            self.device_badge.set_badge("Device Offline", "danger")

        if active_snapshot.camera_source:
            self.camera_hint.set_badge(f"Camera {active_snapshot.camera_source}", "soft")
        else:
            self.camera_hint.set_badge("Camera Closed", "soft")

        self._refresh_interaction_state(active_snapshot)

    def _log(self, message: str) -> None:
        self.activity_dialog.log_output.appendPlainText(message)
        self.activity_dialog.log_output.moveCursor(QtGui.QTextCursor.End)

    def resizeEvent(self, event: QtGui.QResizeEvent) -> None:
        super().resizeEvent(event)
        if hasattr(self, "toast_message"):
            self.toast_message._reposition()

    def _show_error_toast(self, message: str) -> None:
        self.toast_message.show_toast(message, duration_ms=5000)

    def _current_tracking_state(self, snapshot: RuntimeSnapshot | None = None) -> str | None:
        active_snapshot = snapshot or self.latest_snapshot
        if active_snapshot is not None:
            return active_snapshot.tracking_state
        return self.runtime.state.tracking_state

    def _refresh_interaction_state(self, snapshot: RuntimeSnapshot | None = None) -> None:
        camera_open = self.runtime.camera_source is not None
        serial_connected = self.runtime.serial_client is not None
        voice_running = self._voice_assistant_running() or self.voice_session_config is not None or self.voice_client_state in {
            "checking_backend",
            "creating_session",
            "reconnecting",
            "closing",
        }
        active_snapshot = snapshot or self.latest_snapshot
        button_state = build_desktop_button_state(
            has_camera_source=bool(self._selected_camera_source()),
            camera_open=camera_open,
            has_serial_port=bool(self.serial_combo.currentText().strip()),
            serial_connected=serial_connected,
            has_report_target=snapshot_has_report_target(active_snapshot),
            voice_running=voice_running,
        )
        self.open_camera_button.setEnabled(button_state.open_camera_enabled)
        self.close_camera_button.setEnabled(button_state.close_camera_enabled)
        self.connect_serial_button.setEnabled(button_state.connect_enabled)
        self.disconnect_serial_button.setEnabled(button_state.disconnect_enabled)
        self.redetect_button.setEnabled(button_state.redetect_enabled)
        self.center_button.setEnabled(button_state.center_enabled)
        self.stop_button.setEnabled(button_state.stop_enabled)
        self.report_window.set_generate_enabled(button_state.report_enabled and self.report_thread is None)
        self.voice_window.set_running(voice_running)

        flow_state = build_desktop_flow_state(
            camera_open=camera_open,
            serial_connected=serial_connected,
            tracking_state=self._current_tracking_state(snapshot),
        )
        self.guidance_card.set_guidance(flow_state.text, flow_state.tone)


def build_runtime_from_args(args: argparse.Namespace) -> PointerRuntime:
    detector = YOLO(args.model)
    return PointerRuntime(
        detector=detector,
        model_name=args.model,
        camera_backend=args.camera_backend,
        on_loss=args.on_loss,
    )


def main() -> int:
    load_dotenv()

    parser = argparse.ArgumentParser(description="PySide6 desktop console for TargetPointer.")
    parser.add_argument("--model", default="yolov8n.pt", help="YOLO model path or model name.")
    parser.add_argument("--port", help="Serial port to select on startup, for example COM4.")
    parser.add_argument("--auto-connect", action="store_true", help="Connect the selected serial port at startup.")
    parser.add_argument("--camera", help="Camera source to auto-open, for example 2.")
    parser.add_argument(
        "--camera-backend",
        choices=("auto", "any", "dshow", "msmf"),
        default="auto",
        help="Camera backend preference. On Windows, use the same values as pointer_vision_app.py.",
    )
    parser.add_argument("--on-loss", choices=("stop", "center"), default="stop", help="Loss strategy for the runtime.")
    args = parser.parse_args()

    app = QtWidgets.QApplication(sys.argv)
    app.setApplicationName("TargetPointer")
    app.setStyle("Fusion")
    app.setWindowIcon(build_arrow_icon(WINDOW_ICON_TEXT))
    runtime = build_runtime_from_args(args)
    window = PointerDesktopWindow(
        runtime,
        initial_camera=args.camera,
        initial_port=args.port,
        auto_connect_serial=args.auto_connect or args.port is not None,
    )
    window.show()
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
