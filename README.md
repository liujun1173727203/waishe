# Hikvision Speaker Validation Toolkit

项目基于海康 `HCNetSDK` 和 `ISAPI`，用于验证设备扬声器、拾音器、补光灯、录像与抓图等能力。

核心扬声器验证流程：

1. 向被测设备发送一段专属测试音
2. 由录音设备录制现场音视频
3. 从录像中提取音频
4. 分析是否存在声音，以及是否匹配当前被测设备的测试音

## 主要能力

- SDK 初始化、登录、登出、清理
- 双向语音转发
- 实时预览取流并保存录像
- ISAPI 查询和配置双向音频、音量、复合流音频
- 生成设备专属测试音
- 从录像中提取音频并判断是否有声
- 判断录像音频中是否包含目标测试音
- 按设备 IP 分类保存录像、参考音频、提取音频和日志

## 关键文件

- `hikvision_voice.py`：HCNetSDK 封装
- `hikvision_isapi.py`：ISAPI 封装
- `video_analysis.py`：录像音频提取与匹配分析
- `use_cases/voice_talk_cases.py`：测试音生成与发送
- `use_cases/speaker_test_cases.py`：扬声器测试流程
- `use_cases/pickup_test_cases.py`：拾音测试流程
- `use_cases/supplement_light_cases.py`：补光灯测试流程
- `demo_http_api.py`：FastAPI 异步接口服务

## 快速运行

```powershell
python .\demo_speaker_test.py `
  --host 10.18.117.22 `
  --username admin `
  --password asdf!234 `
  --recorder-pool-config .\configs\recorder_device_pool.json
```

## ffmpeg 配置

`ffmpeg` 不再通过 `--ffmpeg-path` 参数传入，统一从配置文件读取：

文件：

```text
configs/app_config.json
```

示例：

```json
{
  "ffmpeg_path": "ffmpeg"
}
```

也可以配置绝对路径：

```json
{
  "ffmpeg_path": "D:\\ffmpeg\\bin\\ffmpeg.exe"
}
```

## 录音设备池

录音设备需要维护在：

```text
configs/recorder_device_pool.json
```

配置示例：

```json
{
  "devices": [
    {
      "id": "recorder-a-10.40.230.23",
      "host": "10.40.230.23",
      "port": 8000,
      "username": "admin",
      "password": "asdf!234",
      "channel": 0,
      "voice_channel": 1,
      "max_connections": 5
    }
  ]
}
```

## 输出目录

扬声器测试默认输出到：

```text
recordings/speaker_tests/<device-ip>/
```

拾音测试默认输出到：

```text
recordings/pickup_tests/<device-ip>/
```

补光灯测试默认输出到：

```text
recordings/supplement_light_tests/<device-ip>/
```

## 补光灯测试说明

补光灯测试会使用 `ffmpeg` 读取抓图中心 `40% x 40%` ROI 的灰度均值，判断补光灯是否真正开启，以及亮度是否随档位递增。

## Linux 运行

Linux 环境需要：

- 安装海康 Linux 64 位 HCNetSDK
- 安装 `ffmpeg` 并确保配置文件中的 `ffmpeg_path` 可用
- 配置 `HIKVISION_SDK_ROOT` 和 `LD_LIBRARY_PATH`

示例：

```bash
export HIKVISION_SDK_ROOT=/opt/hikvision/HCNetSDK
export LD_LIBRARY_PATH="$HIKVISION_SDK_ROOT:$HIKVISION_SDK_ROOT/HCNetSDKCom:$LD_LIBRARY_PATH"
python3 demo_speaker_test.py
```

更完整的 Linux 部署说明见 [docs/linux.md](docs/linux.md)。

## FastAPI 接口

项目支持通过 FastAPI 以异步任务方式调用测试用例：

```powershell
python .\demo_http_api.py --host 0.0.0.0 --port 18080
```

接口文档见 [docs/http_api.md](docs/http_api.md)。
