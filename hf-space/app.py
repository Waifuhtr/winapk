"""Winlator Tek-Oyun Port Aracı — AŞAMA A web sunucusu.

Gradio yerine düz FastAPI + statik HTML/CSS/JS kullanılıyor (kullanıcı talebi):
Gradio'nun log bileşeni uzun canlı çıktılarda yetersiz kalıyor ve kopyalama /
otomatik kaydırma kontrolü vermiyor. Burada loglar WebSocket ile satır satır
akıyor, geçmiş bellekte tutuluyor (sayfa yenilense de kaybolmaz) ve tek tuşla
kopyalanabiliyor.
"""
from __future__ import annotations

import json
import os
import shutil
import threading
import time
from typing import Any

from fastapi import FastAPI, File, Form, HTTPException, UploadFile, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from pipeline import android_build
from pipeline.build import STEPS, safe_run_build
from pipeline.bus import EventBus
from pipeline.config import BAKED_APP_ID, BOX64_PRESETS, BuildConfig, out_dir, upload_dir
from pipeline.util import cpu_count, human

STATIC_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "static")
LOG_PATH = os.path.join(out_dir(), "build_log.jsonl")
RESULT_PATH = os.path.join(out_dir(), "last_result.json")
INTERNAL_FILES = frozenset({"build_log.jsonl", "last_result.json"})

os.makedirs(out_dir(), exist_ok=True)
os.makedirs(upload_dir(), exist_ok=True)

app = FastAPI(title="Winlator Tek-Oyun Port Aracı")
# Kalıcı log: build arka planda sürerken tarayıcı kapatılabilsin, hatta Space
# yeniden başlasa bile geçmiş kaybolmasın.
bus = EventBus(persist_path=LOG_PATH)


def _save_result(result: dict[str, Any] | None) -> None:
    try:
        if result is None:
            if os.path.exists(RESULT_PATH):
                os.remove(RESULT_PATH)
            return
        with open(RESULT_PATH, "w", encoding="utf-8") as fh:
            json.dump(result, fh, ensure_ascii=False)
    except OSError:
        pass


def _load_result() -> dict[str, Any] | None:
    try:
        with open(RESULT_PATH, encoding="utf-8") as fh:
            return json.load(fh)
    except (OSError, json.JSONDecodeError):
        return None


class BuildState:
    """Aynı anda tek build çalışsın; durumu paylaşılan tek yerde tut."""

    def __init__(self) -> None:
        self.lock = threading.Lock()
        self.thread: threading.Thread | None = None
        self.running = False
        self.cancel_requested = False
        self.last_result: dict[str, Any] | None = None
        self.started_at: float = 0.0
        self.uploaded: dict[str, Any] | None = None

    def is_running(self) -> bool:
        return self.running and self.thread is not None and self.thread.is_alive()


state = BuildState()
state.last_result = _load_result()


# --------------------------------------------------------------------- statik
@app.get("/", response_class=HTMLResponse)
async def index() -> HTMLResponse:
    with open(os.path.join(STATIC_DIR, "index.html"), encoding="utf-8") as fh:
        return HTMLResponse(fh.read())


app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")


# ------------------------------------------------------------------------ API
@app.get("/api/state")
async def api_state() -> JSONResponse:
    defaults = BuildConfig()
    return JSONResponse({
        "running": state.is_running(),
        "lastResult": state.last_result,
        "uploaded": state.uploaded,
        "steps": [{"key": k, "label": v} for k, v in STEPS],
        "defaults": defaults.to_dict(),
        "bakedAppId": BAKED_APP_ID,
        "appIdMaxLength": len(BAKED_APP_ID),
        "box64Presets": list(BOX64_PRESETS),
        "artifacts": _list_artifacts(),
        "host": {
            "cpus": cpu_count(),
            "hasDatasetToken": bool(
                os.environ.get("HF_TOKEN") or os.environ.get("HUGGING_FACE_HUB_TOKEN")
            ),
            "androidReady": _android_ready(),
        },
        "elapsed": round(time.time() - state.started_at, 1) if state.is_running() else 0,
    })


@app.post("/api/upload")
async def api_upload(file: UploadFile = File(...)) -> JSONResponse:
    if state.is_running():
        raise HTTPException(409, "Build çalışırken yükleme yapılamaz.")

    up = upload_dir()
    os.makedirs(up, exist_ok=True)
    # Tek oyunluk araç: her yükleme öncekini geçersiz kılar, disk şişmesin.
    for name in os.listdir(up):
        try:
            os.remove(os.path.join(up, name))
        except OSError:
            pass

    safe_name = os.path.basename(file.filename or "game.zip")
    dest = os.path.join(up, safe_name)
    size = 0
    with open(dest, "wb") as out:
        while True:
            chunk = await file.read(1 << 22)     # 4MB
            if not chunk:
                break
            out.write(chunk)
            size += len(chunk)

    state.uploaded = {"name": safe_name, "path": dest, "size": size, "human": human(size)}
    bus.ok(f"Oyun arşivi yüklendi: {safe_name} ({human(size)})")
    return JSONResponse(state.uploaded)


@app.post("/api/build")
async def api_build(payload: dict[str, Any]) -> JSONResponse:
    with state.lock:
        if state.is_running():
            raise HTTPException(409, "Zaten çalışan bir build var.")

        try:
            cfg = _config_from_payload(payload)
        except (ValueError, FileNotFoundError) as exc:
            raise HTTPException(400, str(exc)) from exc

        bus.reset()
        state.cancel_requested = False
        state.last_result = None
        _save_result(None)
        state.running = True
        state.started_at = time.time()

        bus.info("=" * 68)
        bus.info(f"Winlator tek-oyun port build'i başlıyor — {cfg.game_name}")
        bus.info(f"applicationId: {cfg.app_id} ({len(cfg.app_id)} karakter)")
        bus.info(f"Box64 preset: {cfg.box64_preset} | exec args: {cfg.exec_args or '(yok)'}")
        bus.info(f"Host: {cpu_count()} vCPU")
        bus.info("=" * 68)

        def _worker() -> None:
            try:
                result = safe_run_build(cfg, bus, lambda: state.cancel_requested)
                state.last_result = result
                _save_result(result)
            finally:
                state.running = False

        state.thread = threading.Thread(target=_worker, name="build", daemon=True)
        state.thread.start()

    return JSONResponse({"started": True})


@app.post("/api/cancel")
async def api_cancel() -> JSONResponse:
    if not state.is_running():
        return JSONResponse({"cancelled": False, "reason": "çalışan build yok"})
    state.cancel_requested = True
    bus.warn("İptal istendi; mevcut adım bitince duracak.")
    return JSONResponse({"cancelled": True})


@app.get("/api/artifacts")
async def api_artifacts() -> JSONResponse:
    return JSONResponse({"artifacts": _list_artifacts()})


@app.get("/api/download/{name}")
async def api_download(name: str) -> FileResponse:
    safe = os.path.basename(name)
    path = os.path.join(out_dir(), safe)
    if not os.path.isfile(path):
        raise HTTPException(404, f"Dosya yok: {safe}")
    return FileResponse(path, filename=safe, media_type="application/octet-stream")


@app.websocket("/ws")
async def ws(socket: WebSocket) -> None:
    await socket.accept()
    queue = bus.subscribe()
    try:
        # Geç bağlanan istemciye önce geçmişi ver — log hiç kaybolmasın.
        history = bus.history()
        for i in range(0, len(history), 400):
            await socket.send_json({"type": "batch", "events": history[i : i + 400]})
        await socket.send_json({"type": "synced", "running": state.is_running()})
        while True:
            event = await queue.get()
            await socket.send_json(event)
    except WebSocketDisconnect:
        pass
    except Exception:  # noqa: BLE001 - kopan soket build'i etkilemesin
        pass
    finally:
        bus.unsubscribe(queue)


# ------------------------------------------------------------------- yardımcı
def _android_ready() -> tuple[bool, str]:
    """APK derleme ortamı hazır mı? Arayüz bunu kullanıcıya baştan söylüyor."""
    try:
        android_build.preflight(_SilentBus())
        return (True, "")
    except Exception as exc:  # noqa: BLE001 - mesajı UI'a taşımak istiyoruz
        return (False, str(exc))


class _SilentBus(EventBus):
    """preflight'ın log basmadan çalıştırılması için."""

    def emit(self, event: dict[str, Any]) -> None:
        return


def _list_artifacts() -> list[dict[str, Any]]:
    od = out_dir()
    if not os.path.isdir(od):
        return []
    items = []
    for name in sorted(os.listdir(od)):
        path = os.path.join(od, name)
        # İç dosyalar çıktı listesinde görünmesin: build_log.jsonl makine
        # formatı (arayüzün kendi "İndir" düğmesi zaten düz metin veriyor),
        # last_result.json ve .filelist ise sadece durum tutma amaçlı.
        if not os.path.isfile(path) or name in INTERNAL_FILES or name.endswith(".filelist"):
            continue
        size = os.path.getsize(path)
        items.append({
            "name": name,
            "size": size,
            "human": human(size),
            "mtime": os.path.getmtime(path),
        })
    # APK en üstte, sonra zip — kullanıcının asıl indireceği bunlar.
    def rank(item: dict[str, Any]) -> tuple[int, str]:
        name = item["name"]
        if name.endswith(".apk"):
            return (0, name)
        if name.endswith(".zip"):
            return (1, name)
        return (2, name)

    items.sort(key=rank)
    return items


def _config_from_payload(payload: dict[str, Any]) -> BuildConfig:
    cfg = BuildConfig()
    for key, value in (payload or {}).items():
        if not hasattr(cfg, key):
            continue
        current = getattr(cfg, key)
        if isinstance(current, bool):
            setattr(cfg, key, bool(value))
        elif isinstance(current, int) and not isinstance(current, bool):
            setattr(cfg, key, int(value))
        elif isinstance(current, list):
            if isinstance(value, str):
                value = [v.strip() for v in value.split(",") if v.strip()]
            setattr(cfg, key, list(value or []))
        else:
            setattr(cfg, key, "" if value is None else str(value))

    if cfg.source_kind == "upload":
        if not state.uploaded:
            raise FileNotFoundError("Önce bir oyun arşivi yükle.")
        cfg.upload_path = state.uploaded["path"]
    return cfg.normalized()


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(
        app,
        host="0.0.0.0",
        port=int(os.environ.get("PORT", "7860")),
        log_level="info",
        ws_ping_interval=20,
        ws_ping_timeout=20,
    )
