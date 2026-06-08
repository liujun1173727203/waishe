# 补光灯 IrLight 测试说明

## 测试目标

补光灯用例用于验证设备是否支持补光灯，以及补光灯在不同模式和不同强度限制下，画面亮度是否产生符合预期的变化。

## ISAPI 接口

设备图像通道能力接口：

```text
GET /ISAPI/System/capabilities?type=all
```

能力接口：

```text
GET /ISAPI/Image/channels/capabilities
```

配置接口：

```text
GET /ISAPI/Image/channels/<channel>
PUT /ISAPI/Image/channels/<channel>
```

能力判断规则：

- 响应中存在 `IrLight` 标签，才认为设备支持补光灯测试。
- 从 `IrLight/mode` 的 `opt` 属性读取全部可遍历模式。
- 从 `IrLight/brightnessLimit` 的 `min` 和 `max` 属性读取强度范围。

配置修改规则：

- 先通过 `GET /ISAPI/Image/channels/<channel>` 获取完整图像通道报文。
- 在完整报文中只修改 `IrLight/mode` 和 `IrLight/brightnessLimit`。
- 再通过 `PUT /ISAPI/Image/channels/<channel>` 写回完整报文。

## 图像通道发现

默认传入 `--channel 0` 时，测试程序自动发现并遍历图像通道：

1. 请求 `GET /ISAPI/System/capabilities?type=all`。
2. 如果存在 `supportImageChannel`，读取其 `opt` 值作为实际图像配置通道。
3. 如果不存在 `supportImageChannel`，请求 `GET /ISAPI/Streaming/channels`，使用 `StreamingChannel/id` 作为回退通道。

例如：

```xml
<supportImageChannel opt="1,2,3,4,5,6">true</supportImageChannel>
```

表示需要依次测试：

```text
/ISAPI/Image/channels/1
/ISAPI/Image/channels/2
/ISAPI/Image/channels/3
/ISAPI/Image/channels/4
/ISAPI/Image/channels/5
/ISAPI/Image/channels/6
```

图像配置通道可能与编码码流通道不一致。补光灯测试使用图像通道修改配置，抓图默认使用当前遍历的图像通道。拼接设备可通过 `--capture-channel` 显式指定固定抓图通道。

```powershell
python .\demo_supplement_light_test.py --channel 0 --capture-channel 1
```

## SupplementLight 混合补光灯遍历

每个图像通道通过以下接口读取补光灯能力：

```text
GET /ISAPI/Image/channels/<channel>/capabilities
```

在切换补光灯前，必须先检查并切换日夜模式：

1. 从能力报文的 `IrcutFilter/IrcutFilterType` 标签读取 `opt`。
2. 如果 `opt` 中包含 `night`，通过 `GET /ISAPI/Image/channels/<channel>` 获取完整配置。
3. 将完整配置中 `IrcutFilter/IrcutFilterType` 修改为 `night`。
4. 通过 `PUT /ISAPI/Image/channels/<channel>` 写回配置。
5. 等待设备切换稳定后再开始补光灯模式和亮度测试。
6. 测试结束后恢复原始 `IrcutFilterType`。

如果设备不支持 `night`，当前通道补光灯测试失败，不继续执行补光灯切换。

从 `SupplementLight/supplementLightMode` 的 `opt` 中识别灯类型：

```text
colorVuWhiteLight
irLight
close
```

如果同时支持 `colorVuWhiteLight` 和 `irLight`，两种灯都必须分别执行功能有效性和效果有效性测试。其他模式如 `eventIntelligence` 不参与本轮灯类型遍历。

如果 `mixedLightBrightnessRegulatMode` 的 `opt` 包含 `manual`，测试过程中统一配置为：

```xml
<mixedLightBrightnessRegulatMode>manual</mixedLightBrightnessRegulatMode>
```

白光灯强度测试同时设置：

```xml
<highWhiteLightBrightness>50</highWhiteLightBrightness>
<lowWhiteLightBrightness>50</lowWhiteLightBrightness>
```

红外灯强度测试同时设置：

```xml
<highIrLightBrightness>50</highIrLightBrightness>
<lowIrLightBrightness>50</lowIrLightBrightness>
```

每种灯的功能有效性测试：

1. 如果支持 `close`，设置为 `close` 后抓取开启前图片。
2. 切换到当前灯类型并设置最大亮度，抓取开启后图片。
3. 开启后与开启前亮度差达到 `on_threshold`，判定功能有效。

每种灯的效果有效性测试：

1. 在当前灯类型下遍历该灯亮度能力的 `min / middle / max`。
2. 每档配置后抓图并分析亮度。
3. 相邻档位亮度差均达到 `level_threshold`，判定效果有效。

测试结束后恢复通道测试前的 `SupplementLight` 配置。

示例配置片段：

```xml
<IrLight version="2.0" xmlns="http://www.hikvision.com/ver20/XMLSchema">
    <mode>auto</mode>
    <brightnessLimit>50</brightnessLimit>
</IrLight>
```

## 遍历策略

对 `mode opt` 中的每一种模式都执行一轮测试。

每种模式下遍历三个强度点：

- `min`：`brightnessLimit` 最小值。
- `middle`：`(min + max) // 2`。
- `max`：`brightnessLimit` 最大值。

每个设置点执行流程：

1. PUT 修改 `IrLight/mode` 和 `IrLight/brightnessLimit`。
2. 等待 `--settle-seconds`，默认 `2s`。
3. 抓图，默认优先 JPEG 抓图，失败后 fallback 到取流抓图。
4. 使用 ffmpeg 分析抓图中心 `40% x 40%` ROI 的灰度均值。

## 判定逻辑

补光灯测试分为两个结论：

- 功能有效：通过开启补光灯前和开启补光灯后的抓图亮度差判断。
- 效果有效：通过同一个补光灯模式下不同强度是否递增判断。

最终用例通过条件：

```text
passed = function_pass and effect_pass
```

## 功能有效判断

优先使用能力中的关闭模式作为开启前状态，例如 `close/off`。

如果设备能力中没有关闭模式，例如只有 `auto`，则使用同一模式下的最小 `brightnessLimit` 作为开启前状态，最大 `brightnessLimit` 作为开启后状态。

计算：

```text
brightness_delta = brightness(after) - brightness(before)
```

如果：

```text
brightness_delta >= on_threshold
```

则认为补光灯功能有效。

默认：

```text
on_threshold = 10.0
```

## 效果有效判断

同一个 mode 下，分别计算：

```text
min_to_middle_delta = brightness(middle) - brightness(min)
middle_to_max_delta = brightness(max) - brightness(middle)
```

如果两段亮度差都大于等于 `--level-threshold`，则该 mode 判定通过。

所有参与强度递增测试的 mode 都通过，则认为补光灯效果有效。

```text
effect_pass = all(mode_result.passed for mode_result in mode_results)
```

## 输出文件

默认输出目录：

```text
recordings/supplement_light_tests/<device-ip>/
```

抓图文件名包含 mode 和 brightnessLimit：

```text
supplement_light_ir_<mode>_<brightnessLimit>_<ip>_<timestamp>.jpg
```

如果 JPEG 抓图失败并 fallback 到取流抓图，实际输出可能为 `_stream.bmp`。

## 日志结论

最终日志会输出：

- `mode`
- `min/middle/max` 三个强度点的图像亮度
- `min_to_middle_delta`
- `middle_to_max_delta`
- `threshold`
- `passed`

用例结束后会尽量恢复测试前的 `IrLight.mode` 和 `IrLight.brightnessLimit`。
