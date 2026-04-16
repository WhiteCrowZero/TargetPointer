# TargetPointer

一个基于固定摄像头、YOLO 检测初始化与串口舵机控制的实体人物指向系统。

## 当前版本做什么

- 在固定摄像头视角下显示实时画面。
- 用 YOLO 检测画面中的 `person`，作为目标人物初始化入口。
- 允许用户点击一个 YOLO 检测到的人物，或手动框选人物区域。
- 上位机持续维护单目标位置，并根据目标框中心点相对画面中心的水平偏移计算控制角度。
- 通过串口向 Blue Pill 发送 `ANGLE`、`CENTER`、`STOP`、`STATUS?` 等命令。
- 由 Blue Pill 驱动 SG90，带动实体箭头持续做单轴水平指向。
- 目标连续丢失若干帧后执行 `STOP` 或 `CENTER`。

## 当前版本不做什么

- 不做语音控制或麦克风输入。
- 不做上传参考图找人。
- 不做复杂 ReID 或身份识别。
- 不做多目标身份管理。
- 不做多轴云台。
- 不做完整空间定位或距离估计。

## 唯一主线

`固定摄像头 -> 上位机实时画面 -> YOLO 检测人物并初始化目标 -> 跟踪/持续更新 -> 水平偏移映射到舵机角度 -> 串口 -> Blue Pill -> SG90 箭头指向`

## 演示流程

1. 烧录固件并完成 `Blue Pill + CH340 + SG90 + 独立 5V` 接线。
2. 用串口命令验证 `PING`、`STATUS?`、`CENTER`、`STOP`。
3. 启动上位机视觉程序，打开固定摄像头实时画面。
4. 在画面里点击一个 YOLO 检测到的人物，或手动框选人物区域。
5. 上位机以该人物完成初始化，并持续更新其水平位置。
6. 上位机把水平偏移映射为舵机角度，经串口发给 Blue Pill。
7. SG90 带动实体箭头持续指向该人物。
8. 目标连续丢失若干帧后，系统执行 `STOP` 或 `CENTER`。

## 项目结构

- `scripts/`
  上位机控制链路，包括串口通信、控制逻辑、YOLO 检测与可视化。
- `firmware/`
  Blue Pill 固件，只负责串口协议解析、状态回报和 SG90 控制。
- `docs/`
  当前单主线方案的需求、架构、协议、硬件与调试文档。
- `hardware/`
  接线、BOM、供电预算和机械说明。
- `tests/`
  上位机纯逻辑与串口辅助模块测试。

## 硬件文档

- [文档导航](docs/README.md)
- [硬件规格](docs/硬件规格.md)
- [接口与协议](docs/接口与协议.md)
- [硬件调试计划](docs/硬件调试计划.md)

## 当前状态

- 固件主线已收缩为串口方向控制与状态查询。
- 上位机主线聚焦于“YOLO 检测初始化 + 单目标持续定位 + 串口联动”。
- 语音相关主线、依赖和说明已移除，不再作为当前版本范围。
- 当前项目目标是尽快完成可重复演示，而不是扩展成复杂多目标识别系统。

## 环境准备

项目统一使用 `uv` 管理 Python 依赖，使用 `PlatformIO` 构建固件。

```bash
uv sync
```

Python 侧核心依赖只围绕当前主线：

- `opencv-python`
- `ultralytics`
- `pyserial`
- `platformio`

如果本地没有 YOLO 权重，可在首次运行时让 `ultralytics` 下载默认权重，或直接传入本地模型路径。

## 运行命令

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

启动上位机视觉主线：

```bash
uv run python scripts/pointer_vision_app.py --port COM5 --camera 0 --model yolov8n.pt --verbose
```

运行测试：

```bash
uv run python -m unittest discover -s tests
uv run pio test --project-dir firmware -e native
```

## 开发路线

- 先稳定 `Blue Pill + SG90 + 串口协议`。
- 再稳定 `YOLO 人物检测 + 目标选择 + 单目标跟踪`。
- 最后调通 `水平偏移 -> 角度映射 -> STOP/CENTER 丢失策略`，完成实机演示。
