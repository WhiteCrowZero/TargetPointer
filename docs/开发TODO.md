# 开发 TODO

当前 TODO 只服务于“固定摄像头 + 单目标人物 + 上位机控制 + YOLO 初始化 + SG90 水平指向”这一条主线。

## 当前进度

- 已完成：
  固件串口主命令、YOLO 人物初始化、单目标持续定位、桌面端 UI、点击选人和手动框选、自适应分段限速舵机控制。
- 正在收尾：
  实机参数标定、独立 5V 条件下的舵机安全验证、连续演示稳定性验证。

## 当前产品路线优先级

- 第一优先级：
  做成一套稳定、可重复的互动演示装置。
- 第二优先级：
  优化目标选择体验、动作气质和主界面收束。
- 当前不优先：
  增加复杂功能边界或扩展成多目标视觉产品。

## 已完成阶段

### Day 1

- Blue Pill 固件烧录
- 串口联通
- SG90 基础动作验证
- `PING` / `STATUS?` / `CENTER` / `STOP` 验证

验收：

- 固件能稳定启动并回中
- 串口返回格式稳定
- 舵机在安全角度范围内动作正常

### Day 2

- 摄像头接入
- YOLO 检测人物
- 在画面中选择目标人物
- 建立目标初始化流程

验收：

- 实时画面稳定显示
- 画面中能检测到 `person`
- 用户能点击一个 YOLO 检测到的人物，或手动框选人物

### Day 3

- 目标中心点计算
- 水平偏移到舵机角度映射
- 串口控制闭环跑通
- 丢失目标后的 `STOP` / `CENTER` 策略

验收：

- 左中右三种位置对应合理角度
- 串口控制与视觉位置变化一致
- 丢失目标后动作可预测，不出现随机抖动

### Day 4

- 跟踪稳定性优化
- 检测框和状态可视化优化
- 控制死区和平滑调整
- 演示链路稳定性验证

验收：

- 连续演示过程中角度变化平稳
- 目标选择、跟踪、停止与回中状态清晰可见
- 演示链路可重复运行

## 下一步优先事项

- 在独立 5V 条件下验证 `90 -> 极左`、`90 -> 极右` 的大偏差安全移动。
- 根据实机速度和抖动情况，微调分段限速参数：
  `angle-small-error-threshold`、`angle-medium-error-threshold`、`angle-small-step`、`angle-medium-step`、`angle-large-step`。
- 验证点击选人和手动框选两种初始化方式在桌面端都能稳定进入锁定。
- 做至少一次完整演示链路复测：连接设备、打开摄像头、选中人物、持续跟随、丢失后 `STOP/CENTER`。

## 命令备忘

```bash
uv sync
uv run pio run --project-dir firmware
uv run pio run --project-dir firmware -t upload
uv run pio test --project-dir firmware -e native
uv run python scripts/pointer_serial_cli.py --port COM5 ping
uv run python scripts/pointer_serial_cli.py --port COM5 status
uv run python scripts/pointer_vision_app.py --port COM5 --camera 0 --model yolov8n.pt --verbose
uv run python scripts/pointer_desktop_app.py --port COM4 --camera 2 --camera-backend msmf --model yolov8n.pt
```
