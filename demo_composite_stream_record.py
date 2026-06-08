from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

from hikvision_isapi import HikvisionIsapiClient
from hikvision_voice import HikvisionSDKError, HikvisionVoiceSDK
from use_cases import RecorderDevicePool, RecorderDevicePoolError


POST_STOP_SETTLE_SECONDS = 3.0


def main() -> int:
    """
    作用：作为命令行入口，解析参数并编排完整执行流程。
    执行步骤：
    1. 解析输入参数并准备依赖对象。
    2. 按业务流程顺序执行核心步骤。
    3. 输出日志、执行结果或退出码。
    """
    parser = argparse.ArgumentParser(description="Hikvision composite stream record demo")
    parser.add_argument("--host", required=True, help="device ip or hostname")
    parser.add_argument("--port", type=int, default=8000, help="sdk port, default 8000")
    parser.add_argument("--channel", type=int, default=0, help="preview channel, 0 means auto")
    parser.add_argument("--duration", type=int, default=10, help="record duration in seconds")
    parser.add_argument("--output", default="", help="output file path, default auto generated")
    parser.add_argument("--recorder-pool-config", default=str(Path.cwd() / "configs" / "recorder_device_pool.json"), help="recorder device pool config path")
    parser.add_argument("--recorder-pool-wait-seconds", type=float, default=300.0, help="max wait time for recorder device pool lease")
    parser.add_argument("--enable-log", action="store_true", help="enable sdk log output")
    args = parser.parse_args()

    sdk = HikvisionVoiceSDK()
    isapi = HikvisionIsapiClient(sdk)
    recorder_pool = RecorderDevicePool(args.recorder_pool_config)
    session = None
    recorder = None
    try:
        sdk.initialize(enable_log=args.enable_log)
        with recorder_pool.acquire_for_device(args.host, args.port, wait_seconds=args.recorder_pool_wait_seconds) as lease:
            recorder_config = lease.recorder_device
            print(
                "recorder device pool lease acquired:",
                f"device_id={lease.device_id}",
                f"host={recorder_config.host}:{recorder_config.port}",
            )
            session = sdk.login(
                recorder_config.host,
                recorder_config.port,
                recorder_config.username,
                recorder_config.password,
            )
            channel = args.channel or recorder_config.channel or session.default_preview_channel

            volume_status = isapi.ensure_audio_input_output_volume_max(session=session)
            status = isapi.ensure_composite_stream_recording_enabled(session=session, channel=channel)
            if not status.supported:
                raise HikvisionSDKError(
                    f"device channel {channel} does not support composite stream recording",
                    api_name="GET /ISAPI/Streaming/channels/<trackStreamID>/capabilities",
                )

            output_path = Path(args.output) if args.output else None
            recorder = sdk.start_stream_record(
                session=session,
                file_path=output_path,
                channel=channel,
            )
            print(
                "composite stream ready:",
                f"trackStreamID={status.track_stream_id}",
                f"audio_enabled={status.audio_enabled}",
                f"changed={status.changed}",
            )
            print(
                "audio volume:",
                f"input={volume_status.input_status.value}/{volume_status.input_status.maximum}",
                f"output={volume_status.output_status.value}/{volume_status.output_status.maximum}",
            )
            print(f"recording started: channel={channel} output={recorder.file_path}")
            time.sleep(args.duration)
            print(f"recording finished after {args.duration}s")
            print(
                "stopping recorder:",
                f"handle={recorder.handle}",
                f"received_bytes={recorder.received_bytes}",
                f"file_size_bytes={recorder.file_size_bytes}",
            )
            recorder.stop_save_real_data()
            print("NET_DVR_StopSaveRealData done")
            recorder.stop_real_play()
            print("NET_DVR_StopRealPlay done")
            time.sleep(POST_STOP_SETTLE_SECONDS)
            print(
                "recorder stopped:",
                f"waited={POST_STOP_SETTLE_SECONDS:.2f}s",
                f"file_size_bytes={recorder.file_size_bytes}",
            )
            recorder = None
            if session is not None:
                sdk.logout(session)
                session = None
            print(f"recorder device pool lease released: device_id={lease.device_id}")
    except (HikvisionSDKError, RecorderDevicePoolError, FileNotFoundError, ValueError) as exc:
        print(f"sdk call failed: {exc}", file=sys.stderr)
        return 1
    finally:
        if recorder is not None:
            print(
                "stopping recorder:",
                f"handle={recorder.handle}",
                f"received_bytes={recorder.received_bytes}",
                f"file_size_bytes={recorder.file_size_bytes}",
            )
            recorder.stop_save_real_data()
            print("NET_DVR_StopSaveRealData done")
            recorder.stop_real_play()
            print("NET_DVR_StopRealPlay done")
            time.sleep(POST_STOP_SETTLE_SECONDS)
            print(
                "recorder stopped:",
                f"waited={POST_STOP_SETTLE_SECONDS:.2f}s",
                f"file_size_bytes={recorder.file_size_bytes}",
            )
        if session is not None:
            sdk.logout(session)
        sdk.cleanup()
    return 0


if __name__ == "__main__":
    sys.exit(main())
