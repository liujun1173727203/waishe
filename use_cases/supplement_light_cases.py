from __future__ import annotations

import shutil
import subprocess
import time
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Optional

from hikvision_isapi import HikvisionIsapiClient, SupplementLightStatus
from hikvision_voice import CapturePictureResult, DeviceSession, HikvisionSDKError, HikvisionVoiceSDK, STREAM_TYPE_MAIN


SUPPLEMENT_LIGHT_LEVELS = (0, 50, 100)


@dataclass(frozen=True)
class SupplementLightLevelResult:
    level: int
    status: SupplementLightStatus
    capture_result: CapturePictureResult
    brightness: float


@dataclass(frozen=True)
class SupplementLightTestResult:
    baseline_status: SupplementLightStatus
    baseline_capture_result: CapturePictureResult
    baseline_brightness: float
    level_results: tuple[SupplementLightLevelResult, ...]
    light_on_pass: bool
    level_pass: bool
    passed: bool
    on_delta: float
    level_0_to_50_delta: float
    level_50_to_100_delta: float
    on_threshold: float
    level_threshold: float


class SupplementLightUseCases:
    def __init__(
        self,
        sdk: HikvisionVoiceSDK,
        isapi: HikvisionIsapiClient,
        *,
        ffmpeg_path: str = "ffmpeg",
    ) -> None:
        self.sdk = sdk
        self.isapi = isapi
        self.ffmpeg_path = ffmpeg_path

    def _log_step(self, message: str) -> None:
        print(f"[supplement-light-test] {message}", flush=True)

    def run_supplement_light_test(
        self,
        session: DeviceSession,
        channel: Optional[int] = None,
        output_dir: str | Path | None = None,
        settle_seconds: float = 2.0,
        on_threshold: float = 10.0,
        level_threshold: float = 5.0,
        stream_type: int = STREAM_TYPE_MAIN,
    ) -> SupplementLightTestResult:
        if settle_seconds < 0:
            raise ValueError("settle_seconds must be non-negative")
        if on_threshold <= 0:
            raise ValueError("on_threshold must be positive")
        if level_threshold <= 0:
            raise ValueError("level_threshold must be positive")

        target_channel = channel or session.default_preview_channel
        base_dir = Path(output_dir) if output_dir is not None else self._default_output_dir(session.host)
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        test_id = f"{session.host.replace('.', '_')}_{timestamp}"

        self._log_step(f"start host={session.host} channel={target_channel}")
        self._log_step(f"check image analysis dependency ffmpeg path={self.ffmpeg_path}")
        ffmpeg = self._resolve_ffmpeg()
        self._log_step(f"image analysis dependency ready ffmpeg={ffmpeg}")

        status = self.isapi.get_supplement_light_status(session, channel=target_channel)
        if not status.supported:
            raise HikvisionSDKError(
                "device does not support supplement light config",
                api_name=status.config_path or "GET /ISAPI/Image/channels/<channel>/SupplementLight",
            )
        self._log_step(
            "supplement light capability ready "
            f"path={status.config_path} brightness_range={status.brightness_min}-{status.brightness_max} "
            f"enabled={status.enabled} brightness={status.brightness} mode={status.mode or 'unknown'}"
        )

        self._log_step("set supplement light baseline off/level 0")
        baseline_status = self.isapi.ensure_supplement_light_ready(
            session=session,
            channel=target_channel,
            enabled=False,
            brightness=0,
        )
        time.sleep(settle_seconds)
        baseline_capture = self._capture(
            session=session,
            channel=target_channel,
            file_path=base_dir / f"supplement_light_baseline_off_{test_id}.jpg",
            stream_type=stream_type,
        )
        baseline_brightness = self._image_brightness(baseline_capture.file_path)
        self._log_step(
            f"baseline captured brightness={baseline_brightness:.2f} "
            f"method={baseline_capture.method} path={baseline_capture.file_path}"
        )

        level_results: list[SupplementLightLevelResult] = []
        for level in SUPPLEMENT_LIGHT_LEVELS:
            self._log_step(f"set supplement light enabled level={level}")
            level_status = self.isapi.ensure_supplement_light_ready(
                session=session,
                channel=target_channel,
                enabled=True,
                brightness=level,
            )
            time.sleep(settle_seconds)
            capture_result = self._capture(
                session=session,
                channel=target_channel,
                file_path=base_dir / f"supplement_light_level_{level}_{test_id}.jpg",
                stream_type=stream_type,
            )
            brightness = self._image_brightness(capture_result.file_path)
            self._log_step(
                f"level analysis level={level} brightness={brightness:.2f} "
                f"method={capture_result.method} fallback={capture_result.fallback_used} "
                f"path={capture_result.file_path}"
            )
            level_results.append(
                SupplementLightLevelResult(
                    level=level,
                    status=level_status,
                    capture_result=capture_result,
                    brightness=brightness,
                )
            )

        brightness_by_level = {result.level: result.brightness for result in level_results}
        on_delta = brightness_by_level[100] - baseline_brightness
        level_0_to_50_delta = brightness_by_level[50] - brightness_by_level[0]
        level_50_to_100_delta = brightness_by_level[100] - brightness_by_level[50]
        light_on_pass = on_delta >= on_threshold
        level_pass = level_0_to_50_delta >= level_threshold and level_50_to_100_delta >= level_threshold
        passed = light_on_pass and level_pass

        self._log_step(
            "final conclusion "
            f"baseline={baseline_brightness:.2f} "
            f"level0={brightness_by_level[0]:.2f} "
            f"level50={brightness_by_level[50]:.2f} "
            f"level100={brightness_by_level[100]:.2f} "
            f"on_delta={on_delta:.2f} "
            f"level_0_to_50_delta={level_0_to_50_delta:.2f} "
            f"level_50_to_100_delta={level_50_to_100_delta:.2f} "
            f"light_on_pass={light_on_pass} level_pass={level_pass} passed={passed}"
        )

        return SupplementLightTestResult(
            baseline_status=baseline_status,
            baseline_capture_result=baseline_capture,
            baseline_brightness=baseline_brightness,
            level_results=tuple(level_results),
            light_on_pass=light_on_pass,
            level_pass=level_pass,
            passed=passed,
            on_delta=on_delta,
            level_0_to_50_delta=level_0_to_50_delta,
            level_50_to_100_delta=level_50_to_100_delta,
            on_threshold=on_threshold,
            level_threshold=level_threshold,
        )

    def _capture(
        self,
        session: DeviceSession,
        channel: int,
        file_path: Path,
        stream_type: int,
    ) -> CapturePictureResult:
        file_path.parent.mkdir(parents=True, exist_ok=True)
        return self.sdk.capture_picture(
            session=session,
            file_path=file_path,
            channel=channel,
            stream_type=stream_type,
        )

    def _image_brightness(self, image_path: Path) -> float:
        ffmpeg = self._resolve_ffmpeg()
        command = [
            ffmpeg,
            "-v",
            "error",
            "-i",
            str(image_path),
            "-vf",
            "crop=iw*0.4:ih*0.4:iw*0.3:ih*0.3,format=gray",
            "-f",
            "rawvideo",
            "-pix_fmt",
            "gray",
            "-",
        ]
        result = subprocess.run(command, capture_output=True)
        if result.returncode != 0:
            raise HikvisionSDKError(
                "image brightness analysis failed",
                api_name="ffmpeg",
                error_message=result.stderr.decode("utf-8", errors="ignore").strip(),
            )
        if not result.stdout:
            raise HikvisionSDKError("image brightness analysis returned empty data", api_name="ffmpeg")
        return sum(result.stdout) / len(result.stdout)

    def _resolve_ffmpeg(self) -> str:
        configured = Path(self.ffmpeg_path)
        if configured.is_file():
            return str(configured.resolve())
        if configured.is_dir():
            for candidate in (configured / "ffmpeg.exe", configured / "ffmpeg"):
                if candidate.is_file():
                    return str(candidate.resolve())
        resolved = shutil.which(self.ffmpeg_path)
        if resolved is not None:
            return resolved
        raise HikvisionSDKError(
            f"ffmpeg not found: {self.ffmpeg_path}. Please install ffmpeg or pass a valid ffmpeg_path.",
            api_name="ffmpeg",
        )

    def _default_output_dir(self, host: str) -> Path:
        host_dir = "".join(char if char.isalnum() or char in "._-" else "_" for char in host)
        return Path.cwd() / "recordings" / "supplement_light_tests" / host_dir
