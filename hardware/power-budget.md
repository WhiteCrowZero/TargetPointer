# Power Budget

## Rails

- Main input: `5V`
- Logic rail: `Blue Pill 3.3V`
- Actuator rail: `独立 5V 舵机供电`

## Estimated Loads

| Subsystem | Voltage | Typical Current | Peak Current | Notes |
| --- | --- | --- | --- | --- |
| Blue Pill | 5V input / 3.3V logic | `50mA` 左右 | `100mA` 内 | 含串口日志与基础 IO |
| CH340 USB-TTL | 5V | `20mA` 左右 | `50mA` 内 | 由 USB 侧供电 |
| SG90 | 5V | `100mA~250mA` | `500mA+` | 启动和堵转时电流明显增大 |

## Power Rules

- SG90 不直接从 Blue Pill 的 `3.3V` 引脚取电。
- 舵机电源和主控电源可以分开，但必须共地。
- 若使用 `HW-131`，优先让它给主控侧供电，舵机侧使用更稳定的 5V 来源。

## Design Checks

- 上电或快速转向时，Blue Pill 不能因舵机电流波动复位。
- 舵机动作时串口通信不能明显异常。
- USB 供电不足时，优先排查电源而不是协议或固件。
