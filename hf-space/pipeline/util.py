"""Ortak yardımcılar: komut çalıştırma, hash, arşivleme."""
from __future__ import annotations

import hashlib
import os
import shutil
import subprocess
import threading
from concurrent.futures import ThreadPoolExecutor
from typing import Callable, Iterable, Sequence

from .bus import EventBus


class CommandError(RuntimeError):
    def __init__(self, cmd: Sequence[str], code: int, tail: str) -> None:
        super().__init__(f"Komut başarısız (exit {code}): {' '.join(cmd)}\n{tail}")
        self.cmd = list(cmd)
        self.code = code
        self.tail = tail


def cpu_count() -> int:
    return max(1, os.cpu_count() or 1)


def run(
    cmd: Sequence[str],
    bus: EventBus,
    *,
    cwd: str | None = None,
    env: dict[str, str] | None = None,
    timeout: int | None = None,
    check: bool = True,
    prefix: str = "",
    quiet: bool = False,
) -> tuple[int, str]:
    """Komutu çalıştırır, çıktısını satır satır bus'a akıtır.

    Canlı log gereksinimi yüzünden çıktıyı biriktirip sonda basmıyoruz;
    her satır anında yayınlanır ki hata anında kullanıcı görebilsin.
    """
    shown = " ".join(cmd)
    if len(shown) > 400:
        shown = shown[:400] + " …"
    bus.log(f"$ {shown}", "cmd")

    full_env = dict(os.environ)
    if env:
        full_env.update(env)

    lines: list[str] = []
    proc = subprocess.Popen(
        list(cmd),
        cwd=cwd,
        env=full_env,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        errors="replace",
        bufsize=1,
    )

    timed_out = threading.Event()

    def _kill_on_timeout() -> None:
        timed_out.set()
        proc.kill()

    timer = threading.Timer(timeout, _kill_on_timeout) if timeout else None
    if timer:
        timer.daemon = True
        timer.start()

    try:
        assert proc.stdout is not None
        for raw in proc.stdout:
            line = raw.rstrip("\n")
            lines.append(line)
            if not quiet:
                bus.log(f"{prefix}{line}", "out")
        code = proc.wait()
    finally:
        if timer:
            timer.cancel()

    output = "\n".join(lines)
    if timed_out.is_set():
        bus.warn(f"Komut {timeout}s zaman aşımına uğradı ve sonlandırıldı.")
        return -9, output

    if check and code != 0:
        raise CommandError(cmd, code, "\n".join(lines[-40:]))
    return code, output


def sha256_file(path: str, chunk: int = 1 << 20) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        while True:
            block = fh.read(chunk)
            if not block:
                break
            h.update(block)
    return h.hexdigest()


def hash_tree(root: str, *, workers: int | None = None) -> dict[str, str]:
    """Dizin ağacının {göreli yol: sha256} manifest'ini üretir.

    Symlink'ler içerik yerine hedefleriyle kaydedilir (kopyalarken de öyle
    davranıyoruz). 8 vCPU'yu kullanmak için paralel hash'liyoruz — ~280MB
    DLL ağacı bu sayede birkaç saniyede bitiyor.
    """
    entries: list[tuple[str, str]] = []
    links: dict[str, str] = {}

    for dirpath, dirnames, filenames in os.walk(root):
        for name in list(dirnames):
            full = os.path.join(dirpath, name)
            if os.path.islink(full):
                dirnames.remove(name)
                rel = os.path.relpath(full, root)
                links[rel] = "link:" + os.readlink(full)
        for name in filenames:
            full = os.path.join(dirpath, name)
            rel = os.path.relpath(full, root)
            if os.path.islink(full):
                links[rel] = "link:" + os.readlink(full)
            else:
                entries.append((rel, full))

    manifest: dict[str, str] = dict(links)
    with ThreadPoolExecutor(max_workers=workers or cpu_count()) as pool:
        for rel, digest in zip(
            (e[0] for e in entries),
            pool.map(lambda e: sha256_file(e[1]), entries, chunksize=32),
        ):
            manifest[rel] = digest
    return manifest


def copy_tree_into(src: str, dst: str) -> None:
    """src içeriğini dst içine kopyalar (symlink'leri koruyarak)."""
    os.makedirs(dst, exist_ok=True)
    for name in os.listdir(src):
        s = os.path.join(src, name)
        d = os.path.join(dst, name)
        if os.path.isdir(s) and not os.path.islink(s):
            shutil.copytree(s, d, symlinks=True, dirs_exist_ok=True)
        else:
            if os.path.lexists(d):
                os.remove(d)
            shutil.copy2(s, d, follow_symlinks=False)


def human(size: float) -> str:
    for unit in ("B", "KB", "MB", "GB"):
        if size < 1024 or unit == "GB":
            return f"{size:.1f} {unit}" if unit != "B" else f"{int(size)} B"
        size /= 1024
    return f"{size:.1f} GB"


def dir_size(path: str) -> int:
    total = 0
    for dirpath, _dirnames, filenames in os.walk(path):
        for name in filenames:
            full = os.path.join(dirpath, name)
            if not os.path.islink(full):
                try:
                    total += os.path.getsize(full)
                except OSError:
                    pass
    return total


def tar_zstd_create(
    src_dir: str,
    out_file: str,
    bus: EventBus,
    *,
    level: int = 19,
    members: Iterable[str] | None = None,
    threads: int | None = None,
) -> None:
    """Dizini .tzst olarak paketler.

    Android tarafı bunu com.github.luben:zstd-jni + commons-compress ile
    açıyor; standart zstd tar akışı uyumludur. -T0 ile tüm çekirdekler
    kullanılır (Space 8 vCPU).
    """
    os.makedirs(os.path.dirname(out_file) or ".", exist_ok=True)
    nthreads = threads if threads is not None else cpu_count()
    zstd_opts = f"-{level} -T{nthreads} --long=27"
    cmd = ["tar", "-I", f"zstd {zstd_opts}", "-cf", out_file, "-C", src_dir]
    if members is None:
        cmd.append(".")
    else:
        listing = out_file + ".filelist"
        with open(listing, "w", encoding="utf-8") as fh:
            for m in members:
                fh.write(m + "\n")
        cmd += ["--no-recursion", "-T", listing]
    run(cmd, bus, quiet=True)
    if members is not None:
        try:
            os.remove(out_file + ".filelist")
        except OSError:
            pass


def tar_extract(archive: str, dest: str, bus: EventBus) -> None:
    os.makedirs(dest, exist_ok=True)
    run(["tar", "--zstd", "-xf", archive, "-C", dest], bus, quiet=True)


def rm_rf(path: str) -> None:
    if os.path.islink(path) or os.path.isfile(path):
        try:
            os.remove(path)
        except OSError:
            pass
    elif os.path.isdir(path):
        shutil.rmtree(path, ignore_errors=True)
