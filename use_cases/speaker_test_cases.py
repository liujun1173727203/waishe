from __future__ import annotations

import time
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Optional

from hikvision_isapi import (
    AudioInputCapabilityStatus,
    AudioIoVolumeStatus,
    CompositeStreamStatus,
    HikvisionIsapiClient,
)
from hikvision_voice import DeviceSession, HikvisionSDKError, HikvisionVoiceSDK
from video_analysis import ReferenceAudioMatchResult, RecordedVideoAnalyzer, SoundAnalysisResult

from .voice_talk_cases import PreparedRandomAudio, RandomAudioTalkResult, VoiceTalkUseCases


@dataclass(frozen=True)
class SpeakerTestResult:
    login_verified: bool
    audio_input_status: AudioInputCapabilityStatus
    volume_status: AudioIoVolumeStatus
    composite_status: CompositeStreamStatus
    record_file_path: Path
    reference_audio_path: Path
    extracted_audio_path: Path
    talk_result: RandomAudioTalkResult
    sound_result: SoundAnalysisResult
    match_result: ReferenceAudioMatchResult


class SpeakerTestUseCases:
    def __init__(
        self,
        sdk: HikvisionVoiceSDK,
        isapi: HikvisionIsapiClient,
        analyzer: Optional[RecordedVideoAnalyzer] = None,
        voice_talk_use_cases: Optional[VoiceTalkUseCases] = None,
    ) -> None:
        self.sdk = sdk
        self.isapi = isapi
        self.analyzer = analyzer or RecordedVideoAnalyzer()
        self.voice_talk_use_cases = voice_talk_use_cases or VoiceTalkUseCases(sdk)

    def run_speaker_test(
        self,
        session: DeviceSession,
        record_channel: Optional[int] = None,
        voice_channel: Optional[int] = None,
        record_duration_seconds: int = 10,
        send_duration_seconds: int = 3,
        output_dir: str | Path | None = None,
        similarity_threshold: float = 0.8,
        amplitude_ratio: float = 0.6,
        seed: Optional[int] = None,
        per_frame_delay: float = 0.02,
    ) -> SpeakerTestResult:
        if record_duration_seconds <= 0:
            raise ValueError("record_duration_seconds must be positive")
        if send_duration_seconds <= 0:
            raise ValueError("send_duration_seconds must be positive")
        if send_duration_seconds > record_duration_seconds:
            raise ValueError("send_duration_seconds must not exceed record_duration_seconds")
        if not 0 < similarity_threshold <= 1:
            raise ValueError("similarity_threshold must be in (0, 1]")

        audio_input_status = self.isapi.get_audio_input_capability_status(session)
        if not audio_input_status.supported:
            raise HikvisionSDKError(
                "device does not support audio input",
                api_name=f"GET {audio_input_status.request_path or '/ISAPI/System/Audio/capabilities'}",
            )

        target_record_channel = record_channel or session.default_preview_channel
        target_voice_channel = voice_channel or session.default_voice_channel
        base_dir = Path(output_dir) if output_dir is not None else self._default_output_dir(session.host)
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        record_file_path = base_dir / f"speaker_test_{timestamp}.mp4"
        extracted_audio_path = base_dir / f"speaker_test_{timestamp}.wav"

        volume_status = self.isapi.ensure_audio_input_output_volume_max(session=session)
        composite_status = self.isapi.ensure_composite_stream_recording_enabled(
            session=session,
            channel=target_record_channel,
        )
        if not composite_status.supported:
            raise HikvisionSDKError(
                f"device channel {target_record_channel} does not support composite stream recording",
                api_name="GET /ISAPI/Streaming/channels/<trackStreamID>/capabilities",
            )

        prepared_audio = self.voice_talk_use_cases.prepare_random_audio_file(
            session=session,
            duration_seconds=send_duration_seconds,
            voice_channel=target_voice_channel,
            output_dir=base_dir,
            seed=seed,
            amplitude_ratio=amplitude_ratio,
        )
        talk_result = self._record_and_send_audio(
            session=session,
            prepared_audio=prepared_audio,
            record_file_path=record_file_path,
            record_channel=target_record_channel,
            record_duration_seconds=record_duration_seconds,
            per_frame_delay=per_frame_delay,
        )

        sound_result = self.analyzer.analyze_sound_presence(
            video_path=record_file_path,
            extracted_audio_path=extracted_audio_path,
        )
        match_result = self.analyzer.detect_reference_audio(
            video_path=record_file_path,
            reference_audio_path=prepared_audio.file_path,
            extracted_audio_path=extracted_audio_path,
            score_threshold=similarity_threshold,
        )

        return SpeakerTestResult(
            login_verified=True,
            audio_input_status=audio_input_status,
            volume_status=volume_status,
            composite_status=composite_status,
            record_file_path=record_file_path,
            reference_audio_path=prepared_audio.file_path,
            extracted_audio_path=extracted_audio_path,
            talk_result=talk_result,
            sound_result=sound_result,
            match_result=match_result,
        )

    def _record_and_send_audio(
        self,
        session: DeviceSession,
        prepared_audio: PreparedRandomAudio,
        record_file_path: Path,
        record_channel: int,
        record_duration_seconds: int,
        per_frame_delay: float,
    ) -> RandomAudioTalkResult:
        frames = self.voice_talk_use_cases.split_prepared_audio_frames(prepared_audio)
        bytes_sent = 0
        frames_sent = 0
        start_time = time.time()

        with self.sdk.start_voice_forward(session=session, voice_channel=prepared_audio.voice_channel) as forward:
            # Keep talk open before recording so the media path matches the test case sequence.
            recorder = self.sdk.start_stream_record(
                session=session,
                file_path=record_file_path,
                channel=record_channel,
            )
            try:
                for frame in frames:
                    forward.send_encoded_audio(frame)
                    bytes_sent += len(frame)
                    frames_sent += 1
                    time.sleep(per_frame_delay)

                remaining = record_duration_seconds - (time.time() - start_time)
                if remaining > 0:
                    time.sleep(remaining)
            finally:
                recorder.stop()

        return RandomAudioTalkResult(
            file_path=prepared_audio.file_path,
            duration_seconds=prepared_audio.duration_seconds,
            encode_type=prepared_audio.encode_type,
            voice_channel=prepared_audio.voice_channel,
            bytes_sent=bytes_sent,
            frames_sent=frames_sent,
        )

    def _default_output_dir(self, host: str) -> Path:
        host_dir = "".join(char if char.isalnum() or char in "._-" else "_" for char in host)
        return Path.cwd() / "recordings" / "speaker_tests" / host_dir
