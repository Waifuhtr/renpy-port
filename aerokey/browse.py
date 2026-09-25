"""
Yüklenen proje ZIP'inin içini, AÇMADAN gezmeyi ve betik dosyalarını
okuyup düzenlemeyi sağlar.

Neden gerekli
-------------
Derlemeyi durduran satırı görmek için kullanıcının 784 MB'lık arşivi
indirip açması, doğru dosyayı bulması, düzeltip geri yüklemesi
gerekiyordu. Üstelik aradığı dosya çoğu zaman ZIP'in içindeki bir RPA
arşivinin de içinde oluyor — yani iki kat gömülü.

Burada ZIP bir kez taranıyor, içindeki RPA arşivlerinin dizinleri de
okunuyor ve düzenlenebilir olan dosyalar (betikler, JSON, düz metin)
küçük bir önbelleğe çıkarılıyor. Büyük ikili dosyalar (resim, ses)
yalnızca LİSTELENİYOR; onları çıkarmak hem gereksiz hem de diski
doldurur.

Yollar
------
Her girdi PROJE KÖKÜNE göre normalleştiriliyor (`game/script.rpyc`
gibi). Böylece kaydedilen bir düzenleme, derlemenin çalışma kopyasına
doğrudan aynı yola yazılabiliyor — arşivin içinden mi yoksa ZIP'ten mi
geldiğinin bir önemi kalmıyor.
"""

from __future__ import annotations

import json
import re
import zipfile
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Optional

from . import rpa as rpa_mod
from . import rpycfile

# Düzenlenebilir sayılan uzantılar. Diğerleri listelenir ama açılmaz.
METIN_UZANTILARI = (
    ".rpy", ".rpym", ".py", ".json", ".txt", ".csv", ".md", ".yaml", ".yml",
    ".cfg", ".ini", ".xml", ".html", ".css", ".js",
)
DERLENMIS_UZANTILAR = (".rpyc", ".rpymc")

MAKS_DOSYA = 4 * 1024 * 1024
MAKS_TOPLAM = 96 * 1024 * 1024
MAKS_GIRDI = 20000

_DIZIN_ADI = "browse"
_INDEKS = "index.json"
_DOSYALAR = "files"
_DUZENLEMELER = "edits"
_DUZENLEME_KAYDI = "edits.json"

_GUVENSIZ = re.compile(r"[^A-Za-z0-9._-]+")


class BrowseError(Exception):
    """Gezinme sırasında oluşan, kullanıcıya gösterilebilir hata."""


@dataclass
class Girdi:
    """Listedeki tek bir dosya."""

    path: str                 # proje köküne göre (ör. "game/script.rpyc")
    size: int
    kind: str                 # "text" | "rpyc" | "binary"
    source: str               # "zip" | "rpa:<arşiv adı>"
    openable: bool = False


@dataclass
class Dizin:
    """Bir yüklemenin taranmış hâli."""

    entries: list[Girdi] = field(default_factory=list)
    project_root: str = ""
    archives: list[str] = field(default_factory=list)
    truncated: bool = False
    note: str = ""


def _tur(ad: str) -> str:
    alt = ad.lower()
    if alt.endswith(DERLENMIS_UZANTILAR):
        return "rpyc"
    if alt.endswith(METIN_UZANTILARI):
        return "text"
    return "binary"


def _duz_ad(yol: str) -> str:
    """Yolu, dosya sisteminde güvenli tek bir ada çevirir."""
    return _GUVENSIZ.sub("_", yol.replace("/", "__"))[:180]


def _proje_koku(adlar: list[str]) -> str:
    """
    ZIP içindeki proje kökünü (game/ klasörünü içeren yolu) bulur.

    En KISA aday seçiliyor: bir mod paketinde `game/` adını taşıyan
    birden çok klasör olabiliyor ve derleme de en dıştakini kullanıyor.
    """
    adaylar: set[str] = set()
    for ad in adlar:
        parcalar = ad.split("/")
        for i, parca in enumerate(parcalar):
            if parca == "game" and i + 1 < len(parcalar):
                adaylar.add("/".join(parcalar[:i]))
                break
    if not adaylar:
        return ""
    return min(adaylar, key=lambda s: (s.count("/"), len(s)))


def _proje_gore(ad: str, kok: str) -> Optional[str]:
    if not kok:
        return ad
    onek = kok + "/"
    if ad.startswith(onek):
        return ad[len(onek):]
    return None


# ---------------------------------------------------------------------------
# Tarama
# ---------------------------------------------------------------------------


def kok_dizin(upload_dir: Path) -> Path:
    return upload_dir / _DIZIN_ADI


def hazir_mi(upload_dir: Path) -> bool:
    return (kok_dizin(upload_dir) / _INDEKS).is_file()


def tara(zip_path: Path, upload_dir: Path) -> Dizin:
    """
    ZIP'i (ve içindeki RPA arşivlerini) tarar, sonucu önbelleğe yazar.

    Pahalı olan tek adım bu; sonraki listeleme/okuma istekleri diskteki
    önbellekten karşılanıyor.
    """
    hedef = kok_dizin(upload_dir)
    dosyalar = hedef / _DOSYALAR
    dosyalar.mkdir(parents=True, exist_ok=True)

    dizin = Dizin()
    toplam = 0

    try:
        zf = zipfile.ZipFile(zip_path)
    except (OSError, zipfile.BadZipFile) as exc:
        raise BrowseError(f"ZIP açılamadı: {exc}") from exc

    with zf:
        adlar = [i.filename for i in zf.infolist() if not i.is_dir()]
        kok = _proje_koku(adlar)
        dizin.project_root = kok

        arsivler: list[str] = []
        for info in zf.infolist():
            if info.is_dir():
                continue
            goreli = _proje_gore(info.filename, kok)
            if goreli is None:
                continue
            if len(dizin.entries) >= MAKS_GIRDI:
                dizin.truncated = True
                break

            tur = _tur(goreli)
            girdi = Girdi(
                path=goreli, size=info.file_size, kind=tur, source="zip",
            )
            if goreli.lower().endswith(".rpa"):
                arsivler.append(info.filename)

            if tur in ("text", "rpyc") and info.file_size <= MAKS_DOSYA:
                if toplam + info.file_size <= MAKS_TOPLAM:
                    try:
                        (dosyalar / _duz_ad(goreli)).write_bytes(zf.read(info))
                        girdi.openable = True
                        toplam += info.file_size
                    except (OSError, zipfile.BadZipFile, RuntimeError):
                        girdi.openable = False
            dizin.entries.append(girdi)

        # RPA arşivlerinin içi
        for arsiv_adi in arsivler:
            goreli_arsiv = _proje_gore(arsiv_adi, kok) or arsiv_adi
            try:
                toplam += _arsivi_tara(
                    zf, arsiv_adi, goreli_arsiv, dizin, dosyalar, toplam
                )
            except Exception as exc:  # noqa: BLE001
                dizin.note += (
                    f"\n{goreli_arsiv} okunamadı: {exc}"
                )

    _indeksi_yaz(upload_dir, dizin)
    return dizin


def _arsiv_taban(goreli_arsiv: str) -> str:
    """
    RPA girdilerinin proje köküne göre önekini bulur.

    RPA dizin anahtarları OYUN klasörüne göredir (`renpy/loader.py` ->
    `index_archives`). Arşiv `game/scripts.rpa` ise içindeki
    `script.rpyc`, projede `game/script.rpyc` olur.
    """
    parent = goreli_arsiv.rsplit("/", 1)[0] if "/" in goreli_arsiv else ""
    return parent + "/" if parent else ""


def _arsivi_tara(
    zf: zipfile.ZipFile, arsiv_adi: str, goreli_arsiv: str,
    dizin: Dizin, dosyalar: Path, toplam: int,
) -> int:
    """Tek bir RPA arşivinin dizinini okur; betikleri çıkarır."""
    with zf.open(arsiv_adi) as akis:
        baslik = akis.readline(4096)
        offset, key = rpa_mod._parse_header(baslik)
        _ileri_sar(akis, offset - len(baslik))
        index = rpa_mod._load_index(akis.read())

    dizin.archives.append(goreli_arsiv)
    taban = _arsiv_taban(goreli_arsiv)

    # Hangi girdileri gerçekten çıkaracağız? Önce karar ver, sonra TEK
    # ileri geçişte hepsini oku: geriye sarmak, sıkıştırılmış akışı
    # baştan çözmek demek olurdu.
    istenen: list[tuple[int, int, bytes, str]] = []
    eklenen = 0
    for ham_ad, girdiler in index.items():
        ad = (
            ham_ad.decode("utf-8", "replace")
            if isinstance(ham_ad, bytes) else str(ham_ad)
        )
        if not girdiler:
            continue
        kayit = girdiler[0]
        try:
            basla, uzunluk = int(kayit[0]), int(kayit[1])
        except (TypeError, ValueError, IndexError):
            continue
        onek = rpa_mod._as_bytes(kayit[2]) if len(kayit) > 2 else b""
        if key:
            basla ^= key
            uzunluk ^= key

        proje_yolu = taban + ad.replace("\\", "/")
        tur = _tur(ad)
        girdi = Girdi(
            path=proje_yolu, size=uzunluk + len(onek), kind=tur,
            source=f"rpa:{goreli_arsiv}",
        )
        if (
            tur in ("text", "rpyc")
            and girdi.size <= MAKS_DOSYA
            and toplam + girdi.size <= MAKS_TOPLAM
        ):
            istenen.append((basla, uzunluk, onek, proje_yolu))
            girdi.openable = True
            toplam += girdi.size
        dizin.entries.append(girdi)
        if girdi.openable:
            eklenen += girdi.size
        if len(dizin.entries) >= MAKS_GIRDI:
            dizin.truncated = True
            break

    if istenen:
        istenen.sort(key=lambda t: t[0])
        with zf.open(arsiv_adi) as akis:
            konum = 0
            for basla, uzunluk, onek, proje_yolu in istenen:
                if basla < konum:
                    continue  # sırayı bozan girdi; geriye sarmıyoruz
                _ileri_sar(akis, basla - konum)
                govde = akis.read(uzunluk)
                konum = basla + len(govde)
                try:
                    (dosyalar / _duz_ad(proje_yolu)).write_bytes(onek + govde)
                except OSError:
                    pass
    return eklenen


def _ileri_sar(akis, miktar: int, parca: int = 1 << 20) -> None:
    """Sıkıştırılmış akışta ileri atlar (geriye sarmak desteklenmiyor)."""
    while miktar > 0:
        okunan = akis.read(min(parca, miktar))
        if not okunan:
            return
        miktar -= len(okunan)


# ---------------------------------------------------------------------------
# Önbellek okuma
# ---------------------------------------------------------------------------


def _indeksi_yaz(upload_dir: Path, dizin: Dizin) -> None:
    hedef = kok_dizin(upload_dir)
    hedef.mkdir(parents=True, exist_ok=True)
    (hedef / _INDEKS).write_text(
        json.dumps(
            {
                "project_root": dizin.project_root,
                "archives": dizin.archives,
                "truncated": dizin.truncated,
                "note": dizin.note,
                "entries": [asdict(e) for e in dizin.entries],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )


def indeks(upload_dir: Path) -> Optional[Dizin]:
    yol = kok_dizin(upload_dir) / _INDEKS
    if not yol.is_file():
        return None
    try:
        ham = json.loads(yol.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return Dizin(
        entries=[Girdi(**e) for e in ham.get("entries", [])],
        project_root=ham.get("project_root", ""),
        archives=ham.get("archives", []),
        truncated=bool(ham.get("truncated")),
        note=ham.get("note", ""),
    )


def _girdi_bul(upload_dir: Path, path: str) -> Optional[Girdi]:
    d = indeks(upload_dir)
    if d is None:
        return None
    for e in d.entries:
        if e.path == path:
            return e
    return None


def ham_icerik(upload_dir: Path, path: str) -> bytes:
    """Dosyanın (varsa düzenlenmiş) ham içeriğini döner."""
    duzenli = kok_dizin(upload_dir) / _DUZENLEMELER / _duz_ad(path)
    if duzenli.is_file():
        return duzenli.read_bytes()
    kaynak = kok_dizin(upload_dir) / _DOSYALAR / _duz_ad(path)
    if not kaynak.is_file():
        raise BrowseError("Bu dosya açılabilir değil (listede yok ya da çok büyük).")
    return kaynak.read_bytes()


def ac(upload_dir: Path, path: str) -> dict:
    """
    Dosyayı arayüzde gösterilecek biçimde açar.

    Düz metin dosyalarında içerik OLDUĞU GİBİ dönüyor. Derlenmiş
    betiklerde (`.rpyc`) dosyanın içindeki Python kod blokları, satır
    numaralarıyla birlikte dönüyor — derlemeyi durduran kod her zaman
    bu blokların içinde oluyor.
    """
    girdi = _girdi_bul(upload_dir, path)
    if girdi is None:
        raise BrowseError("Böyle bir dosya listede yok.")
    if not girdi.openable:
        raise BrowseError(
            "Bu dosya düzenlenemez (ikili dosya ya da 4 MB'tan büyük)."
        )

    ham = ham_icerik(upload_dir, path)
    duzenli = (kok_dizin(upload_dir) / _DUZENLEMELER / _duz_ad(path)).is_file()

    if girdi.kind == "rpyc":
        bloklar = rpycfile.bloklari_cikar(ham)
        return {
            "path": path,
            "kind": "rpyc",
            "edited": duzenli,
            "blocks": [
                {"line": b.line, "index": b.index, "mode": b.mode,
                 "title": b.baslik(), "source": b.source}
                for b in bloklar
            ],
        }

    return {
        "path": path,
        "kind": "text",
        "edited": duzenli,
        "text": ham.decode("utf-8", "replace"),
    }


# ---------------------------------------------------------------------------
# Düzenlemeler
# ---------------------------------------------------------------------------


def _kayit_yolu(upload_dir: Path) -> Path:
    return kok_dizin(upload_dir) / _DUZENLEME_KAYDI


def duzenlemeler(upload_dir: Path) -> list[dict]:
    yol = _kayit_yolu(upload_dir)
    if not yol.is_file():
        return []
    try:
        veri = json.loads(yol.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    return veri if isinstance(veri, list) else []


def _kaydi_yaz(upload_dir: Path, kayitlar: list[dict]) -> None:
    _kayit_yolu(upload_dir).write_text(
        json.dumps(kayitlar, ensure_ascii=False, indent=2), encoding="utf-8"
    )


def kaydet(
    upload_dir: Path, path: str, metin: str, blok_index: int = -1,
    blok_satir: int = -1,
) -> dict:
    """
    Düzenlemeyi kaydeder.

    Düz metin dosyasında metnin tamamı yazılıyor. Derlenmiş betikte ise
    SEÇİLEN kod bloğunun kaynağı değiştirilip yeni `.rpyc` içeriği
    üretiliyor — yani kaydettiğiniz an dosya gerçekten yamanıyor, derleme
    sırasında sürpriz olmuyor.

    Orijinal ZIP'e DOKUNULMUYOR: düzenleme ayrı duruyor ve derlemenin
    çalışma kopyasına uygulanıyor.
    """
    girdi = _girdi_bul(upload_dir, path)
    if girdi is None:
        raise BrowseError("Böyle bir dosya listede yok.")
    if not girdi.openable:
        raise BrowseError("Bu dosya düzenlenemez.")

    hedef_dizin = kok_dizin(upload_dir) / _DUZENLEMELER
    hedef_dizin.mkdir(parents=True, exist_ok=True)
    hedef = hedef_dizin / _duz_ad(path)

    if girdi.kind == "rpyc":
        ham = ham_icerik(upload_dir, path)
        bloklar = rpycfile.bloklari_cikar(ham)
        secili = None
        for b in bloklar:
            if b.line == blok_satir and b.index == blok_index:
                secili = b
                break
        if secili is None:
            raise BrowseError(
                "Düzenlenecek kod bloğu bulunamadı; dosyayı yeniden açıp "
                "tekrar deneyin."
            )
        if metin == secili.source:
            raise BrowseError("İçerik değişmemiş; kaydedilecek bir şey yok.")
        yeni = rpycfile.blogu_degistir(ham, secili, metin)
        hedef.write_bytes(yeni)
        ozet = f"satır {secili.line} kod bloğu"
    else:
        hedef.write_bytes(metin.encode("utf-8"))
        ozet = "dosyanın tamamı"

    kayitlar = [k for k in duzenlemeler(upload_dir) if k.get("path") != path]
    kayitlar.append(
        {
            "path": path,
            "kind": girdi.kind,
            "summary": ozet,
            "size": hedef.stat().st_size,
        }
    )
    _kaydi_yaz(upload_dir, kayitlar)
    return kayitlar[-1]


def sil(upload_dir: Path, path: str) -> bool:
    kayitlar = duzenlemeler(upload_dir)
    kalan = [k for k in kayitlar if k.get("path") != path]
    if len(kalan) == len(kayitlar):
        return False
    hedef = kok_dizin(upload_dir) / _DUZENLEMELER / _duz_ad(path)
    try:
        hedef.unlink(missing_ok=True)
    except OSError:
        pass
    _kaydi_yaz(upload_dir, kalan)
    return True


def uygula(upload_dir: Path, project_root: Path) -> list[str]:
    """
    Kaydedilmiş düzenlemeleri derlemenin çalışma kopyasına yazar.

    `.rpyc` yamalandıysa `game/cache` SİLİNİYOR. Sebebi ölçüldü: Ren'Py
    derlenmiş bytecode'u `game/cache/bytecode-*.rpyb` içinde kaynağın
    hash'ine göre saklıyor; önbellek dururken yamalı dosya sorunsuz
    yükleniyor ama ESKİ kod çalışıyordu.
    """
    yazilan: list[str] = []
    rpyc_var = False
    for kayit in duzenlemeler(upload_dir):
        yol = kayit.get("path", "")
        kaynak = kok_dizin(upload_dir) / _DUZENLEMELER / _duz_ad(yol)
        if not yol or not kaynak.is_file():
            continue
        hedef = project_root / yol
        if not _icerde(hedef, project_root):
            continue
        try:
            hedef.parent.mkdir(parents=True, exist_ok=True)
            hedef.write_bytes(kaynak.read_bytes())
        except OSError:
            continue
        yazilan.append(yol)
        if kayit.get("kind") == "rpyc":
            rpyc_var = True

    if rpyc_var:
        import shutil

        shutil.rmtree(project_root / "game" / "cache", ignore_errors=True)
    return yazilan


def _icerde(yol: Path, kok: Path) -> bool:
    try:
        yol.resolve().relative_to(kok.resolve())
    except ValueError:
        return False
    return True
