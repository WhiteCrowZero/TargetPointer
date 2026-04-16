# Firmware

本目录包含 Blue Pill 的最小可用固件实现，目标是尽快打通“串口文本命令 -> SG90 舵机动作”的闭环。

## 当前内容

- `platformio.ini`
  Blue Pill 的 PlatformIO 构建配置。
- `config/default_config.hpp`
  串口波特率、舵机引脚、角度范围和中心角等默认参数。
- `include/pointer_protocol.hpp`
  文本协议解析接口，供固件主循环和主机侧测试复用。
- `src/main.cpp`
  串口接收、命令解析和 SG90 控制逻辑。
- `test/test_pointer_protocol.cpp`
  协议解析与角度边界的宿主机测试。

## 当前协议

- `PING`
- `CENTER`
- `STOP`
- `ANGLE:<deg>`
- `TARGET:<name>`
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

- Blue Pill 不做视觉识别和语音识别。
- 固件只负责串口命令解析、角度安全校验和舵机控制。
- 上位机负责目标检测、目标选择和角度计算。
