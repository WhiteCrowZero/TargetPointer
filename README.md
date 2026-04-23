# TargetPointer

固定摄像头下的实体人物指向系统。

当前版本的产品目标很简单：用户在画面中选中一个人，系统持续锁定这个人，并驱动现实里的实体箭头指向他。它首先是一套互动演示装置，不是多目标身份识别平台。

## 当前状态

- 软件主链路已经打通：YOLO 人物检测初始化、单目标维护、串口控制、桌面端演示 UI、固件执行层都已就位。
- 默认演示入口是 `scripts/pointer_desktop_app.py`；调试入口保留 `scripts/pointer_vision_app.py` 和 `scripts/pointer_serial_cli.py`。
- Windows 侧原型实机链路已经跑通，当前验证环境为“手机摄像头 + CH340 临时供电 SG90”。
- 当前硬件方案为 `Blue Pill + SG90 + 蜂鸣器 + 红/绿 LED`；提示硬件、镜像角度方向和实时语音仍需要 Windows 侧实机复测。
- 当前主要剩余工作不是功能补齐，而是硬件形态收尾，包括独立 5V 供电、独立固定摄像头、状态提示硬件验证和更稳妥的参数标定。

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
uv run python scripts/pointer_serial_cli.py --port COM4 ping
uv run python scripts/pointer_serial_cli.py --port COM4 status
uv run python scripts/pointer_serial_cli.py --port COM4 center
uv run python scripts/pointer_serial_cli.py --port COM4 angle 120
uv run python scripts/pointer_serial_cli.py --port COM4 state lock
```

桌面端下拉框默认显示 `COM4`，但未显式传 `--port COMx` 或 `--auto-connect` 时不会启动即连接串口。命令行串口工具仍默认使用 `COM4`。

启动桌面端工作台：

```bash
uv run python scripts/pointer_desktop_app.py --camera 0 --camera-backend msmf --model yolov8n.pt
```

桌面端当前交互约定：

- 启动后进入一体化工作台，侧边栏可切换 `Live Control`、`AI 实时对话`、`目标报告`、`Data Analysis` 和 `Activity`。
- `Live Control` 持续提示下一步推荐操作，不适合当前状态的按钮会自动禁用。
- 失败操作会弹出短暂错误提示，详细记录保留在 `Activity`。
- `目标报告` 可在目标锁定后生成中文 `reports/YYYYMMDD_HHMMSS_target_report.pdf`，内容包含环境、穿着、姿态、活动和不确定性。
- `AI 实时对话` 现在只保留 `RealtimeAIChat` 后端这一条路径。TargetPointer 负责调用后端会话 API、连接 LiveKit、采集麦克风、播放 AI 音频和展示实时字幕；不再自己启动本地 worker，也不再生成手动命令。

仓库根目录 `.env` 中至少需要填写：

```bash
OPENAI_API_KEY=
REALTIME_CHAT_API_BASE_URL=http://127.0.0.1:8000
```

其中 `OPENAI_API_KEY` 用于报告；语音默认通过 `RealtimeAIChat` 后端处理，TargetPointer 只需要知道 API 基地址。

语音启动流程：

1. 先启动 `RealtimeAIChat` 的 API、worker 和 LiveKit。
2. 在 TargetPointer 的 `.env` 里设置 `REALTIME_CHAT_API_BASE_URL`。
3. 启动桌面端，进入 `AI 实时对话`。
4. 点击 `启动会话`，桌面端会自动创建 session、连接 LiveKit 并开始收发字幕和音频。
5. 不需要手动启动 worker，也不需要额外打开外部客户端。

## 调试入口

视觉调试入口：

```bash
uv run python scripts/pointer_vision_app.py --port COM4 --camera 0 --camera-backend msmf --model yolov8n.pt --verbose
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
- `docs/`：产品概述、协议、硬件接线验证和调试记录。
- `tests/`：上位机逻辑与工具测试。

## 文档入口

- [docs/README.md](docs/README.md)
- [docs/项目概述.md](docs/项目概述.md)
- [docs/接口与协议.md](docs/接口与协议.md)
- [docs/硬件接线与验证.md](docs/硬件接线与验证.md)
- [手工调试文档.md](手工调试文档.md)
