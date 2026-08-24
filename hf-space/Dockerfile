# ==============================================================================
# AŞAMA A — Winlator Tek-Oyun Port Aracı / Prefix Hazırlama Space'i
# ==============================================================================
# Neden ubuntu:24.04 + x86_64?
#   Winlator'ın kendi Wine'ı (/opt/wine/bin/wine, wine-10.10 custom) NATIVE bir
#   x86_64 ELF'tir ve sadece libc.so.6'ya bağlıdır (readelf ile doğrulandı).
#   Yani bu imajda Box64/QEMU olmadan, tam hızda çalışır. Ubuntu'nun kendi
#   wine paketini KURMUYORUZ — prefix'i APK'nın içindeki Wine'ın ta kendisiyle
#   üretiyoruz, böylece sürüm/patch uyuşmazlığı riski sıfırlanır.
# ==============================================================================
FROM ubuntu:24.04

ENV DEBIAN_FRONTEND=noninteractive \
    LANG=C.UTF-8 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

# --- Sistem paketleri -------------------------------------------------------
# Tek RUN katmanı + apt listelerinin temizliği = küçük imaj (HF disk limiti).
#
# Gruplar:
#   1) Çalışma zamanı/araçlar : python, curl, git, arşivleyiciler
#      NOT: 'bsdtar' Ubuntu'da bağımsız bir paket ADI DEĞİL — komut
#      'libarchive-tools' paketinden gelir. 'bsdtar' yazmak noble'da
#      "Unable to locate package" ile build'i düşürür.
#   2) Xvfb + görüntü         : headless kurulum sihirbazları ve smoke test için
#   3) Wine'ın unix modülleri : lib/wine/x86_64-unix/*.so bunları dlopen eder.
#      (wine binary'sinin kendisi sadece libc ister, ama winex11.drv/winealsa
#       gibi modüller aşağıdaki kütüphaneleri runtime'da açar.)
RUN apt-get update && apt-get install -y --no-install-recommends \
        ca-certificates curl wget git jq \
        python3 python3-pip python3-venv \
        zstd xz-utils zip unzip p7zip-full cabextract \
        libarchive-tools \
        xvfb x11-utils x11-xserver-utils xauth imagemagick \
        libx11-6 libxext6 libxrender1 libxrandr2 libxi6 libxcursor1 \
        libxcomposite1 libxinerama1 libxxf86vm1 libxfixes3 libxcb1 \
        libfreetype6 libfontconfig1 fonts-liberation fonts-dejavu-core \
        libgl1 libglu1-mesa libegl1 libgbm1 libosmesa6 \
        libvulkan1 mesa-vulkan-drivers vulkan-tools \
        libasound2t64 libpulse0 \
        libgnutls30t64 libkrb5-3 libgssapi-krb5-2 libldap2 \
        libudev1 libusb-1.0-0 libcups2t64 libdbus-1-3 libsdl2-2.0-0 \
    && rm -rf /var/lib/apt/lists/* /var/cache/apt/archives/*

# --- winetricks -------------------------------------------------------------
# Redistributable kurulumları için. Winlator'ın Wine'ıyla kullanılacak
# (WINE env var'ı ile), sistem wine'ı yok.
RUN curl -fsSL -o /usr/local/bin/winetricks \
        https://raw.githubusercontent.com/Winetricks/winetricks/master/src/winetricks \
    && chmod +x /usr/local/bin/winetricks

# --- Uygulama ---------------------------------------------------------------
WORKDIR /app
COPY requirements.txt .
RUN pip3 install --break-system-packages --no-cache-dir -r requirements.txt

COPY app.py .
COPY pipeline/ ./pipeline/
COPY static/ ./static/
COPY scripts/ ./scripts/
RUN chmod +x ./scripts/*.sh 2>/dev/null || true

# --- Çalışma dizinleri ------------------------------------------------------
# ÖNEMLİ: Wine binary'sine /data/data/com.winlator/files/rootfs yolu DERLENMİŞ
# halde gömülü (rootfs'te 165 dosya / 447 tekrar). Bu yüzden o yolun var olması
# şart. Ama HF Spaces kalıcı diski /data'ya mount ettiği için oraya gerçek veri
# yazmıyoruz: /data/data/<appId>/files/rootfs sadece bir SYMLINK olacak,
# gerçek veri /build altında duracak. (Symlink ile wineserver'ın çalıştığı
# test edildi.)
RUN mkdir -p /build /data/data /work/out /work/uploads \
    && chmod -R 777 /build /data /work

ENV WORK_DIR=/work \
    BUILD_DIR=/build \
    OUT_DIR=/work/out \
    UPLOAD_DIR=/work/uploads \
    PORT=7860

EXPOSE 7860
CMD ["python3", "-u", "app.py"]
