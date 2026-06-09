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


def _print_result(result) -> None:
    print(
        "supplement light test done:",
        f"channel={result.channel}",
        f"passed={result.passed}",
        f"function_pass={result.function_pass}",
        f"effect_pass={result.effect_pass}",
        f"tested_modes={','.join(result.tested_modes)}",
        f"original_ircut={result.original_ircut_status.filter_type}",
        f"night_ircut={result.night_ircut_status.filter_type}",
        f"mode_result_count={len(result.mode_results)}",
        f"capture_count={len(result.capture_results)}",
        f"on_threshold={result.on_threshold:.2f}",
        f"level_threshold={result.level_threshold:.2f}",
    )
    for function_result in result.function_results:
        print(
            "supplement light function result:",
            f"channel={result.channel}",
            f"mode={function_result.mode}",
            f"before_mode={function_result.before_mode}",
            f"before_brightness={function_result.before_brightness_limit}",
            f"before_level={function_result.before_image_brightness:.2f}",
            f"after_brightness={function_result.after_brightness_limit}",
            f"after_level={function_result.after_image_brightness:.2f}",
            f"delta={function_result.brightness_delta:.2f}",
            f"threshold={function_result.threshold:.2f}",
            f"passed={function_result.passed}",
        )
    for mode_result in result.mode_results:
        print(
            "supplement light mode result:",
            f"channel={result.channel}",
            f"mode={mode_result.mode}",
            f"min={mode_result.min_limit}:{mode_result.min_image_brightness:.2f}",
            f"middle={mode_result.middle_limit}:{mode_result.middle_image_brightness:.2f}",
            f"max={mode_result.max_limit}:{mode_result.max_image_brightness:.2f}",
            f"min_to_middle_delta={mode_result.min_to_middle_delta:.2f}",
            f"middle_to_max_delta={mode_result.middle_to_max_delta:.2f}",
            f"passed={mode_result.passed}",
        )


def main() -> int:
    parser = argparse.ArgumentParser(description="Supplement light capability and effect test")
    parser.add_argument("--host", default="10.41.203.66", help="device IP or hostname")
    parser.add_argument("--port", type=int, default=8000, help="SDK port")
    parser.add_argument("--username", default="admin", help="device username")
    parser.add_argument("--password", default="asdf!234", help="device password")
    parser.add_argument("--channel", type=int, default=0, help="image channel, 0 means auto scan")
    parser.add_argument("--capture-channel", type=int, default=0, help="capture channel, 0 means current image channel")
    parser.add_argument("--stream-type", choices=["main", "sub"], default="main", help="stream type used for capture")
    parser.add_argument("--settle-seconds", type=float, default=2.0, help="wait time after each config change")
    parser.add_argument("--on-threshold", type=float, default=10.0, help="function threshold")
    parser.add_argument("--level-threshold", type=float, default=5.0, help="effect threshold")
    parser.add_argument("--enable-log", action="store_true", help="enable SDK log")
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
        print(f"supplement light log file: {log_file_path}")
        sdk.initialize(enable_log=args.enable_log)
        session = sdk.login(args.host, args.port, args.username, args.password)
        isapi = HikvisionIsapiClient(sdk)
        use_cases = SupplementLightUseCases(sdk=sdk, isapi=isapi)
        stream_type = STREAM_TYPE_MAIN if args.stream_type == "main" else STREAM_TYPE_SUB

        if args.channel:
            channel_ids = (args.channel,)
            channel_source = "cli"
            channel_path = "--channel"
        else:
            channel_status = isapi.get_supported_image_channel_ids(session)
            channel_ids = channel_status.channel_ids
            channel_source = channel_status.source
            channel_path = channel_status.request_path

        print(
            "supplement light channels:",
            f"channels={','.join(str(channel) for channel in channel_ids)}",
            f"source={channel_source}",
            f"path={channel_path}",
            f"capture_channel={args.capture_channel or 'current-image-channel'}",
        )

        results = []
        failed_channels: list[int] = []
        for channel_id in channel_ids:
            capture_channel = args.capture_channel or channel_id
            print(f"supplement light channel start: channel={channel_id} capture_channel={capture_channel}")
            try:
                result = use_cases.run_supplement_light_test(
                    session=session,
                    channel=channel_id,
                    capture_channel=capture_channel,
                    output_dir=base_dir,
                    settle_seconds=args.settle_seconds,
                    on_threshold=args.on_threshold,
                    level_threshold=args.level_threshold,
                    stream_type=stream_type,
                )
                results.append(result)
                _print_result(result)
            except (HikvisionSDKError, FileNotFoundError, ValueError) as exc:
                failed_channels.append(channel_id)
                print(
                    f"supplement light channel failed: channel={channel_id} error={exc}",
                    file=sys.stderr,
                )

        passed = bool(results) and not failed_channels and all(result.passed for result in results)
        print(
            "supplement light final:",
            f"passed={passed}",
            f"success_channels={','.join(str(result.channel) for result in results) or 'none'}",
            f"failed_channels={','.join(str(channel) for channel in failed_channels) or 'none'}",
        )
        return 0 if passed else 1
    except (HikvisionSDKError, FileNotFoundError, ValueError) as exc:
        print(f"supplement light failed: {exc}", file=sys.stderr)
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
