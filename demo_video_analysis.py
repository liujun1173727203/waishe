from __future__ import annotations

import argparse
import sys

from video_analysis import RecordedVideoAnalyzer, VideoAnalysisError


def main() -> int:
    parser = argparse.ArgumentParser(description="Analyze recorded video audio")
    parser.add_argument("--video", required=True, help="recorded video file path")
    parser.add_argument("--reference-audio", default="", help="optional random audio wav path for matching")
    parser.add_argument("--expected-digits", default="", help="optional expected digit sequence for DTMF matching")
    parser.add_argument("--ffmpeg-path", default="ffmpeg", help="ffmpeg executable path")
    parser.add_argument("--rms-threshold", type=float, default=300.0, help="sound detection RMS threshold")
    parser.add_argument("--score-threshold", type=float, default=0.75, help="reference audio match threshold")
    args = parser.parse_args()

    analyzer = RecordedVideoAnalyzer(ffmpeg_path=args.ffmpeg_path)
    try:
        sound_result = analyzer.analyze_sound_presence(
            video_path=args.video,
            rms_threshold=args.rms_threshold,
        )
        print(
            "sound analysis:",
            f"has_sound={sound_result.has_sound}",
            f"audio={sound_result.audio_path}",
            f"active_frames={sound_result.active_frame_count}/{sound_result.frame_count}",
            f"max_rms={sound_result.max_rms:.2f}",
            f"avg_rms={sound_result.average_rms:.2f}",
        )

        if args.reference_audio:
            match_result = analyzer.detect_reference_audio(
                video_path=args.video,
                reference_audio_path=args.reference_audio,
                score_threshold=args.score_threshold,
                expected_digit_sequence=args.expected_digits,
            )
            print(
                "reference match:",
                f"matched={match_result.matched}",
                f"score={match_result.best_score:.4f}",
                f"offset_frames={match_result.best_offset_frames}",
                f"reference={match_result.reference_path}",
                f"expected_digits={match_result.expected_digit_sequence}",
                f"detected_digits={match_result.detected_digit_sequence}",
            )
    except (VideoAnalysisError, FileNotFoundError) as exc:
        print(f"video analysis failed: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
