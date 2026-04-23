# Firmware

本目录包含 Blue Pill 的当前执行层固件。它只负责串口协议解析、SG90 控制和状态回报，不负责任何视觉识别。

## 目录内容

- `platformio.ini`
  PlatformIO 构建配置。
- `config/default_config.hpp`
  波特率、舵机、红绿 LED、蜂鸣器引脚、中心角、角度边界和步进参数。
- `include/pointer_protocol.hpp`
  协议解析接口。
- `src/pointer_protocol.cpp`
  文本命令解析实现。
- `src/main.cpp`
  串口主循环、状态回报、红绿 LED/蜂鸣器状态提示和舵机执行逻辑。
- `test/test_pointer_protocol.cpp`
  协议与角度边界测试。

## 当前支持的命令

- `PING`
- `CENTER`
- `STOP`
- `ANGLE:<deg>`
- `STATE:IDLE`
- `STATE:SEARCH`
- `STATE:LOCK`
- `STATE:LOST`
- `BUZZER:ON`
- `BUZZER:OFF`
- `BUZZER:BEEP`
- `LED:ON`，废弃兼容命令，不再改变 LED 状态
- `LED:OFF`，废弃兼容命令，不再改变 LED 状态
- `STATUS?`
- `STATUS`

## 当前行为

- 设备上电后默认保持空闲，不自动回中。
- 舵机采用懒 attach，只有在需要动作时才 attach。
- `CENTER` 和 `ANGLE` 通过配置化步进参数渐进到位。
- `STATE` 驱动外接红绿 LED、蜂鸣器和板载阶段指示。
- `STATUS` 响应会返回当前角度、目标角、attach 状态、LED 状态、设备状态、最近命令类别和最近结果。

## 状态输出约定

| 状态 | 触发命令 | 绿 LED | 红 LED | 蜂鸣器 |
| --- | --- | --- | --- | --- |
| 空闲 | `STATE:IDLE` | 灭 | 灭 | 不响，并清空未完成提示 |
| 等待选择 | `STATE:SEARCH` | 慢闪 | 灭 | 不响，并清空未完成提示 |
| 锁定跟踪 | `STATE:LOCK` | 常亮 | 灭 | 短响 1 声 |
| 目标丢失 | `STATE:LOST` | 灭 | 常亮 | 短响 2 声 |

`LED:ON/OFF` 只保留为旧上位机兼容命令，响应 `OK:LED:DEPRECATED`，不再改变红绿 LED 或设备状态。
蜂鸣器按低电平触发模块处理，固件在响的时候把 `PB12` 下拉到 GND，不响时释放为高阻输入，避免 5V 模块把 3.3V 高电平误判为低。

## Windows 常用命令

```bash
uv run pio run --project-dir firmware
uv run pio run --project-dir firmware -t upload
uv run pio device monitor --project-dir firmware
uv run pio test --project-dir firmware -e native
uv run python scripts/pointer_serial_cli.py --port COM4 state lock
uv run python scripts/pointer_serial_cli.py --port COM4 buzzer beep
uv run python scripts/pointer_serial_cli.py --port COM4 buzzer on
uv run python scripts/pointer_serial_cli.py --port COM4 buzzer off
```

## 边界

- 固件不处理人物检测、目标选择或跟踪。
- 上位机负责全部视觉语义和控制决策。
- 固件只保证文本协议、状态同步和安全角度范围内的执行。
