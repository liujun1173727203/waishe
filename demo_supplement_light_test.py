from __future__ import annotations

import argparse
import sys
from datetime import datetime
from pathlib import Path

from hikvision_isapi import HikvisionIsapiClient
from hikvision_voice import HikvisionSDKError, HikvisionVoiceSDK, STREAM_TYPE_MAIN, STREAM_TYPE_SUB
from use_cases import SupplementLightUseCases


class TimestampTee:
    def __init__(self, stream, log_file) -> None:
        """
        作用：初始化对象实例，保存后续执行所需的依赖、配置或运行状态。
        执行步骤：
        1. 接收并校验输入参数。
        2. 执行方法职责对应的核心处理。
        3. 返回处理结果，失败时抛出异常。
        """
        self._stream = stream
        self._log_file = log_file
        self._buffer = ""

    def write(self, data: str) -> int:
        """
        作用：执行本方法对应的业务处理。
        执行步骤：
        1. 接收并校验输入参数。
        2. 执行方法职责对应的核心处理。
        3. 返回处理结果，失败时抛出异常。
        """
        if not data:
            return 0
        self._buffer += data
        while "\n" in self._buffer:
            line, self._buffer = self._buffer.split("\n", 1)
            self._write_line(line, newline=True)
        return len(data)

    def flush(self) -> None:
        """
        作用：执行本方法对应的业务处理。
        执行步骤：
        1. 接收并校验输入参数。
        2. 执行方法职责对应的核心处理。
        3. 返回处理结果，失败时抛出异常。
        """
        if self._buffer:
            self._write_line(self._buffer, newline=False)
            self._buffer = ""
        self._stream.flush()
        self._log_file.flush()

    @property
    def encoding(self):
        """
        作用：执行本方法对应的业务处理。
        执行步骤：
        1. 接收并校验输入参数。
        2. 执行方法职责对应的核心处理。
        3. 返回处理结果，失败时抛出异常。
        """
        return getattr(self._stream, "encoding", None)

    def _write_line(self, line: str, newline: bool) -> None:
        """
        作用：作为内部辅助方法，完成本方法对应的数据处理。
        执行步骤：
        1. 接收并校验输入参数。
        2. 执行方法职责对应的核心处理。
        3. 返回处理结果，失败时抛出异常。
        """
        timestamped = f"[{datetime.now():%Y-%m-%d %H:%M:%S}] {line}"
        suffix = "\n" if newline else ""
        self._stream.write(timestamped + suffix)
        self._log_file.write(timestamped + suffix)


def _default_output_dir(host: str) -> Path:
    """
    作用：作为内部辅助方法，完成本方法对应的数据处理。
    执行步骤：
    1. 接收并校验输入参数。
    2. 执行方法职责对应的核心处理。
    3. 返回处理结果，失败时抛出异常。
    """
    host_dir = "".join(char if char.isalnum() or char in "._-" else "_" for char in host)
    return Path.cwd() / "recordings" / "supplement_light_tests" / host_dir


def _print_result(result) -> None:
    """
    作用：作为内部辅助方法，完成本方法对应的数据处理。
    执行步骤：
    1. 接收并校验输入参数。
    2. 执行方法职责对应的核心处理。
    3. 返回处理结果，失败时抛出异常。
    """
    print(
        "补光灯通道测试完成:",
        f"图像通道={result.channel}",
        f"是否通过={result.passed}",
        f"功能有效={result.function_pass}",
        f"效果有效={result.effect_pass}",
        f"测试灯类型={','.join(result.tested_modes)}",
        f"测试前日夜模式={result.original_ircut_status.filter_type}",
        f"测试日夜模式={result.night_ircut_status.filter_type}",
        f"模式数量={len(result.mode_results)}",
        f"抓图数量={len(result.capture_results)}",
        f"开启阈值={result.on_threshold:.2f}",
        f"强度递增阈值={result.level_threshold:.2f}",
    )
    for function_result in result.function_results:
        print(
            "补光灯功能有效性结果:",
            f"图像通道={result.channel}",
            f"灯类型={function_result.mode}",
            f"开启前mode={function_result.before_mode}",
            f"开启前brightness={function_result.before_brightness_limit}",
            f"开启前亮度={function_result.before_image_brightness:.2f}",
            f"开启后brightness={function_result.after_brightness_limit}",
            f"开启后亮度={function_result.after_image_brightness:.2f}",
            f"亮度差={function_result.brightness_delta:.2f}",
            f"阈值={function_result.threshold:.2f}",
            f"是否通过={function_result.passed}",
        )
    for mode_result in result.mode_results:
        print(
            "补光灯模式结果:",
            f"图像通道={result.channel}",
            f"mode={mode_result.mode}",
            f"最小值={mode_result.min_limit}:{mode_result.min_image_brightness:.2f}",
            f"中间值={mode_result.middle_limit}:{mode_result.middle_image_brightness:.2f}",
            f"最大值={mode_result.max_limit}:{mode_result.max_image_brightness:.2f}",
            f"最小到中间亮度差={mode_result.min_to_middle_delta:.2f}",
            f"中间到最大亮度差={mode_result.middle_to_max_delta:.2f}",
            f"是否通过={mode_result.passed}",
        )


def main() -> int:
    """
    作用：作为命令行入口，解析参数并编排完整执行流程。
    执行步骤：
    1. 解析输入参数并准备依赖对象。
    2. 按业务流程顺序执行核心步骤。
    3. 输出日志、执行结果或退出码。
    """
    parser = argparse.ArgumentParser(description="补光灯功能和效果自动化测试")
    parser.add_argument("--host", default="10.41.203.66", help="设备 IP 或主机名")
    parser.add_argument("--port", type=int, default=8000, help="SDK 端口")
    parser.add_argument("--username", default="admin", help="设备用户名")
    parser.add_argument("--password", default="asdf!234", help="设备密码")
    parser.add_argument("--channel", type=int, default=0, help="图像通道，0 表示自动遍历全部支持的图像通道")
    parser.add_argument("--capture-channel", type=int, default=0, help="SDK 抓图通道，0 表示使用当前遍历的图像通道")
    parser.add_argument("--stream-type", choices=["main", "sub"], default="main", help="取流抓图使用的码流类型")
    parser.add_argument("--settle-seconds", type=float, default=2.0, help="每次修改补光灯配置后的等待时间")
    parser.add_argument("--on-threshold", type=float, default=10.0, help="功能有效性亮度差阈值")
    parser.add_argument("--level-threshold", type=float, default=5.0, help="效果有效性相邻强度亮度差阈值")
    parser.add_argument(
        "--ffmpeg-path",
        default="ffmpeg",
        help="ffmpeg 可执行文件路径",
    )
    parser.add_argument("--enable-log", action="store_true", help="开启 SDK 日志")
    args = parser.parse_args()

    base_dir = _default_output_dir(args.host)
    base_dir.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    log_file_path = base_dir / f"supplement_light_test_log_{args.host.replace('.', '_')}_{timestamp}.log"

    original_stdout = sys.stdout
    original_stderr = sys.stderr
    log_file = log_file_path.open("a", encoding="utf-8")
    sys.stdout = TimestampTee(original_stdout, log_file)
    sys.stderr = TimestampTee(original_stderr, log_file)

    sdk = HikvisionVoiceSDK()
    session = None
    try:
        print(f"补光灯测试日志文件: {log_file_path}")
        sdk.initialize(enable_log=args.enable_log)
        session = sdk.login(args.host, args.port, args.username, args.password)
        isapi = HikvisionIsapiClient(sdk)
        use_cases = SupplementLightUseCases(sdk=sdk, isapi=isapi, ffmpeg_path=args.ffmpeg_path)
        stream_type = STREAM_TYPE_MAIN if args.stream_type == "main" else STREAM_TYPE_SUB
        if args.channel:
            channel_ids = (args.channel,)
            channel_source = "命令行指定"
            channel_path = "--channel"
        else:
            channel_status = isapi.get_supported_image_channel_ids(session)
            channel_ids = channel_status.channel_ids
            channel_source = channel_status.source
            channel_path = channel_status.request_path
        print(
            "补光灯图像通道列表:",
            f"通道={','.join(str(channel) for channel in channel_ids)}",
            f"来源={channel_source}",
            f"接口={channel_path}",
            f"固定抓图通道={args.capture_channel or '未指定，使用当前图像通道'}",
        )

        results = []
        failed_channels: list[int] = []
        for channel_id in channel_ids:
            capture_channel = args.capture_channel or channel_id
            print(f"开始补光灯通道测试: 图像通道={channel_id} 抓图通道={capture_channel}")
            try:
                result = use_cases.run_supplement_light_test(
                    session=session,
                    channel=channel_id,
                    capture_channel=capture_channel,
                    output_dir=base_dir,
                    settle_seconds=args.settle_seconds,
                    on_threshold=args.on_threshold,
                    level_threshold=args.level_threshold,
                    stream_type=stream_type,
                )
                results.append(result)
                _print_result(result)
            except (HikvisionSDKError, FileNotFoundError, ValueError) as exc:
                failed_channels.append(channel_id)
                print(f"补光灯通道测试失败: 图像通道={channel_id} 异常={exc}", file=sys.stderr)

        passed = bool(results) and not failed_channels and all(result.passed for result in results)
        print(
            "补光灯全部通道最终结论:",
            f"是否通过={passed}",
            f"成功通道={','.join(str(result.channel) for result in results) or '无'}",
            f"失败通道={','.join(str(channel) for channel in failed_channels) or '无'}",
        )
        return 0 if passed else 1
    except (HikvisionSDKError, FileNotFoundError, ValueError) as exc:
        print(f"用例执行失败: {exc}", file=sys.stderr)
        return 1
    finally:
        if session is not None:
            sdk.logout(session)
        sdk.cleanup()
        sys.stdout.flush()
        sys.stderr.flush()
        sys.stdout = original_stdout
        sys.stderr = original_stderr
        log_file.close()


if __name__ == "__main__":
    sys.exit(main())
