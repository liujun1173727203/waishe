from __future__ import annotations

import argparse
import sys
from datetime import datetime
from pathlib import Path

from hikvision_isapi import HikvisionIsapiClient
from hikvision_voice import HikvisionSDKError, HikvisionVoiceSDK, STREAM_TYPE_MAIN, STREAM_TYPE_SUB
from use_cases import SupplementLightUseCases


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
    return Path.cwd() / "recordings" / "supplement_light_tests" / host_dir


def main() -> int:
    parser = argparse.ArgumentParser(description="Supplement light test with image brightness validation")
    parser.add_argument("--host", default="10.18.117.22", help="device ip or hostname")
    parser.add_argument("--port", type=int, default=8000, help="sdk port")
    parser.add_argument("--username", default="admin", help="device username")
    parser.add_argument("--password", default="asdf!234", help="device password")
    parser.add_argument("--channel", type=int, default=0, help="image/preview channel, 0 means auto")
    parser.add_argument("--stream-type", choices=["main", "sub"], default="main", help="stream type for capture fallback")
    parser.add_argument("--settle-seconds", type=float, default=2.0, help="wait after each light setting")
    parser.add_argument("--on-threshold", type=float, default=10.0, help="brightness delta threshold for light-on")
    parser.add_argument("--level-threshold", type=float, default=5.0, help="brightness delta threshold between levels")
    parser.add_argument("--ffmpeg-path", default=r"D:\ffmpeg\ffmpeg-2026-05-28-git-7b46c6a2a3-essentials_build\bin\ffmpeg.exe", help="ffmpeg executable path")
    parser.add_argument("--enable-log", action="store_true", help="enable sdk log output")
    args = parser.parse_args()

    base_dir = _default_output_dir(args.host)
    base_dir.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    log_file_path = base_dir / f"supplement_light_test_log_{args.host.replace('.', '_')}_{timestamp}.log"

    original_stdout = sys.stdout
    original_stderr = sys.stderr
    log_file = log_file_path.open("a", encoding="utf-8")
    sys.stdout = TimestampTee(original_stdout, log_file)
    sys.stderr = TimestampTee(original_stderr, log_file)

    sdk = HikvisionVoiceSDK()
    session = None
    try:
        print(f"supplement light test log file: {log_file_path}")
        sdk.initialize(enable_log=args.enable_log)
        session = sdk.login(args.host, args.port, args.username, args.password)
        isapi = HikvisionIsapiClient(sdk)
        use_cases = SupplementLightUseCases(sdk=sdk, isapi=isapi, ffmpeg_path=args.ffmpeg_path)
        stream_type = STREAM_TYPE_MAIN if args.stream_type == "main" else STREAM_TYPE_SUB
        result = use_cases.run_supplement_light_test(
            session=session,
            channel=args.channel or session.default_preview_channel,
            output_dir=base_dir,
            settle_seconds=args.settle_seconds,
            on_threshold=args.on_threshold,
            level_threshold=args.level_threshold,
            stream_type=stream_type,
        )
        print(
            "supplement light test done:",
            f"passed={result.passed}",
            f"baseline={result.baseline_brightness:.2f}",
            f"on_delta={result.on_delta:.2f}",
            f"level_0_to_50_delta={result.level_0_to_50_delta:.2f}",
            f"level_50_to_100_delta={result.level_50_to_100_delta:.2f}",
            f"light_on_pass={result.light_on_pass}",
            f"level_pass={result.level_pass}",
        )
        return 0 if result.passed else 1
    except (HikvisionSDKError, FileNotFoundError, ValueError) as exc:
        print(f"use case failed: {exc}", file=sys.stderr)
        return 1
    finally:
        if session is not None:
            sdk.logout(session)
        sdk.cleanup()
        sys.stdout.flush()
        sys.stderr.flush()
        sys.stdout = original_stdout
        sys.stderr = original_stderr
        log_file.close()


if __name__ == "__main__":
    sys.exit(main())
