"""
ZIP dosyalarını DERİNLEMESİNE denetler ve güvenli biçimde açar.

Neden gerekli
-------------
Bir ZIP'in "açılabilir" olduğunu anlamanın üç ayrı derinliği var ve
aralarındaki fark, bu projede ÜÇ KEZ üst üste arızaya yol açtı:

1. `zipfile.is_zipfile()` — yalnızca dosyanın SONUNDAKİ EOCD kaydına
   bakar. Merkezi dizin bozuksa bile True döner.
2. `ZipFile(...).namelist()` — merkezi dizini okur. Bu da yeterli DEĞİL:
   dosyaların GERÇEK verisi bambaşka bir yerde duruyor.
3. Her üyenin YEREL BAŞLIĞINI gerçekten okumak — asıl açma işleminin
   yaptığı şey.

Ölçülen davranış (gerçek dosyalar üretilip denendi):

    bozulma                         namelist()   extractall()
    ------------------------------  -----------  ---------------------------
    yerel başlık imzası bozuk       OK           Bad magic number for file header
    merkezi dizinde offset yanlış   OK           Truncated file header
    gövde verisi bozuk              OK           name in directory ... differ
    merkezi dizin bozuk             BadZipFile   —
    dosya sonu kesik                BadZipFile   —

İlk üç satır, kullanıcının "yükleme başarılı ama derleme ZIP bozuk
diyor" şikâyetinin tam karşılığı. Bu modül üçünü de yükleme anında
yakalıyor.

Kalıcı disk (ağ birimi) sorunu
------------------------------
Yükleme SIRALI yazılıyor ve doğrulama dosyanın SONUNU okuyor; açma ise
784 MB'lık dosyanın her yerine ATLAYARAK erişiyor. Ağ üzerinden bağlı
bir birimde bu iki erişim biçimi aynı sonucu vermeyebiliyor. Bu yüzden
derleme, dosyayı önce YEREL diske sıralı olarak kopyalayıp SHA-256'sını
doğruluyor: kopya tutmuyorsa sorunun diskte olduğunu KANITLAMIŞ
oluyoruz, tutuyorsa dosyanın kendisi bozuk demektir.
"""

from __future__ import annotations

import hashlib
import shutil
import struct
import zipfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Optional

_YEREL_IMZA = b"PK\x03\x04"
_YEREL_BASLIK_BOYU = 30
_OKUMA_PARCASI = 8 * 1024 * 1024


@dataclass
class ZipDurum:
    """Derin denetimin sonucu."""

    ok: bool
    girdi_sayisi: int = 0
    boyut: int = 0
    hata: str = ""
    # Sorunlu ilk üyenin adı ve konumu — hangi dosyanın bozuk olduğunu
    # söylemek, "ZIP bozuk" demekten çok daha kullanışlı.
    bozuk_uye: str = ""
    bozuk_konum: int = -1
    gorulen_baytlar: str = ""
    # Dosyanın başına eklenmiş veri (kendi kendine açılan arşivlerde
    # olur). Sıfırdan farklıysa bilmek isteriz.
    onek_baytlari: int = 0


def _yerel_basliklari_denetle(zf: zipfile.ZipFile, fh) -> Optional[ZipDurum]:
    """
    Her üyenin yerel başlığını yerinde okuyup doğrular.

    Ucuz: üye başına bir konumlanma + 30 bayt. 1000 üyeli bir arşivde
    gözle görülür bir maliyeti yok, ama açma sırasında patlayacak her
    bozulmayı ÖNCEDEN yakalıyor.
    """
    for info in zf.infolist():
        konum = info.header_offset
        if konum < 0:
            # Python'un zipfile'i, dosyanin basina veri eklenmis
            # arsivleri desteklemek icin tum konumlari kaydiriyor.
            # Dosyadan bayt DUSMUSSE bu kaydirma eksiye geciyor ve
            # merkezi dizin okunabilir kaldigi halde hicbir uye
            # bulunamiyor. Acik soylemek gerekiyor.
            return ZipDurum(
                ok=False,
                hata="dosyada eksik bayt var (üye konumları negatife "
                     "kayıyor; arşivin ortasından veri kaybolmuş)",
                bozuk_uye=info.filename,
                bozuk_konum=konum,
            )
        try:
            fh.seek(konum)
            baslik = fh.read(_YEREL_BASLIK_BOYU)
        except OSError as exc:
            return ZipDurum(
                ok=False,
                hata=f"dosya okunamadı ({exc})",
                bozuk_uye=info.filename,
                bozuk_konum=konum,
            )

        if len(baslik) < _YEREL_BASLIK_BOYU:
            return ZipDurum(
                ok=False,
                hata="dosya beklenenden kısa (üye başlığı dosya sonuna taşıyor)",
                bozuk_uye=info.filename,
                bozuk_konum=konum,
            )

        if baslik[:4] != _YEREL_IMZA:
            return ZipDurum(
                ok=False,
                hata="üye başlığı beklenen imzayı taşımıyor "
                     "(açma sırasında 'Bad magic number for file header')",
                bozuk_uye=info.filename,
                bozuk_konum=konum,
                gorulen_baytlar=baslik[:4].hex(" "),
            )

        # Adın merkezi dizindekiyle aynı olduğunu da doğruluyoruz: gövde
        # kaymışsa imza tutup ad tutmayabiliyor.
        ad_boyu, ek_boyu = struct.unpack("<HH", baslik[26:30])
        try:
            ham_ad = fh.read(ad_boyu)
        except OSError:
            ham_ad = b""
        if len(ham_ad) < ad_boyu:
            return ZipDurum(
                ok=False,
                hata="üye adı okunamadı (dosya kısa)",
                bozuk_uye=info.filename,
                bozuk_konum=konum,
            )
        try:
            yerel_ad = ham_ad.decode(
                "utf-8" if info.flag_bits & 0x800 else "cp437", "replace"
            )
        except Exception:  # noqa: BLE001
            yerel_ad = ""
        if yerel_ad and yerel_ad != info.filename:
            return ZipDurum(
                ok=False,
                hata=f"üye adı tutmuyor (dizinde {info.filename!r}, "
                     f"dosyada {yerel_ad!r})",
                bozuk_uye=info.filename,
                bozuk_konum=konum,
            )
    return None


def derin_denetle(path: Path, tam_crc: bool = False) -> ZipDurum:
    """
    ZIP'i, açma işleminin göreceği DERİNLİKTE denetler.

    `tam_crc` açıksa her üyenin verisi de açılıp CRC'si doğrulanıyor.
    Bu yavaş (dosyanın tamamı çözülüyor), o yüzden varsayılan kapalı:
    yerel başlık denetimi, gözlenen bozulmaların hepsini zaten yakalıyor.
    """
    try:
        boyut = path.stat().st_size
    except OSError as exc:
        return ZipDurum(ok=False, hata=f"dosya okunamadı: {exc}")

    if boyut == 0:
        return ZipDurum(ok=False, hata="dosya boş (0 bayt)")

    try:
        with path.open("rb") as fh, zipfile.ZipFile(fh) as zf:
            try:
                adlar = zf.namelist()
            except zipfile.BadZipFile as exc:
                return ZipDurum(
                    ok=False, boyut=boyut,
                    hata=f"ZIP dizini okunamadı: {exc}",
                )
            if not adlar:
                return ZipDurum(ok=False, boyut=boyut, hata="ZIP boş (hiç dosya yok)")

            onek = 0
            try:
                ilk = min(i.header_offset for i in zf.infolist())
                onek = ilk if ilk > 0 else 0
            except ValueError:
                onek = 0

            sorun = _yerel_basliklari_denetle(zf, fh)
            if sorun is not None:
                sorun.boyut = boyut
                sorun.girdi_sayisi = len(adlar)
                sorun.onek_baytlari = onek
                return sorun

            if tam_crc:
                try:
                    kotu = zf.testzip()
                except zipfile.BadZipFile as exc:
                    return ZipDurum(
                        ok=False, boyut=boyut, girdi_sayisi=len(adlar),
                        hata=f"içerik doğrulanamadı: {exc}",
                    )
                if kotu:
                    return ZipDurum(
                        ok=False, boyut=boyut, girdi_sayisi=len(adlar),
                        hata="üyenin içeriği bozuk (CRC tutmuyor)",
                        bozuk_uye=kotu,
                    )

            return ZipDurum(
                ok=True, girdi_sayisi=len(adlar), boyut=boyut, onek_baytlari=onek
            )
    except zipfile.BadZipFile as exc:
        return ZipDurum(ok=False, boyut=boyut, hata=f"ZIP dizini okunamadı: {exc}")
    except OSError as exc:
        return ZipDurum(ok=False, boyut=boyut, hata=f"dosya okunamadı: {exc}")


# ---------------------------------------------------------------------------
# Yerel kopya
# ---------------------------------------------------------------------------


@dataclass
class KopyaSonuc:
    ok: bool
    hedef: Optional[Path] = None
    boyut: int = 0
    sha256: str = ""
    hata: str = ""
    # Kaydedilen özetle tutuyor mu? None = karşılaştıracak özet yoktu.
    ozet_tutuyor: Optional[bool] = None


def sha256_of(path: Path) -> str:
    """Dosyanın SHA-256'sını sıralı okuyarak hesaplar."""
    hasher = hashlib.sha256()
    try:
        with path.open("rb") as fh:
            for parca in iter(lambda: fh.read(_OKUMA_PARCASI), b""):
                hasher.update(parca)
    except OSError:
        return ""
    return hasher.hexdigest()


def yerel_kopya(
    kaynak: Path, hedef: Path, beklenen_sha: str = "",
) -> KopyaSonuc:
    """
    Dosyayı SIRALI okuyup hedefe kopyalar ve özetini hesaplar.

    Neden kopyalıyoruz: kalıcı disk bir ağ birimi. Yükleme sıralı
    yazıyor, doğrulama dosyanın sonunu okuyor; açma ise dosyanın her
    yerine atlıyor. Bu üçü aynı veriyi vermeyebilir. Sıralı bir kopya +
    özet karşılaştırması, sorunun diskte mi yoksa dosyada mı olduğunu
    KESİN olarak ayırıyor.
    """
    hasher = hashlib.sha256()
    okunan = 0
    try:
        hedef.parent.mkdir(parents=True, exist_ok=True)
        with kaynak.open("rb") as kay, hedef.open("wb") as hed:
            while True:
                parca = kay.read(_OKUMA_PARCASI)
                if not parca:
                    break
                hasher.update(parca)
                hed.write(parca)
                okunan += len(parca)
    except OSError as exc:
        return KopyaSonuc(ok=False, hata=f"kopyalanamadı: {exc}", boyut=okunan)

    ozet = hasher.hexdigest()
    tutuyor: Optional[bool] = None
    if beklenen_sha:
        tutuyor = ozet == beklenen_sha

    return KopyaSonuc(
        ok=True, hedef=hedef, boyut=okunan, sha256=ozet, ozet_tutuyor=tutuyor
    )


# ---------------------------------------------------------------------------
# Üye üye açma
# ---------------------------------------------------------------------------


@dataclass
class CikarmaSonuc:
    basarili: int = 0
    bozuk: list[str] = field(default_factory=list)
    hata: str = ""

    @property
    def ok(self) -> bool:
        return not self.bozuk and not self.hata


def uye_uye_cikar(
    zip_path: Path, hedef: Path, ilerleme: Optional[Callable[[int, int], None]] = None,
) -> CikarmaSonuc:
    """
    Arşivi üye üye açar; bozuk üyeleri ATLAYIP adlarını biriktirir.

    `extractall()` ilk bozuk üyede tüm işi düşürüyor. Bir oyunun tek bir
    ses dosyası bozuksa derlemenin tamamını iptal etmek gereksiz; hangi
    dosyaların açılamadığını bilmek ise tamiri mümkün kılıyor.
    """
    sonuc = CikarmaSonuc()
    try:
        with zipfile.ZipFile(zip_path) as zf:
            uyeler = zf.infolist()
            toplam = len(uyeler)
            for i, info in enumerate(uyeler):
                try:
                    zf.extract(info, hedef)
                    sonuc.basarili += 1
                except (zipfile.BadZipFile, OSError, EOFError, RuntimeError):
                    sonuc.bozuk.append(info.filename)
                if ilerleme is not None and (i % 200 == 0 or i == toplam - 1):
                    ilerleme(i + 1, toplam)
    except zipfile.BadZipFile as exc:
        sonuc.hata = f"arşiv dizini okunamadı: {exc}"
    except OSError as exc:
        sonuc.hata = f"dosya açılamadı: {exc}"
    return sonuc


def guvenli_sil(yol: Path) -> None:
    shutil.rmtree(yol, ignore_errors=True)
