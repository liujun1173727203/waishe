from __future__ import annotations

import argparse
import sys

from hikvision_voice import HikvisionSDKError, HikvisionVoiceSDK
from use_cases import VoiceTalkUseCases


def main() -> int:
    parser = argparse.ArgumentParser(description="Send a device fingerprint audio file to device via voice talk")
    parser.add_argument("--host", default="10.18.117.22", help="device ip or hostname")
    parser.add_argument("--port", type=int, default=8000, help="sdk port, default 8000")
    parser.add_argument("--username", default="admin", help="device username")
    parser.add_argument("--password", default="asdf!234", help="device password")
    parser.add_argument("--voice-channel", type=int, default=0, help="voice talk channel, 0 means auto")
    parser.add_argument("--duration", type=int, default=4, help="audio duration in seconds")
    parser.add_argument("--seed", type=int, default=None, help="optional random seed")
    parser.add_argument("--digit-sequence", default="", help="optional fixed speaker validation digit sequence")
    parser.add_argument("--test-tone-id", default="", help="optional id for this device's speaker validation tone")
    parser.add_argument("--enable-log", action="store_true", help="enable sdk log output")
    args = parser.parse_args()

    sdk = HikvisionVoiceSDK()
    session = None
    try:
        sdk.initialize(enable_log=args.enable_log)
        sdk.set_talk_mode(use_windows_api=False)
        session = sdk.login(args.host, args.port, args.username, args.password)

        use_cases = VoiceTalkUseCases(sdk)
        result = use_cases.play_random_audio_file(
            session=session,
            duration_seconds=args.duration,
            voice_channel=args.voice_channel or session.default_voice_channel,
            seed=args.seed,
            digit_sequence=args.digit_sequence or None,
            fingerprint_source=args.test_tone_id or args.host,
        )
        print(
            "random audio talk done:",
            f"file={result.file_path}",
            f"duration={result.duration_seconds}s",
            f"encode_type={result.encode_type}",
            f"voice_channel={result.voice_channel}",
            f"digit_sequence={result.digit_sequence}",
            f"bytes_sent={result.bytes_sent}",
            f"frames_sent={result.frames_sent}",
        )
    except HikvisionSDKError as exc:
        print(f"sdk call failed: {exc}", file=sys.stderr)
        return 1
    finally:
        if session is not None:
            sdk.logout(session)
        sdk.cleanup()
    return 0


if __name__ == "__main__":
    sys.exit(main())
