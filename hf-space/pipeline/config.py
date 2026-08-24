"""Build konfigürasyonu ve pipeline genelinde kullanılan sabitler.

Buradaki sabitlerin çoğu Winlator kaynağından DOĞRULANARAK alınmıştır,
tahminle değil. İlgili kaynak dosya her birinin yanında belirtilmiştir.
"""
from __future__ import annotations

import os
import re
from dataclasses import dataclass, field, asdict
from typing import Any

# --- Winlator kaynak sabitleri ----------------------------------------------
# Bunlar pin'lenmiş commit'lerden okundu; sürüm yükseltirken tekrar doğrula.
WINLATOR_REPO = "https://github.com/brunodev85/winlator.git"
WINLATOR_APP_REPO = "https://github.com/brunodev85/winlator-app.git"
# Pin: 2026-08-19 tarihli HEAD'ler (Winlator 11.2 / rootfs v22 / wine 10.10)
WINLATOR_APP_PIN = "4f55d117fff1542944e5b91f433470445160ce08"
WINLATOR_PIN = "5949297d9dc83ad24ce3f5119fe382da7c899a78"

# winlator-app/app/src/main/java/com/winlator/core/WineInfo.java
MAIN_WINE_VERSION = "10.10"
# winlator-app/app/src/main/java/com/winlator/xenvironment/RootFSInstaller.java
ROOTFS_VERSION = 22
# winlator-app/app/src/main/java/com/winlator/xenvironment/RootFS.java
RFS_USER = "xuser"
# Wine binary'sine derlenmiş olarak gömülü olan uygulama kimliği.
# (rootfs içinde 165 dosyada, 447 kez geçiyor — grep ile doğrulandı.)
BAKED_APP_ID = "com.winlator"

# Android tarafındaki container dizini: <rootfs>/home/xuser-1
# ContainerManager.activateContainer() ayrıca home/xuser -> home/xuser-1
# symlink'ini oluşturur; biz de aynısını yapıyoruz ki registry'deki yollar
# birebir aynı olsun.
CONTAINER_ID = 1

# APK assets'inden alınacak dosyalar
ASSET_ROOTFS = "rootfs.tzst"
ASSET_CONTAINER_PATTERN = "container_pattern.tzst"
ASSET_COMMON_DLLS = "common_dlls.json"

# copyCommonDlls() eşlemesi — ContainerManager.extractContainerPatternFile()
COMMON_DLL_MAP = [("x86_64-windows", "system32"), ("i386-windows", "syswow64")]

# Delta'ya ASLA girmemesi gereken yollar (container köküne göre).
# Bunlar ya cihaza özgü ya da Android tarafının kendisinin yönettiği şeyler.
DELTA_EXCLUDES = (
    ".wine/dosdevices/",          # sürücü harfleri — Container.DEFAULT_DRIVES ile app kurar
    ".wine/drive_c/windows/temp/",
    ".wine/drive_c/users/xuser/Temp/",
    ".wine/drive_c/users/xuser/AppData/Local/Temp/",
    ".wine/.update-timestamp",
    # winemenubuilder'in urettigi XDG menu/mime/ikon ciktilari. Android'de
    # hicbir karsiligi yok; ilk wineboot'ta olusup delta'yi sisiriyorlardi.
    ".local/",
    ".cache/",
    ".config/menus/",
)

# Box64 preset — Box64Preset.java'da tanımlı sabitler
BOX64_PRESETS = ("STABILITY", "CONSERVATIVE", "INTERMEDIATE", "PERFORMANCE")

# Container.java'dan birebir alinan varsayilanlar
DEFAULT_ENV_VARS = (
    "ZINK_DESCRIPTORS=lazy ZINK_DEBUG=compact MESA_SHADER_CACHE_DISABLE=false "
    "MESA_SHADER_CACHE_MAX_SIZE=512MB mesa_glthread=true WINEESYNC=1 "
    "TU_DEBUG=noconform"
)
DEFAULT_WINCOMPONENTS = (
    "direct3d=1,directsound=1,directmusic=1,directshow=0,directplay=0,"
    "xaudio=1,vcrun2005=0,vcrun2010=1,wmdecoder=1"
)
DEFAULT_DESKTOP_THEME = "LIGHT,IMAGE,#0277bd"   # WineThemeManager.DEFAULT_DESKTOP_THEME
STARTUP_SELECTION_ESSENTIAL = 1                  # Container.STARTUP_SELECTION_ESSENTIAL

APP_ID_RE = re.compile(r"^[a-z][a-z0-9_]*(\.[a-z][a-z0-9_]*)+$")


def validate_app_id(app_id: str) -> str:
    """applicationId doğrulaması.

    Wine binary'sinde yol gömülü olduğu için uzunluk kritik:
    tam 12 karakter olursa rootfs'teki 447 tekrar aynı uzunlukta
    byte-patch'lenebilir (offset kayması olmaz) — bu en güvenli yol.
    12'den kısa da NUL padding ile çalışır; uzun OLAMAZ.
    """
    app_id = (app_id or "").strip()
    if not APP_ID_RE.match(app_id):
        raise ValueError(
            f"Geçersiz applicationId: {app_id!r}. "
            "Örnek: com.gameport (küçük harf, en az bir nokta)."
        )
    if len(app_id) > len(BAKED_APP_ID):
        raise ValueError(
            f"applicationId en fazla {len(BAKED_APP_ID)} karakter olabilir "
            f"(şu an {len(app_id)}). Wine binary'sinde "
            f"'/data/data/{BAKED_APP_ID}/files/rootfs' yolu derlenmiş halde "
            "gömülü; daha uzun bir isim byte-patch sırasında offset kaydırır. "
            f"En güvenlisi TAM {len(BAKED_APP_ID)} karakter kullanmaktır."
        )
    return app_id


@dataclass
class BuildConfig:
    """Tek bir build çalıştırmasının tüm girdileri."""

    # --- kimlik ---
    app_id: str = "com.gameport"          # tam 12 karakter önerilir
    game_name: str = "Game"
    app_label: str = ""                    # boşsa game_name kullanılır
    version_name: str = "1.0"
    version_code: int = 1

    # --- oyun kaynağı ---
    source_kind: str = "upload"            # upload | dataset
    upload_path: str = ""                  # source_kind=upload
    dataset_repo: str = ""                 # source_kind=dataset (private olabilir)
    dataset_file: str = ""                 # dataset içindeki dosya yolu
    exec_path_override: str = ""           # boşsa otomatik tespit

    # --- prefix hazırlama ---
    winetricks_verbs: list[str] = field(default_factory=list)  # örn ["vcrun2022"]
    auto_redist: bool = True               # tespit sonucuna göre otomatik seç
    prefix_cleanup: bool = True            # gereksiz Wine bileşenlerini sil
    run_smoke_test: bool = True
    smoke_test_seconds: int = 25

    # --- Android çalışma zamanı sabitleri (shortcut [Extra Data]) ---
    box64_preset: str = "STABILITY"
    exec_args: str = "-force-gfx-direct"
    screen_size: str = "1280x720"
    # GraphicsDrivers.java: "<vulkan>,<opengl>" ciftidir. Upstream varsayilani
    # vortek,gladio -- vortek host Vulkan surucusu uzerinde calistigi icin
    # Adreno/Mali fark etmeksizin en genis cihaz uyumunu verir. turnip sadece
    # Adreno'da ve daha hizlidir; bilinen bir cihaz hedefliyorsan onu sec.
    graphics_driver: str = "vortek,gladio"
    dxwrapper: str = "dxvk"
    audio_driver: str = "alsa"
    force_fullscreen: bool = True
    controls_profile: str = "1"

    # --- paketleme ---
    zstd_level: int = 19
    repack_rootfs: bool = True             # app_id != com.winlator ise zorunlu

    def normalized(self) -> "BuildConfig":
        self.app_id = validate_app_id(self.app_id)
        self.game_name = (self.game_name or "Game").strip() or "Game"
        # app_label'i BILEREK burada doldurmuyoruz: oyun adi app.info'dan
        # otomatik tespit edilebiliyor ve o tespit bu noktadan sonra oluyor.
        # Bos birakilirsa game_config yazilirken game_name'e duser.
        if self.box64_preset not in BOX64_PRESETS:
            raise ValueError(
                f"box64_preset {self.box64_preset!r} geçersiz. "
                f"Geçerli değerler: {', '.join(BOX64_PRESETS)}"
            )
        self.zstd_level = max(1, min(19, int(self.zstd_level)))
        self.smoke_test_seconds = max(5, min(180, int(self.smoke_test_seconds)))
        if self.app_id != BAKED_APP_ID:
            # Farklı bir applicationId, rootfs'in yeniden paketlenmesini gerektirir.
            self.repack_rootfs = True
        return self

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def work_dir() -> str:
    return os.environ.get("WORK_DIR", "/work")


def build_dir() -> str:
    return os.environ.get("BUILD_DIR", "/build")


def out_dir() -> str:
    return os.environ.get("OUT_DIR", os.path.join(work_dir(), "out"))


def upload_dir() -> str:
    return os.environ.get("UPLOAD_DIR", os.path.join(work_dir(), "uploads"))
