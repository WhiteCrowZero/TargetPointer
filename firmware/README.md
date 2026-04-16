# Firmware

本目录包含 Blue Pill 的最小可用固件实现，目标是尽快打通“上位机方向控制命令 -> SG90 舵机动作”的闭环。

## 当前内容

- `platformio.ini`
  Blue Pill 的 PlatformIO 构建配置。
- `config/default_config.hpp`
  串口波特率、舵机引脚、角度范围和中心角等默认参数。
- `include/pointer_protocol.hpp`
  文本协议解析接口，供固件主循环和宿主机测试复用。
- `src/main.cpp`
  串口接收、命令解析、状态回报和 SG90 控制逻辑。
- `test/test_pointer_protocol.cpp`
  协议解析与角度边界的宿主机测试。

## 当前协议

- `PING`
- `CENTER`
- `STOP`
- `ANGLE:<deg>`
- `STATUS?`

## 本地命令

- `uv run pio run --project-dir firmware`
  构建固件。
- `uv run pio run --project-dir firmware -t upload`
  烧录固件。
- `uv run pio device monitor --project-dir firmware`
  打开串口监视器。
- `uv run pio test --project-dir firmware -e native`
  在宿主机运行协议解析测试。

## 实现边界

- Blue Pill 不做人物检测、目标选择或任何视觉处理。
- 固件只负责方向控制、角度安全校验和状态回报。
- 目标识别逻辑完全在上位机。
