from __future__ import annotations

import argparse
import sys
from datetime import datetime
from pathlib import Path

from hikvision_isapi import HikvisionIsapiClient
from hikvision_voice import HikvisionSDKError, HikvisionVoiceSDK
from use_cases import PickupTestUseCases, PlaybackDeviceConfig, RecorderDevicePool, RecorderDevicePoolError
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
    return Path.cwd() / "recordings" / "pickup_tests" / host_dir


def main() -> int:
    parser = argparse.ArgumentParser(description="Pickup test: record test device MicIn while playback device A plays audio")
    parser.add_argument("--host", default="10.18.117.22", help="test device ip or hostname")
    parser.add_argument("--port", type=int, default=8000, help="test device sdk port")
    parser.add_argument("--username", default="admin", help="test device username")
    parser.add_argument("--password", default="asdf!234", help="test device password")
    parser.add_argument("--record-channel", type=int, default=1, help="test device preview channel, 0 means auto")
    parser.add_argument("--record-duration", type=int, default=10, help="record duration in seconds")
    parser.add_argument("--send-duration", type=int, default=4, help="generated audio duration in seconds")
    parser.add_argument("--similarity-threshold", type=float, default=0.7, help="match threshold, default 0.7")
    parser.add_argument("--seed", type=int, default=None, help="optional random seed")
    parser.add_argument("--test-tone-id", default="", help="optional id for playback device A continuous-frequency validation tone")
    parser.add_argument("--test-device-input-type", default="MicIn", help="test device audioInputType, e.g. MicIn or LineIn")
    parser.add_argument("--test-device-output-type", default="Speaker", help="test device audioOutputType, default Speaker")
    parser.add_argument(
        "--audio-compression-types",
        default="auto",
        help="comma separated playback device A audioCompressionType list, default auto means iterate supported options",
    )
    parser.add_argument("--ffmpeg-path", default=r"D:\ffmpeg\ffmpeg-2026-05-28-git-7b46c6a2a3-essentials_build\bin\ffmpeg.exe", help="ffmpeg executable path")
    parser.add_argument("--playback-host", default="10.40.230.23", help="playback device A ip or hostname")
    parser.add_argument("--playback-port", type=int, default=8000, help="playback device A sdk port")
    parser.add_argument("--playback-username", default="admin", help="playback device A username")
    parser.add_argument("--playback-password", default="asdf!234", help="playback device A password")
    parser.add_argument("--playback-voice-channel", type=int, default=1, help="playback device A voice talk channel")
    parser.add_argument(
        "--recorder-pool-config",
        default=str(Path.cwd() / "configs" / "recorder_device_pool.json"),
        help="recorder device pool config path; test device recording must be in this pool",
    )
    parser.add_argument(
        "--recorder-pool-wait-seconds",
        type=float,
        default=300.0,
        help="max wait time for recorder device pool lease, default 300 seconds",
    )
    parser.add_argument("--enable-log", action="store_true", help="enable sdk log output")
    args = parser.parse_args()

    base_dir = _default_output_dir(args.host)
    base_dir.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    log_file_path = base_dir / f"pickup_test_log_{args.host.replace('.', '_')}_{timestamp}.log"

    original_stdout = sys.stdout
    original_stderr = sys.stderr
    log_file = log_file_path.open("a", encoding="utf-8")
    sys.stdout = TimestampTee(original_stdout, log_file)
    sys.stderr = TimestampTee(original_stderr, log_file)

    sdk = HikvisionVoiceSDK()
    playback_sdk = HikvisionVoiceSDK()
    isapi = HikvisionIsapiClient(sdk)
    recorder_pool = RecorderDevicePool(args.recorder_pool_config)
    analyzer = RecordedVideoAnalyzer(ffmpeg_path=args.ffmpeg_path)

    session = None
    try:
        print(f"pickup test log file: {log_file_path}")
        print(f"recorder device pool config: {args.recorder_pool_config}")
        sdk.initialize(enable_log=args.enable_log)
        playback_sdk.initialize(enable_log=args.enable_log)
        print(
            "wait recorder device pool lease "
            f"host={args.host}:{args.port} timeout={args.recorder_pool_wait_seconds:.0f}s "
            "policy=first-come-first-served"
        )
        with recorder_pool.acquire_for_device(
            host=args.host,
            port=args.port,
            wait_seconds=args.recorder_pool_wait_seconds,
        ) as recorder_lease:
            recorder_config = recorder_lease.recorder_device
            print(
                "recorder device pool lease acquired:",
                f"device_id={recorder_lease.device_id}",
                f"host={recorder_config.host}:{recorder_config.port}",
            )
            session = sdk.login(
                recorder_config.host,
                recorder_config.port,
                recorder_config.username,
                recorder_config.password,
            )
            use_cases = PickupTestUseCases(
                sdk=sdk,
                isapi=isapi,
                playback_sdk=playback_sdk,
                analyzer=analyzer,
                playback_device=PlaybackDeviceConfig(
                    host=args.playback_host,
                    port=args.playback_port,
                    username=args.playback_username,
                    password=args.playback_password,
                    voice_channel=args.playback_voice_channel,
                ),
            )

            audio_compression_types = _resolve_playback_audio_compression_types(
                isapi=isapi,
                playback_sdk=playback_sdk,
                host=args.playback_host,
                port=args.playback_port,
                username=args.playback_username,
                password=args.playback_password,
                requested=args.audio_compression_types,
            )
            print(f"pickup test playback audioCompressionType list: {','.join(audio_compression_types)}")
            failed = False
            for audio_compression_type in audio_compression_types:
                print(f"pickup test start playback audioCompressionType={audio_compression_type}")
                try:
                    result = use_cases.run_pickup_test(
                        session=session,
                        record_channel=args.record_channel or recorder_config.channel,
                        record_duration_seconds=args.record_duration,
                        send_duration_seconds=args.send_duration,
                        similarity_threshold=args.similarity_threshold,
                        seed=args.seed,
                        fingerprint_source=args.test_tone_id or args.playback_host,
                        test_device_input_type=args.test_device_input_type,
                        test_device_output_type=args.test_device_output_type,
                        audio_compression_type=audio_compression_type,
                    )

                    print(
                        "pickup test done:",
                        f"analysis_source={result.analysis_source}",
                        f"audio_compression_type={result.playback_device_audio_status.audio_compression_type or audio_compression_type}",
                        f"record={result.record_file_path}",
                        f"callback_audio={result.callback_audio_path or ''}",
                        f"reference={result.reference_audio_path}",
                        _format_audio_identity(result.talk_result),
                        f"has_sound={result.sound_result.has_sound}",
                        f"match={result.match_result.matched}",
                        f"score={result.match_result.best_score:.4f}",
                        f"threshold={result.match_result.threshold:.2f}",
                    )
                except (HikvisionSDKError, VideoAnalysisError, FileNotFoundError, ValueError) as exc:
                    failed = True
                    print(f"pickup test failed audioCompressionType={audio_compression_type}: {exc}", file=sys.stderr)
            print(f"recorder device pool lease released: device_id={recorder_lease.device_id}")
            if session is not None:
                sdk.logout(session)
                session = None
            if failed:
                return 1
    except (HikvisionSDKError, VideoAnalysisError, FileNotFoundError, ValueError, RecorderDevicePoolError) as exc:
        print(f"use case failed: {exc}", file=sys.stderr)
        return 1
    finally:
        if session is not None:
            sdk.logout(session)
        sdk.cleanup()
        playback_sdk.cleanup()
        sys.stdout.flush()
        sys.stderr.flush()
        sys.stdout = original_stdout
        sys.stderr = original_stderr
        log_file.close()
    return 0


def _format_audio_identity(talk_result) -> str:
    profile = ",".join(f"{frequency:.1f}" for frequency in talk_result.frequency_profile)
    return f"frequency_profile={profile}"


def _resolve_playback_audio_compression_types(
    isapi: HikvisionIsapiClient,
    playback_sdk: HikvisionVoiceSDK,
    host: str,
    port: int,
    username: str,
    password: str,
    requested: str,
) -> list[str]:
    if requested.strip().lower() != "auto":
        values = [item.strip() for item in requested.split(",") if item.strip()]
        if not values:
            raise ValueError("audio-compression-types cannot be empty")
        return values

    playback_session = playback_sdk.login(host, port, username, password)
    try:
        status = isapi.get_two_way_audio_channel_status(playback_session)
    finally:
        playback_sdk.logout(playback_session)
    if status.audio_compression_type_options:
        return list(status.audio_compression_type_options)
    if status.audio_compression_type:
        return [status.audio_compression_type]
    raise ValueError("playback device A audioCompressionType options/current value not found")


if __name__ == "__main__":
    sys.exit(main())
