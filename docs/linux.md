# Linux 运行说明

## 支持范围

项目 Python 代码支持 Windows 和 Linux。
HCNetSDK 动态库必须与操作系统匹配：

- Windows 使用 `HCNetSDK.dll`
- Linux 使用 `libhcnetsdk.so`
- Windows DLL 不能直接在 Linux 中使用

仓库当前主要内置 Windows SDK。Linux 环境请自行安装海康官方 Linux 64 位 HCNetSDK。

## SDK 目录

Linux 默认 SDK 目录建议为：

```text
libs/linux64/
```

推荐结构：

```text
libs/linux64/
|-- libhcnetsdk.so
|-- libcrypto.so
|-- libssl.so
`-- HCNetSDKCom/
    `-- *.so
```

也可以通过环境变量指定其它目录：

```bash
export HIKVISION_SDK_ROOT=/opt/hikvision/HCNetSDK
export LD_LIBRARY_PATH="$HIKVISION_SDK_ROOT:$HIKVISION_SDK_ROOT/HCNetSDKCom:$LD_LIBRARY_PATH"
```

## 系统依赖

安装 Python、ffmpeg 和项目依赖：

```bash
sudo apt-get update
sudo apt-get install -y python3 python3-venv ffmpeg

python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

如果没有 `requirements.txt`，至少需要：

```bash
pip install requests
```

## ffmpeg 配置

`ffmpeg` 不再通过命令行参数传入，统一从配置文件读取：

文件：

```text
configs/app_config.json
```

示例：

```json
{
  "ffmpeg_path": "/usr/bin/ffmpeg"
}
```

如果系统已将 `ffmpeg` 加入 PATH，也可以直接写：

```json
{
  "ffmpeg_path": "ffmpeg"
}
```

## 运行

```bash
python3 demo_speaker_test.py
python3 demo_pickup_test.py
python3 demo_supplement_light_test.py
```

## 平台差异

- Windows callback 使用 `WINFUNCTYPE`，Linux callback 使用 `CFUNCTYPE`
- Windows 动态库使用 `WinDLL`，Linux 动态库使用 `CDLL`
- Windows 默认 SDK 目录是 `libs/win64`，Linux 默认目录是 `libs/linux64`
- Windows 文件路径按 GBK 传给 HCNetSDK，Linux 文件路径按 UTF-8 传递
- HCNetSDK 的 `LONG` 固定按 32 位整数处理，避免 Linux 64 位系统中的 `long` 宽度差异
- 录音设备池使用跨平台文件锁和 PID 检查，可同时运行于 Windows 和 Linux

## 常见错误

找不到 Linux SDK：

```text
SDK path not found
```

检查 `HIKVISION_SDK_ROOT`，或将 Linux SDK 放到 `libs/linux64`。

找不到主库：

```text
Linux SDK library containing 'hcnetsdk' not found
```

确认 SDK 根目录中存在 `libhcnetsdk.so`。

动态库依赖缺失：

```text
cannot open shared object file
```

检查 `LD_LIBRARY_PATH`，并确认 HCNetSDK 配套 `.so` 文件完整。
