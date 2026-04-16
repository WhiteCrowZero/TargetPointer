# 开发 TODO

本计划用于按步骤推进“寻音指针”原型实现。目标是在两天内完成最小可演示闭环，每一步都包含烧录后检查点。

## Day 1：Blue Pill 与 SG90 打通

### 1. 固定接线并确认工具链

- 完成 `Blue Pill + ST-LINK + CH340 + SG90` 接线。
- 确认 `ST-LINK` 与 `CH340` 都能被电脑识别。

检查点：

- 电脑能识别 `ST-LINK`
- 电脑能识别 `CH340` 串口
- 舵机供电接入后板子不异常复位

### 2. 烧录最小启动程序

- 构建并烧录 `firmware/` 当前固件。
- 打开串口监视器，确认上电日志输出。

检查点：

- 串口能输出 `BOOT`
- 上电后能输出 `OK:CENTER`
- 重启 3 次结果一致

### 3. 检查舵机回中

- 观察 SG90 是否在上电后转到 `90°`。

检查点：

- 舵机不会打到底
- 回中位置稳定
- 连续上电 3 次表现一致

### 4. 检查串口协议基础命令

- 使用 `scripts/pointer_serial_cli.py` 发送 `PING`、`CENTER`、`STOP`、`ANGLE:<deg>`。

检查点：

- `PING` 返回 `PONG`
- `CENTER` 能回到 `90°`
- `ANGLE:120` 能正确转到右侧
- `ANGLE:10` 返回 `ERR:BAD_ANGLE`

### 5. 检查安全角范围

- 测试 `ANGLE:20`、`ANGLE:90`、`ANGLE:160`。

检查点：

- 左中右三个角度都可达
- 结构不碰撞
- 舵机无明显持续抖动

Day 1 完成标准：

- 只靠串口命令就能稳定控制 SG90，串口协议可重复执行。

## Day 2：上位机视觉闭环

### 6. 跑通串口控制脚本

- 在 Windows 上运行 `scripts/pointer_serial_cli.py`。

检查点：

- 能发送并收到返回消息
- 连续发送多条命令不丢失

### 7. 接入手机 USB 摄像头

- 在电脑端打开手机视频源。

检查点：

- Python 能读到实时画面
- 连续运行 5 分钟不断流

### 8. 跑通 YOLO 检测

- 运行 `scripts/pointer_vision_app.py`，先用文本输入目标类别。

检查点：

- 目标框能稳定显示
- 只针对当前目标类别做指向
- 找不到目标时不会发送无效角度

### 9. 检查角度映射

- 在画面左、中、右分别放置目标。

检查点：

- 左侧目标对应较小角度
- 中间目标接近 `90°`
- 右侧目标对应较大角度

### 10. 完成文本输入闭环

- 输入目标名称，例如 `cup` 或 `remote`。

检查点：

- 检测到目标后能发送 `ANGLE:<deg>`
- 箭头会随着目标横向位置变化而变化
- 目标丢失时会发送 `STOP`

### 11. 接入语音输入

- 使用 `--input-mode speech` 运行上位机程序。

检查点：

- 能识别至少 1 个目标词
- 语音结果能映射到内部目标类别
- 识别失败不会导致程序卡死

### 12. 录制最终演示

- 固定桌面场景完成一次完整流程。

检查点：

- 用户输入或说出目标名
- 画面中目标被检测
- SG90 转向正确方向
- 整机连续运行 10 分钟不崩溃

## 命令备忘

```bash
uv sync
uv run pio run --project-dir firmware
uv run pio run --project-dir firmware -t upload
uv run pio device monitor --project-dir firmware
uv run pio test --project-dir firmware -e native
uv run python scripts/pointer_serial_cli.py --port COM5 ping
uv run python scripts/pointer_vision_app.py --port COM5 --camera 0 --target cup --show
```
