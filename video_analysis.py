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
CONTINUOUS_FREQUENCY_MIN_HZ = 700.0
CONTINUOUS_FREQUENCY_MAX_HZ = 3600.0
DTMF_FREQUENCIES = (697.0, 770.0, 852.0, 941.0, 1209.0, 1336.0, 1477.0)
DTMF_LOW_FREQUENCIES = (697.0, 770.0, 852.0, 941.0)
DTMF_HIGH_FREQUENCIES = (1209.0, 1336.0, 1477.0)
DTMF_FREQUENCY_OFFSETS = (-0.12, -0.08, -0.04, 0.0, 0.04, 0.08, 0.12)
DTMF_SEQUENCE_PITCH_SCALES = (0.88, 0.92, 0.96, 1.0, 1.04, 1.08, 1.12, 1.16, 1.20, 1.24)
DTMF_DIGIT_FREQUENCIES = {
    "0": (941.0, 1336.0),
    "1": (697.0, 1209.0),
    "2": (697.0, 1336.0),
    "3": (697.0, 1477.0),
    "4": (770.0, 1209.0),
    "5": (770.0, 1336.0),
    "6": (770.0, 1477.0),
    "7": (852.0, 1209.0),
    "8": (852.0, 1336.0),
    "9": (852.0, 1477.0),
}
DTMF_DIGIT_MAP = {
    (697.0, 1209.0): "1",
    (697.0, 1336.0): "2",
    (697.0, 1477.0): "3",
    (770.0, 1209.0): "4",
    (770.0, 1336.0): "5",
    (770.0, 1477.0): "6",
    (852.0, 1209.0): "7",
    (852.0, 1336.0): "8",
    (852.0, 1477.0): "9",
    (941.0, 1336.0): "0",
}


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
    expected_digit_sequence: str = ""
    detected_digit_sequence: str = ""


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

    def resolve_ffmpeg(self) -> str:
        configured = Path(self.ffmpeg_path)
        if configured.is_file():
            return str(configured.resolve())

        if configured.is_dir():
            candidates = [configured / "ffmpeg.exe", configured / "ffmpeg"]
            for candidate in candidates:
                if candidate.is_file():
                    return str(candidate.resolve())

        ffmpeg = shutil.which(self.ffmpeg_path)
        if ffmpeg is not None:
            return ffmpeg

        raise VideoAnalysisError(
            f"ffmpeg not found: {self.ffmpeg_path}. Please install ffmpeg or pass a valid ffmpeg_path."
        )

    def extract_audio(
        self,
        video_path: str | Path,
        output_path: str | Path | None = None,
    ) -> Path:
        video = Path(video_path).resolve()
        if not video.exists():
            raise FileNotFoundError(f"video file not found: {video}")

        ffmpeg = self.resolve_ffmpeg()

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
        return self.analyze_wav_sound_presence(audio_path, rms_threshold=rms_threshold)

    def analyze_wav_sound_presence(
        self,
        audio_path: str | Path,
        rms_threshold: float = 300.0,
    ) -> SoundAnalysisResult:
        audio_path = Path(audio_path)
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
        expected_digit_sequence: str = "",
    ) -> ReferenceAudioMatchResult:
        audio_path = self.extract_audio(video_path, extracted_audio_path)
        return self.detect_reference_audio_in_wav(
            audio_path=audio_path,
            reference_audio_path=reference_audio_path,
            score_threshold=score_threshold,
            expected_digit_sequence=expected_digit_sequence,
        )

    def detect_reference_audio_in_wav(
        self,
        audio_path: str | Path,
        reference_audio_path: str | Path,
        score_threshold: float = 0.75,
        expected_digit_sequence: str = "",
    ) -> ReferenceAudioMatchResult:
        audio_path = Path(audio_path)
        target_samples = self._read_wav_samples(audio_path)
        reference_samples = self._read_wav_samples(reference_audio_path)

        target_rms = self._frame_rms_values(target_samples)
        reference_rms = self._frame_rms_values(reference_samples)
        target_signature = self._audio_signature(target_samples)
        reference_signature = self._trim_signature_to_active_region(
            self._audio_signature(reference_samples),
            reference_rms,
        )
        # Use sliding-window signature matching to find where the reference best aligns.
        best_score, best_offset = self._best_signature_match(target_signature, reference_signature)
        continuous_score, continuous_offset = self._continuous_frequency_match_score(
            target_samples=target_samples,
            reference_samples=reference_samples,
        )
        projection_score, projection_offset = self._time_domain_projection_match_score(
            target_samples=target_samples,
            reference_samples=reference_samples,
        )
        detected_digit_sequence = self._decode_dtmf_sequence(target_samples)
        sequence_score, sequence_offset = self._expected_digit_sequence_match_score(
            target_samples=target_samples,
            reference_samples=reference_samples,
            expected_digit_sequence=expected_digit_sequence,
        )
        digit_matched = bool(expected_digit_sequence and expected_digit_sequence in detected_digit_sequence)
        if sequence_score > best_score:
            best_offset = sequence_offset
        if continuous_score > max(best_score, sequence_score, projection_score):
            best_offset = continuous_offset
        if projection_score > max(best_score, sequence_score, continuous_score):
            best_offset = projection_offset
        final_score = max(best_score, sequence_score, continuous_score, projection_score, 1.0 if digit_matched else 0.0)

        return ReferenceAudioMatchResult(
            matched=final_score >= score_threshold,
            audio_path=audio_path,
            reference_path=Path(reference_audio_path).resolve(),
            best_score=final_score,
            best_offset_frames=best_offset,
            frame_count=len(target_signature),
            reference_frame_count=len(reference_signature),
            threshold=score_threshold,
            expected_digit_sequence=expected_digit_sequence,
            detected_digit_sequence=detected_digit_sequence,
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

    def _audio_signature(self, samples: list[int]) -> list[float]:
        rms_signature = self._normalize_signature(self._frame_rms_values(samples))
        dtmf_signature = self._normalize_signature(self._frame_dtmf_scores(samples))
        if not dtmf_signature:
            return rms_signature
        if not rms_signature:
            return dtmf_signature

        length = min(len(rms_signature), len(dtmf_signature))
        combined = [
            0.35 * rms_signature[index] + 0.65 * dtmf_signature[index]
            for index in range(length)
        ]
        return self._normalize_signature(combined)

    def _trim_signature_to_active_region(self, signature: list[float], frame_rms: list[float]) -> list[float]:
        if not signature or not frame_rms:
            return signature
        max_rms = max(frame_rms)
        if max_rms <= 0:
            return signature

        threshold = max(300.0, max_rms * 0.12)
        active_indices = [index for index, value in enumerate(frame_rms) if value >= threshold]
        if not active_indices:
            return signature

        padding_frames = max(1, int(100 / self.frame_ms))
        start = max(0, active_indices[0] - padding_frames)
        end = min(len(signature), active_indices[-1] + padding_frames + 1)
        if end <= start:
            return signature
        return self._normalize_signature(signature[start:end])

    def _frame_dtmf_scores(self, samples: list[int]) -> list[float]:
        samples_per_frame = self.sample_rate * self.frame_ms // 1000
        if samples_per_frame <= 0:
            raise VideoAnalysisError("invalid frame size")

        scores: list[float] = []
        for start in range(0, len(samples), samples_per_frame):
            chunk = samples[start:start + samples_per_frame]
            if not chunk:
                continue
            low_power = max(self._goertzel_band_power(chunk, frequency) for frequency in DTMF_LOW_FREQUENCIES)
            high_power = max(self._goertzel_band_power(chunk, frequency) for frequency in DTMF_HIGH_FREQUENCIES)
            scores.append(math.sqrt(low_power) + math.sqrt(high_power))
        return scores

    def _goertzel_band_power(self, samples: list[int], center_frequency: float) -> float:
        powers = [
            self._goertzel_power(samples, center_frequency * (1.0 + offset))
            for offset in DTMF_FREQUENCY_OFFSETS
            if 0 < center_frequency * (1.0 + offset) < self.sample_rate / 2
        ]
        return max(powers) if powers else 0.0

    def _decode_dtmf_sequence(self, samples: list[int]) -> str:
        samples_per_frame = self.sample_rate * self.frame_ms // 1000
        if samples_per_frame <= 0:
            raise VideoAnalysisError("invalid frame size")

        frame_digits: list[str] = []
        for start in range(0, len(samples), samples_per_frame):
            chunk = samples[start:start + samples_per_frame]
            if not chunk:
                continue
            frame_digits.append(self._decode_dtmf_frame(chunk))

        runs: list[tuple[str, int]] = []
        current_digit = ""
        current_count = 0
        for digit in frame_digits:
            if digit == current_digit:
                current_count += 1
                continue
            if current_digit:
                runs.append((current_digit, current_count))
            current_digit = digit
            current_count = 1
        if current_digit:
            runs.append((current_digit, current_count))

        min_digit_frames = max(2, int(80 / self.frame_ms))
        decoded = []
        for digit, count in runs:
            if not digit or count < min_digit_frames:
                continue
            if not decoded or decoded[-1] != digit:
                decoded.append(digit)
        return "".join(decoded)

    def _decode_dtmf_frame(self, samples: list[int]) -> str:
        low_powers = [(frequency, self._goertzel_band_power(samples, frequency)) for frequency in DTMF_LOW_FREQUENCIES]
        high_powers = [(frequency, self._goertzel_band_power(samples, frequency)) for frequency in DTMF_HIGH_FREQUENCIES]
        low_powers.sort(key=lambda item: item[1], reverse=True)
        high_powers.sort(key=lambda item: item[1], reverse=True)

        low_frequency, low_power = low_powers[0]
        high_frequency, high_power = high_powers[0]
        low_second = low_powers[1][1] if len(low_powers) > 1 else 0.0
        high_second = high_powers[1][1] if len(high_powers) > 1 else 0.0

        low_ratio = low_power / max(low_second, 1.0)
        high_ratio = high_power / max(high_second, 1.0)
        frame_rms = self._frame_rms_values(samples)[0] if samples else 0.0
        dtmf_strength = math.sqrt(low_power) + math.sqrt(high_power)
        if low_ratio < 1.15 or high_ratio < 1.15 or dtmf_strength < frame_rms * 80.0:
            return ""

        return DTMF_DIGIT_MAP.get((low_frequency, high_frequency), "")

    def _expected_digit_sequence_match_score(
        self,
        target_samples: list[int],
        reference_samples: list[int],
        expected_digit_sequence: str,
    ) -> tuple[float, int]:
        if not expected_digit_sequence:
            return 0.0, -1

        target_chunks = self._frame_chunks(target_samples)
        reference_rms = self._frame_rms_values(reference_samples)
        reference_labels = self._reference_digit_labels(reference_rms, expected_digit_sequence)
        if not target_chunks or not reference_labels or len(target_chunks) < len(reference_labels):
            return 0.0, -1

        reference_signature = [1.0 if label else 0.0 for label in reference_labels]
        reference_signature = self._normalize_signature(reference_signature)
        best_score = 0.0
        best_offset = -1
        target_length = len(target_chunks)
        reference_length = len(reference_labels)
        digits = sorted(set(expected_digit_sequence))

        for scale in DTMF_SEQUENCE_PITCH_SCALES:
            digit_powers = {
                digit: [self._digit_power(chunk, digit, scale) for chunk in target_chunks]
                for digit in digits
            }
            for offset in range(0, target_length - reference_length + 1):
                candidate = [
                    digit_powers[label][offset + index] if label else 0.0
                    for index, label in enumerate(reference_labels)
                ]
                score = sum(
                    left * right
                    for left, right in zip(self._normalize_signature(candidate), reference_signature)
                )
                if score > best_score:
                    best_score = score
                    best_offset = offset
        return best_score, best_offset

    def _continuous_frequency_match_score(
        self,
        target_samples: list[int],
        reference_samples: list[int],
    ) -> tuple[float, int]:
        reference_track = self._dominant_frequency_track(reference_samples)
        target_chunks = self._frame_chunks(target_samples)
        if not target_chunks or not reference_track or len(target_chunks) < len(reference_track):
            return 0.0, -1

        reference_active = [item for item in reference_track if item > 0]
        if len(reference_active) < max(5, len(reference_track) // 4):
            return 0.0, -1

        best_score = 0.0
        best_offset = -1
        reference_length = len(reference_track)
        for offset in range(0, len(target_chunks) - reference_length + 1):
            scores: list[float] = []
            for target_chunk, reference_frequency in zip(target_chunks[offset:offset + reference_length], reference_track):
                if reference_frequency <= 0:
                    continue
                scores.append(self._frequency_presence_score(target_chunk, reference_frequency))
            if not scores:
                continue
            strong_ratio = sum(1 for value in scores if value >= 0.55) / len(scores)
            average_score = sum(scores) / len(scores)
            score = 0.55 * strong_ratio + 0.45 * average_score
            if score > best_score:
                best_score = score
                best_offset = offset
        return best_score, best_offset

    def _time_domain_projection_match_score(
        self,
        target_samples: list[int],
        reference_samples: list[int],
    ) -> tuple[float, int]:
        if not target_samples or not reference_samples or len(target_samples) < len(reference_samples):
            return 0.0, -1

        reference_energy = sum(sample * sample for sample in reference_samples)
        target_energy = sum(sample * sample for sample in target_samples)
        if reference_energy <= 0 or target_energy <= 0:
            return 0.0, -1

        samples_per_frame = self.sample_rate * self.frame_ms // 1000
        step = max(1, samples_per_frame)
        best_score = 0.0
        best_offset = -1
        reference_length = len(reference_samples)
        for offset in range(0, len(target_samples) - reference_length + 1, step):
            window = target_samples[offset:offset + reference_length]
            window_energy = sum(sample * sample for sample in window)
            if window_energy <= 0:
                continue
            dot = sum(left * right for left, right in zip(window, reference_samples))
            coefficient = dot / reference_energy
            cosine = dot / math.sqrt(reference_energy * window_energy)
            coefficient_score = max(0.0, 1.0 - abs(coefficient - 1.0) / 0.45)
            cosine_score = max(0.0, min(1.0, (cosine - 0.20) / 0.25))
            score = 0.70 * coefficient_score + 0.30 * cosine_score
            if score > best_score:
                best_score = score
                best_offset = offset // step
        return best_score, best_offset

    def _frequency_presence_score(self, samples: list[int], frequency: float) -> float:
        center_power = math.sqrt(self._goertzel_power(samples, frequency))
        neighbor_offsets = (-450.0, -300.0, -200.0, 200.0, 300.0, 450.0)
        neighbor_powers = [
            math.sqrt(self._goertzel_power(samples, frequency + offset))
            for offset in neighbor_offsets
            if CONTINUOUS_FREQUENCY_MIN_HZ <= frequency + offset <= CONTINUOUS_FREQUENCY_MAX_HZ
        ]
        background = (sum(neighbor_powers) / len(neighbor_powers)) if neighbor_powers else 1.0
        ratio = center_power / max(background, 1.0)
        return max(0.0, min(1.0, (ratio - 0.9) / 1.6))

    def _dominant_frequency_track(self, samples: list[int]) -> list[float]:
        chunks = self._frame_chunks(samples)
        if not chunks:
            return []
        frequencies = [
            CONTINUOUS_FREQUENCY_MIN_HZ + index * 100.0
            for index in range(
                int((CONTINUOUS_FREQUENCY_MAX_HZ - CONTINUOUS_FREQUENCY_MIN_HZ) // 100) + 1
            )
        ]
        track: list[float] = []
        rms_values = self._frame_rms_values(samples)
        rms_threshold = max(120.0, (max(rms_values) if rms_values else 0.0) * 0.08)
        for chunk in chunks:
            frame_rms = self._frame_rms_values(chunk)[0] if chunk else 0.0
            if frame_rms < rms_threshold:
                track.append(0.0)
                continue
            powers = [(frequency, self._goertzel_power(chunk, frequency)) for frequency in frequencies]
            powers.sort(key=lambda item: item[1], reverse=True)
            best_frequency, best_power = powers[0]
            second_power = powers[1][1] if len(powers) > 1 else 0.0
            if best_power <= max(second_power * 1.03, 1.0):
                track.append(0.0)
            else:
                track.append(best_frequency)
        return track

    def _reference_digit_labels(self, frame_rms: list[float], expected_digit_sequence: str) -> list[str]:
        if not frame_rms:
            return []
        max_rms = max(frame_rms)
        if max_rms <= 0:
            return []
        threshold = max(300.0, max_rms * 0.12)

        runs: list[tuple[int, int]] = []
        start: Optional[int] = None
        for index, value in enumerate(frame_rms):
            if value >= threshold and start is None:
                start = index
            elif value < threshold and start is not None:
                runs.append((start, index))
                start = None
        if start is not None:
            runs.append((start, len(frame_rms)))
        if len(runs) < len(expected_digit_sequence):
            return []

        labels = ["" for _ in frame_rms]
        for digit, (start, end) in zip(expected_digit_sequence, runs[:len(expected_digit_sequence)]):
            for index in range(start, end):
                labels[index] = digit
        return labels

    def _frame_chunks(self, samples: list[int]) -> list[list[int]]:
        samples_per_frame = self.sample_rate * self.frame_ms // 1000
        if samples_per_frame <= 0:
            raise VideoAnalysisError("invalid frame size")
        return [
            samples[start:start + samples_per_frame]
            for start in range(0, len(samples), samples_per_frame)
            if samples[start:start + samples_per_frame]
        ]

    def _digit_power(self, samples: list[int], digit: str, pitch_scale: float) -> float:
        frequencies = DTMF_DIGIT_FREQUENCIES.get(digit)
        if frequencies is None:
            return 0.0
        low_frequency, high_frequency = frequencies
        return (
            math.sqrt(self._goertzel_power(samples, low_frequency * pitch_scale)) +
            math.sqrt(self._goertzel_power(samples, high_frequency * pitch_scale))
        )

    def _goertzel_power(self, samples: list[int], frequency: float) -> float:
        coefficient = 2.0 * math.cos(2.0 * math.pi * frequency / self.sample_rate)
        previous = 0.0
        previous_2 = 0.0
        for sample in samples:
            current = sample + coefficient * previous - previous_2
            previous_2 = previous
            previous = current
        return previous_2 * previous_2 + previous * previous - coefficient * previous * previous_2

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
