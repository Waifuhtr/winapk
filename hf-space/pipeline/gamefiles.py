"""Oyun arşivinin açılması ve prefix içine yerleştirilmesi."""
from __future__ import annotations

import os
import shutil

from .bus import EventBus
from .util import dir_size, human, rm_rf, run

GAMES_DIR_C = "Games"          # -> C:\Games\<OyunAdi>


def extract_archive(archive: str, dest: str, bus: EventBus) -> str:
    """zip / 7z / tar.* arşivini açar; klasör verilirse olduğu gibi kullanır."""
    rm_rf(dest)
    os.makedirs(dest, exist_ok=True)

    if os.path.isdir(archive):
        bus.info("Kaynak bir klasör; kopyalanıyor…")
        shutil.copytree(archive, dest, symlinks=False, dirs_exist_ok=True)
    else:
        low = archive.lower()
        bus.info(f"Oyun arşivi açılıyor: {os.path.basename(archive)}")
        if low.endswith(".zip"):
            run(["unzip", "-q", "-o", archive, "-d", dest], bus, quiet=True)
        elif low.endswith(".7z"):
            run(["7z", "x", "-y", f"-o{dest}", archive], bus, quiet=True)
        elif low.endswith((".tar", ".tar.gz", ".tgz", ".tar.xz", ".tar.zst", ".tzst", ".tar.bz2")):
            run(["bsdtar", "-xf", archive, "-C", dest], bus, quiet=True)
        else:
            # Bilinmeyen uzantı: bsdtar çoğu formatı otomatik tanır.
            bus.warn(f"Bilinmeyen arşiv uzantısı; bsdtar ile deneniyor: {archive}")
            run(["bsdtar", "-xf", archive, "-C", dest], bus, quiet=True)

    root = _strip_single_wrapper(dest)
    bus.ok(f"Oyun dosyaları açıldı ({human(dir_size(root))}).")
    return root


def _strip_single_wrapper(path: str) -> str:
    """Zip içinde tek bir sarmalayıcı klasör varsa onun içine iner.

    'MyGame.zip -> MyGame/MyGame.exe' düzeni çok yaygın; exe tespitini
    kolaylaştırmak için tek çocuk klasörleri geçiyoruz.
    """
    current = path
    for _ in range(4):
        try:
            entries = os.listdir(current)
        except OSError:
            break
        visible = [e for e in entries if not e.startswith("__MACOSX")]
        if len(visible) == 1 and os.path.isdir(os.path.join(current, visible[0])):
            current = os.path.join(current, visible[0])
        else:
            break
    return current


def install_into_prefix(game_root: str, prefix_root: str, game_name: str,
                        bus: EventBus) -> tuple[str, str]:
    """Oyunu C:\\Games\\<OyunAdi> altına taşır.

    Dönüş: (unix yolu, DOS yolu)
    """
    safe = "".join(c for c in game_name if c.isalnum() or c in " _-").strip() or "Game"
    dest = os.path.join(prefix_root, "drive_c", GAMES_DIR_C, safe)
    rm_rf(dest)
    os.makedirs(os.path.dirname(dest), exist_ok=True)
    shutil.copytree(game_root, dest, symlinks=False)

    dos_path = f"C:\\{GAMES_DIR_C}\\{safe}"
    bus.ok(f"Oyun yerleştirildi: {dos_path} ({human(dir_size(dest))})")
    return dest, dos_path
