# Scripts

本目录保存当前唯一上位机控制通路的脚本，默认围绕“视觉 + 串口 + 固件”工作。

## 模块职责

- `pointer_serial.py`
  串口通信、命令发送、响应读取、状态查询。
- `pointer_host_logic.py`
  目标水平偏移到控制量的映射、死区、平滑、丢失策略等纯逻辑。
- `pointer_runtime.py`
  上位机运行时控制层，负责摄像头、YOLO、目标维护、串口和状态快照。
- `pointer_vision_app.py`
  摄像头读取、YOLO 推理、目标选择、可视化、串口联动。
- `pointer_desktop_app.py`
  Windows 优先的单窗口桌面应用入口，提供视频舞台、右侧操作面板，以及默认隐藏的活动日志弹层。
- `pointer_serial_cli.py`
  手动串口调试工具，用于固件联调和协议验证。

## 安装

```bash
uv sync
```

当前脚本依赖只围绕：

- `opencv-python`
- `PySide6`
- `ultralytics`
- `pyserial`

不需要安装任何与当前主线无关的额外输入系统库。

## 快速使用

串口联调：

```bash
uv run python scripts/pointer_serial_cli.py --port COM5 ping
uv run python scripts/pointer_serial_cli.py --port COM5 status
uv run python scripts/pointer_serial_cli.py --port COM5 center
uv run python scripts/pointer_serial_cli.py --port COM5 angle 120
```

启动视觉主线：

```bash
uv run python scripts/pointer_vision_app.py --port COM5 --camera 0 --model yolov8n.pt --verbose
```

启动桌面上位机：

```bash
uv run python scripts/pointer_desktop_app.py --model yolov8n.pt
```

如果你已经知道摄像头和串口，优先直接通过参数传入，避免启动时探测：

```bash
uv run python scripts/pointer_desktop_app.py --port COM4 --camera 2 --camera-backend msmf --model yolov8n.pt
```

Windows 下如果默认摄像头打开失败，可先扫描可用索引：

```bash
uv run python scripts/pointer_vision_app.py --list-cameras
```

也可以显式指定 backend：

```bash
uv run python scripts/pointer_vision_app.py --port COM5 --camera 0 --camera-backend msmf --model yolov8n.pt --verbose
```

## 交互约定

- 运行后默认打开实时画面窗口。
- 空闲状态下会显示 YOLO 检测到的人物框。
- 鼠标左键点击某个人物框可开始跟踪。
- 按 `r` 可手动框选人物区域。
- 按 `d` 可强制刷新一次 YOLO 检测。
- 按 `c` 回中，按 `x` 停止，按 `q` 退出。

## 调试说明

- `pointer_serial_cli.py` 会校验固件返回，收到 `ERR:*` 或无响应时以非零状态退出。
- `pointer_serial_cli.py` 的 `status` 子命令会先发 `STATUS?`，旧固件若返回 `ERR:BAD_CMD` 会自动回退到 `STATUS`。
- `pointer_desktop_app.py` 是首选演示入口；它不会在启动时自动扫摄像头，优先使用 `--camera` 和 `--camera-backend`。
- 桌面端活动日志默认隐藏，通过窗口右上角 `Activity` 按钮展开。
- `pointer_vision_app.py` 继续保留为算法和摄像头调试入口。
- `pointer_vision_app.py` 启动时会先读取固件启动日志，再发送 `CENTER`。
- Windows 下 `pointer_vision_app.py` 默认会依次尝试 `msmf`、`dshow`、`any` 三种摄像头 backend。
- 目标连续丢失若干帧后，程序会按配置执行 `STOP` 或 `CENTER`，避免舵机持续乱动。
