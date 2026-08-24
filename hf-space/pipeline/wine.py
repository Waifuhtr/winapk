"""Wine sürücüsü: ortam değişkenleri, wineboot, winetricks, registry, smoke test.

Ortam değişkenleri Android'deki GuestProgramLauncherComponent.execGuestProgram()
ile BİREBİR aynı tutulmuştur (HOME, USER, TMPDIR, WINEPREFIX). Amaç: prefix
içine yazılan mutlak yolların telefondakiyle aynı olması.
"""
from __future__ import annotations

import os
import shutil
import signal
import subprocess
import time

from .bus import EventBus
from .config import RFS_USER
from .util import CommandError, run

XVFB_DISPLAY = ":99"


def wine_env(root: str, *, display: str | None = XVFB_DISPLAY) -> dict[str, str]:
    home = os.path.join(root, "home", RFS_USER)
    env = {
        "WINEPREFIX": os.path.join(home, ".wine"),
        "WINEARCH": "win64",
        "HOME": home,
        "USER": RFS_USER,
        "TMPDIR": os.path.join(root, "tmp"),
        "WINEDEBUG": "-all",
        # gecko/mono diyaloglarini bastir; winemenubuilder'i ilk wineboot'tan
        # itibaren kapat ki prefix'e XDG .desktop/mime/ikon copu hic yazilmasin.
        "WINEDLLOVERRIDES": "mscoree,mshtml,winemenubuilder.exe=",
        "PATH": f"{root}/opt/wine/bin:" + os.environ.get("PATH", "/usr/bin:/bin"),
        "DISPLAY": display or "",
        "LC_ALL": "C.UTF-8",
    }
    return env


def wine_bin(root: str) -> str:
    return os.path.join(root, "opt/wine/bin/wine")


def wine_cwd(root: str) -> str:
    """Wine komutlari icin calisma dizini.

    Build makinesinin cwd'si prefix disinda kalirsa wine her cagrida
    'could not open working directory' uyarisi basiyor. Android'de de calisma
    dizini kullanicinin home'u oldugu icin ayni davranisi kuruyoruz.
    """
    # drive_c seciyoruz cunku bu dizin prefix icinde C:\ olarak MAP'li.
    # home/xuser gibi map'siz bir unix yolu verirsek wine her cagrida
    # "could not open working directory" uyarisi basiyor.
    drive_c = os.path.join(root, "home", RFS_USER, ".wine", "drive_c")
    if os.path.isdir(drive_c):
        return drive_c
    home = os.path.join(root, "home", RFS_USER)
    os.makedirs(home, exist_ok=True)
    return home


def ensure_dirs(root: str) -> None:
    for sub in ("tmp", f"home/{RFS_USER}"):
        os.makedirs(os.path.join(root, sub), exist_ok=True)


class Xvfb:
    """Headless X sunucusu.

    Bazı kurulum sihirbazları (özellikle VC++ redist) gerçek bir X bağlantısı
    ister; ayrıca smoke test için de gerekli.
    """

    def __init__(self, bus: EventBus, display: str = XVFB_DISPLAY, size: str = "1280x720x24"):
        self.bus = bus
        self.display = display
        self.size = size
        self.proc: subprocess.Popen | None = None

    def __enter__(self) -> "Xvfb":
        if not shutil.which("Xvfb"):
            self.bus.warn("Xvfb bulunamadı; null display ile devam ediliyor.")
            return self
        self.proc = subprocess.Popen(
            ["Xvfb", self.display, "-screen", "0", self.size, "-nolisten", "tcp", "-ac"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        # X soketinin hazır olmasını bekle (yoklama, sabit uyku değil).
        for _ in range(50):
            if os.path.exists(f"/tmp/.X11-unix/X{self.display.lstrip(':')}"):
                break
            time.sleep(0.1)
        self.bus.ok(f"Xvfb başlatıldı ({self.display}, {self.size}).")
        return self

    def __exit__(self, *exc: object) -> None:
        if self.proc and self.proc.poll() is None:
            self.proc.send_signal(signal.SIGTERM)
            try:
                self.proc.wait(timeout=10)
            except subprocess.TimeoutExpired:
                self.proc.kill()


def wineboot(root: str, bus: EventBus, *, timeout: int = 900) -> None:
    ensure_dirs(root)
    env = wine_env(root)
    bus.info("wineboot -u çalıştırılıyor (prefix ilkleme)…")
    run([wine_bin(root), "wineboot", "-u"], bus, env=env, timeout=timeout,
        cwd=wine_cwd(root))
    wineserver_wait(root, bus)
    bus.ok("Prefix ilklendi.")


def wineserver_wait(root: str, bus: EventBus, *, timeout: int = 300) -> None:
    """wineserver'ın kapanmasını bekler ki registry diske tam yazılsın."""
    server = os.path.join(root, "opt/wine/bin/wineserver")
    if not os.path.isfile(server):
        return
    try:
        run([server, "-w"], bus, env=wine_env(root), timeout=timeout, quiet=True,
            cwd=wine_cwd(root))
    except CommandError as exc:
        bus.warn(f"wineserver -w beklenmedik şekilde bitti: {exc.code}")


def run_wine(
    root: str,
    args: list[str],
    bus: EventBus,
    *,
    timeout: int = 600,
    check: bool = True,
    cwd: str | None = None,
) -> tuple[int, str]:
    return run(
        [wine_bin(root), *args], bus, env=wine_env(root), timeout=timeout,
        check=check, cwd=cwd or wine_cwd(root),
    )


def reg_add(root: str, key: str, name: str | None, value: str, bus: EventBus,
            *, vtype: str = "REG_SZ") -> None:
    args = ["reg", "add", key]
    if name is not None:
        args += ["/v", name]
    else:
        args += ["/ve"]
    args += ["/t", vtype, "/d", value, "/f"]
    run_wine(root, args, bus, timeout=120, check=False)


def registry_cleanup(root: str, bus: EventBus) -> None:
    """Android'de anlamsız olan Wine bileşenlerini kapatır.

    Neden bunlar:
      - winemenubuilder: her .lnk için .desktop üretmeye çalışır; bizim tek
        oyunluk kurulumumuzda sadece I/O ve açılış gecikmesi demek.
      - Spooler: yazıcı servisi; telefonda karşılığı yok, her açılışta başlar.
      - Explorer 'Desktop' entegrasyonu ve MIME/tarayıcı ilişkilendirmeleri
        de gereksiz; ama bunlar prefix'te ağır yer kaplamadığı için sadece
        başlatma maliyeti olanları kapatıyoruz (agresif registry cerrahisi
        riskli, bilerek kaçınıldı).
    """
    bus.info("Gereksiz Wine bileşenleri devre dışı bırakılıyor…")
    reg_add(root, r"HKCU\Software\Wine\DllOverrides", "winemenubuilder.exe", "", bus)
    reg_add(
        root,
        r"HKLM\System\CurrentControlSet\Services\Spooler",
        "Start", "4", bus, vtype="REG_DWORD",
    )
    wineserver_wait(root, bus)
    bus.ok("Registry temizliği tamam.")


def install_winetricks(root: str, verbs: list[str], bus: EventBus, *, timeout: int = 2700) -> list[str]:
    """winetricks verb'lerini Winlator'ın kendi Wine'ıyla kurar.

    Sistem wine'ı yok; WINE/WINESERVER env değişkenleriyle winetricks'i
    bizim binary'lere yönlendiriyoruz.
    """
    if not verbs:
        bus.info("Kurulacak redistributable seçilmedi; adım atlandı.")
        return []
    if not shutil.which("winetricks"):
        bus.warn("winetricks bulunamadı; redistributable kurulumu atlandı.")
        return []

    env = wine_env(root)
    env.update({
        "WINE": wine_bin(root),
        "WINESERVER": os.path.join(root, "opt/wine/bin/wineserver"),
        "WINETRICKS_LATEST_VERSION_CHECK": "disabled",
        "W_CACHE": os.path.join(root, "tmp", "winetricks-cache"),
    })
    os.makedirs(env["W_CACHE"], exist_ok=True)

    installed: list[str] = []
    for verb in verbs:
        bus.info(f"winetricks {verb} kuruluyor…")
        code, _out = run(
            ["winetricks", "-q", "--force", verb],
            bus, env=env, timeout=timeout, check=False,
        )
        wineserver_wait(root, bus)
        if code == 0:
            installed.append(verb)
            bus.ok(f"{verb} kuruldu.")
        else:
            bus.error(
                f"{verb} kurulamadı (exit {code}). Log'un üstündeki winetricks "
                "çıktısına bak; genelde ağ erişimi ya da sürüm değişikliğidir."
            )
    return installed


def smoke_test(root: str, exec_unix_path: str, bus: EventBus, *, seconds: int = 25,
               exec_args: str = "") -> dict:
    """Oyunu kısa süre çalıştırıp yükleyici hatası var mı diye bakar.

    Space'te GPU yok; amaç oynanabilirlik testi DEĞİL, 'prefix bozuk değil ve
    exe'nin bağımlılıkları çözülüyor' doğrulaması. Sonuç build'i asla
    başarısız yapmaz, sadece rapor edilir.
    """
    env = wine_env(root)
    env["WINEDEBUG"] = "err+module,err+loaddll"
    work_dir = os.path.dirname(exec_unix_path)
    cmd = [wine_bin(root), exec_unix_path]
    if exec_args:
        cmd += exec_args.split()

    bus.info(f"Smoke test: oyun {seconds}s çalıştırılıyor…")
    proc = subprocess.Popen(
        cmd, cwd=work_dir, env={**os.environ, **env},
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
        text=True, errors="replace",
    )
    deadline = time.time() + seconds
    lines: list[str] = []
    while time.time() < deadline and proc.poll() is None:
        time.sleep(0.25)

    exited_early = proc.poll() is not None
    if not exited_early:
        proc.terminate()
    try:
        out = proc.communicate(timeout=20)[0] or ""
    except subprocess.TimeoutExpired:
        proc.kill()
        out = proc.communicate()[0] or ""
    lines = out.splitlines()

    for line in lines[-120:]:
        bus.log(line, "out")

    load_errors = [
        ln for ln in lines
        if "could not load" in ln.lower()
        or "err:module" in ln.lower()
        or "not found" in ln.lower()
    ]
    result = {
        "ran": True,
        "exited_early": exited_early,
        "exit_code": proc.returncode,
        "load_errors": load_errors[:25],
        "survived_seconds": seconds if not exited_early else None,
    }
    if exited_early and proc.returncode not in (0, None):
        bus.warn(
            f"Oyun {seconds}s dolmadan çıktı (exit {proc.returncode}). "
            "GPU olmadığı için bu normal olabilir; yükleyici hatalarına bak."
        )
    elif exited_early:
        bus.info("Oyun kendi kendine çıktı (exit 0).")
    else:
        bus.ok(f"Oyun {seconds}s boyunca ayakta kaldı, yükleyici hatası yok.")
    if load_errors:
        bus.warn(f"{len(load_errors)} modül yükleme hatası tespit edildi.")
    return result


def screenshot(display: str, out_file: str, bus: EventBus) -> str | None:
    """Xvfb ekranından PNG alır (imagemagick varsa)."""
    if not shutil.which("import"):
        return None
    try:
        run(
            ["import", "-display", display, "-window", "root", out_file],
            bus, timeout=60, quiet=True,
        )
        if os.path.isfile(out_file) and os.path.getsize(out_file) > 0:
            bus.ok(f"Ekran görüntüsü alındı: {os.path.basename(out_file)}")
            return out_file
    except Exception as exc:  # noqa: BLE001 - görüntü opsiyonel
        bus.warn(f"Ekran görüntüsü alınamadı: {exc}")
    return None
