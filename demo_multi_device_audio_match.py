from __future__ import annotations

import argparse
import random
import wave
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from use_cases.voice_talk_cases import (
    CHANNELS_MONO,
    SAMPLE_WIDTH_BYTES,
    VoiceTalkUseCases,
    WIDEBAND_SAMPLE_RATE,
)
from video_analysis import RecordedVideoAnalyzer


DEFAULT_DEVICE_IDS = (
    "sim-device-001",
    "sim-device-002",
    "sim-device-003",
    "sim-device-004",
    "sim-device-005",
)


@dataclass(frozen=True)
class SimulatedDeviceAudio:
    device_id: str
    wav_path: Path
    frequency_profile: tuple[float, ...]
    samples: list[int]


@dataclass(frozen=True)
class DeviceMatchSummary:
    device_id: str
    wav_path: Path
    matched: bool
    score: float
    threshold: float
    best_offset_frames: int


def main() -> int:
    """
    作用：作为命令行入口，解析参数并编排完整执行流程。
    执行步骤：
    1. 解析输入参数并准备依赖对象。
    2. 按业务流程顺序执行核心步骤。
    3. 输出日志、执行结果或退出码。
    """
    parser = argparse.ArgumentParser(
        description=(
            "Simulate multiple devices playing audio at the same time, mix them as device A recording, "
            "and judge whether the recording contains one specified device audio."
        )
    )
    parser.add_argument("--device-ids", default=",".join(DEFAULT_DEVICE_IDS), help="comma separated simulated device ids")
    parser.add_argument("--target-device-id", default=DEFAULT_DEVICE_IDS[0], help="device id to judge in the mixed recording")
    parser.add_argument("--duration", type=int, default=4, help="single device audio duration in seconds")
    parser.add_argument("--sample-rate", type=int, default=WIDEBAND_SAMPLE_RATE, help="wav sample rate")
    parser.add_argument("--min-frequency", type=float, default=900.0, help="minimum frequency in Hz")
    parser.add_argument("--max-frequency", type=float, default=3200.0, help="maximum frequency in Hz")
    parser.add_argument("--segment-count", type=int, default=8, help="discrete frequency segment count")
    parser.add_argument("--device-amplitude", type=float, default=0.10, help="single device amplitude before mixing")
    parser.add_argument("--noise-amplitude", type=float, default=0.0, help="optional simulated environment noise amplitude")
    parser.add_argument("--threshold", type=float, default=0.7, help="match threshold")
    parser.add_argument("--output-dir", default="", help="output directory, default recordings/multi_device_audio_match/<timestamp>")
    parser.add_argument("--output-name", default="device_a_recording_mixed.wav", help="mixed recording wav file name")
    parser.add_argument("--absent-target", action="store_true", help="do not include target device in mixed recording")
    args = parser.parse_args()

    device_ids = [item.strip() for item in args.device_ids.split(",") if item.strip()]
    if len(device_ids) < 2:
        raise ValueError("device-ids must contain at least two ids")
    if args.target_device_id not in device_ids:
        raise ValueError("target-device-id must be included in device-ids")
    if args.duration <= 0:
        raise ValueError("duration must be positive")
    if not 0 < args.device_amplitude <= 1:
        raise ValueError("device-amplitude must be in (0, 1]")
    if not 0 <= args.noise_amplitude <= 1:
        raise ValueError("noise-amplitude must be in [0, 1]")
    if not 0 < args.threshold <= 1:
        raise ValueError("threshold must be in (0, 1]")

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
    output_dir = Path(args.output_dir) if args.output_dir else Path.cwd() / "recordings" / "multi_device_audio_match" / timestamp
    output_dir.mkdir(parents=True, exist_ok=True)

    generator = VoiceTalkUseCases.__new__(VoiceTalkUseCases)
    simulated_audios: list[SimulatedDeviceAudio] = []
    for index, device_id in enumerate(device_ids, start=1):
        wav_path = output_dir / f"device_{index}_{_filename_token(device_id)}.wav"
        pcm_bytes, frequency_profile = generator.generate_continuous_frequency_wav(
            file_path=wav_path,
            duration_seconds=args.duration,
            fingerprint_source=device_id,
            seed=None,
            amplitude_ratio=args.device_amplitude,
            sample_rate=args.sample_rate,
            min_frequency=args.min_frequency,
            max_frequency=args.max_frequency,
            segment_count=args.segment_count,
        )
        simulated_audios.append(
            SimulatedDeviceAudio(
                device_id=device_id,
                wav_path=wav_path,
                frequency_profile=frequency_profile,
                samples=_pcm_bytes_to_samples(pcm_bytes),
            )
        )

    included_audios = [
        audio
        for audio in simulated_audios
        if not (args.absent_target and audio.device_id == args.target_device_id)
    ]
    mixed_samples = _mix_samples([audio.samples for audio in included_audios])
    if args.noise_amplitude > 0:
        mixed_samples = _add_noise(mixed_samples, amplitude_ratio=args.noise_amplitude)

    mixed_path = output_dir / args.output_name
    _write_wav(mixed_path, mixed_samples, sample_rate=args.sample_rate)

    analyzer = RecordedVideoAnalyzer(sample_rate=args.sample_rate)
    sound_result = analyzer.analyze_wav_sound_presence(mixed_path)
    match_summaries = _match_all_devices(
        analyzer=analyzer,
        mixed_path=mixed_path,
        simulated_audios=simulated_audios,
        threshold=args.threshold,
    )
    target_summary = next(summary for summary in match_summaries if summary.device_id == args.target_device_id)
    conclusion_passed = target_summary.matched == (not args.absent_target)

    print(f"output_dir={output_dir}")
    print(f"device_a_recording={mixed_path}")
    print(f"target_device_id={args.target_device_id}")
    print(f"target_included={not args.absent_target}")
    print(f"has_sound={sound_result.has_sound}")
    print(f"conclusion_passed={conclusion_passed}")
    print(
        "target_match:",
        f"matched={target_summary.matched}",
        f"score={target_summary.score:.4f}",
        f"threshold={target_summary.threshold:.2f}",
        f"best_offset_frames={target_summary.best_offset_frames}",
        f"reference={target_summary.wav_path}",
    )
    print("ranking:")
    for rank, summary in enumerate(sorted(match_summaries, key=lambda item: item.score, reverse=True), start=1):
        print(
            f"{rank}.",
            f"device_id={summary.device_id}",
            f"matched={summary.matched}",
            f"score={summary.score:.4f}",
            f"reference={summary.wav_path}",
        )
    print("frequency_profiles:")
    for audio in simulated_audios:
        profile = ",".join(f"{frequency:.1f}" for frequency in audio.frequency_profile)
        included = not (args.absent_target and audio.device_id == args.target_device_id)
        print(f"device_id={audio.device_id} included={included} frequency_profile={profile}")
    return 0


def _match_all_devices(
    analyzer: RecordedVideoAnalyzer,
    mixed_path: Path,
    simulated_audios: list[SimulatedDeviceAudio],
    threshold: float,
) -> list[DeviceMatchSummary]:
    """
    作用：作为内部辅助方法，完成本方法对应的数据处理。
    执行步骤：
    1. 接收并校验输入参数。
    2. 执行方法职责对应的核心处理。
    3. 返回处理结果，失败时抛出异常。
    """
    summaries: list[DeviceMatchSummary] = []
    for audio in simulated_audios:
        result = analyzer.detect_reference_audio_in_wav(
            audio_path=mixed_path,
            reference_audio_path=audio.wav_path,
            score_threshold=threshold,
        )
        summaries.append(
            DeviceMatchSummary(
                device_id=audio.device_id,
                wav_path=audio.wav_path,
                matched=result.matched,
                score=result.best_score,
                threshold=result.threshold,
                best_offset_frames=result.best_offset_frames,
            )
        )
    return summaries


def _pcm_bytes_to_samples(pcm_bytes: bytes) -> list[int]:
    """
    作用：作为内部辅助方法，完成本方法对应的数据处理。
    执行步骤：
    1. 接收并校验输入参数。
    2. 执行方法职责对应的核心处理。
    3. 返回处理结果，失败时抛出异常。
    """
    return [
        int.from_bytes(pcm_bytes[offset:offset + SAMPLE_WIDTH_BYTES], byteorder="little", signed=True)
        for offset in range(0, len(pcm_bytes), SAMPLE_WIDTH_BYTES)
    ]


def _mix_samples(tracks: list[list[int]]) -> list[int]:
    """
    作用：作为内部辅助方法，完成本方法对应的数据处理。
    执行步骤：
    1. 接收并校验输入参数。
    2. 执行方法职责对应的核心处理。
    3. 返回处理结果，失败时抛出异常。
    """
    if not tracks:
        return []
    max_len = max(len(track) for track in tracks)
    mixed: list[int] = []
    for index in range(max_len):
        sample_sum = sum(track[index] if index < len(track) else 0 for track in tracks)
        mixed.append(max(-32768, min(32767, sample_sum)))
    return mixed


def _add_noise(samples: list[int], amplitude_ratio: float) -> list[int]:
    """
    作用：作为内部辅助方法，完成本方法对应的数据处理。
    执行步骤：
    1. 接收并校验输入参数。
    2. 执行方法职责对应的核心处理。
    3. 返回处理结果，失败时抛出异常。
    """
    random_generator = random.Random(20260604)
    max_noise = int(32767 * amplitude_ratio)
    return [
        max(-32768, min(32767, sample + random_generator.randint(-max_noise, max_noise)))
        for sample in samples
    ]


def _write_wav(path: Path, samples: list[int], sample_rate: int) -> None:
    """
    作用：作为内部辅助方法，完成本方法对应的数据处理。
    执行步骤：
    1. 接收并校验输入参数。
    2. 执行方法职责对应的核心处理。
    3. 返回处理结果，失败时抛出异常。
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    pcm_bytes = b"".join(int(sample).to_bytes(SAMPLE_WIDTH_BYTES, byteorder="little", signed=True) for sample in samples)
    with wave.open(str(path), "wb") as wav_file:
        wav_file.setnchannels(CHANNELS_MONO)
        wav_file.setsampwidth(SAMPLE_WIDTH_BYTES)
        wav_file.setframerate(sample_rate)
        wav_file.writeframes(pcm_bytes)


def _filename_token(value: str) -> str:
    """
    作用：作为内部辅助方法，完成本方法对应的数据处理。
    执行步骤：
    1. 接收并校验输入参数。
    2. 执行方法职责对应的核心处理。
    3. 返回处理结果，失败时抛出异常。
    """
    return "".join(char if char.isalnum() or char in "._-" else "_" for char in value).strip("_") or "device"


if __name__ == "__main__":
    raise SystemExit(main())
