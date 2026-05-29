from __future__ import annotations

import math
import shutil
import subprocess
import wave
from dataclasses import dataclass
from pathlib import Path
from typing import Optional


DEFAULT_SAMPLE_RATE = 8000
DEFAULT_CHANNELS = 1
DEFAULT_SAMPLE_WIDTH = 2
DEFAULT_FRAME_MS = 20


class VideoAnalysisError(RuntimeError):
    pass


@dataclass(frozen=True)
class SoundAnalysisResult:
    has_sound: bool
    audio_path: Path
    frame_count: int
    active_frame_count: int
    max_rms: float
    average_rms: float
    threshold: float


@dataclass(frozen=True)
class ReferenceAudioMatchResult:
    matched: bool
    audio_path: Path
    reference_path: Path
    best_score: float
    best_offset_frames: int
    frame_count: int
    reference_frame_count: int
    threshold: float


class RecordedVideoAnalyzer:
    def __init__(
        self,
        ffmpeg_path: str = "ffmpeg",
        sample_rate: int = DEFAULT_SAMPLE_RATE,
        channels: int = DEFAULT_CHANNELS,
        frame_ms: int = DEFAULT_FRAME_MS,
    ) -> None:
        self.ffmpeg_path = ffmpeg_path
        self.sample_rate = sample_rate
        self.channels = channels
        self.frame_ms = frame_ms

    def extract_audio(
        self,
        video_path: str | Path,
        output_path: str | Path | None = None,
    ) -> Path:
        video = Path(video_path).resolve()
        if not video.exists():
            raise FileNotFoundError(f"video file not found: {video}")

        ffmpeg = shutil.which(self.ffmpeg_path)
        if ffmpeg is None:
            raise VideoAnalysisError(
                f"ffmpeg not found: {self.ffmpeg_path}. Please install ffmpeg or pass a valid ffmpeg_path."
            )

        target = Path(output_path) if output_path is not None else self._default_audio_output_path(video)
        target.parent.mkdir(parents=True, exist_ok=True)

        # Normalize to mono 8 kHz PCM so recorded audio and reference audio share the same format.
        command = [
            ffmpeg,
            "-y",
            "-i",
            str(video),
            "-vn",
            "-acodec",
            "pcm_s16le",
            "-ar",
            str(self.sample_rate),
            "-ac",
            str(self.channels),
            str(target),
        ]
        result = subprocess.run(command, capture_output=True, text=True)
        if result.returncode != 0:
            raise VideoAnalysisError(
                "ffmpeg audio extraction failed: "
                f"code={result.returncode}, stderr={result.stderr.strip() or result.stdout.strip()}"
            )
        return target

    def analyze_sound_presence(
        self,
        video_path: str | Path,
        extracted_audio_path: str | Path | None = None,
        rms_threshold: float = 300.0,
    ) -> SoundAnalysisResult:
        audio_path = self.extract_audio(video_path, extracted_audio_path)
        samples = self._read_wav_samples(audio_path)
        frame_rms = self._frame_rms_values(samples)
        # Treat any frame above threshold as evidence that audible sound exists.
        active_frames = [value for value in frame_rms if value >= rms_threshold]
        average_rms = sum(frame_rms) / len(frame_rms) if frame_rms else 0.0
        max_rms = max(frame_rms) if frame_rms else 0.0
        return SoundAnalysisResult(
            has_sound=bool(active_frames),
            audio_path=audio_path,
            frame_count=len(frame_rms),
            active_frame_count=len(active_frames),
            max_rms=max_rms,
            average_rms=average_rms,
            threshold=rms_threshold,
        )

    def detect_reference_audio(
        self,
        video_path: str | Path,
        reference_audio_path: str | Path,
        extracted_audio_path: str | Path | None = None,
        score_threshold: float = 0.75,
    ) -> ReferenceAudioMatchResult:
        audio_path = self.extract_audio(video_path, extracted_audio_path)
        target_samples = self._read_wav_samples(audio_path)
        reference_samples = self._read_wav_samples(reference_audio_path)

        target_signature = self._normalize_signature(self._frame_rms_values(target_samples))
        reference_signature = self._normalize_signature(self._frame_rms_values(reference_samples))
        # Use sliding-window energy matching to find where the reference best aligns.
        best_score, best_offset = self._best_signature_match(target_signature, reference_signature)

        return ReferenceAudioMatchResult(
            matched=best_score >= score_threshold,
            audio_path=audio_path,
            reference_path=Path(reference_audio_path).resolve(),
            best_score=best_score,
            best_offset_frames=best_offset,
            frame_count=len(target_signature),
            reference_frame_count=len(reference_signature),
            threshold=score_threshold,
        )

    def _default_audio_output_path(self, video_path: Path) -> Path:
        return Path.cwd() / "recordings" / "analysis" / f"{video_path.stem}.wav"

    def _read_wav_samples(self, wav_path: str | Path) -> list[int]:
        path = Path(wav_path)
        if not path.exists():
            raise FileNotFoundError(f"audio file not found: {path}")

        with wave.open(str(path), "rb") as wav_file:
            channels = wav_file.getnchannels()
            sample_width = wav_file.getsampwidth()
            sample_rate = wav_file.getframerate()
            frame_count = wav_file.getnframes()
            raw = wav_file.readframes(frame_count)

        if sample_width != DEFAULT_SAMPLE_WIDTH:
            raise VideoAnalysisError(f"unsupported wav sample width: {sample_width}")
        if sample_rate != self.sample_rate:
            raise VideoAnalysisError(f"unexpected wav sample rate: {sample_rate}, expected {self.sample_rate}")

        samples: list[int] = []
        step = sample_width * channels
        for offset in range(0, len(raw), step):
            sample = int.from_bytes(raw[offset:offset + sample_width], byteorder="little", signed=True)
            samples.append(sample)
        return samples

    def _frame_rms_values(self, samples: list[int]) -> list[float]:
        samples_per_frame = self.sample_rate * self.frame_ms // 1000
        if samples_per_frame <= 0:
            raise VideoAnalysisError("invalid frame size")

        values: list[float] = []
        for start in range(0, len(samples), samples_per_frame):
            chunk = samples[start:start + samples_per_frame]
            if not chunk:
                continue
            square_sum = sum(sample * sample for sample in chunk)
            values.append(math.sqrt(square_sum / len(chunk)))
        return values

    def _normalize_signature(self, values: list[float]) -> list[float]:
        if not values:
            return []
        # Remove mean and normalize energy so matching is less sensitive to gain changes.
        mean = sum(values) / len(values)
        centered = [value - mean for value in values]
        energy = math.sqrt(sum(value * value for value in centered))
        if energy == 0:
            return [0.0 for _ in centered]
        return [value / energy for value in centered]

    def _best_signature_match(self, target: list[float], reference: list[float]) -> tuple[float, int]:
        if not target or not reference or len(target) < len(reference):
            return 0.0, -1

        best_score = -1.0
        best_offset = -1
        ref_len = len(reference)
        for offset in range(0, len(target) - ref_len + 1):
            window = target[offset:offset + ref_len]
            score = sum(left * right for left, right in zip(window, reference))
            if score > best_score:
                best_score = score
                best_offset = offset
        return best_score, best_offset
