#!/usr/bin/env python3

from __future__ import annotations

import argparse
import sys

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

from pointer_runtime import PointerRuntime, RuntimeSnapshot, list_serial_ports


WINDOW_TITLE = "TargetPointer Console"

TRACKING_LABELS = {
    "selecting": "Selecting",
    "locked": "Locked",
    "reacquiring": "Reacquiring",
    "lost": "Lost",
}

TRACKING_TONES = {
    "selecting": "soft",
    "locked": "good",
    "reacquiring": "warm",
    "lost": "danger",
}


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


class ActivityDialog(QtWidgets.QDialog):
    def __init__(self, parent: QtWidgets.QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Activity")
        self.setModal(False)
        self.setWindowFlag(QtCore.Qt.FramelessWindowHint, True)
        self.setAttribute(QtCore.Qt.WA_TranslucentBackground)
        self.resize(460, 480)

        layout = QtWidgets.QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)

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
        close_button.clicked.connect(self.hide)

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
        self.log_output.setReadOnly(True)
        self.log_output.setMaximumBlockCount(300)
        self.log_output.setPlaceholderText("Recent activity appears here.")

        shell_layout.addLayout(top_row)
        shell_layout.addWidget(self.log_output, stretch=1)
        layout.addWidget(shell)


class PointerDesktopWindow(QtWidgets.QMainWindow):
    def __init__(self, runtime: PointerRuntime, initial_camera: str | None, initial_port: str | None) -> None:
        super().__init__()
        self.runtime = runtime
        self.initial_camera = initial_camera
        self.initial_port = initial_port
        self.latest_snapshot: RuntimeSnapshot | None = None

        self.setWindowTitle(WINDOW_TITLE)
        self.resize(1560, 980)
        self.activity_dialog = ActivityDialog(self)

        self.refresh_timer = QtCore.QTimer(self)
        self.refresh_timer.setInterval(33)
        self.refresh_timer.timeout.connect(self._tick)

        self._build_ui()
        self._apply_styles()
        self._wire_events()
        self._refresh_serial_ports()

        if self.initial_camera:
            self.camera_input.clear()
            self.camera_input.addItem(f"Camera {self.initial_camera}", self.initial_camera)
            self.camera_input.setCurrentIndex(0)

        QtCore.QTimer.singleShot(0, self._apply_startup_intent)

    def closeEvent(self, event: QtGui.QCloseEvent) -> None:
        self.refresh_timer.stop()
        self.runtime.close_camera()
        self.runtime.disconnect_serial()
        super().closeEvent(event)

    def _build_ui(self) -> None:
        central = QtWidgets.QWidget(self)
        root_layout = QtWidgets.QVBoxLayout(central)
        root_layout.setContentsMargins(28, 22, 28, 26)
        root_layout.setSpacing(18)

        root_layout.addWidget(self._build_header())

        body_layout = QtWidgets.QHBoxLayout()
        body_layout.setSpacing(22)
        body_layout.addWidget(self._build_stage_shell(), stretch=11)
        body_layout.addWidget(self._build_control_shell(), stretch=4)
        root_layout.addLayout(body_layout, stretch=1)

        self.setCentralWidget(central)

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

        layout.addWidget(self._build_connection_section())
        layout.addWidget(self._build_action_section())
        layout.addWidget(self._build_status_section())
        layout.addWidget(self._build_meta_strip())
        layout.addStretch(1)
        return shell

    def _build_connection_section(self) -> QtWidgets.QFrame:
        section = self._make_section("Connections")
        layout = section.layout()

        self.camera_input = QtWidgets.QComboBox()
        self.camera_input.setEditable(False)
        self.camera_input.setSizeAdjustPolicy(QtWidgets.QComboBox.AdjustToMinimumContentsLengthWithIcon)
        self.camera_input.addItem("Scan cameras first", None)

        self.serial_combo = QtWidgets.QComboBox()

        self.backend_value = QtWidgets.QLabel(self.runtime.camera_backend_preference.upper())
        self.backend_value.setObjectName("InlineValue")
        self.model_label = QtWidgets.QLabel(self.runtime.model_name)
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

    def _apply_styles(self) -> None:
        self.setStyleSheet(
            """
            QWidget {
                background: #f5f5f7;
                color: #1f1f21;
                font-family: "Segoe UI", "Microsoft YaHei UI", sans-serif;
                font-size: 14px;
            }
            QLabel {
                background: transparent;
                border: none;
            }
            QFrame#TopBar,
            QFrame#StageShell,
            QFrame#ControlShell,
            QFrame#MetaStrip,
            QFrame#DialogShell {
                background: #ffffff;
                border: 1px solid #e8e8ed;
                border-radius: 24px;
            }
            QFrame#SectionShell {
                background: transparent;
                border: none;
            }
            QFrame#StatTile {
                background: #f8f8fa;
                border: 1px solid #ececf1;
                border-radius: 16px;
            }
            QFrame#StatTile[featured="true"] {
                background: #eef5ff;
                border: 1px solid #d5e6ff;
                border-radius: 18px;
            }
            QLabel#BrandLabel {
                font-size: 28px;
                font-weight: 600;
                color: #111114;
            }
            QLabel#SubtitleLabel {
                color: #6e6e73;
                font-size: 12px;
                padding-top: 2px;
            }
            QLabel#PanelTitle {
                font-size: 22px;
                font-weight: 600;
                color: #16161a;
            }
            QLabel#SectionTitle,
            QLabel#DrawerTitle {
                font-size: 15px;
                font-weight: 600;
                color: #1c1c20;
            }
            QLabel#SubtleLabel {
                color: #6f6f78;
                font-size: 12px;
            }
            QLabel#BodyCopy,
            QLabel#HintLabel {
                color: #6f6f78;
                font-size: 12px;
            }
            QLabel#FieldLabel {
                color: #8f8f98;
                font-size: 11px;
                font-weight: 700;
                letter-spacing: 0.4px;
                padding-bottom: 2px;
            }
            QLabel#InlineValue {
                color: #202024;
                font-size: 13px;
                padding: 2px 0;
            }
            QLabel#TileLabel {
                color: #8d8d96;
                font-size: 11px;
                font-weight: 700;
                letter-spacing: 0.4px;
            }
            QLabel#TileValue {
                color: #18181c;
                font-size: 17px;
                font-weight: 600;
            }
            QFrame#StatTile[featured="true"] QLabel#TileValue {
                color: #005fc1;
                font-size: 24px;
                font-weight: 700;
            }
            QLabel#VideoSurface {
                background: #121214;
                color: #f1f1f4;
                border: 1px solid #1a1a1e;
                border-radius: 26px;
                padding: 10px;
            }
            QLabel[ tone="soft" ] {
                background: #f2f2f7;
                color: #4a4a52;
                border: 1px solid #e5e5ea;
                border-radius: 999px;
                padding: 7px 13px;
                font-size: 12px;
                font-weight: 700;
            }
            QLabel[ tone="good" ] {
                background: #e7f1ff;
                color: #0066cc;
                border: 1px solid #d4e5fb;
                border-radius: 999px;
                padding: 7px 13px;
                font-size: 12px;
                font-weight: 700;
            }
            QLabel[ tone="warm" ] {
                background: #fff3d8;
                color: #9a6b00;
                border: 1px solid #f3e3b7;
                border-radius: 999px;
                padding: 7px 13px;
                font-size: 12px;
                font-weight: 700;
            }
            QLabel[ tone="danger" ] {
                background: #f7e8e7;
                color: #9a4c47;
                border: 1px solid #efd4d2;
                border-radius: 999px;
                padding: 7px 13px;
                font-size: 12px;
                font-weight: 700;
            }
            QComboBox,
            QLineEdit,
            QPlainTextEdit {
                background: #fbfbfd;
                border: 1px solid #e4e4ea;
                border-radius: 14px;
                padding: 10px 12px;
                color: #1f1f22;
                selection-background-color: #d8e7ff;
            }
            QComboBox::drop-down {
                border: none;
                width: 28px;
            }
            QComboBox::down-arrow {
                image: none;
                width: 0px;
                height: 0px;
            }
            QComboBox QAbstractItemView {
                background: #ffffff;
                border: 1px solid #e5e5eb;
                border-radius: 14px;
                padding: 6px;
                selection-background-color: #eef4ff;
                selection-color: #1f1f22;
            }
            QRubberBand#SelectionBand {
                background: rgba(0, 113, 227, 0.14);
                border: 2px solid #0071e3;
                border-radius: 10px;
            }
            QPushButton {
                border-radius: 14px;
                padding: 11px 16px;
                font-weight: 600;
                border: 1px solid transparent;
                background: #ffffff;
                color: #1f1f22;
            }
            QPushButton#PrimaryButton {
                background: #0071e3;
                color: #ffffff;
                border: 1px solid #0071e3;
            }
            QPushButton#PrimaryButton:hover {
                background: #0077ed;
                border: 1px solid #0077ed;
            }
            QPushButton#PrimaryButton:pressed {
                background: #0068d1;
                border: 1px solid #0068d1;
            }
            QPushButton#GhostButton {
                background: #ffffff;
                color: #2d2d33;
                border: 1px solid #dfdfe6;
            }
            QPushButton#GhostButton:hover {
                background: #f5f5f8;
            }
            QPushButton#GhostButton:pressed {
                background: #ececf3;
            }
            QPushButton#WarnButton {
                background: #fff5f4;
                color: #c23b32;
                border: 1px solid #f0d7d4;
            }
            QPushButton#WarnButton:hover {
                background: #ffefed;
            }
            QPushButton#WarnButton:pressed {
                background: #f9e2de;
            }
            QPushButton#HeaderPillButton {
                background: #ffffff;
                color: #2d2d33;
                border: 1px solid #dfdfe6;
                border-radius: 999px;
                padding: 8px 16px;
                min-height: 34px;
            }
            QPushButton#HeaderPillButton:hover {
                background: #f5f5f8;
            }
            QPushButton#HeaderPillButton:pressed {
                background: #ececf3;
            }
            QPushButton:disabled {
                background: #e5e5ea;
                color: #a0a0a7;
                border: 1px solid #e5e5ea;
            }
            QPushButton:focus,
            QComboBox:focus,
            QLineEdit:focus,
            QPlainTextEdit:focus {
                border: 1px solid #9ec4f5;
                outline: none;
            }
            QScrollBar:vertical {
                background: transparent;
                width: 10px;
                margin: 8px 3px 8px 3px;
            }
            QScrollBar::handle:vertical {
                background: #d4d4dc;
                border-radius: 5px;
                min-height: 36px;
            }
            QScrollBar::add-line:vertical,
            QScrollBar::sub-line:vertical,
            QScrollBar::add-page:vertical,
            QScrollBar::sub-page:vertical {
                background: transparent;
                border: none;
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
            self._connect_serial()

        if self.initial_camera:
            self._open_camera()

    def _refresh_serial_ports(self) -> None:
        current = self.serial_combo.currentText()
        ports = list_serial_ports()
        self.serial_combo.clear()
        self.serial_combo.addItems(ports)
        if current and current in ports:
            self.serial_combo.setCurrentText(current)
        self._log(f"Serial ports refreshed: {', '.join(ports) if ports else 'none'}")

    def _refresh_cameras(self) -> None:
        current_data = self.camera_input.currentData()
        self.camera_input.clear()
        cameras = self.runtime.list_cameras()
        if not cameras:
            self.camera_input.addItem("No cameras found", None)
            self._log("Camera scan completed: none")
            return
        for index, backend_name, read_ok in cameras:
            status = "ready" if read_ok else "open_no_frame"
            label = f"Camera {index} · {backend_name.upper()} · {status}"
            self.camera_input.addItem(label, str(index))
        if current_data is not None:
            matched_index = self.camera_input.findData(current_data)
            if matched_index >= 0:
                self.camera_input.setCurrentIndex(matched_index)
        self._log("Camera scan completed")

    def _selected_camera_source(self) -> str:
        data = self.camera_input.currentData()
        if data is None:
            return ""
        return str(data)

    def _open_camera(self) -> None:
        source = self._selected_camera_source()
        if not source:
            self._log("Camera source is empty")
            return

        try:
            _source, backend_name = self.runtime.open_camera(source)
        except Exception as exc:
            self._log(f"Open camera failed: {exc}")
            return

        self.backend_value.setText(backend_name)
        self.camera_hint.set_badge(f"Camera {source}", "soft")
        self.refresh_timer.start()
        self._log(f"Camera opened: source={source} backend={backend_name}")

    def _close_camera(self) -> None:
        self.refresh_timer.stop()
        self.runtime.close_camera()
        self.video_widget.clear_preview("Open a camera to start the stage preview")
        self.camera_hint.set_badge("Camera Closed", "soft")
        self._update_status_labels(None, force_idle=False)
        self._log("Camera closed")

    def _connect_serial(self) -> None:
        port = self.serial_combo.currentText().strip()
        if not port:
            self._log("Serial port is empty")
            return

        try:
            responses = self.runtime.connect_serial(port)
        except (serial.SerialException, Exception) as exc:
            self._log(f"Connect device failed: {exc}")
            self.device_badge.set_badge("Device Offline", "danger")
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
            return
        self._log("Stop command sent")
        for line in responses:
            self._log(line)
        self._update_status_labels()

    def _select_target(self, point_x: int, point_y: int) -> None:
        if self.runtime.select_target_at(point_x, point_y):
            self._log(f"Target selected at ({point_x}, {point_y})")
        else:
            self._log("Click did not hit a detected person")

    def _select_target_bbox(self, x: int, y: int, width: int, height: int) -> None:
        self.runtime.select_target_bbox((x, y, width, height))
        self._log(f"Manual bbox selected: ({x}, {y}, {width}, {height})")

    def _toggle_activity(self) -> None:
        if self.activity_dialog.isVisible():
            self.activity_dialog.hide()
            return
        self.activity_dialog.move(self.frameGeometry().center() - self.activity_dialog.rect().center())
        self.activity_dialog.show()
        self.activity_dialog.raise_()
        self.activity_dialog.activateWindow()

    def _tick(self) -> None:
        try:
            snapshot = self.runtime.process_next_frame()
        except Exception as exc:
            self.refresh_timer.stop()
            self._log(f"Runtime stopped: {exc}")
            return

        self.latest_snapshot = snapshot
        self.video_widget.set_frame(render_preview_frame(snapshot))
        self._update_status_labels(snapshot)

    def _update_status_labels(
        self,
        snapshot: RuntimeSnapshot | None = None,
        *,
        force_idle: bool = True,
    ) -> None:
        active_snapshot = snapshot or self.latest_snapshot
        if active_snapshot is None:
            if not force_idle:
                self.camera_hint.set_badge("Camera Closed", "soft")
                return
            self.tiles["tracking_state"].set_value("idle")
            self.tiles["angle"].set_value("—")
            self.tiles["detections"].set_value("0")
            self.tiles["missed_frames"].set_value("0")
            self.tiles["last_match"].set_value("none")
            self.header_status.set_badge("Idle", "soft")
            self.device_badge.set_badge("Device Offline", "danger")
            self.camera_hint.set_badge("Camera Closed", "soft")
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

    def _log(self, message: str) -> None:
        self.activity_dialog.log_output.appendPlainText(message)


def build_runtime_from_args(args: argparse.Namespace) -> PointerRuntime:
    detector = YOLO(args.model)
    return PointerRuntime(
        detector=detector,
        model_name=args.model,
        camera_backend=args.camera_backend,
        on_loss=args.on_loss,
    )


def main() -> int:
    parser = argparse.ArgumentParser(description="PySide6 desktop console for TargetPointer.")
    parser.add_argument("--model", default="yolov8n.pt", help="YOLO model path or model name.")
    parser.add_argument("--port", help="Serial port to auto-connect, for example COM4.")
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
    runtime = build_runtime_from_args(args)
    window = PointerDesktopWindow(runtime, initial_camera=args.camera, initial_port=args.port)
    window.show()
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
