#!/usr/bin/env python3
"""Pipeline'ı web arayüzü olmadan çalıştıran CLI.

Ne işe yarar:
  * Space'i açmadan, yerel Docker içinde hızlı deneme
  * CI'dan tekrarlanabilir build
  * Arayüz bir hatayı gizlediğinde ham çıktıyı görmek

Örnek:
    python3 scripts/build_cli.py \
        --game /work/uploads/MyGame.zip \
        --app-id com.gameport \
        --name "My Game"
"""
from __future__ import annotations

import argparse
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from pipeline.bus import EventBus              # noqa: E402
from pipeline.build import run_build           # noqa: E402
from pipeline.config import BuildConfig, out_dir  # noqa: E402

LEVEL_TAGS = {
    "error": "\033[31mERR \033[0m",
    "warn": "\033[33mWARN\033[0m",
    "ok": "\033[32mOK  \033[0m",
    "cmd": "\033[35m$   \033[0m",
    "out": "    ",
}


class ConsoleBus(EventBus):
    """Olayları doğrudan stdout'a basan bus."""

    def emit(self, event: dict) -> None:
        super().emit(event)
        kind = event.get("type")
        if kind == "log":
            tag = LEVEL_TAGS.get(event.get("level", "info"), "--  ")
            print(f"{tag}{event['line']}", flush=True)
        elif kind == "step":
            print(f"\033[1;36m>>> {event['key']}: {event['status']} "
                  f"{event.get('detail', '')}\033[0m", flush=True)


def main() -> int:
    parser = argparse.ArgumentParser(description="Winlator tek-oyun port build'i")
    parser.add_argument("--game", required=True, help="oyun arşivi ya da klasörü")
    parser.add_argument("--app-id", default="com.gameport",
                        help="applicationId (en fazla 12 karakter)")
    parser.add_argument("--name", default="", help="oyun adı (boşsa otomatik)")
    parser.add_argument("--exec-path", default="", help="exe yolu (boşsa otomatik)")
    parser.add_argument("--box64-preset", default="STABILITY")
    parser.add_argument("--exec-args", default="-force-gfx-direct")
    parser.add_argument("--graphics-driver", default="vortek,gladio")
    parser.add_argument("--winetricks", default="",
                        help="virgülle ayrılmış verb listesi")
    parser.add_argument("--no-auto-redist", action="store_true")
    parser.add_argument("--no-smoke-test", action="store_true")
    parser.add_argument("--no-cleanup", action="store_true")
    parser.add_argument("--zstd-level", type=int, default=19)
    args = parser.parse_args()

    config = BuildConfig(
        app_id=args.app_id,
        game_name=args.name or "Game",
        source_kind="upload",
        upload_path=args.game,
        exec_path_override=args.exec_path,
        box64_preset=args.box64_preset,
        exec_args=args.exec_args,
        graphics_driver=args.graphics_driver,
        winetricks_verbs=[v.strip() for v in args.winetricks.split(",") if v.strip()],
        auto_redist=not args.no_auto_redist,
        run_smoke_test=not args.no_smoke_test,
        prefix_cleanup=not args.no_cleanup,
        zstd_level=args.zstd_level,
    )

    result = run_build(config, ConsoleBus())
    print("\n" + json.dumps(result, indent=2, ensure_ascii=False))
    print(f"\nÇıktılar: {out_dir()}")
    return 0 if result.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
