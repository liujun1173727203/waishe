from __future__ import annotations

import argparse
import sys
import time
from datetime import datetime
from pathlib import Path

from hikvision_voice import HikvisionSDKError, HikvisionVoiceSDK


def main() -> int:
    parser = argparse.ArgumentParser(description="Hikvision real-time stream record demo")
    parser.add_argument("--host", required=True, help="device ip or hostname")
    parser.add_argument("--port", type=int, default=8000, help="sdk port, default 8000")
    parser.add_argument("--username", required=True, help="device username")
    parser.add_argument("--password", required=True, help="device password")
    parser.add_argument("--channel", type=int, default=0, help="preview channel, 0 means auto")
    parser.add_argument("--duration", type=int, default=30, help="record duration in seconds")
    parser.add_argument("--output", default="", help="output file path, default auto generated")
    parser.add_argument("--enable-log", action="store_true", help="enable sdk log output")
    args = parser.parse_args()

    sdk = HikvisionVoiceSDK()
    session = None
    recorder = None
    try:
        sdk.initialize(enable_log=args.enable_log)
        session = sdk.login(args.host, args.port, args.username, args.password)

        channel = args.channel or session.default_preview_channel
        output_path = Path(args.output) if args.output else (
            Path("recordings")
            / "streams"
            / args.host
            / f"stream_ch{channel}_{datetime.now():%Y%m%d_%H%M%S}.mp4"
        )

        recorder = sdk.start_stream_record(
            session=session,
            file_path=output_path,
            channel=channel,
        )
        print(f"recording started: channel={channel} output={output_path}")
        time.sleep(args.duration)
        print("recording finished")
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
