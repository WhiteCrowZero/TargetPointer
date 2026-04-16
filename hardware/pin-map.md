# Pin Map

## Controller

- Board: `Blue Pill (STM32F103C8)`
- Revision: `V1 prototype`

## Assigned Functions

- `PA8`: `SG90` PWM signal
- `PA9`: `USART1 TX`，Blue Pill 发回调试与状态信息到 `CH340 RX`
- `PA10`: `USART1 RX`，接收上位机文本命令
- `PA13 / PA14`: `SWDIO / SWCLK`，连接 `ST-LINK V2`
- `PC13`: 板载状态 LED

## Wiring Notes

- `CH340 TX -> PA10`
- `CH340 RX -> PA9`
- `SG90 signal -> PA8`
- `SG90 5V -> 独立 5V 电源`
- `Blue Pill GND / CH340 GND / SG90 GND` 必须共地

## Constraints

- `PA13 / PA14` 保留给 SWD，不复用。
- `PA8` 使用定时器输出，优先保留给舵机。
- 若后续增加按键或蜂鸣器，优先选不影响串口和 SWD 的空闲引脚。
