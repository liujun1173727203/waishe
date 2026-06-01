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
class TwoWayAudioChannelStatus:
    supported: bool
    changed: bool
    capability_path: str
    config_path: str
    output_type: str
    output_type_is_speaker: bool
    speaker_supported: bool
    microphone_volume: int
    microphone_volume_max: int


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

    def get_streaming_channel_capabilities(
        self,
        session: DeviceSession,
        channel: Optional[int] = None,
        stream_type: int = STREAM_TYPE_MAIN,
    ) -> str:
        target_channel = channel or session.default_preview_channel
        track_stream_id = self.track_stream_id(target_channel, stream_type)
        return self._request_text(session, "GET", f"/ISAPI/Streaming/channels/{track_stream_id}/capabilities")

    def get_audio_capabilities(self, session: DeviceSession) -> str:
        return self._request_text(session, "GET", "/ISAPI/System/Audio/capabilities")

    def get_audio_input_capability_status(self, session: DeviceSession) -> AudioInputCapabilityStatus:
        status = self.get_two_way_audio_channel_status(session)
        return AudioInputCapabilityStatus(
            supported=status.supported,
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
            return TwoWayAudioChannelStatus(False, False, capability_path, config_path, "", False, False, 0, 0)

        output_type_node = _find_first_by_local_name(capability_channel, "audioOutputType")
        microphone_volume_node = _find_first_by_local_name(capability_channel, "microphoneVolume")
        speaker_supported = self._node_supports_option(output_type_node, "Speaker")
        output_type = (output_type_node.text or "").strip() if output_type_node is not None and output_type_node.text else ""
        microphone_volume = self._safe_int(microphone_volume_node.text if microphone_volume_node is not None else None) or 0
        microphone_volume_max = self._max_from_node(microphone_volume_node) or 0

        return TwoWayAudioChannelStatus(
            supported=speaker_supported,
            changed=False,
            capability_path=capability_path,
            config_path=config_path,
            output_type=output_type,
            output_type_is_speaker=output_type == "Speaker",
            speaker_supported=speaker_supported,
            microphone_volume=microphone_volume,
            microphone_volume_max=microphone_volume_max,
        )

    def ensure_two_way_audio_speaker_ready(
        self,
        session: DeviceSession,
        channel: int = 1,
    ) -> TwoWayAudioChannelStatus:
        status = self.get_two_way_audio_channel_status(session, channel=channel)
        if not status.supported:
            return status

        microphone_at_max = status.microphone_volume_max > 0 and status.microphone_volume >= status.microphone_volume_max
        if status.output_type_is_speaker and microphone_at_max:
            return status

        config_path = status.config_path
        config_root = ET.fromstring(self._request_text(session, "GET", config_path))
        channel_root = self._first_twoway_channel(config_root) or config_root

        self._set_or_create_text(channel_root, "id", str(channel))
        self._set_or_create_text(channel_root, "enabled", "true")
        self._set_or_create_text(channel_root, "audioOutputType", "Speaker")
        if status.microphone_volume_max > 0:
            self._set_or_create_text(channel_root, "microphoneVolume", str(status.microphone_volume_max))

        xml_body = ET.tostring(config_root, encoding="utf-8", xml_declaration=True).decode("utf-8")
        self._request_text(session, "PUT", config_path, body=xml_body)

        refreshed = self.get_two_way_audio_channel_status(session, channel=channel)
        return TwoWayAudioChannelStatus(
            supported=refreshed.supported,
            changed=True,
            capability_path=refreshed.capability_path,
            config_path=refreshed.config_path,
            output_type=refreshed.output_type,
            output_type_is_speaker=refreshed.output_type_is_speaker,
            speaker_supported=refreshed.speaker_supported,
            microphone_volume=refreshed.microphone_volume,
            microphone_volume_max=refreshed.microphone_volume_max,
        )

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
        return self._request_text(
            session,
            "PUT",
            f"/ISAPI/Streaming/channels/{track_stream_id}",
            body=xml_body,
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

    def _first_twoway_channel(self, root: ET.Element) -> Optional[ET.Element]:
        for node in root.iter():
            if _local_name(node.tag) == "TwoWayAudioChannel":
                return node
        return None

    def _node_supports_option(self, node: Optional[ET.Element], expected_option: str) -> bool:
        if node is None:
            return False
        options = node.attrib.get("opt", "")
        return expected_option in {item.strip() for item in options.split(",") if item.strip()}

    def _set_or_create_text(self, root: ET.Element, local_name: str, value: str) -> ET.Element:
        node = _find_first_by_local_name(root, local_name)
        if node is None:
            node = ET.SubElement(root, _child_tag_like(root, local_name))
        node.text = value
        return node

    def _max_from_node(self, node: ET.Element) -> Optional[int]:
        if node is None:
            return None
        return self._safe_int(node.attrib.get("max"))

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
        headers = {
            "Accept": "application/xml,text/xml,*/*",
        }
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
        host = session.host
        return urllib.parse.urlunsplit((self.scheme, host, normalized_path, "", ""))

    def _request_error_message(self, error: requests.RequestException) -> str:
        response = getattr(error, "response", None)
        if response is None:
            return str(error)
        try:
            return response.text
        except Exception:
            return str(error)
