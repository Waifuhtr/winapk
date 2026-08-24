# Winlator Tabanlı Tek-Oyun Android Port Aracı

Kaynak kodu elde olmayan, yalnızca derlenmiş Windows (x86_64) build'i bulunan
**kendi oyununu**, Winlator'ın çalışma zamanı altyapısı (Wine + Box64 +
DXVK/VKD3D + Mesa) üzerinde çalışan **tek-oyuna-özel bir Android APK**'ya
dönüştürür.

Kullanıcı deneyimi: APK'yı kur → aç → kısa bir yükleme ekranı → oyun sanal
kontrollerle ekranda. Container seçimi yok, ayar menüsü yok, "oyun ekle" yok.

> Bu genel amaçlı bir emülatör ya da "herhangi bir oyunu portla" aracı değildir.
> Sistem tek bir oyuna gömülüdür ve build-time'da sabitlenir.

---

## Yapı

```
.
├── NASIL_CALISTIRILIR.md    ← build/test adımları burada
├── hf-space/                HF Space — her iki aşama da burada çalışır
│   ├── pipeline/                AŞAMA A (prefix/delta) + AŞAMA B (APK)
│   ├── static/                  web arayüzü (canlı log, kopyala)
│   └── android/                 overlay + patches.json (TEK KOPYA)
└── android/setup.sh         AŞAMA B'yi yerelde derlemek isteyenler için
```

Oyun zip'ini Space'e yükle → tek tuşla `.apk` al. Derleme arka planda sürer,
tarayıcıyı kapatabilirsin.

İki aşama kod olarak hâlâ bağımsızdır: AŞAMA A'nın çıktısı (delta paketi +
`game_config.json`) statik bir veri paketidir; Android tarafı Wine kurulum
mantığını **bilmez**, sadece hazır paketi açıp çalıştırır. İstersen AŞAMA B'yi
kapatıp APK'yı yerelde derleyebilirsin.

---

## Winlator kaynağından doğrulanan bulgular

Aşağıdakiler varsayım değil; pin'lenmiş kaynak üzerinde ölçülerek/çalıştırılarak
doğrulandı. Prompt'taki bazı varsayımlar bu yüzden düzeltildi.

| Konu | Bulgu |
|---|---|
| Uygulama dili | Winlator **%100 Java** (269 dosya, 0 Kotlin). Yeni katman Kotlin, upstream'e dokunulmadı. |
| `input_controls` | Modül değil — **54 adet `.icp`** (düz JSON) oyun profili. App içinde 4 şablon var. |
| `gladio` / `vortek` | Çekirdek runtime değil — sırasıyla **OpenGL** ve **Vulkan** uyumluluk katmanları (C). |
| Wine | **wine-10.10 custom**, native **x86_64 ELF**, yalnızca `libc.so.6`'ya bağlı. |
| rootfs | **aarch64** glibc userland; Wine x86_64 olarak Box64 altında koşuyor. |
| Prefix şablonu | `container_pattern.tzst` (7 MB → 55 MB) APK'da zaten var. |
| Ortak DLL'ler | Container kurulurken rootfs'ten **1394 DLL (~280 MB)** kopyalanıyor. |
| Gömülü yol | `/data/data/com.winlator/files/rootfs` → rootfs'te **165 dosyada 447 kez** derlenmiş halde. `TMPDIR` bunu **ezmiyor**. |
| Başlatma yolu | `XServerDisplayActivity` + `container_id` + `shortcut_path`. Ayarlar `.desktop` içindeki `[Extra Data]` bölümünden okunuyor. |

### En önemli iki sonuç

**1. Prefix'i APK'nın kendi Wine'ıyla üretiyoruz.**
Winlator'ın Wine'ı x86_64 native olduğu için Space'in Docker'ında Box64
olmadan çalışıyor. Bu, prompt'un endişelendiği "Ubuntu Wine sürümü tutmaz"
riskini tamamen ortadan kaldırıyor.

```
wine --version   → wine-10.10
wineboot -u      → exit 0
wine cmd /c ver  → Microsoft Windows 10.0.19045
```

**2. Tam prefix değil, delta paketliyoruz.**
APK zaten prefix şablonunu ve 280 MB DLL'i taşıdığı için tam prefix'i gömmek
~300 MB'ı ikinci kez taşımak olurdu. Bunun yerine pristine container'a göre
fark alınıyor:

| | Dosya | Boyut |
|---|---|---|
| Tam prefix | 1717 | ~313 MB |
| **Delta** | **97** | **9.8 MB** |

Aynı mantık `rootfs.tzst` için de geçerli: yeniden paketlerken `home/`
(container) ve `tmp/` (winetricks önbelleği) hariç tutulur — upstream'in
kendi arşivinde de bunlar boş dizindir. Aksi halde oyun APK'ya **iki kez**
girer. Ölçüm: hariç tutmadan 176.5 MB, tutunca **62.9 MB**
(upstream referansı: 62.2 MB).

---

## applicationId kısıtı

Gömülü yol yüzünden `applicationId` serbestçe seçilemez. AŞAMA A rootfs'i
byte-patch'ler:

- **Tam 12 karakter** (`com.winlator` ile aynı uzunluk) → offset kaymaz, **en güvenlisi**
- 12'den kısa → NUL padding ile çalışır
- 12'den uzun → desteklenmiyor

`com.gameport` ile doğrulandı: `wineboot -u` ve `wine cmd` sorunsuz çalıştı.

`com.winlator` olarak bırakmak da mümkündür (hiç yama gerekmez) ama cihazda
gerçek Winlator kuruluysa APK kurulamaz.

---

## Lisans

Winlator **LGPL-2.1**. Bu depo Winlator kaynağının değiştirilmiş bir kopyasını
**taşımaz**; `android/setup.sh` upstream'i pin'li commit'ten çeker ve üstüne
ayrı bir katman serer. Upstream kaynağına yalnızca iki cerrahi yama uygulanır
ve ikisi de `setup.sh` içinde açıkça görülebilir.

Dağıtacağın APK LGPL-2.1 yükümlülüklerini taşır: lisans metnini koru, Winlator
kaynağına ve yaptığın değişikliklere erişim sağla.

Oyun dosyaların sana aittir ve bu depoda yer almaz.
