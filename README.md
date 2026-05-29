# Hikvision Voice Talk Wrapper

基于 `libs/win64` 下的 `HCNetSDK` 做了一个纯 Python `ctypes` 封装，用来完成电脑与设备之间的语音输入输出通信。

## 文件

- `hikvision_voice.py`: SDK 初始化、登录、语音对讲、语音转发接口封装。
- `demo_voice_call.py`: 直接发起电脑和设备双向语音通话的命令行示例。

## 已封装能力

- `HikvisionVoiceSDK.initialize()`: 初始化 SDK，并自动配置 `HCNetSDKCom`、OpenSSL 依赖路径。
- `login()` / `logout()`: 登录和登出设备。
- `set_talk_mode()`: 设置对讲模式。
  - `False`: 使用 SDK 默认的对讲库模式。
  - `True`: 使用旧版 Windows API 模式。
- `start_call()`: 启动电脑麦克风和电脑扬声器参与的双向语音通话。
- `start_voice_forward()`: 启动语音转发，接收设备编码后的音频，并支持 `send_encoded_audio()` 主动向设备发送编码音频数据。
- `get_current_audio_compress()`: 获取设备当前生效的语音编码参数。
- `set_audio_compress()`: 设置设备语音编码参数。

## 直接通话示例

```powershell
python .\demo_voice_call.py `
  --host 192.168.1.64 `
  --port 8000 `
  --username admin `
  --password 12345
```

如果你要切到旧版 Windows 采集模式：

```powershell
python .\demo_voice_call.py `
  --host 192.168.1.64 `
  --username admin `
  --password 12345 `
  --windows-api
```

## 代码里调用

```python
from hikvision_voice import HikvisionVoiceSDK

sdk = HikvisionVoiceSDK()
sdk.initialize()
sdk.set_talk_mode(use_windows_api=False)

session = sdk.login("192.168.1.64", 8000, "admin", "12345")
call = sdk.start_call(session)

input("语音通话中，回车结束...")

call.stop()
sdk.logout(session)
sdk.cleanup()
```

## 说明

- `start_call()` 复用 SDK 自带的本地采集和本地播放能力，不依赖 `pyaudio` 一类第三方库。
- `start_voice_forward()` 面向更底层的“设备音频收发接口”场景，适合你后续接入自定义编码器、文件流或你自己的音频处理链路。
- 设备侧如果修改了语音编码参数，通常需要重启设备后生效。
- 如果设备是通过 NVR 挂接的 IPC，对讲通道号通常不是 `1`，默认应优先使用登录信息里的 `byStartDTalkChan`。
