"""AŞAMA B — APK'nın Space içinde derlenmesi.

AŞAMA A'nın ürettiği delta paketini alır, Winlator kaynağını pin'li commit'ten
çeker, tek-oyun katmanını serer ve gradlew ile APK üretir.

Neden Space'te: derleme ~10-20 dakika sürüyor ve arka plan thread'inde
çalıştığı için kullanıcı tarayıcıyı kapatabiliyor. Sonuç diske yazılıyor,
geri dönüldüğünde indirilebilir halde duruyor.

Winlator kaynağı FORK'LANMIYOR: pin'li commit'ten çekilip üstüne overlay
seriliyor ve yalnızca patches.json'daki cerrahi yamalar uygulanıyor.
"""
from __future__ import annotations

import json
import os
import shutil

from .bus import EventBus
from .config import WINLATOR_APP_PIN, WINLATOR_APP_REPO
from .util import CommandError, cpu_count, human, memory_limit_bytes, rm_rf, run

# Bu modülün bulunduğu paketin bir üstü = Space kökü
SPACE_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OVERLAY_DIR = os.path.join(SPACE_ROOT, "android", "overlay")
PATCHES_FILE = os.path.join(SPACE_ROOT, "android", "patches.json")

# Dockerfile bu sürümleri kuruyor; değiştirirsen orayı da güncelle.
REQUIRED_NDK = "24.0.8215888"


class AndroidBuildError(RuntimeError):
    pass


def _sdk_root() -> str:
    return (
        os.environ.get("ANDROID_HOME")
        or os.environ.get("ANDROID_SDK_ROOT")
        or "/opt/android-sdk"
    )


def preflight(bus: EventBus) -> None:
    """Derleme ortamının gerçekten hazır olduğunu, build'e girmeden doğrular.

    20 dakikalık bir gradle çalıştırmasının ortasında 'NDK yok' demek yerine
    en baştan net bir hata vermek istiyoruz.
    """
    sdk = _sdk_root()
    problems: list[str] = []

    if not os.path.isdir(sdk):
        problems.append(f"Android SDK bulunamadı: {sdk}")
    else:
        ndk_dir = os.path.join(sdk, "ndk", REQUIRED_NDK)
        if not os.path.isdir(ndk_dir):
            available = []
            ndk_root = os.path.join(sdk, "ndk")
            if os.path.isdir(ndk_root):
                available = sorted(os.listdir(ndk_root))
            problems.append(
                f"NDK {REQUIRED_NDK} bulunamadı ({ndk_dir}). "
                f"Kurulu olanlar: {', '.join(available) or 'yok'}"
            )
        if not os.path.isdir(os.path.join(sdk, "platforms", "android-35")):
            problems.append("platforms;android-35 kurulu değil.")

    if not os.path.isdir(OVERLAY_DIR):
        problems.append(f"Overlay klasörü yok: {OVERLAY_DIR}")
    if not os.path.isfile(PATCHES_FILE):
        problems.append(f"patches.json yok: {PATCHES_FILE}")

    if not shutil.which("java"):
        problems.append("java bulunamadı (JDK 17 gerekli).")

    if problems:
        raise AndroidBuildError(
            "APK derleme ortamı hazır değil:\n  - " + "\n  - ".join(problems)
        )
    bus.ok(f"Derleme ortamı hazır (SDK: {sdk}, NDK {REQUIRED_NDK}).")


def _ensure_full_source(cache_root: str, bus: EventBus) -> str:
    """winlator-app'in TAM kaynağını pin'li commit'te hazırlar.

    AŞAMA A aynı depoyu sparse (sadece assets) çekiyor. APK için tüm kaynak
    lazım; sparse'ı kapatıyoruz. Partial clone olduğu için git eksik blob'ları
    bu noktada tembel olarak indiriyor. Bir sorun çıkarsa sıfırdan tam klona
    düşüyoruz — yarım bir çalışma ağacıyla derlemeye girmek istemiyoruz.
    """
    repo = os.path.join(cache_root, "winlator-app")

    if os.path.isdir(os.path.join(repo, ".git")):
        try:
            bus.info("Mevcut klon tam kaynağa genişletiliyor…")
            run(["git", "-C", repo, "sparse-checkout", "disable"], bus, quiet=True)
            run(["git", "-C", repo, "checkout", "--force", WINLATOR_APP_PIN], bus, quiet=True)
        except (CommandError, OSError) as exc:
            bus.warn(f"Genişletme başarısız ({exc}); sıfırdan tam klon alınıyor.")
            rm_rf(repo)

    if not os.path.isdir(os.path.join(repo, ".git")):
        bus.info(f"winlator-app tam klonu alınıyor (pin {WINLATOR_APP_PIN[:10]})…")
        os.makedirs(cache_root, exist_ok=True)
        run(["git", "clone", WINLATOR_APP_REPO, repo], bus, quiet=True)
        run(["git", "-C", repo, "checkout", "--force", WINLATOR_APP_PIN], bus, quiet=True)

    gradle_file = os.path.join(repo, "app", "build.gradle")
    if not os.path.isfile(gradle_file):
        raise AndroidBuildError(
            f"Kaynak eksik görünüyor: {gradle_file} yok. Klon bozulmuş olabilir."
        )
    bus.ok("Winlator kaynağı hazır.")
    return repo


def _prepare_project(repo: str, project: str, bus: EventBus) -> None:
    """Pin'li kaynağın temiz bir kopyasını çıkarır (cache'i kirletmeden)."""
    rm_rf(project)
    bus.info("Çalışma kopyası hazırlanıyor…")
    shutil.copytree(
        repo, project, symlinks=True,
        ignore=shutil.ignore_patterns(".git", "build", ".gradle", "local.properties"),
    )


def _apply_overlay(project: str, bus: EventBus) -> None:
    for name in os.listdir(OVERLAY_DIR):
        src = os.path.join(OVERLAY_DIR, name)
        dst = os.path.join(project, name)
        if os.path.isdir(src):
            shutil.copytree(src, dst, dirs_exist_ok=True, symlinks=True)
        else:
            shutil.copy2(src, dst)
    # Upstream deposunda gradlew'in çalıştırma biti yok.
    gradlew = os.path.join(project, "gradlew")
    if os.path.isfile(gradlew):
        os.chmod(gradlew, 0o755)
    bus.ok("Tek-oyun katmanı serildi.")


def _apply_patches(project: str, bus: EventBus) -> None:
    """patches.json'daki cerrahi yamaları uygular.

    Çapa bulunamazsa DURUYORUZ: upstream pin'i değişmiş demektir ve sessizce
    yanlış yere yazmaktansa build'i düşürmek doğru olan.
    """
    with open(PATCHES_FILE, encoding="utf-8") as fh:
        patches = json.load(fh)["patches"]

    for patch in patches:
        path = os.path.join(project, patch["file"])
        if not os.path.isfile(path):
            raise AndroidBuildError(f"Yamalanacak dosya yok: {patch['file']}")
        with open(path, encoding="utf-8") as fh:
            text = fh.read()

        if patch["marker"] in text:
            bus.info(f"  {patch['id']}: zaten yamalı, atlandı")
            continue
        if patch["anchor"] not in text:
            raise AndroidBuildError(
                f"YAMA ÇAPASI BULUNAMADI: {patch['id']} ({patch['file']}).\n"
                f"Aranan: {patch['anchor'][:120]}\n"
                "Upstream pin'i değişmiş olabilir; patches.json güncellenmeli."
            )
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(text.replace(patch["anchor"], patch["replacement"], 1))
        bus.ok(f"  {patch['id']}: yamalandı")


def _place_artifacts(project: str, artifacts: dict[str, str], config_path: str,
                     bus: EventBus) -> int:
    assets = os.path.join(project, "app", "src", "main", "assets")
    gameport = os.path.join(assets, "gameport")
    os.makedirs(gameport, exist_ok=True)

    shutil.copy2(config_path, os.path.join(gameport, "game_config.json"))
    shutil.copy2(artifacts["payload"], os.path.join(gameport, "game_payload.tzst"))

    if artifacts.get("rootfs"):
        shutil.copy2(artifacts["rootfs"], os.path.join(assets, "rootfs.tzst"))
        bus.info("  yamalanmış rootfs.tzst yerleştirildi")
    else:
        bus.info("  upstream rootfs.tzst korunuyor (applicationId=com.winlator)")

    if artifacts.get("controls"):
        profiles = os.path.join(assets, "inputcontrols", "profiles")
        os.makedirs(profiles, exist_ok=True)
        shutil.copy2(artifacts["controls"], os.path.join(profiles, "controls-1.icp"))
        bus.info("  özel input profili yerleştirildi")

    sizes = [
        os.path.getsize(os.path.join(dp, f))
        for dp, _d, fs in os.walk(assets) for f in fs
    ]
    total, largest = sum(sizes), (max(sizes) if sizes else 0)
    bus.ok(f"Asset'ler yerleşti (toplam {human(total)}, en büyük {human(largest)}).")
    return largest


def _write_port_properties(project: str, config_path: str, bus: EventBus) -> None:
    with open(config_path, encoding="utf-8") as fh:
        config = json.load(fh)

    lines = [
        "# Space tarafindan game_config.json'dan uretildi.",
        f"gameport.applicationId={config['appId']}",
        f"gameport.appLabel={config.get('appLabel') or config['game']['name']}",
        f"gameport.versionName={config.get('versionName', '1.0')}",
        f"gameport.versionCode={config.get('versionCode', 1)}",
    ]
    with open(os.path.join(project, "port.properties"), "w", encoding="utf-8") as fh:
        fh.write("\n".join(lines) + "\n")
    for line in lines[1:]:
        bus.info(f"  {line}")


def _write_local_properties(project: str) -> None:
    with open(os.path.join(project, "local.properties"), "w", encoding="utf-8") as fh:
        fh.write(f"sdk.dir={_sdk_root()}\n")


# Gradle'in varsayilan daemon heap'i 512 MB. AGP'nin CompressAssetsTask'i her
# asset'i Files.readAllBytes ile TAMAMEN bellege okuyor (zipflinger BytesSource).
# Bizim asset'lerimiz devasa (rootfs.tzst + game_payload.tzst yuzlerce MB), ve
# bu is worker havuzunda PARALEL kosuyor. 512 MB heap + cok sayida worker =
# kesin OutOfMemoryError.
#
# GRADLE_OPTS bunu COZMEZ: o yalnizca Gradle istemci JVM'ine uygulanir, build
# ise daemon/forked JVM'de kosar ve oradaki heap'i org.gradle.jvmargs belirler.
# Bu yuzden gradle.properties'i burada uretiyoruz.
MIN_HEAP_MB = 2048
MAX_HEAP_MB = 8192
HEAP_HEADROOM_MB = 2048          # JVM disi kullanim + NDK/CMake surecleri icin


def _heap_mb() -> int:
    limit = memory_limit_bytes()
    if not limit:
        return 4096
    usable = int(limit / (1024 * 1024)) - HEAP_HEADROOM_MB
    return max(MIN_HEAP_MB, min(MAX_HEAP_MB, usable))


def _max_workers(asset_bytes: int, heap_mb: int) -> int:
    """Paralel worker tavani.

    Her CompressAssets worker'i en buyuk asset kadar bellek tutabilir; heap'i
    asmayacak sayida worker'a izin veriyoruz. Ayrica CPU sayisini asmiyoruz.
    """
    per_worker_mb = max(64, int(asset_bytes / (1024 * 1024)))
    by_memory = max(1, (heap_mb // 2) // per_worker_mb)
    return max(1, min(cpu_count(), by_memory, 8))


def _write_gradle_properties(project: str, largest_asset: int, bus: EventBus) -> int:
    """Upstream gradle.properties'i koruyarak bellek ayarlarini ekler."""
    path = os.path.join(project, "gradle.properties")
    existing: list[str] = []
    if os.path.isfile(path):
        with open(path, encoding="utf-8") as fh:
            existing = [
                line.rstrip("\n") for line in fh
                if not line.startswith(("org.gradle.jvmargs", "org.gradle.workers.max",
                                        "org.gradle.daemon", "org.gradle.parallel"))
            ]

    heap_mb = _heap_mb()
    workers = _max_workers(largest_asset, heap_mb)

    added = [
        "",
        "# --- tek-oyun port araci tarafindan eklendi ---",
        f"org.gradle.jvmargs=-Xmx{heap_mb}m -XX:MaxMetaspaceSize=1g "
        "-Dfile.encoding=UTF-8",
        f"org.gradle.workers.max={workers}",
        "org.gradle.daemon=false",
    ]
    with open(path, "w", encoding="utf-8") as fh:
        fh.write("\n".join(existing + added) + "\n")

    bus.info(
        f"Gradle bellek ayari: heap {heap_mb} MB, {workers} worker "
        f"(en buyuk asset {human(largest_asset)}, {cpu_count()} vCPU)."
    )
    return workers


def _gradle_env(gradle_home: str) -> dict[str, str]:
    sdk = _sdk_root()
    return {
        "ANDROID_HOME": sdk,
        "ANDROID_SDK_ROOT": sdk,
        "GRADLE_USER_HOME": gradle_home,
        # NOT: GRADLE_OPTS yalnizca Gradle ISTEMCI JVM'ine uygulanir; build'in
        # kostugu JVM'in heap'ini org.gradle.jvmargs belirler ve onu
        # _write_gradle_properties() yaziyor. Burasi sadece istemci icin.
        "GRADLE_OPTS": "-Xmx1g -Dfile.encoding=UTF-8",
        "JAVA_TOOL_OPTIONS": "",
        "TERM": "dumb",
    }


def build(
    cfg,
    artifacts: dict[str, str],
    config_path: str,
    cache_root: str,
    work_root: str,
    out_root: str,
    bus: EventBus,
) -> dict:
    """APK üretir. Dönüş: {'apk': yol, 'size': bayt, 'variant': 'debug'}"""
    preflight(bus)

    repo = _ensure_full_source(cache_root, bus)
    project = os.path.join(work_root, "android-project")
    _prepare_project(repo, project, bus)

    bus.info("Cerrahi yamalar uygulanıyor…")
    _apply_overlay(project, bus)
    _apply_patches(project, bus)

    bus.info("AŞAMA A çıktıları assets'e yerleştiriliyor…")
    largest_asset = _place_artifacts(project, artifacts, config_path, bus)
    _write_port_properties(project, config_path, bus)
    _write_local_properties(project)
    workers = _write_gradle_properties(project, largest_asset, bus)

    variant = "debug"
    task = "assembleDebug"
    gradle_home = os.path.join(cache_root, "gradle-home")
    os.makedirs(gradle_home, exist_ok=True)

    bus.info(
        "Gradle başlatılıyor. İlk çalıştırma Gradle dağıtımını ve bağımlılıkları "
        "indirir (5-15 dk); sonraki derlemeler 2-5 dk sürer. Native CMake/NDK "
        "derlemesi de bu adıma dahil."
    )
    run(
        [
            "./gradlew", task,
            "--no-daemon", "--console=plain", "--stacktrace",
            f"--max-workers={workers}",
        ],
        bus,
        cwd=project,
        env=_gradle_env(gradle_home),
        timeout=5400,
    )

    apk = _find_apk(project, variant)
    if not apk:
        raise AndroidBuildError(
            "Gradle başarıyla bitti ama APK bulunamadı. "
            f"Aranan yer: app/build/outputs/apk/{variant}/"
        )

    safe = "".join(c for c in cfg.game_name if c.isalnum() or c in "_-") or "game"
    os.makedirs(out_root, exist_ok=True)
    dest = os.path.join(out_root, f"{safe}-{variant}.apk")
    rm_rf(dest)
    shutil.copy2(apk, dest)

    size = os.path.getsize(dest)
    bus.ok(f"APK hazır: {os.path.basename(dest)} ({human(size)})")
    return {"apk": dest, "size": size, "variant": variant}


def _find_apk(project: str, variant: str) -> str | None:
    outputs = os.path.join(project, "app", "build", "outputs", "apk", variant)
    if not os.path.isdir(outputs):
        return None
    for name in sorted(os.listdir(outputs)):
        if name.endswith(".apk"):
            return os.path.join(outputs, name)
    return None
