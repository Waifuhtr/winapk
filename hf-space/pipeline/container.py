"""Winlator container'ının build makinesinde birebir yeniden üretilmesi.

Android'de ContainerManager.createContainer() şunu yapar:
    1) <rootfs>/home/xuser-<id> dizinini oluşturur
    2) container_pattern.tzst'i oraya açar
    3) copyCommonDlls(): common_dlls.json'a göre
         /opt/wine/lib/wine/x86_64-windows -> .wine/drive_c/windows/system32
         /opt/wine/lib/wine/i386-windows   -> .wine/drive_c/windows/syswow64
    4) activateContainer(): home/xuser -> home/xuser-<id> symlink'i

Burada aynısını yapıyoruz. Böylece elde ettiğimiz "pristine" durum, telefonda
ilk açılışta oluşacak durumun BİREBİR aynısı olur; delta'yı buna göre
çıkarınca telefonda zaten var olan ~300MB'ı tekrar paketlemiyoruz.
"""
from __future__ import annotations

import json
import os
import shutil
from concurrent.futures import ThreadPoolExecutor

from .bus import EventBus
from .config import CONTAINER_ID, COMMON_DLL_MAP, RFS_USER
from .util import cpu_count, dir_size, human, rm_rf, tar_extract


def container_dir(root: str, container_id: int = CONTAINER_ID) -> str:
    return os.path.join(root, "home", f"{RFS_USER}-{container_id}")


def prefix_dir(root: str, container_id: int = CONTAINER_ID) -> str:
    # Android WINEPREFIX'i symlink üzerinden (home/xuser/.wine) kullanır;
    # registry'de aynı yol yazsın diye biz de öyle kullanıyoruz.
    return os.path.join(root, "home", RFS_USER, ".wine")


def create(root: str, assets, bus: EventBus, *, container_id: int = CONTAINER_ID) -> str:
    """container_pattern'ı açar, ortak DLL'leri kopyalar, symlink'i kurar."""
    cdir = container_dir(root, container_id)
    rm_rf(cdir)
    os.makedirs(cdir, exist_ok=True)

    bus.info("container_pattern.tzst açılıyor…")
    tar_extract(assets.require("container_pattern.tzst"), cdir, bus)

    _copy_common_dlls(root, cdir, assets, bus)

    # activateContainer(): home/xuser -> home/xuser-<id>
    link = os.path.join(root, "home", RFS_USER)
    rm_rf(link)
    os.symlink(f"{RFS_USER}-{container_id}", link)
    bus.ok(f"Container hazır: {cdir} ({human(dir_size(cdir))})")
    return cdir


def _copy_common_dlls(root: str, cdir: str, assets, bus: EventBus) -> None:
    with open(assets.require("common_dlls.json"), encoding="utf-8") as fh:
        common = json.load(fh)

    jobs: list[tuple[str, str]] = []
    missing: list[str] = []
    for src_name, dst_name in COMMON_DLL_MAP:
        src_dir = os.path.join(root, "opt/wine/lib/wine", src_name)
        dst_dir = os.path.join(cdir, ".wine/drive_c/windows", dst_name)
        os.makedirs(dst_dir, exist_ok=True)
        for dll in common.get(dst_name, []):
            src = os.path.join(src_dir, dll)
            if os.path.isfile(src):
                jobs.append((src, os.path.join(dst_dir, dll)))
            else:
                missing.append(f"{src_name}/{dll}")

    def _cp(job: tuple[str, str]) -> None:
        shutil.copy2(job[0], job[1])

    with ThreadPoolExecutor(max_workers=cpu_count()) as pool:
        list(pool.map(_cp, jobs, chunksize=32))

    bus.ok(f"copyCommonDlls: {len(jobs)} DLL kopyalandı.")
    if missing:
        bus.warn(
            f"{len(missing)} DLL rootfs'te bulunamadı (ilk 5): "
            + ", ".join(missing[:5])
        )
