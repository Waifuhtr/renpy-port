"""
Ekransız (headless) konteyner için sanal X ekranı.

NEDEN GEREKLİ
-------------
`renconstruct` APK üretmeden önce Ren'Py Launcher'ı çağırır; Launcher da
projenin `build` meta verisini (Google Play anahtarı vb.) toplamak için
oyunu bir kez GERÇEKTEN açıp hemen kapatır:

    renpy.py <proje> quit --json-dump navigation.json

Bu adım Launcher'ın kendi kaynak kodunda koşulsuz çağrılır, atlanamaz.

Konteynerde X sunucusu yoksa SDL "dummy" video sürücüsüne düşer. Bu
sürücünün OpenGL desteği hiç yoktur; Ren'Py sırasıyla gl2 ve gles2
denemelerinde temiz şekilde başarısız olur, ardından yazılım (sw)
render'ına düşer ve orada — GPU da Mesa yazılım render'ı da olmadığı için
— segfault ile çöker (`Launch failed (returned -11)`).

Ardından Launcher kendi hata penceresini çizmeye çalışır; ama komut satırı
kipinde çalıştığı için ekran katmanları hiç kurulmamıştır ve bu kez
`KeyError: 'bottom'` ile o da çöker. Yani günlükte görünen hata, asıl
sebebin (ekran sunucusu yokluğu) üstünü örten İKİNCİL bir çökmedir.

Çözüm, ekransız CI ortamlarında standart olan yöntem: bellekte çalışan bir
sanal ekran (Xvfb) açıp `DISPLAY` değişkenini ona yöneltmek. Xvfb hiçbir
şeyi fiziksel olarak çizmez, yalnızca bir kare tamponu tutar; maliyeti
ihmal edilebilir ve konteyner ayakta kaldığı sürece bir kez başlatılır.
"""

from __future__ import annotations

import atexit
import os
import shutil
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

# Ren'Py'nin açılışta istediği pencere boyutundan büyük olsun; oyunun
# gerçek çözünürlüğü önemli değil, çünkü tek bir kare bile çizilmeden
# kapanıyor.
SCREEN_GEOMETRY = "1280x1024x24"

# Xvfb'nin soket açması genelde birkaç yüz milisaniye sürer. Yavaş bir
# konteynerde daha uzun sürebileceği için cömert bir üst sınır veriyoruz;
# normalde bu süreye hiç yaklaşılmaz çünkü soket görülür görülmez devam
# ediyoruz.
STARTUP_TIMEOUT_SECONDS = 15.0

_X11_SOCKET_DIR = Path("/tmp/.X11-unix")

# Başlattığımız süreci tutuyoruz: referans bırakmazsak çöp toplayıcı
# nesneyi toplayabilir ve süreç sahipsiz kalır.
_process: Optional[subprocess.Popen] = None


@dataclass
class DisplayInfo:
    """Sanal ekran kurulumunun sonucu."""

    active: bool
    display: str = ""
    note: str = ""
    started: bool = False


def _socket_path(number: int) -> Path:
    return _X11_SOCKET_DIR / f"X{number}"


def _lock_path(number: int) -> Path:
    return Path(f"/tmp/.X{number}-lock")


def _display_is_live(display: str) -> bool:
    """
    `DISPLAY` değerinin arkasında gerçekten bir sunucu var mı?

    Yalnızca değişkenin dolu olmasına bakmak yetmez: bayat bir DISPLAY
    değeri (ör. önceki bir konteynerden kalan) SDL'i var olmayan bir
    sunucuya bağlanmaya çalıştırır ve hata yine ekransız durumdakine
    benzer, teşhisi zor bir biçimde döner.
    """
    if not display.startswith(":"):
        # Uzak/TCP ekranlar (host:0 gibi) için soket kontrolü yapamayız;
        # kullanıcı bilerek ayarlamıştır, olduğu gibi kabul ediyoruz.
        return True
    try:
        number = int(display[1:].split(".", 1)[0])
    except ValueError:
        return False
    return _socket_path(number).exists()


def _find_free_display(start: int = 99, limit: int = 30) -> Optional[int]:
    """
    Kullanılmayan bir ekran numarası bulur.

    Hem soketi hem kilit dosyasını kontrol ediyoruz: Xvfb, kilit dosyası
    duruyorsa (süreç ölmüş olsa bile) o numarada başlamayı reddeder.
    """
    for number in range(start, start + limit):
        if _socket_path(number).exists() or _lock_path(number).exists():
            continue
        return number
    return None


def _apply_render_environment() -> None:
    """
    Yazılım render'ı ve sessiz ses sürücüsü.

    LIBGL_ALWAYS_SOFTWARE: konteynerde GPU yok; Mesa'yı doğrudan yazılım
    render'ına (llvmpipe) yönlendirmezsek donanım sürücüsü aramaya çalışıp
    başarısız olabiliyor.

    SDL_AUDIODRIVER=dummy: konteynerde ses aygıtı da yok. Ren'Py ses
    açamadığında genelde devam eder ama açılışta gereksiz gecikme ve
    gürültülü uyarılar üretir; bu adımda hiç ses çalınmayacağı için sessiz
    sürücü doğru seçim.

    SDL_VIDEODRIVER'a BİLEREK dokunmuyoruz: DISPLAY geçerli olduğunda SDL
    kendiliğinden x11'i seçer. Buraya "dummy" yazmak, düzeltmeye
    çalıştığımız hatanın ta kendisini geri getirirdi.
    """
    os.environ.setdefault("LIBGL_ALWAYS_SOFTWARE", "1")
    os.environ.setdefault("SDL_AUDIODRIVER", "dummy")


def _terminate(process: subprocess.Popen) -> None:
    """Xvfb'yi nazikçe kapatır; direnirse zorla sonlandırır."""
    if process.poll() is not None:
        return
    try:
        process.terminate()
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=5)
    except (OSError, subprocess.SubprocessError):
        pass


def ensure_virtual_display() -> DisplayInfo:
    """
    Sanal bir ekranın hazır olduğundan emin olur.

    Çağrılabilirliği idempotenttir: ekran zaten varsa yeniden başlatmaz.
    HİÇBİR durumda istisna fırlatmaz — Xvfb kurulu değilse bile uygulama
    açılmaya devam etmeli, yalnızca durum bilgisi döndürülür. (Derleme o
    durumda yine başarısız olur, ama sebebini açıkça yazabiliyoruz.)
    """
    global _process

    # 1) Zaten çalışan bir ekran var mı?
    existing = os.environ.get("DISPLAY", "").strip()
    if existing and _display_is_live(existing):
        _apply_render_environment()
        return DisplayInfo(
            active=True,
            display=existing,
            note="mevcut ekran kullanılıyor",
        )

    # Bayat bir DISPLAY değeri varsa temizliyoruz; onu olduğu gibi
    # bırakmak SDL'i var olmayan bir sunucuya bağlanmaya iter.
    if existing:
        os.environ.pop("DISPLAY", None)

    # 2) Daha önce biz başlattıysak ve hâlâ yaşıyorsa onu kullan.
    if _process is not None and _process.poll() is None:
        display = f":{_process.aerokey_display_number}"  # type: ignore[attr-defined]
        if _display_is_live(display):
            os.environ["DISPLAY"] = display
            _apply_render_environment()
            return DisplayInfo(active=True, display=display, note="zaten başlatılmıştı")

    # 3) Xvfb var mı?
    xvfb = shutil.which("Xvfb")
    if not xvfb:
        return DisplayInfo(
            active=False,
            note=(
                "Xvfb bulunamadı. Ren'Py, derleme öncesi projeyi bir kez "
                "grafiksel olarak açtığı için ekransız ortamda bu adım "
                "çöker. Docker imajına 'xvfb' paketini ekleyin."
            ),
        )

    number = _find_free_display()
    if number is None:
        return DisplayInfo(
            active=False,
            note="Boş bir X ekran numarası bulunamadı.",
        )

    display = f":{number}"

    try:
        process = subprocess.Popen(
            [
                xvfb, display,
                "-screen", "0", SCREEN_GEOMETRY,
                # Ağdan erişime hiç ihtiyacımız yok; kapatmak hem güvenli
                # hem de başlatmayı hızlandırıyor.
                "-nolisten", "tcp",
            ],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
            text=True,
        )
    except OSError as exc:
        return DisplayInfo(active=False, note=f"Xvfb başlatılamadı: {exc}")

    # 4) Soketin açılmasını bekle. Süreç bu arada ölürse hemen anlıyoruz;
    #    sabit bir uyku koymak hem yavaş hem de güvenilmez olurdu.
    deadline = time.monotonic() + STARTUP_TIMEOUT_SECONDS
    while time.monotonic() < deadline:
        if process.poll() is not None:
            stderr = ""
            try:
                stderr = (process.stderr.read() or "").strip() if process.stderr else ""
            except (OSError, ValueError):
                pass
            return DisplayInfo(
                active=False,
                note=f"Xvfb beklenmedik şekilde kapandı. {stderr[-300:]}".strip(),
            )
        if _socket_path(number).exists():
            break
        time.sleep(0.05)
    else:
        _terminate(process)
        return DisplayInfo(
            active=False,
            note=(
                f"Xvfb {STARTUP_TIMEOUT_SECONDS:.0f} saniyede hazır olmadı "
                f"({display})."
            ),
        )

    process.aerokey_display_number = number  # type: ignore[attr-defined]
    _process = process
    atexit.register(_terminate, process)

    os.environ["DISPLAY"] = display
    _apply_render_environment()

    return DisplayInfo(
        active=True,
        display=display,
        note=f"Xvfb başlatıldı ({SCREEN_GEOMETRY})",
        started=True,
    )


def shutdown() -> None:
    """Başlattığımız sanal ekranı kapatır (testler ve düzenli kapanış için)."""
    global _process
    if _process is not None:
        _terminate(_process)
        _process = None
