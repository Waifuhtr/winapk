# AŞAMA B — Android Projesi

AŞAMA A'nın ürettiği delta paketini alıp **tek-oyuna-özel bir APK**'ya
dönüştürür. Kullanıcı container seçmez, ayar görmez: uygulama açılır,
gerekiyorsa kurulumu yapar, doğrudan oyunu başlatır.

## Neden fork değil, overlay

Winlator **LGPL-2.1**. 269 Java dosyasını kopyalayıp dağıtmak yerine
`setup.sh` upstream'i **pin'li commit'ten** çeker ve üstüne bu klasördeki
katmanı serer. Böylece:

- Hangi satırın bize ait olduğu net kalır (lisans uyumu için önemli)
- Upstream güncellemesi tek satır pin değişikliği olur
- Bu depo LGPL kodun değiştirilmiş bir kopyasını taşımaz

Upstream kaynağına **yalnızca iki cerrahi yama** uygulanır; ikisi de çapa
doğrulamalıdır (çapa bulunamazsa script hata verip durur, sessizce yanlış
yere yazmaz).

## Yapı

```
android/
├── setup.sh                 Projeyi kuran script (klon + overlay + yama + asset)
└── overlay/
    ├── build.gradle             kök — Kotlin eklentisi eklendi
    └── app/
        ├── build.gradle         applicationId/etiket port.properties'ten
        └── src/main/
            ├── AndroidManifest.xml       giriş noktası GameLauncherActivity
            ├── res/layout/game_launcher_activity.xml
            ├── res/values/gameport.xml
            └── java/com/winlator/gameport/
                ├── GamePortConfig.kt      game_config.json okuyucusu
                ├── GameInstaller.kt       kurulum motoru (Coroutines + StateFlow)
                ├── GameLauncherActivity.kt  splash + ilerleme + başlatma
                └── GamePortMenu.kt        oyun içi menü sadeleştirme
```

## Kullanım

```bash
unzip winlator-port-MyGame.zip -d /tmp/port
./setup.sh --artifacts /tmp/port
# özel sanal joystick profili ile:
./setup.sh --artifacts /tmp/port --controls ./MyGame.icp
```

Sonra:

```bash
cd build/winlator-app
./gradlew assembleDebug
```

## Neden Kotlin sadece yeni kodda

Prompt Kotlin istiyordu; ancak Winlator'ın kaynağı **%100 Java** (269 dosya,
0 Kotlin). Hepsini çevirmek saf risk. Bu yüzden upstream'e hiç dokunulmadı ve
tek-oyun katmanı (`com.winlator.gameport`) Kotlin yazıldı — ikisi aynı modülde
sorunsuz derlenir. Coroutines + StateFlow ile ilerleme akışı prompt'ta
istendiği gibi.

## İlk açılışta ne olur

1. **Sistem dosyaları** — `rootfs.tzst` açılır (yalnızca ilk kurulumda / sürüm
   değişiminde). İlerleme yüzdesi gösterilir.
2. **Ortam** — Winlator'ın kendi `ContainerManager`'ı ile container oluşturulur
   (`container_pattern` + 1394 ortak DLL). Bu, AŞAMA A'daki "pristine" durumun
   birebir aynısıdır.
3. **Oyun dosyaları** — `game_payload.tzst` container üzerine serilir,
   AŞAMA A'da silinen yollar cihazda da silinir.
4. **Kısayol** — `[Extra Data]` bölümünde sabit ayarlarla `.desktop` yazılır.
5. **Başlatma** — `XServerDisplayActivity` doğrudan açılır.

2–4. adımlar bir "stamp" dosyasıyla korunur: APK güncellenip payload değişirse
kurulum kendini yeniler, değişmediyse açılış anında geçilir.

## Sabitlenen ayarlar

Hepsi `game_config.json`'dan gelir; kullanıcıya hiçbir seçenek sunulmaz.

| Ayar | Değer | Neden |
|---|---|---|
| Box64 preset | `STABILITY` | Winlator README'sinin Unity için önerisi |
| Exec args | `-force-gfx-direct` | Aynı öneri |
| Grafik | `vortek,gladio` | Vortek host Vulkan üstünde çalışır → en geniş cihaz uyumu |
| DX sarmalayıcı | `dxvk` | Unity D3D11 için standart |
| Tam ekran | açık | Düşük çözünürlüklü oyunlarda görüntü oranı sorunlarını çözer |

`turnip` daha hızlıdır ama **yalnızca Adreno** GPU'larda çalışır. Belirli bir
cihaz hedefliyorsan AŞAMA A arayüzünden seç.

## Bilinen kısıtlar

- **`targetSdkVersion 28` korunuyor.** Winlator'ın rootfs/exec akışı legacy
  depolama davranışına dayanıyor. Google Play bu değeri kabul etmez; mağaza
  dağıtımı ayrı bir çalışma konusudur (doğrudan APK dağıtımı sorunsuz).
- **`namespace` `com.winlator` kalmalı.** R sınıfı ve 269 Java dosyasının
  paketi bu. Kimlik ayrımı `applicationId` ile yapılır — bu yüzden
  FileProvider authority'si `${applicationId}.FileProvider`'a bağlandı,
  aksi halde cihazda gerçek Winlator kuruluyken kurulum reddedilirdi.
- **CPU affinity uygulanmadı.** Prompt bunu "best-effort" olarak istiyordu.
  Kurulum thread'ine öncelik veriliyor, ancak **Wine sürecinin** çekirdeklere
  sabitlenmesi uygulama sürecinden erişilebilir değil (Winlator oyunu kendi
  `ProcessHelper`'ı ile ayrı süreçte başlatıyor; `sched_setaffinity` başka bir
  sürece root olmadan uygulanamaz). Çalışmayacak bir kod yazmaktansa
  yazmamayı tercih ettim.
