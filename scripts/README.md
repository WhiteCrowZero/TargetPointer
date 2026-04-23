# Scripts

本目录只保留当前上位机的薄入口脚本和兼容导入层。实际实现位于仓库根目录的 `targetpointer/` 包。Windows 是默认运行环境。

## 入口

- `pointer_desktop_app.py`
  默认演示入口。启动一体化工作台，并通过侧边栏切换主控、AI 实时对话、目标报告、数据分析和活动日志。
- `pointer_vision_app.py`
  调试入口。更适合验证摄像头、识别和串口链路。
- `pointer_serial_cli.py`
  串口协议联调工具。
- `pointer_voice_agent.py`
  LiveKit Agents 语音助手 worker。桌面端会自动启动它，通常不需要手动运行；独立调试时可手动运行 `dev`。

## 核心模块位置

- `targetpointer/runtime/runtime.py`
  管理摄像头、检测、目标维护、串口和状态快照。
- `targetpointer/runtime/host_logic.py`
  纯控制逻辑，包括偏移映射、死区、分段限速和丢失策略。
- `targetpointer/runtime/serial.py`
  串口发送、响应读取和状态查询封装。
- `targetpointer/reporting/report.py`
  选中人物截图、OpenAI 视觉分析和 PDF 报告生成。
- `targetpointer/ui/launcher.py`
  工作台启动器主页。
- `targetpointer/ui/desktop_app.py`
  PySide 主控台、报告、语音助手和数据分析窗口。

## 依赖安装

```bash
uv sync
```

## 常用命令

串口联调：

```bash
uv run python scripts/pointer_serial_cli.py --port COM4 ping
uv run python scripts/pointer_serial_cli.py --port COM4 status
uv run python scripts/pointer_serial_cli.py --port COM4 center
uv run python scripts/pointer_serial_cli.py --port COM4 angle 120
uv run python scripts/pointer_serial_cli.py --port COM4 state search
uv run python scripts/pointer_serial_cli.py --port COM4 state lock
uv run python scripts/pointer_serial_cli.py --port COM4 state lost
uv run python scripts/pointer_serial_cli.py --port COM4 state idle
uv run python scripts/pointer_serial_cli.py --port COM4 buzzer beep
uv run python scripts/pointer_serial_cli.py --port COM4 buzzer off
```

当前默认串口是 `COM4`。需要换口时再显式传入其他 `COMx`。

启动桌面端工作台：

```bash
uv run python scripts/pointer_desktop_app.py --camera 0 --camera-backend msmf --model yolov8n.pt
```

桌面端下拉框默认显示 `COM4`，但只有显式传 `--port COMx` 或 `--auto-connect` 时才会启动即连接串口。

报告与语音助手会从仓库根目录 `.env` 读取云端配置。留空的模板如下，实际值由操作者在 Windows 工作区填写：

```bash
OPENAI_API_KEY=
ELEVEN_API_KEY=
LIVEKIT_URL=
LIVEKIT_API_KEY=
LIVEKIT_API_SECRET=
TARGETPOINTER_REPORT_MODEL=
TARGETPOINTER_VOICE_LLM_MODEL=
TARGETPOINTER_VOICE_TEMPERATURE=
TARGETPOINTER_VOICE_MAX_OUTPUT_TOKENS=
TARGETPOINTER_STT_MODEL=
TARGETPOINTER_STT_LANGUAGE=
TARGETPOINTER_TTS_MODEL=
TARGETPOINTER_TTS_VOICE=
TARGETPOINTER_TTS_SPEED=
TARGETPOINTER_ELEVEN_STABILITY=
TARGETPOINTER_ELEVEN_SIMILARITY_BOOST=
TARGETPOINTER_VAD_ACTIVATION_THRESHOLD=
TARGETPOINTER_VAD_SILENCE_DURATION_MS=
```

启动命令行视觉调试：

```bash
uv run python scripts/pointer_vision_app.py --port COM4 --camera 0 --camera-backend msmf --model yolov8n.pt --verbose
```

列出摄像头：

```bash
uv run python scripts/pointer_vision_app.py --list-cameras
```

摄像头扫描默认只检查索引能否打开，不读帧，避免 Windows MSMF 后端在异常设备索引上卡住。需要强制读一帧时再加 `--camera-scan-read`。

## 当前桌面端交互约定

- 启动后进入一体化工作台，侧边栏切换各页面。
- `Live Control` 会持续显示下一步推荐操作，不适合当前状态的按钮会自动禁用。
- 支持点击检测框和拖框初始化两种方式。
- 失败时会显示 5 秒左右的短暂错误提示，详细信息继续写入 `Activity`。
- `Data Analysis` 是独立窗口，用于查看角度、检测数量和匹配质量等趋势。
- `Target Report` 仅在摄像头已打开且当前人物处于锁定或重关联状态时可生成报告。
- `AI 实时对话` 使用 LiveKit worker pipeline，按 `Silero VAD -> OpenAI STT -> OpenAI LLM -> ElevenLabs TTS` 拼接，方便单独调音色、转写模型和提示词；界面会显示用户静音开关、实时字幕和状态动画。真实房间连接、麦克风和扬声器链路需要在 Windows 环境验证。

## 协议与运行时说明

- 启动或重连后，运行时会查询 `STATUS?` 以同步真实设备状态。
- `status` 命令会优先发送 `STATUS?`，旧固件若返回 `ERR:BAD_CMD` 会回退到 `STATUS`。
- `state` 命令用于手动验证红绿 LED 和蜂鸣器，支持 `idle`、`search`、`lock`、`lost`。
- 桌面端和视觉调试入口会在状态变化时发送 `STATE:<mode>`；`LED:ON/OFF` 已废弃，不再作为状态提示降级路径。
- 角度控制采用“目标角 + 分段限速输出角”；镜像摄像头下已反转水平偏移到舵机角的方向。
- 目标连续丢失若干帧后，程序会按配置执行 `STOP` 或 `CENTER`。
