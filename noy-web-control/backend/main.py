"""
main.py — Entry point Noy Web Control backend.

Jalankan dari folder noy-web-control/ dengan:
    py -3.12 -m uvicorn backend.main:app --host 127.0.0.1 --port 8787 --reload

atau cukup:
    py -3.12 backend/main.py

Endpoint yang tersedia sengaja dibatasi (TIDAK ADA arbitrary execute):
    GET  /api/status
    POST /api/start
    POST /api/stop
    GET  /api/settings
    POST /api/settings
    GET  /api/microphones   (opsional, best-effort)
    WS   /ws/status
"""
import asyncio
from pathlib import Path

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from . import config
from .agent_manager import manager
from . import settings_store

app = FastAPI(title="Noy Web Control", version="0.1.0")

FRONTEND_DIR = Path(__file__).resolve().parent.parent / "frontend"


class SettingsUpdate(BaseModel):
    listening_mode: str | None = None
    push_to_talk_shortcut: str | None = None
    stt_silence_timeout_ms: int | None = None
    microphone: str | None = None
    tts_volume: int | None = None
    debug_mode: bool | None = None


@app.on_event("startup")
async def on_startup():
    manager.bind_loop(asyncio.get_event_loop())


# ---------------------------- REST API ----------------------------

@app.get("/api/status")
def get_status():
    return manager.status()


@app.post("/api/start")
def post_start():
    return manager.start()


@app.post("/api/stop")
def post_stop():
    return manager.stop()


@app.get("/api/settings")
def get_settings():
    return settings_store.load()


@app.post("/api/settings")
def post_settings(update: SettingsUpdate):
    payload = {k: v for k, v in update.model_dump().items() if v is not None}
    return settings_store.save(payload)


@app.get("/api/microphones")
def get_microphones():
    """Best-effort daftar mic. Tidak wajib — kalau sounddevice tidak
    terpasang, kembalikan list kosong tanpa error ke frontend."""
    try:
        import sounddevice as sd  # import lazy & opsional
        devices = sd.query_devices()
        mics = [
            {"index": i, "name": d["name"]}
            for i, d in enumerate(devices)
            if d.get("max_input_channels", 0) > 0
        ]
        return {"available": True, "microphones": mics}
    except Exception:
        return {"available": False, "microphones": []}


# ---------------------------- WebSocket realtime ----------------------------

@app.websocket("/ws/status")
async def ws_status(websocket: WebSocket):
    await websocket.accept()
    queue = manager.subscribe()
    try:
        # kirim snapshot status saat pertama connect
        await websocket.send_json({"type": "status", "data": manager.status()})
        while True:
            payload = await queue.get()
            await websocket.send_json(payload)
    except WebSocketDisconnect:
        pass
    finally:
        manager.unsubscribe(queue)


# ---------------------------- Frontend statis ----------------------------

app.mount("/static", StaticFiles(directory=str(FRONTEND_DIR)), name="static")


@app.get("/")
def index():
    return FileResponse(str(FRONTEND_DIR / "index.html"))


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("backend.main:app", host=config.HOST, port=config.PORT, reload=False)
