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
  --recorder-pool-config .\configs\recorder_device_pool.json
```

录音设备必须维护在 `configs/recorder_device_pool.json` 中。配置包含 IP、端口、用户名、密码、通道、语音通道和 `max_connections`。当录音设备连接数达到上限时，后续测试按先来先服务策略排队，默认最多等待 `300s`。

默认时间线：

- 录音设备先录制 `2s` 环境音。
- 被测设备播放 `4s` 扬声器测试音。
- 播放结束后继续录制 `3s`。
- 停止录像后等待 `3s` 再检查文件并分析。

## 扬声器测试音

默认会使用被测设备 IP 作为 `test-tone-id`，生成稳定的设备专属离散频率指纹音。测试音由 `900Hz ~ 3200Hz` 内的多段固定正弦频率组成，段边界使用短淡化，降低实验室多台设备同时播放时的误判概率。

可显式指定测试音 ID：

```powershell
python .\demo_speaker_test.py --host 10.18.117.22 --test-tone-id device-B-001
```

## 判定逻辑

扬声器有效的核心判断是：

- `has_sound=True`：录音设备录像中存在有效声音。
- `match=True`：录像音频中匹配到当前被测设备的扬声器测试音。
- `score>=threshold`：匹配分数达到阈值，默认阈值 `0.7`。

分析逻辑使用频率指纹分量检测：从参考音频提取频率探针，在录像音频中搜索这些频率分量是否出现。该设计适用于设备 B 扬声器播放、设备 A 麦克风录音、现场存在噪声且音调可能偏移的实验室环境。

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

## Supplement Light IrLight Test

补光灯用例当前以 `IrLight` 为准：

- 能力判断：`GET /ISAPI/Image/channels/capabilities`，存在 `IrLight` 标签才认为支持补光灯。
- 能力读取：从 `IrLight/mode` 的 `opt` 属性获取全部模式，从 `IrLight/brightnessLimit` 的 `min`、`max` 属性获取强度范围。
- 配置读取：`GET /ISAPI/Image/channels/<channel>`，获取设备当前完整图像通道报文。
- 配置写入：基于 GET 到的完整报文，只修改 `IrLight/mode` 和 `IrLight/brightnessLimit`，再 `PUT /ISAPI/Image/channels/<channel>`。
- 遍历策略：对每个 `mode opt`，分别设置 `brightnessLimit=min/middle/max`，每档等待稳定后抓图。
- 判定策略：使用 ffmpeg 读取抓图中心 `40% x 40%` ROI 灰度均值，判断 `middle - min` 和 `max - middle` 是否均超过 `--level-threshold`。

输出文件位于：

```text
recordings/supplement_light_tests/<device-ip>/
```

抓图文件名包含 mode 和 brightnessLimit，例如：

```text
supplement_light_ir_auto_50_10_18_117_22_20260605_101010.jpg
```

## Linux 运行

Python 业务代码支持 Windows 和 Linux。HCNetSDK 动态库必须使用与当前操作系统匹配的海康官方版本：

- Windows 默认读取 `libs/win64/HCNetSDK.dll`。
- Linux 默认读取 `libs/linux64/` 下的 `libhcnetsdk.so` 及配套 `.so` 文件。
- 也可以通过环境变量 `HIKVISION_SDK_ROOT` 指定 SDK 根目录。

Linux 示例：

```bash
export HIKVISION_SDK_ROOT=/opt/hikvision/HCNetSDK
export LD_LIBRARY_PATH="$HIKVISION_SDK_ROOT:$HIKVISION_SDK_ROOT/HCNetSDKCom:$LD_LIBRARY_PATH"
python3 demo_speaker_test.py --ffmpeg-path ffmpeg
```

Linux 运行要求：

- 安装海康 Linux 64 位 HCNetSDK，不能使用仓库中的 Windows DLL。
- 安装 `ffmpeg` 并确保可通过 PATH 执行。
- SDK 根目录应包含 `libhcnetsdk.so`，配套组件放在 `HCNetSDKCom` 或 SDK 根目录。
- Linux 路径使用 UTF-8 编码；Windows 路径继续使用 GBK 编码。

更完整的 Linux 部署说明见 [docs/linux.md](docs/linux.md)。
