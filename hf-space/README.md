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

# AŞAMA A — Wine Prefix Hazırlama Space'i

Bu Space, Winlator tabanlı **tek-oyuna-özel bir Android APK** üretmenin ilk
yarısıdır: Windows oyununu bir Wine prefix'ine kurar, doğrular ve Android
tarafına gömülecek **delta paketini** çıkarır.

## Neden bu yaklaşım

Winlator'ın kendi Wine'ı (`/opt/wine/bin/wine`, **wine-10.10 custom**) native
bir **x86_64 ELF**'tir ve sadece `libc.so.6`'ya bağlıdır. Yani bu Docker
imajında **Box64/QEMU olmadan, tam hızda** çalışır.

Sonuç: prefix'i, APK'nın içindeki Wine'ın *ta kendisiyle* üretiyoruz. Ubuntu'nun
`wine` paketini kurmuyoruz; sürüm/patch uyuşmazlığı riski yok.

## Ne üretir

| Dosya | Ne işe yarar |
|---|---|
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
**8 vCPU / 32 GB** (paralel sha256 ve `zstd -T0` tüm çekirdekleri kullanır).

### Secrets (opsiyonel)

Oyun dosyalarını private bir HF Dataset'ten çekeceksen:
`Settings → Variables and secrets → HF_TOKEN`

Oyun dosyalarını **Dockerfile'a gömme** — imaj public olabilir.

## Kullanım

1. **applicationId** gir. Uzunluk kritiktir; arayüz canlı uyarır.
2. Oyun arşivini yükle ya da HF Dataset repo'sunu gir.
3. `Build başlat`. Loglar canlı akar, tek tuşla kopyalanır.
4. Biten build'in `.zip`'ini indir → AŞAMA B'ye ver.

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
- **Shader cache önceden derlenemez.** Winlator'ın DXVK/Turnip katmanında
  build-time'da doldurulabilecek bir shader cache API'si bulamadım. Mesa'nın
  kendi cache'i (`MESA_SHADER_CACHE_*`) cihaz GPU'suna özeldir; burada
  üretilen bir cache telefonda geçersiz olurdu. Bu yüzden bu adım
  **bilinçli olarak atlandı** — uydurulmuş bir çözüm koymaktansa yokluğunu
  belirtmeyi tercih ettim.
