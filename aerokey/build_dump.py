"""
Ren'Py'nin `navigation.json` (build dump) dosyasını biz üretiriz.

NEDEN
-----
Ren'Py Launcher, APK üretmeden önce projenin `build` meta verisini toplamak
için oyunu bir alt süreçte GERÇEKTEN açar:

    renpy.py <proje> quit --json-dump navigation.json

Bu adım, oyunun kendi `init python` bloklarını çalıştırır
(`renpy/main.py` içinde `node.execute_init()` döngüsü). Bazı oyunlarda bu
döngü ekransız bir konteynerde segfault veriyor — Python tarafında hiçbir
iz bırakmadan, yalnızca `Launch failed (returned -11)` olarak.

Sebebi oyundan oyuna değişir (yerel kütüphane, yazı tipi, ses aygıtı…) ve
bizim tarafımızdan güvenilir biçimde düzeltilemez: oyunun KENDİ kodu
çalışıyor. Üstelik bir oyunun sorununu çözmek diğerini kurtarmaz.

Bu yüzden adımı düzeltmeye çalışmak yerine ONA OLAN BAĞIMLILIĞI
kaldırıyoruz: dump dosyasını kendimiz yazıyoruz. Launcher dosyayı hazır
bulunca alt süreci hiç başlatmıyor (bkz. patch_rapt.patch_launcher_dump).

NE GEREKİYOR
------------
Launcher'ın bu dosyadan okuduğu alanlar yalnızca şunlar
(`launcher/game/android.rpy`):

  build["google_play_key"]    - yoksa None kabul edilir (isteğe bağlı)
  build["google_play_salt"]   - yoksa None kabul edilir (isteğe bağlı)
  build["destination"]        - yalnızca GUI kipinde okunur, bizde okunmaz
  build["android_permissions"]- APK manifestine giren izinler
  build["version"]            - APK sürümü

Sürümü zaten biliyoruz (kullanıcı giriyor ya da kaynaktan okuyoruz).
İzinleri ise derlenmiş `.rpyc` dosyalarından kurtarmaya çalışıyoruz:
izin adları (`android.permission.XXX`) derlenmiş kodda düz metin olarak
durduğu için taranabiliyorlar.
"""

from __future__ import annotations

import json
import re
import zlib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable, Optional

# Android izin adları derlenmiş kodda bu biçimde düz metin olarak durur.
_PERMISSION_RE = re.compile(rb"android\.permission\.([A-Z][A-Z0-9_]{2,})")

# Ren'Py'nin manifest şablonu bunları zaten ekliyor; dump'a koymaya gerek
# yok ve koymak "oyun bunu özellikle istedi" izlenimi verirdi.
_IMPLICIT_PERMISSIONS = {"INTERNET", "ACCESS_NETWORK_STATE"}

_DUMP_NAME = "navigation.json"


@dataclass
class DumpResult:
    """Dump yazma sonucu."""

    path: Optional[Path] = None
    permissions: list[str] = field(default_factory=list)
    scanned_files: int = 0
    note: str = ""


def _rpyc_payloads(data: bytes) -> Iterable[bytes]:
    """
    RPYC2 dosyasındaki sıkıştırılmış blokları çözer.

    Biçim: `RENPY RPC2` başlığı + (slot, başlangıç, uzunluk) üçlüleri
    (slot == 0 sonlandırır) + zlib ile sıkıştırılmış yükler.
    """
    if not data.startswith(b"RENPY RPC2"):
        # Çok eski (RPYC1) biçim: dosyanın tamamı tek bir zlib bloğu.
        try:
            yield zlib.decompress(data)
        except zlib.error:
            pass
        return

    offset = len(b"RENPY RPC2")
    while offset + 12 <= len(data):
        slot = int.from_bytes(data[offset:offset + 4], "little")
        start = int.from_bytes(data[offset + 4:offset + 8], "little")
        length = int.from_bytes(data[offset + 8:offset + 12], "little")
        offset += 12

        if slot == 0:
            break
        if start < 0 or length < 0 or start + length > len(data):
            continue
        try:
            yield zlib.decompress(data[start:start + length])
        except zlib.error:
            continue


def scan_android_permissions(game_dir: Path) -> tuple[list[str], int]:
    """
    Oyunun betiklerinde geçen Android izinlerini toplar.

    Döner: (izin listesi, taranan dosya sayısı)

    Bu bir SEZGİSEL yöntemdir: izin adını gerçekten `build.android_permissions`
    listesine koymuş bir oyunla, izin adını yalnızca bir yorumda anmış bir
    oyunu ayırt edemez. Yanlış pozitifin bedeli APK'ya fazladan bir izin
    girmesi; yanlış negatifin bedeli ise iznin hiç eklenmemesi olurdu. Bu
    yüzden bilerek CÖMERT davranıyoruz.
    """
    permissions: set[str] = set()
    scanned = 0

    if not game_dir.is_dir():
        return [], 0

    for path in game_dir.rglob("*"):
        if not path.is_file():
            continue
        if path.suffix.lower() not in (".rpy", ".rpyc", ".rpym", ".rpymc"):
            continue

        try:
            data = path.read_bytes()
        except OSError:
            continue

        scanned += 1
        blobs = [data] if path.suffix.lower() in (".rpy", ".rpym") else list(_rpyc_payloads(data))
        for blob in blobs:
            for match in _PERMISSION_RE.finditer(blob):
                name = match.group(1).decode("ascii", "ignore")
                if name and name not in _IMPLICIT_PERMISSIONS:
                    permissions.add(name)

    return sorted(permissions), scanned


def dump_path_for(project_root: Path, sdk_root: Optional[Path]) -> Optional[Path]:
    """
    Launcher'ın dump dosyasını arayacağı yol.

    `launcher/game/project.rpy` içindeki `get_dump_filename` ile BİREBİR
    aynı mantık:

        game/saves/ varsa  -> game/saves/navigation.json
        yoksa              -> <sdk>/tmp/<proje klasör adı>/navigation.json

    İkinci dala SDK kökü gerekiyor; bilinmiyorsa None döneriz ve çağıran
    taraf dump yazmayı atlar (o zaman Launcher eskisi gibi alt süreci
    başlatır).
    """
    saves = project_root / "game" / "saves"
    if saves.is_dir():
        return saves / _DUMP_NAME

    if sdk_root is None:
        return None
    return sdk_root / "tmp" / project_root.name / _DUMP_NAME


def write_dump(
    project_root: Path,
    sdk_root: Optional[Path],
    version: str,
) -> DumpResult:
    """
    Sentetik `navigation.json` dosyasını yazar.

    Dosya zaten varsa ÜZERİNE YAZILIR: bayat bir dump, önceki derlemenin
    sürümünü taşıyabilir ve sessizce yanlış sürüm numaralı bir APK
    üretilmesine yol açardı.
    """
    target = dump_path_for(project_root, sdk_root)
    if target is None:
        return DumpResult(note="SDK kökü bilinmiyor, dump yazılmadı.")

    permissions, scanned = scan_android_permissions(project_root / "game")

    payload = {
        # Launcher yalnızca "build" anahtarını okuyor; "location" alanını
        # boş da olsa koyuyoruz ki dosya gerçek bir dump'a benzesin ve
        # Launcher'ın ileride ekleyebileceği okumalar patlamasın.
        "location": {},
        "build": {
            "version": version,
            "android_permissions": permissions,
            # GUI kipinde okunuyor, bizim akışımızda okunmuyor; yine de
            # Ren'Py'nin varsayılanını koyuyoruz.
            "destination": "-dists",
        },
    }

    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        # Atomik yazma: yarım kalmış bir JSON, Launcher tarafında
        # anlaşılmaz bir ayrıştırma hatası olurdu.
        tmp = target.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        tmp.replace(target)
    except OSError as exc:
        return DumpResult(note=f"Dump yazılamadı: {exc}")

    return DumpResult(
        path=target,
        permissions=permissions,
        scanned_files=scanned,
        note="yazıldı",
    )
