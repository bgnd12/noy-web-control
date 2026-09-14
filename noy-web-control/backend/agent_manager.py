"""
agent_manager.py — Jembatan (bridge) antara Web Control dan Noy Agent ASLI.

Prinsip kerja:
- Menjalankan Noy yang sudah ada (C:\\Users\\akbar\\Noy Agent\\main.py) sebagai
  subprocess, persis seperti perintah manual `py -3.12 main.py`, TANPA
  mengubah / mengimpor satu baris pun kode Noy.
- Launcher `py` di-resolve ke biner python.exe asli agar proses yang dikelola
  Web Control adalah proses python Noy yang sesungguhnya (bukan wrapper).
  Artinya: PID pada status mencerminkan proses nyata, dan STOP benar-benar
  menghentikan Noy tanpa meninggalkan proses orphan.
- Anti-proses-dobel:
  * guard terhadap proses kelolaan sendiri, plus
  * deteksi (best-effort via psutil) Noy yang sudah berjalan DI LUAR Web
    Control. Jika ditemukan, START tidak membuat proses baru.
- STOP hanya menyentuh proses Noy yang dikelola Web Control. Noy yang berjalan
  di luar (mis. dijalankan manual oleh user) TIDAK pernah dibunuh.
- stdout/stderr Noy dibaca (UTF-8, unbuffered lewat env) dan dikirim ke
  frontend via WebSocket. Kode ANSI warna dibersihkan, dan nilai secret
  (API key / token / password) yang mungkin lolos ke log di-redaksi sehingga
  tidak pernah tampil ke browser.
- Tidak ada endpoint/command arbitrary: satu-satunya perintah yang pernah
  dieksekusi adalah argv tetap dari config (NOY_AGENT_CMD / NOY_AGENT_PYTHON),
  diset lewat .env, bukan dari request browser.
"""
from __future__ import annotations

import asyncio
import os
import re
import signal
import subprocess
import sys
import threading
import time
from collections import deque
from dataclasses import dataclass, field
from enum import Enum
from typing import Optional

from . import config

try:
    import psutil  # opsional, hanya untuk deteksi proses Noy eksternal
    _PS_EXC = (psutil.NoSuchProcess, psutil.AccessDenied, OSError, TypeError)
except ImportError:  # pragma: no cover
    psutil = None
    _PS_EXC = (OSError, TypeError)


class NoyState(str, Enum):
    STOPPED = "STOPPED"
    STARTING = "STARTING"
    ONLINE = "ONLINE"
    LISTENING = "LISTENING"
    PROCESSING = "PROCESSING"
    RESPONDING = "RESPONDING"
    OFFLINE = "OFFLINE"   # proses mati tak terduga (crash)
    STOPPING = "STOPPING"


# ---------------------------------------------------------------------------
# Prediksi status dari stdout Noy.
# Noy asli mencetak transit ke state machine dalam bentuk:
#   [State Machine] Transisi: X -> Y
# sehingga baris semacam itu dipetakan langsung ke target state Y.
# ---------------------------------------------------------------------------
_TRANSITION_RE = re.compile(
    r"\[\s*State Machine\s*\]\s*Transisi:\s*.*?\s*->\s*([A-Za-z][A-Za-z ]*)",
    re.I,
)
# Pemetaan target state machine Noy -> state Web Control.
_TRANSITION_TARGETS = {
    "listening": NoyState.LISTENING,
    "wake word detected": NoyState.LISTENING,
    "processing": NoyState.PROCESSING,
    "executing": NoyState.PROCESSING,
    "responding": NoyState.RESPONDING,
    "waiting for confirmation": NoyState.RESPONDING,
    "standby": NoyState.ONLINE,
}

# Pola fallback untuk baris log yang bukan transisi (termasuk dummy agent).
_STATE_PATTERNS: list[tuple[re.Pattern, NoyState]] = [
    (re.compile(r"(noy siap|engine started|\[Inisialisasi\]|AI DESKTOP ASSISTANT)", re.I), NoyState.ONLINE),
    (re.compile(r"(mendengarkan suara|tahan tombol|listening|halo\s*noy)", re.I), NoyState.LISTENING),
    (re.compile(r"(processing|berpikir)", re.I), NoyState.PROCESSING),
    (re.compile(r"(respond(?:ing)?\b|🔊)", re.I), NoyState.RESPONDING),
]

# Bersihkan ANSI escape code (colorama) dari baris log.
_ANSI_RE = re.compile(r"\x1B(?:[@-Z\\-_]|\[[0-?]*[ -/]*[@-~])")
# Redaksi nilai secret yang mungkin lolos ke log (pertahanan ekstra, supaya
# API key / token / password apa pun TIDAK pernah tampil ke frontend).
_SECRET_RE = re.compile(
    r"(?i)(?P<key>\b[\w.\-/]*?(?:api[_-]?key|token|secret|password|passwd|authorization)[\w.\-/]*?)\b\s*[:=]\s*(?P<val>\S+)"
)
_SECRET_MASK = r"\g<key>=***"


def _sanitize_line(line: str) -> str:
    line = _SECRET_RE.sub(_SECRET_MASK, line)
    line = _ANSI_RE.sub("", line)
    return line.strip()


# Cache hasil resolve `py -3.12` -> sys.executable (dipanggil sekali saja).
_interpreter_cache = ""


def _resolve_interpreter(argv0: str, flags: list[str]) -> str:
    """Cari biner python yang akan dijalankan launcher `py`.

    Contoh: `py -3.12 -c "..."` mengembalikan `C:\\...\\Python312\\python.exe`.
    Hasil di-cache; kalau gagal, kembalikan string kosong (spawn pakai argv asli).
    """
    global _interpreter_cache
    if _interpreter_cache:
        return _interpreter_cache
    try:
        out = subprocess.run(
            [argv0, *flags, "-c", "import sys; print(sys.executable)"],
            capture_output=True,
            text=True,
            timeout=30,
        )
        lines = [ln.strip() for ln in out.stdout.splitlines() if ln.strip()]
        if out.returncode == 0 and lines:
            cand = lines[-1]
            if os.path.isfile(cand):
                _interpreter_cache = cand
    except Exception:
        pass
    return _interpreter_cache


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
        self._external_pid: Optional[int] = None  # Noy berjalan di luar Web Control
        self._status = AgentStatus()
        self._lock = threading.Lock()
        self._reader_thread: Optional[threading.Thread] = None
        self._subscribers: set[asyncio.Queue] = set()
        self._loop: Optional[asyncio.AbstractEventLoop] = None
        self._started_args: list[str] = []
        # Nama skrip Noy (mis. main.py / dummy_noy_agent.py) untuk deteksi eksternal.
        self._script_name = self._extract_script_name(config.NOY_AGENT_CMD)

    # ---------- dipanggil dari FastAPI startup untuk simpan event loop ----------
    def bind_loop(self, loop: asyncio.AbstractEventLoop):
        self._loop = loop

    # ---------------------------- PUBLIC API ----------------------------

    def start(self) -> dict:
        with self._lock:
            if self._process is not None and self._process.poll() is None:
                return {"ok": True, "status": "already_running",
                        "state": self._status.state.value, "pid": self._process.pid}

            self._reconcile()
            if self._external_pid is not None:
                self._push_log(
                    f"[web-control] Noy sudah berjalan di luar Web Control "
                    f"(PID {self._external_pid}). Tidak membuat proses baru."
                )
                return {"ok": True, "status": "already_running",
                        "state": self._status.state.value,
                        "pid": self._external_pid, "managed": False}

            argv = self._build_spawn_argv()
            if not argv:
                return {"ok": False, "status": "error",
                        "error": "NOY_AGENT_CMD kosong — cek .env"}
            try:
                env = os.environ.copy()
                # stdout Noy dibaca Web Control: wajib UTF-8 & unbuffered supaya
                # log berbahasa Indonesia + emoji terbaca, dan sampai realtime.
                env["PYTHONIOENCODING"] = "utf-8"
                env["PYTHONUNBUFFERED"] = "1"
                creationflags = 0
                if sys.platform == "win32":
                    creationflags |= subprocess.CREATE_NEW_PROCESS_GROUP
                    creationflags |= getattr(subprocess, "CREATE_NO_WINDOW", 0)
                self._process = subprocess.Popen(
                    argv,
                    cwd=config.NOY_AGENT_DIR,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.STDOUT,
                    text=True,
                    encoding="utf-8",
                    errors="replace",
                    bufsize=1,
                    env=env,
                    creationflags=creationflags,
                )
            except FileNotFoundError as e:
                self._push_log(f"[web-control] Gagal start: {e}")
                return {"ok": False, "status": "error", "error": str(e)}

            self._started_args = list(argv)
            self._status = AgentStatus(
                state=NoyState.STARTING,
                pid=self._process.pid,
                started_at=time.time(),
            )
            self._push_log(
                f"[web-control] Noy Agent dimulai (PID {self._process.pid}) "
                f"via {' '.join(argv)}"
            )
            self._set_state(NoyState.STARTING)

            self._reader_thread = threading.Thread(target=self._read_output, daemon=True)
            self._reader_thread.start()

            threading.Thread(target=self._grace_timeout_watch, daemon=True).start()

            return {"ok": True, "status": "starting",
                    "state": self._status.state.value, "pid": self._process.pid}

    def stop(self) -> dict:
        with self._lock:
            proc = self._process
            external = self._external_pid

            # Tidak ada proses kelolaan sama sekali.
            if (proc is None or proc.poll() is not None) and external is None:
                self._set_state(NoyState.STOPPED)
                return {"ok": True, "status": "already_stopped",
                        "state": self._status.state.value}

            # Hanya Noy eksternal yang berjalan -> TIDAK dikelola Web Control,
            # jadi tidak dihentikan (kebijakan: STOP hanya proses kelolaan).
            if proc is None or proc.poll() is not None:
                self._push_log(
                    f"[web-control] Noy berjalan di luar Web Control (PID {external}) — "
                    f"tidak dihentikan karena tidak dikelola Web Control."
                )
                return {"ok": True, "status": "not_managed",
                        "state": self._status.state.value, "pid": external}

            self._set_state(NoyState.STOPPING)
            self._push_log("[web-control] Mengirim sinyal berhenti ke Noy Agent...")

        # Graceful stop DI LUAR lock supaya status endpoint tidak terblokir.
        try:
            if sys.platform == "win32":
                try:
                    # CTRL_BREAK (pendekatan paling "Ctrl+C"-ish): Noy menangkap
                    # KeyboardInterrupt dan menutup diri secara normal.
                    proc.send_signal(signal.CTRL_BREAK_EVENT)
                    proc.wait(timeout=config.STOP_GRACE_SECONDS)
                except (OSError, ValueError):
                    # Tidak punya console (mis. berjalan headless) -> terminate.
                    proc.terminate()
                    proc.wait(timeout=config.STOP_GRACE_SECONDS)
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
            self._external_pid = None
            self._started_args = []
            self._status.pid = None
            self._status.started_at = None
            self._set_state(NoyState.STOPPED)

        self._push_log("[web-control] Noy Agent berhenti.")
        return {"ok": True, "status": "stopped", "state": self._status.state.value}

    def status(self) -> dict:
        with self._lock:
            self._reconcile()
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

    @staticmethod
    def _extract_script_name(argv: list[str]) -> str:
        """Ambil nama skrip (arg non-flag pertama) dari NOY_AGENT_CMD."""
        for tok in argv[1:]:
            if tok.startswith("-"):
                continue
            return os.path.basename(tok)
        return ""

    def _build_spawn_argv(self) -> list[str]:
        """Susun argv spawn; resolve `py -3.12 ...` -> python.exe asli."""
        argv = list(config.NOY_AGENT_CMD)
        if not argv:
            return []
        if sys.platform != "win32":
            return argv
        if os.path.basename(argv[0]).lower() not in ("py", "py.exe"):
            return argv

        flags: list[str] = []
        i = 1
        while i < len(argv) and argv[i].startswith("-"):
            flags.append(argv[i])
            i += 1
        rest = argv[i:]

        interp = config.NOY_AGENT_PYTHON or _resolve_interpreter(argv[0], flags)
        if not rest:
            # kasus `py -3.12 script.py ...` selalu punya skrip; amankan saja
            return argv
        if interp and os.path.isfile(interp):
            return [interp, *rest]
        return argv

    def _reconcile(self):
        """Samakan status dengan proses nyata. Dipanggil dalam lock."""
        # 1) Proses kelolaan sudah keluar sendiri (crash / normal tanpa lewat stop).
        if self._process is not None and self._process.poll() is not None:
            self._push_log(
                f"[web-control] Noy Agent berhenti tak terduga "
                f"(exit code {self._process.returncode})."
            )
            self._process = None
            self._started_args = []
            if self._status.state not in (NoyState.STOPPING, NoyState.STOPPED):
                self._set_state(NoyState.OFFLINE)
            self._status.pid = None
            self._status.started_at = None

        managed_alive = self._process is not None and self._process.poll() is None

        # 2) Rekonsiliasi Noy eksternal dengan keadaan nyata.
        if self._external_pid is not None:
            if not self._is_external_alive():
                self._external_pid = None
                if not managed_alive:
                    self._status.pid = None
                    self._push_log("[web-control] Proses Noy eksternal tidak ditemukan lagi.")
                    if self._status.state != NoyState.STOPPED:
                        self._set_state(NoyState.STOPPED)
        elif not managed_alive:
            ext = self._find_external_noy()
            if ext is not None:
                self._external_pid = ext.pid
                self._status.pid = ext.pid
                self._status.started_at = None
                self._push_log(
                    f"[web-control] Terdeteksi Noy berjalan di luar Web Control "
                    f"(PID {ext.pid})."
                )
                if self._status.state == NoyState.STOPPED:
                    self._set_state(NoyState.ONLINE)

    def _set_state(self, state: NoyState):
        self._status.state = state
        self._broadcast({"type": "status", "data": self._status.to_dict()})

    def _push_log(self, line: str):
        line = _sanitize_line(line)
        if not line:
            return
        self._status.last_log = line
        self._status.logs.append(line)
        self._broadcast({"type": "log", "line": line})

    # -------- deteksi / pemantauan proses Noy eksternal (best-effort) --------

    def _is_noy_process(self, p) -> bool:
        try:
            if hasattr(p, "info"):
                cmd = p.info.get("cmdline") or []
                cwd = p.info.get("cwd")
            else:  # psutil.Process biasa
                cmd = p.cmdline() or []
                cwd = p.cwd()
        except (_PS_EXC, TypeError):
            return False
        if not cmd or not self._script_name:
            return False
        first = os.path.basename(cmd[0]).lower().rstrip(".exe")
        if first not in ("python", "pythonw", "py"):
            return False
        if not any(self._script_name.lower() in tok.lower() for tok in cmd):
            return False
        if not cwd:
            return False
        try:
            return os.path.normcase(os.path.realpath(str(cwd))) == os.path.normcase(
                os.path.realpath(config.NOY_AGENT_DIR)
            )
        except Exception:
            return False

    def _is_external_alive(self) -> bool:
        if self._external_pid is None:
            return False
        if psutil is None:
            return True  # tidak bisa dipastikan -> anggap masih hidup
        try:
            return self._is_noy_process(psutil.Process(self._external_pid))
        except _PS_EXC:
            return False

    def _find_external_noy(self):
        """Temukan Noy yang berjalan di luar kendali Web Control."""
        if psutil is None or not self._script_name:
            return None
        pid_ok = None
        if self._process is not None:
            pid_ok = self._process.pid
        for p in psutil.process_iter(["pid", "name", "cmdline", "cwd"]):
            try:
                if p.info["pid"] == pid_ok:
                    continue
                if self._is_noy_process(p):
                    return p
            except _PS_EXC:
                continue
        return None

    # -------- pembaca stdout Noy --------

    def _read_output(self):
        proc = self._process
        if proc is None or proc.stdout is None:
            return
        for raw_line in proc.stdout:
            line = raw_line.rstrip()
            if not line:
                continue
            self._push_log(line)

            m = _TRANSITION_RE.search(line)
            if m:
                target = m.group(1).strip()
                mapped = _TRANSITION_TARGETS.get(target.lower())
                if mapped is not None:
                    self._set_state(mapped)
                    continue
            for pattern, new_state in _STATE_PATTERNS:
                if pattern.search(line):
                    self._set_state(new_state)
                    break

        # stdout habis -> proses keluar.
        with self._lock:
            if self._process is proc:
                self._process = None
                self._started_args = []
                if self._status.state not in (NoyState.STOPPING, NoyState.STOPPED):
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