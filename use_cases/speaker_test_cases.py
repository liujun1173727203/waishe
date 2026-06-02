from __future__ import annotations

import time
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Optional

from hikvision_isapi import (
    AudioInputCapabilityStatus,
    CompositeStreamStatus,
    HikvisionIsapiClient,
    TwoWayAudioChannelStatus,
)
from hikvision_voice import DeviceSession, HikvisionSDKError, HikvisionVoiceSDK
from video_analysis import ReferenceAudioMatchResult, RecordedVideoAnalyzer, SoundAnalysisResult

from .voice_talk_cases import PreparedRandomAudio, RandomAudioTalkResult, VoiceTalkUseCases


PRE_TALK_RECORD_SECONDS = 2.0
POST_TALK_RECORD_SECONDS = 3.0
POST_STOP_SETTLE_SECONDS = 3.0


@dataclass(frozen=True)
class RecorderDeviceConfig:
    host: str = "10.40.230.23"
    port: int = 8000
    username: str = "admin"
    password: str = "asdf!234"
    channel: int = 0
    voice_channel: int = 1


@dataclass(frozen=True)
class SpeakerTestResult:
    login_verified: bool
    audio_input_status: AudioInputCapabilityStatus
    two_way_audio_status: TwoWayAudioChannelStatus
    recorder_audio_status: TwoWayAudioChannelStatus
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
        recorder_sdk: Optional[HikvisionVoiceSDK] = None,
        analyzer: Optional[RecordedVideoAnalyzer] = None,
        voice_talk_use_cases: Optional[VoiceTalkUseCases] = None,
        recorder_device: Optional[RecorderDeviceConfig] = None,
    ) -> None:
        self.sdk = sdk
        self.recorder_sdk = recorder_sdk or sdk
        self.isapi = isapi
        self.analyzer = analyzer or RecordedVideoAnalyzer()
        self.voice_talk_use_cases = voice_talk_use_cases or VoiceTalkUseCases(sdk)
        self.recorder_device = recorder_device or RecorderDeviceConfig()

    def _log_step(self, message: str) -> None:
        print(f"[speaker-test] {message}", flush=True)

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
        digit_sequence: Optional[str] = None,
        fingerprint_source: Optional[str] = None,
    ) -> SpeakerTestResult:
        if record_duration_seconds <= 0:
            raise ValueError("record_duration_seconds must be positive")
        if send_duration_seconds <= 0:
            raise ValueError("send_duration_seconds must be positive")
        if not 0 < similarity_threshold <= 1:
            raise ValueError("similarity_threshold must be in (0, 1]")
        expected_record_seconds = PRE_TALK_RECORD_SECONDS + send_duration_seconds + POST_TALK_RECORD_SECONDS
        minimum_expected_record_seconds = max(1.0, expected_record_seconds - 1.0)

        self._log_step(f"start host={session.host}")
        self._log_step(f"prepare recorder device host={self.recorder_device.host}:{self.recorder_device.port}")
        self._log_step(
            "use separate sdk instances "
            f"talk_sdk={'yes' if self.recorder_sdk is not self.sdk else 'no'}"
        )
        self._log_step(f"check analysis dependency ffmpeg path={self.analyzer.ffmpeg_path}")
        resolved_ffmpeg = self.analyzer.resolve_ffmpeg()
        self._log_step(f"analysis dependency ready ffmpeg={resolved_ffmpeg}")
        self._log_step(
            f"expected record timeline pre_talk={PRE_TALK_RECORD_SECONDS:.2f}s "
            f"talk={send_duration_seconds:.2f}s post_talk={POST_TALK_RECORD_SECONDS:.2f}s "
            f"total>={minimum_expected_record_seconds:.2f}s"
        )
        self._log_step("set sdk talk mode to library mode")
        self.sdk.set_talk_mode(use_windows_api=False)

        self._log_step("check test device MicIn capability")
        audio_input_status = self.isapi.get_audio_input_capability_status(session)
        if not audio_input_status.supported:
            raise HikvisionSDKError(
                "device does not support MicIn audio input",
                api_name=f"GET {audio_input_status.request_path or '/ISAPI/System/TwoWayAudio/channels/capabilities'}",
            )
        self._log_step(f"test device audio input supported path={audio_input_status.request_path}")

        target_voice_channel = voice_channel or session.default_voice_channel
        base_dir = Path(output_dir) if output_dir is not None else self._default_output_dir(session.host)
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        test_id = f"{session.host.replace('.', '_')}_{timestamp}"
        record_file_path = base_dir / f"speaker_test_record_{test_id}.mp4"
        extracted_audio_path = base_dir / f"speaker_test_record_audio_{test_id}.wav"

        recorder_session: Optional[DeviceSession] = None
        recorder = None
        record_started_at = 0.0
        record_size = 0
        prepared_audio: Optional[PreparedRandomAudio] = None
        talk_result: Optional[RandomAudioTalkResult] = None
        sound_result: Optional[SoundAnalysisResult] = None
        match_result: Optional[ReferenceAudioMatchResult] = None
        pending_error: Optional[BaseException] = None
        try:
            recorder_session = self.recorder_sdk.login(
                self.recorder_device.host,
                self.recorder_device.port,
                self.recorder_device.username,
                self.recorder_device.password,
            )
            recorder_channel = record_channel or self.recorder_device.channel or recorder_session.default_preview_channel
            self._log_step(f"recorder device login verified host={recorder_session.host}")

            self._log_step("configure recorder device A to MicIn and enable audio")
            recorder_audio_status = self.isapi.ensure_two_way_audio_channel_ready(
                session=recorder_session,
                channel=self.recorder_device.voice_channel,
                require_input_type="MicIn",
                enable=True,
                maximize_microphone_volume=True,
                maximize_speaker_volume=True,
            )
            if not recorder_audio_status.micin_supported:
                raise HikvisionSDKError(
                    "recorder device does not support MicIn",
                    api_name=recorder_audio_status.capability_path or "GET /ISAPI/System/TwoWayAudio/channels/capabilities",
                )
            self._log_step(
                "recorder device ready "
                f"input_type={recorder_audio_status.input_type} "
                f"enabled={recorder_audio_status.enabled} "
                f"microphone_volume={recorder_audio_status.microphone_volume}/{recorder_audio_status.microphone_volume_max} "
                f"speaker_volume={recorder_audio_status.speaker_volume}/{recorder_audio_status.speaker_volume_max} "
                f"changed={recorder_audio_status.changed}"
            )

            self._log_step(f"enable recorder device A composite stream channel={recorder_channel}")
            composite_status = self.isapi.ensure_composite_stream_recording_enabled(
                session=recorder_session,
                channel=recorder_channel,
            )
            if not composite_status.supported:
                raise HikvisionSDKError(
                    f"recorder device channel {recorder_channel} does not support composite stream recording",
                    api_name="GET /ISAPI/Streaming/channels/<trackStreamID>/capabilities",
                )
            self._log_step(
                f"recorder composite stream ready trackStreamID={composite_status.track_stream_id} "
                f"audio_enabled={composite_status.audio_enabled} changed={composite_status.changed}"
            )

            self._log_step("configure test device to MicIn + Speaker + max volume")
            two_way_audio_status = self.isapi.ensure_two_way_audio_speaker_ready(session=session)
            if not two_way_audio_status.micin_supported:
                raise HikvisionSDKError(
                    "test device does not support MicIn",
                    api_name=two_way_audio_status.capability_path or "GET /ISAPI/System/TwoWayAudio/channels/capabilities",
                )
            if not two_way_audio_status.speaker_supported:
                raise HikvisionSDKError(
                    "test device does not support Speaker output",
                    api_name=two_way_audio_status.capability_path or "GET /ISAPI/System/TwoWayAudio/channels/capabilities",
                )
            self._log_step(
                "test device ready "
                f"input_type={two_way_audio_status.input_type} "
                f"output_type={two_way_audio_status.output_type} "
                f"microphone_volume={two_way_audio_status.microphone_volume}/{two_way_audio_status.microphone_volume_max} "
                f"speaker_volume={two_way_audio_status.speaker_volume}/{two_way_audio_status.speaker_volume_max} "
                f"changed={two_way_audio_status.changed}"
            )

            self._log_step(f"prepare speaker validation audio voice_channel={target_voice_channel} duration={send_duration_seconds}s")
            prepared_audio = self.voice_talk_use_cases.prepare_random_audio_file(
                session=session,
                duration_seconds=send_duration_seconds,
                voice_channel=target_voice_channel,
                output_dir=base_dir,
                file_prefix=f"speaker_test_reference_{test_id}",
                seed=seed,
                amplitude_ratio=amplitude_ratio,
                digit_sequence=digit_sequence,
                fingerprint_source=fingerprint_source or session.host,
            )
            self._log_step(f"reference audio ready path={prepared_audio.file_path}")
            self._log_step(
                f"digit sequence={prepared_audio.digit_sequence} "
                f"test_tone_id={fingerprint_source or session.host}"
            )

            self._log_step(f"start recorder device A stream channel={recorder_channel} output={record_file_path}")
            recorder = self.recorder_sdk.start_stream_record(
                session=recorder_session,
                file_path=record_file_path,
                channel=recorder_channel,
            )
            self._log_step(
                f"recorder stream started handle={recorder.handle} "
                f"channel={recorder_channel} path={record_file_path}"
            )
            record_started_at = time.monotonic()
            if not recorder.wait_for_first_data(timeout_seconds=min(5.0, max(1.0, record_duration_seconds / 2))):
                raise HikvisionSDKError(
                    "recorder device did not receive media data in time",
                    api_name="NET_DVR_RealPlay_V40/NET_DVR_SaveRealData",
                )
            self._log_step(f"recorder stream data ready bytes={recorder.received_bytes}")
            self._log_step(f"keep recorder device A recording for {PRE_TALK_RECORD_SECONDS:.2f}s before talk starts")
            time.sleep(PRE_TALK_RECORD_SECONDS)

            self._log_step(
                f"start random audio talk like demo_random_audio_talk_use_case "
                f"voice_channel={prepared_audio.voice_channel} frames={prepared_audio.frame_count}"
            )
            talk_result = self.voice_talk_use_cases.send_prepared_audio(
                session=session,
                prepared_audio=prepared_audio,
                per_frame_delay=per_frame_delay,
            )
            self._log_step(
                f"audio sent digits={talk_result.digit_sequence} frames={talk_result.frames_sent} bytes={talk_result.bytes_sent}"
            )

            elapsed_record_seconds = max(0.0, time.monotonic() - record_started_at)
            self._log_step(
                f"playback finished, recorded={elapsed_record_seconds:.2f}s, "
                f"keep recorder device A recording for {POST_TALK_RECORD_SECONDS:.2f}s"
            )
            time.sleep(POST_TALK_RECORD_SECONDS)
        except BaseException as exc:
            pending_error = exc
            self._log_step(f"ERROR during speaker test flow: {exc}")
        finally:
            if recorder is not None:
                self._log_step("stop recorder device A stream")
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
                    f"recorder device A stream stopped, wait {POST_STOP_SETTLE_SECONDS:.2f}s before file flush check"
                )
                time.sleep(POST_STOP_SETTLE_SECONDS)
                record_size = self._wait_for_record_file(record_file_path)
                if record_size <= 0:
                    raise HikvisionSDKError(
                        f"record file is empty: {record_file_path}",
                        error_message="speaker test recording size is 0 bytes",
                    )
                total_record_seconds = max(0.0, time.monotonic() - record_started_at) if record_started_at else 0.0
                self._log_step(f"record file ready size={record_size} bytes duration={total_record_seconds:.2f}s")
            if recorder_session is not None:
                self.recorder_sdk.logout(recorder_session)
                self._log_step(f"recorder device logout host={recorder_session.host}")
        if record_size > 0 and prepared_audio is not None:
            self._log_step("analyze recorded audio presence")
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

            self._log_step("match recorded audio against this device speaker validation audio")
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
            f"digit_sequence={talk_result.digit_sequence} "
            f"has_sound={sound_result.has_sound} "
            f"match={match_result.matched} "
            f"score={match_result.best_score:.4f} "
            f"threshold={match_result.threshold:.2f}"
        )
        self._log_step("speaker test completed")

        return SpeakerTestResult(
            login_verified=True,
            audio_input_status=audio_input_status,
            two_way_audio_status=two_way_audio_status,
            recorder_audio_status=recorder_audio_status,
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
        return Path.cwd() / "recordings" / "speaker_tests" / host_dir

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
