# Firmware

本目录包含 Blue Pill 的当前执行层固件。它只负责串口协议解析、SG90 控制和状态回报，不负责任何视觉识别。

## 目录内容

- `platformio.ini`
  PlatformIO 构建配置。
- `config/default_config.hpp`
  波特率、舵机引脚、中心角、角度边界和步进参数。
- `include/pointer_protocol.hpp`
  协议解析接口。
- `src/pointer_protocol.cpp`
  文本命令解析实现。
- `src/main.cpp`
  串口主循环、状态回报、LED 控制和舵机执行逻辑。
- `test/test_pointer_protocol.cpp`
  协议与角度边界测试。

## 当前支持的命令

- `PING`
- `CENTER`
- `STOP`
- `ANGLE:<deg>`
- `LED:ON`
- `LED:OFF`
- `STATUS?`
- `STATUS`

## 当前行为

- 设备上电后默认保持空闲，不自动回中。
- 舵机采用懒 attach，只有在需要动作时才 attach。
- `CENTER` 和 `ANGLE` 通过配置化步进参数渐进到位。
- `STATUS` 响应会返回当前角度、目标角、attach 状态、LED 状态、最近命令类别和最近结果。

## Windows 常用命令

```bash
uv run pio run --project-dir firmware
uv run pio run --project-dir firmware -t upload
uv run pio device monitor --project-dir firmware
uv run pio test --project-dir firmware -e native
```

## 边界

- 固件不处理人物检测、目标选择或跟踪。
- 上位机负责全部视觉语义和控制决策。
- 固件只保证文本协议、状态同步和安全角度范围内的执行。
