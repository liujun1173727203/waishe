from __future__ import annotations

import argparse
import sys
from datetime import datetime
from pathlib import Path

from hikvision_isapi import HikvisionIsapiClient
from hikvision_voice import HikvisionSDKError, HikvisionVoiceSDK
from use_cases import RecorderDeviceConfig, SpeakerTestUseCases
from video_analysis import RecordedVideoAnalyzer, VideoAnalysisError


class TimestampTee:
    def __init__(self, stream, log_file) -> None:
        self._stream = stream
        self._log_file = log_file
        self._buffer = ""

    def write(self, data: str) -> int:
        if not data:
            return 0
        self._buffer += data
        while "\n" in self._buffer:
            line, self._buffer = self._buffer.split("\n", 1)
            self._write_line(line, newline=True)
        return len(data)

    def flush(self) -> None:
        if self._buffer:
            self._write_line(self._buffer, newline=False)
            self._buffer = ""
        self._stream.flush()
        self._log_file.flush()

    def isatty(self) -> bool:
        return bool(getattr(self._stream, "isatty", lambda: False)())

    @property
    def encoding(self):
        return getattr(self._stream, "encoding", None)

    def _write_line(self, line: str, newline: bool) -> None:
        timestamped = f"[{datetime.now():%Y-%m-%d %H:%M:%S}] {line}"
        suffix = "\n" if newline else ""
        self._stream.write(timestamped + suffix)
        self._log_file.write(timestamped + suffix)


def _default_output_dir(host: str) -> Path:
    host_dir = "".join(char if char.isalnum() or char in "._-" else "_" for char in host)
    return Path.cwd() / "recordings" / "speaker_tests" / host_dir


def main() -> int:
    parser = argparse.ArgumentParser(description="Speaker test use case with composite stream recording and audio matching")
    parser.add_argument("--host", default="10.18.117.22", help="device ip or hostname")
    parser.add_argument("--port", type=int, default=8000, help="sdk port, default 8000")
    parser.add_argument("--username", default="admin", help="device username")
    parser.add_argument("--password", default="asdf!234", help="device password")
    parser.add_argument("--voice-channel", type=int, default=2, help="voice talk channel, 0 means auto")
    parser.add_argument("--record-channel", type=int, default=0, help="recorder device A preview channel, 0 means auto")
    parser.add_argument("--record-duration", type=int, default=10, help="record duration in seconds")
    parser.add_argument("--send-duration", type=int, default=4, help="generated audio duration in seconds")
    parser.add_argument("--similarity-threshold", type=float, default=0.8, help="match threshold, default 0.8")
    parser.add_argument("--seed", type=int, default=None, help="optional random seed")
    parser.add_argument("--digit-sequence", default="", help="optional fixed speaker validation digit sequence")
    parser.add_argument("--test-tone-id", default="", help="optional id for this device's speaker validation tone")
    parser.add_argument("--ffmpeg-path", default=r"D:\ffmpeg\ffmpeg-2026-05-28-git-7b46c6a2a3-essentials_build\bin\ffmpeg.exe", help="ffmpeg executable path")
    parser.add_argument("--recorder-host", default="10.40.230.23", help="recorder device A ip or hostname")
    parser.add_argument("--recorder-port", type=int, default=8000, help="recorder device A sdk port, default 8000")
    parser.add_argument("--recorder-username", default="admin", help="recorder device A username")
    parser.add_argument("--recorder-password", default="asdf!234", help="recorder device A password")
    parser.add_argument("--recorder-channel", type=int, default=0, help="recorder device A preview channel, 0 means auto")
    parser.add_argument("--recorder-voice-channel", type=int, default=1, help="recorder device A two-way audio channel")
    parser.add_argument("--enable-log", action="store_true", help="enable sdk log output")
    args = parser.parse_args()

    base_dir = _default_output_dir(args.host)
    base_dir.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    log_file_path = base_dir / f"speaker_test_log_{args.host.replace('.', '_')}_{timestamp}.log"

    original_stdout = sys.stdout
    original_stderr = sys.stderr
    log_file = log_file_path.open("a", encoding="utf-8")
    sys.stdout = TimestampTee(original_stdout, log_file)
    sys.stderr = TimestampTee(original_stderr, log_file)

    sdk = HikvisionVoiceSDK()
    recorder_sdk = HikvisionVoiceSDK()
    isapi = HikvisionIsapiClient(sdk)
    use_cases = SpeakerTestUseCases(
        sdk=sdk,
        isapi=isapi,
        recorder_sdk=recorder_sdk,
        analyzer=RecordedVideoAnalyzer(ffmpeg_path=args.ffmpeg_path),
        recorder_device=RecorderDeviceConfig(
            host=args.recorder_host,
            port=args.recorder_port,
            username=args.recorder_username,
            password=args.recorder_password,
            channel=args.recorder_channel,
            voice_channel=args.recorder_voice_channel,
        ),
    )

    session = None
    try:
        print(f"speaker test log file: {log_file_path}")
        sdk.initialize(enable_log=args.enable_log)
        recorder_sdk.initialize(enable_log=args.enable_log)
        session = sdk.login(args.host, args.port, args.username, args.password)

        result = use_cases.run_speaker_test(
            session=session,
            record_channel=args.record_channel or args.recorder_channel,
            voice_channel=args.voice_channel or session.default_voice_channel,
            record_duration_seconds=args.record_duration,
            send_duration_seconds=args.send_duration,
            similarity_threshold=args.similarity_threshold,
            seed=args.seed,
            digit_sequence=args.digit_sequence or None,
            fingerprint_source=args.test_tone_id or args.host,
        )

        print(
            "speaker test done:",
            f"record={result.record_file_path}",
            f"reference={result.reference_audio_path}",
            f"audio_input_supported={result.audio_input_status.supported}",
            f"audio_output_type={result.two_way_audio_status.output_type}",
            f"microphone_volume={result.two_way_audio_status.microphone_volume}/{result.two_way_audio_status.microphone_volume_max}",
            f"digit_sequence={result.talk_result.digit_sequence}",
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
        recorder_sdk.cleanup()
        sys.stdout.flush()
        sys.stderr.flush()
        sys.stdout = original_stdout
        sys.stderr = original_stderr
        log_file.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
