from __future__ import annotations

import random
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
SAMPLE_WIDTH_BYTES = 2
CHANNELS_MONO = 1
FRAME_MS = 20
SAMPLES_PER_FRAME = SAMPLE_RATE_8K * FRAME_MS // 1000
PCM_BYTES_PER_FRAME = SAMPLES_PER_FRAME * SAMPLE_WIDTH_BYTES


@dataclass(frozen=True)
class RandomAudioTalkResult:
    file_path: Path
    duration_seconds: int
    encode_type: int
    voice_channel: int
    bytes_sent: int
    frames_sent: int


@dataclass(frozen=True)
class PreparedRandomAudio:
    file_path: Path
    duration_seconds: int
    encode_type: int
    voice_channel: int
    pcm_bytes: bytes
    encoded_bytes: bytes
    frame_count: int


class VoiceTalkUseCases:
    def __init__(self, sdk: HikvisionVoiceSDK) -> None:
        self.sdk = sdk

    def prepare_random_audio_file(
        self,
        session: DeviceSession,
        duration_seconds: int = 3,
        voice_channel: Optional[int] = None,
        output_dir: str | Path | None = None,
        seed: Optional[int] = None,
        amplitude_ratio: float = 0.35,
        per_frame_delay: float = FRAME_MS / 1000.0,
        encoded_audio_callback: Optional[Callable[[bytes, int], None]] = None,
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
        wav_path = self._build_output_path(session.host, output_dir)
        pcm_bytes = self._generate_random_pcm_wav(
            file_path=wav_path,
            duration_seconds=duration_seconds,
            amplitude_ratio=amplitude_ratio,
            seed=seed,
        )
        encoded_bytes = self._encode_pcm_for_device(pcm_bytes, compress.encode_type)

        frames = self._split_encoded_frames(encoded_bytes, compress.encode_type)
        return PreparedRandomAudio(
            file_path=wav_path,
            duration_seconds=duration_seconds,
            encode_type=compress.encode_type,
            voice_channel=target_channel,
            pcm_bytes=pcm_bytes,
            encoded_bytes=encoded_bytes,
            frame_count=len(frames),
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
            bytes_sent=bytes_sent,
            frames_sent=frames_sent,
        )

    def split_prepared_audio_frames(self, prepared_audio: PreparedRandomAudio) -> list[bytes]:
        return self._split_encoded_frames(prepared_audio.encoded_bytes, prepared_audio.encode_type)

    def play_random_audio_file(
        self,
        session: DeviceSession,
        duration_seconds: int = 3,
        voice_channel: Optional[int] = None,
        output_dir: str | Path | None = None,
        seed: Optional[int] = None,
        amplitude_ratio: float = 0.35,
        per_frame_delay: float = FRAME_MS / 1000.0,
        encoded_audio_callback: Optional[Callable[[bytes, int], None]] = None,
    ) -> RandomAudioTalkResult:
        prepared_audio = self.prepare_random_audio_file(
            session=session,
            duration_seconds=duration_seconds,
            voice_channel=voice_channel,
            output_dir=output_dir,
            seed=seed,
            amplitude_ratio=amplitude_ratio,
        )
        return self.send_prepared_audio(
            session=session,
            prepared_audio=prepared_audio,
            per_frame_delay=per_frame_delay,
            encoded_audio_callback=encoded_audio_callback,
        )

    def _build_output_path(self, host: str, output_dir: str | Path | None, prefix: str = "random_audio") -> Path:
        host_dir = "".join(char if char.isalnum() or char in "._-" else "_" for char in host)
        base_dir = Path(output_dir) if output_dir is not None else Path.cwd() / "recordings" / "use_cases" / host_dir
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        return base_dir / f"{prefix}_{timestamp}.wav"

    def _generate_random_pcm_wav(
        self,
        file_path: Path,
        duration_seconds: int,
        amplitude_ratio: float,
        seed: Optional[int],
    ) -> bytes:
        file_path.parent.mkdir(parents=True, exist_ok=True)
        generator = random.Random(seed)
        amplitude = int(32767 * amplitude_ratio)
        sample_count = SAMPLE_RATE_8K * duration_seconds

        pcm_chunks: list[bytes] = []
        for _ in range(sample_count):
            sample = generator.randint(-amplitude, amplitude)
            pcm_chunks.append(int(sample).to_bytes(2, byteorder="little", signed=True))
        pcm_bytes = b"".join(pcm_chunks)

        with wave.open(str(file_path), "wb") as wav_file:
            wav_file.setnchannels(CHANNELS_MONO)
            wav_file.setsampwidth(SAMPLE_WIDTH_BYTES)
            wav_file.setframerate(SAMPLE_RATE_8K)
            wav_file.writeframes(pcm_bytes)

        return pcm_bytes

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
