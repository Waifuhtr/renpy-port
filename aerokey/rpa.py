"""
Ren'Py RPA arşivlerini açar.

`Build Distributions` çıktısı, oyun dosyalarını genelde tek bir
`game/archive.rpa` içinde toplar. Ren'Py bu arşivi çalışma anında kendisi
okuyabildiği için paketleme AÇISINDAN açmak şart değildir; ama bizim
hattımızda iki yerde şart oluyor:

1. Çeviri kurulumu, kanca etiketinin (`splashscreen` /
   `before_main_menu`) oyunda TANIMLI OLMADIĞINI doğrulamak için `.rpyc`
   dosyalarını tarıyor. Dosyalar arşivin içindeyse tarayıcı hiçbir şey
   göremez, etiketi "boş" sanır ve aynı etiket ikinci kez tanımlanır —
   Ren'Py'de bu, oyunun HİÇ AÇILMAMASI demektir.

2. Çeviri `tl/` klasörü, arşivdeki mevcut `tl/` içeriğiyle aynı yerde
   olmalı; ikisi ayrı katmanlarda kalırsa davranış sürüme göre değişir.

Bu yüzden arşivi açıp dosyaları `game/` altına gerçek klasör yapısıyla
yerleştiriyor, sonra arşivi siliyoruz (aksi halde aynı içerik APK'ya iki
kez girerdi).

Biçim notları
-------------
RPA-3.0 : `RPA-3.0 <offset:016x> <key:08x>\\n` + `offset` konumunda zlib
          ile sıkıştırılmış bir pickle dizini. Dizindeki her offset ve
          uzunluk `key` ile XOR'lanmıştır.
RPA-3.2 : Aynı, ama başlıkta birden çok anahtar parçası olabilir; hepsi
          XOR'lanarak tek anahtar elde edilir.
RPA-2.0 : Anahtar yok (0 kabul edilir).
RPA-1.0 : Dizin ayrı bir `.rpi` dosyasındadır; desteklenmiyor (Ren'Py 6.x
          öncesi, pratikte karşımıza çıkmıyor).

Dizin girdileri `{dosya_adi: [(offset, uzunluk, on_ek), ...]}` biçiminde.
Dosyanın TAM içeriği `on_ek + arşivden okunan (uzunluk - len(on_ek))
bayt`tır; ön ek arşiv içinde ayrıca saklanmaz.
"""

from __future__ import annotations

import io
import pickle
import zlib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

# Ren'Py arşivlerinde dizin her zaman şu tiplerden oluşur: dict, list,
# tuple, str, bytes, int. Özel bir sınıf ASLA bulunmaz.
_SAFE_HEADER_LIMIT = 64


class RpaError(Exception):
    """Arşiv okunamadığında yükseltilir."""


# Dizin pickle'ında meşru olarak geçebilen TEK şeyler: temel veri
# kurucuları. Protokol 2 ile yazılmış bir pickle, `bytes` nesnelerini
# `_codecs.encode` ya da `__builtin__.bytes` üzerinden yeniden kurar —
# yani gerçek Ren'Py arşivlerinde bunlar normaldir. Hepsi veri kurucusu,
# hiçbiri kod çalıştırmaz.
_ALLOWED_GLOBALS = {
    ("__builtin__", "bytes"),
    ("__builtin__", "bytearray"),
    ("__builtin__", "set"),
    ("builtins", "bytes"),
    ("builtins", "bytearray"),
    ("builtins", "set"),
    ("_codecs", "encode"),
}


class _RestrictedUnpickler(pickle.Unpickler):
    """
    Yalnızca temel veri kuruculara izin veren Unpickler.

    Pickle çözmek, tasarımı gereği rastgele kod çalıştırabilir ve arşiv
    kullanıcıdan gelen güvenilmez veridir. Bu yüzden `find_class` bir
    izin listesine bakar: listedekiler yalnızca bayt/küme kurar, geri
    kalan her şey reddedilir.

    Listeyi boş bırakmak cazipti ama YANLIŞ olurdu: protokol 2 ile
    yazılmış meşru arşivler `bytes` kurucusuna başvuruyor ve koşulsuz
    reddetmek gerçek oyunları açılamaz hale getiriyordu.
    """

    def find_class(self, module: str, name: str):  # noqa: D102
        if (module, name) in _ALLOWED_GLOBALS:
            return super().find_class(module, name)
        raise RpaError(
            f"Arşiv dizini beklenmeyen bir nesne içeriyor ({module}.{name}). "
            "Güvenlik gereği açılmadı."
        )


def _load_index(blob: bytes) -> dict:
    """Sıkıştırılmış dizini çözer; eski Python 2 pickle'larını da dener."""
    try:
        raw = zlib.decompress(blob)
    except zlib.error as exc:
        raise RpaError(f"Arşiv dizini açılamadı (zlib): {exc}") from exc

    # Ren'Py 7+ Python 3 pickle yazar. Daha eski arşivler Python 2 ile
    # yazılmış olabilir; o durumda metinleri latin-1 ile çözmek gerekir.
    for encoding in (None, "latin-1", "bytes"):
        try:
            handle = _RestrictedUnpickler(io.BytesIO(raw))
            if encoding is not None:
                handle = _RestrictedUnpickler(io.BytesIO(raw), encoding=encoding)
            index = handle.load()
        except RpaError:
            raise
        except Exception:  # noqa: BLE001 - bir sonraki kodlamayı deneyeceğiz
            continue
        if isinstance(index, dict):
            return index

    raise RpaError("Arşiv dizini çözülemedi (tanınmayan pickle biçimi).")


def _parse_header(line: bytes) -> tuple[int, int]:
    """Başlık satırından (dizin_konumu, anahtar) çıkarır."""
    try:
        text = line.decode("utf-8", "replace").strip()
    except Exception as exc:  # noqa: BLE001
        raise RpaError(f"Arşiv başlığı okunamadı: {exc}") from exc

    parts = text.split()
    if not parts:
        raise RpaError("Arşiv başlığı boş.")

    version = parts[0]
    if version == "RPA-1.0" or version.startswith("RPI-"):
        raise RpaError(
            "RPA-1.0 arşivleri desteklenmiyor (dizin ayrı bir .rpi dosyasında)."
        )
    if not version.startswith("RPA-"):
        raise RpaError(f"Bu bir RPA arşivi değil (başlık: {text[:32]!r}).")

    if len(parts) < 2:
        raise RpaError("Arşiv başlığında dizin konumu yok.")

    try:
        offset = int(parts[1], 16)
    except ValueError as exc:
        raise RpaError(f"Arşiv başlığındaki dizin konumu geçersiz: {parts[1]!r}") from exc

    # RPA-2.0'da anahtar yoktur. 3.x'te başlıkta bir veya birden çok
    # anahtar parçası bulunur; Ren'Py hepsini XOR'layarak birleştirir.
    key = 0
    if version.startswith("RPA-3"):
        if len(parts) < 3:
            raise RpaError("RPA-3 başlığında anahtar yok.")
        for piece in parts[2:]:
            try:
                key ^= int(piece, 16)
            except ValueError as exc:
                raise RpaError(f"Arşiv anahtarı geçersiz: {piece!r}") from exc

    return offset, key


def _as_bytes(value) -> bytes:
    """Ön eki bayta çevirir (eski arşivlerde `str` olabiliyor)."""
    if value is None:
        return b""
    if isinstance(value, bytes):
        return value
    if isinstance(value, str):
        return value.encode("latin-1", "replace")
    return b""


def _safe_relative(name: str) -> Optional[Path]:
    """
    Arşiv içindeki adı, hedefin DIŞINA çıkamayacak göreli bir yola çevirir.

    Arşiv güvenilmez veri: `../../bir_yer` gibi bir ad, açma sırasında
    hedef klasörün dışına yazmaya çalışırdı. Böyle girdileri atıyoruz.
    """
    cleaned = name.replace("\\", "/").strip()
    if not cleaned:
        return None

    parts: list[str] = []
    for piece in cleaned.split("/"):
        if piece in ("", "."):
            continue
        if piece == "..":
            return None
        parts.append(piece)

    if not parts:
        return None

    candidate = Path(*parts)
    if candidate.is_absolute():
        return None
    return candidate


@dataclass
class ExtractResult:
    """Tek bir arşivin açılma sonucu."""

    archive: Path
    files: int = 0
    skipped: list[str] = field(default_factory=list)
    overwritten: int = 0


def extract(archive: Path, dest: Path) -> ExtractResult:
    """
    Bir RPA arşivini `dest` altına açar.

    Zaten var olan dosyaların ÜZERİNE YAZILMAZ: oyunun kök klasöründe
    duran gevşek bir dosya, arşivdeki eski bir kopyadan daha günceldir
    (Ren'Py de çalışma anında gevşek dosyayı tercih eder). Bu davranışı
    korumak, arşivi açmanın oyunun davranışını değiştirmemesini sağlar.
    """
    result = ExtractResult(archive=archive)

    with archive.open("rb") as handle:
        header = handle.readline(_SAFE_HEADER_LIMIT * 4)
        offset, key = _parse_header(header)

        handle.seek(offset)
        index = _load_index(handle.read())

        for raw_name, entries in index.items():
            name = raw_name.decode("utf-8", "replace") if isinstance(raw_name, bytes) else str(raw_name)

            relative = _safe_relative(name)
            if relative is None:
                result.skipped.append(name)
                continue

            if not isinstance(entries, (list, tuple)) or not entries:
                result.skipped.append(name)
                continue

            entry = entries[0]
            if not isinstance(entry, (list, tuple)) or len(entry) < 2:
                result.skipped.append(name)
                continue

            try:
                data_offset = int(entry[0]) ^ key
                data_length = int(entry[1]) ^ key
            except (TypeError, ValueError):
                result.skipped.append(name)
                continue

            prefix = _as_bytes(entry[2]) if len(entry) > 2 else b""

            remaining = data_length - len(prefix)
            if data_offset < 0 or remaining < 0:
                result.skipped.append(name)
                continue

            target = dest / relative
            if target.exists():
                # Gevşek dosya kazanır; arşivdeki kopyayı atlıyoruz.
                result.overwritten += 1
                continue

            handle.seek(data_offset)
            payload = prefix + handle.read(remaining)

            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(payload)
            result.files += 1

    return result


def find_archives(game_dir: Path) -> list[Path]:
    """
    `game/` altındaki tüm `.rpa` arşivlerini bulur.

    Yalnızca `archive.rpa` değil: bazı oyunlar içeriği `images.rpa`,
    `scripts.rpa` gibi birden çok arşive böler. Sıralama, aynı dosya
    birden çok arşivde varsa sonucun derlemeden derlemeye değişmemesi
    için alfabetiktir.
    """
    if not game_dir.is_dir():
        return []
    return sorted(
        (p for p in game_dir.rglob("*.rpa") if p.is_file()),
        key=lambda p: str(p).lower(),
    )


def extract_all(game_dir: Path, remove: bool = True) -> list[ExtractResult]:
    """
    `game/` altındaki tüm arşivleri açar ve (istenirse) arşivleri siler.

    Arşivi silmek bilinçli: içerik artık gevşek dosyalar olarak durduğu
    için arşivi bırakmak aynı veriyi APK'ya İKİNCİ KEZ koyardı.

    Bir arşiv okunamazsa o arşiv OLDUĞU GİBİ bırakılır ve hata yukarı
    taşınır; yarım açılmış bir oyunla derlemeye devam etmek, sessizce
    bozuk bir APK üretmek olurdu.
    """
    results: list[ExtractResult] = []

    for archive in find_archives(game_dir):
        # Arşiv `game/` altında herhangi bir derinlikte olabilir; içeriği
        # her zaman `game/` köküne göre yazılır, çünkü Ren'Py arşivdeki
        # yolları oyun kökünden itibaren saklar.
        result = extract(archive, game_dir)
        results.append(result)

        if remove:
            try:
                archive.unlink()
            except OSError:
                pass

    return results
