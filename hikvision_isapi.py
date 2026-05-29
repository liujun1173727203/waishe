from __future__ import annotations

import xml.etree.ElementTree as ET
from dataclasses import dataclass
from typing import Optional

from hikvision_voice import DeviceSession, HikvisionVoiceSDK, STREAM_TYPE_MAIN


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


class HikvisionIsapiClient:
    def __init__(self, sdk: HikvisionVoiceSDK) -> None:
        self.sdk = sdk

    def stream_type_to_track_suffix(self, stream_type: int) -> int:
        mapping = {
            STREAM_TYPE_MAIN: 1,
            1: 2,
        }
        return mapping.get(stream_type, stream_type + 1)

    def track_stream_id(self, channel: int, stream_type: int) -> int:
        return channel * 100 + self.stream_type_to_track_suffix(stream_type)

    def get_streaming_channel_capabilities(
        self,
        session: DeviceSession,
        channel: Optional[int] = None,
        stream_type: int = STREAM_TYPE_MAIN,
    ) -> str:
        target_channel = channel or session.default_preview_channel
        track_stream_id = self.track_stream_id(target_channel, stream_type)
        xml_text, _ = self.sdk.stdxml_config(
            session,
            "GET",
            f"/ISAPI/Streaming/channels/{track_stream_id}/capabilities",
        )
        return xml_text

    def get_audio_capabilities(self, session: DeviceSession) -> str:
        xml_text, _ = self.sdk.stdxml_config(session, "GET", "/ISAPI/System/Audio/capabilities")
        return xml_text

    def get_streaming_channel_config(
        self,
        session: DeviceSession,
        channel: Optional[int] = None,
        stream_type: int = STREAM_TYPE_MAIN,
    ) -> str:
        target_channel = channel or session.default_preview_channel
        track_stream_id = self.track_stream_id(target_channel, stream_type)
        xml_text, _ = self.sdk.stdxml_config(
            session,
            "GET",
            f"/ISAPI/Streaming/channels/{track_stream_id}",
        )
        return xml_text

    def set_streaming_channel_config(
        self,
        session: DeviceSession,
        xml_body: str,
        channel: Optional[int] = None,
        stream_type: int = STREAM_TYPE_MAIN,
    ) -> str:
        target_channel = channel or session.default_preview_channel
        track_stream_id = self.track_stream_id(target_channel, stream_type)
        _, status_text = self.sdk.stdxml_config(
            session,
            "PUT",
            f"/ISAPI/Streaming/channels/{track_stream_id}",
            body=xml_body,
        )
        return status_text

    def ensure_composite_stream_recording_enabled(
        self,
        session: DeviceSession,
        channel: Optional[int] = None,
        stream_type: int = STREAM_TYPE_MAIN,
    ) -> CompositeStreamStatus:
        target_channel = channel or session.default_preview_channel
        track_stream_id = self.track_stream_id(target_channel, stream_type)

        # 先看能力描述里是否存在 Audio 相关节点，避免直接对不支持的设备下发配置。
        capabilities_xml = self.get_streaming_channel_capabilities(session, target_channel, stream_type)
        cap_root = ET.fromstring(capabilities_xml)
        audio_cap_node = _find_first_by_local_name(cap_root, "Audio")
        if audio_cap_node is None:
            return CompositeStreamStatus(
                supported=False,
                track_stream_id=track_stream_id,
                audio_enabled=False,
                changed=False,
            )

        config_xml = self.get_streaming_channel_config(session, target_channel, stream_type)
        config_root = ET.fromstring(config_xml)
        audio_node = _find_first_by_local_name(config_root, "Audio")
        if audio_node is None:
            audio_node = ET.SubElement(config_root, _child_tag_like(config_root, "Audio"))

        # 不同设备固件可能返回 enable 或 enabled，这里统一兼容。
        enabled_node = _find_first_by_local_name(audio_node, "enabled")
        if enabled_node is None:
            enabled_node = _find_first_by_local_name(audio_node, "enable")
        if enabled_node is None:
            enabled_node = ET.SubElement(audio_node, _child_tag_like(audio_node, "enabled"))

        current_value = (enabled_node.text or "").strip().lower()
        if current_value in {"true", "1"}:
            return CompositeStreamStatus(
                supported=True,
                track_stream_id=track_stream_id,
                audio_enabled=True,
                changed=False,
            )

        enabled_node.text = "true"
        xml_body = ET.tostring(config_root, encoding="utf-8", xml_declaration=True).decode("utf-8")
        self.set_streaming_channel_config(session, xml_body, target_channel, stream_type)
        return CompositeStreamStatus(
            supported=True,
            track_stream_id=track_stream_id,
            audio_enabled=True,
            changed=True,
        )

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

    def _set_audio_volume_to_max(
        self,
        session: DeviceSession,
        config_paths: list[str],
        capability_paths: list[str],
    ) -> AudioVolumeStatus:
        # 先从多个候选配置路径里找到当前设备真正支持的那一条。
        config_path, config_root = self._get_first_xml(session, config_paths)
        if config_root is None or config_path is None:
            return AudioVolumeStatus(False, False, 0, 0, "")

        volume_node = self._find_volume_node(config_root)
        if volume_node is None:
            return AudioVolumeStatus(False, False, 0, 0, config_path)

        # 优先使用能力节点里的最大值，拿不到时再退回到配置节点属性或默认值。
        max_value = self._find_volume_max(session, capability_paths) or self._max_from_node(volume_node) or 15
        current_value = self._safe_int(volume_node.text)
        if current_value is None:
            current_value = 0
        if current_value >= max_value:
            return AudioVolumeStatus(True, False, current_value, max_value, config_path)

        volume_node.text = str(max_value)
        xml_body = ET.tostring(config_root, encoding="utf-8", xml_declaration=True).decode("utf-8")
        self.sdk.stdxml_config(session, "PUT", config_path, body=xml_body)
        return AudioVolumeStatus(True, True, max_value, max_value, config_path)

    def _get_first_xml(self, session: DeviceSession, paths: list[str]) -> tuple[Optional[str], Optional[ET.Element]]:
        for path in paths:
            try:
                xml_text, _ = self.sdk.stdxml_config(session, "GET", path)
                return path, ET.fromstring(xml_text)
            except Exception:
                # 同一能力在不同型号设备上路径可能不同，失败时继续尝试下一个候选路径。
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

    def _max_from_node(self, node: ET.Element) -> Optional[int]:
        return self._safe_int(node.attrib.get("max"))

    def _safe_int(self, value: Optional[str]) -> Optional[int]:
        if value is None:
            return None
        try:
            return int(str(value).strip())
        except (TypeError, ValueError):
            return None
