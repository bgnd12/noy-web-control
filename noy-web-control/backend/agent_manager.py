"""
agent_manager.py — Jembatan (bridge) antara Web Control dan Noy Agent.

Tanggung jawab modul ini:
- Menjalankan Noy Agent sebagai subprocess (persis seperti `py -3.12 main.py`
  yang biasa diketik manual), TANPA mengubah satu baris pun kode Noy.
- Mencegah proses dobel jika tombol START ditekan berkali-kali.
- Menghentikan proses secara graceful saat STOP ditekan.
- Membaca stdout Noy untuk menyimpulkan status (LISTENING/PROCESSING/dst)
  dan menyimpan log activity, murni dengan pattern matching teks —
  tidak butuh Noy tahu apa-apa soal Web Control.
- Menyiarkan (broadcast) perubahan status/log ke semua klien WebSocket
  yang terhubung.

Tidak ada endpoint/command arbitrary di sini. Satu-satunya command yang
pernah dieksekusi adalah config.NOY_AGENT_CMD yang tetap (fixed),
diset lewat .env, bukan dari request browser.
"""
from __future__ import annotations

import asyncio
import re
import subprocess
import sys
import threading
import time
from collections import deque
from dataclasses import dataclass, field
from enum import Enum
from typing import Optional

from . import config


class NoyState(str, Enum):
    STOPPED = "STOPPED"
    STARTING = "STARTING"
    ONLINE = "ONLINE"
    LISTENING = "LISTENING"
    PROCESSING = "PROCESSING"
    RESPONDING = "RESPONDING"
    OFFLINE = "OFFLINE"   # proses mati tak terduga (crash)
    STOPPING = "STOPPING"


# Pola baris stdout Noy -> state baru.
# Silakan sesuaikan/tambah pola ini agar cocok dengan log asli Noy Anda;
# ini TIDAK mengubah Noy, hanya bagaimana Web Control menafsirkan outputnya.
_STATE_PATTERNS: list[tuple[re.Pattern, NoyState]] = [
    (re.compile(r"halo\s*noy", re.I), NoyState.LISTENING),
    (re.compile(r"listening", re.I), NoyState.LISTENING),
    (re.compile(r"processing", re.I), NoyState.PROCESSING),
    (re.compile(r"respond(ing)?", re.I), NoyState.RESPONDING),
    (re.compile(r"(noy siap|ready|noy is ready|engine started)", re.I), NoyState.ONLINE),
]


@dataclass
class AgentStatus:
    state: NoyState = NoyState.STOPPED
    pid: Optional[int] = None
    started_at: Optional[float] = None
    last_log: str = ""
    logs: deque = field(default_factory=lambda: deque(maxlen=config.LOG_BUFFER_SIZE))

    def to_dict(self):
        return {
            "state": self.state.value,
            "pid": self.pid,
            "uptime_seconds": (time.time() - self.started_at) if self.started_at else None,
            "last_log": self.last_log,
            "recent_logs": list(self.logs),
        }


class AgentManager:
    def __init__(self):
        self._process: Optional[subprocess.Popen] = None
        self._status = AgentStatus()
        self._lock = threading.Lock()
        self._reader_thread: Optional[threading.Thread] = None
        self._subscribers: set[asyncio.Queue] = set()
        self._loop: Optional[asyncio.AbstractEventLoop] = None

    # ---------- dipanggil dari FastAPI startup untuk simpan event loop ----------
    def bind_loop(self, loop: asyncio.AbstractEventLoop):
        self._loop = loop

    # ---------------------------- PUBLIC API ----------------------------

    def start(self) -> dict:
        with self._lock:
            if self._process is not None and self._process.poll() is None:
                return {"ok": True, "status": "already_running", "state": self._status.state.value}

            try:
                self._process = subprocess.Popen(
                    config.NOY_AGENT_CMD,
                    cwd=config.NOY_AGENT_DIR,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.STDOUT,
                    text=True,
                    bufsize=1,
                    creationflags=(
                        subprocess.CREATE_NEW_PROCESS_GROUP
                        if sys.platform == "win32"
                        else 0
                    ),
                )
            except FileNotFoundError as e:
                self._push_log(f"[web-control] Gagal start: {e}")
                return {"ok": False, "status": "error", "error": str(e)}

            self._status = AgentStatus(
                state=NoyState.STARTING,
                pid=self._process.pid,
                started_at=time.time(),
            )
            self._push_log(f"[web-control] Noy Agent dimulai (PID {self._process.pid})")
            self._set_state(NoyState.STARTING)

            self._reader_thread = threading.Thread(target=self._read_output, daemon=True)
            self._reader_thread.start()

            threading.Thread(target=self._grace_timeout_watch, daemon=True).start()

            return {"ok": True, "status": "starting", "state": self._status.state.value, "pid": self._process.pid}

    def stop(self) -> dict:
        with self._lock:
            if self._process is None or self._process.poll() is not None:
                self._set_state(NoyState.STOPPED)
                return {"ok": True, "status": "already_stopped", "state": self._status.state.value}

            proc = self._process
            self._set_state(NoyState.STOPPING)
            self._push_log("[web-control] Mengirim sinyal berhenti ke Noy Agent...")

        # Graceful terminate di luar lock supaya tidak memblok status endpoint
        try:
            if sys.platform == "win32":
                proc.terminate()  # kirim CTRL_BREAK/TerminateProcess yang wajar
            else:
                proc.terminate()  # SIGTERM
            proc.wait(timeout=config.STOP_GRACE_SECONDS)
        except subprocess.TimeoutExpired:
            self._push_log("[web-control] Tidak merespons, force stop...")
            proc.kill()
            try:
                proc.wait(timeout=3)
            except subprocess.TimeoutExpired:
                pass
        except Exception as e:
            self._push_log(f"[web-control] Error saat stop: {e}")

        with self._lock:
            self._process = None
            self._set_state(NoyState.STOPPED)
            self._status.pid = None
            self._status.started_at = None

        self._push_log("[web-control] Noy Agent berhenti.")
        return {"ok": True, "status": "stopped", "state": self._status.state.value}

    def status(self) -> dict:
        # Deteksi crash: proses ada tapi sudah exit tanpa lewat stop()
        with self._lock:
            if self._process is not None and self._process.poll() is not None:
                self._push_log(f"[web-control] Noy Agent berhenti tak terduga (exit code {self._process.returncode})")
                self._process = None
                self._set_state(NoyState.OFFLINE)
                self._status.pid = None
        return self._status.to_dict()

    # ---------------------------- WebSocket pub/sub ----------------------------

    def subscribe(self) -> asyncio.Queue:
        q: asyncio.Queue = asyncio.Queue()
        self._subscribers.add(q)
        return q

    def unsubscribe(self, q: asyncio.Queue):
        self._subscribers.discard(q)

    def _broadcast(self, payload: dict):
        if self._loop is None:
            return
        for q in list(self._subscribers):
            self._loop.call_soon_threadsafe(q.put_nowait, payload)

    # ---------------------------- Internal helpers ----------------------------

    def _set_state(self, state: NoyState):
        self._status.state = state
        self._broadcast({"type": "status", "data": self._status.to_dict()})

    def _push_log(self, line: str):
        self._status.last_log = line
        self._status.logs.append(line)
        self._broadcast({"type": "log", "line": line})

    def _read_output(self):
        proc = self._process
        if proc is None or proc.stdout is None:
            return
        for raw_line in proc.stdout:
            line = raw_line.rstrip()
            if not line:
                continue
            self._push_log(line)
            for pattern, new_state in _STATE_PATTERNS:
                if pattern.search(line):
                    self._set_state(new_state)
                    break
        # stdout habis -> proses exit
        with self._lock:
            if self._process is proc:
                self._process = None
                if self._status.state != NoyState.STOPPING and self._status.state != NoyState.STOPPED:
                    self._set_state(NoyState.OFFLINE)
                else:
                    self._set_state(NoyState.STOPPED)

    def _grace_timeout_watch(self):
        time.sleep(config.STARTING_GRACE_SECONDS)
        with self._lock:
            if self._status.state == NoyState.STARTING and self._process is not None and self._process.poll() is None:
                self._set_state(NoyState.ONLINE)
                self._push_log("[web-control] Tidak ada sinyal 'ready' eksplisit, menandai ONLINE setelah grace period.")


manager = AgentManager()
