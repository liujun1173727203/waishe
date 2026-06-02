# Hikvision Speaker Validation Toolkit

本项目基于海康 `HCNetSDK` Windows 64 位动态库，使用 Python `ctypes` 封装设备登录、语音转发、实时取流录像、ISAPI 配置和录像音频分析能力。

核心目标是验证“被测设备的扬声器是否有效”：向被测设备发送一段专属扬声器测试音，由另一台录音设备录制复合流，再分析录像音频中是否包含这段测试音。

## 主要能力

- SDK 初始化、登录、登出、清理。
- 双向语音转发，支持向设备发送编码音频帧。
- 实时预览取流并保存录像。
- ISAPI 查询和配置双向音频、音量、复合流音频。
- 生成设备专属扬声器测试音。
- 从录像中提取音频并判断是否有声音。
- 判断录像音频中是否包含当前被测设备的扬声器测试音。
- 按设备 IP 目录保存录像、参考音频、提取音频和执行日志。

## 关键文件

- `hikvision_voice.py`：HCNetSDK 封装，包含登录、语音转发、取流录像、录像关闭。
- `hikvision_isapi.py`：ISAPI 封装，包含音频能力、音量、复合流音频配置。
- `video_analysis.py`：录像音频提取、声音检测、参考测试音匹配。
- `use_cases/voice_talk_cases.py`：扬声器测试音生成和发送。
- `use_cases/speaker_test_cases.py`：完整扬声器测试流程。
- `demo_speaker_test.py`：扬声器测试命令行入口。
- `demo_video_analysis.py`：单独分析录像音频的命令行入口。
- `demo_stream_record.py`：普通实时流录像示例。
- `demo_composite_stream_record.py`：复合流录像示例。

## 快速运行

```powershell
python .\demo_speaker_test.py `
  --host 10.18.117.22 `
  --username admin `
  --password asdf!234 `
  --recorder-host 10.40.230.23 `
  --recorder-username admin `
  --recorder-password asdf!234
```

默认时间线：

- 录音设备先录制 `2s` 环境音。
- 被测设备播放 `4s` 扬声器测试音。
- 播放结束后继续录制 `3s`。
- 停止录像后等待 `3s` 再检查文件并分析。

## 扬声器测试音

默认会使用被测设备 IP 作为 `test-tone-id`，生成稳定的设备专属测试音。测试音不仅数字序列不同，还会在音调比例、数字间隔和有效发声占比上做差异化，降低实验室多台设备同时播放时的误判概率。

可显式指定测试音 ID：

```powershell
python .\demo_speaker_test.py --host 10.18.117.22 --test-tone-id device-B-001
```

也可指定固定数字序列：

```powershell
python .\demo_speaker_test.py --host 10.18.117.22 --digit-sequence 1234
```

## 判定逻辑

扬声器有效的核心判断是：

- `has_sound=True`：录音设备录像中存在有效声音。
- `match=True`：录像音频中匹配到当前被测设备的扬声器测试音。
- `score>=threshold`：匹配分数达到阈值，默认阈值 `0.8`。

分析逻辑同时使用 RMS 能量曲线、DTMF 频率特征、频偏容忍模板搜索和期望数字序列匹配。该设计适用于设备 B 扬声器播放、设备 A 麦克风录音、现场存在噪声且音调可能偏移的实验室环境。

## 输出目录

默认输出到：

```text
recordings/speaker_tests/<device-ip>/
```

包含：

- `speaker_test_log_<ip>_<timestamp>.log`：带时间戳的完整控制台日志。
- `speaker_test_reference_<ip>_<timestamp>_*.wav`：本次测试参考音频。
- `speaker_test_record_<ip>_<timestamp>.mp4`：录音设备录制的录像。
- `speaker_test_record_audio_<ip>_<timestamp>.wav`：从录像提取出的音频。

## 依赖

- Windows 64 位。
- Python 3。
- 项目内置 `libs/win64` 海康 SDK 依赖。
- 本机安装 `ffmpeg`，或通过 `--ffmpeg-path` 指定可执行文件路径。

默认示例路径：

```text
D:\ffmpeg\ffmpeg-2026-05-28-git-7b46c6a2a3-essentials_build\bin\ffmpeg.exe
```
