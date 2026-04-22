# TargetPointer

固定摄像头下的实体人物指向系统。

当前版本的产品目标很简单：用户在画面中选中一个人，系统持续锁定这个人，并驱动现实里的实体箭头指向他。它首先是一套互动演示装置，不是多目标身份识别平台。

## 当前状态

- 软件主链路已经打通：YOLO 人物检测初始化、单目标维护、串口控制、桌面端演示 UI、固件执行层都已就位。
- 默认演示入口是 `scripts/pointer_desktop_app.py`，它会打开工作台启动器；调试入口保留 `scripts/pointer_vision_app.py` 和 `scripts/pointer_serial_cli.py`。
- Windows 侧原型实机链路已经跑通，当前验证环境为“手机摄像头 + CH340 临时供电 SG90”。
- 当前主要剩余工作不是功能补齐，而是硬件形态收尾，包括独立 5V 供电、独立固定摄像头和更稳妥的参数标定。

## Windows 快速开始

安装依赖：

```bash
uv sync
```

构建固件：

```bash
uv run pio run --project-dir firmware
```

烧录固件：

```bash
uv run pio run --project-dir firmware -t upload
```

串口联调：

```bash
uv run python scripts/pointer_serial_cli.py --port COM5 ping
uv run python scripts/pointer_serial_cli.py --port COM5 status
uv run python scripts/pointer_serial_cli.py --port COM5 center
uv run python scripts/pointer_serial_cli.py --port COM5 angle 120
```

启动桌面端工作台：

```bash
uv run python scripts/pointer_desktop_app.py --port COM4 --camera 0 --camera-backend msmf --model yolov8n.pt
```

桌面端当前交互约定：

- 启动后先进入工作台主页，`Live Control`、`Voice Assistant`、`Target Report`、`Data Analysis` 都是独立窗口入口。
- `Live Control` 持续提示下一步推荐操作，不适合当前状态的按钮会自动禁用。
- 失败操作会弹出短暂错误提示，详细记录保留在 `Activity`。
- `Target Report` 独立窗口可在目标锁定后生成 `reports/YYYYMMDD_HHMMSS_target_report.pdf` 并展示分析内容。
- `Voice Assistant` 独立窗口可配置 ElevenLabs STT、LLM 温度、TTS 音色/速度，并从桌面端每 5 秒采样当前画面作为上下文。

云端报告和语音助手需要在仓库根目录 `.env` 中填写：

```bash
OPENAI_API_KEY=
ELEVEN_API_KEY=
LIVEKIT_URL=
LIVEKIT_API_KEY=
LIVEKIT_API_SECRET=
```

可选模型覆盖项也在 `.env` 中，留空时使用默认值。

## 调试入口

视觉调试入口：

```bash
uv run python scripts/pointer_vision_app.py --port COM5 --camera 0 --camera-backend msmf --model yolov8n.pt --verbose
```

列出摄像头：

```bash
uv run python scripts/pointer_vision_app.py --list-cameras
```

运行测试：

```bash
uv run python -m unittest discover -s tests
uv run pio test --project-dir firmware -e native
```

## 仓库结构

- `targetpointer/`：上位机应用包，包含运行时、视觉、串口、报告、语音和 PySide UI。
- `scripts/`：Windows 优先的薄入口脚本和兼容导入层。
- `firmware/`：Blue Pill 固件、协议解析和 PlatformIO 配置。
- `docs/`：产品、需求、架构、协议和硬件调试文档。
- `hardware/`：BOM、引脚图、供电与结构说明。
- `tests/`：上位机逻辑与工具测试。

## 文档入口

- [docs/README.md](docs/README.md)
- [docs/项目概述.md](docs/项目概述.md)
- [docs/需求说明.md](docs/需求说明.md)
- [docs/架构设计.md](docs/架构设计.md)
- [docs/接口与协议.md](docs/接口与协议.md)
- [手工调试文档.md](手工调试文档.md)
