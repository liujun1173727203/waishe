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
