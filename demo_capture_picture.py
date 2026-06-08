from __future__ import annotations

import argparse
import sys
from datetime import datetime
from pathlib import Path

from hikvision_voice import HikvisionSDKError, HikvisionVoiceSDK, STREAM_TYPE_MAIN, STREAM_TYPE_SUB


def _default_output_path(host: str, channel: int) -> Path:
    """
    作用：作为内部辅助方法，完成本方法对应的数据处理。
    执行步骤：
    1. 接收并校验输入参数。
    2. 执行方法职责对应的核心处理。
    3. 返回处理结果，失败时抛出异常。
    """
    host_dir = "".join(char if char.isalnum() or char in "._-" else "_" for char in host)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    return Path.cwd() / "recordings" / "captures" / host_dir / f"capture_ch{channel}_{timestamp}.jpg"


def main() -> int:
    """
    作用：作为命令行入口，解析参数并编排完整执行流程。
    执行步骤：
    1. 解析输入参数并准备依赖对象。
    2. 按业务流程顺序执行核心步骤。
    3. 输出日志、执行结果或退出码。
    """
    parser = argparse.ArgumentParser(description="Capture picture with JPEG-first fallback-to-stream strategy")
    parser.add_argument("--host", default="10.18.117.22", help="device ip or hostname")
    parser.add_argument("--port", type=int, default=8000, help="sdk port")
    parser.add_argument("--username", default="admin", help="device username")
    parser.add_argument("--password", default="asdf!234", help="device password")
    parser.add_argument("--channel", type=int, default=0, help="preview channel, 0 means auto")
    parser.add_argument("--stream-type", choices=["main", "sub"], default="main", help="stream type for stream fallback")
    parser.add_argument("--output", default="", help="output path, default recordings/captures/<host>/capture_*.jpg")
    parser.add_argument("--mode", choices=["auto", "jpeg", "stream"], default="auto", help="auto defaults to JPEG then stream fallback")
    parser.add_argument("--jpeg-picture-size", type=lambda value: int(value, 0), default=0xFF, help="NET_DVR_JPEGPARA.wPicSize")
    parser.add_argument("--jpeg-quality", type=int, default=0, help="NET_DVR_JPEGPARA.wPicQuality, 0 is best")
    parser.add_argument("--enable-log", action="store_true", help="enable sdk log output")
    args = parser.parse_args()

    sdk = HikvisionVoiceSDK()
    session = None
    try:
        sdk.initialize(enable_log=args.enable_log)
        session = sdk.login(args.host, args.port, args.username, args.password)
        channel = args.channel or session.default_preview_channel
        output_path = Path(args.output) if args.output else _default_output_path(args.host, channel)
        stream_type = STREAM_TYPE_MAIN if args.stream_type == "main" else STREAM_TYPE_SUB

        if args.mode == "jpeg":
            path = sdk.capture_jpeg_picture(
                session=session,
                file_path=output_path,
                channel=channel,
                picture_size=args.jpeg_picture_size,
                quality=args.jpeg_quality,
            )
            print(f"capture done: method=jpeg fallback=False path={path}")
        elif args.mode == "stream":
            path = sdk.capture_stream_picture(
                session=session,
                file_path=output_path,
                channel=channel,
                stream_type=stream_type,
            )
            print(f"capture done: method=stream fallback=False path={path}")
        else:
            result = sdk.capture_picture(
                session=session,
                file_path=output_path,
                channel=channel,
                stream_type=stream_type,
                jpeg_picture_size=args.jpeg_picture_size,
                jpeg_quality=args.jpeg_quality,
            )
            print(
                "capture done:",
                f"method={result.method}",
                f"fallback={result.fallback_used}",
                f"path={result.file_path}",
            )
            if result.jpeg_error is not None:
                print(f"jpeg capture failed, fallback reason: {result.jpeg_error}")
    except (HikvisionSDKError, FileNotFoundError, ValueError) as exc:
        print(f"capture failed: {exc}", file=sys.stderr)
        return 1
    finally:
        if session is not None:
            sdk.logout(session)
        sdk.cleanup()
    return 0


if __name__ == "__main__":
    sys.exit(main())
