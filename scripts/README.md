# Scripts

本目录保存上位机联调用脚本，默认面向 `Windows + Python`，并统一使用 `uv` 管理 Python 环境与依赖。

## Files

- `pointer_serial_cli.py`
  串口手动发令工具，用于 Day 1 和 Day 2 的舵机联调。
- `pointer_vision_app.py`
  上位机最小闭环程序，负责摄像头读取、目标选择、YOLO 检测、角度计算和串口发令。

## Quick Start

同步依赖：

```bash
uv sync
```

手动发串口命令：

```bash
uv run python scripts/pointer_serial_cli.py --port COM5 ping
uv run python scripts/pointer_serial_cli.py --port COM5 angle 120
uv run python scripts/pointer_serial_cli.py --port COM5 center
```

运行视觉闭环：

```bash
uv run python scripts/pointer_vision_app.py --port COM5 --camera 0 --target cup --show
```

若要尝试语音输入：

```bash
uv run python scripts/pointer_vision_app.py --port COM5 --camera 0 --input-mode speech --show
```
