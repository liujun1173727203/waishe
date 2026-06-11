from __future__ import annotations

import math
import subprocess
import wave
from dataclasses import dataclass
from pathlib import Path

from app_config import get_ffmpeg_path, resolve_ffmpeg_path


DEFAULT_SAMPLE_RATE = 8000
DEFAULT_CHANNELS = 1
DEFAULT_SAMPLE_WIDTH = 2
DEFAULT_FRAME_MS = 20
CONTINUOUS_FREQUENCY_MIN_HZ = 700.0
CONTINUOUS_FREQUENCY_MAX_HZ = 3600.0
FREQUENCY_COMPONENT_PITCH_SCALES = (0.92, 0.96, 1.0, 1.04, 1.08)
FREQUENCY_COMPONENT_PROBE_COUNT = 16


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
        ffmpeg_path: str | None = None,
        sample_rate: int = DEFAULT_SAMPLE_RATE,
        channels: int = DEFAULT_CHANNELS,
        frame_ms: int = DEFAULT_FRAME_MS,
    ) -> None:
        """
        作用：初始化对象实例，保存后续执行所需的依赖、配置或运行状态。
        执行步骤：
        1. 接收并校验输入参数。
        2. 执行方法职责对应的核心处理。
        3. 返回处理结果，失败时抛出异常。
        """
        self.ffmpeg_path = ffmpeg_path or get_ffmpeg_path()
        self.sample_rate = sample_rate
        self.channels = channels
        self.frame_ms = frame_ms

    def resolve_ffmpeg(self) -> str:
        """
        作用：读取配置、设备或运行状态，并转换为结构化结果。
        执行步骤：
        1. 读取输入参数、配置或设备响应。
        2. 解析并校验目标字段。
        3. 返回解析后的结构化结果。
        """
        try:
            return resolve_ffmpeg_path(self.ffmpeg_path)
        except FileNotFoundError as exc:
            raise VideoAnalysisError(str(exc)) from exc

    def extract_audio(
        self,
        video_path: str | Path,
        output_path: str | Path | None = None,
    ) -> Path:
        """
        作用：执行本方法对应的业务处理。
        执行步骤：
        1. 接收并校验输入参数。
        2. 执行方法职责对应的核心处理。
        3. 返回处理结果，失败时抛出异常。
        """
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
        """
        作用：分析输入数据，计算特征、分数或判定结果。
        执行步骤：
        1. 读取待分析的输入数据。
        2. 计算统计量、特征或匹配分数。
        3. 返回分析结论供用例判定。
        """
        audio_path = self.extract_audio(video_path, extracted_audio_path)
        return self.analyze_wav_sound_presence(audio_path, rms_threshold=rms_threshold)

    def analyze_wav_sound_presence(
        self,
        audio_path: str | Path,
        rms_threshold: float = 300.0,
    ) -> SoundAnalysisResult:
        """
        作用：分析输入数据，计算特征、分数或判定结果。
        执行步骤：
        1. 读取待分析的输入数据。
        2. 计算统计量、特征或匹配分数。
        3. 返回分析结论供用例判定。
        """
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
    ) -> ReferenceAudioMatchResult:
        """
        作用：分析输入数据，计算特征、分数或判定结果。
        执行步骤：
        1. 读取待分析的输入数据。
        2. 计算统计量、特征或匹配分数。
        3. 返回分析结论供用例判定。
        """
        audio_path = self.extract_audio(video_path, extracted_audio_path)
        return self.detect_reference_audio_in_wav(
            audio_path=audio_path,
            reference_audio_path=reference_audio_path,
            score_threshold=score_threshold,
        )

    def detect_reference_audio_in_wav(
        self,
        audio_path: str | Path,
        reference_audio_path: str | Path,
        score_threshold: float = 0.75,
    ) -> ReferenceAudioMatchResult:
        """
        作用：分析输入数据，计算特征、分数或判定结果。
        执行步骤：
        1. 读取待分析的输入数据。
        2. 计算统计量、特征或匹配分数。
        3. 返回分析结论供用例判定。
        """
        audio_path = Path(audio_path)
        target_samples = self._read_wav_samples(audio_path)
        reference_samples = self._read_wav_samples(reference_audio_path)

        target_rms = self._frame_rms_values(target_samples)
        reference_rms = self._frame_rms_values(reference_samples)
        component_score, component_offset = self._frequency_component_match_score(
            target_samples=target_samples,
            reference_samples=reference_samples,
        )
        final_score = component_score
        best_offset = component_offset

        return ReferenceAudioMatchResult(
            matched=final_score >= score_threshold,
            audio_path=audio_path,
            reference_path=Path(reference_audio_path).resolve(),
            best_score=final_score,
            best_offset_frames=best_offset,
            frame_count=len(target_rms),
            reference_frame_count=len(reference_rms),
            threshold=score_threshold,
        )

    def _default_audio_output_path(self, video_path: Path) -> Path:
        """
        作用：作为内部辅助方法，完成本方法对应的数据处理。
        执行步骤：
        1. 接收并校验输入参数。
        2. 执行方法职责对应的核心处理。
        3. 返回处理结果，失败时抛出异常。
        """
        return Path.cwd() / "recordings" / "analysis" / f"{video_path.stem}.wav"

    def _read_wav_samples(self, wav_path: str | Path) -> list[int]:
        """
        作用：读取配置、设备或运行状态，并转换为结构化结果。
        执行步骤：
        1. 读取输入参数、配置或设备响应。
        2. 解析并校验目标字段。
        3. 返回解析后的结构化结果。
        """
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
        """
        作用：作为内部辅助方法，完成本方法对应的数据处理。
        执行步骤：
        1. 接收并校验输入参数。
        2. 执行方法职责对应的核心处理。
        3. 返回处理结果，失败时抛出异常。
        """
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
        """
        作用：作为内部辅助方法，完成本方法对应的数据处理。
        执行步骤：
        1. 接收并校验输入参数。
        2. 执行方法职责对应的核心处理。
        3. 返回处理结果，失败时抛出异常。
        """
        if not values:
            return []
        # Remove mean and normalize energy so matching is less sensitive to gain changes.
        mean = sum(values) / len(values)
        centered = [value - mean for value in values]
        energy = math.sqrt(sum(value * value for value in centered))
        if energy == 0:
            return [0.0 for _ in centered]
        return [value / energy for value in centered]

    def _continuous_frequency_match_score(
        self,
        target_samples: list[int],
        reference_samples: list[int],
    ) -> tuple[float, int]:
        """
        作用：作为内部辅助方法，完成本方法对应的数据处理。
        执行步骤：
        1. 接收并校验输入参数。
        2. 执行方法职责对应的核心处理。
        3. 返回处理结果，失败时抛出异常。
        """
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
        offset_step = max(1, int(100 / self.frame_ms))
        for offset in range(0, len(target_chunks) - reference_length + 1, offset_step):
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
        """
        作用：作为内部辅助方法，完成本方法对应的数据处理。
        执行步骤：
        1. 接收并校验输入参数。
        2. 执行方法职责对应的核心处理。
        3. 返回处理结果，失败时抛出异常。
        """
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

    def _frequency_component_match_score(
        self,
        target_samples: list[int],
        reference_samples: list[int],
    ) -> tuple[float, int]:
        """
        作用：分析输入数据，计算特征、分数或判定结果。
        执行步骤：
        1. 读取待分析的输入数据。
        2. 计算统计量、特征或匹配分数。
        3. 返回分析结论供用例判定。
        """
        reference_chunks = self._frame_chunks(reference_samples)
        target_chunks = self._frame_chunks(target_samples)
        if not reference_chunks or not target_chunks or len(target_chunks) < len(reference_chunks):
            return 0.0, -1

        probes = self._reference_frequency_component_probes(reference_chunks)
        if len(probes) < max(4, FREQUENCY_COMPONENT_PROBE_COUNT // 3):
            return 0.0, -1

        best_score = 0.0
        best_offset = -1
        reference_length = len(reference_chunks)
        # Real recordings can be slightly shifted; search each frame, but only score a small set of probes.
        for offset in range(0, len(target_chunks) - reference_length + 1):
            scores: list[float] = []
            for relative_frame, frequency in probes:
                target_index = offset + relative_frame
                chunk = self._merge_neighbor_chunks(target_chunks, target_index, radius=1)
                if not chunk:
                    continue
                scores.append(self._frequency_component_presence_score(chunk, frequency))
            if not scores:
                continue
            strong_ratio = sum(1 for value in scores if value >= 0.58) / len(scores)
            average_score = sum(scores) / len(scores)
            weak_ratio = sum(1 for value in scores if value >= 0.35) / len(scores)
            score = 0.50 * strong_ratio + 0.35 * average_score + 0.15 * weak_ratio
            if score > best_score:
                best_score = score
                best_offset = offset
        return best_score, best_offset

    def _reference_frequency_component_probes(self, reference_chunks: list[list[int]]) -> list[tuple[int, float]]:
        """
        作用：作为内部辅助方法，完成本方法对应的数据处理。
        执行步骤：
        1. 接收并校验输入参数。
        2. 执行方法职责对应的核心处理。
        3. 返回处理结果，失败时抛出异常。
        """
        if not reference_chunks:
            return []
        probe_count = min(FREQUENCY_COMPONENT_PROBE_COUNT, len(reference_chunks))
        if probe_count <= 0:
            return []

        probes: list[tuple[int, float]] = []
        seen: set[tuple[int, int]] = set()
        for probe_index in range(probe_count):
            relative = (probe_index + 0.5) / probe_count
            frame_index = min(len(reference_chunks) - 1, max(0, int(relative * len(reference_chunks))))
            chunk = self._merge_neighbor_chunks(reference_chunks, frame_index, radius=1)
            frequency = self._dominant_continuous_frequency(chunk)
            if frequency <= 0:
                continue
            rounded_key = (frame_index, int(round(frequency / 25.0) * 25))
            if rounded_key in seen:
                continue
            seen.add(rounded_key)
            probes.append((frame_index, frequency))
        return probes

    def _dominant_continuous_frequency(self, samples: list[int]) -> float:
        """
        作用：分析输入数据，计算特征、分数或判定结果。
        执行步骤：
        1. 读取待分析的输入数据。
        2. 计算统计量、特征或匹配分数。
        3. 返回分析结论供用例判定。
        """
        if not samples:
            return 0.0
        frequencies = [
            CONTINUOUS_FREQUENCY_MIN_HZ + index * 25.0
            for index in range(
                int((CONTINUOUS_FREQUENCY_MAX_HZ - CONTINUOUS_FREQUENCY_MIN_HZ) // 25) + 1
            )
        ]
        powers = [(frequency, self._goertzel_power(samples, frequency)) for frequency in frequencies]
        powers.sort(key=lambda item: item[1], reverse=True)
        best_frequency, best_power = powers[0]
        second_power = powers[1][1] if len(powers) > 1 else 0.0
        if best_power <= max(second_power * 1.01, 1.0):
            return 0.0
        return best_frequency

    def _frequency_component_presence_score(self, samples: list[int], frequency: float) -> float:
        """
        作用：分析输入数据，计算特征、分数或判定结果。
        执行步骤：
        1. 读取待分析的输入数据。
        2. 计算统计量、特征或匹配分数。
        3. 返回分析结论供用例判定。
        """
        if not samples or frequency <= 0:
            return 0.0
        center_power = max(
            math.sqrt(self._goertzel_power(samples, frequency * pitch_scale))
            for pitch_scale in FREQUENCY_COMPONENT_PITCH_SCALES
            if 0 < frequency * pitch_scale < self.sample_rate / 2
        )
        neighbor_offsets = (-520.0, -360.0, -240.0, 240.0, 360.0, 520.0)
        neighbor_powers = [
            math.sqrt(self._goertzel_power(samples, frequency + offset))
            for offset in neighbor_offsets
            if CONTINUOUS_FREQUENCY_MIN_HZ <= frequency + offset <= CONTINUOUS_FREQUENCY_MAX_HZ
        ]
        background = (sum(neighbor_powers) / len(neighbor_powers)) if neighbor_powers else 1.0
        ratio_score = max(0.0, min(1.0, (center_power / max(background, 1.0) - 0.95) / 1.35))

        square_sum = sum(sample * sample for sample in samples)
        rms = math.sqrt(square_sum / len(samples)) if samples else 0.0
        # For a sine component, sqrt(Goertzel power) scales with N * amplitude.
        normalized_tone = center_power / max(rms * len(samples), 1.0)
        tone_score = max(0.0, min(1.0, (normalized_tone - 0.045) / 0.20))
        return 0.55 * tone_score + 0.45 * ratio_score

    def _merge_neighbor_chunks(self, chunks: list[list[int]], center_index: int, radius: int) -> list[int]:
        """
        作用：作为内部辅助方法，完成本方法对应的数据处理。
        执行步骤：
        1. 接收并校验输入参数。
        2. 执行方法职责对应的核心处理。
        3. 返回处理结果，失败时抛出异常。
        """
        start = max(0, center_index - radius)
        end = min(len(chunks), center_index + radius + 1)
        merged: list[int] = []
        for chunk in chunks[start:end]:
            merged.extend(chunk)
        return merged

    def _frequency_presence_score(self, samples: list[int], frequency: float) -> float:
        """
        作用：分析输入数据，计算特征、分数或判定结果。
        执行步骤：
        1. 读取待分析的输入数据。
        2. 计算统计量、特征或匹配分数。
        3. 返回分析结论供用例判定。
        """
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
        """
        作用：分析输入数据，计算特征、分数或判定结果。
        执行步骤：
        1. 读取待分析的输入数据。
        2. 计算统计量、特征或匹配分数。
        3. 返回分析结论供用例判定。
        """
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

    def _frame_chunks(self, samples: list[int]) -> list[list[int]]:
        """
        作用：作为内部辅助方法，完成本方法对应的数据处理。
        执行步骤：
        1. 接收并校验输入参数。
        2. 执行方法职责对应的核心处理。
        3. 返回处理结果，失败时抛出异常。
        """
        samples_per_frame = self.sample_rate * self.frame_ms // 1000
        if samples_per_frame <= 0:
            raise VideoAnalysisError("invalid frame size")
        return [
            samples[start:start + samples_per_frame]
            for start in range(0, len(samples), samples_per_frame)
            if samples[start:start + samples_per_frame]
        ]

    def _goertzel_power(self, samples: list[int], frequency: float) -> float:
        """
        作用：作为内部辅助方法，完成本方法对应的数据处理。
        执行步骤：
        1. 接收并校验输入参数。
        2. 执行方法职责对应的核心处理。
        3. 返回处理结果，失败时抛出异常。
        """
        coefficient = 2.0 * math.cos(2.0 * math.pi * frequency / self.sample_rate)
        previous = 0.0
        previous_2 = 0.0
        for sample in samples:
            current = sample + coefficient * previous - previous_2
            previous_2 = previous
            previous = current
        return previous_2 * previous_2 + previous * previous - coefficient * previous * previous_2
