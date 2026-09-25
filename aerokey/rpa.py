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

# Boş ama GEÇERLİ bir RPA-3.0 arşivi. Açılan arşivin yerine bunu
# bırakıyoruz; sebebi `_write_placeholder` içinde anlatılıyor.
_PLACEHOLDER_KEY = 0xDEADBEEF
_PLACEHOLDER_HEADER_LEN = 34

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
    # Arşiv, yerine boş bir arşiv yazılarak boşaltıldı mı.
    emptied: bool = False
    # TEŞHİS İÇİN: arşiv dizinindeki HAM girdi adlarından bir örnek
    # (çözülmeden önce, ilk birkaçı). Arşivi paketleyen araç yolları
    # "game/" önekiyle mi yoksa öneksiz mi sakladığını buradan görürüz —
    # bu, "dosyalar açıldı" deyip aslında yanlış yere yazma sınıfı
    # hataları gözle görülür kılar.
    sample_entries: list[str] = field(default_factory=list)
    # Gerçekten YAZILAN dosyaların uzantı dağılımı ({".rpyc": 12, ...}).
    extensions: dict[str, int] = field(default_factory=dict)
    # "Zaten mevcut olduğu için atlandı" durumundaki dosyaların ADLARI
    # (yalnızca sayı değil). Kaç tanesinin gerçekten önemli bir dosya
    # (örn. script.rpy) olduğunu görmek için.
    overwritten_names: list[str] = field(default_factory=list)


_SAMPLE_ENTRY_LIMIT = 8
_OVERWRITTEN_NAME_LIMIT = 30


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

            if len(result.sample_entries) < _SAMPLE_ENTRY_LIMIT:
                result.sample_entries.append(name)

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
                if len(result.overwritten_names) < _OVERWRITTEN_NAME_LIMIT:
                    result.overwritten_names.append(str(relative))
                continue

            handle.seek(data_offset)
            payload = prefix + handle.read(remaining)

            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(payload)
            result.files += 1

            uzanti = target.suffix.lower() or "(uzantısız)"
            result.extensions[uzanti] = result.extensions.get(uzanti, 0) + 1

    return result


def empty_archive() -> bytes:
    """
    Boş ama tamamen geçerli bir RPA-3.0 arşivi üretir (48 bayt).

    Biçim `_parse_header`/`_load_index` ile birebir uyumlu: başlık satırı,
    ardından boş bir sözlüğün zlib ile sıkıştırılmış pickle'ı.
    """
    index = zlib.compress(pickle.dumps({}, 2))
    header = f"RPA-3.0 {_PLACEHOLDER_HEADER_LEN:016x} {_PLACEHOLDER_KEY:08x}\n".encode()
    assert len(header) == _PLACEHOLDER_HEADER_LEN, len(header)
    return header + index


def _write_placeholder(archive: Path) -> bool:
    """
    Açılan arşivin yerine boş bir arşiv bırakır.

    NEDEN SİLMİYORUZ
    ----------------
    Eskiden arşivi siliyorduk (aynı veri APK'ya iki kez girmesin diye).
    Ama bazı oyunlar AÇILIŞTA kendi arşiv dosyalarının VARLIĞINI
    denetliyor; dosya yoksa `renpy.error(...)` ile duruyorlar. Gerçek bir
    örnek, kullanıcının derleme günlüğünden:

        Exception: DDEK arşiv dosyaları /game klasöründe bulunamadı.

    Bu, oyunun kendi kodunun bilinçli bir kontrolü. Arşivi silmemiz onu
    tetikliyordu — yani hatayı BİZ üretiyorduk.

    Bu hata masum değil: init kodunda oluşan HER istisna, Ren'Py'nin
    `navigation.json` dosyasına `error: true` yazdırıyor
    (`renpy/display/error.py` -> `error_dump()` -> `renpy.dump.dump(True)`)
    ve Launcher bunu görünce derlemeyi tümden reddediyor
    (`launcher/game/distribute.rpy`: "Could not get build data from the
    project"). Yani tek bir varlık kontrolü bütün derlemeyi düşürüyordu.

    Çözüm: dosyayı silmek yerine İÇİ BOŞ ama geçerli bir arşivle
    değiştiriyoruz.
      - Varlık kontrolleri geçiyor (dosya duruyor).
      - Ren'Py arşivi sorunsuz açıyor, içinde hiçbir şey bulamıyor;
        içerik zaten gevşek dosyalar olarak duruyor (gerçek Ren'Py ile
        doğrulandı).
      - APK'ya giren fazladan veri: arşiv başına 48 bayt.
    """
    try:
        archive.write_bytes(empty_archive())
    except OSError:
        return False
    return True


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
    `game/` altındaki tüm arşivleri açar ve yerlerine boş arşiv bırakır.

    `remove=True` (varsayılan) artık "dosyayı sil" değil, "içeriğini
    boşalt" anlamına geliyor — gerekçesi `_write_placeholder` içinde.
    Böylece hem aynı veri APK'ya iki kez girmiyor, hem de arşivinin
    varlığını denetleyen oyunlar çalışmaya devam ediyor.

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
            result.emptied = _write_placeholder(archive)

    return results
