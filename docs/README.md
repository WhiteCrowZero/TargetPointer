# 文档导航

`docs/` 目录保存“寻音指针”的需求、架构、协议和调试文档，全部以当前最终方案为准：手机摄像头接入上位机，上位机完成语音识别与 YOLO 检测，Blue Pill 负责舵机执行。

## 推荐阅读顺序

1. [项目概述.md](/home/wzw/projects/VoicePointer/docs/项目概述.md)：项目背景、目标场景和总体流程。
2. [需求说明.md](/home/wzw/projects/VoicePointer/docs/需求说明.md)：功能需求、非功能需求和边界。
3. [架构设计.md](/home/wzw/projects/VoicePointer/docs/架构设计.md)：系统分层、数据流和角度计算思路。
4. [接口与协议.md](/home/wzw/projects/VoicePointer/docs/接口与协议.md)：上位机到 Blue Pill 的串口文本协议。
5. [硬件规格.md](/home/wzw/projects/VoicePointer/docs/硬件规格.md)：当前硬件选型和供电约束。
6. [硬件调试计划.md](/home/wzw/projects/VoicePointer/docs/硬件调试计划.md)：分阶段调试顺序。
7. [硬件调试记录.md](/home/wzw/projects/VoicePointer/docs/硬件调试记录.md)：当前硬件盘点、已确认项与待验证项。
8. [开发TODO.md](/home/wzw/projects/VoicePointer/docs/开发TODO.md)：两天内可执行的开发与验收步骤。

## 维护约定

- 文档统一使用“寻音指针”作为项目名称。
- 旧的独立外设识别与定位方案已废弃，不再作为设计依据。
- 变更主控、供电、串口参数或舵机角度边界时，先更新 [硬件规格.md](/home/wzw/projects/VoicePointer/docs/硬件规格.md)。
- 调试结论先记入 [硬件调试记录.md](/home/wzw/projects/VoicePointer/docs/硬件调试记录.md)，稳定后再同步到正式文档。
