"""Pipeline orkestratörü — AŞAMA A'nın tüm adımlarını sırayla yürütür.

Adımlar (UI'daki ilerleme çubuğu bunlarla eşleşir):
    1  acquire   Winlator asset'lerini çek
    2  rootfs    rootfs.tzst'i aç + applicationId yamala + Android yolunu bağla
    3  container container_pattern + copyCommonDlls (telefondaki ilk hâlin aynısı)
    4  baseline  pristine manifest (delta bunun üzerinden hesaplanacak)
    5  game      oyun arşivini aç, analiz et, C:\\Games altına yerleştir
    6  wineboot  prefix'i ilklendir
    7  redist    winetricks ile gerekli runtime'ları kur
    8  regclean  gereksiz Wine bileşenlerini kapat + prefix temizliği
    9  verify    PE import kontrolü + (opsiyonel) smoke test
    10 delta     pristine'e göre farkı çıkar ve paketle
    11 pack      rootfs'i yeniden paketle + config yaz + zip'le
"""
from __future__ import annotations

import json
import os
import shutil
import time
import traceback

from . import container as container_mod
from . import delta as delta_mod
from . import gamefiles, gamescan, rootfs as rootfs_mod, wine, winlator_src
from .bus import EventBus
from .config import (
    BAKED_APP_ID,
    CONTAINER_ID,
    DEFAULT_DESKTOP_THEME,
    DEFAULT_ENV_VARS,
    DEFAULT_WINCOMPONENTS,
    MAIN_WINE_VERSION,
    ROOTFS_VERSION,
    STARTUP_SELECTION_ESSENTIAL,
    BuildConfig,
    build_dir,
    out_dir,
)
from .util import CommandError, human, rm_rf, run

STEPS = [
    ("acquire", "Winlator asset'leri"),
    ("rootfs", "rootfs + applicationId"),
    ("container", "Container oluştur"),
    ("baseline", "Pristine manifest"),
    ("game", "Oyun dosyaları"),
    ("wineboot", "Prefix ilkleme"),
    ("redist", "Redistributable"),
    ("regclean", "Registry temizliği"),
    ("verify", "Doğrulama"),
    ("delta", "Delta çıkar"),
    ("pack", "Paketle"),
]
SCHEMA_VERSION = 1


class BuildAborted(RuntimeError):
    pass


def _dos_exec(dos_dir: str, exec_relpath: str) -> tuple[str, str, str]:
    """(tam DOS yolu, DOS klasörü, dosya adı) üretir."""
    rel = exec_relpath.replace("/", "\\")
    full = f"{dos_dir}\\{rel}"
    parent = full.rsplit("\\", 1)[0]
    name = full.rsplit("\\", 1)[1]
    return full, parent, name


def run_build(cfg: BuildConfig, bus: EventBus, cancel=lambda: False) -> dict:
    cfg = cfg.normalized()
    started = time.time()
    bd = build_dir()
    od = out_dir()
    os.makedirs(od, exist_ok=True)

    real_root = os.path.join(bd, "rootfs")
    stage = os.path.join(bd, "stage")
    cache = os.path.join(bd, "cache")
    rm_rf(stage)
    os.makedirs(stage, exist_ok=True)

    report: dict = {
        "schemaVersion": SCHEMA_VERSION,
        "config": cfg.to_dict(),
        "startedAt": started,
    }

    def guard() -> None:
        if cancel():
            raise BuildAborted("Build kullanıcı tarafından iptal edildi.")

    def begin(key: str, index: int) -> None:
        guard()
        bus.step(key, "running")
        bus.progress(index / len(STEPS), dict(STEPS)[key])

    def done(key: str, detail: str = "") -> None:
        bus.step(key, "done", detail)

    # ---------------------------------------------------------------- 1
    begin("acquire", 0)
    assets = winlator_src.acquire(cache, bus)
    done("acquire")

    # ---------------------------------------------------------------- 2
    begin("rootfs", 1)
    rootfs_mod.extract(assets.require("rootfs.tzst"), real_root, bus)
    patched_files, patched_hits = rootfs_mod.patch_app_id(real_root, cfg.app_id, bus)
    android_root = rootfs_mod.link_android_path(real_root, cfg.app_id, bus)
    report["rootfs"] = {
        "patchedFiles": patched_files,
        "patchedOccurrences": patched_hits,
        "androidPath": android_root,
        "version": ROOTFS_VERSION,
        "wineVersion": MAIN_WINE_VERSION,
    }
    done("rootfs", f"{patched_hits} tekrar yamalandı" if patched_hits else "yama gerekmedi")

    # Bundan sonraki her şey Android'in göreceği yol üzerinden yapılır.
    root = android_root

    # ---------------------------------------------------------------- 3
    begin("container", 2)
    cdir = container_mod.create(root, assets, bus, container_id=CONTAINER_ID)
    prefix = container_mod.prefix_dir(root, CONTAINER_ID)
    done("container")

    # ---------------------------------------------------------------- 4
    begin("baseline", 3)
    baseline = delta_mod.snapshot(cdir, bus, "Pristine")
    done("baseline", f"{len(baseline)} dosya")

    # ---------------------------------------------------------------- 5
    begin("game", 4)
    source = _resolve_source(cfg, stage, bus)
    game_root = gamefiles.extract_archive(source, os.path.join(stage, "game"), bus)
    detected = gamescan.detect_game(game_root, bus, exec_override=cfg.exec_path_override)
    if not cfg.game_name or cfg.game_name == "Game":
        if detected.get("product_name"):
            cfg.game_name = detected["product_name"]
            bus.info(f"Oyun adı app.info'dan alındı: {cfg.game_name}")
    game_unix, game_dos = gamefiles.install_into_prefix(
        game_root, prefix, cfg.game_name, bus
    )
    exec_dos, exec_dir_dos, exec_file = _dos_exec(game_dos, detected["exec_relpath"])
    exec_unix = os.path.join(game_unix, detected["exec_relpath"])
    report["game"] = {**detected, "execPathDos": exec_dos}
    done("game", detected["exec_relpath"])

    # ---------------------------------------------------------------- 6
    with wine.Xvfb(bus) as xvfb:
        begin("wineboot", 5)
        wine.wineboot(root, bus)
        done("wineboot")

        # ------------------------------------------------------------ 7
        begin("redist", 6)
        verbs = list(cfg.winetricks_verbs)
        if cfg.auto_redist:
            for verb in gamescan.recommended_verbs(detected):
                if verb not in verbs:
                    verbs.append(verb)
                    bus.info(f"Otomatik seçildi: {verb}")
        installed = wine.install_winetricks(root, verbs, bus)
        report["redist"] = {"requested": verbs, "installed": installed}
        done("redist", ", ".join(installed) if installed else "yok")

        # ------------------------------------------------------------ 8
        begin("regclean", 7)
        wine.registry_cleanup(root, bus)
        cleaned: list[str] = []
        if cfg.prefix_cleanup:
            cleaned = delta_mod.prefix_cleanup(cdir, bus)
        report["cleanup"] = cleaned
        done("regclean")

        # ------------------------------------------------------------ 9
        begin("verify", 8)
        imports = gamescan.check_imports(exec_unix, prefix, bus)
        report["imports"] = imports
        smoke: dict = {"ran": False}
        shot: str | None = None
        if cfg.run_smoke_test:
            smoke = wine.smoke_test(
                root, exec_unix, bus,
                seconds=cfg.smoke_test_seconds, exec_args=cfg.exec_args,
            )
            shot = wine.screenshot(xvfb.display, os.path.join(od, "smoke_test.png"), bus)
        report["smokeTest"] = smoke
        wine.wineserver_wait(root, bus)
        done("verify", "import tamam" if imports.get("ok") else "eksik DLL var")

    # --------------------------------------------------------------- 10
    begin("delta", 9)
    current = delta_mod.snapshot(cdir, bus, "Son durum")
    changed, removed = delta_mod.diff(baseline, current)
    payload_file = os.path.join(od, "game_payload.tzst")
    rm_rf(payload_file)
    payload_stats = delta_mod.pack(
        cdir, changed, removed, payload_file, bus, level=cfg.zstd_level
    )
    report["payload"] = payload_stats
    done("delta", f"{payload_stats['changed_count']} dosya / {human(payload_stats['packed_bytes'])}")

    # --------------------------------------------------------------- 11
    begin("pack", 10)
    artifacts: list[str] = [payload_file]

    rootfs_out = ""
    if cfg.repack_rootfs:
        rootfs_out = os.path.join(od, "rootfs.tzst")
        rootfs_mod.repack(real_root, rootfs_out, bus, level=cfg.zstd_level)
        artifacts.append(rootfs_out)
    else:
        bus.info("applicationId com.winlator; rootfs.tzst yeniden paketlenmedi.")

    game_config = _build_game_config(
        cfg, detected, exec_dos, exec_dir_dos, exec_file,
        payload_stats, bool(rootfs_out), report,
    )
    config_file = os.path.join(od, "game_config.json")
    delta_mod.write_manifest(config_file, game_config)
    artifacts.append(config_file)

    report["finishedAt"] = time.time()
    report["durationSeconds"] = round(report["finishedAt"] - started, 1)
    report_file = os.path.join(od, "build_report.json")
    delta_mod.write_manifest(report_file, report)
    artifacts.append(report_file)
    if shot:
        artifacts.append(shot)

    bundle = _make_bundle(cfg, od, artifacts, bus)
    done("pack", human(os.path.getsize(bundle)))
    bus.progress(1.0, "Tamamlandı")

    result = {
        "ok": True,
        "bundle": os.path.basename(bundle),
        "artifacts": [os.path.basename(a) for a in artifacts],
        "durationSeconds": report["durationSeconds"],
        "payloadBytes": payload_stats["packed_bytes"],
        "rootfsRepacked": bool(rootfs_out),
        "importsOk": imports.get("ok", False),
        "missingDlls": [m["dll"] for m in imports.get("missing", [])],
        "smokeTest": smoke,
        "gameName": cfg.game_name,
        "execPathDos": exec_dos,
    }
    bus.result(result)
    bus.ok(
        f"BUILD TAMAM — {report['durationSeconds']}s, "
        f"payload {human(payload_stats['packed_bytes'])}"
    )
    return result


def _resolve_source(cfg: BuildConfig, stage: str, bus: EventBus) -> str:
    """Oyun kaynağını (upload ya da HF Dataset) yerel bir yola indirger."""
    if cfg.source_kind == "upload":
        if not cfg.upload_path or not os.path.exists(cfg.upload_path):
            raise FileNotFoundError(
                "Oyun arşivi yüklenmemiş. Önce bir zip yükle ya da kaynak olarak "
                "HF Dataset seç."
            )
        return cfg.upload_path

    if cfg.source_kind == "dataset":
        token = os.environ.get("HF_TOKEN") or os.environ.get("HUGGING_FACE_HUB_TOKEN")
        if not cfg.dataset_repo:
            raise ValueError("HF Dataset repo id'si boş.")
        if not token:
            bus.warn(
                "HF_TOKEN Space secret'ı tanımlı değil. Private dataset için "
                "Settings -> Variables and secrets altından HF_TOKEN ekle."
            )
        from huggingface_hub import hf_hub_download, snapshot_download

        dest = os.path.join(stage, "dataset")
        os.makedirs(dest, exist_ok=True)
        if cfg.dataset_file:
            bus.info(f"HF Dataset'ten indiriliyor: {cfg.dataset_repo}/{cfg.dataset_file}")
            return hf_hub_download(
                repo_id=cfg.dataset_repo, filename=cfg.dataset_file,
                repo_type="dataset", token=token, local_dir=dest,
            )
        bus.info(f"HF Dataset tamamı indiriliyor: {cfg.dataset_repo}")
        return snapshot_download(
            repo_id=cfg.dataset_repo, repo_type="dataset", token=token, local_dir=dest,
        )

    raise ValueError(f"Bilinmeyen kaynak türü: {cfg.source_kind}")


def _build_game_config(cfg, detected, exec_dos, exec_dir_dos, exec_file,
                       payload_stats, rootfs_repacked, report) -> dict:
    """Android tarafının okuyacağı sözleşme dosyası.

    Alan isimleri Winlator'ın kendi Container/Shortcut alanlarıyla birebir
    eşleşir; Android tarafı bunları doğrudan .container JSON'una ve
    .desktop [Extra Data] bölümüne yazar.
    """
    return {
        "schemaVersion": SCHEMA_VERSION,
        "appId": cfg.app_id,
        "appLabel": cfg.app_label or cfg.game_name,
        "versionName": cfg.version_name,
        "versionCode": cfg.version_code,
        "wineVersion": MAIN_WINE_VERSION,
        "rootfsVersion": ROOTFS_VERSION,
        "game": {
            "name": cfg.game_name,
            "execPathDos": exec_dos,
            "execDirDos": exec_dir_dos,
            "execFile": exec_file,
            "engine": detected.get("engine", ""),
            "scriptingBackend": detected.get("scripting_backend", ""),
            "unityVersion": detected.get("unity_version", ""),
            "machine": report.get("imports", {}).get("machine", ""),
        },
        "container": {
            "id": CONTAINER_ID,
            "name": cfg.game_name,
            "screenSize": cfg.screen_size,
            "envVars": DEFAULT_ENV_VARS,
            "graphicsDriver": cfg.graphics_driver,
            "dxwrapper": cfg.dxwrapper,
            "audioDriver": cfg.audio_driver,
            "box64Preset": cfg.box64_preset,
            "wincomponents": DEFAULT_WINCOMPONENTS,
            "desktopTheme": DEFAULT_DESKTOP_THEME,
            "startupSelection": STARTUP_SELECTION_ESSENTIAL,
            "drives": "",
        },
        "shortcut": {
            "execArgs": cfg.exec_args,
            "forceFullscreen": "1" if cfg.force_fullscreen else "0",
            "controlsProfile": cfg.controls_profile,
            "box64Preset": cfg.box64_preset,
            "graphicsDriver": cfg.graphics_driver,
            "dxwrapper": cfg.dxwrapper,
            "audioDriver": cfg.audio_driver,
            "screenSize": cfg.screen_size,
        },
        "payload": {
            "file": "game_payload.tzst",
            "changedCount": payload_stats["changed_count"],
            "rawBytes": payload_stats["raw_bytes"],
            "packedBytes": payload_stats["packed_bytes"],
            "removedPaths": payload_stats["removed"],
        },
        "rootfs": {
            "file": "rootfs.tzst" if rootfs_repacked else "",
            "repacked": rootfs_repacked,
            "patchedAppId": cfg.app_id != BAKED_APP_ID,
        },
    }


def _make_bundle(cfg: BuildConfig, od: str, artifacts: list[str], bus: EventBus) -> str:
    """İndirilebilir tek zip üretir (Android projesine kopyalanacak dosyalar)."""
    safe = "".join(c for c in cfg.game_name if c.isalnum() or c in "_-") or "game"
    bundle = os.path.join(od, f"winlator-port-{safe}.zip")
    rm_rf(bundle)
    names = [os.path.basename(a) for a in artifacts if os.path.exists(a)]
    bus.info(f"Paket hazırlanıyor: {os.path.basename(bundle)}")
    # ZIP_STORED bilincli: icerideki .tzst zaten zstd ile sikistirilmis.
    # Tekrar deflate etmek ~%0 kazanc icin dakikalar harcar.
    try:
        run(["zip", "-0", "-j", bundle, *names], bus, cwd=od, quiet=True)
    except (OSError, CommandError) as exc:
        bus.warn(f"'zip' kullanilamadi ({exc}); Python zipfile'a dusuluyor.")
        rm_rf(bundle)
        import zipfile

        with zipfile.ZipFile(bundle, "w", zipfile.ZIP_STORED, allowZip64=True) as archive:
            for name in names:
                archive.write(os.path.join(od, name), name)
    bus.ok(f"Paket hazır: {os.path.basename(bundle)} ({human(os.path.getsize(bundle))})")
    return bundle


def safe_run_build(cfg: BuildConfig, bus: EventBus, cancel=lambda: False) -> dict:
    """run_build'i sararak hataları UI'a düzgün iletir."""
    try:
        return run_build(cfg, bus, cancel)
    except BuildAborted as exc:
        bus.warn(str(exc))
        payload = {"ok": False, "aborted": True, "error": str(exc)}
        bus.result(payload)
        return payload
    except Exception as exc:  # noqa: BLE001 - UI'a her hatayı göstermek istiyoruz
        bus.error(f"BUILD BAŞARISIZ: {exc}")
        for line in traceback.format_exc().splitlines():
            bus.error(line)
        payload = {"ok": False, "error": str(exc)}
        bus.result(payload)
        return payload
