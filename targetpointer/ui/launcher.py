from __future__ import annotations

import argparse
import sys

from dotenv import load_dotenv

try:
    from PySide6 import QtCore, QtGui, QtWidgets
except ImportError as exc:
    raise SystemExit(
        "Missing dependency: PySide6. Run `uv sync` in the repository root, then retry with `uv run python ...`."
    ) from exc

from targetpointer.ui.desktop_app import (
    WINDOW_ICON_TEXT,
    PointerDesktopWindow,
    build_arrow_icon,
    build_runtime_from_args,
)


class LauncherTile(QtWidgets.QPushButton):
    def __init__(self, title: str, subtitle: str) -> None:
        super().__init__()
        self.setObjectName("LauncherTile")
        self.setCursor(QtCore.Qt.PointingHandCursor)
        self.setMinimumHeight(126)

        layout = QtWidgets.QVBoxLayout(self)
        layout.setContentsMargins(18, 16, 18, 16)
        layout.setSpacing(6)

        title_label = QtWidgets.QLabel(title)
        title_label.setObjectName("LauncherTileTitle")
        subtitle_label = QtWidgets.QLabel(subtitle)
        subtitle_label.setObjectName("LauncherTileSubtitle")
        subtitle_label.setWordWrap(True)

        layout.addWidget(title_label)
        layout.addWidget(subtitle_label)
        layout.addStretch(1)


class LauncherWindow(QtWidgets.QMainWindow):
    def __init__(self, live_window: PointerDesktopWindow) -> None:
        super().__init__()
        self.live_window = live_window
        self.setWindowTitle("TargetPointer Workbench")
        self.setWindowIcon(build_arrow_icon(WINDOW_ICON_TEXT))
        self.resize(1040, 680)
        self._build_ui()
        self._apply_styles()

    def closeEvent(self, event: QtGui.QCloseEvent) -> None:
        self.live_window.close()
        super().closeEvent(event)

    def _build_ui(self) -> None:
        central = QtWidgets.QWidget(self)
        root = QtWidgets.QVBoxLayout(central)
        root.setContentsMargins(30, 28, 30, 30)
        root.setSpacing(22)

        header = QtWidgets.QFrame()
        header.setObjectName("LauncherHeader")
        header_layout = QtWidgets.QVBoxLayout(header)
        header_layout.setContentsMargins(24, 20, 24, 20)
        header_layout.setSpacing(6)

        title = QtWidgets.QLabel("TargetPointer Workbench")
        title.setObjectName("LauncherTitle")
        subtitle = QtWidgets.QLabel("Open each operator surface as an independent window.")
        subtitle.setObjectName("LauncherSubtitle")
        header_layout.addWidget(title)
        header_layout.addWidget(subtitle)

        grid = QtWidgets.QGridLayout()
        grid.setHorizontalSpacing(16)
        grid.setVerticalSpacing(16)

        self.live_tile = LauncherTile("Live Control", "Camera, serial connection, target selection, and device control.")
        self.voice_tile = LauncherTile("Voice Assistant", "LiveKit pipeline voice conversation with subtitles and user mute control.")
        self.report_tile = LauncherTile("Target Report", "Generate and inspect the selected person PDF report.")
        self.insights_tile = LauncherTile("Data Analysis", "Tracking state, servo angle, detection, and match-quality trends.")

        grid.addWidget(self.live_tile, 0, 0)
        grid.addWidget(self.voice_tile, 0, 1)
        grid.addWidget(self.report_tile, 1, 0)
        grid.addWidget(self.insights_tile, 1, 1)

        self.live_tile.clicked.connect(self.show_live_control)
        self.voice_tile.clicked.connect(self.show_voice_assistant)
        self.report_tile.clicked.connect(self.show_report)
        self.insights_tile.clicked.connect(self.show_insights)

        root.addWidget(header)
        root.addLayout(grid)
        root.addStretch(1)
        self.setCentralWidget(central)

    def _apply_styles(self) -> None:
        self.setStyleSheet(
            """
            QWidget {
                background: #eef3f8;
                color: #111b2b;
                font-family: "Segoe UI", "Microsoft YaHei UI", sans-serif;
                font-size: 14px;
            }
            QFrame#LauncherHeader {
                background: #ffffff;
                border: 1px solid #dfe7f0;
                border-radius: 22px;
            }
            QLabel#LauncherTitle {
                font-size: 32px;
                font-weight: 750;
                color: #111b2b;
            }
            QLabel#LauncherSubtitle {
                color: #5f6f86;
                font-size: 14px;
            }
            QPushButton#LauncherTile {
                text-align: left;
                background: #ffffff;
                border: 1px solid #dfe7f0;
                border-radius: 18px;
                color: #111b2b;
            }
            QPushButton#LauncherTile:hover {
                background: #f8fbff;
                border: 1px solid #b7cae3;
            }
            QLabel#LauncherTileTitle {
                font-size: 20px;
                font-weight: 750;
            }
            QLabel#LauncherTileSubtitle {
                color: #5f6f86;
                font-size: 13px;
                line-height: 1.45;
            }
            """
        )

    def show_live_control(self) -> None:
        self.live_window.show()
        self.live_window.raise_()
        self.live_window.activateWindow()

    def show_voice_assistant(self) -> None:
        self.live_window.show_voice_window()

    def show_report(self) -> None:
        self.live_window.show_report_window()

    def show_insights(self) -> None:
        self.live_window.show_insights_window()


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="TargetPointer workbench launcher.")
    parser.add_argument("--model", default="yolov8n.pt", help="YOLO model path or model name.")
    parser.add_argument("--port", help="Serial port to select on startup, for example COM4.")
    parser.add_argument("--auto-connect", action="store_true", help="Connect the selected serial port at startup.")
    parser.add_argument("--camera", help="Camera source to auto-open, for example 0.")
    parser.add_argument(
        "--camera-backend",
        choices=("auto", "any", "dshow", "msmf"),
        default="auto",
        help="Camera backend preference. On Windows, prefer msmf or dshow for indexed cameras.",
    )
    parser.add_argument("--on-loss", choices=("stop", "center"), default="stop", help="Loss strategy for the runtime.")
    return parser


def main() -> int:
    load_dotenv()
    args = build_arg_parser().parse_args()

    app = QtWidgets.QApplication(sys.argv)
    app.setApplicationName("TargetPointer")
    app.setStyle("Fusion")
    app.setWindowIcon(build_arrow_icon(WINDOW_ICON_TEXT))

    runtime = build_runtime_from_args(args)
    live_window = PointerDesktopWindow(
        runtime,
        initial_camera=args.camera,
        initial_port=args.port,
        auto_connect_serial=args.auto_connect or args.port is not None,
    )
    live_window.show()
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
