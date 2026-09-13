"""
dummy_noy_agent.py — BUKAN bagian dari Noy Agent asli.

Skrip ini HANYA untuk menguji Web Control (start/stop/status/log)
sebelum dihubungkan ke noy-agent Anda yang sesungguhnya. Ia mencetak
baris-baris log yang meniru siklus wake-word -> listening -> processing
-> responding, persis seperti yang dijelaskan di brief, supaya
agent_manager.py bisa diuji end-to-end.

Cara pakai (sementara, untuk testing saja):
1. Di backend/.env, set:
     NOY_AGENT_DIR=<path folder noy-web-control/testing>
     NOY_AGENT_CMD=py -3.12 dummy_noy_agent.py
2. Jalankan Web Control seperti biasa, klik START di browser.
3. Setelah selesai uji coba, KEMBALIKAN NOY_AGENT_DIR/NOY_AGENT_CMD
   ke path Noy Agent asli Anda.
"""
import signal
import sys
import time

_running = True


def _handle_stop(signum, frame):
    global _running
    print("[dummy-noy] Menerima sinyal berhenti, shutting down gracefully...", flush=True)
    _running = False


signal.signal(signal.SIGTERM, _handle_stop)
signal.signal(signal.SIGINT, _handle_stop)

print("[dummy-noy] Engine started", flush=True)
time.sleep(1)
print("[dummy-noy] Noy siap", flush=True)

cycle = 0
try:
    while _running:
        cycle += 1
        print("[dummy-noy] Halo Noy terdeteksi", flush=True)
        time.sleep(1)
        print("[dummy-noy] Listening...", flush=True)
        time.sleep(2)
        print("[dummy-noy] Processing...", flush=True)
        time.sleep(1.5)
        print("[dummy-noy] Responding with answer...", flush=True)
        time.sleep(1.5)
        if cycle >= 500:
            break
except KeyboardInterrupt:
    pass

print("[dummy-noy] Exit.", flush=True)
sys.exit(0)
