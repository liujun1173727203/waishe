from __future__ import annotations

import argparse
from datetime import datetime
from pathlib import Path

from use_cases.voice_talk_cases import VoiceTalkUseCases, WIDEBAND_SAMPLE_RATE


def main() -> int:
    parser = argparse.ArgumentParser(description="Generate continuous wide-span frequency validation audio")
    parser.add_argument("--device-id", default="10.18.117.22", help="device/test tone id")
    parser.add_argument("--duration", type=int, default=4, help="audio duration in seconds")
    parser.add_argument("--sample-rate", type=int, default=WIDEBAND_SAMPLE_RATE, help="wav sample rate")
    parser.add_argument("--min-frequency", type=float, default=900.0, help="minimum frequency in Hz")
    parser.add_argument("--max-frequency", type=float, default=3200.0, help="maximum frequency in Hz")
    parser.add_argument("--segment-count", type=int, default=8, help="continuous frequency control points")
    parser.add_argument("--amplitude-ratio", type=float, default=0.35, help="audio amplitude ratio")
    parser.add_argument("--seed", type=int, default=None, help="optional stable seed")
    parser.add_argument("--output-dir", default="", help="output directory")
    args = parser.parse_args()

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    host_dir = "".join(char if char.isalnum() or char in "._-" else "_" for char in args.device_id)
    output_dir = Path(args.output_dir) if args.output_dir else Path.cwd() / "recordings" / "continuous_frequency_audio" / host_dir
    output_path = output_dir / f"continuous_frequency_{host_dir}_{timestamp}.wav"

    generator = VoiceTalkUseCases.__new__(VoiceTalkUseCases)
    _pcm_bytes, frequencies = generator.generate_continuous_frequency_wav(
        file_path=output_path,
        duration_seconds=args.duration,
        fingerprint_source=args.device_id,
        seed=args.seed,
        amplitude_ratio=args.amplitude_ratio,
        sample_rate=args.sample_rate,
        min_frequency=args.min_frequency,
        max_frequency=args.max_frequency,
        segment_count=args.segment_count,
    )

    print(f"continuous frequency audio ready: {output_path}")
    print(f"device_id={args.device_id}")
    print(f"sample_rate={args.sample_rate}")
    print("frequency_profile=" + ",".join(f"{frequency:.1f}" for frequency in frequencies))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
