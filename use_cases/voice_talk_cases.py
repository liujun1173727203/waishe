from __future__ import annotations

import math
import random
import hashlib
import time
import wave
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Callable, Optional

from hikvision_voice import DeviceSession, HikvisionSDKError, HikvisionVoiceSDK


G711_U = 1
G711_A = 2
PCM = 8

SAMPLE_RATE_8K = 8000
WIDEBAND_SAMPLE_RATE = 24000
SAMPLE_WIDTH_BYTES = 2
CHANNELS_MONO = 1
FRAME_MS = 20
SAMPLES_PER_FRAME = SAMPLE_RATE_8K * FRAME_MS // 1000
PCM_BYTES_PER_FRAME = SAMPLES_PER_FRAME * SAMPLE_WIDTH_BYTES

DIGIT_TONE_MAP = {
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


@dataclass(frozen=True)
class RandomAudioTalkResult:
    file_path: Path
    duration_seconds: int
    encode_type: int
    voice_channel: int
    digit_sequence: str
    bytes_sent: int
    frames_sent: int
    frequency_profile: tuple[float, ...] = ()


@dataclass(frozen=True)
class PreparedRandomAudio:
    file_path: Path
    duration_seconds: int
    encode_type: int
    voice_channel: int
    digit_sequence: str
    pcm_bytes: bytes
    encoded_bytes: bytes
    frame_count: int
    frequency_profile: tuple[float, ...] = ()


class VoiceTalkUseCases:
    def __init__(self, sdk: HikvisionVoiceSDK) -> None:
        self.sdk = sdk

    def prepare_random_audio_file(
        self,
        session: DeviceSession,
        duration_seconds: int = 4,
        voice_channel: Optional[int] = None,
        output_dir: str | Path | None = None,
        file_prefix: str = "random_audio",
        seed: Optional[int] = None,
        amplitude_ratio: float = 0.35,
        per_frame_delay: float = FRAME_MS / 1000.0,
        encoded_audio_callback: Optional[Callable[[bytes, int], None]] = None,
        digit_sequence: Optional[str] = None,
        fingerprint_source: Optional[str] = None,
    ) -> RandomAudioTalkResult:
        if duration_seconds <= 0:
            raise ValueError("duration_seconds must be positive")
        if not 0 < amplitude_ratio <= 1:
            raise ValueError("amplitude_ratio must be in (0, 1]")

        compress = self.sdk.get_current_audio_compress(session)
        if compress.encode_type not in {G711_U, G711_A, PCM}:
            raise HikvisionSDKError(
                "Unsupported audio encode type for random audio talk use case",
                error_message=f"encode_type={compress.encode_type}, supported={sorted([G711_U, G711_A, PCM])}",
            )

        target_channel = voice_channel or session.default_voice_channel
        wav_path = self._build_output_path(session.host, output_dir, prefix=file_prefix)
        frequency_profile: tuple[float, ...] = ()
        if digit_sequence is None:
            pcm_bytes, frequency_profile = self.generate_continuous_frequency_wav(
                file_path=wav_path,
                duration_seconds=duration_seconds,
                fingerprint_source=fingerprint_source or session.host,
                seed=seed,
                amplitude_ratio=amplitude_ratio,
                sample_rate=SAMPLE_RATE_8K,
                min_frequency=900.0,
                max_frequency=3200.0,
                segment_count=8,
            )
            digit_sequence = ""
        else:
            pcm_bytes, digit_sequence = self._generate_digit_pcm_wav(
                file_path=wav_path,
                duration_seconds=duration_seconds,
                amplitude_ratio=amplitude_ratio,
                seed=seed,
                digit_sequence=digit_sequence,
                fingerprint_source=fingerprint_source or session.host,
            )
        encoded_bytes = self._encode_pcm_for_device(pcm_bytes, compress.encode_type)

        frames = self._split_encoded_frames(encoded_bytes, compress.encode_type)
        return PreparedRandomAudio(
            file_path=wav_path,
            duration_seconds=duration_seconds,
            encode_type=compress.encode_type,
            voice_channel=target_channel,
            digit_sequence=digit_sequence,
            pcm_bytes=pcm_bytes,
            encoded_bytes=encoded_bytes,
            frame_count=len(frames),
            frequency_profile=frequency_profile,
        )

    def send_prepared_audio(
        self,
        session: DeviceSession,
        prepared_audio: PreparedRandomAudio,
        per_frame_delay: float = FRAME_MS / 1000.0,
        encoded_audio_callback: Optional[Callable[[bytes, int], None]] = None,
    ) -> RandomAudioTalkResult:
        bytes_sent = 0
        frames_sent = 0
        with self.sdk.start_voice_forward(
            session=session,
            voice_channel=prepared_audio.voice_channel,
            encoded_audio_callback=encoded_audio_callback,
        ) as forward:
            for frame in self._split_encoded_frames(prepared_audio.encoded_bytes, prepared_audio.encode_type):
                forward.send_encoded_audio(frame)
                bytes_sent += len(frame)
                frames_sent += 1
                time.sleep(per_frame_delay)

        return RandomAudioTalkResult(
            file_path=prepared_audio.file_path,
            duration_seconds=prepared_audio.duration_seconds,
            encode_type=prepared_audio.encode_type,
            voice_channel=prepared_audio.voice_channel,
            digit_sequence=prepared_audio.digit_sequence,
            bytes_sent=bytes_sent,
            frames_sent=frames_sent,
            frequency_profile=prepared_audio.frequency_profile,
        )

    def split_prepared_audio_frames(self, prepared_audio: PreparedRandomAudio) -> list[bytes]:
        return self._split_encoded_frames(prepared_audio.encoded_bytes, prepared_audio.encode_type)

    def play_random_audio_file(
        self,
        session: DeviceSession,
        duration_seconds: int = 4,
        voice_channel: Optional[int] = None,
        output_dir: str | Path | None = None,
        file_prefix: str = "random_audio",
        seed: Optional[int] = None,
        amplitude_ratio: float = 0.35,
        per_frame_delay: float = FRAME_MS / 1000.0,
        encoded_audio_callback: Optional[Callable[[bytes, int], None]] = None,
        digit_sequence: Optional[str] = None,
        fingerprint_source: Optional[str] = None,
    ) -> RandomAudioTalkResult:
        prepared_audio = self.prepare_random_audio_file(
            session=session,
            duration_seconds=duration_seconds,
            voice_channel=voice_channel,
            output_dir=output_dir,
            file_prefix=file_prefix,
            seed=seed,
            amplitude_ratio=amplitude_ratio,
            digit_sequence=digit_sequence,
            fingerprint_source=fingerprint_source,
        )
        return self.send_prepared_audio(
            session=session,
            prepared_audio=prepared_audio,
            per_frame_delay=per_frame_delay,
            encoded_audio_callback=encoded_audio_callback,
        )

    def generate_continuous_frequency_wav(
        self,
        file_path: str | Path,
        duration_seconds: int = 4,
        fingerprint_source: Optional[str] = None,
        seed: Optional[int] = None,
        amplitude_ratio: float = 0.35,
        sample_rate: int = WIDEBAND_SAMPLE_RATE,
        min_frequency: float = 900.0,
        max_frequency: float = 3200.0,
        segment_count: int = 8,
    ) -> tuple[bytes, tuple[float, ...]]:
        """Generate continuous wide-span tones for offline/device-fingerprint validation."""
        if duration_seconds <= 0:
            raise ValueError("duration_seconds must be positive")
        if not 0 < amplitude_ratio <= 1:
            raise ValueError("amplitude_ratio must be in (0, 1]")
        if segment_count < 2:
            raise ValueError("segment_count must be at least 2")
        if min_frequency <= 0 or max_frequency <= min_frequency:
            raise ValueError("invalid frequency range")
        nyquist_limit = sample_rate / 2 - 200.0
        if max_frequency > nyquist_limit:
            raise ValueError(
                f"sample_rate={sample_rate} cannot represent max_frequency={max_frequency}; "
                f"use sample_rate >= {int((max_frequency + 200.0) * 2)}"
            )

        file_path = Path(file_path)
        file_path.parent.mkdir(parents=True, exist_ok=True)
        fingerprint = self._fingerprint_bytes(fingerprint_source or "default", seed)
        frequencies = self._continuous_frequency_profile(
            fingerprint=fingerprint,
            min_frequency=min_frequency,
            max_frequency=max_frequency,
            segment_count=segment_count,
        )
        sample_count = sample_rate * duration_seconds
        amplitude = int(32767 * amplitude_ratio)
        samples = self._build_continuous_frequency_samples(
            sample_count=sample_count,
            sample_rate=sample_rate,
            frequencies=frequencies,
            amplitude=amplitude,
        )
        pcm_bytes = b"".join(int(sample).to_bytes(2, byteorder="little", signed=True) for sample in samples)

        with wave.open(str(file_path), "wb") as wav_file:
            wav_file.setnchannels(CHANNELS_MONO)
            wav_file.setsampwidth(SAMPLE_WIDTH_BYTES)
            wav_file.setframerate(sample_rate)
            wav_file.writeframes(pcm_bytes)

        return pcm_bytes, tuple(frequencies)

    def _build_output_path(self, host: str, output_dir: str | Path | None, prefix: str = "random_audio") -> Path:
        host_dir = "".join(char if char.isalnum() or char in "._-" else "_" for char in host)
        base_dir = Path(output_dir) if output_dir is not None else Path.cwd() / "recordings" / "use_cases" / host_dir
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        return base_dir / f"{prefix}_{timestamp}.wav"

    def _generate_digit_pcm_wav(
        self,
        file_path: Path,
        duration_seconds: int,
        amplitude_ratio: float,
        seed: Optional[int],
        digit_sequence: Optional[str],
        fingerprint_source: Optional[str],
    ) -> tuple[bytes, str]:
        file_path.parent.mkdir(parents=True, exist_ok=True)
        if digit_sequence is None:
            digit_sequence = self._build_device_fingerprint_digits(fingerprint_source, seed)
        if not digit_sequence or any(digit not in DIGIT_TONE_MAP for digit in digit_sequence):
            raise ValueError("digit_sequence must contain only digits 0-9")

        fingerprint = self._fingerprint_bytes(fingerprint_source or digit_sequence, seed)
        pitch_scale = 0.94 + (fingerprint[6] / 255.0) * 0.14
        gap_ratio = 0.025 + (fingerprint[7] / 255.0) * 0.035
        active_ratio = 0.70 + (fingerprint[8] / 255.0) * 0.18
        digit_count = len(digit_sequence)
        amplitude = int(32767 * amplitude_ratio)
        sample_count = SAMPLE_RATE_8K * duration_seconds
        pcm_samples = [0] * sample_count
        gap_sample_count = max(1, int(sample_count * gap_ratio))
        available_digit_samples = max(1, sample_count - gap_sample_count * (digit_count - 1))
        digit_sample_count = max(1, int(available_digit_samples * active_ratio / digit_count))
        total_digit_samples = digit_sample_count * digit_count + gap_sample_count * (digit_count - 1)
        cursor = max(0, (sample_count - total_digit_samples) // 2)

        for digit in digit_sequence:
            tone = self._build_digit_tone_samples(digit, digit_sample_count, amplitude, pitch_scale=pitch_scale)
            end = min(sample_count, cursor + len(tone))
            for index in range(cursor, end):
                pcm_samples[index] = tone[index - cursor]
            cursor = min(sample_count, end + gap_sample_count)

        pcm_bytes = b"".join(int(sample).to_bytes(2, byteorder="little", signed=True) for sample in pcm_samples)

        with wave.open(str(file_path), "wb") as wav_file:
            wav_file.setnchannels(CHANNELS_MONO)
            wav_file.setsampwidth(SAMPLE_WIDTH_BYTES)
            wav_file.setframerate(SAMPLE_RATE_8K)
            wav_file.writeframes(pcm_bytes)

        return pcm_bytes, digit_sequence

    def _build_device_fingerprint_digits(self, fingerprint_source: Optional[str], seed: Optional[int]) -> str:
        if seed is not None:
            generator = random.Random(seed)
            return "".join(generator.choice("0123456789") for _ in range(6))

        digest = self._fingerprint_bytes(fingerprint_source or "default", seed)
        digits = [str(byte % 10) for byte in digest[:6]]
        for index in range(1, len(digits)):
            if digits[index] == digits[index - 1]:
                digits[index] = str((int(digits[index]) + index + 3) % 10)
        return "".join(digits)

    def _fingerprint_bytes(self, fingerprint_source: str, seed: Optional[int]) -> bytes:
        source = f"{fingerprint_source}|{'' if seed is None else seed}"
        return hashlib.sha256(source.encode("utf-8")).digest()

    def _continuous_frequency_profile(
        self,
        fingerprint: bytes,
        min_frequency: float,
        max_frequency: float,
        segment_count: int,
    ) -> list[float]:
        span = max_frequency - min_frequency
        low_band = min_frequency + span * 0.05
        high_band = min_frequency + span * 0.95
        frequencies: list[float] = []
        for index in range(segment_count):
            byte = fingerprint[(index * 3) % len(fingerprint)]
            if index % 2 == 0:
                # Alternate low/high bands so adjacent sections have a large frequency span.
                base = low_band + (byte / 255.0) * span * 0.28
            else:
                base = high_band - (byte / 255.0) * span * 0.28
            if frequencies and abs(base - frequencies[-1]) < span * 0.35:
                if base < (min_frequency + max_frequency) / 2:
                    base = min(max_frequency, base + span * 0.45)
                else:
                    base = max(min_frequency, base - span * 0.45)
            frequencies.append(max(min_frequency, min(max_frequency, base)))
        return frequencies

    def _build_continuous_frequency_samples(
        self,
        sample_count: int,
        sample_rate: int,
        frequencies: list[float],
        amplitude: int,
    ) -> list[int]:
        samples: list[int] = []
        phase = 0.0
        segment_count = len(frequencies)
        for index in range(sample_count):
            position = index / max(1, sample_count - 1)
            scaled = position * (segment_count - 1)
            left_index = min(segment_count - 2, int(scaled))
            ratio = scaled - left_index
            smooth_ratio = 0.5 - 0.5 * math.cos(math.pi * ratio)
            frequency = frequencies[left_index] * (1.0 - smooth_ratio) + frequencies[left_index + 1] * smooth_ratio
            phase += 2.0 * math.pi * frequency / sample_rate
            envelope = 1.0
            fade_samples = max(1, min(sample_count // 20, sample_rate // 50))
            if index < fade_samples:
                envelope = index / fade_samples
            elif index >= sample_count - fade_samples:
                envelope = (sample_count - index - 1) / fade_samples
            samples.append(int(amplitude * envelope * math.sin(phase)))
        return samples

    def _build_digit_tone_samples(
        self,
        digit: str,
        sample_count: int,
        amplitude: int,
        pitch_scale: float = 1.0,
    ) -> list[int]:
        low_freq, high_freq = DIGIT_TONE_MAP[digit]
        low_freq *= pitch_scale
        high_freq *= pitch_scale
        samples: list[int] = []
        fade_samples = max(1, min(sample_count // 8, SAMPLE_RATE_8K // 100))
        for index in range(sample_count):
            t = index / SAMPLE_RATE_8K
            envelope = 1.0
            if index < fade_samples:
                envelope = index / fade_samples
            elif index >= sample_count - fade_samples:
                envelope = (sample_count - index - 1) / fade_samples
            mixed = 0.5 * (
                math.sin(2.0 * math.pi * low_freq * t) +
                math.sin(2.0 * math.pi * high_freq * t)
            )
            samples.append(int(amplitude * envelope * mixed))
        return samples

    def _encode_pcm_for_device(self, pcm_bytes: bytes, encode_type: int) -> bytes:
        if encode_type == PCM:
            return pcm_bytes
        if encode_type == G711_U:
            return bytes(self._linear_to_ulaw(self._read_sample(pcm_bytes, offset)) for offset in range(0, len(pcm_bytes), 2))
        if encode_type == G711_A:
            return bytes(self._linear_to_alaw(self._read_sample(pcm_bytes, offset)) for offset in range(0, len(pcm_bytes), 2))
        raise HikvisionSDKError("Unsupported encode type", error_message=f"encode_type={encode_type}")

    def _split_encoded_frames(self, encoded_bytes: bytes, encode_type: int) -> list[bytes]:
        frame_size = PCM_BYTES_PER_FRAME if encode_type == PCM else SAMPLES_PER_FRAME
        frames = [
            encoded_bytes[index:index + frame_size]
            for index in range(0, len(encoded_bytes), frame_size)
            if encoded_bytes[index:index + frame_size]
        ]
        if not frames:
            raise HikvisionSDKError("No encoded audio frames generated for sending")
        return frames

    @staticmethod
    def _read_sample(pcm_bytes: bytes, offset: int) -> int:
        return int.from_bytes(pcm_bytes[offset:offset + 2], byteorder="little", signed=True)

    @staticmethod
    def _linear_to_ulaw(sample: int) -> int:
        bias = 0x84
        clip = 32635

        if sample > clip:
            sample = clip
        elif sample < -clip:
            sample = -clip

        sign = 0x80 if sample < 0 else 0x00
        if sample < 0:
            sample = -sample

        sample = sample + bias
        exponent = 7
        mask = 0x4000
        while exponent > 0 and (sample & mask) == 0:
            mask >>= 1
            exponent -= 1
        mantissa = (sample >> (exponent + 3)) & 0x0F
        return (~(sign | (exponent << 4) | mantissa)) & 0xFF

    @staticmethod
    def _linear_to_alaw(sample: int) -> int:
        clip = 32767
        if sample > clip:
            sample = clip
        elif sample < -clip:
            sample = -clip

        sign = 0x80 if sample < 0 else 0x00
        if sample < 0:
            sample = -sample

        if sample < 256:
            mantissa = sample >> 4
            exponent = 0
        else:
            exponent = 7
            mask = 0x4000
            while exponent > 0 and (sample & mask) == 0:
                mask >>= 1
                exponent -= 1
            mantissa = (sample >> (exponent + 3)) & 0x0F

        return (sign | (exponent << 4) | mantissa) ^ 0xD5
