#!/usr/bin/env bash
# ============================================================================
#  AŞAMA B — Android projesini hazırla
# ============================================================================
#  Ne yapar:
#    1) Winlator kaynağını PIN'Lİ commit'lerden klonlar
#    2) overlay/ altındaki dosyaları üzerine kopyalar
#    3) Upstream kaynağına iki cerrahi yama uygular (çapa doğrulamalı)
#    4) AŞAMA A çıktılarını assets'e yerleştirir
#    5) game_config.json'dan port.properties üretir
#
#  Neden fork yerine overlay: Winlator LGPL-2.1. 269 Java dosyasını kopyalayıp
#  dağıtmak yerine upstream'i olduğu gibi çekip üstüne kendi katmanımızı
#  koyuyoruz. Böylece hangi satırın bize ait olduğu net kalıyor ve upstream
#  güncellemesi tek satır pin değişikliği oluyor.
# ============================================================================
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
BUILD_ROOT="${BUILD_ROOT:-$HERE/build}"
PROJECT="$BUILD_ROOT/winlator-app"

# Pin'ler — AŞAMA A'daki pipeline/config.py ile AYNI olmalı.
WINLATOR_APP_REPO="https://github.com/brunodev85/winlator-app.git"
WINLATOR_APP_PIN="4f55d117fff1542944e5b91f433470445160ce08"
WINLATOR_REPO="https://github.com/brunodev85/winlator.git"
WINLATOR_PIN="5949297d9dc83ad24ce3f5119fe382da7c899a78"

ARTIFACTS=""
CONTROLS_ICP=""

usage() {
    cat <<'USAGE'
Kullanım:
  ./setup.sh --artifacts <dizin> [--controls <profil.icp>]

  --artifacts   AŞAMA A'nın ürettiği zip'in açılmış hâli. İçinde en az
                game_config.json ve game_payload.tzst olmalı; applicationId
                değiştirildiyse rootfs.tzst de bulunmalı.
  --controls    Sanal joystick profili (.icp). Verilmezse Winlator'ın hazır
                controls-1 profili kullanılır.

Örnek:
  unzip winlator-port-MyGame.zip -d /tmp/port
  ./setup.sh --artifacts /tmp/port
USAGE
}

while [[ $# -gt 0 ]]; do
    case "$1" in
        --artifacts) ARTIFACTS="${2:-}"; shift 2 ;;
        --controls)  CONTROLS_ICP="${2:-}"; shift 2 ;;
        -h|--help)   usage; exit 0 ;;
        *) echo "Bilinmeyen argüman: $1" >&2; usage; exit 1 ;;
    esac
done

if [[ -z "$ARTIFACTS" ]]; then
    echo "HATA: --artifacts zorunlu." >&2
    usage
    exit 1
fi
ARTIFACTS="$(cd "$ARTIFACTS" && pwd)"

for required in game_config.json game_payload.tzst; do
    if [[ ! -f "$ARTIFACTS/$required" ]]; then
        echo "HATA: $ARTIFACTS/$required bulunamadı." >&2
        exit 1
    fi
done

say() { printf '\n\033[1;36m==> %s\033[0m\n' "$*"; }

# ---------------------------------------------------------------- 1) klonla
say "Winlator kaynağı çekiliyor (pin ${WINLATOR_APP_PIN:0:10})"
mkdir -p "$BUILD_ROOT"
if [[ ! -d "$PROJECT/.git" ]]; then
    git clone "$WINLATOR_APP_REPO" "$PROJECT"
fi
git -C "$PROJECT" fetch --all --tags --quiet
git -C "$PROJECT" checkout --quiet --force "$WINLATOR_APP_PIN"
git -C "$PROJECT" clean -fd --quiet -e build -e .gradle -e local.properties

# input_controls profilleri ana depoda; sadece --controls verilmediyse lazım.
MAIN_REPO="$BUILD_ROOT/winlator"
if [[ -z "$CONTROLS_ICP" && ! -d "$MAIN_REPO/.git" ]]; then
    say "Hazır input profilleri için ana depo çekiliyor"
    git clone --depth 1 "$WINLATOR_REPO" "$MAIN_REPO" || \
        echo "UYARI: ana depo çekilemedi; yerleşik profiller kullanılacak."
fi

# ------------------------------------------------------------- 2) overlay
say "Tek-oyun katmanı kopyalanıyor"
cp -a "$HERE/overlay/." "$PROJECT/"

# Upstream deposunda gradlew'in calistirma biti YOK; her kullanici
# "Permission denied" ile karsilasiyor. Burada bir kez duzeltiyoruz.
chmod +x "$PROJECT/gradlew"

# ------------------------------------------------------------- 3) yamalar
say "Upstream kaynağına cerrahi yamalar uygulanıyor"
python3 - "$PROJECT" <<'PY'
import sys, pathlib

project = pathlib.Path(sys.argv[1])
applied, skipped = [], []

def patch(rel, anchor, replacement, marker):
    path = project / rel
    text = path.read_text(encoding="utf-8")
    if marker in text:
        skipped.append(f"{rel} (zaten yamalı)")
        return
    if anchor not in text:
        raise SystemExit(
            f"YAMA ÇAPASI BULUNAMADI: {rel}\n"
            f"  aranan: {anchor!r}\n"
            "  Upstream pin'i değişmiş olabilir. setup.sh içindeki pin ile\n"
            "  kaynağı karşılaştır ve yamayı güncelle."
        )
    path.write_text(text.replace(anchor, replacement, 1), encoding="utf-8")
    applied.append(rel)

# (a) Oyun ici cekmece menusunu sadelestir: kullanici editor/ayar ekranlarina
#     ulasamasin. Tek satirlik cagri; menu ogeleri gizlenir, silinmez.
patch(
    "app/src/main/java/com/winlator/XServerDisplayActivity.java",
    "        menu.findItem(R.id.menu_item_logs).setVisible(enableLogs);",
    "        menu.findItem(R.id.menu_item_logs).setVisible(enableLogs);\n"
    "        com.winlator.gameport.GamePortMenu.apply(menu);",
    "GamePortMenu.apply(menu)",
)

# (b) FileProvider authority'si manifest'te ${applicationId}.FileProvider oldu;
#     kodda sabit "com.winlator.FileProvider" kalirsa bu yol cagrilirsa
#     ActivityNotFound/IllegalArgument ile patlar.
patch(
    "app/src/main/java/com/winlator/core/FileUtils.java",
    'FileProvider.getUriForFile(activity, "com.winlator.FileProvider", file)',
    'FileProvider.getUriForFile(activity, '
    'activity.getPackageName() + ".FileProvider", file)',
    'getPackageName() + ".FileProvider"',
)

for item in applied:
    print(f"  yamalandı : {item}")
for item in skipped:
    print(f"  atlandı   : {item}")
PY

# ------------------------------------------------- 4) AŞAMA A çıktıları
say "AŞAMA A çıktıları assets'e yerleştiriliyor"
ASSETS="$PROJECT/app/src/main/assets"
mkdir -p "$ASSETS/gameport"
cp "$ARTIFACTS/game_config.json"   "$ASSETS/gameport/"
cp "$ARTIFACTS/game_payload.tzst"  "$ASSETS/gameport/"

if [[ -f "$ARTIFACTS/rootfs.tzst" ]]; then
    echo "  yamalanmış rootfs.tzst kullanılıyor (applicationId değiştirildi)"
    cp "$ARTIFACTS/rootfs.tzst" "$ASSETS/rootfs.tzst"
else
    echo "  rootfs.tzst verilmedi; upstream'inki korunuyor (applicationId=com.winlator)"
fi

if [[ -n "$CONTROLS_ICP" ]]; then
    echo "  özel input profili: $(basename "$CONTROLS_ICP")"
    cp "$CONTROLS_ICP" "$ASSETS/inputcontrols/profiles/controls-1.icp"
fi

# ------------------------------------------------- 5) port.properties
say "port.properties üretiliyor"
python3 - "$ARTIFACTS/game_config.json" "$PROJECT/port.properties" <<'PY'
import json, sys

config = json.load(open(sys.argv[1], encoding="utf-8"))
lines = [
    "# setup.sh tarafindan game_config.json'dan uretildi. Elle duzenleme.",
    f"gameport.applicationId={config['appId']}",
    f"gameport.appLabel={config.get('appLabel') or config['game']['name']}",
    f"gameport.versionName={config.get('versionName', '1.0')}",
    f"gameport.versionCode={config.get('versionCode', 1)}",
]
open(sys.argv[2], "w", encoding="utf-8").write("\n".join(lines) + "\n")
print("\n".join("  " + line for line in lines[1:]))
PY

# ------------------------------------------------------------------- ozet
PAYLOAD_SIZE=$(du -h "$ASSETS/gameport/game_payload.tzst" | cut -f1)
ASSETS_SIZE=$(du -sh "$ASSETS" | cut -f1)
say "Hazır"
cat <<EOF
  Proje       : $PROJECT
  Payload     : $PAYLOAD_SIZE
  Toplam asset: $ASSETS_SIZE   (APK bu boyutun biraz üstünde olur)

  Derlemek için:
    cd "$PROJECT"
    ./gradlew assembleDebug
    # APK: app/build/outputs/apk/debug/app-debug.apk
EOF
