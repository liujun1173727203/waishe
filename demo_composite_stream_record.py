from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

from hikvision_isapi import HikvisionIsapiClient
from hikvision_voice import HikvisionSDKError, HikvisionVoiceSDK


def main() -> int:
    parser = argparse.ArgumentParser(description="Hikvision composite stream record demo")
    parser.add_argument("--host", required=True, help="device ip or hostname")
    parser.add_argument("--port", type=int, default=8000, help="sdk port, default 8000")
    parser.add_argument("--username", required=True, help="device username")
    parser.add_argument("--password", required=True, help="device password")
    parser.add_argument("--channel", type=int, default=0, help="preview channel, 0 means auto")
    parser.add_argument("--duration", type=int, default=10, help="record duration in seconds")
    parser.add_argument("--output", default="", help="output file path, default auto generated")
    parser.add_argument("--enable-log", action="store_true", help="enable sdk log output")
    args = parser.parse_args()

    sdk = HikvisionVoiceSDK()
    isapi = HikvisionIsapiClient(sdk)
    session = None
    recorder = None
    try:
        sdk.initialize(enable_log=args.enable_log)
        session = sdk.login(args.host, args.port, args.username, args.password)
        channel = args.channel or session.default_preview_channel

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
        print(f"recording started: channel={channel} output={recorder.file_path}")
        time.sleep(args.duration)
        print(f"recording finished after {args.duration}s")
    except HikvisionSDKError as exc:
        print(f"sdk call failed: {exc}", file=sys.stderr)
        return 1
    finally:
        if recorder is not None:
            recorder.stop()
        if session is not None:
            sdk.logout(session)
        sdk.cleanup()
    return 0


if __name__ == "__main__":
    sys.exit(main())
