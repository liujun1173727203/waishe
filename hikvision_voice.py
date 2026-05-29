from __future__ import annotations

import atexit
import ctypes
import os
import threading
from datetime import datetime
from ctypes import POINTER, Structure, byref, c_bool, c_byte, c_char, c_char_p, c_int, c_long, c_ubyte, c_uint16, c_uint32, c_void_p
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Optional


NET_DVR_GET_COMPRESSCFG_AUD = 1058
NET_DVR_SET_COMPRESSCFG_AUD = 1059

NET_SDK_INIT_CFG_SDK_PATH = 2
NET_SDK_INIT_CFG_LIBEAY_PATH = 3
NET_SDK_INIT_CFG_SSLEAY_PATH = 4

NET_SDK_LOCAL_CFG_TYPE_TALK_MODE = 5
STREAM_ID_LEN = 32

AUDIO_FLAG_LOCAL = 0
AUDIO_FLAG_REMOTE = 1

TALK_MODE_LIBRARY = 0
TALK_MODE_WINDOWS_API = 1

STREAM_TYPE_MAIN = 0
STREAM_TYPE_SUB = 1

LINK_MODE_TCP = 0
LINK_MODE_UDP = 1

SDK_BOOL = c_uint32
XML_BUFFER_SIZE = 1024 * 1024


class HikvisionSDKError(RuntimeError):
    def __init__(
        self,
        message: str,
        error_code: Optional[int] = None,
        error_message: Optional[str] = None,
        api_name: Optional[str] = None,
    ) -> None:
        parts = [message]
        if api_name:
            parts.append(f"api={api_name}")
        if error_code is not None:
            parts.append(f"error={error_code}")
        if error_message:
            parts.append(f"detail={error_message}")
        super().__init__(" | ".join(parts))
        self.error_code = error_code
        self.error_message = error_message
        self.api_name = api_name


class NET_DVR_LOCAL_SDK_PATH(Structure):
    _fields_ = [
        ("sPath", c_char * 256),
        ("byRes", c_byte * 128),
    ]


class NET_DVR_LOCAL_TALK_MODE_CFG(Structure):
    _fields_ = [
        ("byTalkMode", c_ubyte),
        ("byRes", c_byte * 127),
    ]


class NET_DVR_COMPRESSION_AUDIO(Structure):
    _fields_ = [
        ("byAudioEncType", c_ubyte),
        ("byAudioSamplingRate", c_ubyte),
        ("byAudioBitRate", c_ubyte),
        ("byRes", c_ubyte * 4),
        ("bySupport", c_ubyte),
    ]


class NET_DVR_PREVIEWINFO(Structure):
    _fields_ = [
        ("lChannel", c_long),
        ("dwStreamType", c_uint32),
        ("dwLinkMode", c_uint32),
        ("hPlayWnd", c_void_p),
        ("bBlocked", SDK_BOOL),
        ("bPassbackRecord", SDK_BOOL),
        ("byPreviewMode", c_ubyte),
        ("byStreamID", c_ubyte * STREAM_ID_LEN),
        ("byProtoType", c_ubyte),
        ("byRes1", c_ubyte),
        ("byVideoCodingType", c_ubyte),
        ("dwDisplayBufNum", c_uint32),
        ("byNPQMode", c_ubyte),
        ("byRecvMetaData", c_ubyte),
        ("byDataType", c_ubyte),
        ("byRes", c_ubyte * 213),
    ]


class NET_DVR_XML_CONFIG_INPUT(Structure):
    _fields_ = [
        ("dwSize", c_uint32),
        ("lpRequestUrl", c_void_p),
        ("dwRequestUrlLen", c_uint32),
        ("lpInBuffer", c_void_p),
        ("dwInBufferSize", c_uint32),
        ("dwRecvTimeOut", c_uint32),
        ("byForceEncrpt", c_ubyte),
        ("byRes", c_ubyte * 31),
    ]


class NET_DVR_XML_CONFIG_OUTPUT(Structure):
    _fields_ = [
        ("dwSize", c_uint32),
        ("lpOutBuffer", c_void_p),
        ("dwOutBufferSize", c_uint32),
        ("dwReturnedXMLSize", c_uint32),
        ("lpStatusBuffer", c_void_p),
        ("dwStatusSize", c_uint32),
        ("byRes", c_ubyte * 32),
    ]


class NET_DVR_DEVICEINFO_V30(Structure):
    _fields_ = [
        ("sSerialNumber", c_ubyte * 48),
        ("byAlarmInPortNum", c_ubyte),
        ("byAlarmOutPortNum", c_ubyte),
        ("byDiskNum", c_ubyte),
        ("byDVRType", c_ubyte),
        ("byChanNum", c_ubyte),
        ("byStartChan", c_ubyte),
        ("byAudioChanNum", c_ubyte),
        ("byIPChanNum", c_ubyte),
        ("byZeroChanNum", c_ubyte),
        ("byMainProto", c_ubyte),
        ("bySubProto", c_ubyte),
        ("bySupport", c_ubyte),
        ("bySupport1", c_ubyte),
        ("bySupport2", c_ubyte),
        ("wDevType", ctypes.c_uint16),
        ("bySupport3", c_ubyte),
        ("byMultiStreamProto", c_ubyte),
        ("byStartDChan", c_ubyte),
        ("byStartDTalkChan", c_ubyte),
        ("byHighDChanNum", c_ubyte),
        ("bySupport4", c_ubyte),
        ("byLanguageType", c_ubyte),
        ("byVoiceInChanNum", c_ubyte),
        ("byStartVoiceInChanNo", c_ubyte),
        ("bySupport5", c_ubyte),
        ("bySupport6", c_ubyte),
        ("byMirrorChanNum", c_ubyte),
        ("wStartMirrorChanNo", ctypes.c_uint16),
        ("bySupport7", c_ubyte),
        ("byRes2", c_ubyte * 2),
    ]


class NET_DVR_USER_LOGIN_INFO(Structure):
    _fields_ = [
        ("sDeviceAddress", c_char * 129),
        ("byUseTransport", c_ubyte),
        ("wPort", c_uint16),
        ("sUserName", c_char * 64),
        ("sPassword", c_char * 64),
        ("cbLoginResult", c_void_p),
        ("bUseAsynLogin", SDK_BOOL),
        ("byProxyType", c_ubyte),
        ("byUseUTCTime", c_ubyte),
        ("byLoginMode", c_ubyte),
        ("byHttps", c_ubyte),
        ("iProxyID", c_long),
        ("byVerifyMode", c_ubyte),
        ("byRes3", c_ubyte),
        ("bySupport", c_ubyte),
        ("byRetryLoginTime", c_ubyte),
        ("byRes2", c_byte * 119),
    ]


class NET_DVR_DEVICEINFO_V40(Structure):
    _fields_ = [
        ("struDeviceV30", NET_DVR_DEVICEINFO_V30),
        ("bySupportLock", c_ubyte),
        ("byRetryLoginTime", c_ubyte),
        ("byPasswordLevel", c_ubyte),
        ("byProxyType", c_ubyte),
        ("dwSurplusLockTime", c_uint32),
        ("byCharEncodeType", c_ubyte),
        ("bySupportDev5", c_ubyte),
        ("bySupport", c_ubyte),
        ("byLoginMode", c_ubyte),
        ("dwOEMCode", c_uint32),
        ("iResidualValidity", c_int),
        ("byResidualValidity", c_ubyte),
        ("bySingleStartDTalkChan", c_ubyte),
        ("bySingleDTalkChanNums", c_ubyte),
        ("byPassWordResetLevel", c_ubyte),
        ("bySupportStreamEncrypt", c_ubyte),
        ("byMarketType", c_ubyte),
        ("byRes2", c_byte * 238),
    ]


VOICE_DATA_CALLBACK = ctypes.WINFUNCTYPE(None, c_long, c_char_p, c_uint32, c_ubyte, c_void_p)
REAL_DATA_CALLBACK = ctypes.WINFUNCTYPE(None, c_long, c_uint32, c_void_p, c_uint32, c_void_p)


@dataclass(frozen=True)
class AudioCompressInfo:
    encode_type: int
    sampling_rate: int
    bit_rate: int
    support_flag: int


@dataclass(frozen=True)
class DeviceSession:
    user_id: int
    host: str
    device_info: NET_DVR_DEVICEINFO_V30
    device_info_v40: Optional[NET_DVR_DEVICEINFO_V40] = None

    @property
    def default_voice_channel(self) -> int:
        return self.device_info.byStartDTalkChan or 1

    @property
    def default_preview_channel(self) -> int:
        return self.device_info.byStartChan or 1


class HikvisionVoiceSDK:
    def __init__(self, sdk_root: str | os.PathLike[str] | None = None) -> None:
        self.sdk_root = Path(sdk_root or Path(__file__).resolve().parent / "libs" / "win64").resolve()
        self._sdk = None
        self._initialized = False
        self._cleanup_registered = False
        self._lock = threading.RLock()
        self._active_callbacks: dict[int, VOICE_DATA_CALLBACK] = {}

    def initialize(self, enable_log: bool = False, log_dir: str | os.PathLike[str] | None = None) -> None:
        with self._lock:
            if self._initialized:
                return
            self._load_sdk()
            self._configure_init_paths()
            self._bind_functions()
            if not self._sdk.NET_DVR_Init():
                raise self._last_error("NET_DVR_Init failed", "NET_DVR_Init")
            if enable_log:
                target = Path(log_dir or Path.cwd() / "SdkLog")
                target.mkdir(parents=True, exist_ok=True)
                self._sdk.NET_DVR_SetLogToFile(3, str(target).encode("gbk", errors="ignore"), False)
            self._initialized = True
            if not self._cleanup_registered:
                atexit.register(self.cleanup)
                self._cleanup_registered = True

    def cleanup(self) -> None:
        with self._lock:
            if not self._initialized or self._sdk is None:
                return
            self._active_callbacks.clear()
            self._sdk.NET_DVR_Cleanup()
            self._initialized = False

    def get_last_error_info(self) -> dict[str, Optional[str | int]]:
        code = int(self._sdk.NET_DVR_GetLastError()) if self._sdk is not None else None
        return {
            "error_code": code,
            "error_message": self._get_error_message(code),
        }

    def login(self, host: str, port: int, username: str, password: str) -> DeviceSession:
        self._require_initialized()
        login_info = NET_DVR_USER_LOGIN_INFO()
        login_info.sDeviceAddress = host.encode("ascii")
        login_info.wPort = port
        login_info.sUserName = username.encode("ascii")
        login_info.sPassword = password.encode("ascii")
        login_info.bUseAsynLogin = False
        login_info.byLoginMode = 0

        device_info_v40 = NET_DVR_DEVICEINFO_V40()
        user_id = self._sdk.NET_DVR_Login_V40(byref(login_info), byref(device_info_v40))
        if user_id < 0:
            raise self._last_error(f"NET_DVR_Login_V40 failed for {host}:{port}", "NET_DVR_Login_V40")
        return DeviceSession(
            user_id=user_id,
            host=host,
            device_info=device_info_v40.struDeviceV30,
            device_info_v40=device_info_v40,
        )

    def logout(self, session: DeviceSession) -> None:
        self._require_initialized()
        if not self._sdk.NET_DVR_Logout(session.user_id):
            raise self._last_error("NET_DVR_Logout failed", "NET_DVR_Logout")

    def set_talk_mode(self, use_windows_api: bool = False) -> None:
        self._require_initialized()
        cfg = NET_DVR_LOCAL_TALK_MODE_CFG()
        cfg.byTalkMode = TALK_MODE_WINDOWS_API if use_windows_api else TALK_MODE_LIBRARY
        if not self._sdk.NET_DVR_SetSDKLocalCfg(NET_SDK_LOCAL_CFG_TYPE_TALK_MODE, byref(cfg)):
            raise self._last_error("NET_DVR_SetSDKLocalCfg(TALK_MODE) failed", "NET_DVR_SetSDKLocalCfg")

    def get_current_audio_compress(self, session: DeviceSession) -> AudioCompressInfo:
        self._require_initialized()
        compress = NET_DVR_COMPRESSION_AUDIO()
        if not self._sdk.NET_DVR_GetCurrentAudioCompress(session.user_id, byref(compress)):
            raise self._last_error("NET_DVR_GetCurrentAudioCompress failed", "NET_DVR_GetCurrentAudioCompress")
        return AudioCompressInfo(
            encode_type=compress.byAudioEncType,
            sampling_rate=compress.byAudioSamplingRate,
            bit_rate=compress.byAudioBitRate,
            support_flag=compress.bySupport,
        )

    def set_audio_compress(self, session: DeviceSession, encode_type: int, sampling_rate: int = 0, bit_rate: int = 0) -> None:
        self._require_initialized()
        compress = NET_DVR_COMPRESSION_AUDIO()
        compress.byAudioEncType = encode_type
        compress.byAudioSamplingRate = sampling_rate
        compress.byAudioBitRate = bit_rate
        ok = self._sdk.NET_DVR_SetDVRConfig(
            session.user_id,
            NET_DVR_SET_COMPRESSCFG_AUD,
            0xFFFFFFFF,
            byref(compress),
            ctypes.sizeof(compress),
        )
        if not ok:
            raise self._last_error("NET_DVR_SetDVRConfig(VOICE) failed", "NET_DVR_SetDVRConfig")

    def start_call(
        self,
        session: DeviceSession,
        voice_channel: Optional[int] = None,
        need_pcm_callback: bool = False,
        audio_callback: Optional[Callable[[bytes, int], None]] = None,
    ) -> "VoiceCall":
        self._require_initialized()
        callback = None
        if audio_callback is not None:
            def _handler(_handle: int, buffer: bytes, audio_flag: int) -> None:
                audio_callback(buffer, audio_flag)

            callback = _handler
        call = VoiceCall(
            sdk=self,
            session=session,
            voice_channel=voice_channel or session.default_voice_channel,
            need_pcm_callback=need_pcm_callback,
            audio_callback=callback,
        )
        call.start()
        return call

    def start_voice_forward(
        self,
        session: DeviceSession,
        voice_channel: Optional[int] = None,
        encoded_audio_callback: Optional[Callable[[bytes, int], None]] = None,
    ) -> "VoiceForwardSession":
        self._require_initialized()
        forward = VoiceForwardSession(
            sdk=self,
            session=session,
            voice_channel=voice_channel or session.default_voice_channel,
            encoded_audio_callback=encoded_audio_callback,
        )
        forward.start()
        return forward

    def start_stream_record(
        self,
        session: DeviceSession,
        file_path: str | os.PathLike[str] | None = None,
        channel: Optional[int] = None,
        stream_type: int = STREAM_TYPE_MAIN,
        link_mode: int = LINK_MODE_TCP,
        blocked: bool = True,
        real_data_callback: Optional[Callable[[int, int, bytes], None]] = None,
    ) -> "StreamRecorder":
        self._require_initialized()
        target_channel = channel or session.default_preview_channel
        recorder = StreamRecorder(
            sdk=self,
            session=session,
            file_path=Path(file_path) if file_path is not None else self._default_stream_record_path(session.host, target_channel),
            channel=target_channel,
            stream_type=stream_type,
            link_mode=link_mode,
            blocked=blocked,
            real_data_callback=real_data_callback,
        )
        recorder.start()
        return recorder

    def _load_sdk(self) -> None:
        if not self.sdk_root.exists():
            raise FileNotFoundError(f"SDK path not found: {self.sdk_root}")
        os.add_dll_directory(str(self.sdk_root))
        com_dir = self.sdk_root / "HCNetSDKCom"
        if com_dir.exists():
            os.add_dll_directory(str(com_dir))
        os.environ["PATH"] = f"{self.sdk_root};{com_dir};{os.environ.get('PATH', '')}"
        self._sdk = ctypes.WinDLL(str(self.sdk_root / "HCNetSDK.dll"))

    def _configure_init_paths(self) -> None:
        sdk_path = NET_DVR_LOCAL_SDK_PATH()
        com_dir = str((self.sdk_root / "HCNetSDKCom").resolve()).encode("gbk", errors="ignore")
        sdk_path.sPath = com_dir
        if not self._sdk.NET_DVR_SetSDKInitCfg(NET_SDK_INIT_CFG_SDK_PATH, byref(sdk_path)):
            raise self._last_error("NET_DVR_SetSDKInitCfg(SDK_PATH) failed", "NET_DVR_SetSDKInitCfg")
        crypto_path = str((self.sdk_root / "libcrypto-3-x64.dll").resolve()).encode("gbk", errors="ignore")
        ssl_path = str((self.sdk_root / "libssl-3-x64.dll").resolve()).encode("gbk", errors="ignore")
        self._sdk.NET_DVR_SetSDKInitCfg(NET_SDK_INIT_CFG_LIBEAY_PATH, c_char_p(crypto_path))
        self._sdk.NET_DVR_SetSDKInitCfg(NET_SDK_INIT_CFG_SSLEAY_PATH, c_char_p(ssl_path))

    def _bind_functions(self) -> None:
        self._sdk.NET_DVR_Init.restype = c_bool
        self._sdk.NET_DVR_Cleanup.restype = c_bool
        self._sdk.NET_DVR_GetLastError.restype = c_uint32
        self._sdk.NET_DVR_GetErrorMsg.argtypes = [POINTER(c_long)]
        self._sdk.NET_DVR_GetErrorMsg.restype = c_char_p
        self._sdk.NET_DVR_Login_V40.argtypes = [POINTER(NET_DVR_USER_LOGIN_INFO), POINTER(NET_DVR_DEVICEINFO_V40)]
        self._sdk.NET_DVR_Login_V40.restype = c_long
        self._sdk.NET_DVR_Logout.argtypes = [c_long]
        self._sdk.NET_DVR_Logout.restype = c_bool
        self._sdk.NET_DVR_SetSDKInitCfg.argtypes = [c_long, c_void_p]
        self._sdk.NET_DVR_SetSDKInitCfg.restype = c_bool
        self._sdk.NET_DVR_SetSDKLocalCfg.argtypes = [c_long, c_void_p]
        self._sdk.NET_DVR_SetSDKLocalCfg.restype = c_bool
        self._sdk.NET_DVR_SetLogToFile.argtypes = [c_long, c_char_p, c_bool]
        self._sdk.NET_DVR_SetLogToFile.restype = c_bool
        self._sdk.NET_DVR_GetCurrentAudioCompress.argtypes = [c_long, POINTER(NET_DVR_COMPRESSION_AUDIO)]
        self._sdk.NET_DVR_GetCurrentAudioCompress.restype = c_bool
        self._sdk.NET_DVR_SetDVRConfig.argtypes = [c_long, c_uint32, c_long, c_void_p, c_uint32]
        self._sdk.NET_DVR_SetDVRConfig.restype = c_bool
        self._sdk.NET_DVR_StartVoiceCom_V30.argtypes = [c_long, c_uint32, c_bool, VOICE_DATA_CALLBACK, c_void_p]
        self._sdk.NET_DVR_StartVoiceCom_V30.restype = c_long
        self._sdk.NET_DVR_StartVoiceCom_MR_V30.argtypes = [c_long, c_uint32, VOICE_DATA_CALLBACK, c_void_p]
        self._sdk.NET_DVR_StartVoiceCom_MR_V30.restype = c_long
        self._sdk.NET_DVR_StopVoiceCom.argtypes = [c_long]
        self._sdk.NET_DVR_StopVoiceCom.restype = c_bool
        self._sdk.NET_DVR_VoiceComSendData.argtypes = [c_long, c_char_p, c_uint32]
        self._sdk.NET_DVR_VoiceComSendData.restype = c_bool
        self._sdk.NET_DVR_RealPlay_V40.argtypes = [c_long, POINTER(NET_DVR_PREVIEWINFO), REAL_DATA_CALLBACK, c_void_p]
        self._sdk.NET_DVR_RealPlay_V40.restype = c_long
        self._sdk.NET_DVR_StopRealPlay.argtypes = [c_long]
        self._sdk.NET_DVR_StopRealPlay.restype = c_bool
        self._sdk.NET_DVR_SaveRealData.argtypes = [c_long, c_char_p]
        self._sdk.NET_DVR_SaveRealData.restype = c_bool
        self._sdk.NET_DVR_StopSaveRealData.argtypes = [c_long]
        self._sdk.NET_DVR_StopSaveRealData.restype = c_bool
        self._sdk.NET_DVR_STDXMLConfig.argtypes = [c_long, POINTER(NET_DVR_XML_CONFIG_INPUT), POINTER(NET_DVR_XML_CONFIG_OUTPUT)]
        self._sdk.NET_DVR_STDXMLConfig.restype = c_bool

    def _build_callback(self, callback: Optional[Callable[[int, bytes, int], None]]) -> VOICE_DATA_CALLBACK:
        def _wrapped(voice_handle: int, recv_buffer: bytes, buf_size: int, audio_flag: int, _user: int) -> None:
            if callback is None or not recv_buffer or buf_size == 0:
                return
            data = ctypes.string_at(recv_buffer, buf_size)
            callback(voice_handle, data, audio_flag)

        return VOICE_DATA_CALLBACK(_wrapped)

    def _remember_callback(self, handle: int, callback: VOICE_DATA_CALLBACK) -> None:
        self._active_callbacks[handle] = callback

    def _forget_callback(self, handle: int) -> None:
        self._active_callbacks.pop(handle, None)

    def _build_real_data_callback(self, callback: Optional[Callable[[int, int, bytes], None]]) -> REAL_DATA_CALLBACK:
        def _wrapped(real_handle: int, data_type: int, buffer_ptr: int, buf_size: int, _user: int) -> None:
            if callback is None or not buffer_ptr or buf_size == 0:
                return
            data = ctypes.string_at(buffer_ptr, buf_size)
            callback(real_handle, data_type, data)

        return REAL_DATA_CALLBACK(_wrapped)

    def _default_stream_record_path(self, host: str, channel: int) -> Path:
        host_dir = "".join(char if char.isalnum() or char in "._-" else "_" for char in host)
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        return Path.cwd() / "recordings" / "streams" / host_dir / f"stream_ch{channel}_{timestamp}.mp4"

    def stdxml_config(
        self,
        session: DeviceSession,
        method: str,
        path: str,
        body: str | None = None,
        timeout_ms: int = 5000,
    ) -> tuple[str, str]:
        self._require_initialized()
        request_line = f"{method.upper()} {path}"
        request_bytes = request_line.encode("ascii")
        in_bytes = body.encode("utf-8") if body is not None else b""
        request_buffer = ctypes.create_string_buffer(request_bytes)
        in_buffer = ctypes.create_string_buffer(in_bytes) if in_bytes else None
        out_buffer = ctypes.create_string_buffer(XML_BUFFER_SIZE)
        status_buffer = ctypes.create_string_buffer(16 * 1024)

        input_cfg = NET_DVR_XML_CONFIG_INPUT()
        input_cfg.dwSize = ctypes.sizeof(input_cfg)
        input_cfg.lpRequestUrl = ctypes.cast(request_buffer, c_void_p)
        input_cfg.dwRequestUrlLen = len(request_bytes)
        if in_buffer is not None:
            input_cfg.lpInBuffer = ctypes.cast(in_buffer, c_void_p)
            input_cfg.dwInBufferSize = len(in_bytes)
        input_cfg.dwRecvTimeOut = timeout_ms

        output_cfg = NET_DVR_XML_CONFIG_OUTPUT()
        output_cfg.dwSize = ctypes.sizeof(output_cfg)
        output_cfg.lpOutBuffer = ctypes.cast(out_buffer, c_void_p)
        output_cfg.dwOutBufferSize = ctypes.sizeof(out_buffer)
        output_cfg.lpStatusBuffer = ctypes.cast(status_buffer, c_void_p)
        output_cfg.dwStatusSize = ctypes.sizeof(status_buffer)

        ok = self._sdk.NET_DVR_STDXMLConfig(session.user_id, byref(input_cfg), byref(output_cfg))
        if not ok:
            raise self._last_error(f"NET_DVR_STDXMLConfig failed for {request_line}", "NET_DVR_STDXMLConfig")

        out_text = out_buffer.raw[: output_cfg.dwReturnedXMLSize].decode("utf-8", errors="ignore").strip("\x00")
        status_text = status_buffer.value.decode("utf-8", errors="ignore").strip("\x00")
        return out_text, status_text


    def _require_initialized(self) -> None:
        if not self._initialized or self._sdk is None:
            raise HikvisionSDKError("SDK not initialized")

    def _get_error_message(self, error_code: Optional[int] = None) -> Optional[str]:
        if self._sdk is None:
            return None
        code = c_long(-1 if error_code is None else int(error_code))
        raw = self._sdk.NET_DVR_GetErrorMsg(byref(code))
        if not raw:
            return None
        try:
            return raw.decode("gbk")
        except UnicodeDecodeError:
            return raw.decode("utf-8", errors="ignore")

    def _last_error(self, message: str, api_name: Optional[str] = None) -> HikvisionSDKError:
        code = int(self._sdk.NET_DVR_GetLastError()) if self._sdk is not None else None
        return HikvisionSDKError(message, code, self._get_error_message(code), api_name)


class VoiceCall:
    def __init__(
        self,
        sdk: HikvisionVoiceSDK,
        session: DeviceSession,
        voice_channel: int,
        need_pcm_callback: bool,
        audio_callback: Optional[Callable[[int, bytes, int], None]],
    ) -> None:
        self.sdk = sdk
        self.session = session
        self.voice_channel = voice_channel
        self.need_pcm_callback = need_pcm_callback
        self.audio_callback = audio_callback
        self.handle: Optional[int] = None

    def start(self) -> None:
        if self.handle is not None:
            return
        callback = self.sdk._build_callback(self.audio_callback)
        handle = self.sdk._sdk.NET_DVR_StartVoiceCom_V30(
            self.session.user_id,
            self.voice_channel,
            self.need_pcm_callback,
            callback,
            None,
        )
        if handle < 0:
            raise self.sdk._last_error("NET_DVR_StartVoiceCom_V30 failed", "NET_DVR_StartVoiceCom_V30")
        self.handle = handle
        self.sdk._remember_callback(handle, callback)

    def stop(self) -> None:
        if self.handle is None:
            return
        handle = self.handle
        self.handle = None
        self.sdk._forget_callback(handle)
        if not self.sdk._sdk.NET_DVR_StopVoiceCom(handle):
            raise self.sdk._last_error("NET_DVR_StopVoiceCom failed", "NET_DVR_StopVoiceCom")

    def __enter__(self) -> "VoiceCall":
        self.start()
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        self.stop()


class VoiceForwardSession:
    def __init__(
        self,
        sdk: HikvisionVoiceSDK,
        session: DeviceSession,
        voice_channel: int,
        encoded_audio_callback: Optional[Callable[[bytes, int], None]],
    ) -> None:
        self.sdk = sdk
        self.session = session
        self.voice_channel = voice_channel
        self.encoded_audio_callback = encoded_audio_callback
        self.handle: Optional[int] = None

    def start(self) -> None:
        if self.handle is not None:
            return

        def _handler(_voice_handle: int, data: bytes, audio_flag: int) -> None:
            if self.encoded_audio_callback is not None:
                self.encoded_audio_callback(data, audio_flag)

        callback = self.sdk._build_callback(_handler)
        handle = self.sdk._sdk.NET_DVR_StartVoiceCom_MR_V30(
            self.session.user_id,
            self.voice_channel,
            callback,
            None,
        )
        if handle < 0:
            raise self.sdk._last_error("NET_DVR_StartVoiceCom_MR_V30 failed", "NET_DVR_StartVoiceCom_MR_V30")
        self.handle = handle
        self.sdk._remember_callback(handle, callback)

    def send_encoded_audio(self, data: bytes) -> None:
        if self.handle is None:
            raise HikvisionSDKError("Voice forwarding session not started")
        if not data:
            return
        if not self.sdk._sdk.NET_DVR_VoiceComSendData(self.handle, data, len(data)):
            raise self.sdk._last_error("NET_DVR_VoiceComSendData failed", "NET_DVR_VoiceComSendData")

    def stop(self) -> None:
        if self.handle is None:
            return
        handle = self.handle
        self.handle = None
        self.sdk._forget_callback(handle)
        if not self.sdk._sdk.NET_DVR_StopVoiceCom(handle):
            raise self.sdk._last_error("NET_DVR_StopVoiceCom failed", "NET_DVR_StopVoiceCom")

    def __enter__(self) -> "VoiceForwardSession":
        self.start()
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        self.stop()


class StreamRecorder:
    def __init__(
        self,
        sdk: HikvisionVoiceSDK,
        session: DeviceSession,
        file_path: Path,
        channel: int,
        stream_type: int,
        link_mode: int,
        blocked: bool,
        real_data_callback: Optional[Callable[[int, int, bytes], None]],
    ) -> None:
        self.sdk = sdk
        self.session = session
        self.file_path = file_path
        self.channel = channel
        self.stream_type = stream_type
        self.link_mode = link_mode
        self.blocked = blocked
        self.real_data_callback = real_data_callback
        self.handle: Optional[int] = None
        self._saving = False

    def start(self) -> None:
        if self.handle is not None:
            return

        self.file_path.parent.mkdir(parents=True, exist_ok=True)
        preview_info = NET_DVR_PREVIEWINFO()
        preview_info.lChannel = self.channel
        preview_info.dwStreamType = self.stream_type
        preview_info.dwLinkMode = self.link_mode
        preview_info.hPlayWnd = None
        preview_info.bBlocked = bool(self.blocked)
        preview_info.bPassbackRecord = False

        callback = self.sdk._build_real_data_callback(self.real_data_callback)
        handle = self.sdk._sdk.NET_DVR_RealPlay_V40(
            self.session.user_id,
            byref(preview_info),
            callback,
            None,
        )
        if handle < 0:
            raise self.sdk._last_error("NET_DVR_RealPlay_V40 failed", "NET_DVR_RealPlay_V40")

        encoded_path = str(self.file_path.resolve()).encode("gbk", errors="ignore")
        if not self.sdk._sdk.NET_DVR_SaveRealData(handle, encoded_path):
            self.sdk._sdk.NET_DVR_StopRealPlay(handle)
            raise self.sdk._last_error("NET_DVR_SaveRealData failed", "NET_DVR_SaveRealData")

        self.handle = handle
        self._saving = True
        self.sdk._remember_callback(handle, callback)

    def stop(self) -> None:
        if self.handle is None:
            return

        handle = self.handle
        self.handle = None
        self.sdk._forget_callback(handle)
        errors: list[HikvisionSDKError] = []

        if self._saving:
            self._saving = False
            if not self.sdk._sdk.NET_DVR_StopSaveRealData(handle):
                errors.append(self.sdk._last_error("NET_DVR_StopSaveRealData failed", "NET_DVR_StopSaveRealData"))

        if not self.sdk._sdk.NET_DVR_StopRealPlay(handle):
            errors.append(self.sdk._last_error("NET_DVR_StopRealPlay failed", "NET_DVR_StopRealPlay"))

        if errors:
            raise errors[0]

    def __enter__(self) -> "StreamRecorder":
        self.start()
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        self.stop()


if __name__ =="__main__":
    sdk=HikvisionVoiceSDK()
    sdk.initialize(enable_log=True)
    sdk.login("10.41.203.51", 8000, "admin", "abcd1234")
