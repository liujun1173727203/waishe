from __future__ import annotations

import argparse
import wave
from datetime import datetime
from pathlib import Path

from use_cases.voice_talk_cases import (
    CHANNELS_MONO,
    SAMPLE_WIDTH_BYTES,
    VoiceTalkUseCases,
    WIDEBAND_SAMPLE_RATE,
)


DEFAULT_DEVICE_IDS = (
    "sim-device-001",
    "sim-device-002",
    "sim-device-003",
    "sim-device-004",
    "sim-device-005",
)


def main() -> int:
    parser = argparse.ArgumentParser(description="Generate and mix five simulated discrete-frequency device audios")
    parser.add_argument("--duration", type=int, default=4, help="audio duration in seconds")
    parser.add_argument("--amplitude-ratio", type=float, default=0.16, help="single-device amplitude before mixing")
    parser.add_argument("--sample-rate", type=int, default=WIDEBAND_SAMPLE_RATE, help="wav sample rate")
    parser.add_argument("--min-frequency", type=float, default=900.0, help="minimum frequency in Hz")
    parser.add_argument("--max-frequency", type=float, default=3200.0, help="maximum frequency in Hz")
    parser.add_argument("--segment-count", type=int, default=8, help="discrete frequency segment count")
    parser.add_argument("--output-dir", default="", help="output directory, default recordings/mixed_audio/<timestamp>")
    parser.add_argument("--output-name", default="mixed_five_devices.wav", help="mixed wav file name")
    parser.add_argument(
        "--device-ids",
        default=",".join(DEFAULT_DEVICE_IDS),
        help="comma separated simulated device ids, default five ids",
    )
    args = parser.parse_args()

    device_ids = [item.strip() for item in args.device_ids.split(",") if item.strip()]
    if len(device_ids) != 5:
        raise ValueError("device-ids must contain exactly 5 ids")
    if args.duration <= 0:
        raise ValueError("duration must be positive")
    if not 0 < args.amplitude_ratio <= 1:
        raise ValueError("amplitude-ratio must be in (0, 1]")

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    output_dir = Path(args.output_dir) if args.output_dir else Path.cwd() / "recordings" / "mixed_audio" / timestamp
    output_dir.mkdir(parents=True, exist_ok=True)

    generator = VoiceTalkUseCases.__new__(VoiceTalkUseCases)
    pcm_tracks: list[list[int]] = []
    metadata: list[tuple[str, tuple[float, ...], Path]] = []

    for index, device_id in enumerate(device_ids, start=1):
        wav_path = output_dir / f"device_{index}_{_filename_token(device_id)}.wav"
        pcm_bytes, frequencies = generator.generate_continuous_frequency_wav(
            file_path=wav_path,
            duration_seconds=args.duration,
            fingerprint_source=device_id,
            seed=None,
            amplitude_ratio=args.amplitude_ratio,
            sample_rate=args.sample_rate,
            min_frequency=args.min_frequency,
            max_frequency=args.max_frequency,
            segment_count=args.segment_count,
        )
        pcm_tracks.append(_pcm_bytes_to_samples(pcm_bytes))
        metadata.append((device_id, frequencies, wav_path))

    mixed_samples = _mix_samples(pcm_tracks)
    mixed_path = output_dir / args.output_name
    _write_wav(mixed_path, mixed_samples, sample_rate=args.sample_rate)

    print(f"mixed audio ready: {mixed_path}")
    for device_id, frequencies, wav_path in metadata:
        frequency_profile = ",".join(f"{frequency:.1f}" for frequency in frequencies)
        print(f"device_id={device_id} frequency_profile={frequency_profile} wav={wav_path}")
    return 0


def _pcm_bytes_to_samples(pcm_bytes: bytes) -> list[int]:
    return [
        int.from_bytes(pcm_bytes[offset:offset + SAMPLE_WIDTH_BYTES], byteorder="little", signed=True)
        for offset in range(0, len(pcm_bytes), SAMPLE_WIDTH_BYTES)
    ]


def _mix_samples(tracks: list[list[int]]) -> list[int]:
    if not tracks:
        return []
    max_len = max(len(track) for track in tracks)
    mixed: list[int] = []
    for index in range(max_len):
        sample_sum = sum(track[index] if index < len(track) else 0 for track in tracks)
        mixed.append(max(-32768, min(32767, sample_sum)))
    return mixed


def _write_wav(path: Path, samples: list[int], sample_rate: int) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    pcm_bytes = b"".join(int(sample).to_bytes(SAMPLE_WIDTH_BYTES, byteorder="little", signed=True) for sample in samples)
    with wave.open(str(path), "wb") as wav_file:
        wav_file.setnchannels(CHANNELS_MONO)
        wav_file.setsampwidth(SAMPLE_WIDTH_BYTES)
        wav_file.setframerate(sample_rate)
        wav_file.writeframes(pcm_bytes)


def _filename_token(value: str) -> str:
    return "".join(char if char.isalnum() or char in "._-" else "_" for char in value).strip("_") or "device"


if __name__ == "__main__":
    raise SystemExit(main())
