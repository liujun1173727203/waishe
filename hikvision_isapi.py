from __future__ import annotations

import urllib.parse
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from typing import Optional

import requests

from hikvision_voice import DeviceSession, HikvisionSDKError, HikvisionVoiceSDK, STREAM_TYPE_MAIN


ISAPI_SCHEMA = "http://www.isapi.org/ver20/XMLSchema"


def _local_name(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def _find_first_by_local_name(root: ET.Element, local_name: str) -> Optional[ET.Element]:
    for node in root.iter():
        if _local_name(node.tag) == local_name:
            return node
    return None


def _child_tag_like(node: ET.Element, local_name: str) -> str:
    if node.tag.startswith("{"):
        namespace = node.tag.split("}", 1)[0][1:]
        return f"{{{namespace}}}{local_name}"
    return local_name


def _namespace_of(node: ET.Element) -> Optional[str]:
    if node.tag.startswith("{"):
        return node.tag.split("}", 1)[0][1:]
    return None


@dataclass(frozen=True)
class CompositeStreamStatus:
    supported: bool
    track_stream_id: int
    audio_enabled: bool
    changed: bool


@dataclass(frozen=True)
class AudioVolumeStatus:
    supported: bool
    changed: bool
    value: int
    maximum: int
    request_path: str


@dataclass(frozen=True)
class AudioIoVolumeStatus:
    input_status: AudioVolumeStatus
    output_status: AudioVolumeStatus


@dataclass(frozen=True)
class AudioInputCapabilityStatus:
    supported: bool
    request_path: str


@dataclass(frozen=True)
class ImageChannelCapabilityStatus:
    channel_ids: tuple[int, ...]
    source: str
    request_path: str


@dataclass(frozen=True)
class IrcutFilterStatus:
    supported: bool
    changed: bool
    capability_path: str
    config_path: str
    filter_type: str
    filter_type_options: tuple[str, ...]


@dataclass(frozen=True)
class SupplementLightStatus:
    supported: bool
    changed: bool
    capability_path: str
    config_path: str
    enabled: Optional[bool]
    brightness: Optional[int]
    brightness_min: int
    brightness_max: int
    mode: str


@dataclass(frozen=True)
class IrLightCapabilityStatus:
    supported: bool
    capability_path: str
    mode_options: tuple[str, ...]
    brightness_min: int
    brightness_max: int


@dataclass(frozen=True)
class IrLightStatus:
    supported: bool
    changed: bool
    capability_path: str
    config_path: str
    mode: str
    mode_options: tuple[str, ...]
    brightness_limit: int
    brightness_min: int
    brightness_max: int


@dataclass(frozen=True)
class MixedSupplementLightStatus:
    supported: bool
    changed: bool
    capability_path: str
    config_path: str
    mode: str
    mode_options: tuple[str, ...]
    regulation_mode: str
    regulation_mode_options: tuple[str, ...]
    high_ir_brightness: int
    low_ir_brightness: int
    ir_brightness_min: int
    ir_brightness_max: int
    high_white_brightness: int
    low_white_brightness: int
    white_brightness_min: int
    white_brightness_max: int


@dataclass(frozen=True)
class TwoWayAudioChannelStatus:
    supported: bool
    changed: bool
    capability_path: str
    config_path: str
    enabled: bool
    input_type: str
    input_type_is_micin: bool
    micin_supported: bool
    output_type: str
    output_type_is_speaker: bool
    speaker_supported: bool
    audio_compression_type: str
    speaker_volume: int
    speaker_volume_max: int
    microphone_volume: int
    microphone_volume_max: int
    input_type_options: tuple[str, ...] = ()
    output_type_options: tuple[str, ...] = ()
    audio_compression_type_options: tuple[str, ...] = ()


class HikvisionIsapiClient:
    def __init__(
        self,
        sdk: HikvisionVoiceSDK | None = None,
        *,
        scheme: str = "http",
        timeout_seconds: float = 5.0,
    ) -> None:
        self.sdk = sdk
        self.scheme = scheme
        self.timeout_seconds = timeout_seconds

    def stream_type_to_track_suffix(self, stream_type: int) -> int:
        mapping = {
            STREAM_TYPE_MAIN: 1,
            1: 2,
        }
        return mapping.get(stream_type, stream_type + 1)

    def track_stream_id(self, channel: int, stream_type: int) -> int:
        return channel * 100 + self.stream_type_to_track_suffix(stream_type)

    def get_supported_image_channel_ids(self, session: DeviceSession) -> ImageChannelCapabilityStatus:
        capability_path = "/ISAPI/System/capabilities?type=all"
        try:
            capability_root = ET.fromstring(self._request_text(session, "GET", capability_path))
            support_node = _find_first_by_local_name(capability_root, "supportImageChannel")
            channel_ids = self._integer_options(support_node)
            if channel_ids:
                return ImageChannelCapabilityStatus(
                    channel_ids=channel_ids,
                    source="supportImageChannel",
                    request_path=capability_path,
                )
        except (ET.ParseError, HikvisionSDKError):
            pass

        streaming_path = "/ISAPI/Streaming/channels"
        streaming_root = ET.fromstring(self._request_text(session, "GET", streaming_path))
        channel_ids: list[int] = []
        for node in streaming_root.iter():
            if _local_name(node.tag) != "StreamingChannel":
                continue
            enabled_node = _find_first_by_local_name(node, "enabled")
            if enabled_node is not None and (enabled_node.text or "").strip().lower() not in {"true", "1"}:
                continue
            id_node = _find_first_by_local_name(node, "id")
            streaming_channel_id = self._safe_int(id_node.text if id_node is not None else None)
            if streaming_channel_id is None:
                continue
            # Streaming IDs use <physical-channel><stream-suffix>, for example
            # 101/102 are the main/sub streams of image channel 1.
            image_channel_id = streaming_channel_id // 100 if streaming_channel_id >= 100 else streaming_channel_id
            if image_channel_id not in channel_ids:
                channel_ids.append(image_channel_id)

        if not channel_ids:
            channel_ids.append(session.default_preview_channel)
        return ImageChannelCapabilityStatus(
            channel_ids=tuple(channel_ids),
            source="StreamingChannel/id映射图像通道",
            request_path=streaming_path,
        )

    def get_streaming_channel_capabilities(
        self,
        session: DeviceSession,
        channel: Optional[int] = None,
        stream_type: int = STREAM_TYPE_MAIN,
    ) -> str:
        target_channel = channel or session.default_preview_channel
        track_stream_id = self.track_stream_id(target_channel, stream_type)
        return self._request_text(session, "GET", f"/ISAPI/Streaming/channels/{track_stream_id}/capabilities")

    def get_streaming_channel_config(
        self,
        session: DeviceSession,
        channel: Optional[int] = None,
        stream_type: int = STREAM_TYPE_MAIN,
    ) -> str:
        target_channel = channel or session.default_preview_channel
        track_stream_id = self.track_stream_id(target_channel, stream_type)
        return self._request_text(session, "GET", f"/ISAPI/Streaming/channels/{track_stream_id}")

    def set_streaming_channel_config(
        self,
        session: DeviceSession,
        xml_body: str,
        channel: Optional[int] = None,
        stream_type: int = STREAM_TYPE_MAIN,
    ) -> str:
        target_channel = channel or session.default_preview_channel
        track_stream_id = self.track_stream_id(target_channel, stream_type)
        return self._request_text(session, "PUT", f"/ISAPI/Streaming/channels/{track_stream_id}", body=xml_body)

    def get_audio_input_capability_status(self, session: DeviceSession) -> AudioInputCapabilityStatus:
        status = self.get_two_way_audio_channel_status(session)
        return AudioInputCapabilityStatus(
            supported=status.micin_supported,
            request_path=status.capability_path,
        )

    def get_two_way_audio_channel_status(
        self,
        session: DeviceSession,
        channel: int = 1,
    ) -> TwoWayAudioChannelStatus:
        capability_path = "/ISAPI/System/TwoWayAudio/channels/capabilities"
        config_path = f"/ISAPI/System/TwoWayAudio/channels/{channel}"
        capability_root = ET.fromstring(self._request_text(session, "GET", capability_path))
        capability_channel = self._first_twoway_channel(capability_root)
        if capability_channel is None:
            return TwoWayAudioChannelStatus(
                supported=False,
                changed=False,
                capability_path=capability_path,
                config_path=config_path,
                enabled=False,
                input_type="",
                input_type_is_micin=False,
                micin_supported=False,
                output_type="",
                output_type_is_speaker=False,
                speaker_supported=False,
                audio_compression_type="",
                speaker_volume=0,
                speaker_volume_max=0,
                microphone_volume=0,
                microphone_volume_max=0,
            )

        capability_input_type_node = _find_first_by_local_name(capability_channel, "audioInputType")
        capability_output_type_node = _find_first_by_local_name(capability_channel, "audioOutputType")
        capability_audio_compression_type_node = _find_first_by_local_name(capability_channel, "audioCompressionType")
        capability_speaker_volume_node = _find_first_by_local_name(capability_channel, "speakerVolume")
        capability_microphone_volume_node = _find_first_by_local_name(capability_channel, "microphoneVolume")

        input_type_options = self._node_options(capability_input_type_node)
        output_type_options = self._node_options(capability_output_type_node)
        audio_compression_type_options = self._node_options(capability_audio_compression_type_node)
        micin_supported = "MicIn" in input_type_options
        speaker_supported = "Speaker" in output_type_options
        speaker_volume_max = self._max_from_node(capability_speaker_volume_node) or 0
        microphone_volume_max = self._max_from_node(capability_microphone_volume_node) or 0
        if capability_output_type_node is None and capability_speaker_volume_node is not None:
            # Some devices expose speaker capability via speakerVolume but do not publish audioOutputType.
            speaker_supported = True
            output_type_options = ("Speaker",)

        config_root = ET.fromstring(self._request_text(session, "GET", config_path))
        config_channel = self._first_twoway_channel(config_root) or config_root
        enabled_node = _find_first_by_local_name(config_channel, "enabled")
        input_type_node = _find_first_by_local_name(config_channel, "audioInputType")
        output_type_node = _find_first_by_local_name(config_channel, "audioOutputType")
        audio_compression_type_node = _find_first_by_local_name(config_channel, "audioCompressionType")
        speaker_volume_node = _find_first_by_local_name(config_channel, "speakerVolume")
        microphone_volume_node = _find_first_by_local_name(config_channel, "microphoneVolume")

        enabled = (enabled_node.text or "").strip().lower() in {"true", "1"} if enabled_node is not None else False
        input_type = (input_type_node.text or "").strip() if input_type_node is not None and input_type_node.text else ""
        output_type = (output_type_node.text or "").strip() if output_type_node is not None and output_type_node.text else ""
        audio_compression_type = (
            (audio_compression_type_node.text or "").strip()
            if audio_compression_type_node is not None and audio_compression_type_node.text
            else ""
        )
        speaker_volume = self._safe_int(speaker_volume_node.text if speaker_volume_node is not None else None) or 0
        microphone_volume = self._safe_int(microphone_volume_node.text if microphone_volume_node is not None else None) or 0
        if not output_type and speaker_supported:
            output_type = "Speaker"

        return TwoWayAudioChannelStatus(
            supported=micin_supported or speaker_supported,
            changed=False,
            capability_path=capability_path,
            config_path=config_path,
            enabled=enabled,
            input_type=input_type,
            input_type_is_micin=input_type == "MicIn",
            micin_supported=micin_supported,
            output_type=output_type,
            output_type_is_speaker=output_type == "Speaker",
            speaker_supported=speaker_supported,
            audio_compression_type=audio_compression_type,
            speaker_volume=speaker_volume,
            speaker_volume_max=speaker_volume_max,
            microphone_volume=microphone_volume,
            microphone_volume_max=microphone_volume_max,
            input_type_options=input_type_options,
            output_type_options=output_type_options,
            audio_compression_type_options=audio_compression_type_options,
        )

    def ensure_two_way_audio_channel_ready(
        self,
        session: DeviceSession,
        channel: int = 1,
        *,
        require_input_type: Optional[str] = None,
        require_output_type: Optional[str] = None,
        require_audio_compression_type: Optional[str] = None,
        enable: bool = True,
        maximize_microphone_volume: bool = False,
        maximize_speaker_volume: bool = False,
    ) -> TwoWayAudioChannelStatus:
        status = self.get_two_way_audio_channel_status(session, channel=channel)
        if require_input_type == "MicIn" and not status.micin_supported:
            return status
        if require_output_type == "Speaker" and not status.speaker_supported:
            return status
        if require_input_type and status.input_type_options and require_input_type not in status.input_type_options:
            return status
        if require_output_type and status.output_type_options and require_output_type not in status.output_type_options:
            return status
        if (
            require_audio_compression_type
            and status.audio_compression_type_options
            and require_audio_compression_type not in status.audio_compression_type_options
        ):
            return status

        microphone_at_max = status.microphone_volume_max > 0 and status.microphone_volume >= status.microphone_volume_max
        speaker_at_max = status.speaker_volume_max > 0 and status.speaker_volume >= status.speaker_volume_max
        input_type_ready = require_input_type is None or status.input_type == require_input_type
        output_type_ready = require_output_type is None or status.output_type == require_output_type
        audio_compression_type_ready = (
            require_audio_compression_type is None
            or status.audio_compression_type == require_audio_compression_type
        )
        enabled_ready = (not enable) or status.enabled
        microphone_ready = (not maximize_microphone_volume) or status.microphone_volume_max <= 0 or microphone_at_max
        speaker_ready = (not maximize_speaker_volume) or status.speaker_volume_max <= 0 or speaker_at_max
        if (
            input_type_ready
            and output_type_ready
            and audio_compression_type_ready
            and enabled_ready
            and microphone_ready
            and speaker_ready
        ):
            return status

        config_path = status.config_path
        config_root = ET.fromstring(self._request_text(session, "GET", config_path))
        channel_root = self._first_twoway_channel(config_root) or config_root

        self._set_existing_text(channel_root, "id", str(channel))
        if enable:
            self._set_existing_text(channel_root, "enabled", "true")
        if require_input_type is not None:
            self._set_existing_text(channel_root, "audioInputType", require_input_type)
        if require_output_type is not None:
            self._set_existing_text(channel_root, "audioOutputType", require_output_type)
        if require_audio_compression_type is not None:
            self._set_existing_text(channel_root, "audioCompressionType", require_audio_compression_type)
        if maximize_microphone_volume and status.microphone_volume_max > 0:
            self._set_existing_text(channel_root, "microphoneVolume", str(status.microphone_volume_max))
        if maximize_speaker_volume and status.speaker_volume_max > 0:
            self._set_existing_text(channel_root, "speakerVolume", str(status.speaker_volume_max))

        xml_body = self._serialize_xml(config_root)
        self._request_text(session, "PUT", config_path, body=xml_body)

        refreshed = self.get_two_way_audio_channel_status(session, channel=channel)
        return TwoWayAudioChannelStatus(
            supported=refreshed.supported,
            changed=True,
            capability_path=refreshed.capability_path,
            config_path=refreshed.config_path,
            enabled=refreshed.enabled,
            input_type=refreshed.input_type,
            input_type_is_micin=refreshed.input_type_is_micin,
            micin_supported=refreshed.micin_supported,
            output_type=refreshed.output_type,
            output_type_is_speaker=refreshed.output_type_is_speaker,
            speaker_supported=refreshed.speaker_supported,
            audio_compression_type=refreshed.audio_compression_type,
            speaker_volume=refreshed.speaker_volume,
            speaker_volume_max=refreshed.speaker_volume_max,
            microphone_volume=refreshed.microphone_volume,
            microphone_volume_max=refreshed.microphone_volume_max,
            input_type_options=refreshed.input_type_options,
            output_type_options=refreshed.output_type_options,
            audio_compression_type_options=refreshed.audio_compression_type_options,
        )

    def ensure_two_way_audio_speaker_ready(
        self,
        session: DeviceSession,
        channel: int = 1,
    ) -> TwoWayAudioChannelStatus:
        return self.ensure_two_way_audio_channel_ready(
            session,
            channel=channel,
            require_input_type="MicIn",
            require_output_type="Speaker",
            enable=True,
            maximize_microphone_volume=True,
            maximize_speaker_volume=True,
        )

    def ensure_composite_stream_recording_enabled(
        self,
        session: DeviceSession,
        channel: Optional[int] = None,
        stream_type: int = STREAM_TYPE_MAIN,
    ) -> CompositeStreamStatus:
        target_channel = channel or session.default_preview_channel
        track_stream_id = self.track_stream_id(target_channel, stream_type)

        capabilities_xml = self.get_streaming_channel_capabilities(session, target_channel, stream_type)
        cap_root = ET.fromstring(capabilities_xml)
        audio_cap_node = _find_first_by_local_name(cap_root, "Audio")
        if audio_cap_node is None:
            return CompositeStreamStatus(False, track_stream_id, False, False)

        config_xml = self.get_streaming_channel_config(session, target_channel, stream_type)
        config_root = ET.fromstring(config_xml)
        audio_node = _find_first_by_local_name(config_root, "Audio")
        if audio_node is None:
            audio_node = ET.SubElement(config_root, _child_tag_like(config_root, "Audio"))

        enabled_node = _find_first_by_local_name(audio_node, "enabled")
        if enabled_node is None:
            enabled_node = _find_first_by_local_name(audio_node, "enable")
        if enabled_node is None:
            enabled_node = ET.SubElement(audio_node, _child_tag_like(audio_node, "enabled"))

        current_value = (enabled_node.text or "").strip().lower()
        if current_value in {"true", "1"}:
            return CompositeStreamStatus(True, track_stream_id, True, False)

        enabled_node.text = "true"
        xml_body = ET.tostring(config_root, encoding="utf-8", xml_declaration=True).decode("utf-8")
        self.set_streaming_channel_config(session, xml_body, target_channel, stream_type)
        return CompositeStreamStatus(True, track_stream_id, True, True)

    def set_audio_input_volume_to_max(self, session: DeviceSession, channel: int = 1) -> AudioVolumeStatus:
        return self._set_audio_volume_to_max(
            session=session,
            config_paths=[
                f"/ISAPI/System/Audio/AudioIn/channels/{channel}",
                f"/ISAPI/System/Audio/channels/{channel}/AudioIn",
            ],
            capability_paths=[
                f"/ISAPI/System/Audio/AudioIn/channels/{channel}/capabilities",
                "/ISAPI/System/Audio/capabilities",
            ],
        )

    def set_audio_output_volume_to_max(self, session: DeviceSession, channel: int = 1) -> AudioVolumeStatus:
        return self._set_audio_volume_to_max(
            session=session,
            config_paths=[
                f"/ISAPI/System/Audio/AudioOut/channels/{channel}",
                "/ISAPI/System/SoundCfg",
            ],
            capability_paths=[
                f"/ISAPI/System/Audio/AudioOut/channels/{channel}/capabilities",
                "/ISAPI/System/Audio/capabilities",
                "/ISAPI/System/SoundCfg/capabilities",
            ],
        )

    def ensure_audio_input_output_volume_max(
        self,
        session: DeviceSession,
        input_channel: int = 1,
        output_channel: int = 1,
    ) -> AudioIoVolumeStatus:
        return AudioIoVolumeStatus(
            input_status=self.set_audio_input_volume_to_max(session, input_channel),
            output_status=self.set_audio_output_volume_to_max(session, output_channel),
        )

    def get_mixed_supplement_light_status(
        self,
        session: DeviceSession,
        channel: int = 1,
    ) -> MixedSupplementLightStatus:
        capability_path = f"/ISAPI/Image/channels/{channel}/capabilities"
        config_path = f"/ISAPI/Image/channels/{channel}"
        capability_root = ET.fromstring(self._request_text(session, "GET", capability_path))
        config_root = ET.fromstring(self._request_text(session, "GET", config_path))
        capability_node = _find_first_by_local_name(capability_root, "SupplementLight")
        config_node = _find_first_by_local_name(config_root, "SupplementLight")
        if capability_node is None or config_node is None:
            return MixedSupplementLightStatus(
                False, False, capability_path, config_path, "", (), "", (), 0, 0, 0, 0, 0, 0, 0, 0
            )

        capability_mode = _find_first_by_local_name(capability_node, "supplementLightMode")
        capability_regulation = _find_first_by_local_name(capability_node, "mixedLightBrightnessRegulatMode")
        capability_ir = _find_first_by_local_name(capability_node, "highIrLightBrightness")
        capability_white = _find_first_by_local_name(capability_node, "highWhiteLightBrightness")
        mode_options = self._node_options(capability_mode)
        regulation_options = self._node_options(capability_regulation)

        def config_int(name: str) -> int:
            node = _find_first_by_local_name(config_node, name)
            return self._safe_int(node.text if node is not None else None) or 0

        mode_node = _find_first_by_local_name(config_node, "supplementLightMode")
        regulation_node = _find_first_by_local_name(config_node, "mixedLightBrightnessRegulatMode")
        return MixedSupplementLightStatus(
            supported=bool(mode_options) or mode_node is not None,
            changed=False,
            capability_path=capability_path,
            config_path=config_path,
            mode=(mode_node.text or "").strip() if mode_node is not None and mode_node.text else "",
            mode_options=mode_options,
            regulation_mode=(
                (regulation_node.text or "").strip()
                if regulation_node is not None and regulation_node.text
                else ""
            ),
            regulation_mode_options=regulation_options,
            high_ir_brightness=config_int("highIrLightBrightness"),
            low_ir_brightness=config_int("lowIrLightBrightness"),
            ir_brightness_min=self._min_from_node(capability_ir) or 0,
            ir_brightness_max=self._max_from_node(capability_ir) or 100,
            high_white_brightness=config_int("highWhiteLightBrightness"),
            low_white_brightness=config_int("lowWhiteLightBrightness"),
            white_brightness_min=self._min_from_node(capability_white) or 0,
            white_brightness_max=self._max_from_node(capability_white) or 100,
        )

    def get_ircut_filter_status(
        self,
        session: DeviceSession,
        channel: int,
    ) -> IrcutFilterStatus:
        channel_capability_path = f"/ISAPI/Image/channels/{channel}/capabilities"
        common_capability_path = "/ISAPI/Image/channels/capabilities"
        config_path = f"/ISAPI/Image/channels/{channel}"
        capability_path, capability_root = self._get_first_xml(
            session,
            [channel_capability_path, common_capability_path],
        )
        if capability_path is None or capability_root is None:
            raise HikvisionSDKError(
                "未找到图像通道日夜切换能力",
                api_name=channel_capability_path,
            )
        config_root = ET.fromstring(self._request_text(session, "GET", config_path))
        capability_ircut = _find_first_by_local_name(capability_root, "IrcutFilter")
        if capability_ircut is None and capability_path != common_capability_path:
            capability_path, capability_root = self._get_first_xml(session, [common_capability_path])
            capability_ircut = (
                _find_first_by_local_name(capability_root, "IrcutFilter")
                if capability_root is not None
                else None
            )
        config_ircut = _find_first_by_local_name(config_root, "IrcutFilter")
        capability_type = (
            _find_first_by_local_name(capability_ircut, "IrcutFilterType")
            if capability_ircut is not None
            else None
        )
        config_type = (
            _find_first_by_local_name(config_ircut, "IrcutFilterType")
            if config_ircut is not None
            else None
        )
        options = self._node_options(capability_type)
        filter_type = (config_type.text or "").strip() if config_type is not None and config_type.text else ""
        return IrcutFilterStatus(
            supported=config_type is not None and bool(options),
            changed=False,
            capability_path=capability_path or channel_capability_path,
            config_path=config_path,
            filter_type=filter_type,
            filter_type_options=options,
        )

    def ensure_ircut_filter_type(
        self,
        session: DeviceSession,
        channel: int,
        filter_type: str,
    ) -> IrcutFilterStatus:
        status = self.get_ircut_filter_status(session, channel)
        if not status.supported:
            return status
        if filter_type not in status.filter_type_options:
            raise HikvisionSDKError(
                f"设备不支持日夜切换类型: {filter_type}",
                api_name=status.capability_path,
            )
        if status.filter_type == filter_type:
            return status

        config_root = ET.fromstring(self._request_text(session, "GET", status.config_path))
        config_ircut = _find_first_by_local_name(config_root, "IrcutFilter")
        if config_ircut is None:
            raise HikvisionSDKError("未找到 IrcutFilter 配置", api_name=status.config_path)
        self._set_or_create_text(config_ircut, "IrcutFilterType", filter_type)
        self._request_text(session, "PUT", status.config_path, body=self._serialize_xml(config_root))
        refreshed = self.get_ircut_filter_status(session, channel)
        return IrcutFilterStatus(
            supported=refreshed.supported,
            changed=True,
            capability_path=refreshed.capability_path,
            config_path=refreshed.config_path,
            filter_type=refreshed.filter_type,
            filter_type_options=refreshed.filter_type_options,
        )

    def ensure_mixed_supplement_light_ready(
        self,
        session: DeviceSession,
        channel: int,
        *,
        mode: str,
        brightness: Optional[int] = None,
        prefer_manual: bool = True,
    ) -> MixedSupplementLightStatus:
        status = self.get_mixed_supplement_light_status(session, channel=channel)
        if not status.supported:
            return status
        if status.mode_options and mode not in status.mode_options:
            raise HikvisionSDKError(f"不支持补光灯模式: {mode}", api_name=status.capability_path)

        config_root = ET.fromstring(self._request_text(session, "GET", status.config_path))
        config_node = _find_first_by_local_name(config_root, "SupplementLight")
        if config_node is None:
            raise HikvisionSDKError("未找到 SupplementLight 配置", api_name=status.config_path)

        self._set_or_create_text(config_node, "supplementLightMode", mode)
        if prefer_manual and "manual" in status.regulation_mode_options:
            self._set_or_create_text(config_node, "mixedLightBrightnessRegulatMode", "manual")
        if brightness is not None:
            if mode == "colorVuWhiteLight":
                value = max(status.white_brightness_min, min(status.white_brightness_max, int(brightness)))
                self._set_or_create_text(config_node, "highWhiteLightBrightness", str(value))
                self._set_or_create_text(config_node, "lowWhiteLightBrightness", str(value))
            elif mode == "irLight":
                value = max(status.ir_brightness_min, min(status.ir_brightness_max, int(brightness)))
                self._set_or_create_text(config_node, "highIrLightBrightness", str(value))
                self._set_or_create_text(config_node, "lowIrLightBrightness", str(value))

        self._request_text(session, "PUT", status.config_path, body=self._serialize_xml(config_root))
        refreshed = self.get_mixed_supplement_light_status(session, channel=channel)
        return MixedSupplementLightStatus(**{**refreshed.__dict__, "changed": True})

    def restore_mixed_supplement_light_status(
        self,
        session: DeviceSession,
        channel: int,
        status: MixedSupplementLightStatus,
    ) -> MixedSupplementLightStatus:
        config_root = ET.fromstring(self._request_text(session, "GET", status.config_path))
        config_node = _find_first_by_local_name(config_root, "SupplementLight")
        if config_node is None:
            raise HikvisionSDKError("未找到 SupplementLight 配置", api_name=status.config_path)
        values = {
            "supplementLightMode": status.mode,
            "mixedLightBrightnessRegulatMode": status.regulation_mode,
            "highIrLightBrightness": status.high_ir_brightness,
            "lowIrLightBrightness": status.low_ir_brightness,
            "highWhiteLightBrightness": status.high_white_brightness,
            "lowWhiteLightBrightness": status.low_white_brightness,
        }
        for name, value in values.items():
            if value != "":
                self._set_or_create_text(config_node, name, str(value))
        self._request_text(session, "PUT", status.config_path, body=self._serialize_xml(config_root))
        return self.get_mixed_supplement_light_status(session, channel=channel)

    def get_ir_light_capability_status(
        self,
        session: DeviceSession,
        channel: int = 1,
    ) -> IrLightCapabilityStatus:
        capability_path = "/ISAPI/Image/channels/capabilities"
        try:
            capability_root = ET.fromstring(self._request_text(session, "GET", capability_path))
        except HikvisionSDKError:
            return IrLightCapabilityStatus(False, capability_path, (), 0, 0)

        ir_light_node = _find_first_by_local_name(capability_root, "IrLight")
        if ir_light_node is None:
            return IrLightCapabilityStatus(False, capability_path, (), 0, 0)

        mode_node = _find_first_by_local_name(ir_light_node, "mode")
        brightness_limit_node = _find_first_by_local_name(ir_light_node, "brightnessLimit")
        mode_options = self._node_options(mode_node)
        brightness_min = self._min_from_node(brightness_limit_node) or 0
        brightness_max = self._max_from_node(brightness_limit_node) or 100

        return IrLightCapabilityStatus(
            supported=brightness_limit_node is not None,
            capability_path=capability_path,
            mode_options=mode_options,
            brightness_min=brightness_min,
            brightness_max=brightness_max,
        )

    def get_ir_light_status(
        self,
        session: DeviceSession,
        channel: int = 1,
    ) -> IrLightStatus:
        capability = self.get_ir_light_capability_status(session, channel=channel)
        config_path = f"/ISAPI/Image/channels/{channel}"
        config_root = ET.fromstring(self._request_text(session, "GET", config_path))
        ir_light_node = _find_first_by_local_name(config_root, "IrLight")
        if ir_light_node is None:
            return IrLightStatus(
                supported=False,
                changed=False,
                capability_path=capability.capability_path,
                config_path=config_path,
                mode="",
                mode_options=capability.mode_options,
                brightness_limit=0,
                brightness_min=capability.brightness_min,
                brightness_max=capability.brightness_max,
            )

        mode_node = _find_first_by_local_name(ir_light_node, "mode")
        brightness_limit_node = _find_first_by_local_name(ir_light_node, "brightnessLimit")
        mode = (mode_node.text or "").strip() if mode_node is not None and mode_node.text else ""
        brightness_limit = self._safe_int(brightness_limit_node.text if brightness_limit_node is not None else None) or 0
        mode_options = capability.mode_options or ((mode,) if mode else ())
        brightness_min = capability.brightness_min
        brightness_max = capability.brightness_max or self._max_from_node(brightness_limit_node) or 100

        return IrLightStatus(
            supported=capability.supported and brightness_limit_node is not None,
            changed=False,
            capability_path=capability.capability_path,
            config_path=config_path,
            mode=mode,
            mode_options=mode_options,
            brightness_limit=brightness_limit,
            brightness_min=brightness_min,
            brightness_max=brightness_max,
        )

    def ensure_ir_light_ready(
        self,
        session: DeviceSession,
        channel: int = 1,
        *,
        mode: str,
        brightness_limit: int,
    ) -> IrLightStatus:
        status = self.get_ir_light_status(session, channel=channel)
        if not status.supported:
            return status
        if status.mode_options and mode not in status.mode_options:
            raise HikvisionSDKError(
                f"IrLight mode is not supported: {mode}",
                api_name=status.capability_path,
            )

        clipped = max(status.brightness_min, min(status.brightness_max, int(brightness_limit)))
        if status.mode == mode and status.brightness_limit == clipped:
            return status

        config_root = ET.fromstring(self._request_text(session, "GET", status.config_path))
        ir_light_node = _find_first_by_local_name(config_root, "IrLight")
        if ir_light_node is None:
            raise HikvisionSDKError("IrLight config not found", api_name=status.config_path)

        self._set_or_create_text(ir_light_node, "mode", mode)
        self._set_or_create_text(ir_light_node, "brightnessLimit", str(clipped))
        self._request_text(session, "PUT", status.config_path, body=self._serialize_xml(config_root))

        refreshed = self.get_ir_light_status(session, channel=channel)
        return IrLightStatus(
            supported=refreshed.supported,
            changed=True,
            capability_path=refreshed.capability_path,
            config_path=refreshed.config_path,
            mode=refreshed.mode,
            mode_options=refreshed.mode_options,
            brightness_limit=refreshed.brightness_limit,
            brightness_min=refreshed.brightness_min,
            brightness_max=refreshed.brightness_max,
        )

    def get_supplement_light_status(
        self,
        session: DeviceSession,
        channel: int = 1,
    ) -> SupplementLightStatus:
        capability_path = f"/ISAPI/Image/channels/{channel}/SupplementLight/capabilities"
        config_paths = [
            f"/ISAPI/Image/channels/{channel}/SupplementLight",
            f"/ISAPI/System/channel/{channel}/externalDevice/supplementLight",
        ]
        capability_root: Optional[ET.Element] = None
        try:
            capability_root = ET.fromstring(self._request_text(session, "GET", capability_path))
        except HikvisionSDKError:
            capability_root = None

        config_path, config_root = self._get_first_xml(session, config_paths)
        if config_path is None or config_root is None:
            raise HikvisionSDKError(
                "supplement light config not found",
                api_name="GET /ISAPI/Image/channels/<channel>/SupplementLight",
            )
        status = self._supplement_light_status_from_xml(
            capability_root=capability_root,
            config_root=config_root,
            capability_path=capability_path,
            config_path=config_path,
            changed=False,
        )
        return status

    def ensure_supplement_light_ready(
        self,
        session: DeviceSession,
        channel: int = 1,
        *,
        enabled: Optional[bool] = None,
        brightness: Optional[int] = None,
        mode: Optional[str] = "manual",
    ) -> SupplementLightStatus:
        status = self.get_supplement_light_status(session, channel=channel)
        if not status.supported:
            return status

        brightness_ready = brightness is None or status.brightness == brightness
        enabled_ready = enabled is None or status.enabled == enabled
        mode_ready = mode is None or not status.mode or status.mode.lower() == mode.lower()
        if brightness_ready and enabled_ready and mode_ready:
            return status

        config_root = ET.fromstring(self._request_text(session, "GET", status.config_path))
        if enabled is not None:
            self._set_first_existing_text(config_root, ("enabled", "enable"), "true" if enabled else "false")
        if mode is not None:
            self._set_first_existing_text(
                config_root,
                ("mode", "supplementLightMode", "lightMode"),
                mode,
            )
        if brightness is not None:
            clipped = max(status.brightness_min, min(status.brightness_max, int(brightness)))
            self._set_first_existing_text(
                config_root,
                (
                    "brightness",
                    "supplementLightIntensity",
                    "supplementLightBrightness",
                    "intensity",
                    "lightBrightness",
                    "whiteLightBrightness",
                ),
                str(clipped),
            )

        self._request_text(session, "PUT", status.config_path, body=self._serialize_xml(config_root))
        refreshed = self.get_supplement_light_status(session, channel=channel)
        return SupplementLightStatus(
            supported=refreshed.supported,
            changed=True,
            capability_path=refreshed.capability_path,
            config_path=refreshed.config_path,
            enabled=refreshed.enabled,
            brightness=refreshed.brightness,
            brightness_min=refreshed.brightness_min,
            brightness_max=refreshed.brightness_max,
            mode=refreshed.mode,
        )

    def _set_audio_volume_to_max(
        self,
        session: DeviceSession,
        config_paths: list[str],
        capability_paths: list[str],
    ) -> AudioVolumeStatus:
        config_path, config_root = self._get_first_xml(session, config_paths)
        if config_root is None or config_path is None:
            return AudioVolumeStatus(False, False, 0, 0, "")

        volume_node = self._find_volume_node(config_root)
        if volume_node is None:
            return AudioVolumeStatus(False, False, 0, 0, config_path)

        max_value = self._find_volume_max(session, capability_paths) or self._max_from_node(volume_node) or 15
        current_value = self._safe_int(volume_node.text)
        if current_value is None:
            current_value = 0
        if current_value >= max_value:
            return AudioVolumeStatus(True, False, current_value, max_value, config_path)

        volume_node.text = str(max_value)
        enabled_node = _find_first_by_local_name(config_root, "enabled")
        if enabled_node is not None:
            enabled_node.text = "true"
        enable_node = _find_first_by_local_name(config_root, "enable")
        if enable_node is not None:
            enable_node.text = "true"
        xml_body = ET.tostring(config_root, encoding="utf-8", xml_declaration=True).decode("utf-8")
        self._request_text(session, "PUT", config_path, body=xml_body)
        return AudioVolumeStatus(True, True, max_value, max_value, config_path)

    def _get_first_xml(self, session: DeviceSession, paths: list[str]) -> tuple[Optional[str], Optional[ET.Element]]:
        for path in paths:
            try:
                xml_text = self._request_text(session, "GET", path)
                return path, ET.fromstring(xml_text)
            except Exception:
                continue
        return None, None

    def _find_volume_max(self, session: DeviceSession, capability_paths: list[str]) -> Optional[int]:
        _path, cap_root = self._get_first_xml(session, capability_paths)
        if cap_root is None:
            return None

        volume_node = self._find_volume_node(cap_root)
        if volume_node is not None:
            max_value = self._max_from_node(volume_node)
            if max_value is not None:
                return max_value

        for node in cap_root.iter():
            attr_max = node.attrib.get("max")
            if _local_name(node.tag) in {"audioVolume", "volume"} and attr_max is not None:
                parsed = self._safe_int(attr_max)
                if parsed is not None:
                    return parsed
        return None

    def _find_volume_node(self, root: ET.Element) -> Optional[ET.Element]:
        for name in ("audioVolume", "volume"):
            node = _find_first_by_local_name(root, name)
            if node is not None:
                return node
        return None

    def _supplement_light_status_from_xml(
        self,
        capability_root: Optional[ET.Element],
        config_root: ET.Element,
        capability_path: str,
        config_path: str,
        changed: bool,
    ) -> SupplementLightStatus:
        enabled_node = self._find_first_by_local_names(config_root, ("enabled", "enable"))
        brightness_node = self._find_first_by_local_names(
            config_root,
            (
                "brightness",
                "supplementLightIntensity",
                "supplementLightBrightness",
                "intensity",
                "lightBrightness",
                "whiteLightBrightness",
            ),
        )
        mode_node = self._find_first_by_local_names(config_root, ("mode", "supplementLightMode", "lightMode"))
        brightness_capability_node = None
        if capability_root is not None:
            brightness_capability_node = self._find_first_by_local_names(
                capability_root,
                (
                    "brightness",
                    "supplementLightIntensity",
                    "supplementLightBrightness",
                    "intensity",
                    "lightBrightness",
                    "whiteLightBrightness",
                ),
            )
        brightness_min = self._min_from_node(brightness_capability_node) or self._min_from_node(brightness_node) or 0
        brightness_max = self._max_from_node(brightness_capability_node) or self._max_from_node(brightness_node) or 100
        enabled: Optional[bool] = None
        if enabled_node is not None and enabled_node.text is not None:
            enabled = enabled_node.text.strip().lower() in {"true", "1", "yes", "on"}
        brightness = self._safe_int(brightness_node.text if brightness_node is not None else None)
        mode = (mode_node.text or "").strip() if mode_node is not None and mode_node.text else ""
        return SupplementLightStatus(
            supported=brightness_node is not None or enabled_node is not None,
            changed=changed,
            capability_path=capability_path,
            config_path=config_path,
            enabled=enabled,
            brightness=brightness,
            brightness_min=brightness_min,
            brightness_max=brightness_max,
            mode=mode,
        )

    def _find_first_by_local_names(self, root: ET.Element, local_names: tuple[str, ...]) -> Optional[ET.Element]:
        for local_name in local_names:
            node = _find_first_by_local_name(root, local_name)
            if node is not None:
                return node
        return None

    def _set_first_existing_text(self, root: ET.Element, local_names: tuple[str, ...], value: str) -> ET.Element:
        for local_name in local_names:
            node = _find_first_by_local_name(root, local_name)
            if node is not None:
                node.text = value
                return node
        return self._set_or_create_text(root, local_names[0], value)

    def _first_twoway_channel(self, root: ET.Element) -> Optional[ET.Element]:
        for node in root.iter():
            if _local_name(node.tag) == "TwoWayAudioChannel":
                return node
        return None

    def _node_supports_option(self, node: Optional[ET.Element], expected_option: str) -> bool:
        if node is None:
            return False
        return expected_option in self._node_options(node)

    def _node_options(self, node: Optional[ET.Element]) -> tuple[str, ...]:
        if node is None:
            return ()
        options = node.attrib.get("opt", "")
        return tuple(item.strip() for item in options.split(",") if item.strip())

    def _integer_options(self, node: Optional[ET.Element]) -> tuple[int, ...]:
        values: list[int] = []
        for option in self._node_options(node):
            parsed = self._safe_int(option)
            if parsed is not None and parsed not in values:
                values.append(parsed)
        return tuple(values)

    def _set_or_create_text(self, root: ET.Element, local_name: str, value: str) -> ET.Element:
        node = _find_first_by_local_name(root, local_name)
        if node is None:
            node = ET.SubElement(root, _child_tag_like(root, local_name))
        node.text = value
        return node

    def _set_existing_text(self, root: ET.Element, local_name: str, value: str) -> Optional[ET.Element]:
        node = _find_first_by_local_name(root, local_name)
        if node is None:
            return None
        node.text = value
        return node

    def _serialize_xml(self, root: ET.Element) -> str:
        namespace = _namespace_of(root)
        if namespace:
            ET.register_namespace("", namespace)
        return ET.tostring(root, encoding="utf-8", xml_declaration=True).decode("utf-8")

    def _max_from_node(self, node: Optional[ET.Element]) -> Optional[int]:
        if node is None:
            return None
        return self._safe_int(node.attrib.get("max"))

    def _min_from_node(self, node: Optional[ET.Element]) -> Optional[int]:
        if node is None:
            return None
        return self._safe_int(node.attrib.get("min"))

    def _safe_int(self, value: Optional[str]) -> Optional[int]:
        if value is None:
            return None
        try:
            return int(str(value).strip())
        except (TypeError, ValueError):
            return None

    def _request_text(
        self,
        session: DeviceSession,
        method: str,
        path: str,
        body: str | None = None,
    ) -> str:
        response_body, _headers = self._request(session, method, path, body)
        return response_body.decode("utf-8", errors="ignore")

    def _request(
        self,
        session: DeviceSession,
        method: str,
        path: str,
        body: str | None = None,
    ) -> tuple[bytes, dict[str, str]]:
        url = self._build_url(session, path)
        payload = body.encode("utf-8") if body is not None else None
        headers = {"Accept": "application/xml,text/xml,*/*"}
        if body is not None:
            headers["Content-Type"] = "application/xml; charset=utf-8"
        try:
            response = requests.request(
                method=method.upper(),
                url=url,
                headers=headers,
                data=payload,
                auth=requests.auth.HTTPDigestAuth(session.username, session.password),
                timeout=self.timeout_seconds,
            )
            response.raise_for_status()
            return response.content, dict(response.headers.items())
        except requests.RequestException as exc:
            raise HikvisionSDKError(
                f"isapi request failed for {method.upper()} {path}",
                api_name=f"{method.upper()} {path}",
                error_code=getattr(getattr(exc, "response", None), "status_code", None),
                error_message=self._request_error_message(exc),
            ) from exc

    def _build_url(self, session: DeviceSession, path: str) -> str:
        normalized_path = path if path.startswith("/") else f"/{path}"
        return urllib.parse.urlunsplit((self.scheme, session.host, normalized_path, "", ""))

    def _request_error_message(self, error: requests.RequestException) -> str:
        response = getattr(error, "response", None)
        if response is None:
            return str(error)
        try:
            return response.text
        except Exception:
            return str(error)
