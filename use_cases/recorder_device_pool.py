from __future__ import annotations

import json
import os
import time
import uuid
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Iterator

from .speaker_test_cases import RecorderDeviceConfig


DEFAULT_RECORDER_POOL_WAIT_SECONDS = 300.0
LOCK_STALE_SECONDS = 30.0
POLL_SECONDS = 1.0


@dataclass(frozen=True)
class RecorderPoolDevice:
    device_id: str
    config: RecorderDeviceConfig
    max_connections: int


@dataclass(frozen=True)
class RecorderPoolLease:
    request_id: str
    device_id: str
    recorder_device: RecorderDeviceConfig


class RecorderDevicePoolError(RuntimeError):
    pass


class RecorderDevicePool:
    def __init__(self, config_path: str | Path) -> None:
        self.config_path = Path(config_path)
        self.state_path = self.config_path.with_suffix(".state.json")
        self.lock_path = self.config_path.with_suffix(".lock")

    @contextmanager
    def acquire(self, wait_seconds: float = DEFAULT_RECORDER_POOL_WAIT_SECONDS) -> Iterator[RecorderPoolLease]:
        request_id = f"{os.getpid()}-{uuid.uuid4().hex}"
        lease: RecorderPoolLease | None = None
        try:
            lease = self.acquire_once(request_id=request_id, wait_seconds=wait_seconds)
            yield lease
        finally:
            if lease is not None:
                self.release(lease.request_id)
            else:
                self._remove_wait_request(request_id)

    @contextmanager
    def acquire_for_device(
        self,
        host: str,
        port: int = 8000,
        wait_seconds: float = DEFAULT_RECORDER_POOL_WAIT_SECONDS,
    ) -> Iterator[RecorderPoolLease]:
        request_id = f"{os.getpid()}-{uuid.uuid4().hex}"
        lease: RecorderPoolLease | None = None
        try:
            lease = self.acquire_once(
                request_id=request_id,
                wait_seconds=wait_seconds,
                target_host=host,
                target_port=port,
            )
            yield lease
        finally:
            if lease is not None:
                self.release(lease.request_id)
            else:
                self._remove_wait_request(request_id)

    def acquire_once(
        self,
        request_id: str,
        wait_seconds: float,
        target_host: str = "",
        target_port: int = 0,
    ) -> RecorderPoolLease:
        if wait_seconds <= 0:
            raise ValueError("wait_seconds must be positive")
        deadline = time.time() + wait_seconds
        while time.time() <= deadline:
            with self._file_lock():
                devices = self._load_devices()
                target_device_id = self._resolve_target_device_id(devices, target_host, target_port)
                state = self._load_state()
                self._cleanup_state(state)
                self._ensure_wait_request(state, request_id, target_device_id)

                queue = state["queue"]
                if queue and queue[0]["request_id"] == request_id:
                    lease = self._try_acquire_head_request(state, devices, request_id, target_device_id)
                    if lease is not None:
                        self._save_state(state)
                        return lease
                self._save_state(state)
            time.sleep(POLL_SECONDS)

        self._remove_wait_request(request_id)
        raise RecorderDevicePoolError(f"no recorder device available within {wait_seconds:.0f}s")

    def release(self, request_id: str) -> None:
        with self._file_lock():
            state = self._load_state()
            state["active"] = [
                item for item in state.get("active", [])
                if item.get("request_id") != request_id
            ]
            self._save_state(state)

    def _try_acquire_head_request(
        self,
        state: dict,
        devices: list[RecorderPoolDevice],
        request_id: str,
        target_device_id: str = "",
    ) -> RecorderPoolLease | None:
        active = state["active"]
        for device in devices:
            if target_device_id and device.device_id != target_device_id:
                continue
            active_count = sum(1 for item in active if item.get("device_id") == device.device_id)
            if active_count >= device.max_connections:
                continue
            active.append(
                {
                    "request_id": request_id,
                    "device_id": device.device_id,
                    "pid": os.getpid(),
                    "acquired_at": time.time(),
                }
            )
            state["queue"] = [
                item for item in state["queue"]
                if item.get("request_id") != request_id
            ]
            return RecorderPoolLease(
                request_id=request_id,
                device_id=device.device_id,
                recorder_device=device.config,
            )
        return None

    def _ensure_wait_request(self, state: dict, request_id: str, target_device_id: str = "") -> None:
        if any(item.get("request_id") == request_id for item in state["queue"]):
            return
        state["queue"].append(
            {
                "request_id": request_id,
                "target_device_id": target_device_id,
                "pid": os.getpid(),
                "created_at": time.time(),
            }
        )

    def _remove_wait_request(self, request_id: str) -> None:
        with self._file_lock():
            state = self._load_state()
            state["queue"] = [
                item for item in state.get("queue", [])
                if item.get("request_id") != request_id
            ]
            self._save_state(state)

    def _cleanup_state(self, state: dict) -> None:
        state["queue"] = [
            item for item in state.get("queue", [])
            if self._pid_alive(item.get("pid"))
        ]
        state["active"] = [
            item for item in state.get("active", [])
            if self._pid_alive(item.get("pid"))
        ]

    def _load_devices(self) -> list[RecorderPoolDevice]:
        if not self.config_path.exists():
            raise FileNotFoundError(f"recorder device pool config not found: {self.config_path}")
        with self.config_path.open("r", encoding="utf-8") as file:
            payload = json.load(file)
        devices_payload = payload.get("devices")
        if not isinstance(devices_payload, list) or not devices_payload:
            raise RecorderDevicePoolError("recorder device pool config must contain non-empty devices list")

        devices: list[RecorderPoolDevice] = []
        seen_ids: set[str] = set()
        for index, item in enumerate(devices_payload, start=1):
            if not isinstance(item, dict):
                raise RecorderDevicePoolError(f"recorder device item #{index} must be an object")
            device_id = str(item.get("id") or item.get("host") or f"recorder-{index}").strip()
            if not device_id:
                raise RecorderDevicePoolError(f"recorder device item #{index} id is empty")
            if device_id in seen_ids:
                raise RecorderDevicePoolError(f"duplicate recorder device id: {device_id}")
            seen_ids.add(device_id)
            max_connections = int(item.get("max_connections", 1))
            if max_connections <= 0:
                raise RecorderDevicePoolError(f"recorder device {device_id} max_connections must be positive")
            devices.append(
                RecorderPoolDevice(
                    device_id=device_id,
                    max_connections=max_connections,
                    config=RecorderDeviceConfig(
                        host=str(item["host"]),
                        port=int(item.get("port", 8000)),
                        username=str(item["username"]),
                        password=str(item["password"]),
                        channel=int(item.get("channel", 0)),
                        voice_channel=int(item.get("voice_channel", 1)),
                    ),
                )
            )
        return devices

    def _resolve_target_device_id(
        self,
        devices: list[RecorderPoolDevice],
        target_host: str,
        target_port: int,
    ) -> str:
        if not target_host:
            return ""
        normalized_host = target_host.strip().lower()
        normalized_port = int(target_port or 8000)
        for device in devices:
            if device.config.host.strip().lower() == normalized_host and device.config.port == normalized_port:
                return device.device_id
        raise RecorderDevicePoolError(
            f"recording device {target_host}:{normalized_port} is not in recorder device pool"
        )

    def _load_state(self) -> dict:
        if not self.state_path.exists():
            return {"queue": [], "active": []}
        with self.state_path.open("r", encoding="utf-8") as file:
            state = json.load(file)
        if not isinstance(state, dict):
            return {"queue": [], "active": []}
        queue = state.get("queue") if isinstance(state.get("queue"), list) else []
        active = state.get("active") if isinstance(state.get("active"), list) else []
        return {"queue": queue, "active": active}

    def _save_state(self, state: dict) -> None:
        self.state_path.parent.mkdir(parents=True, exist_ok=True)
        with self.state_path.open("w", encoding="utf-8") as file:
            json.dump(state, file, ensure_ascii=False, indent=2)

    @contextmanager
    def _file_lock(self) -> Iterator[None]:
        self.lock_path.parent.mkdir(parents=True, exist_ok=True)
        while True:
            try:
                fd = os.open(str(self.lock_path), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
                try:
                    os.write(fd, str(os.getpid()).encode("ascii", errors="ignore"))
                finally:
                    os.close(fd)
                break
            except FileExistsError:
                if self._lock_stale():
                    try:
                        self.lock_path.unlink()
                    except FileNotFoundError:
                        pass
                    continue
                time.sleep(0.1)
        try:
            yield
        finally:
            try:
                self.lock_path.unlink()
            except FileNotFoundError:
                pass

    def _lock_stale(self) -> bool:
        try:
            age = time.time() - self.lock_path.stat().st_mtime
        except FileNotFoundError:
            return False
        return age > LOCK_STALE_SECONDS

    @staticmethod
    def _pid_alive(pid: object) -> bool:
        try:
            value = int(pid)
        except (TypeError, ValueError):
            return False
        if value <= 0:
            return False
        try:
            os.kill(value, 0)
        except OSError:
            return False
        return True
