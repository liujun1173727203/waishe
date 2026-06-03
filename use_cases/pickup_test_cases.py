from __future__ import annotations

import time
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Optional

from hikvision_isapi import (
    CompositeStreamStatus,
    HikvisionIsapiClient,
    TwoWayAudioChannelStatus,
)
from hikvision_voice import DeviceSession, HikvisionSDKError, HikvisionVoiceSDK
from video_analysis import ReferenceAudioMatchResult, RecordedVideoAnalyzer, SoundAnalysisResult

from .speaker_test_cases import POST_STOP_SETTLE_SECONDS, POST_TALK_RECORD_SECONDS, PRE_TALK_RECORD_SECONDS
from .voice_talk_cases import PreparedRandomAudio, RandomAudioTalkResult, VoiceTalkUseCases


@dataclass(frozen=True)
class PlaybackDeviceConfig:
    host: str = "10.40.230.23"
    port: int = 8000
    username: str = "admin"
    password: str = "asdf!234"
    voice_channel: int = 1


@dataclass(frozen=True)
class PickupTestResult:
    test_device_audio_status: TwoWayAudioChannelStatus
    playback_device_audio_status: TwoWayAudioChannelStatus
    composite_status: CompositeStreamStatus
    record_file_path: Path
    reference_audio_path: Path
    extracted_audio_path: Path
    talk_result: RandomAudioTalkResult
    sound_result: SoundAnalysisResult
    match_result: ReferenceAudioMatchResult


class PickupTestUseCases:
    def __init__(
        self,
        sdk: HikvisionVoiceSDK,
        isapi: HikvisionIsapiClient,
        playback_sdk: Optional[HikvisionVoiceSDK] = None,
        analyzer: Optional[RecordedVideoAnalyzer] = None,
        voice_talk_use_cases: Optional[VoiceTalkUseCases] = None,
        playback_device: Optional[PlaybackDeviceConfig] = None,
    ) -> None:
        self.sdk = sdk
        self.playback_sdk = playback_sdk or sdk
        self.isapi = isapi
        self.analyzer = analyzer or RecordedVideoAnalyzer()
        self.voice_talk_use_cases = voice_talk_use_cases or VoiceTalkUseCases(self.playback_sdk)
        self.playback_device = playback_device or PlaybackDeviceConfig()

    def _log_step(self, message: str) -> None:
        print(f"[pickup-test] {message}", flush=True)

    def run_pickup_test(
        self,
        session: DeviceSession,
        record_channel: Optional[int] = None,
        record_duration_seconds: int = 10,
        send_duration_seconds: int = 4,
        output_dir: str | Path | None = None,
        similarity_threshold: float = 0.8,
        amplitude_ratio: float = 0.6,
        seed: Optional[int] = None,
        per_frame_delay: float = 0.02,
        digit_sequence: Optional[str] = None,
        fingerprint_source: Optional[str] = None,
        test_device_input_type: str = "MicIn",
        test_device_output_type: str = "Speaker",
        audio_compression_type: Optional[str] = None,
    ) -> PickupTestResult:
        if record_duration_seconds <= 0:
            raise ValueError("record_duration_seconds must be positive")
        if send_duration_seconds <= 0:
            raise ValueError("send_duration_seconds must be positive")
        if not 0 < similarity_threshold <= 1:
            raise ValueError("similarity_threshold must be in (0, 1]")

        expected_record_seconds = PRE_TALK_RECORD_SECONDS + send_duration_seconds + POST_TALK_RECORD_SECONDS
        minimum_expected_record_seconds = max(1.0, expected_record_seconds - 1.0)

        self._log_step(f"start test_device={session.host}")
        self._log_step(f"prepare playback device A host={self.playback_device.host}:{self.playback_device.port}")
        self._log_step(f"check analysis dependency ffmpeg path={self.analyzer.ffmpeg_path}")
        resolved_ffmpeg = self.analyzer.resolve_ffmpeg()
        self._log_step(f"analysis dependency ready ffmpeg={resolved_ffmpeg}")
        self._log_step(
            f"expected record timeline pre_talk={PRE_TALK_RECORD_SECONDS:.2f}s "
            f"talk={send_duration_seconds:.2f}s post_talk={POST_TALK_RECORD_SECONDS:.2f}s "
            f"total>={minimum_expected_record_seconds:.2f}s"
        )

        self.sdk.set_talk_mode(use_windows_api=False)
        self.playback_sdk.set_talk_mode(use_windows_api=False)

        base_dir = Path(output_dir) if output_dir is not None else self._default_output_dir(session.host)
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        compression_suffix = self._filename_token(audio_compression_type or "current")
        test_id = f"{session.host.replace('.', '_')}_{compression_suffix}_{timestamp}"
        record_file_path = base_dir / f"pickup_test_record_{test_id}.mp4"
        extracted_audio_path = base_dir / f"pickup_test_record_audio_{test_id}.wav"

        playback_session: Optional[DeviceSession] = None
        recorder = None
        record_started_at = 0.0
        record_size = 0
        prepared_audio: Optional[PreparedRandomAudio] = None
        talk_result: Optional[RandomAudioTalkResult] = None
        sound_result: Optional[SoundAnalysisResult] = None
        match_result: Optional[ReferenceAudioMatchResult] = None
        pending_error: Optional[BaseException] = None
        try:
            self._log_step(
                f"configure test device to {test_device_input_type} + {test_device_output_type} + max volume"
            )
            test_device_audio_status = self.isapi.ensure_two_way_audio_channel_ready(
                session=session,
                require_input_type=test_device_input_type,
                require_output_type=test_device_output_type,
                enable=True,
                maximize_microphone_volume=True,
                maximize_speaker_volume=True,
            )
            if not self._input_type_supported(test_device_audio_status, test_device_input_type):
                raise HikvisionSDKError(
                    f"test device does not support {test_device_input_type}",
                    api_name=test_device_audio_status.capability_path or "GET /ISAPI/System/TwoWayAudio/channels/capabilities",
                )
            if not self._output_type_supported(test_device_audio_status, test_device_output_type):
                raise HikvisionSDKError(
                    f"test device does not support {test_device_output_type} output",
                    api_name=test_device_audio_status.capability_path or "GET /ISAPI/System/TwoWayAudio/channels/capabilities",
                )
            if test_device_audio_status.input_type != test_device_input_type:
                raise HikvisionSDKError(
                    f"test device failed to switch audioInputType to {test_device_input_type}",
                    api_name=test_device_audio_status.config_path or "PUT /ISAPI/System/TwoWayAudio/channels/<channel>",
                    error_message=f"current_input_type={test_device_audio_status.input_type}",
                )
            if test_device_audio_status.output_type != test_device_output_type:
                raise HikvisionSDKError(
                    f"test device failed to switch audioOutputType to {test_device_output_type}",
                    api_name=test_device_audio_status.config_path or "PUT /ISAPI/System/TwoWayAudio/channels/<channel>",
                    error_message=f"current_output_type={test_device_audio_status.output_type}",
                )
            self._log_step(
                "test device ready "
                f"input_type={test_device_audio_status.input_type} "
                f"output_type={test_device_audio_status.output_type} "
                f"audio_compression_type={test_device_audio_status.audio_compression_type or 'unknown'} "
                f"microphone_volume={test_device_audio_status.microphone_volume}/{test_device_audio_status.microphone_volume_max} "
                f"speaker_volume={test_device_audio_status.speaker_volume}/{test_device_audio_status.speaker_volume_max} "
                f"changed={test_device_audio_status.changed}"
            )

            playback_session = self.playback_sdk.login(
                self.playback_device.host,
                self.playback_device.port,
                self.playback_device.username,
                self.playback_device.password,
            )
            self._log_step(f"playback device A login verified host={playback_session.host}")
            self._log_step("configure playback device A to MicIn + Speaker + max volume")
            playback_device_audio_status = self.isapi.ensure_two_way_audio_channel_ready(
                session=playback_session,
                require_input_type="MicIn",
                require_output_type="Speaker",
                require_audio_compression_type=audio_compression_type,
                enable=True,
                maximize_microphone_volume=True,
                maximize_speaker_volume=True,
            )
            if not playback_device_audio_status.micin_supported:
                raise HikvisionSDKError(
                    "playback device A does not support MicIn",
                    api_name=playback_device_audio_status.capability_path or "GET /ISAPI/System/TwoWayAudio/channels/capabilities",
                )
            if not playback_device_audio_status.speaker_supported:
                raise HikvisionSDKError(
                    "playback device A does not support Speaker output",
                    api_name=playback_device_audio_status.capability_path or "GET /ISAPI/System/TwoWayAudio/channels/capabilities",
                )
            if audio_compression_type and not self._audio_compression_type_supported(playback_device_audio_status, audio_compression_type):
                raise HikvisionSDKError(
                    f"playback device A does not support audioCompressionType={audio_compression_type}",
                    api_name=playback_device_audio_status.capability_path or "GET /ISAPI/System/TwoWayAudio/channels/capabilities",
                )
            if audio_compression_type and playback_device_audio_status.audio_compression_type != audio_compression_type:
                raise HikvisionSDKError(
                    f"playback device A failed to switch audioCompressionType to {audio_compression_type}",
                    api_name=playback_device_audio_status.config_path or "PUT /ISAPI/System/TwoWayAudio/channels/<channel>",
                    error_message=f"current_audio_compression_type={playback_device_audio_status.audio_compression_type}",
                )
            self._log_step(
                "playback device A ready "
                f"input_type={playback_device_audio_status.input_type} "
                f"output_type={playback_device_audio_status.output_type} "
                f"audio_compression_type={playback_device_audio_status.audio_compression_type or 'unknown'} "
                f"microphone_volume={playback_device_audio_status.microphone_volume}/{playback_device_audio_status.microphone_volume_max} "
                f"speaker_volume={playback_device_audio_status.speaker_volume}/{playback_device_audio_status.speaker_volume_max} "
                f"changed={playback_device_audio_status.changed}"
            )

            recorder_channel = record_channel or session.default_preview_channel
            self._log_step(f"enable test device composite stream channel={recorder_channel}")
            composite_status = self.isapi.ensure_composite_stream_recording_enabled(
                session=session,
                channel=recorder_channel,
            )
            if not composite_status.supported:
                raise HikvisionSDKError(
                    f"test device channel {recorder_channel} does not support composite stream recording",
                    api_name="GET /ISAPI/Streaming/channels/<trackStreamID>/capabilities",
                )
            self._log_step(
                f"test device composite stream ready trackStreamID={composite_status.track_stream_id} "
                f"audio_enabled={composite_status.audio_enabled} changed={composite_status.changed}"
            )

            self._log_step(
                f"prepare pickup validation audio voice_channel={self.playback_device.voice_channel} "
                f"duration={send_duration_seconds}s"
            )
            prepared_audio = self.voice_talk_use_cases.prepare_random_audio_file(
                session=playback_session,
                duration_seconds=send_duration_seconds,
                voice_channel=self.playback_device.voice_channel,
                output_dir=base_dir,
                file_prefix=f"pickup_test_reference_{test_id}",
                seed=seed,
                amplitude_ratio=amplitude_ratio,
                digit_sequence=digit_sequence,
                fingerprint_source=fingerprint_source or self.playback_device.host,
            )
            self._log_step(f"reference audio ready path={prepared_audio.file_path}")
            self._log_step(
                f"digit sequence={prepared_audio.digit_sequence} "
                f"test_tone_id={fingerprint_source or self.playback_device.host}"
            )

            self._log_step(f"start test device recording channel={recorder_channel} output={record_file_path}")
            recorder = self.sdk.start_stream_record(
                session=session,
                file_path=record_file_path,
                channel=recorder_channel,
            )
            self._log_step(
                f"test device stream started handle={recorder.handle} "
                f"channel={recorder_channel} path={record_file_path}"
            )
            record_started_at = time.monotonic()
            if not recorder.wait_for_first_data(timeout_seconds=min(5.0, max(1.0, record_duration_seconds / 2))):
                raise HikvisionSDKError(
                    "test device did not receive media data in time",
                    api_name="NET_DVR_RealPlay_V40/NET_DVR_SaveRealData",
                )
            self._log_step(f"test device stream data ready bytes={recorder.received_bytes}")
            self._log_step(f"keep test device recording for {PRE_TALK_RECORD_SECONDS:.2f}s before playback starts")
            time.sleep(PRE_TALK_RECORD_SECONDS)

            self._log_step(
                f"start playback device A audio talk voice_channel={prepared_audio.voice_channel} "
                f"frames={prepared_audio.frame_count}"
            )
            talk_result = self.voice_talk_use_cases.send_prepared_audio(
                session=playback_session,
                prepared_audio=prepared_audio,
                per_frame_delay=per_frame_delay,
            )
            self._log_step(
                f"audio sent to playback device A digits={talk_result.digit_sequence} "
                f"frames={talk_result.frames_sent} bytes={talk_result.bytes_sent}"
            )

            elapsed_record_seconds = max(0.0, time.monotonic() - record_started_at)
            self._log_step(
                f"playback finished, recorded={elapsed_record_seconds:.2f}s, "
                f"keep test device recording for {POST_TALK_RECORD_SECONDS:.2f}s"
            )
            time.sleep(POST_TALK_RECORD_SECONDS)
        except BaseException as exc:
            pending_error = exc
            self._log_step(f"ERROR during pickup test flow: {exc}")
        finally:
            if recorder is not None:
                self._log_step("stop test device recording")
                self._log_step(
                    "stop recorder snapshot "
                    f"handle={recorder.handle} "
                    f"received_bytes={recorder.received_bytes} "
                    f"file_size_bytes={recorder.file_size_bytes} "
                    f"path={record_file_path}"
                )
                self._log_step("call NET_DVR_StopSaveRealData")
                recorder.stop_save_real_data()
                self._log_step("NET_DVR_StopSaveRealData done")
                self._log_step("call NET_DVR_StopRealPlay")
                recorder.stop_real_play()
                self._log_step("NET_DVR_StopRealPlay done")
                self._log_step(
                    f"test device recording stopped, wait {POST_STOP_SETTLE_SECONDS:.2f}s before file flush check"
                )
                time.sleep(POST_STOP_SETTLE_SECONDS)
                record_size = self._wait_for_record_file(record_file_path)
                if record_size <= 0:
                    raise HikvisionSDKError(
                        f"record file is empty: {record_file_path}",
                        error_message="pickup test recording size is 0 bytes",
                    )
                total_record_seconds = max(0.0, time.monotonic() - record_started_at) if record_started_at else 0.0
                self._log_step(f"record file ready size={record_size} bytes duration={total_record_seconds:.2f}s")
            if playback_session is not None:
                self.playback_sdk.logout(playback_session)
                self._log_step(f"playback device A logout host={playback_session.host}")

        if record_size > 0 and prepared_audio is not None:
            self._log_step("analyze test device recorded audio presence")
            sound_result = self.analyzer.analyze_sound_presence(
                video_path=record_file_path,
                extracted_audio_path=extracted_audio_path,
            )
            analyzed_duration_seconds = sound_result.frame_count * self.analyzer.frame_ms / 1000.0
            self._log_step(
                f"sound analysis done has_sound={sound_result.has_sound} "
                f"duration={analyzed_duration_seconds:.2f}s extracted={extracted_audio_path}"
            )
            if analyzed_duration_seconds < minimum_expected_record_seconds:
                short_record_error = HikvisionSDKError(
                    "recorded media duration is shorter than expected",
                    error_message=(
                        f"actual_duration={analyzed_duration_seconds:.2f}s, "
                        f"expected_duration>={minimum_expected_record_seconds:.2f}s"
                    ),
                )
                self._log_step(f"ERROR short recording detected: {short_record_error}")
                if pending_error is None:
                    pending_error = short_record_error

            self._log_step("match recorded audio against playback device A validation audio")
            match_result = self.analyzer.detect_reference_audio(
                video_path=record_file_path,
                reference_audio_path=prepared_audio.file_path,
                extracted_audio_path=extracted_audio_path,
                score_threshold=similarity_threshold,
                expected_digit_sequence=prepared_audio.digit_sequence,
            )
            self._log_step(
                f"match analysis done matched={match_result.matched} "
                f"score={match_result.best_score:.4f} threshold={match_result.threshold:.2f} "
                f"expected_digits={match_result.expected_digit_sequence} "
                f"detected_digits={match_result.detected_digit_sequence}"
            )
        else:
            self._log_step("skip analysis because no valid recording or reference audio is available")

        if pending_error is not None:
            self._log_step(f"ERROR final result: {pending_error}")
            raise pending_error

        self._log_step(
            "final conclusion "
            f"record={record_file_path} "
            f"reference={prepared_audio.file_path} "
            f"audio_compression_type={playback_device_audio_status.audio_compression_type or 'unknown'} "
            f"digit_sequence={talk_result.digit_sequence} "
            f"has_sound={sound_result.has_sound} "
            f"match={match_result.matched} "
            f"score={match_result.best_score:.4f} "
            f"threshold={match_result.threshold:.2f}"
        )
        self._log_step("pickup test completed")

        return PickupTestResult(
            test_device_audio_status=test_device_audio_status,
            playback_device_audio_status=playback_device_audio_status,
            composite_status=composite_status,
            record_file_path=record_file_path,
            reference_audio_path=prepared_audio.file_path,
            extracted_audio_path=extracted_audio_path,
            talk_result=talk_result,
            sound_result=sound_result,
            match_result=match_result,
        )

    def _default_output_dir(self, host: str) -> Path:
        host_dir = "".join(char if char.isalnum() or char in "._-" else "_" for char in host)
        return Path.cwd() / "recordings" / "pickup_tests" / host_dir

    def _wait_for_record_file(self, record_file_path: Path, timeout_seconds: float = 2.0) -> int:
        deadline = time.time() + timeout_seconds
        size = 0
        while time.time() < deadline:
            if record_file_path.exists():
                size = record_file_path.stat().st_size
                if size > 0:
                    return size
            time.sleep(0.1)
        return size

    def _input_type_supported(self, status: TwoWayAudioChannelStatus, input_type: str) -> bool:
        if input_type == "MicIn":
            return status.micin_supported
        return input_type in status.input_type_options

    def _output_type_supported(self, status: TwoWayAudioChannelStatus, output_type: str) -> bool:
        if output_type == "Speaker":
            return status.speaker_supported
        return output_type in status.output_type_options

    def _audio_compression_type_supported(self, status: TwoWayAudioChannelStatus, audio_compression_type: str) -> bool:
        return (
            not status.audio_compression_type_options
            or audio_compression_type in status.audio_compression_type_options
        )

    def _filename_token(self, value: str) -> str:
        return "".join(char if char.isalnum() or char in "._-" else "_" for char in value).strip("_") or "unknown"
