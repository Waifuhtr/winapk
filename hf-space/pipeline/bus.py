"""Log ve ilerleme yayını.

Pipeline ayrı bir thread'de çalışır, WebSocket ise asyncio loop'unda. Bu
modül ikisini birbirine bağlar: pipeline thread-safe şekilde olay basar,
her WebSocket abonesi kendi asyncio.Queue'sundan okur.

Ayrıca tüm loglar bellekte tutulur ki sayfayı geç açan / yenileyen kullanıcı
build'in başından itibaren her şeyi görebilsin (canlı log + kopyala butonu).
"""
from __future__ import annotations

import asyncio
import json
import os
import threading
import time
from typing import Any

MAX_BUFFERED_LINES = 20000


class EventBus:
    """Olay yayını + isteğe bağlı diske kalıcılık.

    Kalıcılık neden: build arka planda sürerken kullanıcı tarayıcıyı kapatabilir.
    Sadece bellekte tutarsak Space yeniden başladığında (HF konteyneri
    yenileyebilir) tüm log kaybolur ve kullanıcı ne olduğunu göremez.
    Her olay JSONL olarak diske de yazılıyor; sunucu açılışta geri yüklüyor.
    """

    def __init__(self, persist_path: str | None = None) -> None:
        self._lock = threading.Lock()
        self._subscribers: list[tuple[asyncio.AbstractEventLoop, asyncio.Queue]] = []
        self._history: list[dict[str, Any]] = []
        self._seq = 0
        self._persist_path = persist_path
        self._persist_file = None
        if persist_path:
            self._open_persist("a")
            self._load_persisted()

    # ---- kalicilik ----
    def _open_persist(self, mode: str) -> None:
        if not self._persist_path:
            return
        try:
            os.makedirs(os.path.dirname(self._persist_path), exist_ok=True)
            self._persist_file = open(self._persist_path, mode, encoding="utf-8")
        except OSError:
            # Disk yazilamiyorsa build'i durdurmaya deger bir sey degil;
            # sadece kalicilik kapanir.
            self._persist_file = None

    def _load_persisted(self) -> None:
        if not self._persist_path or not os.path.isfile(self._persist_path):
            return
        try:
            with open(self._persist_path, encoding="utf-8") as fh:
                for line in fh:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        event = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    self._history.append(event)
                    self._seq = max(self._seq, int(event.get("seq", 0)))
        except OSError:
            return
        if len(self._history) > MAX_BUFFERED_LINES:
            del self._history[: len(self._history) - MAX_BUFFERED_LINES]

    # ---- abone yönetimi (asyncio tarafı) ----
    def subscribe(self) -> asyncio.Queue:
        loop = asyncio.get_running_loop()
        queue: asyncio.Queue = asyncio.Queue()
        with self._lock:
            self._subscribers.append((loop, queue))
        return queue

    def unsubscribe(self, queue: asyncio.Queue) -> None:
        with self._lock:
            self._subscribers = [s for s in self._subscribers if s[1] is not queue]

    def history(self) -> list[dict[str, Any]]:
        with self._lock:
            return list(self._history)

    # ---- yayın (pipeline thread'i tarafı) ----
    def emit(self, event: dict[str, Any]) -> None:
        with self._lock:
            self._seq += 1
            event = {"seq": self._seq, "ts": time.time(), **event}
            self._history.append(event)
            if len(self._history) > MAX_BUFFERED_LINES:
                # Baştan kırp ama kırpıldığını belli et.
                del self._history[: len(self._history) - MAX_BUFFERED_LINES]
            subscribers = list(self._subscribers)
            if self._persist_file is not None:
                try:
                    self._persist_file.write(json.dumps(event, ensure_ascii=False) + "\n")
                    self._persist_file.flush()
                except (OSError, ValueError):
                    self._persist_file = None

        for loop, queue in subscribers:
            try:
                loop.call_soon_threadsafe(queue.put_nowait, event)
            except RuntimeError:
                # Loop kapanmış — abone zaten ölü, sessizce geç.
                pass

    # ---- kolaylık metotları ----
    def log(self, line: str, level: str = "info") -> None:
        self.emit({"type": "log", "level": level, "line": line})

    def info(self, line: str) -> None:
        self.log(line, "info")

    def warn(self, line: str) -> None:
        self.log(line, "warn")

    def error(self, line: str) -> None:
        self.log(line, "error")

    def ok(self, line: str) -> None:
        self.log(line, "ok")

    def step(self, key: str, status: str, detail: str = "") -> None:
        self.emit({"type": "step", "key": key, "status": status, "detail": detail})

    def progress(self, value: float, label: str = "") -> None:
        self.emit({"type": "progress", "value": max(0.0, min(1.0, value)), "label": label})

    def result(self, payload: dict[str, Any]) -> None:
        self.emit({"type": "result", **payload})

    def reset(self) -> None:
        """Yeni bir build baslarken gecmisi temizler (diskteki dahil)."""
        with self._lock:
            self._history.clear()
            self._seq = 0
            if self._persist_file is not None:
                try:
                    self._persist_file.close()
                except OSError:
                    pass
                self._persist_file = None
        self._open_persist("w")
