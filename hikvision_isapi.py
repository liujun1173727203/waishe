from __future__ import annotations

import hashlib
import re
import secrets
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from typing import Optional

from hikvision_voice import DeviceSession, HikvisionSDKError, HikvisionVoiceSDK, STREAM_TYPE_MAIN


ISAPI_SCHEMA = "http://www.isapi.org/ver20/XMLSchema"
_AUTH_PARAM_RE = re.compile(r'(\w+)=("([^"]*)"|[^,]+)')


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


def _parse_www_authenticate(header_value: str) -> dict[str, str]:
    if not header_value.lower().startswith("digest "):
        raise HikvisionSDKError("unsupported www-authenticate scheme", error_message=header_value)

    challenge = header_value[7:]
    parsed: dict[str, str] = {}
    for match in _AUTH_PARAM_RE.finditer(challenge):
        key = match.group(1)
        value = match.group(3) if match.group(3) is not None else match.group(2)
        parsed[key] = value.strip().strip('"')
    return parsed


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


class HikvisionIsapiClient:
    def __init__(
        self,
        sdk: HikvisionVoiceSDK | None = None,
        *,
        port: int = 80,
        scheme: str = "http",
        timeout_seconds: float = 5.0,
    ) -> None:
        self.sdk = sdk
        self.port = port
        self.scheme = scheme
        self.timeout_seconds = timeout_seconds
        self._nonce_count = 0

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
        candidate_paths = [
            "/ISAPI/System/Audio/capabilities",
            "/ISAPI/System/TwoWayAudio/channels/1/capabilities",
        ]
        path, root = self._get_first_xml(session, candidate_paths)
        if path is None or root is None:
            return AudioInputCapabilityStatus(supported=False, request_path="")

        for node in root.iter():
            local_name = _local_name(node.tag).lower()
            if local_name in {"audioin", "audioinput", "twowayaudiochannel", "audiochannel"}:
                return AudioInputCapabilityStatus(supported=True, request_path=path)
            if local_name in {"audioinputsupport", "supportaudioinput"}:
                value = (node.text or "").strip().lower()
                return AudioInputCapabilityStatus(supported=value in {"true", "1"}, request_path=path)
        return AudioInputCapabilityStatus(supported=False, request_path=path)

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

    def _max_from_node(self, node: ET.Element) -> Optional[int]:
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

        request = urllib.request.Request(url=url, data=payload, method=method.upper(), headers=headers)
        try:
            with urllib.request.urlopen(request, timeout=self.timeout_seconds) as response:
                return response.read(), dict(response.headers.items())
        except urllib.error.HTTPError as exc:
            if exc.code != 401:
                raise self._http_error(exc, method, path)
            challenge = exc.headers.get("WWW-Authenticate", "")
            auth_header = self._build_digest_authorization(
                session=session,
                method=method.upper(),
                request_uri=path,
                challenge_header=challenge,
            )
            retry_headers = dict(headers)
            retry_headers["Authorization"] = auth_header
            retry_request = urllib.request.Request(url=url, data=payload, method=method.upper(), headers=retry_headers)
            try:
                with urllib.request.urlopen(retry_request, timeout=self.timeout_seconds) as response:
                    return response.read(), dict(response.headers.items())
            except urllib.error.HTTPError as retry_exc:
                raise self._http_error(retry_exc, method, path)
            except urllib.error.URLError as retry_exc:
                raise HikvisionSDKError(
                    f"isapi request failed for {method.upper()} {path}",
                    api_name=f"{method.upper()} {path}",
                    error_message=str(retry_exc.reason),
                ) from retry_exc
        except urllib.error.URLError as exc:
            raise HikvisionSDKError(
                f"isapi request failed for {method.upper()} {path}",
                api_name=f"{method.upper()} {path}",
                error_message=str(exc.reason),
            ) from exc

    def _build_digest_authorization(
        self,
        session: DeviceSession,
        method: str,
        request_uri: str,
        challenge_header: str,
    ) -> str:
        params = _parse_www_authenticate(challenge_header)
        realm = params.get("realm")
        nonce = params.get("nonce")
        qop = params.get("qop")
        algorithm = params.get("algorithm", "MD5")
        opaque = params.get("opaque")

        if not realm or not nonce:
            raise HikvisionSDKError("invalid digest auth challenge", error_message=challenge_header)
        if algorithm.upper() != "MD5":
            raise HikvisionSDKError("unsupported digest algorithm", error_message=algorithm)

        qop_value = None
        if qop:
            qop_candidates = [item.strip() for item in qop.split(",")]
            if "auth" in qop_candidates:
                qop_value = "auth"
            elif qop_candidates:
                qop_value = qop_candidates[0]

        nc = self._next_nonce_count()
        cnonce = secrets.token_hex(8)

        ha1 = self._md5_hex(f"{session.username}:{realm}:{session.password}")
        ha2 = self._md5_hex(f"{method}:{request_uri}")
        if qop_value:
            response = self._md5_hex(f"{ha1}:{nonce}:{nc}:{cnonce}:{qop_value}:{ha2}")
        else:
            response = self._md5_hex(f"{ha1}:{nonce}:{ha2}")

        values = [
            f'username="{session.username}"',
            f'realm="{realm}"',
            f'nonce="{nonce}"',
            f'uri="{request_uri}"',
            f'response="{response}"',
            f'algorithm="{algorithm}"',
        ]
        if opaque:
            values.append(f'opaque="{opaque}"')
        if qop_value:
            values.extend(
                [
                    f"qop={qop_value}",
                    f"nc={nc}",
                    f'cnonce="{cnonce}"',
                ]
            )
        return "Digest " + ", ".join(values)

    def _next_nonce_count(self) -> str:
        self._nonce_count += 1
        return f"{self._nonce_count:08x}"

    def _md5_hex(self, value: str) -> str:
        return hashlib.md5(value.encode("utf-8")).hexdigest()

    def _build_url(self, session: DeviceSession, path: str) -> str:
        normalized_path = path if path.startswith("/") else f"/{path}"
        host = session.host
        port = self.port
        netloc = host if (self.scheme == "http" and port == 80) or (self.scheme == "https" and port == 443) else f"{host}:{port}"
        return urllib.parse.urlunsplit((self.scheme, netloc, normalized_path, "", ""))

    def _http_error(self, error: urllib.error.HTTPError, method: str, path: str) -> HikvisionSDKError:
        try:
            detail = error.read().decode("utf-8", errors="ignore")
        except Exception:
            detail = str(error)
        return HikvisionSDKError(
            f"isapi request failed for {method.upper()} {path}",
            error_code=error.code,
            error_message=detail or error.reason,
            api_name=f"{method.upper()} {path}",
        )
