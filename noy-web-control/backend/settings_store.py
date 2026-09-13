"""
settings_store.py — Penyimpanan pengaturan dasar Web Control.

PENTING: file settings.json ini milik Web Control, BUKAN file konfigurasi
Noy Agent. Noy Agent tidak membacanya (dan tidak diubah untuk membacanya)
pada tahap ini — lihat README bagian "Settings & Batasan Tahap Ini".
"""
import json
import threading
from pathlib import Path

from . import config

_lock = threading.Lock()

_DEFAULTS = {
    "listening_mode": "wake_word",       # "wake_word" | "push_to_talk"
    "push_to_talk_shortcut": "Ctrl+Shift+Space",
    "stt_silence_timeout_ms": 1200,
    "microphone": "default",
    "tts_volume": 80,
    "debug_mode": False,
}


def _ensure_file():
    config.SETTINGS_FILE.parent.mkdir(parents=True, exist_ok=True)
    if not config.SETTINGS_FILE.exists():
        config.SETTINGS_FILE.write_text(json.dumps(_DEFAULTS, indent=2), encoding="utf-8")


def load() -> dict:
    with _lock:
        _ensure_file()
        try:
            data = json.loads(config.SETTINGS_FILE.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            data = {}
        merged = {**_DEFAULTS, **data}
        return merged


def save(partial: dict) -> dict:
    with _lock:
        _ensure_file()
        current = load()
        current.update({k: v for k, v in partial.items() if k in _DEFAULTS})
        config.SETTINGS_FILE.write_text(json.dumps(current, indent=2), encoding="utf-8")
        return current
