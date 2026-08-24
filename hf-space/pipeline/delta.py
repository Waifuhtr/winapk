"""Pristine container'a göre delta çıkarma ve paketleme.

NEDEN DELTA:
    APK zaten container_pattern.tzst (7MB -> 55MB) taşıyor ve container
    oluştururken rootfs'ten 1394 DLL (~280MB) kopyalıyor. Tam prefix'i
    paketlemek bu ~300MB'ı APK'da İKİNCİ KEZ taşımak demek. Bunun yerine
    sadece 'pristine container'dan farklı olan' dosyaları paketliyoruz:
    oyun dosyaları + wineboot sonrası registry + kurulan redistributable'lar.

    Telefonda ilk açılış: container_pattern açılır -> ortak DLL'ler
    kopyalanır -> bu delta üzerine serilir. Sonuç, burada ürettiğimizin
    birebir aynısı olur.
"""
from __future__ import annotations

import json
import os

from .bus import EventBus
from .config import DELTA_EXCLUDES
from .util import hash_tree, human, tar_zstd_create


def _excluded(rel: str) -> bool:
    rel = rel.replace(os.sep, "/")
    return any(rel == e.rstrip("/") or rel.startswith(e) for e in DELTA_EXCLUDES)


def snapshot(container_root: str, bus: EventBus, label: str = "baseline") -> dict[str, str]:
    bus.info(f"{label} manifest'i hesaplanıyor (paralel sha256)…")
    manifest = hash_tree(container_root)
    bus.ok(f"{label}: {len(manifest)} dosya hash'lendi.")
    return manifest


def diff(baseline: dict[str, str], current: dict[str, str]) -> tuple[list[str], list[str]]:
    """(değişen/eklenen, silinen) listelerini döndürür."""
    changed = [
        rel for rel, digest in current.items()
        if not _excluded(rel) and baseline.get(rel) != digest
    ]
    removed = [
        rel for rel in baseline
        if rel not in current and not _excluded(rel)
    ]
    changed.sort()
    removed.sort()
    return changed, removed


def _with_parent_dirs(changed: list[str]) -> list[str]:
    """Değişen dosyaların üst dizinlerini de üye listesine ekler.

    NEDEN ŞART: Winlator'ın TarCompressorUtils.extract() metodu mkdirs()'i
    YALNIZCA dizin girdileri için çağırıyor; normal dosyalarda doğrudan
    FileOutputStream açıyor. Arşivde dizin girdisi yoksa, container'da
    bulunmayan bir klasöre (örn. .wine/drive_c/Games/<Oyun>/) yazarken
    FileNotFoundException alıyor ve extract() false dönüyor.

    hash_tree() yalnızca dosyaları gezdiği için üye listesinde dizin
    olmuyordu; burada ekliyoruz. Sıralama önemli: tar üyeleri verilen
    sırayla işler, bu yüzden dizinler önce ve sözlük sırasında geliyor
    (sözlük sırası üst dizinin alt dizinden önce gelmesini garanti eder).
    """
    directories: set[str] = set()
    for rel in changed:
        parts = rel.replace(os.sep, "/").split("/")[:-1]
        for depth in range(1, len(parts) + 1):
            directories.add("/".join(parts[:depth]))
    return sorted(directories) + changed


def pack(container_root: str, changed: list[str], removed: list[str], out_file: str,
         bus: EventBus, *, level: int = 19) -> dict:
    """Delta'yı game_payload.tzst olarak paketler.

    Arşiv kökü container dizinidir; yani içindeki yollar '.wine/drive_c/...'
    şeklindedir ve doğrudan <rootfs>/home/xuser-1 üzerine açılabilir.
    """
    total_bytes = 0
    for rel in changed:
        full = os.path.join(container_root, rel)
        if os.path.isfile(full) and not os.path.islink(full):
            try:
                total_bytes += os.path.getsize(full)
            except OSError:
                pass

    members = _with_parent_dirs(changed)
    dir_count = len(members) - len(changed)
    bus.info(
        f"Delta paketleniyor: {len(changed)} dosya ({human(total_bytes)} ham) "
        f"+ {dir_count} dizin girdisi, {len(removed)} silinecek."
    )
    tar_zstd_create(container_root, out_file, bus, level=level, members=members)
    packed = os.path.getsize(out_file)
    ratio = (packed / total_bytes * 100) if total_bytes else 0
    bus.ok(f"game_payload.tzst: {human(packed)} (ham verinin %{ratio:.1f}'i)")

    return {
        "changed_count": len(changed),
        "removed_count": len(removed),
        "raw_bytes": total_bytes,
        "packed_bytes": packed,
        "removed": removed,
    }


def write_manifest(path: str, data: dict) -> None:
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(data, fh, indent=2, ensure_ascii=False)


def prefix_cleanup(container_root: str, bus: EventBus) -> list[str]:
    """Telefonda anlamsız olan prefix parçalarını siler.

    Bunlar baseline'da var olduğu için delta'ya 'silinecek' olarak girer ve
    Android tarafı ilk açılışta uygular. Bilinçli olarak muhafazakâr bir
    liste: yardım dosyaları, loglar ve IE gibi tek oyunluk kurulumda hiç
    kullanılmayacak şeyler. Sistem DLL'lerine dokunmuyoruz.
    """
    targets = [
        ".wine/drive_c/windows/help",
        ".wine/drive_c/windows/logs",
        ".wine/drive_c/Program Files/Internet Explorer",
        ".wine/drive_c/Program Files (x86)/Internet Explorer",
        ".wine/drive_c/windows/Installer",
    ]
    removed: list[str] = []
    freed = 0
    for rel in targets:
        full = os.path.join(container_root, rel)
        if not os.path.exists(full):
            continue
        for dirpath, _dirs, files in os.walk(full):
            for name in files:
                fp = os.path.join(dirpath, name)
                try:
                    freed += os.path.getsize(fp)
                except OSError:
                    pass
        import shutil as _shutil
        _shutil.rmtree(full, ignore_errors=True)
        removed.append(rel)
    if removed:
        bus.ok(f"Prefix temizliği: {len(removed)} klasör silindi ({human(freed)} kazanç).")
    return removed
