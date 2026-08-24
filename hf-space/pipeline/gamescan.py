"""Oyun dosyalarının analizi: Unity tespiti, exe seçimi ve PE bağımlılık kontrolü.

Buradaki PE ayrıştırıcı bilinçli olarak minimal: amaç 'exe'nin import ettiği
DLL'ler prefix içinde gerçekten var mı' sorusuna GPU'suz, hızlı ve kesin bir
cevap vermek. Bu, prompt'taki 'prefix bozuk değil' kontrolünün
'wine --version' çağırmaktan çok daha anlamlı bir versiyonu.
"""
from __future__ import annotations

import os
import re
import struct
from dataclasses import dataclass, field

from .bus import EventBus

IMAGE_FILE_MACHINE = {0x014C: "i386", 0x8664: "x86_64", 0xAA64: "arm64"}
DIR_IMPORT = 1
DIR_DELAY_IMPORT = 13

# apisetschema ile çözülen sanal DLL'ler — diskte dosya olarak aranmaz.
APISET_RE = re.compile(r"^(api|ext)-ms-win-", re.IGNORECASE)

VCRUNTIME_DLLS = ("vcruntime140.dll", "msvcp140.dll", "vcruntime140_1.dll")


@dataclass
class PEInfo:
    path: str
    machine: str = "?"
    imports: list[str] = field(default_factory=list)
    error: str = ""


def _rva_to_offset(rva: int, sections: list[tuple[int, int, int, int]]) -> int | None:
    for vaddr, vsize, raw_ptr, raw_size in sections:
        if vaddr <= rva < vaddr + max(vsize, raw_size):
            delta = rva - vaddr
            if delta < raw_size:
                return raw_ptr + delta
            return None
    return None


def parse_pe(path: str) -> PEInfo:
    info = PEInfo(path=path)
    try:
        with open(path, "rb") as fh:
            data = fh.read()
    except OSError as exc:
        info.error = f"okunamadi: {exc}"
        return info

    if len(data) < 0x40 or data[:2] != b"MZ":
        info.error = "MZ imzasi yok"
        return info

    e_lfanew = struct.unpack_from("<I", data, 0x3C)[0]
    if e_lfanew + 24 > len(data) or data[e_lfanew : e_lfanew + 4] != b"PE\0\0":
        info.error = "PE imzasi yok"
        return info

    coff = e_lfanew + 4
    machine, nsections = struct.unpack_from("<HH", data, coff)
    opt_size = struct.unpack_from("<H", data, coff + 16)[0]
    info.machine = IMAGE_FILE_MACHINE.get(machine, hex(machine))

    opt = coff + 20
    if opt + 2 > len(data):
        info.error = "optional header kesik"
        return info
    magic = struct.unpack_from("<H", data, opt)[0]
    if magic == 0x10B:
        dd_off = opt + 96
    elif magic == 0x20B:
        dd_off = opt + 112
    else:
        info.error = f"bilinmeyen optional header magic {magic:#x}"
        return info

    sec_off = opt + opt_size
    sections: list[tuple[int, int, int, int]] = []
    for i in range(nsections):
        base = sec_off + i * 40
        if base + 40 > len(data):
            break
        vsize, vaddr, raw_size, raw_ptr = struct.unpack_from("<IIII", data, base + 8)
        sections.append((vaddr, vsize, raw_ptr, raw_size))

    names: list[str] = []
    for dir_index, name_field_off in ((DIR_IMPORT, 12), (DIR_DELAY_IMPORT, 4)):
        entry = dd_off + dir_index * 8
        if entry + 8 > len(data):
            continue
        dir_rva, dir_size = struct.unpack_from("<II", data, entry)
        if not dir_rva or not dir_size:
            continue
        base_off = _rva_to_offset(dir_rva, sections)
        if base_off is None:
            continue
        stride = 20 if dir_index == DIR_IMPORT else 32
        for i in range(4096):
            rec = base_off + i * stride
            if rec + stride > len(data):
                break
            chunk = data[rec : rec + stride]
            if not any(chunk):
                break
            name_rva = struct.unpack_from("<I", data, rec + name_field_off)[0]
            if not name_rva:
                continue
            name_off = _rva_to_offset(name_rva, sections)
            if name_off is None or name_off >= len(data):
                continue
            end = data.find(b"\0", name_off)
            if end == -1:
                continue
            try:
                names.append(data[name_off:end].decode("ascii").lower())
            except UnicodeDecodeError:
                continue

    info.imports = sorted(set(names))
    return info


def _ci_index(directory: str) -> dict[str, str]:
    """Buyuk/kucuk harf duyarsiz dosya indeksi (Windows semantigi)."""
    index: dict[str, str] = {}
    if not os.path.isdir(directory):
        return index
    try:
        for name in os.listdir(directory):
            index.setdefault(name.lower(), os.path.join(directory, name))
    except OSError:
        pass
    return index


def _unity_data_dirs(root: str) -> list[str]:
    try:
        return [
            os.path.join(root, n)
            for n in os.listdir(root)
            if n.endswith("_Data") and os.path.isdir(os.path.join(root, n))
        ]
    except OSError:
        return []


def check_imports(exe_path: str, prefix_root: str, bus: EventBus) -> dict:
    """exe ve yanindaki DLL'lerin importlarini prefix'e karsi dogrular."""
    drive_c = os.path.join(prefix_root, "drive_c")
    system32 = _ci_index(os.path.join(drive_c, "windows/system32"))
    syswow64 = _ci_index(os.path.join(drive_c, "windows/syswow64"))

    main = parse_pe(exe_path)
    if main.error:
        bus.error(f"Ana exe ayristirilamadi: {main.error}")
        return {"ok": False, "error": main.error, "machine": main.machine, "missing": []}

    bus.info(f"Ana yurutulebilir mimarisi: {main.machine}")
    system_index = system32 if main.machine == "x86_64" else syswow64
    game_dir = os.path.dirname(exe_path)
    game_index = _ci_index(game_dir)
    for data_dir in _unity_data_dirs(game_dir):
        for sub in ("Plugins", "Plugins/x86_64", "Plugins/x86"):
            game_index.update(_ci_index(os.path.join(data_dir, sub)))

    to_scan = [exe_path]
    to_scan += [p for n, p in game_index.items() if n.endswith(".dll")]

    missing: dict[str, list[str]] = {}
    scanned = 0
    for target in to_scan[:400]:      # ust sinir: patolojik paketlerde donmasin
        pe = parse_pe(target)
        if pe.error:
            continue
        scanned += 1
        for dep in pe.imports:
            if APISET_RE.match(dep):
                continue
            if dep in game_index or dep in system_index:
                continue
            missing.setdefault(dep, []).append(os.path.basename(target))

    result = {
        "ok": not missing,
        "machine": main.machine,
        "scanned": scanned,
        "imports": main.imports,
        "missing": [
            {"dll": dll, "needed_by": sorted(set(users))[:6]}
            for dll, users in sorted(missing.items())
        ],
    }

    if missing:
        bus.warn(f"{len(missing)} DLL prefix'te bulunamadi:")
        for item in result["missing"][:15]:
            bus.warn(f"  - {item['dll']}  <-  {', '.join(item['needed_by'])}")
    else:
        bus.ok(f"{scanned} PE dosyasinin tum import'lari cozuldu.")
    return result


def _sniff_unity_version(path: str) -> str:
    """globalgamemanagers basinda gecen surum damgasini arar."""
    try:
        with open(path, "rb") as fh:
            head = fh.read(4096)
    except OSError:
        return ""
    match = re.search(rb"(\d{4}\.\d+\.\d+[a-z]?\d*)", head)
    return match.group(1).decode("ascii", "replace") if match else ""


def _autodetect_exe(root: str) -> str:
    """Unity duzenine gore exe secer: <Ad>.exe + <Ad>_Data/ eslesmesi."""
    best: tuple[int, str] | None = None
    for dirpath, dirnames, filenames in os.walk(root):
        dirs = {d for d in dirnames if d.endswith("_Data")}
        for name in filenames:
            if not name.lower().endswith(".exe"):
                continue
            stem = name[:-4]
            full = os.path.join(dirpath, name)
            try:
                size = os.path.getsize(full)
            except OSError:
                continue
            depth = os.path.relpath(full, root).count(os.sep)
            score = 0
            if f"{stem}_Data" in dirs:
                score += 1_000_000_000
            low = stem.lower()
            if "unins" in low or "setup" in low or "crash" in low:
                score -= 500_000_000
            score += max(0, 10_000_000 - depth * 1_000_000)
            score += min(size, 9_000_000)
            if best is None or score > best[0]:
                best = (score, os.path.relpath(full, root))
    if best is None:
        raise FileNotFoundError(
            "Oyun klasorunde .exe bulunamadi. Zip'in icinde oyunun kok klasoru "
            "oldugundan emin ol."
        )
    return best[1]


def detect_game(root: str, bus: EventBus, *, exec_override: str = "") -> dict:
    """Oyun klasorunu tarar: exe, Unity surumu, IL2CPP/Mono, VC runtime durumu."""
    info: dict = {
        "root": root,
        "exec_relpath": "",
        "engine": "unknown",
        "scripting_backend": "",
        "unity_version": "",
        "product_name": "",
        "company": "",
        "has_vcruntime": False,
        "missing_vcruntime": [],
    }

    if exec_override:
        candidate = os.path.join(root, exec_override.replace("\\", "/"))
        if not os.path.isfile(candidate):
            raise FileNotFoundError(f"Belirtilen exe bulunamadi: {exec_override}")
        info["exec_relpath"] = os.path.relpath(candidate, root)
    else:
        info["exec_relpath"] = _autodetect_exe(root)

    exe_abs = os.path.join(root, info["exec_relpath"])
    game_dir = os.path.dirname(exe_abs)
    data_dirs = _unity_data_dirs(game_dir)

    if data_dirs or os.path.isfile(os.path.join(game_dir, "UnityPlayer.dll")):
        info["engine"] = "unity"
        if os.path.isfile(os.path.join(game_dir, "GameAssembly.dll")):
            info["scripting_backend"] = "il2cpp"
        elif any(os.path.isdir(os.path.join(d, "Managed")) for d in data_dirs):
            info["scripting_backend"] = "mono"

    for data_dir in data_dirs:
        app_info = os.path.join(data_dir, "app.info")
        if os.path.isfile(app_info):
            try:
                with open(app_info, encoding="utf-8", errors="replace") as fh:
                    lines = fh.read().splitlines()
                if len(lines) >= 2:
                    info["company"] = lines[0].strip()
                    info["product_name"] = lines[1].strip()
            except OSError:
                pass
        ggm = os.path.join(data_dir, "globalgamemanagers")
        if not info["unity_version"] and os.path.isfile(ggm):
            info["unity_version"] = _sniff_unity_version(ggm)

    present = {
        n.lower() for n in os.listdir(game_dir)
        if os.path.isfile(os.path.join(game_dir, n))
    }
    missing_vc = [d for d in VCRUNTIME_DLLS if d not in present]
    info["has_vcruntime"] = not missing_vc
    info["missing_vcruntime"] = missing_vc

    bus.ok(f"Yurutulebilir: {info['exec_relpath']}")
    if info["engine"] == "unity":
        detail = f"Unity tespit edildi ({info['scripting_backend'] or 'backend?'}"
        if info["unity_version"]:
            detail += f", {info['unity_version']}"
        bus.ok(detail + ")")
    if info["product_name"]:
        bus.info(f"Urun adi: {info['product_name']} - {info['company']}")
    if missing_vc:
        bus.warn(
            "Oyun yaninda MSVC runtime yok: " + ", ".join(missing_vc)
            + " -> redistributable kurulumu onerilir."
        )
    else:
        bus.ok("MSVC runtime DLL'leri oyunla birlikte geliyor.")
    return info


def recommended_verbs(info: dict) -> list[str]:
    """Tespit sonucuna gore onerilen winetricks verb'leri.

    Tahmin degil, gozleme dayali: MSVC runtime DLL'leri oyunla gelmiyorsa
    IL2CPP derlemeleri onlar olmadan acilmaz.
    """
    verbs: list[str] = []
    if info.get("missing_vcruntime"):
        verbs.append("vcrun2022")
    return verbs
