"""
Derlenmiş Ren'Py betiklerinin (.rpyc / .rpymc) içindeki Python kaynağını
okur ve değiştirir.

Neden gerekli
-------------
Derlenmiş bir dağıtımda (DDLC gibi) `.rpy` kaynak dosyası HİÇ yoktur;
yalnızca `.rpyc` vardır. Oyunun init kodu Python 3'te patladığında
kullanıcının düzeltecek bir metin dosyası olmuyor, bizim otomatik
onarımımız da okuyacak satır bulamıyor.

Oysa `.rpyc` dosyası, çalıştırılabilir Python bloklarının KAYNAK METNİNİ
içinde saklıyor. Ren'Py 8, Ren'Py 6 ile derlenmiş dosyaları zaten bu
sayede çalıştırabiliyor: saklı bytecode'u atıp kaynak metni Python 3 ile
yeniden derliyor (`renpy/ast.py` -> `PyCode.__setstate__`, `bytecode` her
zaman None'a çekiliyor). Yani hata da tam olarak bu metinden geliyor ve
düzeltmek için gereken her şey dosyanın içinde duruyor.

Biçim (renpy/script.py:747-820'den birebir)
-------------------------------------------
İki biçim var:

* Eski (Ren'Py 6.99'un yazdığı): dosyanın TAMAMI zlib ile sıkıştırılmış
  tek bir pickle.
* RPYC2: `RENPY RPC2` imzası + ÜÇ adet 12 baytlık slot kaydı (36 bayt) +
  veri + sonda 16 baytlık md5.

Ren'Py slotları `for slot in [2, 1]` sırasıyla deniyor, yani slot 2
VARSA slot 1 hiç okunmuyor. Bu yüzden değiştirilmiş dosyayı yalnızca
slot 1 ile yazıyoruz; Ren'Py slot 2'yi bulamayınca dönüşümleri kendisi
üretiyor (`if slot < 2:` dalı).

Ölçülerek doğrulandı
--------------------
Gerçek Ren'Py 8.5.3 ikilisiyle, gerçek bir derlenmiş projede:

    yama + slot2 duruyor        -> "Could not load file"  (slot 2 okunuyor)
    yama + FRAME düzeltilmemiş  -> "Could not load file"  (akış bozuluyor)
    yama + game/cache duruyor   -> dosya açılıyor ama ESKİ kod çalışıyor
    yama + cache silinmiş       -> YENİ kod çalışıyor  ✔

Dördü de bu modülde ele alınıyor.

Güvenlik
--------
Pickle çözmek tasarımı gereği rastgele kod çalıştırabilir. Burada
`find_class` yalnızca KENDİ ürettiğimiz atıl sınıfları döndürüyor; dışarıdan
tek bir modül bile import edilmiyor. Dolayısıyla dosya ne içerirse içersin
çalıştırılabilecek bir şey bulamıyor.
"""

from __future__ import annotations

import io
import pickle
import pickletools
import struct
import zlib
from dataclasses import dataclass
from typing import Any, Optional

RPYC2_HEADER = b"RENPY RPC2"
_SLOT_KAYIT = 12
_SLOT_SAYISI = 3
_BASLIK_BOYU = len(RPYC2_HEADER) + _SLOT_KAYIT * _SLOT_SAYISI

# Pickle'da metin taşıyan opcode'lar. Python 2 döneminden gelen dosyalar
# BINSTRING/SHORT_BINSTRING kullanıyor; Python 3 olanlar BINUNICODE.
_METIN_OPCODE = (
    "SHORT_BINUNICODE", "BINUNICODE", "UNICODE",
    "SHORT_BINSTRING", "BINSTRING", "STRING",
)
_SAYI_OPCODE = ("BININT", "BININT1", "BININT2", "INT", "LONG", "LONG1", "LONG4")

# Ren'Py'nin kendi okuyucusuyla aynı ayarlar (renpy/compat/pickle.py:297).
_PICKLE_AYARI = dict(fix_imports=True, encoding="utf-8", errors="surrogateescape")

MAKS_BOYUT = 32 * 1024 * 1024


class RpycError(Exception):
    """Dosya okunamadı ya da beklenen biçimde değil."""


@dataclass
class PyBlok:
    """`.rpyc` içindeki tek bir Python kod bloğu."""

    line: int
    source: str
    mode: str = "exec"
    # Aynı metne sahip birden çok blok olabiliyor; hangisi olduğunu
    # kaybetmemek için sırasını da tutuyoruz.
    index: int = 0

    def baslik(self) -> str:
        satir_sayisi = self.source.count("\n") + 1
        return f"satır {self.line} ({satir_sayisi} satır, {self.mode})"


# ---------------------------------------------------------------------------
# Biçim okuma / yazma
# ---------------------------------------------------------------------------


def is_rpyc(ad: str) -> bool:
    return ad.lower().endswith((".rpyc", ".rpymc"))


def slot_oku(ham: bytes, slot: int = 1) -> Optional[bytes]:
    """
    `.rpyc` dosyasından bir slotun açılmış verisini döner.

    Eski biçimde (başlıksız) yalnızca slot 1 vardır ve dosyanın tamamıdır.
    """
    if ham[: len(RPYC2_HEADER)] != RPYC2_HEADER:
        if slot != 1:
            return None
        try:
            return zlib.decompress(ham)
        except zlib.error as exc:
            raise RpycError(f"dosya açılamadı: {exc}") from exc

    pos = len(RPYC2_HEADER)
    for _ in range(_SLOT_SAYISI):
        if pos + _SLOT_KAYIT > len(ham):
            return None
        kayit_slot, start, length = struct.unpack("III", ham[pos: pos + _SLOT_KAYIT])
        if kayit_slot == slot:
            try:
                return zlib.decompress(ham[start: start + length])
            except zlib.error as exc:
                raise RpycError(f"slot {slot} açılamadı: {exc}") from exc
        if kayit_slot == 0:
            return None
        pos += _SLOT_KAYIT
    return None


def slot1_dosyasi(veri: bytes) -> bytes:
    """
    Yalnızca slot 1 içeren bir RPYC2 dosyası kurar.

    Slot 2 BİLEREK yazılmıyor: Ren'Py önce slot 2'ye bakıyor ve orada
    bizim değiştirmediğimiz eski ağaç dururken yamamız hiç okunmuyordu
    (ölçüldü). Slot 2 yoksa Ren'Py dönüşümleri kendisi üretiyor.
    """
    sikis = zlib.compress(veri, 3)
    ust = RPYC2_HEADER + struct.pack("III", 1, _BASLIK_BOYU, len(sikis))
    ust += struct.pack("III", 0, 0, 0) * (_SLOT_SAYISI - 1)
    return ust + sikis


# ---------------------------------------------------------------------------
# Kaynak çıkarma
# ---------------------------------------------------------------------------


class _Atil:
    """
    Pickle'ın kurmaya çalıştığı her sınıfın yerine geçen atıl nesne.

    Hiçbir şey yapmıyor, yalnızca kendisine verilen durumu saklıyor.
    Böylece pickle çözülürken çalıştırılabilecek gerçek bir kod yolu
    kalmıyor.
    """

    __slots__ = ("_ad", "_state", "_args", "_items")

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        self._ad = ""
        self._state: Any = None
        self._args = args
        self._items: list = []

    def __setstate__(self, state: Any) -> None:
        self._state = state

    def append(self, item: Any) -> None:
        self._items.append(item)

    def extend(self, items: Any) -> None:
        self._items.extend(items)

    def __setitem__(self, key: Any, value: Any) -> None:
        self._items.append((key, value))


def _atil_sinif(module: str, name: str) -> type:
    tam = f"{module}.{name}"

    def kur(*args: Any, **kwargs: Any) -> _Atil:
        nesne = _Atil(*args, **kwargs)
        nesne._ad = tam
        return nesne

    # pickle bazen sınıfın kendisini (NEWOBJ) istiyor; çağrılabilir bir
    # sınıf döndürmek her iki yolu da karşılıyor.
    return type(
        "Atil_" + name,
        (_Atil,),
        {"_pickle_ad": tam, "__new__": lambda cls, *a, **k: kur()},
    )


class _AtilUnpickler(pickle.Unpickler):
    """Dışarıdan HİÇBİR şey import etmeyen çözücü."""

    def find_class(self, module: str, name: str):  # noqa: D102
        return _atil_sinif(module, name)


def _pycode_durumlari(nesne: Any, bulunan: list, gorulen: set) -> None:
    """Nesne ağacında `renpy.ast.PyCode` durumlarını toplar."""
    kimlik = id(nesne)
    if kimlik in gorulen:
        return
    gorulen.add(kimlik)

    if isinstance(nesne, _Atil):
        if getattr(nesne, "_ad", "") == "renpy.ast.PyCode" or (
            getattr(type(nesne), "_pickle_ad", "") == "renpy.ast.PyCode"
        ):
            bulunan.append(nesne._state)
        for alt in (nesne._state, nesne._args, nesne._items):
            _pycode_durumlari(alt, bulunan, gorulen)
        return

    if isinstance(nesne, (list, tuple, set, frozenset)):
        for alt in nesne:
            _pycode_durumlari(alt, bulunan, gorulen)
        return

    if isinstance(nesne, dict):
        for anahtar, deger in nesne.items():
            _pycode_durumlari(anahtar, bulunan, gorulen)
            _pycode_durumlari(deger, bulunan, gorulen)
        return

    durum = getattr(nesne, "__dict__", None)
    if isinstance(durum, dict):
        _pycode_durumlari(durum, bulunan, gorulen)


def bloklari_cikar(ham: bytes) -> list[PyBlok]:
    """
    `.rpyc` içindeki Python kod bloklarını kaynak metniyle döner.

    `PyCode.__getstate__` (renpy/ast.py:89) şunu üretiyor:

        (1, source, (filename, linenumber), mode, py, hashcode, col_offset)

    Kısa sürümleri de var; hepsi karşılanıyor.
    """
    veri = slot_oku(ham, 1)
    if veri is None:
        raise RpycError("dosyada okunabilir bir bölüm bulunamadı.")

    try:
        kok = _AtilUnpickler(io.BytesIO(veri), **_PICKLE_AYARI).load()
    except Exception as exc:  # noqa: BLE001  (pickle her şeyi fırlatabilir)
        raise RpycError(f"içerik çözülemedi: {exc}") from exc

    durumlar: list = []
    _pycode_durumlari(kok, durumlar, set())

    bloklar: list[PyBlok] = []
    sayac: dict[str, int] = {}
    for durum in durumlar:
        if not isinstance(durum, tuple) or len(durum) < 3:
            continue
        kaynak = durum[1]
        konum = durum[2]
        if isinstance(kaynak, bytes):
            kaynak = kaynak.decode("utf-8", "surrogateescape")
        if not isinstance(kaynak, str):
            continue
        satir = 0
        if isinstance(konum, (tuple, list)) and len(konum) >= 2:
            try:
                satir = int(konum[1])
            except (TypeError, ValueError):
                satir = 0
        mod = durum[3] if len(durum) > 3 and isinstance(durum[3], str) else "exec"
        sira = sayac.get(kaynak, 0)
        sayac[kaynak] = sira + 1
        bloklar.append(PyBlok(line=satir, source=kaynak, mode=mod, index=sira))

    bloklar.sort(key=lambda b: (b.line, b.index))
    return bloklar


# ---------------------------------------------------------------------------
# Kaynak değiştirme
# ---------------------------------------------------------------------------


def _metin_opcode(metin: str) -> bytes:
    ham = metin.encode("utf-8", "surrogateescape")
    if len(ham) < 256:
        return b"\x8c" + bytes([len(ham)]) + ham        # SHORT_BINUNICODE
    return b"X" + struct.pack("<I", len(ham)) + ham     # BINUNICODE


def _hedef_opcode(ops: list, blok: PyBlok) -> Optional[int]:
    """
    Değiştirilecek metin opcode'unun sırasını bulur.

    Aynı kaynağa sahip birden çok blok olabiliyor. Ayırt etmek için,
    metnin hemen ardından gelen birkaç opcode içinde blok'un SATIR
    NUMARASINI arıyoruz: `PyCode` durumunda kaynak metnin ardından
    `(filename, linenumber)` geliyor.
    """
    adaylar = [
        i for i, (op, arg, _pos) in enumerate(ops)
        if op.name in _METIN_OPCODE and _esit(arg, blok.source)
    ]
    if not adaylar:
        return None
    if len(adaylar) == 1:
        return adaylar[0]

    satir_eslesen = []
    for i in adaylar:
        for op, arg, _pos in ops[i + 1: i + 10]:
            if op.name in _SAYI_OPCODE and arg == blok.line:
                satir_eslesen.append(i)
                break
    havuz = satir_eslesen or adaylar
    if blok.index < len(havuz):
        return havuz[blok.index]
    return havuz[0]


def _esit(arg: Any, metin: str) -> bool:
    if isinstance(arg, bytes):
        arg = arg.decode("utf-8", "surrogateescape")
    return arg == metin


def blogu_degistir(ham: bytes, blok: PyBlok, yeni_kaynak: str) -> bytes:
    """
    Bir Python bloğunun kaynağını değiştirip yeni `.rpyc` içeriğini döner.

    Bayt seviyesinde çalışıyoruz: pickle'ı çözüp yeniden kurmak, gerçek
    Ren'Py sınıfları olmadan mümkün değil. Metin sabitleri uzunluk önekli
    olduğu için yerinde değiştirmek güvenli — tek şart, protokol 4+
    dosyalarındaki FRAME uzunluğunu da düzeltmek (düzeltilmezse Ren'Py
    dosyayı hiç açamıyor; ölçüldü).
    """
    veri = slot_oku(ham, 1)
    if veri is None:
        raise RpycError("dosyada okunabilir bir bölüm bulunamadı.")

    try:
        ops = list(pickletools.genops(io.BytesIO(veri)))
    except Exception as exc:  # noqa: BLE001
        raise RpycError(f"içerik çözümlenemedi: {exc}") from exc

    hedef = _hedef_opcode(ops, blok)
    if hedef is None:
        raise RpycError(
            "Değiştirilecek kod bloğu dosyada bulunamadı. Dosya bu arada "
            "değişmiş olabilir; listeyi yenileyip tekrar deneyin."
        )

    bas = ops[hedef][2]
    son = ops[hedef + 1][2] if hedef + 1 < len(ops) else len(veri)
    yeni_ops = _metin_opcode(yeni_kaynak)
    fark = len(yeni_ops) - (son - bas)

    cikti = bytearray(veri[:bas] + yeni_ops + veri[son:])

    # FRAME uzunluğu: protokol 4+ pickle'larda zorunlu.
    for op, arg, pos in ops:
        if op.name != "FRAME" or not isinstance(arg, int):
            continue
        icerik_bas = pos + 9  # opcode (1) + <Q uzunluk (8)
        if icerik_bas <= bas < icerik_bas + arg:
            cikti[pos + 1: pos + 9] = struct.pack("<Q", arg + fark)
            break

    return slot1_dosyasi(bytes(cikti))
