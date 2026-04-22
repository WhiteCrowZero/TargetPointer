# 开发 TODO

当前 TODO 只围绕“固定摄像头 + 单目标人物 + 上位机控制 + SG90 单轴指向”这条主线。

## 已完成

- Blue Pill 串口协议主命令和状态回报
- YOLO 人物检测初始化
- 单目标持续维护与短时重获
- 水平偏移到角度映射与分段限速输出
- 桌面端演示入口、点击选人、拖框初始化
- 主界面步骤提示、按钮门控、短暂错误提示

## 当前待办

### P1 硬件形态收尾

- 独立 5V 供电下复测 `CENTER`、大偏差移动和持续跟随
- 独立固定摄像头替换当前手机摄像头方案
- 在最终硬件形态下再做一轮完整演示复测

### P1 参数标定

- 根据最终供电和最终机位微调角度阈值与步进参数
- 复核箭头结构和舵机安装位置带来的中心偏差

### P2 演示体验收尾

- 继续检查桌面端状态提示是否足够清楚
- 复核 Insights 和 Activity 是否仍有不必要噪声
- 补充必要的 Windows 手工验收记录和演示材料

## 命令备忘

```bash
uv sync
uv run pio run --project-dir firmware
uv run pio run --project-dir firmware -t upload
uv run pio test --project-dir firmware -e native
uv run python scripts/pointer_serial_cli.py --port COM5 status
uv run python scripts/pointer_desktop_app.py --port COM4 --camera 0 --camera-backend msmf --model yolov8n.pt
```
