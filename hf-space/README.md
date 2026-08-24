---
title: Winlator Tek-Oyun Port Aracı
emoji: 🎮
colorFrom: blue
colorTo: gray
sdk: docker
app_port: 7860
pinned: false
short_description: Winlator tabanlı tek-oyun Android port'u için Wine prefix hazırlar
---

# Winlator Tek-Oyun Port Aracı — Tam Boru Hattı

Bu Space, Windows (x86_64) oyununu **tek-oyuna-özel bir Android APK**'ya
dönüştürür. İki aşama da burada çalışır:

- **AŞAMA A** — Wine prefix hazırlama, doğrulama, delta paketleme
- **AŞAMA B** — Winlator kaynağını çekip APK'yı derleme (varsayılan açık)

Derleme **arka planda** sürer: tarayıcıyı kapatabilirsin. Geri döndüğünde log
ve çıktılar burada bekliyor olur (log diske de yazılıyor, Space yeniden
başlasa bile kaybolmuyor).

## Neden bu yaklaşım

Winlator'ın kendi Wine'ı (`/opt/wine/bin/wine`, **wine-10.10 custom**) native
bir **x86_64 ELF**'tir ve sadece `libc.so.6`'ya bağlıdır. Yani bu Docker
imajında **Box64/QEMU olmadan, tam hızda** çalışır.

Sonuç: prefix'i, APK'nın içindeki Wine'ın *ta kendisiyle* üretiyoruz. Ubuntu'nun
`wine` paketini kurmuyoruz; sürüm/patch uyuşmazlığı riski yok.

## Ne üretir

| Dosya | Ne işe yarar |
|---|---|
| `<Oyun>-debug.apk` | **Kurulmaya hazır APK** (AŞAMA B açıksa) |
> APK boyutu ≈ 110 MB (Winlator'ın kendi asset'leri) + oyununun delta paketi.
> `zstd seviyesi`ni düşürürsen rootfs büyür; varsayılan 19'da bırak.
| `game_payload.tzst` | **Delta paket** — pristine container'a göre değişen her şey |
| `game_config.json` | Android tarafıyla tek sözleşme (exe yolu, container ayarları) |
| `rootfs.tzst` | `applicationId` yamalanmış rootfs (varsayılandan farklıysa) |
| `build_report.json` | Tespit sonuçları, import kontrolü, smoke test çıktısı |
| `smoke_test.png` | Oyunun Xvfb ekranından alınan görüntü (varsa) |

### Neden "delta", tam prefix değil

APK zaten `container_pattern.tzst` (7 MB → 55 MB) taşıyor ve container
oluştururken rootfs'ten **1394 DLL (~280 MB)** kopyalıyor. Tam prefix'i
paketlemek bu ~300 MB'ı APK'da **ikinci kez** taşımak demek.

Ölçüm (test oyunuyla): tam prefix **1717 dosya**, delta **97 dosya / 9.8 MB**.

## Kurulum

1. Yeni bir **Docker** SDK'lı Space aç
2. Bu klasördeki dosyaları repoya yükle
3. Build bitince arayüz açılır

### Donanım

CPU-only yeterlidir; oyun burada **çalıştırılmaz**, sadece kurulur. Öneri:
**8 vCPU / 32 GB** (paralel sha256, `zstd -T0` ve Gradle tüm çekirdekleri
kullanır).

### İmaj boyutu uyarısı

APK derlemesi için imaja **JDK 17 + Android SDK 35 + NDK 24.0.8215888 +
CMake 3.22.1** gömülü. Bu, imajı ~5 GB büyütür ve ilk Space build'ini
15-25 dakikaya çıkarır. Bu **tek seferlik** bir maliyet: HF imaj katmanlarını
önbelleğe alır, sonraki başlatmalar hızlıdır.

NDK sürümü **birebir 24.0.8215888** olmalı — Winlator'ın `app/build.gradle`'ı
`ndkVersion` ile bunu sabitlemiş; farklı sürüm CMake yapısını bozar.

APK derlemesini istemiyorsan Dockerfile'daki Android SDK katmanını silebilir
ve arayüzdeki "Bitince APK'yı da derle" kutusunu kapatabilirsin.

### Secrets (opsiyonel)

Oyun dosyalarını private bir HF Dataset'ten çekeceksen:
`Settings → Variables and secrets → HF_TOKEN`

Oyun dosyalarını **Dockerfile'a gömme** — imaj public olabilir.

## Kullanım

1. **applicationId** gir. Uzunluk kritiktir; arayüz canlı uyarır.
2. Oyun arşivini yükle ya da HF Dataset repo'sunu gir.
3. `Build başlat`. Loglar canlı akar, tek tuşla kopyalanır.
4. **Sayfayı kapatabilirsin** — derleme sunucuda sürer.
5. Bitince çıktı listesinden **`.apk`** dosyasını indir ve cihazına kur.
   (APK derlemesini kapattıysan `.zip`'i indirip yerelde `android/setup.sh`
   ile derlersin.)

### Süre beklentisi

| Aşama | Süre |
|---|---|
| AŞAMA A (prefix + delta) | ~1-5 dk (oyun boyutuna göre) |
| AŞAMA B ilk derleme | 5-15 dk (Gradle dağıtımı + bağımlılık indirmesi dahil) |
| AŞAMA B sonraki derlemeler | 2-5 dk (Gradle önbelleği ısınmış olur) |

> Ölçüm: 8 vCPU'lu bir makinede Gradle'ın kendi raporladığı süre
> **2 dk 29 sn** (native CMake/NDK derlemesi ve R8 dahil). Buna Gradle
> dağıtımının ve bağımlılıkların ilk indirilmesi eklenir.

## applicationId uzunluk kısıtı

`/data/data/com.winlator/files/rootfs` yolu rootfs'teki **165 dosyaya 447 kez
derlenmiş halde gömülü** (`ntdll.so` ve `wineserver` dahil). `TMPDIR` gibi env
değişkenleri bunu **ezmez** — denendi.

Bu yüzden farklı bir `applicationId` istendiğinde rootfs byte-patch'lenir:

- **Tam 12 karakter** (`com.winlator` ile aynı uzunluk) → offset kaymaz, en güvenlisi
- 12'den kısa → NUL padding ile çalışır
- 12'den uzun → **desteklenmiyor**

`com.gameport` ile test edildi: `wineboot -u` ve `wine cmd` sorunsuz çalıştı.

## Bilinen sınırlar

- **Smoke test oynanabilirlik testi değildir.** Space'te GPU yok; amaç
  "prefix bozuk değil ve exe'nin bağımlılıkları çözülüyor" doğrulaması.
  Gerçek performans yalnızca cihazda ölçülür.
- **APK `debug` varyantı olarak derlenir** ve Gradle'ın otomatik debug
  anahtarıyla imzalanır. Yandan yükleme (sideload) için sorunsuzdur; mağaza
  dağıtımı için kendi anahtarınla yeniden imzalaman gerekir (bkz.
  `NASIL_CALISTIRILIR.md`). Upstream `debug` bloğunda `minifyEnabled true`
  tanımlı olduğu için bu yine de küçültülmüş bir derlemedir.
- **Shader cache önceden derlenemez.** Winlator'ın DXVK/Turnip katmanında
  build-time'da doldurulabilecek bir shader cache API'si bulamadım. Mesa'nın
  kendi cache'i (`MESA_SHADER_CACHE_*`) cihaz GPU'suna özeldir; burada
  üretilen bir cache telefonda geçersiz olurdu. Bu yüzden bu adım
  **bilinçli olarak atlandı** — uydurulmuş bir çözüm koymaktansa yokluğunu
  belirtmeyi tercih ettim.
