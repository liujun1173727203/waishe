from __future__ import annotations

import json
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any


ROOT_DIR = Path(__file__).resolve().parent


@dataclass(frozen=True)
class ArgumentSpec:
    name: str
    cli_flag: str
    value_type: str = "string"
    required: bool = False
    default: Any = None
    description: str = ""


@dataclass(frozen=True)
class TestCaseSpec:
    case_id: str
    script_path: Path
    description: str
    arguments: tuple[ArgumentSpec, ...]


def _arg(
    name: str,
    cli_flag: str,
    *,
    value_type: str = "string",
    required: bool = False,
    default: Any = None,
    description: str = "",
) -> ArgumentSpec:
    return ArgumentSpec(
        name=name,
        cli_flag=cli_flag,
        value_type=value_type,
        required=required,
        default=default,
        description=description,
    )


TEST_CASES: dict[str, TestCaseSpec] = {
    "speaker_test": TestCaseSpec(
        case_id="speaker_test",
        script_path=ROOT_DIR / "demo_speaker_test.py",
        description="Run speaker validation with recorder pool, playback and audio match analysis.",
        arguments=(
            _arg("host", "--host", required=True, description="Target device IP or hostname."),
            _arg("port", "--port", value_type="int", default=8000),
            _arg("username", "--username", default="admin"),
            _arg("password", "--password"),
            _arg("voice_channel", "--voice-channel", value_type="int", default=2),
            _arg("record_channel", "--record-channel", value_type="int", default=0),
            _arg("record_duration", "--record-duration", value_type="int", default=10),
            _arg("send_duration", "--send-duration", value_type="int", default=4),
            _arg("similarity_threshold", "--similarity-threshold", value_type="float", default=0.7),
            _arg("seed", "--seed", value_type="int"),
            _arg("test_tone_id", "--test-tone-id"),
            _arg("test_device_output_type", "--test-device-output-type", default="Speaker"),
            _arg("audio_compression_types", "--audio-compression-types", default="auto"),
            _arg("recorder_pool_config", "--recorder-pool-config", default=str(ROOT_DIR / "configs" / "recorder_device_pool.json")),
            _arg("recorder_pool_wait_seconds", "--recorder-pool-wait-seconds", value_type="float", default=300.0),
            _arg("enable_log", "--enable-log", value_type="bool", default=False),
        ),
    ),
    "lineout_test": TestCaseSpec(
        case_id="lineout_test",
        script_path=ROOT_DIR / "demo_lineout_test.py",
        description="Run speaker validation with audioOutputType forced to LineOut.",
        arguments=(
            _arg("host", "--host", required=True),
            _arg("port", "--port", value_type="int", default=8000),
            _arg("username", "--username", default="admin"),
            _arg("password", "--password"),
            _arg("voice_channel", "--voice-channel", value_type="int", default=2),
            _arg("record_channel", "--record-channel", value_type="int", default=0),
            _arg("record_duration", "--record-duration", value_type="int", default=10),
            _arg("send_duration", "--send-duration", value_type="int", default=4),
            _arg("similarity_threshold", "--similarity-threshold", value_type="float", default=0.7),
            _arg("seed", "--seed", value_type="int"),
            _arg("test_tone_id", "--test-tone-id"),
            _arg("audio_compression_types", "--audio-compression-types", default="auto"),
            _arg("recorder_pool_config", "--recorder-pool-config", default=str(ROOT_DIR / "configs" / "recorder_device_pool.json")),
            _arg("recorder_pool_wait_seconds", "--recorder-pool-wait-seconds", value_type="float", default=300.0),
            _arg("enable_log", "--enable-log", value_type="bool", default=False),
        ),
    ),
    "pickup_test": TestCaseSpec(
        case_id="pickup_test",
        script_path=ROOT_DIR / "demo_pickup_test.py",
        description="Run pickup validation by recording target device input while playback device sends audio.",
        arguments=(
            _arg("host", "--host", required=True),
            _arg("port", "--port", value_type="int", default=8000),
            _arg("username", "--username", default="admin"),
            _arg("password", "--password"),
            _arg("record_channel", "--record-channel", value_type="int", default=1),
            _arg("record_duration", "--record-duration", value_type="int", default=10),
            _arg("send_duration", "--send-duration", value_type="int", default=4),
            _arg("similarity_threshold", "--similarity-threshold", value_type="float", default=0.7),
            _arg("seed", "--seed", value_type="int"),
            _arg("test_tone_id", "--test-tone-id"),
            _arg("test_device_input_type", "--test-device-input-type", default="MicIn"),
            _arg("test_device_output_type", "--test-device-output-type", default="Speaker"),
            _arg("audio_compression_types", "--audio-compression-types", default="auto"),
            _arg("playback_host", "--playback-host", default="10.40.230.23"),
            _arg("playback_port", "--playback-port", value_type="int", default=8000),
            _arg("playback_username", "--playback-username", default="admin"),
            _arg("playback_password", "--playback-password"),
            _arg("playback_voice_channel", "--playback-voice-channel", value_type="int", default=1),
            _arg("recorder_pool_config", "--recorder-pool-config", default=str(ROOT_DIR / "configs" / "recorder_device_pool.json")),
            _arg("recorder_pool_wait_seconds", "--recorder-pool-wait-seconds", value_type="float", default=300.0),
            _arg("enable_log", "--enable-log", value_type="bool", default=False),
        ),
    ),
    "linein_test": TestCaseSpec(
        case_id="linein_test",
        script_path=ROOT_DIR / "demo_linein_test.py",
        description="Run pickup validation with audioInputType forced to LineIn.",
        arguments=(
            _arg("host", "--host", required=True),
            _arg("port", "--port", value_type="int", default=8000),
            _arg("username", "--username", default="admin"),
            _arg("password", "--password"),
            _arg("record_channel", "--record-channel", value_type="int", default=1),
            _arg("record_duration", "--record-duration", value_type="int", default=10),
            _arg("send_duration", "--send-duration", value_type="int", default=4),
            _arg("similarity_threshold", "--similarity-threshold", value_type="float", default=0.7),
            _arg("seed", "--seed", value_type="int"),
            _arg("test_tone_id", "--test-tone-id"),
            _arg("test_device_output_type", "--test-device-output-type", default="Speaker"),
            _arg("audio_compression_types", "--audio-compression-types", default="auto"),
            _arg("playback_host", "--playback-host", default="10.40.230.23"),
            _arg("playback_port", "--playback-port", value_type="int", default=8000),
            _arg("playback_username", "--playback-username", default="admin"),
            _arg("playback_password", "--playback-password"),
            _arg("playback_voice_channel", "--playback-voice-channel", value_type="int", default=1),
            _arg("recorder_pool_config", "--recorder-pool-config", default=str(ROOT_DIR / "configs" / "recorder_device_pool.json")),
            _arg("recorder_pool_wait_seconds", "--recorder-pool-wait-seconds", value_type="float", default=300.0),
            _arg("enable_log", "--enable-log", value_type="bool", default=False),
        ),
    ),
    "supplement_light_test": TestCaseSpec(
        case_id="supplement_light_test",
        script_path=ROOT_DIR / "demo_supplement_light_test.py",
        description="Run supplement light capability and effect validation.",
        arguments=(
            _arg("host", "--host", required=True),
            _arg("port", "--port", value_type="int", default=8000),
            _arg("username", "--username", default="admin"),
            _arg("password", "--password"),
            _arg("channel", "--channel", value_type="int", default=0),
            _arg("capture_channel", "--capture-channel", value_type="int", default=0),
            _arg("stream_type", "--stream-type", default="main"),
            _arg("settle_seconds", "--settle-seconds", value_type="float", default=2.0),
            _arg("on_threshold", "--on-threshold", value_type="float", default=10.0),
            _arg("level_threshold", "--level-threshold", value_type="float", default=5.0),
            _arg("enable_log", "--enable-log", value_type="bool", default=False),
        ),
    ),
    "random_audio_talk": TestCaseSpec(
        case_id="random_audio_talk",
        script_path=ROOT_DIR / "demo_random_audio_talk_use_case.py",
        description="Generate and send fingerprint audio to target device by voice talk.",
        arguments=(
            _arg("host", "--host", required=True),
            _arg("port", "--port", value_type="int", default=8000),
            _arg("username", "--username", default="admin"),
            _arg("password", "--password"),
            _arg("voice_channel", "--voice-channel", value_type="int", default=0),
            _arg("duration", "--duration", value_type="int", default=4),
            _arg("seed", "--seed", value_type="int"),
            _arg("test_tone_id", "--test-tone-id"),
            _arg("enable_log", "--enable-log", value_type="bool", default=False),
        ),
    ),
    "capture_picture": TestCaseSpec(
        case_id="capture_picture",
        script_path=ROOT_DIR / "demo_capture_picture.py",
        description="Capture JPEG or stream snapshot from target device.",
        arguments=(
            _arg("host", "--host", required=True),
            _arg("port", "--port", value_type="int", default=8000),
            _arg("username", "--username", default="admin"),
            _arg("password", "--password"),
            _arg("channel", "--channel", value_type="int", default=0),
            _arg("stream_type", "--stream-type", default="main"),
            _arg("output", "--output"),
            _arg("mode", "--mode", default="auto"),
            _arg("jpeg_picture_size", "--jpeg-picture-size", value_type="string", default="0xFF"),
            _arg("jpeg_quality", "--jpeg-quality", value_type="int", default=0),
            _arg("enable_log", "--enable-log", value_type="bool", default=False),
        ),
    ),
    "stream_record": TestCaseSpec(
        case_id="stream_record",
        script_path=ROOT_DIR / "demo_stream_record.py",
        description="Record live stream using recorder pool device binding.",
        arguments=(
            _arg("host", "--host", required=True),
            _arg("port", "--port", value_type="int", default=8000),
            _arg("channel", "--channel", value_type="int", default=0),
            _arg("duration", "--duration", value_type="int", default=30),
            _arg("output", "--output"),
            _arg("recorder_pool_config", "--recorder-pool-config", default=str(ROOT_DIR / "configs" / "recorder_device_pool.json")),
            _arg("recorder_pool_wait_seconds", "--recorder-pool-wait-seconds", value_type="float", default=300.0),
            _arg("enable_log", "--enable-log", value_type="bool", default=False),
        ),
    ),
    "composite_stream_record": TestCaseSpec(
        case_id="composite_stream_record",
        script_path=ROOT_DIR / "demo_composite_stream_record.py",
        description="Enable composite stream recording then record live stream.",
        arguments=(
            _arg("host", "--host", required=True),
            _arg("port", "--port", value_type="int", default=8000),
            _arg("channel", "--channel", value_type="int", default=0),
            _arg("duration", "--duration", value_type="int", default=10),
            _arg("output", "--output"),
            _arg("recorder_pool_config", "--recorder-pool-config", default=str(ROOT_DIR / "configs" / "recorder_device_pool.json")),
            _arg("recorder_pool_wait_seconds", "--recorder-pool-wait-seconds", value_type="float", default=300.0),
            _arg("enable_log", "--enable-log", value_type="bool", default=False),
        ),
    ),
    "video_analysis": TestCaseSpec(
        case_id="video_analysis",
        script_path=ROOT_DIR / "demo_video_analysis.py",
        description="Analyze recorded video sound presence and optional reference-audio matching.",
        arguments=(
            _arg("video", "--video", required=True),
            _arg("reference_audio", "--reference-audio"),
            _arg("rms_threshold", "--rms-threshold", value_type="float", default=300.0),
            _arg("score_threshold", "--score-threshold", value_type="float", default=0.7),
        ),
    ),
    "continuous_frequency_audio": TestCaseSpec(
        case_id="continuous_frequency_audio",
        script_path=ROOT_DIR / "demo_continuous_frequency_audio.py",
        description="Generate discrete frequency validation WAV file.",
        arguments=(
            _arg("device_id", "--device-id", required=True),
            _arg("duration", "--duration", value_type="int", default=4),
            _arg("sample_rate", "--sample-rate", value_type="int", default=24000),
            _arg("min_frequency", "--min-frequency", value_type="float", default=900.0),
            _arg("max_frequency", "--max-frequency", value_type="float", default=3200.0),
            _arg("segment_count", "--segment-count", value_type="int", default=8),
            _arg("amplitude_ratio", "--amplitude-ratio", value_type="float", default=0.35),
            _arg("seed", "--seed", value_type="int"),
            _arg("output_dir", "--output-dir"),
        ),
    ),
    "multi_device_audio_match": TestCaseSpec(
        case_id="multi_device_audio_match",
        script_path=ROOT_DIR / "demo_multi_device_audio_match.py",
        description="Analyze whether multiple device fingerprints exist in one recorded audio/video.",
        arguments=(
            _arg("device_ids", "--device-ids", default="sim-device-001,sim-device-002,sim-device-003,sim-device-004,sim-device-005"),
            _arg("target_device_id", "--target-device-id", default="sim-device-001"),
            _arg("duration", "--duration", value_type="int", default=4),
            _arg("sample_rate", "--sample-rate", value_type="int", default=24000),
            _arg("min_frequency", "--min-frequency", value_type="float", default=900.0),
            _arg("max_frequency", "--max-frequency", value_type="float", default=3200.0),
            _arg("segment_count", "--segment-count", value_type="int", default=8),
            _arg("device_amplitude", "--device-amplitude", value_type="float", default=0.10),
            _arg("noise_amplitude", "--noise-amplitude", value_type="float", default=0.0),
            _arg("threshold", "--threshold", value_type="float", default=0.7),
            _arg("output_dir", "--output-dir"),
            _arg("output_name", "--output-name", default="device_a_recording_mixed.wav"),
            _arg("absent_target", "--absent-target", value_type="bool", default=False),
        ),
    ),
    "mix_five_device_audio": TestCaseSpec(
        case_id="mix_five_device_audio",
        script_path=ROOT_DIR / "test_mix_five_device_audio.py",
        description="Generate and mix five simulated device audios.",
        arguments=(
            _arg("duration", "--duration", value_type="int", default=4),
            _arg("amplitude_ratio", "--amplitude-ratio", value_type="float", default=0.16),
            _arg("sample_rate", "--sample-rate", value_type="int", default=24000),
            _arg("min_frequency", "--min-frequency", value_type="float", default=900.0),
            _arg("max_frequency", "--max-frequency", value_type="float", default=3200.0),
            _arg("segment_count", "--segment-count", value_type="int", default=8),
            _arg("output_dir", "--output-dir"),
            _arg("output_name", "--output-name", default="mixed_five_devices.wav"),
            _arg("device_ids", "--device-ids", default="sim-device-001,sim-device-002,sim-device-003,sim-device-004,sim-device-005"),
        ),
    ),
}


def list_test_cases() -> list[dict[str, Any]]:
    return [serialize_test_case(spec) for spec in TEST_CASES.values()]


def get_test_case(case_id: str) -> TestCaseSpec:
    try:
        return TEST_CASES[case_id]
    except KeyError as exc:
        raise KeyError(f"unsupported test case: {case_id}") from exc


def serialize_test_case(spec: TestCaseSpec) -> dict[str, Any]:
    return {
        "case_id": spec.case_id,
        "script": str(spec.script_path.relative_to(ROOT_DIR)),
        "description": spec.description,
        "arguments": [
            {
                "name": arg.name,
                "cli_flag": arg.cli_flag,
                "type": arg.value_type,
                "required": arg.required,
                "default": arg.default,
                "description": arg.description,
            }
            for arg in spec.arguments
        ],
    }


def build_command_for_test_case(case_id: str, arguments: dict[str, Any] | None = None) -> list[str]:
    spec = get_test_case(case_id)
    payload = arguments or {}
    if not isinstance(payload, dict):
        raise ValueError("arguments must be a JSON object")
    command = [sys.executable, str(spec.script_path)]
    command.extend(_build_cli_arguments(spec, payload))
    return command


def run_test_case(case_id: str, arguments: dict[str, Any] | None = None, timeout_seconds: float | None = None) -> dict[str, Any]:
    command = build_command_for_test_case(case_id, arguments)

    started_at = time.time()
    try:
        completed = subprocess.run(
            command,
            cwd=str(ROOT_DIR),
            capture_output=True,
            text=True,
            timeout=timeout_seconds,
        )
    except subprocess.TimeoutExpired as exc:
        raise TimeoutError(
            f"test case timed out after {timeout_seconds}s: {case_id}"
        ) from exc
    finished_at = time.time()

    return {
        "case_id": case_id,
        "command": command,
        "success": completed.returncode == 0,
        "exit_code": completed.returncode,
        "stdout": completed.stdout,
        "stderr": completed.stderr,
        "started_at": started_at,
        "finished_at": finished_at,
        "duration_seconds": round(finished_at - started_at, 3),
    }


def _build_cli_arguments(spec: TestCaseSpec, payload: dict[str, Any]) -> list[str]:
    allowed = {arg.name for arg in spec.arguments}
    unknown = sorted(set(payload) - allowed)
    if unknown:
        raise ValueError(f"unsupported arguments for {spec.case_id}: {', '.join(unknown)}")

    cli_arguments: list[str] = []
    for arg in spec.arguments:
        if arg.name not in payload:
            if arg.required and arg.default is None:
                raise ValueError(f"missing required argument: {arg.name}")
            continue

        value = payload[arg.name]
        if value is None:
            continue
        if arg.value_type == "bool":
            if not isinstance(value, bool):
                raise ValueError(f"argument {arg.name} must be bool")
            if value:
                cli_arguments.append(arg.cli_flag)
            continue

        cli_arguments.extend((arg.cli_flag, _stringify_argument_value(arg, value)))
    return cli_arguments


def _stringify_argument_value(arg: ArgumentSpec, value: Any) -> str:
    if arg.value_type == "int":
        if isinstance(value, bool):
            raise ValueError(f"argument {arg.name} must be int")
        try:
            return str(int(value))
        except (TypeError, ValueError) as exc:
            raise ValueError(f"argument {arg.name} must be int") from exc
    if arg.value_type == "float":
        if isinstance(value, bool):
            raise ValueError(f"argument {arg.name} must be float")
        try:
            return str(float(value))
        except (TypeError, ValueError) as exc:
            raise ValueError(f"argument {arg.name} must be float") from exc
    if arg.value_type == "string":
        return str(value)
    raise ValueError(f"unsupported value_type: {arg.value_type}")


def build_error_response(message: str, *, case_id: str | None = None) -> dict[str, Any]:
    response = {"success": False, "error": message}
    if case_id:
        response["case_id"] = case_id
    return response


def to_pretty_json(payload: Any) -> bytes:
    return json.dumps(payload, ensure_ascii=False, indent=2).encode("utf-8")
