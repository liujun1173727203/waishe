from __future__ import annotations

import argparse
import sys

from hikvision_isapi import HikvisionIsapiClient
from hikvision_voice import HikvisionSDKError, HikvisionVoiceSDK
from use_cases import SpeakerTestUseCases
from video_analysis import RecordedVideoAnalyzer, VideoAnalysisError


def main() -> int:
    parser = argparse.ArgumentParser(description="Speaker test use case with composite stream recording and audio matching")
    parser.add_argument("--host", default="10.41.203.51", help="device ip or hostname")
    parser.add_argument("--port", type=int, default=8000, help="sdk port, default 8000")
    parser.add_argument("--username", default="admin", help="device username")
    parser.add_argument("--password", default="abcd1234", help="device password")
    parser.add_argument("--voice-channel", type=int, default=1, help="voice talk channel, 0 means auto")
    parser.add_argument("--record-channel", type=int, default=1, help="record channel, 0 means auto")
    parser.add_argument("--record-duration", type=int, default=10, help="record duration in seconds")
    parser.add_argument("--send-duration", type=int, default=3, help="generated audio duration in seconds")
    parser.add_argument("--similarity-threshold", type=float, default=0.8, help="match threshold, default 0.8")
    parser.add_argument("--seed", type=int, default=None, help="optional random seed")
    parser.add_argument("--ffmpeg-path", default="ffmpeg", help="ffmpeg executable path")
    parser.add_argument("--enable-log", action="store_true", help="enable sdk log output")
    args = parser.parse_args()

    sdk = HikvisionVoiceSDK()
    isapi = HikvisionIsapiClient(sdk)
    use_cases = SpeakerTestUseCases(
        sdk=sdk,
        isapi=isapi,
        analyzer=RecordedVideoAnalyzer(ffmpeg_path=args.ffmpeg_path),
    )

    session = None
    try:
        sdk.initialize(enable_log=args.enable_log)
        session = sdk.login(args.host, args.port, args.username, args.password)

        result = use_cases.run_speaker_test(
            session=session,
            record_channel=args.record_channel or session.default_preview_channel,
            voice_channel=args.voice_channel or session.default_voice_channel,
            record_duration_seconds=args.record_duration,
            send_duration_seconds=args.send_duration,
            similarity_threshold=args.similarity_threshold,
            seed=args.seed,
        )

        print(
            "speaker test done:",
            f"record={result.record_file_path}",
            f"reference={result.reference_audio_path}",
            f"audio_input_supported={result.audio_input_status.supported}",
            f"input_volume={result.volume_status.input_status.value}/{result.volume_status.input_status.maximum}",
            f"output_volume={result.volume_status.output_status.value}/{result.volume_status.output_status.maximum}",
            f"has_sound={result.sound_result.has_sound}",
            f"match={result.match_result.matched}",
            f"score={result.match_result.best_score:.4f}",
            f"threshold={result.match_result.threshold:.2f}",
        )
    except (HikvisionSDKError, VideoAnalysisError, FileNotFoundError, ValueError) as exc:
        print(f"use case failed: {exc}", file=sys.stderr)
        return 1
    finally:
        if session is not None:
            sdk.logout(session)
        sdk.cleanup()
    return 0


if __name__ == "__main__":
    sys.exit(main())
