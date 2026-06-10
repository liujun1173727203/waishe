from __future__ import annotations

import argparse
import asyncio
import locale
import mimetypes
import re
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

from testcase_api import ROOT_DIR, build_command_for_test_case, get_test_case, list_test_cases, serialize_test_case


MAX_CONCURRENT_TASKS_PER_RECORDER_IP = 5
ARTIFACT_SCAN_DIRS = (ROOT_DIR / "recordings", ROOT_DIR / "SdkLog")


class TaskSubmitRequest(BaseModel):
    arguments: dict[str, Any] = Field(default_factory=dict)
    recorder_device_ip: str | None = None
    timeout_seconds: float | None = Field(default=None, gt=0)


@dataclass
class TaskAttachment:
    attachment_id: str
    path: Path
    category: str
    size_bytes: int
    modified_at: float
    media_type: str

    def to_dict(self, task_id: str) -> dict[str, Any]:
        try:
            relative_path = str(self.path.relative_to(ROOT_DIR))
        except ValueError:
            relative_path = str(self.path)
        return {
            "attachment_id": self.attachment_id,
            "name": self.path.name,
            "path": str(self.path),
            "relative_path": relative_path,
            "category": self.category,
            "size_bytes": self.size_bytes,
            "modified_at": self.modified_at,
            "media_type": self.media_type,
            "download_url": f"/api/tasks/{task_id}/attachments/{self.attachment_id}",
        }


@dataclass
class TaskRecord:
    task_id: str
    case_id: str
    arguments: dict[str, Any]
    command: list[str]
    recorder_device_ip: str | None
    timeout_seconds: float | None
    status: str = "pending"
    created_at: float = field(default_factory=time.time)
    started_at: float | None = None
    finished_at: float | None = None
    success: bool | None = None
    exit_code: int | None = None
    stdout: str = ""
    stderr: str = ""
    error: str | None = None
    result: dict[str, Any] = field(default_factory=dict)
    execution_log: str = ""
    log_file_path: str | None = None
    attachments: list[TaskAttachment] = field(default_factory=list)
    scan_snapshot_before: dict[str, tuple[int, float]] = field(default_factory=dict)

    def to_dict(self, *, summary: bool = False, task_status: dict[str, Any] | None = None) -> dict[str, Any]:
        payload = {
            "task_id": self.task_id,
            "case_id": self.case_id,
            "arguments": self.arguments,
            "command": self.command,
            "recorder_device_ip": self.recorder_device_ip,
            "timeout_seconds": self.timeout_seconds,
            "status": self.status,
            "task_status": task_status or {"phase": self.status},
            "created_at": self.created_at,
            "started_at": self.started_at,
            "finished_at": self.finished_at,
            "duration_seconds": _duration_seconds(self.started_at, self.finished_at),
            "success": self.success,
            "exit_code": self.exit_code,
            "error": self.error,
            "result": self.result,
            "log_file_path": self.log_file_path,
            "attachment_count": len(self.attachments),
        }
        if summary:
            return payload
        payload.update(
            {
                "stdout": self.stdout,
                "stderr": self.stderr,
                "execution_log": self.execution_log,
                "attachments": [attachment.to_dict(self.task_id) for attachment in self.attachments],
            }
        )
        return payload


class AsyncTaskRegistry:
    def __init__(self) -> None:
        self._lock = asyncio.Lock()
        self._condition = asyncio.Condition(self._lock)
        self._tasks: dict[str, TaskRecord] = {}
        self._active_recorder_ip_counts: dict[str, int] = {}

    async def create_task(
        self,
        case_id: str,
        arguments: dict[str, Any],
        recorder_device_ip: str | None,
        timeout_seconds: float | None,
    ) -> TaskRecord:
        normalized_ip = _normalize_recorder_ip(recorder_device_ip, case_id, arguments)
        command = build_command_for_test_case(case_id, arguments)
        record = TaskRecord(
            task_id=uuid.uuid4().hex,
            case_id=case_id,
            arguments=arguments,
            command=command,
            recorder_device_ip=normalized_ip,
            timeout_seconds=timeout_seconds,
            scan_snapshot_before=_take_artifact_snapshot(),
        )

        async with self._lock:
            self._tasks[record.task_id] = record

        asyncio.create_task(self._run_task(record.task_id))
        return record

    async def list_tasks(self) -> list[dict[str, Any]]:
        async with self._lock:
            return [
                record.to_dict(summary=True, task_status=self._build_task_status_locked(record))
                for record in self._tasks.values()
            ]

    async def list_tasks_grouped(self) -> dict[str, list[dict[str, Any]]]:
        async with self._lock:
            summaries = [
                record.to_dict(summary=True, task_status=self._build_task_status_locked(record))
                for record in self._tasks.values()
            ]

        summaries.sort(key=lambda item: (item.get("created_at") or 0, item.get("task_id") or ""))
        grouped = {
            "queued": [],
            "running": [],
            "finished": [],
        }
        for task in summaries:
            task_status = task.get("task_status", {})
            if task_status.get("is_queued"):
                grouped["queued"].append(task)
            elif task_status.get("is_running"):
                grouped["running"].append(task)
            else:
                grouped["finished"].append(task)
        return grouped

    async def get_task(self, task_id: str) -> dict[str, Any]:
        async with self._lock:
            record = self._tasks.get(task_id)
            if record is None:
                raise KeyError(f"task not found: {task_id}")
            return record.to_dict(summary=False, task_status=self._build_task_status_locked(record))

    async def get_attachment(self, task_id: str, attachment_id: str) -> TaskAttachment:
        async with self._lock:
            record = self._tasks.get(task_id)
            if record is None:
                raise KeyError(f"task not found: {task_id}")
            for attachment in record.attachments:
                if attachment.attachment_id == attachment_id:
                    return attachment
        raise KeyError(f"attachment not found: {attachment_id}")

    async def active_recorder_ips(self) -> dict[str, int]:
        async with self._lock:
            return dict(self._active_recorder_ip_counts)

    async def _run_task(self, task_id: str) -> None:
        async with self._lock:
            record = self._tasks[task_id]

        await self._acquire_recorder_slot(task_id)

        try:
            process = await asyncio.create_subprocess_exec(
                *record.command,
                cwd=str(ROOT_DIR),
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            try:
                stdout_bytes, stderr_bytes = await asyncio.wait_for(
                    process.communicate(),
                    timeout=record.timeout_seconds,
                )
            except asyncio.TimeoutError:
                process.kill()
                stdout_bytes, stderr_bytes = await process.communicate()
                async with self._lock:
                    record.stdout = _decode_process_output(stdout_bytes)
                    record.stderr = _decode_process_output(stderr_bytes)
                    record.status = "timeout"
                    record.success = False
                    record.exit_code = None
                    record.error = f"task timed out after {record.timeout_seconds}s"
                    record.finished_at = time.time()
                    _finalize_task_record(record)
                return

            async with self._lock:
                record.stdout = _decode_process_output(stdout_bytes)
                record.stderr = _decode_process_output(stderr_bytes)
                record.exit_code = process.returncode
                record.success = process.returncode == 0
                record.status = "succeeded" if process.returncode == 0 else "failed"
                record.finished_at = time.time()
                _finalize_task_record(record)
        except Exception as exc:
            async with self._lock:
                record.status = "failed"
                record.success = False
                record.error = str(exc)
                record.finished_at = time.time()
                _finalize_task_record(record)
        finally:
            if record.recorder_device_ip:
                await self._release_recorder_slot(record.recorder_device_ip)

    def _release_recorder_ip_locked(self, recorder_device_ip: str) -> None:
        current_count = self._active_recorder_ip_counts.get(recorder_device_ip, 0)
        if current_count <= 1:
            self._active_recorder_ip_counts.pop(recorder_device_ip, None)
            return
        self._active_recorder_ip_counts[recorder_device_ip] = current_count - 1

    async def _acquire_recorder_slot(self, task_id: str) -> None:
        async with self._condition:
            record = self._tasks[task_id]
            if not record.recorder_device_ip:
                record.status = "running"
                record.started_at = time.time()
                return

            recorder_device_ip = record.recorder_device_ip
            await self._condition.wait_for(
                lambda: self._active_recorder_ip_counts.get(recorder_device_ip, 0) < MAX_CONCURRENT_TASKS_PER_RECORDER_IP
            )
            self._active_recorder_ip_counts[recorder_device_ip] = (
                self._active_recorder_ip_counts.get(recorder_device_ip, 0) + 1
            )
            record.status = "running"
            record.started_at = time.time()

    async def _release_recorder_slot(self, recorder_device_ip: str) -> None:
        async with self._condition:
            self._release_recorder_ip_locked(recorder_device_ip)
            self._condition.notify_all()

    def _build_task_status_locked(self, record: TaskRecord) -> dict[str, Any]:
        recorder_device_ip = record.recorder_device_ip
        active_count = self._active_recorder_ip_counts.get(recorder_device_ip or "", 0) if recorder_device_ip else 0
        queue_position = self._queue_position_locked(record)
        return {
            "phase": record.status,
            "display": _display_status(record.status, queue_position),
            "is_terminal": record.status in {"succeeded", "failed", "timeout"},
            "is_running": record.status == "running",
            "is_queued": record.status == "pending" and queue_position is not None,
            "queue_position": queue_position,
            "recorder_device_ip": recorder_device_ip,
            "current_active_count_for_recorder_ip": active_count,
            "max_concurrent_tasks_per_recorder_ip": MAX_CONCURRENT_TASKS_PER_RECORDER_IP,
        }

    def _queue_position_locked(self, record: TaskRecord) -> int | None:
        if record.status != "pending" or not record.recorder_device_ip:
            return None

        pending_records = [
            task
            for task in self._tasks.values()
            if task.recorder_device_ip == record.recorder_device_ip and task.status == "pending"
        ]
        pending_records.sort(key=lambda task: (task.created_at, task.task_id))
        for index, pending_record in enumerate(pending_records, start=1):
            if pending_record.task_id == record.task_id:
                return index
        return None


def create_app() -> FastAPI:
    app = FastAPI(
        title="Hikvision Test Case API",
        version="2.0.0",
        description="Use FastAPI to expose each test case as an asynchronous HTTP task endpoint.",
    )
    registry = AsyncTaskRegistry()

    @app.get("/health")
    async def health() -> dict[str, Any]:
        return {"success": True, "status": "ok", "framework": "fastapi"}

    @app.get("/api/testcases")
    async def get_testcases() -> dict[str, Any]:
        testcases = list_test_cases()
        return {"success": True, "count": len(testcases), "testcases": testcases}

    @app.get("/api/testcases/{case_id}")
    async def get_testcase(case_id: str) -> dict[str, Any]:
        try:
            spec = get_test_case(case_id)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        return {"success": True, "testcase": serialize_test_case(spec)}

    @app.get("/api/tasks")
    async def get_tasks() -> dict[str, Any]:
        tasks = await registry.list_tasks()
        grouped_tasks = await registry.list_tasks_grouped()
        return {
            "success": True,
            "tasks": tasks,
            "grouped_tasks": grouped_tasks,
            "counts": {
                "total": len(tasks),
                "queued": len(grouped_tasks["queued"]),
                "running": len(grouped_tasks["running"]),
                "finished": len(grouped_tasks["finished"]),
            },
        }

    @app.get("/api/tasks/{task_id}")
    async def get_task(task_id: str) -> dict[str, Any]:
        try:
            task = await registry.get_task(task_id)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        return {"success": True, "task": task}

    @app.get("/api/tasks/{task_id}/attachments/{attachment_id}")
    async def download_attachment(task_id: str, attachment_id: str) -> FileResponse:
        try:
            attachment = await registry.get_attachment(task_id, attachment_id)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        if not attachment.path.exists():
            raise HTTPException(status_code=404, detail=f"attachment file not found: {attachment.path}")
        return FileResponse(
            path=str(attachment.path),
            media_type=attachment.media_type,
            filename=attachment.path.name,
        )

    @app.get("/api/recorder-devices/active")
    async def get_active_recorder_devices() -> dict[str, Any]:
        active = await registry.active_recorder_ips()
        return {
            "success": True,
            "limit_per_recorder_device_ip": MAX_CONCURRENT_TASKS_PER_RECORDER_IP,
            "active_recorder_device_ips": active,
            "active_recorder_device_ip_count": len(active),
        }

    for testcase in list_test_cases():
        _add_submit_route(app, registry, testcase["case_id"])

    return app


def _add_submit_route(app: FastAPI, registry: AsyncTaskRegistry, case_id: str) -> None:
    async def submit_task(request: TaskSubmitRequest) -> dict[str, Any]:
        try:
            task = await registry.create_task(
                case_id=case_id,
                arguments=request.arguments,
                recorder_device_ip=request.recorder_device_ip,
                timeout_seconds=request.timeout_seconds,
            )
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

        return {
            "success": True,
            "message": "task accepted",
            "task_id": task.task_id,
            "case_id": case_id,
            "status": task.status,
            "recorder_device_ip": task.recorder_device_ip,
            "queue_policy": f"max {MAX_CONCURRENT_TASKS_PER_RECORDER_IP} concurrent tasks per recorder device ip; extra tasks stay pending",
            "task_status_url": f"/api/tasks/{task.task_id}",
        }

    submit_task.__name__ = f"submit_{case_id}_task"
    app.add_api_route(
        f"/api/testcases/{case_id}/run",
        submit_task,
        methods=["POST"],
        status_code=202,
        summary=f"Submit async task for {case_id}",
    )


def _normalize_recorder_ip(recorder_device_ip: str | None, case_id: str, arguments: dict[str, Any]) -> str | None:
    if recorder_device_ip:
        normalized = recorder_device_ip.strip()
        return normalized or None

    if case_id in {"stream_record", "composite_stream_record", "pickup_test", "linein_test"}:
        host = arguments.get("host")
        if host:
            return str(host).strip() or None
    return None


def _duration_seconds(started_at: float | None, finished_at: float | None) -> float | None:
    if started_at is None or finished_at is None:
        return None
    return round(finished_at - started_at, 3)


def _display_status(status: str, queue_position: int | None) -> str:
    if status == "pending" and queue_position is not None:
        return f"queued (position {queue_position})"
    if status == "pending":
        return "pending"
    if status == "running":
        return "running"
    if status == "succeeded":
        return "succeeded"
    if status == "failed":
        return "failed"
    if status == "timeout":
        return "timeout"
    return status


def _finalize_task_record(record: TaskRecord) -> None:
    parsed_paths = _extract_paths_from_text(record.stdout, record.stderr)
    attachments = _collect_attachments(record.scan_snapshot_before, parsed_paths)
    record.attachments = attachments
    record.log_file_path = _select_log_file_path(attachments)
    record.execution_log = _build_execution_log(record, attachments)
    record.result = _build_result_summary(record, attachments)


def _take_artifact_snapshot() -> dict[str, tuple[int, float]]:
    snapshot: dict[str, tuple[int, float]] = {}
    for scan_dir in ARTIFACT_SCAN_DIRS:
        if not scan_dir.exists():
            continue
        for path in scan_dir.rglob("*"):
            if not path.is_file():
                continue
            stat = path.stat()
            snapshot[str(path.resolve())] = (stat.st_size, stat.st_mtime)
    return snapshot


def _collect_attachments(
    snapshot_before: dict[str, tuple[int, float]],
    parsed_paths: list[Path],
) -> list[TaskAttachment]:
    attachments_by_path: dict[str, TaskAttachment] = {}
    snapshot_after = _take_artifact_snapshot()

    for path_str, state in snapshot_after.items():
        previous_state = snapshot_before.get(path_str)
        if previous_state != state:
            path = Path(path_str)
            attachment = _build_attachment(path)
            attachments_by_path[path_str] = attachment

    for path in parsed_paths:
        resolved = path.resolve()
        if not resolved.exists():
            continue
        if resolved.is_dir():
            for child in resolved.rglob("*"):
                if not child.is_file():
                    continue
                path_key = str(child.resolve())
                if path_key not in attachments_by_path:
                    attachments_by_path[path_key] = _build_attachment(child.resolve())
            continue
        path_key = str(resolved)
        if path_key not in attachments_by_path:
            attachments_by_path[path_key] = _build_attachment(resolved)

    attachments = list(attachments_by_path.values())
    attachments.sort(key=lambda item: (item.category, item.modified_at, item.path.name))
    return attachments


def _build_attachment(path: Path) -> TaskAttachment:
    stat = path.stat()
    media_type = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
    return TaskAttachment(
        attachment_id=uuid.uuid4().hex,
        path=path,
        category=_classify_attachment(path),
        size_bytes=stat.st_size,
        modified_at=stat.st_mtime,
        media_type=media_type,
    )


def _classify_attachment(path: Path) -> str:
    suffix = path.suffix.lower()
    if suffix in {".log", ".txt"}:
        return "log"
    if suffix in {".jpg", ".jpeg", ".png", ".bmp"}:
        return "image"
    if suffix in {".mp4", ".avi", ".mov"}:
        return "video"
    if suffix in {".wav", ".mp3", ".aac"}:
        return "audio"
    return "file"


def _extract_paths_from_text(*texts: str) -> list[Path]:
    candidates: list[Path] = []
    seen: set[str] = set()
    for text in texts:
        for line in text.splitlines():
            line = line.strip()
            if not line:
                continue
            for prefix in (
                "speaker test log file:",
                "pickup test log file:",
                "output_dir=",
                "device_a_recording=",
            ):
                if line.startswith(prefix):
                    _append_path_candidate(candidates, seen, line[len(prefix):].strip())
            for key in ("record", "reference", "callback_audio", "audio", "output", "file", "path", "wav"):
                for token in re.findall(rf"{re.escape(key)}=([^\s]+)", line):
                    _append_path_candidate(candidates, seen, token)
    return candidates


def _append_path_candidate(candidates: list[Path], seen: set[str], raw_value: str) -> None:
    candidate = raw_value.strip().strip(",").strip("'\"")
    if not candidate:
        return
    path = Path(candidate)
    if not path.is_absolute():
        path = (ROOT_DIR / path).resolve()
    else:
        path = path.resolve()
    path_key = str(path)
    if path_key in seen:
        return
    seen.add(path_key)
    candidates.append(path)


def _select_log_file_path(attachments: list[TaskAttachment]) -> str | None:
    for attachment in attachments:
        if attachment.category == "log":
            return str(attachment.path)
    return None


def _build_execution_log(record: TaskRecord, attachments: list[TaskAttachment]) -> str:
    for attachment in attachments:
        if attachment.category != "log":
            continue
        try:
            return _read_text_with_fallback(attachment.path)
        except OSError:
            continue
    parts = []
    if record.stdout:
        parts.append(record.stdout)
    if record.stderr:
        parts.append(record.stderr)
    return "\n".join(parts)


def _build_result_summary(record: TaskRecord, attachments: list[TaskAttachment]) -> dict[str, Any]:
    parsed_kv = _extract_key_values_from_text(record.stdout)
    return {
        "status": record.status,
        "success": record.success,
        "exit_code": record.exit_code,
        "error": record.error,
        "parsed_result": parsed_kv,
        "attachments_by_category": _count_attachments_by_category(attachments),
    }


def _extract_key_values_from_text(text: str) -> dict[str, str]:
    result: dict[str, str] = {}
    for line in reversed(text.splitlines()):
        pairs = re.findall(r"([A-Za-z_][A-Za-z0-9_]*)=([^\s]+)", line)
        if not pairs:
            continue
        for key, value in pairs:
            result[key] = value.strip().strip(",")
        if result:
            break
    return result


def _count_attachments_by_category(attachments: list[TaskAttachment]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for attachment in attachments:
        counts[attachment.category] = counts.get(attachment.category, 0) + 1
    return counts


def _decode_process_output(payload: bytes) -> str:
    preferred_encoding = locale.getpreferredencoding(False) or "utf-8"
    for encoding in (preferred_encoding, "utf-8", "gbk", "cp936"):
        try:
            return payload.decode(encoding)
        except UnicodeDecodeError:
            continue
    return payload.decode(preferred_encoding, errors="replace")


def _read_text_with_fallback(path: Path) -> str:
    for encoding in ("utf-8", locale.getpreferredencoding(False) or "utf-8", "gbk", "cp936"):
        try:
            return path.read_text(encoding=encoding)
        except UnicodeDecodeError:
            continue
    return path.read_text(encoding="utf-8", errors="replace")


app = create_app()


def main() -> int:
    parser = argparse.ArgumentParser(description="Expose local test scripts as FastAPI async APIs.")
    parser.add_argument("--host", default="0.0.0.0", help="bind host")
    parser.add_argument("--port", type=int, default=18080, help="bind port")
    args = parser.parse_args()

    try:
        import uvicorn
    except ImportError as exc:
        raise SystemExit(
            "uvicorn is required to run the FastAPI service. Install fastapi and uvicorn first."
        ) from exc

    uvicorn.run("demo_http_api:app", host=args.host, port=args.port, reload=False)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
