# ---------------------------------------------------------------------------
# Ren'Py -> Android (APK/AAB) Paketleyici
# ---------------------------------------------------------------------------
# Bu imaj, resmi Ren'Py Android derleme zincirini (RAPT + Gradle) çalıştırmak
# için gereken her şeyi içerir. Derleme işini bizzat biz yürütmüyoruz;
# topluluk tarafından geliştirilen "renkit" (renutil + renconstruct) araç
# setini kullanıyoruz:
#   https://github.com/kobaltcore/renkit   (MIT lisans)
#
# Java kurulum bölümü renkit'in kendi Dockerfile'ından uyarlanmıştır.
#
# Yerelde test etmek için:
#   docker build -t renpy-android-builder --build-arg RENPY_VERSION=8.5.3 .
#   docker run -p 7860:7860 renpy-android-builder
# ---------------------------------------------------------------------------

FROM ubuntu:22.04

# Imaja gömülecek (önceden indirilecek) Ren'Py sürümü.
ARG RENPY_VERSION=8.5.3
ARG RENKIT_VERSION=latest

ENV DEBIAN_FRONTEND=noninteractive \
    JAVA_HOME=/opt/java/openjdk \
    RENPY_VERSION=${RENPY_VERSION} \
    PYTHONUNBUFFERED=1 \
    # Gradle önbelleğini sabit ve öngörülebilir bir yere alıyoruz: imaj
    # derlenirken buraya indirilen Gradle dağıtımı, çalışma anında aynı
    # yoldan bulunur ve yeniden indirilmez.
    GRADLE_USER_HOME=/opt/gradle-home

# --- Java 21 (Temurin) -------------------------------------------------
# renkit'in belgelerine göre Ren'Py >= 8.2.0 için Java 21 gerekir; daha eski
# sürümler için Java 8 yeterlidir. Eski bir Ren'Py sürümüyle çalışacaksanız
# aşağıdaki etiketi eclipse-temurin:8-jdk ile değiştirin.
COPY --from=eclipse-temurin:21-jdk $JAVA_HOME $JAVA_HOME
ENV PATH="${JAVA_HOME}/bin:/root/.cargo/bin:${PATH}"

# --- Sistem bağımlılıkları ----------------------------------------------
RUN apt-get update && \
    apt-get install -y --no-install-recommends \
        curl wget xz-utils libgl1 ca-certificates locales \
        python3 python3-pip && \
    rm -rf /var/lib/apt/lists/* /tmp/* /var/tmp/*

# --- Yerel ayar (locale) --------------------------------------------------
# Bazı Ren'Py projeleri açılışta sistem dilini algılamaya çalışır. Minimal
# bir imajda hiçbir locale tanımlı olmadığı için bu çağrılar None döner ve
# "'NoneType' object has no attribute 'lower'" gibi hatalara yol açabilir.
RUN locale-gen en_US.UTF-8
ENV LANG=en_US.UTF-8 \
    LANGUAGE=en_US:en \
    LC_ALL=en_US.UTF-8

# --- renkit (renutil + renconstruct) kurulumu ---------------------------
RUN curl --proto '=https' --tlsv1.2 -LsSf \
      "https://github.com/kobaltcore/renkit/releases/$(if [ "$RENKIT_VERSION" = "latest" ]; then echo "latest/download"; else echo "download/v$RENKIT_VERSION"; fi)/renkit-installer.sh" \
      | sh

# --- Ren'Py SDK'sını imaja gömüyoruz -------------------------------------
# Bu adım Space build sırasında (daha bol kaynaklı ortamda) çalışır, böylece
# kullanıcı ilk isteği attığında SDK'yı yeniden indirmek zorunda kalmayız.
RUN renutil install "$RENPY_VERSION"

# --- Gradio uygulaması ----------------------------------------------------
WORKDIR /app
COPY requirements.txt .
RUN pip3 install --no-cache-dir -r requirements.txt

# --- AeroKey lisans ekranını Ren'Py'nin Android şablonuna enjekte et ------
# ÖNEMLİ: Ren'Py, her derlemede `rapt/app/AndroidManifest.xml` ve
# `app/build.gradle` gibi dosyaları Jinja2 şablonlarından YENİDEN üretir.
# Bu yüzden yamayı üretilen dosyalara değil, ŞABLONLARIN kendisine
# uyguluyoruz — böylece bu SDK ile derlenen her oyunda otomatik geçerli olur.
#
# Betik, şablon beklenmedik biçimde değiştiyse bilinçli olarak HATA VERİR:
# sessizce atlanan bir yama, lisans ekranı olmayan bir APK üretirdi ve bu
# ancak oyun açıldığında fark edilirdi.
COPY aerokey/ /app/aerokey/
RUN python3 /app/aerokey/patch_rapt.py --all --skip-gradle-warm

# --- Gradle dağıtımını önden indir ---------------------------------------
# Bu adım olmadan, ilk gerçek derlemede Gradle wrapper dağıtımı internetten
# çekmeye çalışır ve sunucu 504 dönerse tüm derleme çöker. Burada bir kez
# indirip önbelleğe aldığımız için çalışma anında indirilecek bir şey kalmaz.
# Ağ tamamen ulaşılamazsa imaj derlemesi yine de sürer (uyarı basılır);
# yalnızca ilk derleme yavaş olur.
RUN python3 /app/aerokey/patch_rapt.py --all --warm-gradle || true

COPY app.py .
COPY web/ /app/web/

# --- (İsteğe bağlı) Space'e gömülü uygulama ikonu -------------------------
# Bu repoya icon.png veya icon.jpg eklerseniz her derlemede otomatik olarak
# Ren'Py'nin beklediği iki katmanlı adaptif ikona dönüştürülüp kullanılır.
# icon.placeholder her zaman var olduğu için "icon.*" deseni asla boş
# kalmaz (Docker, hiçbir dosyayla eşleşmeyen COPY desenlerinde build'i
# BAŞARISIZ yapar).
COPY icon.* /app/icon_source/

# --- (İsteğe bağlı) AeroKey giriş ekranı afişi ----------------------------
# Bu repoya banner.gif (ya da .png/.jpg/.webp) eklerseniz, AeroKey giriş
# ekranının üstünde afiş olarak gösterilir. Tasarım boyutu 500x288'dir;
# oran korunarak karta sığdırılır. GIF ise Android 9+ üzerinde hareketli
# oynatılır. icon.* ile aynı gerekçeyle burada da bir placeholder var:
# hiçbir dosyayla eşleşmeyen bir COPY deseni Docker build'ini BAŞARISIZ yapar.
COPY banner.* /app/banner_source/

# Kalıcı veri (imza anahtarı + oyun kimliği kaydı) için varsayılan konum.
# Hugging Face'te kalıcı disk açıksa /data kullanılır; değilse uygulama
# otomatik olarak yazılabilir bir yedeğe düşer ve arayüzde uyarır.
ENV PORTER_DATA_DIR=/data

EXPOSE 7860
CMD ["python3", "app.py"]
