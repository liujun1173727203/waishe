# FastAPI 接口说明

项目使用 `FastAPI` 封装测试接口。每个测试用例对应一个独立接口，调用后会创建异步任务，调用方再通过任务查询接口获取执行结果、日志和附件。

## 配置文件

`ffmpeg` 不再通过接口参数传入，统一从配置文件读取：

文件：[configs/app_config.json](/d:/workplace/py-workplace/waishezidonghua/configs/app_config.json)

```json
{
  "ffmpeg_path": "ffmpeg"
}
```

可以配置为：

- `ffmpeg`
- `D:\\ffmpeg\\bin\\ffmpeg.exe`
- `/usr/bin/ffmpeg`

## 启动服务

先安装依赖：

```powershell
pip install fastapi uvicorn
```

启动命令：

```powershell
python .\demo_http_api.py --host 0.0.0.0 --port 18080
```

也可以直接用：

```powershell
uvicorn demo_http_api:app --host 0.0.0.0 --port 18080
```

启动后可访问：

```text
http://127.0.0.1:18080/docs
http://127.0.0.1:18080/redoc
```

## 基础接口

### 健康检查

```http
GET /health
```

### 查询全部测试用例

```http
GET /api/testcases
```

### 查询单个测试用例参数

```http
GET /api/testcases/{case_id}
```

### 查询全部任务

```http
GET /api/tasks
```

### 查询单个任务详情

```http
GET /api/tasks/{task_id}
```

返回内容包含：

- 任务执行结果 `result`
- 任务执行日志 `execution_log`
- 附件清单 `attachments`
- 标准输出 `stdout`
- 标准错误 `stderr`

### 下载附件

```http
GET /api/tasks/{task_id}/attachments/{attachment_id}
```

### 查询当前活动录音设备 IP

```http
GET /api/recorder-devices/active
```

## 异步任务提交

每个测试用例都通过 `POST` 提交，例如：

```http
POST /api/testcases/speaker_test/run
POST /api/testcases/pickup_test/run
POST /api/testcases/supplement_light_test/run
```

返回结果是“任务已接收”，不会同步等待脚本执行完成。

## 请求体格式

```json
{
  "arguments": {
    "host": "10.18.117.22",
    "username": "admin",
    "password": "asdf!234"
  },
  "recorder_device_ip": "10.40.230.23",
  "timeout_seconds": 900
}
```

字段说明：

- `arguments`：原命令行参数对应的 JSON 对象
- `recorder_device_ip`：录音设备 IP，用于并发接入校验
- `timeout_seconds`：任务超时秒数，可选

## 录音设备 IP 限制

系统会维护“当前正在执行任务”的录音设备 IP 集合：

- 同时最多允许 `5` 个不同录音设备 IP 处于执行中
- 提交第 `6` 个不同录音设备 IP 时，接口会直接拒绝
- 同一个录音设备 IP 可以重复提交多个任务

当前实现规则：

- 如果请求体传了 `recorder_device_ip`，优先用它做校验
- 对 `stream_record`、`composite_stream_record`、`pickup_test`、`linein_test`，如果没有传 `recorder_device_ip`，会回退使用 `arguments.host`
- 对 `speaker_test`、`lineout_test` 这类通过录音池选择录音设备的用例，建议显式传 `recorder_device_ip`

## 任务状态

任务状态可能为：

- `pending`
- `running`
- `succeeded`
- `failed`
- `timeout`

## 任务详情字段

任务详情会返回：

- `task_id`
- `case_id`
- `status`
- `success`
- `exit_code`
- `error`
- `result`
- `stdout`
- `stderr`
- `execution_log`
- `attachments`
- `created_at`
- `started_at`
- `finished_at`
- `duration_seconds`

其中：

- `result`：提炼后的执行结果摘要
- `execution_log`：优先读取任务日志文件，读取不到时回退为 `stdout + stderr`
- `attachments`：任务执行期间新增或变更的截图、录像、音频、日志等附件

## 附件字段

每个附件对象会返回：

- `attachment_id`
- `name`
- `path`
- `relative_path`
- `category`
- `size_bytes`
- `modified_at`
- `media_type`
- `download_url`

`category` 可能为：

- `log`
- `image`
- `video`
- `audio`
- `file`

## 调用示例

### 1. 提交扬声器测试任务

```powershell
$resp = Invoke-RestMethod `
  -Uri "http://127.0.0.1:18080/api/testcases/speaker_test/run" `
  -Method Post `
  -ContentType "application/json" `
  -Body (@{
    arguments = @{
      host = "10.18.117.22"
      username = "admin"
      password = "asdf!234"
      recorder_pool_config = ".\configs\recorder_device_pool.json"
    }
    recorder_device_ip = "10.40.230.23"
    timeout_seconds = 900
  } | ConvertTo-Json -Depth 6)
```

### 2. 查询任务结果

```powershell
Invoke-RestMethod `
  -Uri ("http://127.0.0.1:18080/api/tasks/" + $resp.task_id) `
  -Method Get
```

### 3. 提交补光灯测试任务

```powershell
Invoke-RestMethod `
  -Uri "http://127.0.0.1:18080/api/testcases/supplement_light_test/run" `
  -Method Post `
  -ContentType "application/json" `
  -Body (@{
    arguments = @{
      host = "10.41.203.66"
      username = "admin"
      password = "asdf!234"
      channel = 1
    }
    timeout_seconds = 600
  } | ConvertTo-Json -Depth 6)
```

## 当前已封装用例

- `speaker_test`
- `lineout_test`
- `pickup_test`
- `linein_test`
- `supplement_light_test`
- `random_audio_talk`
- `capture_picture`
- `stream_record`
- `composite_stream_record`
- `video_analysis`
- `continuous_frequency_audio`
- `multi_device_audio_match`
- `mix_five_device_audio`
