# NASIL ÇALIŞTIRILIR

**Varsayılan yol: her şey HF Space'te.** Oyun zip'ini yükle, tek tuşla
APK'yı al. Derleme arka planda sürer; tarayıcıyı kapatabilirsin.

```
             ┌──────────── HF Space (Docker) ────────────┐
 oyun.zip ──►│  AŞAMA A: Wine prefix ─► delta paketi     │──► game.apk
             │  AŞAMA B: Winlator kaynağı ─► gradlew     │
             └───────────────────────────────────────────┘
```

İstersen AŞAMA B'yi kapatıp APK'yı yerelde de derleyebilirsin (aşağıda
"Alternatif" bölümü). İki aşama hâlâ birbirinden bağımsız çalışır.

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

### 4. Kullanım (tam boru hattı)

| Alan | Not |
|---|---|
| **applicationId** | **Tam 12 karakter** kullan (ör. `com.gameport`). Arayüz canlı doğrular. Sebebi aşağıda. |
| **Oyun adı** | Boş bırak → Unity `app.info` dosyasından okunur |
| **Oyun kaynağı** | Zip sürükle-bırak veya HF Dataset repo id'si |
| **Exe yolu** | Boş bırak → `<Ad>.exe` + `<Ad>_Data/` eşleşmesinden bulunur |
| **Box64 preset** | `STABILITY` (Unity için önerilen) |
| **Exec args** | `-force-gfx-direct` |

| **Bitince APK'yı da derle** | Açık bırak → çıktı doğrudan `.apk` olur |

`Build başlat` → loglar canlı akar. Hata olursa **Kopyala** ile tüm logu
alabilirsin.

**Sayfayı kapatabilirsin.** Derleme sunucu tarafında sürer; geri döndüğünde
log ve çıktılar yerinde olur (log diske yazıldığı için Space yeniden başlasa
bile kaybolmaz).

Çıktılar:

| Dosya | Ne zaman |
|---|---|
| `<Oyun>-debug.apk` | APK derlemesi açıksa — **cihazına kuracağın dosya bu** |
| `winlator-port-<Oyun>.zip` | Her zaman — yerel derleme için AŞAMA A paketi |

### Süre beklentisi

| Aşama | Süre |
|---|---|
| AŞAMA A (prefix + delta) | ~1-5 dk (oyun boyutuna göre) |
| AŞAMA B ilk derleme | 5-15 dk (Gradle dağıtımı + bağımlılık indirmesi dahil) |
| AŞAMA B sonraki derlemeler | 2-5 dk (Gradle önbelleği ısınmış olur) |

> Ölçüm: 8 vCPU'lu bir makinede Gradle'ın kendi raporladığı süre
> **2 dk 29 sn** (native CMake/NDK derlemesi ve R8 dahil). Buna Gradle
> dağıtımının ve bağımlılıkların ilk indirilmesi eklenir.

### APK'yı kurma

```bash
adb install -r <Oyun>-debug.apk
```

ya da APK'yı telefona kopyalayıp dosya yöneticisinden aç ("bilinmeyen
kaynaklardan yükleme" izni gerekir).

---

## Alternatif — AŞAMA B'yi yerelde derlemek

Arayüzde "Bitince APK'yı da derle" kutusunu **kapat**, `.zip`'i indir ve
aşağıdaki adımları izle. Bu yol, kendi imzalama anahtarını kullanmak
istediğinde de gereklidir.

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

**Arayüzde "APK: yok" yazıyor / kutu kapalı geliyor**
İmajda Android SDK/NDK kurulamamış. Space'in build logunda `sdkmanager`
adımına bak. Bu durumda AŞAMA A yine çalışır; APK'yı yerelde derlersin.

**APK derlemesi "YAMA ÇAPASI BULUNAMADI" ile duruyor**
Upstream pin'i değişmiş. `hf-space/android/patches.json` içindeki çapayı
kaynakla karşılaştırıp güncelle. Script bilerek durur — yanlış yere yazmaz.

**Gradle "SDK location not found" diyor**
`ANDROID_HOME` ortam değişkeni boş. Space'te Dockerfile bunu ayarlıyor;
yerelde `local.properties` içine `sdk.dir=...` yaz.

**Build "rootfs içinde gömülü applicationId bulunamadı" diyor**
Winlator pin'i değişmiş olabilir. `hf-space/pipeline/config.py` içindeki
`WINLATOR_APP_PIN` ile `android/setup.sh` içindeki pin **aynı olmalı**.

**Import kontrolü eksik DLL bildiriyor**
Genelde MSVC runtime'dır. AŞAMA A'da `Ek winetricks verb'leri` alanına
`vcrun2022` yaz ve tekrar build al.

**APK kurulmuyor: "duplicate provider authority"**
Cihazda gerçek Winlator kurulu ve `applicationId`'yi `com.winlator` bırakmışsın.
Farklı (12 karakterlik) bir id ile tekrar build al.

**Oyun açılıyor ama siyah ekran**
Grafik sürücüsünü değiştirip tekrar dene: `vortek,gladio` ↔ `turnip,gladio`.
`turnip` yalnızca Adreno GPU'larda çalışır.
