"""Winlator kaynak varlıklarının (rootfs / container_pattern / common_dlls) edinilmesi.

Neden repo'dan çekiyoruz da APK'yı parse etmiyoruz: bu dosyalar zaten
winlator-app deposunda düz asset olarak duruyor (app/src/main/assets/).
Pin'li commit kullanıyoruz ki build tekrar edilebilir olsun.
"""
from __future__ import annotations

import os
import shutil

from .bus import EventBus
from .config import (
    ASSET_COMMON_DLLS,
    ASSET_CONTAINER_PATTERN,
    ASSET_ROOTFS,
    WINLATOR_APP_PIN,
    WINLATOR_APP_REPO,
)
from .util import human, run

ASSET_SUBDIR = "app/src/main/assets"
REQUIRED = (ASSET_ROOTFS, ASSET_CONTAINER_PATTERN, ASSET_COMMON_DLLS)
# İsteğe bağlı ama işe yarayanlar: hazır input profilleri ve wincomponents.
OPTIONAL = ("inputcontrols", "wincomponents", "box64")


class WinlatorAssets:
    def __init__(self, asset_dir: str) -> None:
        self.asset_dir = asset_dir

    def path(self, name: str) -> str:
        return os.path.join(self.asset_dir, name)

    def require(self, name: str) -> str:
        p = self.path(name)
        if not os.path.exists(p):
            raise FileNotFoundError(f"Winlator asset'i bulunamadı: {name} ({p})")
        return p


def _has_all_assets(asset_dir: str) -> bool:
    return all(os.path.exists(os.path.join(asset_dir, n)) for n in REQUIRED)


def acquire(cache_root: str, bus: EventBus, *, pin: str = WINLATOR_APP_PIN) -> WinlatorAssets:
    """winlator-app deposundan sadece assets klasörünü indirir (sparse checkout).

    Tam shallow clone ~312MB; sparse + partial clone ile ~75MB'a düşüyor.
    Sunucu partial clone desteklemezse otomatik olarak düz shallow clone'a
    geri düşüyoruz.
    """
    repo_dir = os.path.join(cache_root, "winlator-app")
    asset_dir = os.path.join(repo_dir, ASSET_SUBDIR)

    if _has_all_assets(asset_dir):
        bus.ok(f"Winlator asset'leri önbellekten kullanılıyor: {asset_dir}")
        return WinlatorAssets(asset_dir)

    os.makedirs(cache_root, exist_ok=True)
    if os.path.isdir(repo_dir):
        shutil.rmtree(repo_dir, ignore_errors=True)

    bus.info(f"winlator-app deposu çekiliyor (pin {pin[:10]})…")
    sparse_ok = True
    try:
        run(
            [
                "git", "clone", "--filter=blob:none", "--no-checkout",
                "--depth", "1", WINLATOR_APP_REPO, repo_dir,
            ],
            bus,
        )
        run(["git", "-C", repo_dir, "sparse-checkout", "init", "--cone"], bus)
        run(["git", "-C", repo_dir, "sparse-checkout", "set", ASSET_SUBDIR], bus)
        run(["git", "-C", repo_dir, "checkout"], bus)
    except Exception as exc:  # noqa: BLE001 - fallback yolu bilinçli geniş
        sparse_ok = False
        bus.warn(f"Sparse checkout başarısız ({exc}); düz shallow clone deneniyor.")
        shutil.rmtree(repo_dir, ignore_errors=True)
        run(["git", "clone", "--depth", "1", WINLATOR_APP_REPO, repo_dir], bus)

    if not _has_all_assets(asset_dir):
        raise FileNotFoundError(
            "winlator-app deposu çekildi ama beklenen asset'ler bulunamadı: "
            f"{', '.join(REQUIRED)} — {asset_dir}"
        )

    total = sum(
        os.path.getsize(os.path.join(asset_dir, n))
        for n in REQUIRED
        if os.path.isfile(os.path.join(asset_dir, n))
    )
    bus.ok(
        f"Asset'ler hazır ({'sparse' if sparse_ok else 'full'} clone, "
        f"{human(total)} çekirdek dosya)."
    )
    return WinlatorAssets(asset_dir)
