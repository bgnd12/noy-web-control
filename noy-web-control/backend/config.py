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
# Folder tempat main.py Noy berada (WAJIB diisi sebelum START dipakai serius)
NOY_AGENT_DIR = os.getenv("NOY_AGENT_DIR", str(BASE_DIR.parent.parent / "noy-agent"))

# Command persis yang dulunya diketik manual: `py -3.12 main.py`
# Format: dipisah spasi, dukung quoting. Contoh .env:
#   NOY_AGENT_CMD=py -3.12 main.py
_default_cmd = "py -3.12 main.py"
NOY_AGENT_CMD = shlex.split(os.getenv("NOY_AGENT_CMD", _default_cmd))

# Berapa lama (detik) proses boleh ada di state STARTING sebelum kita anggap ONLINE
# jika tidak ada log "ready" yang terdeteksi.
STARTING_GRACE_SECONDS = float(os.getenv("NOY_STARTING_GRACE_SECONDS", "5"))

# Timeout graceful shutdown sebelum backend force-kill proses (detik)
STOP_GRACE_SECONDS = float(os.getenv("NOY_STOP_GRACE_SECONDS", "6"))

# Berapa banyak baris log activity terakhir yang disimpan di memori
LOG_BUFFER_SIZE = int(os.getenv("NOY_LOG_BUFFER_SIZE", "200"))

# File penyimpanan settings Web Control (BUKAN config Noy — lihat README bagian Settings)
SETTINGS_FILE = BASE_DIR / "data" / "settings.json"
