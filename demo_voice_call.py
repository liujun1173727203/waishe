from __future__ import annotations

import argparse
import sys
import time

from hikvision_voice import AUDIO_FLAG_LOCAL, HikvisionSDKError, HikvisionVoiceSDK


def main() -> int:
    parser = argparse.ArgumentParser(description="PC <-> Hikvision device voice talk demo")
    parser.add_argument("--host", default="10.41.203.51", help="device ip or hostname")
    parser.add_argument("--port", type=int, default=8000, help="sdk port, default 8000")
    parser.add_argument("--username", default="admin", help="device username")
    parser.add_argument("--password", default="abcd1234", help="device password")
    parser.add_argument("--voice-channel", type=int, default=1, help="voice talk channel, 0 means auto")
    parser.add_argument("--windows-api", action="store_true", help="use legacy windows api talk mode")
    parser.add_argument("--pcm-callback", action="store_true", help="request raw pcm callback instead of encoded data")
    parser.add_argument("--enable-log", action="store_true", help="enable sdk log output")
    args = parser.parse_args()

    sdk = HikvisionVoiceSDK()
    session = None
    call = None
    try:
        sdk.initialize(enable_log=args.enable_log)
        sdk.set_talk_mode(use_windows_api=args.windows_api)

        session = sdk.login(args.host, args.port, args.username, args.password)
        compress = sdk.get_current_audio_compress(session)
        print(
            "login ok:",
            f"default_voice_channel={session.default_voice_channel}",
            f"encode_type={compress.encode_type}",
            f"sampling_rate={compress.sampling_rate}",
            f"bit_rate={compress.bit_rate}",
        )

        def on_audio(data: bytes, audio_flag: int) -> None:
            source = "local-mic" if audio_flag == AUDIO_FLAG_LOCAL else "device"
            print(f"[audio] source={source} bytes={len(data)}")

        voice_channel = args.voice_channel or session.default_voice_channel
        call = sdk.start_call(
            session=session,
            voice_channel=voice_channel,
            need_pcm_callback=args.pcm_callback,
            audio_callback=on_audio,
        )
        print(
            f"voice talk started on channel {voice_channel}, "
            f"recording device audio to recordings/{args.host}/, press Ctrl+C to stop"
        )

        while True:
            time.sleep(1)
    except HikvisionSDKError as exc:
        print(f"sdk call failed: {exc}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        print("\nstopping...")
    finally:
        if call is not None:
            call.stop()
        if session is not None:
            sdk.logout(session)
        sdk.cleanup()
    return 0


if __name__ == "__main__":
    sys.exit(main())
