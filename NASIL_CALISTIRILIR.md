# NASIL ÇALIŞTIRILIR

İki aşama birbirinden bağımsızdır. AŞAMA A bir veri paketi üretir; AŞAMA B onu
APK'ya gömer.

```
AŞAMA A (HF Space, Docker)          AŞAMA B (Android, yerel makine)
 oyun.zip ─► Wine prefix ─► delta ──►  setup.sh ─► gradlew ─► game.apk
```

---

## AŞAMA A — HF Space

### 1. Space'i oluştur

1. huggingface.co → **New Space**
2. SDK: **Docker** (Gradio/Streamlit değil)
3. Donanım: CPU. **8 vCPU / 32 GB** önerilir (paralel hash ve `zstd -T0`
   tüm çekirdekleri kullanır)
4. `hf-space/` klasöründeki **tüm dosyaları** Space repo'sunun köküne yükle

Yapı şöyle olmalı:

```
<space-repo>/
├── Dockerfile
├── README.md          ← HF frontmatter'ı burada, silme
├── requirements.txt
├── app.py
├── pipeline/
├── scripts/
└── static/
```

### 2. (Opsiyonel) Private dataset için secret

Oyun dosyalarını private bir HF Dataset'ten çekeceksen:

`Settings → Variables and secrets → New secret`
· Name: `HF_TOKEN` · Value: HF erişim token'ın

> Oyun dosyalarını **Dockerfile'a gömme** — imaj public olabilir.

### 3. Yerelde denemek (opsiyonel)

```bash
cd hf-space
docker build -t winlator-port .
docker run --rm -p 7860:7860 winlator-port
# http://localhost:7860
```

Kalıcı önbellek istersen (rootfs'i her seferinde indirmesin):

```bash
docker run --rm -p 7860:7860 -v "$PWD/.cache:/build/cache" winlator-port
```

### 4. Kullanım

| Alan | Not |
|---|---|
| **applicationId** | **Tam 12 karakter** kullan (ör. `com.gameport`). Arayüz canlı doğrular. Sebebi aşağıda. |
| **Oyun adı** | Boş bırak → Unity `app.info` dosyasından okunur |
| **Oyun kaynağı** | Zip sürükle-bırak veya HF Dataset repo id'si |
| **Exe yolu** | Boş bırak → `<Ad>.exe` + `<Ad>_Data/` eşleşmesinden bulunur |
| **Box64 preset** | `STABILITY` (Unity için önerilen) |
| **Exec args** | `-force-gfx-direct` |

`Build başlat` → loglar canlı akar. Hata olursa **Kopyala** ile tüm logu
alabilirsin.

Biten build `winlator-port-<Oyun>.zip` üretir. İndir.

---

## AŞAMA B — Android

### Gereksinimler

| Araç | Sürüm | Not |
|---|---|---|
| JDK | 17 | AGP 8.4.2 gerektirir |
| Android SDK | Platform 35, Build-Tools 35 | |
| Android NDK | **24.0.8215888** | Sürüm birebir; Winlator'ın CMake yapısı buna bağlı |
| CMake | 3.22.1 | SDK Manager'dan |
| Git, Python 3 | — | `setup.sh` kullanıyor |

```bash
sdkmanager "platforms;android-35" "build-tools;35.0.0" \
           "ndk;24.0.8215888" "cmake;3.22.1"
```

### 1. Projeyi hazırla

```bash
unzip winlator-port-MyGame.zip -d /tmp/port
cd android
./setup.sh --artifacts /tmp/port
```

Script şunları yapar: Winlator'ı pin'li commit'ten klonlar → tek-oyun katmanını
serer → upstream'e iki cerrahi yama uygular → AŞAMA A çıktılarını assets'e
koyar → `port.properties` üretir.

Özel sanal joystick profili varsa:

```bash
./setup.sh --artifacts /tmp/port --controls ./MyGame.icp
```

### 2. Derle

```bash
cd build/winlator-app
./gradlew assembleDebug
# APK: app/build/outputs/apk/debug/app-debug.apk
```

> `assembleDebug` da minify edilmiş bir APK üretir — upstream `debug` bloğunda
> `minifyEnabled true` tanımlamış, biz de bozmadık.

### 3. Dağıtım için imzala

```bash
keytool -genkey -v -keystore mygame.keystore -alias mygame \
        -keyalg RSA -keysize 2048 -validity 10000

cd build/winlator-app
./gradlew assembleRelease

$ANDROID_HOME/build-tools/35.0.0/apksigner sign \
    --ks mygame.keystore --out mygame-signed.apk \
    app/build/outputs/apk/release/app-release-unsigned.apk
```

> Anahtarını **depoya koyma.**

---

## Sanal joystick profili nasıl hazırlanır

Sıfırdan tasarlamaya gerek yok — Winlator'ın kendi editörünü kullan:

1. Normal Winlator APK'sını bir cihaza kur
2. `Input Controls` → yeni profil → oyununa göre düzenle
3. Profili dışa aktar (`.icp` dosyası — düz JSON)
4. `./setup.sh --artifacts ... --controls ./profil.icp`

Hiç profil vermezsen Winlator'ın hazır `controls-1` profili kullanılır.
`winlator` deposundaki `input_controls/` klasöründe 54 hazır oyun profili var;
oyununa benzeyen bir tanesini başlangıç olarak alabilirsin.

---

## Sorun giderme

**Build "rootfs içinde gömülü applicationId bulunamadı" diyor**
Winlator pin'i değişmiş olabilir. `hf-space/pipeline/config.py` içindeki
`WINLATOR_APP_PIN` ile `android/setup.sh` içindeki pin **aynı olmalı**.

**setup.sh "YAMA ÇAPASI BULUNAMADI" diyor**
Upstream kaynağı pin'den farklı. Script bilerek durur — yanlış yere yazmaz.
Pin'i kontrol et ya da yamayı güncelle.

**Import kontrolü eksik DLL bildiriyor**
Genelde MSVC runtime'dır. AŞAMA A'da `Ek winetricks verb'leri` alanına
`vcrun2022` yaz ve tekrar build al.

**APK kurulmuyor: "duplicate provider authority"**
Cihazda gerçek Winlator kurulu ve `applicationId`'yi `com.winlator` bırakmışsın.
Farklı (12 karakterlik) bir id ile tekrar build al.

**Oyun açılıyor ama siyah ekran**
Grafik sürücüsünü değiştirip tekrar dene: `vortek,gladio` ↔ `turnip,gladio`.
`turnip` yalnızca Adreno GPU'larda çalışır.
