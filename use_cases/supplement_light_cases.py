from __future__ import annotations

import subprocess
import time
from contextlib import suppress
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Optional

from app_config import get_ffmpeg_path, resolve_ffmpeg_path
from hikvision_isapi import HikvisionIsapiClient, IrcutFilterStatus, MixedSupplementLightStatus
from hikvision_voice import CapturePictureResult, DeviceSession, HikvisionSDKError, HikvisionVoiceSDK, STREAM_TYPE_MAIN


TEST_LIGHT_MODES = ("colorVuWhiteLight", "irLight")


@dataclass(frozen=True)
class SupplementLightCaptureResult:
    mode: str
    brightness_limit: int
    status: MixedSupplementLightStatus
    capture_result: CapturePictureResult
    image_brightness: float


@dataclass(frozen=True)
class SupplementLightModeResult:
    mode: str
    min_limit: int
    middle_limit: int
    max_limit: int
    min_image_brightness: float
    middle_image_brightness: float
    max_image_brightness: float
    min_to_middle_delta: float
    middle_to_max_delta: float
    passed: bool


@dataclass(frozen=True)
class SupplementLightFunctionResult:
    mode: str
    before_mode: str
    before_brightness_limit: int
    before_image_brightness: float
    before_capture_result: CapturePictureResult
    after_brightness_limit: int
    after_image_brightness: float
    after_capture_result: CapturePictureResult
    brightness_delta: float
    threshold: float
    passed: bool


@dataclass(frozen=True)
class SupplementLightTestResult:
    channel: int
    original_status: MixedSupplementLightStatus
    original_ircut_status: IrcutFilterStatus
    night_ircut_status: IrcutFilterStatus
    tested_modes: tuple[str, ...]
    function_results: tuple[SupplementLightFunctionResult, ...]
    capture_results: tuple[SupplementLightCaptureResult, ...]
    mode_results: tuple[SupplementLightModeResult, ...]
    function_pass: bool
    effect_pass: bool
    passed: bool
    on_threshold: float
    level_threshold: float


class SupplementLightUseCases:
    def __init__(
        self,
        sdk: HikvisionVoiceSDK,
        isapi: HikvisionIsapiClient,
        *,
        ffmpeg_path: str | None = None,
    ) -> None:
        """
        作用：初始化对象实例，保存后续执行所需的依赖、配置或运行状态。
        执行步骤：
        1. 接收并校验输入参数。
        2. 执行方法职责对应的核心处理。
        3. 返回处理结果，失败时抛出异常。
        """
        self.sdk = sdk
        self.isapi = isapi
        self.ffmpeg_path = ffmpeg_path or get_ffmpeg_path()

    def _log_step(self, message: str) -> None:
        """
        作用：作为内部辅助方法，完成本方法对应的数据处理。
        执行步骤：
        1. 接收并校验输入参数。
        2. 执行方法职责对应的核心处理。
        3. 返回处理结果，失败时抛出异常。
        """
        print(f"[补光灯测试] {message}", flush=True)

    def run_supplement_light_test(
        self,
        session: DeviceSession,
        channel: Optional[int] = None,
        capture_channel: Optional[int] = None,
        output_dir: str | Path | None = None,
        settle_seconds: float = 2.0,
        on_threshold: float = 10.0,
        level_threshold: float = 5.0,
        stream_type: int = STREAM_TYPE_MAIN,
    ) -> SupplementLightTestResult:
        """
        作用：编排并执行完整业务或测试用例，生成执行结果。
        执行步骤：
        1. 解析输入参数并准备依赖对象。
        2. 按业务流程顺序执行核心步骤。
        3. 输出日志、执行结果或退出码。
        """
        if settle_seconds < 0:
            raise ValueError("settle_seconds 必须大于等于 0")
        if on_threshold <= 0 or level_threshold <= 0:
            raise ValueError("亮度判定阈值必须大于 0")

        image_channel = channel or session.default_preview_channel
        target_capture_channel = capture_channel or session.default_preview_channel
        base_dir = Path(output_dir) if output_dir is not None else self._default_output_dir(session.host)
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        test_id = (
            f"{session.host.replace('.', '_')}_image_channel_{image_channel}"
            f"_capture_channel_{target_capture_channel}_{timestamp}"
        )

        self._log_step(
            f"开始执行 host={session.host} 图像配置通道={image_channel} 抓图通道={target_capture_channel}"
        )
        self._log_step(f"检查图像分析依赖 ffmpeg 路径={self.ffmpeg_path}")
        self._log_step(f"图像分析依赖可用 ffmpeg={self._resolve_ffmpeg()}")

        original_status = self.isapi.get_mixed_supplement_light_status(session, channel=image_channel)
        if not original_status.supported:
            raise HikvisionSDKError("设备通道不支持 SupplementLight", api_name=original_status.capability_path)

        tested_modes = tuple(mode for mode in TEST_LIGHT_MODES if mode in original_status.mode_options)
        if not tested_modes:
            raise HikvisionSDKError(
                "supplementLightMode 不支持 colorVuWhiteLight 或 irLight",
                api_name=original_status.capability_path,
            )
        manual_supported = "manual" in original_status.regulation_mode_options
        self._log_step(
            "能力检查通过 "
            f"能力接口={original_status.capability_path} 配置接口={original_status.config_path} "
            f"supplementLightMode选项={','.join(original_status.mode_options)} "
            f"待测试灯类型={','.join(tested_modes)} "
            f"亮度调节模式选项={','.join(original_status.regulation_mode_options) or '未返回'} "
            f"是否支持manual={manual_supported}"
        )
        if manual_supported:
            self._log_step("mixedLightBrightnessRegulatMode 支持 manual，后续配置统一设置为 manual")

        original_ircut_status = self.isapi.get_ircut_filter_status(session, image_channel)
        if not original_ircut_status.supported or "night" not in original_ircut_status.filter_type_options:
            raise HikvisionSDKError(
                "设备图像通道不支持将 IrcutFilterType 切换为 night",
                api_name=original_ircut_status.capability_path,
            )
        self._log_step(
            "日夜切换能力检查通过 "
            f"当前IrcutFilterType={original_ircut_status.filter_type} "
            f"支持选项={','.join(original_ircut_status.filter_type_options)}"
        )
        night_ircut_status = self.isapi.ensure_ircut_filter_type(session, image_channel, "night")
        self._log_step(
            f"已将日夜模式切换为 night changed={night_ircut_status.changed} "
            f"当前IrcutFilterType={night_ircut_status.filter_type}"
        )
        time.sleep(settle_seconds)

        capture_results: list[SupplementLightCaptureResult] = []
        function_results: list[SupplementLightFunctionResult] = []
        try:
            for light_mode in tested_modes:
                minimum, middle, maximum = self._brightness_points_for_mode(original_status, light_mode)
                before_mode = "close" if "close" in original_status.mode_options else light_mode
                before_brightness = minimum
                self._log_step(
                    f"开始功能有效性校验 灯类型={light_mode} "
                    f"开启前模式={before_mode} 开启后模式={light_mode} 开启后亮度={maximum}"
                )

                before_status = self.isapi.ensure_mixed_supplement_light_ready(
                    session,
                    image_channel,
                    mode=before_mode,
                    brightness=before_brightness if before_mode == light_mode else None,
                    prefer_manual=True,
                )
                time.sleep(settle_seconds)
                before_capture = self._capture(
                    session,
                    target_capture_channel,
                    base_dir / f"supplement_light_before_{light_mode}_{test_id}.jpg",
                    stream_type,
                )
                before_image_brightness = self._image_brightness(before_capture.file_path)

                after_status = self.isapi.ensure_mixed_supplement_light_ready(
                    session,
                    image_channel,
                    mode=light_mode,
                    brightness=maximum,
                    prefer_manual=True,
                )
                time.sleep(settle_seconds)
                after_capture = self._capture(
                    session,
                    target_capture_channel,
                    base_dir / f"supplement_light_after_{light_mode}_{maximum}_{test_id}.jpg",
                    stream_type,
                )
                after_image_brightness = self._image_brightness(after_capture.file_path)
                delta = after_image_brightness - before_image_brightness
                function_passed = delta >= on_threshold
                function_results.append(
                    SupplementLightFunctionResult(
                        mode=light_mode,
                        before_mode=before_mode,
                        before_brightness_limit=before_brightness,
                        before_image_brightness=before_image_brightness,
                        before_capture_result=before_capture,
                        after_brightness_limit=maximum,
                        after_image_brightness=after_image_brightness,
                        after_capture_result=after_capture,
                        brightness_delta=delta,
                        threshold=on_threshold,
                        passed=function_passed,
                    )
                )
                capture_results.extend(
                    (
                        SupplementLightCaptureResult(
                            before_mode, before_brightness, before_status, before_capture, before_image_brightness
                        ),
                        SupplementLightCaptureResult(
                            light_mode, maximum, after_status, after_capture, after_image_brightness
                        ),
                    )
                )
                self._log_step(
                    f"功能有效性结论 灯类型={light_mode} 开启前亮度={before_image_brightness:.2f} "
                    f"开启后亮度={after_image_brightness:.2f} 亮度差={delta:.2f} "
                    f"阈值={on_threshold:.2f} 是否有效={function_passed}"
                )

                self._log_step(
                    f"开始效果有效性校验 灯类型={light_mode} "
                    f"亮度测试点={minimum},{middle},{maximum}"
                )
                for brightness in (minimum, middle, maximum):
                    status = self.isapi.ensure_mixed_supplement_light_ready(
                        session,
                        image_channel,
                        mode=light_mode,
                        brightness=brightness,
                        prefer_manual=True,
                    )
                    time.sleep(settle_seconds)
                    capture = self._capture(
                        session,
                        target_capture_channel,
                        base_dir / f"supplement_light_{light_mode}_{brightness}_{test_id}.jpg",
                        stream_type,
                    )
                    image_brightness = self._image_brightness(capture.file_path)
                    capture_results.append(
                        SupplementLightCaptureResult(light_mode, brightness, status, capture, image_brightness)
                    )
                    self._log_step(
                        f"强度抓图分析完成 灯类型={light_mode} brightness={brightness} "
                        f"图像亮度={image_brightness:.2f} 抓图方式={capture.method} 路径={capture.file_path}"
                    )
        finally:
            with suppress(Exception):
                self.isapi.restore_mixed_supplement_light_status(session, image_channel, original_status)
                self._log_step(f"已恢复测试前补光灯配置 图像通道={image_channel}")
            if original_ircut_status.supported and original_ircut_status.filter_type:
                with suppress(Exception):
                    restored_ircut = self.isapi.ensure_ircut_filter_type(
                        session,
                        image_channel,
                        original_ircut_status.filter_type,
                    )
                    self._log_step(
                        f"已恢复测试前日夜模式 IrcutFilterType={restored_ircut.filter_type}"
                    )

        mode_results = self._analyze_modes(capture_results, original_status, tested_modes, level_threshold)
        function_pass = len(function_results) == len(tested_modes) and all(result.passed for result in function_results)
        effect_pass = len(mode_results) == len(tested_modes) and all(result.passed for result in mode_results)
        passed = function_pass and effect_pass

        for result in mode_results:
            self._log_step(
                f"效果有效性结论 灯类型={result.mode} "
                f"最小={result.min_limit}:{result.min_image_brightness:.2f} "
                f"中间={result.middle_limit}:{result.middle_image_brightness:.2f} "
                f"最大={result.max_limit}:{result.max_image_brightness:.2f} "
                f"最小到中间亮度差={result.min_to_middle_delta:.2f} "
                f"中间到最大亮度差={result.middle_to_max_delta:.2f} 是否有效={result.passed}"
            )
        self._log_step(
            f"通道最终结论 图像通道={image_channel} 功能有效={function_pass} "
            f"效果有效={effect_pass} 是否通过={passed}"
        )

        return SupplementLightTestResult(
            channel=image_channel,
            original_status=original_status,
            original_ircut_status=original_ircut_status,
            night_ircut_status=night_ircut_status,
            tested_modes=tested_modes,
            function_results=tuple(function_results),
            capture_results=tuple(capture_results),
            mode_results=mode_results,
            function_pass=function_pass,
            effect_pass=effect_pass,
            passed=passed,
            on_threshold=on_threshold,
            level_threshold=level_threshold,
        )

    def _analyze_modes(
        self,
        capture_results: list[SupplementLightCaptureResult],
        status: MixedSupplementLightStatus,
        modes: tuple[str, ...],
        threshold: float,
    ) -> tuple[SupplementLightModeResult, ...]:
        """
        作用：分析输入数据，计算特征、分数或判定结果。
        执行步骤：
        1. 读取待分析的输入数据。
        2. 计算统计量、特征或匹配分数。
        3. 返回分析结论供用例判定。
        """
        results: list[SupplementLightModeResult] = []
        for mode in modes:
            minimum, middle, maximum = self._brightness_points_for_mode(status, mode)
            by_brightness = {
                item.brightness_limit: item.image_brightness for item in capture_results if item.mode == mode
            }
            if not all(point in by_brightness for point in (minimum, middle, maximum)):
                continue
            min_value = by_brightness[minimum]
            middle_value = by_brightness[middle]
            max_value = by_brightness[maximum]
            first_delta = middle_value - min_value
            second_delta = max_value - middle_value
            results.append(
                SupplementLightModeResult(
                    mode=mode,
                    min_limit=minimum,
                    middle_limit=middle,
                    max_limit=maximum,
                    min_image_brightness=min_value,
                    middle_image_brightness=middle_value,
                    max_image_brightness=max_value,
                    min_to_middle_delta=first_delta,
                    middle_to_max_delta=second_delta,
                    passed=first_delta >= threshold and second_delta >= threshold,
                )
            )
        return tuple(results)

    def _brightness_points_for_mode(
        self,
        status: MixedSupplementLightStatus,
        mode: str,
    ) -> tuple[int, int, int]:
        """
        作用：作为内部辅助方法，完成本方法对应的数据处理。
        执行步骤：
        1. 接收并校验输入参数。
        2. 执行方法职责对应的核心处理。
        3. 返回处理结果，失败时抛出异常。
        """
        if mode == "colorVuWhiteLight":
            minimum, maximum = status.white_brightness_min, status.white_brightness_max
        else:
            minimum, maximum = status.ir_brightness_min, status.ir_brightness_max
        return minimum, (minimum + maximum) // 2, maximum

    def _capture(
        self,
        session: DeviceSession,
        channel: int,
        file_path: Path,
        stream_type: int,
    ) -> CapturePictureResult:
        """
        作用：执行抓图流程，保存图片并返回抓图结果。
        执行步骤：
        1. 接收并校验输入参数。
        2. 执行方法职责对应的核心处理。
        3. 返回处理结果，失败时抛出异常。
        """
        file_path.parent.mkdir(parents=True, exist_ok=True)
        return self.sdk.capture_picture(session, file_path, channel=channel, stream_type=stream_type)

    def _image_brightness(self, image_path: Path) -> float:
        """
        作用：分析输入数据，计算特征、分数或判定结果。
        执行步骤：
        1. 读取待分析的输入数据。
        2. 计算统计量、特征或匹配分数。
        3. 返回分析结论供用例判定。
        """
        command = [
            self._resolve_ffmpeg(),
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
                "图像亮度分析失败",
                api_name="ffmpeg",
                error_message=result.stderr.decode("utf-8", errors="ignore").strip(),
            )
        if not result.stdout:
            raise HikvisionSDKError("图像亮度分析返回空数据", api_name="ffmpeg")
        return sum(result.stdout) / len(result.stdout)

    def _resolve_ffmpeg(self) -> str:
        """
        作用：读取配置、设备或运行状态，并转换为结构化结果。
        执行步骤：
        1. 读取输入参数、配置或设备响应。
        2. 解析并校验目标字段。
        3. 返回解析后的结构化结果。
        """
        try:
            return resolve_ffmpeg_path(self.ffmpeg_path)
        except FileNotFoundError as exc:
            raise HikvisionSDKError(f"未找到 ffmpeg: {exc}", api_name="ffmpeg") from exc

    def _default_output_dir(self, host: str) -> Path:
        """
        作用：作为内部辅助方法，完成本方法对应的数据处理。
        执行步骤：
        1. 接收并校验输入参数。
        2. 执行方法职责对应的核心处理。
        3. 返回处理结果，失败时抛出异常。
        """
        host_dir = "".join(char if char.isalnum() or char in "._-" else "_" for char in host)
        return Path.cwd() / "recordings" / "supplement_light_tests" / host_dir
