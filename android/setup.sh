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

# Overlay ve yama tanimlari hf-space altinda duruyor: Space'in APK'yi kendi
# icinde derleyebilmesi icin oraya tasindi ve TEK KOPYA orada. Bu script de
# ayni kopyayi kullanir, boylece yerel build ile Space build'i asla ayrismaz.
OVERLAY_DIR="${OVERLAY_DIR:-$HERE/../hf-space/android/overlay}"
PATCHES_FILE="${PATCHES_FILE:-$HERE/../hf-space/android/patches.json}"

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
if [[ ! -d "$OVERLAY_DIR" ]]; then
    echo "HATA: Overlay bulunamadı: $OVERLAY_DIR" >&2
    echo "      hf-space/android/overlay klasörünün yanında olması gerekiyor." >&2
    exit 1
fi
say "Tek-oyun katmanı kopyalanıyor"
cp -a "$OVERLAY_DIR/." "$PROJECT/"

# Upstream deposunda gradlew'in calistirma biti YOK; her kullanici
# "Permission denied" ile karsilasiyor. Burada bir kez duzeltiyoruz.
chmod +x "$PROJECT/gradlew"

# ------------------------------------------------------------- 3) yamalar
say "Upstream kaynağına cerrahi yamalar uygulanıyor"
if [[ ! -f "$PATCHES_FILE" ]]; then
    echo "HATA: Yama tanımları bulunamadı: $PATCHES_FILE" >&2
    exit 1
fi
# Yamalar patches.json'dan okunur (Space pipeline'i ile ayni kaynak).
python3 - "$PROJECT" "$PATCHES_FILE" <<'PYEOF'
import json, pathlib, sys

project = pathlib.Path(sys.argv[1])
with open(sys.argv[2], encoding="utf-8") as fh:
    patches = json.load(fh)["patches"]

for patch in patches:
    path = project / patch["file"]
    if not path.is_file():
        raise SystemExit(f"HATA: Yamalanacak dosya yok: {patch['file']}")
    text = path.read_text(encoding="utf-8")

    if patch["marker"] in text:
        print(f"  atlandi   : {patch['id']} (zaten yamali)")
        continue
    if patch["anchor"] not in text:
        raise SystemExit(
            f"YAMA CAPASI BULUNAMADI: {patch['id']} ({patch['file']})\n"
            f"  aranan: {patch['anchor'][:120]}\n"
            "  Upstream pin'i degismis olabilir. patches.json ile kaynagi\n"
            "  karsilastir ve yamayi guncelle."
        )
    path.write_text(text.replace(patch["anchor"], patch["replacement"], 1),
                    encoding="utf-8")
    print(f"  yamalandi : {patch['id']} -> {patch['file']}")
PYEOF
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
