"""rootfs.tzst'in açılması, applicationId yamalanması ve yeniden paketlenmesi.

KRİTİK BİLGİ (doğrulandı, varsayım değil):
    Winlator'ın Wine binary'leri ve rootfs'teki 165 dosya, mutlak yolu
    '/data/data/com.winlator/files/rootfs' olarak DERLENMİŞ halde taşır
    (toplam 447 tekrar; ntdll.so ve wineserver dahil). TMPDIR gibi env
    değişkenleri bunu EZMEZ — denendi, wineserver yine gömülü yolu aradı.

    Bu yüzden iki şey şart:
      1) Build sırasında o yolun gerçekten var olması (symlink yeterli).
      2) Farklı bir applicationId isteniyorsa, rootfs'teki tüm tekrarların
         AYNI UZUNLUKTA yeni değerle değiştirilmesi.

    Aynı uzunlukta değişim offset kaydırmadığı için ELF/ld.so.cache gibi
    ofset tablolu dosyalarda bile güvenlidir. 'com.gameport' ile test edildi:
    wineboot -u ve wine cmd sorunsuz çalıştı.
"""
from __future__ import annotations

import os
from concurrent.futures import ThreadPoolExecutor

from .bus import EventBus
from .config import BAKED_APP_ID
from .util import cpu_count, dir_size, human, rm_rf, run, tar_extract, tar_zstd_create


def android_files_dir(app_id: str) -> str:
    return f"/data/data/{app_id}/files"


def android_rootfs_path(app_id: str) -> str:
    return f"{android_files_dir(app_id)}/rootfs"


def extract(archive: str, dest: str, bus: EventBus) -> str:
    """rootfs.tzst'i hedefe açar."""
    rm_rf(dest)
    os.makedirs(dest, exist_ok=True)
    bus.info(f"rootfs.tzst açılıyor → {dest}")
    tar_extract(archive, dest, bus)
    bus.ok(f"rootfs açıldı ({human(dir_size(dest))}).")
    return dest


def _patch_one(path: str, old: bytes, new: bytes) -> int:
    try:
        if os.path.islink(path) or not os.path.isfile(path):
            return 0
        with open(path, "rb") as fh:
            data = fh.read()
    except OSError:
        return 0
    count = data.count(old)
    if not count:
        return 0
    st = os.stat(path)
    with open(path, "wb") as fh:
        fh.write(data.replace(old, new))
    os.chmod(path, st.st_mode)
    return count


def patch_app_id(root: str, app_id: str, bus: EventBus) -> tuple[int, int]:
    """rootfs içindeki gömülü applicationId'yi değiştirir.

    Dönüş: (yamalanan dosya sayısı, değiştirilen tekrar sayısı)
    """
    if app_id == BAKED_APP_ID:
        bus.info("applicationId com.winlator olarak bırakıldı; yama gerekmiyor.")
        return (0, 0)

    old = BAKED_APP_ID.encode()
    new = app_id.encode()
    if len(new) > len(old):
        raise ValueError(
            f"applicationId çok uzun ({len(new)} > {len(old)}). "
            "Bu byte-patch offset kaydırırdı."
        )
    if len(new) < len(old):
        # NUL padding: yol string'leri null-terminated olduğu için güvenli,
        # ama tam uzunluk kadar garantili değil — kullanıcıyı uyar.
        new = new + b"\x00" * (len(old) - len(new))
        bus.warn(
            f"applicationId {len(app_id)} karakter; {len(old)} karaktere NUL ile "
            "tamamlanıyor. Tam uzunluk kullanmak daha güvenlidir."
        )

    bus.info(f"rootfs'te '{BAKED_APP_ID}' → '{app_id}' yamalanıyor…")
    targets: list[str] = []
    for dirpath, _dirnames, filenames in os.walk(root):
        for name in filenames:
            targets.append(os.path.join(dirpath, name))

    with ThreadPoolExecutor(max_workers=cpu_count()) as pool:
        counts = list(pool.map(lambda p: _patch_one(p, old, new), targets, chunksize=64))

    files = sum(1 for c in counts if c)
    total = sum(counts)
    if total == 0:
        raise RuntimeError(
            "rootfs içinde gömülü applicationId bulunamadı. Winlator sürümü "
            "beklenenden farklı olabilir — pin'i ve BAKED_APP_ID'yi doğrula."
        )
    bus.ok(f"{files} dosyada {total} tekrar yamalandı.")
    return (files, total)


def link_android_path(real_root: str, app_id: str, bus: EventBus) -> str:
    """/data/data/<appId>/files/rootfs → gerçek dizin symlink'ini kurar.

    Gerçek veriyi /data altına koymuyoruz çünkü HF Spaces kalıcı diski oraya
    mount ediyor; sadece minik bir symlink bırakıyoruz. Wine'ın symlink
    üzerinden çalıştığı test edildi.
    """
    files_dir = android_files_dir(app_id)
    link = android_rootfs_path(app_id)
    os.makedirs(files_dir, exist_ok=True)
    if os.path.islink(link) or os.path.exists(link):
        rm_rf(link)
    os.symlink(os.path.abspath(real_root), link)
    bus.ok(f"Android yolu bağlandı: {link} → {real_root}")
    return link


def repack(root: str, out_file: str, bus: EventBus, *, level: int = 19) -> str:
    """Yamalanmış rootfs'i tekrar rootfs.tzst olarak paketler."""
    bus.info(f"rootfs yeniden paketleniyor (zstd -{level}, {cpu_count()} thread)…")
    rm_rf(out_file)
    tar_zstd_create(root, out_file, bus, level=level)
    bus.ok(f"rootfs.tzst hazır: {human(os.path.getsize(out_file))}")
    return out_file
