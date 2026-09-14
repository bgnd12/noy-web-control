"""
config.py — Konfigurasi Noy Web Control.

Semua nilai bisa dioverride lewat file .env di folder backend/
(lihat .env.example). TIDAK ADA konfigurasi di sini yang menyentuh
atau mengimpor kode Noy Agent — komunikasi hanya lewat subprocess
+ stdout, sehingga noy-agent tetap 100% independen.
"""
import os
import shlex
from pathlib import Path

try:
    from dotenv import load_dotenv
    load_dotenv(Path(__file__).parent / ".env")
except ImportError:
    # python-dotenv opsional; kalau tidak terpasang, fallback ke os.environ murni
    pass

BASE_DIR = Path(__file__).resolve().parent

# Host & port web control (bukan port Noy — Noy tidak membuka port apa pun)
HOST = os.getenv("NOY_WEB_HOST", "127.0.0.1")
PORT = int(os.getenv("NOY_WEB_PORT", "8787"))

# --- Lokasi & cara menjalankan Noy Agent ---
# Folder tempat main.py Noy berada. Default menunjuk ke Noy Agent asli.
# Bisa dioverride lewat .env (mis. untuk test memakai dummy_noy_agent.py).
NOY_AGENT_DIR = os.getenv("NOY_AGENT_DIR", r"C:\Users\akbar\Noy Agent")

# Command persis yang dulunya diketik manual: `py -3.12 main.py`
# Format: dipisah spasi, dukung quoting. Contoh .env:
#   NOY_AGENT_CMD=py -3.12 main.py
# Catatan: saat spawn, `py` launcher di-resolve ke biner python.exe asli
# sehingga proses yang dikelola Web Control = proses python Noy sebenarnya
# (PID status akurat, dan STOP tidak meninggalkan orphan process).
_default_cmd = "py -3.12 main.py"
NOY_AGENT_CMD = shlex.split(os.getenv("NOY_AGENT_CMD", _default_cmd))

# (Opsional) Path interpreter Python yang dipakai Noy, mis.:
#   NOY_AGENT_PYTHON=C:\Users\akbar\AppData\Local\Programs\Python\Python312\python.exe
# Kosongkan agar di-resolve otomatis lewat `py -3.12`.
NOY_AGENT_PYTHON = os.getenv("NOY_AGENT_PYTHON", "")

# Berapa lama (detik) proses boleh ada di state STARTING sebelum kita anggap ONLINE
# jika tidak ada log "ready" yang terdeteksi.
STARTING_GRACE_SECONDS = float(os.getenv("NOY_STARTING_GRACE_SECONDS", "5"))

# Timeout graceful shutdown sebelum backend force-kill proses (detik)
STOP_GRACE_SECONDS = float(os.getenv("NOY_STOP_GRACE_SECONDS", "6"))

# Berapa banyak baris log activity terakhir yang disimpan di memori
LOG_BUFFER_SIZE = int(os.getenv("NOY_LOG_BUFFER_SIZE", "200"))

# File penyimpanan settings Web Control (BUKAN config Noy — lihat README bagian Settings)
SETTINGS_FILE = BASE_DIR / "data" / "settings.json"
