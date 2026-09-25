"""
Oyunun `.rpy` dosyalarındaki BASİT söz dizimi hatalarını bulup onarır.

Neden gerekli
-------------
Ren'Py, APK üretmeden önce projeyi bir kez kendi yorumlayıcısıyla açıyor.
Oyunun script'inde bir söz dizimi hatası varsa `renpy/script.py` şunu
yapıyor (gerçek kaynak, `load_script`):

    if renpy.parser.report_parse_errors():
        raise SystemExit(-1)

Yani süreç KASITLI olarak duruyor. Bu bir çökme değil, Ren'Py'nin doğru
davranışı. Ama derleme hattımızda bu, kullanıcıya "Launch failed
(returned 1)" diye ulaşıyor ve asıl satır uzun günlüğün içinde kayboluyor.

Üstelik Ren'Py'nin KENDİ launcher'ı (`launcher/game/project.rpy`) her alt
süreç çağrısına `--errors-in-editor` ekliyor. Bu bayrak varken Ren'Py,
hatalı satırı göstermek için sistem düzenleyicisini açmayı deniyor; sunucu
konteynerinde `xdg-open` bulunmadığı için de günlüğe şu iz düşüyor:

    FileNotFoundError: [Errno 2] No such file or directory: 'xdg-open'

Bu iz TAMAMEN zararsız — `renpy/editor.py` çağrıyı zaten `try/except` ile
sarmış, yalnızca yazdırıyor. Ama teşhis eden gözde asıl hatanın üstünü
örtüyor.

Ne yapıyoruz
------------
1. Derlemeye başlamadan ÖNCE, oyunu gerçek Ren'Py ikilisiyle bir kez
   `compile` komutuyla açıyoruz. Bu komut ekran açmıyor, oyunu
   başlatmıyor; yalnızca script'i ayrıştırıp çıkıyor.
2. Ren'Py hata bildirirse, hatanın KENDİ mesajına bakarak mekanik olarak
   düzeltilebilecek olanları düzeltiyoruz.
3. Düzelttikten sonra TEKRAR `compile` çalıştırıyoruz. Yani hiçbir
   düzeltmeye "herhalde olmuştur" demiyoruz — doğrulayan, Ren'Py'nin
   kendisi.
4. Bir tur hiç ilerleme sağlamazsa o turun değişiklikleri GERİ ALINIYOR
   ve döngü duruyor. Tahminle dosya bozmak, hiç dokunmamaktan kötüdür.

Hangi hatalar düzeltiliyor
--------------------------
Yalnızca Ren'Py dil bilgisinin kendisinin "iki biçim de geçerli" dediği,
anlamı DEĞİŞTİRMEYEN durumlar:

* `<X> statement expects a non-empty block.` — satır iki nokta üst üste
  ile bitiyor ama altında blok yok.
  - `scene / show / show layer / camera / style` için iki nokta
    ZATEN İSTEĞE BAĞLI (gerçek kaynak: `renpy/parser.py`, `if l.match(":")`
    ... `else: l.expect_noblock(...)`). Fazladan `:` siliniyor.
  - Bloğu ZORUNLU olan geri kalan her ifadede (`if / elif / else /
    while / init / python / transform / image / screen / vbox / choice /
    translate` …) boş bloğun tek anlamlı karşılığı `pass` satırıdır;
    o ekleniyor. İfade adı Ren'Py sürümüne göre değişebildiği için
    (ölçüldü: 8.5.3 "screen statement", 8.2.3 yalnızca "screen") burada
    sabit bir izin listesi YOK — `pass` deneniyor ve sonucu gerçek
    Ren'Py onaylıyor.
  - `menu` için güvenli bir karşılık YOK (ölçüldü: `menu:` + `pass` ->
    "expected menuitem"). Ona dokunulmuyor, yalnızca bildiriliyor.

* `Line is indented, but the preceding <X> statement does not expect a
  block.` — bunun tersi: blok yazılmış ama üstteki satırda `:` unutulmuş.
  Yine yalnızca `:` alan beş ifade için, üstteki satıra `:` ekleniyor.

Geri kalan her hata OLDUĞU GİBİ bırakılıyor ve kullanıcıya dosya + satır +
kaynak satırıyla birlikte bildiriliyor.
"""

from __future__ import annotations

import errno
import os
import platform
import re
import subprocess
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from . import py23

# `:` isteğe bağlı olan ifadeler. Bunlarda hem `scene x` hem `scene x:`
# + blok geçerlidir; boş blok varken `:` silmek anlamı değiştirmez.
# Kaynak: renpy/parser.py -> scene_statement / show_statement /
# show_layer_statement / camera_statement / style_statement.
COLON_OPTIONAL: dict[str, str] = {
    "scene statement": "scene",
    "show statement": "show",
    "show layer statement": "show layer",
    "camera statement": "camera",
    "style statement": "style",
}

# Bloğu ZORUNLU olan ifadelerde boş bloğun tek anlamlı karşılığı `pass`
# satırıdır. Aşağıdakiler gerçek Ren'Py ile TEK TEK ölçüldü (8.5.3 ve
# 8.2.3): hepsi `pass` kabul ediyor.
#
#   if / elif / else / while / init / python / transform / label
#   screen ve ekran dili kapsayıcıları (vbox, hbox, frame, …)
#   image (ATL gövdesi olarak), choice menuitem, translate
#
# Liste burada BELGE olarak duruyor; kod bir "izin listesi" gibi
# davranmıyor çünkü ifade adı Ren'Py sürümüne göre değişebiliyor
# (ölçüldü: 8.5.3 "screen statement" derken 8.2.3 yalnızca "screen"
# diyor) ve oyunlar kendi ifadelerini tanımlayabiliyor. Bunun yerine
# `pass` deneniyor ve sonucu GERÇEK Ren'Py onaylıyor; onaylamazsa tüm
# değişiklikler geri alınıyor.
PASS_VERIFIED: frozenset[str] = frozenset(
    {
        "if statement",
        "IF statement",
        "elif clause",
        "ELIF clause",
        "else clause",
        "ELSE clause",
        "while statement",
        "init statement",
        "python block",
        "transform statement",
        "screen statement",
        "screen",
        "vbox",
        "image statement",
        "choice menuitem",
        "translate statement",
    }
)

# Güvenli bir karşılığı OLMAYAN ifadeler. Bunlara hiç dokunmuyoruz;
# denemek yalnızca bir tur zaman kaybettirirdi.
# (Ölçüm: `menu:` + `pass` -> "expected menuitem".)
NO_SAFE_FIX: dict[str, str] = {
    "menu statement": (
        "Boş bir menünün anlamlı karşılığı yok — en az bir seçenek "
        'satırı ("...":) gerekiyor.'
    ),
}

# Ren'Py'nin ayrıştırma hatası satırı. `search` ile aranıyor çünkü aynı
# metin uzun derleme günlüğünün içinde, başka çıktının arasında da
# geçebiliyor. Python'un kendi yığın izi satırı (`File "x", line 5, in f`)
# bu kalıba UYMAZ: orada satır numarasından sonra `, in` gelir, `: ` değil.
_ERROR_LINE_RE = re.compile(
    r'File "(?P<file>[^"]+)", line (?P<line>\d+): (?P<msg>[^\n]*)'
)
_EMPTY_BLOCK_RE = re.compile(r"^(?P<stmt>.+?) expects a non-empty block\.$")
_NOBLOCK_RE = re.compile(
    r"^Line is indented, but the preceding (?P<stmt>.+?) statement does not "
    r"expect a block\."
)

# Ren'Py, kaynak satırının altına konumu gösteren bir `^` çiziyor.
_CARET_RE = re.compile(r"^\s*\^\s*$")

# Ren'Py'nin init/çalışma anı istisnaları için bastığı kendi bandı.
_FULL_TRACEBACK_RE = re.compile(r"^\s*(Full traceback:|While running game code:)\s*$")
# Yığın izi satırı: `  File "game/x.rpy", line 5, in script`
_FRAME_RE = re.compile(r'^\s*File "(?P<file>[^"]+)", line (?P<line>\d+), in ')
# İstisnanın kendisi: girintisiz, `AdI: mesaj` biçiminde.
_EXC_RE = re.compile(
    r"^(?P<exc>[A-Za-z_][\w.]*(?:Error|Exception|Warning)?): ?(?P<msg>.*)$"
)

# Yanlış mimarideki ikili işareti (bkz. run_check / repair).
_ENOEXEC_MARK = "[mimari-uyumsuz] "

_ORTAM_KAPATMA = "AEROKEY_SYNTAX_FIX"
# Init hatalarında derlemeyi durdurmayı kapatmak için kaçış kapısı.
_ORTAM_INIT = "AEROKEY_INIT_CHECK"


def _kapali(deger: str) -> bool:
    return deger.strip().lower() in ("0", "false", "off", "hayir")


def _init_denetimi_acik() -> bool:
    return not _kapali(os.environ.get(_ORTAM_INIT, ""))


@dataclass
class ParseIssue:
    """Ren'Py'nin bildirdiği tek bir ayrıştırma hatası."""

    file: str
    line: int
    message: str
    source: str = ""

    @property
    def key(self) -> tuple[str, int, str]:
        return (self.file, self.line, self.message)

    def human(self) -> str:
        satir = f"{self.file}:{self.line} — {self.message}"
        if self.source.strip():
            satir += f"\n      {self.source.strip()}"
        return satir


@dataclass
class InitError:
    """
    Oyunun init kodunda oluşan bir istisna.

    Söz dizimi hatasından farkı: script AYRIŞTIRILABİLİYOR, ama
    çalıştırılınca patlıyor (`init python:` bloğu, `define` ifadesi,
    `renpy.error(...)` çağrısı…).

    Neden derlemeyi düşürüyor — gerçek kaynaktan doğrulandı:
      1. İstisna oluşunca `renpy/display/error.py` -> `error_dump()`
         çağrılıyor, o da `renpy.dump.dump(True)` ile `navigation.json`
         dosyasına `"error": true` yazıyor.
      2. `dump()` bir kez çalışınca `completed_dump = True` oluyor, yani
         sonradan `main.py`'deki `dump(False)` çağrısı bu bayrağı
         DÜZELTMİYOR.
      3. Launcher (`launcher/game/distribute.rpy`) dosyayı okuyup
         `if project.dump.get("error"): raise` diyor ve derleme
         "Could not get build data from the project" ile duruyor.

    Yani tek bir init istisnası, 10+ dakikalık derlemenin sonunda kesin
    başarısızlık demek. Bu yüzden önceden yakalayıp bildiriyoruz.
    """

    file: str
    line: int
    exception: str
    # Ren'Py, yığın izinde her karenin altına o satırın kaynağını da
    # basıyor; kullanıcıya göstermek teşhisi doğrudan eyleme çeviriyor.
    source: str = ""

    @property
    def key(self) -> tuple[str, int, str]:
        return (self.file, self.line, self.exception)

    def human(self) -> str:
        satir = f"{self.file}:{self.line} — {self.exception}"
        if self.source.strip():
            satir += f"\n      {self.source.strip()}"
        return satir


@dataclass
class Fix:
    """Uygulanmış tek bir düzeltme."""

    file: str
    line: int
    rule: str
    before: str
    after: str

    def human(self) -> str:
        return (
            f"{self.file}:{self.line} ({self.rule})\n"
            f"      önce : {self.before.strip()}\n"
            f"      sonra: {self.after.strip()}"
        )


@dataclass
class RepairResult:
    """Onarım turunun sonucu."""

    ran: bool = False
    ok: bool = False
    rounds: int = 0
    seconds: float = 0.0
    fixes: list[Fix] = field(default_factory=list)
    remaining: list[ParseIssue] = field(default_factory=list)
    # Projede bulunan TÜM ayrıştırma hataları (düzeltilenler dahil).
    # Ren'Py ilk hatalı dosyadan sonra durduğu için bu liste ayrı bir
    # tarama ile toplanıyor; kullanıcı günlükte hepsini bir arada görsün.
    all_issues: list[ParseIssue] = field(default_factory=list)
    # Tarama, dosya sınırına ya da süreye takılıp yarıda kaldıysa True.
    inventory_partial: bool = False
    # Denenmiş ama sonuç temiz çıkmadığı için GERİ ALINMIŞ düzeltmeler.
    # Kullanıcıya "şunları otomatik halledebiliyordum ama şu satır elde
    # kaldığı için hepsini geri aldım" diyebilmek için tutuluyor.
    reverted: list[Fix] = field(default_factory=list)
    # Oyunun INIT kodunda oluşan istisnalar. Söz dizimi hatası değiller —
    # script ayrıştırılıyor ama çalıştırılınca patlıyor. Derlemeyi yine de
    # düşürüyorlar; gerekçe `InitError` içinde.
    init_errors: list["InitError"] = field(default_factory=list)
    # Init hatalarına uygulanan ve Ren'Py tarafından onaylanan düzeltmeler.
    init_fixes: list[Fix] = field(default_factory=list)
    # Denenip Ren'Py tarafından REDDEDİLEN adaylar (hata kaybolmadı).
    init_reverted: list[Fix] = field(default_factory=list)
    # Onaylanmış düzeltmeler, başka hata kaldığı için topluca geri
    # alındıysa True. `init_fixes` yine dolu kalır: o düzeltmeler
    # gerçekten işe yaradı, yalnızca yarım onarım bırakmamak için
    # geri sarıldılar.
    init_rolled_back: bool = False
    note: str = ""
    # Ön denetim çalıştırılamadıysa sebebin ayrıntısı (yol, istisna,
    # zaman aşımı süresi). Bir sonraki turda tahmin etmek zorunda
    # kalmamak için ayrı tutuluyor.
    failure_detail: str = ""
    # Ayrıştırma hatası DIŞINDA bir sebeple çalışmadıysa burası dolar; bu
    # durumda derlemeyi ASLA durdurmuyoruz (bizim adımımız yüzünden
    # çalışabilecek bir derleme engellenmemeli).
    inconclusive: bool = False
    # Ren'Py'nin `.bak`'a çevirip bizim geri aldığımız betikler. Dolu
    # olması, `--keep-orphan-rpyc` bayrağının işe yaramadığı (ör. eski
    # bir Ren'Py sürümü) ve güvenlik ağının devreye girdiği anlamına
    # gelir — günlükte görünmesi gerekir.
    orphan_restored: list[str] = field(default_factory=list)


# --------------------------------------------------------------------------
# Metin tarama yardımcıları
# --------------------------------------------------------------------------


def _scan(
    line: str, quote: Optional[str], depth: int
) -> tuple[str, Optional[str], int]:
    """
    Bir fiziksel satırı tarar.

    Döner: (yorumsuz kod parçası, satır sonunda açık kalan tırnak, parantez
    derinliği). Tırnak durumu satırlar arasında taşınır çünkü Ren'Py'de bir
    metin sabiti birden çok fiziksel satıra yayılabiliyor.
    """
    out: list[str] = []
    i = 0
    n = len(line)
    while i < n:
        ch = line[i]
        if quote is not None:
            out.append(ch)
            if ch == "\\" and i + 1 < n:
                out.append(line[i + 1])
                i += 2
                continue
            if ch == quote:
                quote = None
            i += 1
            continue

        if ch == "#":
            # Yorum: satırın kalanı kod değil.
            break
        if ch in "\"'`":
            quote = ch
            out.append(ch)
            i += 1
            continue
        if ch in "([{":
            depth += 1
        elif ch in ")]}":
            depth -= 1
        out.append(ch)
        i += 1

    return "".join(out), quote, depth


def _code_part(line: str) -> str:
    """Satırın yorum içermeyen kod bölümü (satır sonu dahil değil)."""
    code, _, _ = _scan(line.rstrip("\r\n"), None, 0)
    return code


def _line_ending(line: str) -> str:
    if line.endswith("\r\n"):
        return "\r\n"
    if line.endswith("\n"):
        return "\n"
    return ""


def _indent(line: str) -> str:
    body = line.rstrip("\r\n")
    return body[: len(body) - len(body.lstrip())]


def _is_blank_or_comment(line: str) -> bool:
    return not _code_part(line).strip()


def _logical_line_end(lines: list[str], start: int) -> int:
    """
    `start` ile başlayan MANTIKSAL satırın son fiziksel satırının dizini.

    Ren'Py'de tek bir ifade, açık parantezler ya da satır sonundaki `\\`
    sayesinde birden çok fiziksel satıra yayılabiliyor. Hem `:` silerken
    hem `:` eklerken doğru satıra dokunmak için bunu bilmek şart.
    """
    quote: Optional[str] = None
    depth = 0
    i = start
    while i < len(lines):
        code, quote, depth = _scan(lines[i].rstrip("\r\n"), quote, depth)
        if quote is None and depth <= 0 and not code.rstrip().endswith("\\"):
            return i
        i += 1
    return len(lines) - 1


def _logical_line_start(lines: list[str], end: int, geri: int = 30) -> int:
    """
    Son fiziksel satırı `end` olan mantıksal satırın BAŞINI bulur.

    Adayları yukarı doğru deneyip `_logical_line_end` ile doğruluyoruz;
    böylece parantezli/ters bölülü devam satırları da doğru çözülüyor.
    """
    for aday in range(end, max(-1, end - geri), -1):
        if _is_blank_or_comment(lines[aday]):
            continue
        if _logical_line_end(lines, aday) == end:
            return aday
    return end


# --------------------------------------------------------------------------
# Ren'Py çıktısını okuma
# --------------------------------------------------------------------------


def parse_errors(text: str) -> list[ParseIssue]:
    """
    Ren'Py'nin ayrıştırma hatası çıktısını okur.

    Gerçek biçim (renpy/parser.py -> report_parse_errors):

        File "game/x.rpy", line 8: scene statement expects a non-empty block.
            scene katsuki_house sepia with fade:
                                                ^

    Aynı biçim hem alt sürecin çıktısında hem de proje kökünde bırakılan
    `errors.txt` içinde bulunuyor.
    """
    issues: list[ParseIssue] = []
    lines = text.splitlines()

    for i, raw in enumerate(lines):
        match = _ERROR_LINE_RE.search(raw)
        if not match:
            continue

        try:
            numara = int(match.group("line"))
        except ValueError:
            continue

        # Kaynak satırı: hata satırının ardından gelen, `^` işaretinden
        # ÖNCEKİ satır. Bazı hatalarda hiç bulunmuyor.
        source = ""
        if i + 2 < len(lines) and _CARET_RE.match(lines[i + 2]):
            source = lines[i + 1]
        elif i + 1 < len(lines) and _CARET_RE.match(lines[i + 1]):
            source = ""

        issues.append(
            ParseIssue(
                file=match.group("file"),
                line=numara,
                message=match.group("msg").strip(),
                source=source,
            )
        )

    # Aynı hata hem çıktıda hem errors.txt'te olabilir; tekrarları at.
    benzersiz: list[ParseIssue] = []
    gorulen: set[tuple[str, int, str]] = set()
    for issue in issues:
        if issue.key in gorulen:
            continue
        gorulen.add(issue.key)
        benzersiz.append(issue)
    return benzersiz


# --------------------------------------------------------------------------
# Ren'Py ikilisini bulma ve çalıştırma
# --------------------------------------------------------------------------


def host_platform() -> str:
    """
    Bu makinenin Ren'Py platform adı (`linux-x86_64` gibi).

    Mantık `renpy.sh` ile birebir aynı: `uname -s` + `uname -m`, sonra
    bilinen takma adların eşlenmesi.
    """
    makine = (platform.machine() or "").lower()
    sistem = (platform.system() or "").lower()

    if sistem == "darwin":
        return "mac-universal"

    if makine in ("x86_64", "amd64"):
        mimari = "x86_64"
    elif makine in ("i386", "i486", "i586", "i686"):
        mimari = "i686"
    elif makine in ("aarch64", "arm64"):
        mimari = "aarch64"
    else:
        mimari = makine or "x86_64"

    return f"linux-{mimari}"


def renpy_binaries(sdk_root: Path) -> list[Path]:
    """
    SDK'daki çalıştırılabilir Ren'Py adaylarını, EN UYGUNU ÖNDE olacak
    şekilde sıralar.

    NEDEN SIRALAMA ÖNEMLİ — gerçek bir hatadan öğrenildi: eskiden
    `lib/py3-linux-*` kalıbı alfabetik taranıyordu ve renutil kurulumunda
    hem `py3-linux-aarch64` hem `py3-linux-x86_64` bulunduğu için ARM
    ikilisi seçiliyordu. x86-64 makinede sonuç:

        OSError: [Errno 8] Exec format error: .../py3-linux-aarch64/renpy

    Yani ön denetim, tamamen çalışabilir bir kurulumda sessizce devre dışı
    kalıyordu. Artık önce BU MAKİNENİN platformu deneniyor.
    """
    if not sdk_root.is_dir():
        return []

    tercih = host_platform()
    adaylar: list[Path] = []
    lib = sdk_root / "lib"

    if lib.is_dir():
        # 1) Tam eşleşme (py3-linux-x86_64, sonra eski adlandırma).
        for ad in (f"py3-{tercih}", tercih):
            adaylar.append(lib / ad / "renpy")
        # 2) Geri kalanlar: eşleşme bulunamazsa hiç denememektense
        #    denemek daha iyi; yanlış mimariyi çalıştırmak yalnızca
        #    anlaşılır bir hata verir ve bir sonraki adaya geçilir.
        for kalip in ("py3-linux-*", "linux-*"):
            for p in sorted(lib.glob(kalip)):
                adaylar.append(p / "renpy")

    adaylar.append(sdk_root / "renpy.sh")

    sonuc: list[Path] = []
    for exe in adaylar:
        if exe in sonuc or not exe.is_file():
            continue
        if not os.access(exe, os.X_OK):
            # Dosya var ama çalıştırma izni yok. Bu, arşivden açılmış
            # kurulumlarda olabiliyor ve tek başına ön denetimi sessizce
            # devre dışı bırakırdı; izni verip devam ediyoruz.
            try:
                exe.chmod(exe.stat().st_mode | 0o111)
            except OSError:
                continue
            if not os.access(exe, os.X_OK):
                continue
        sonuc.append(exe)

    return sonuc


def find_renpy_binary(sdk_root: Path) -> Optional[Path]:
    """SDK içindeki, bu makineye en uygun Ren'Py yorumlayıcısı."""
    adaylar = renpy_binaries(sdk_root)
    return adaylar[0] if adaylar else None


_ORPHAN_NOTU = """
`compile` komutu, KAYNAĞI OLMAYAN .rpyc dosyalarını YOK EDER.

Ren'Py 8.5.3 kaynağı (renpy/script.py):

    if (renpy.game.args.command == "compile") and not (
            renpy.game.args.keep_orphan_rpyc):
        self.clean_script_files()

ve clean_script_files():

    if not os.path.isfile(dn + fn + ".rpy") and not os.path.isfile(
            dn + fn + "_ren.py"):
        os.rename(name, name + ".bak")

Yani yanında `.rpy` kaynağı bulunmayan her `.rpyc`, `.rpyc.bak`
adına çevriliyor. DDLC gibi DERLENMİŞ dağıtımlarda `.rpy` hiç YOKTUR:
dolayısıyla söz dizimi denetimimiz oyunun TÜM betiklerini siliyordu.
Sonuç, telefonda "could not find label 'start'" çökmesiydi ve çıkış
kodu 0 olduğu için hiçbir yerde hata görünmüyordu.

Gerçek Ren'Py 8.5.3 ikilisiyle ölçüldü:

    compile                      -> script.rpyc  => script.rpyc.bak
    compile --keep-orphan-rpyc   -> script.rpyc  korunuyor

Bayrağı geçmenin yanında, KOŞULSUZ bir güvenlik ağı da var
(`restore_orphan_backups`): bayrağı tanımayan eski bir Ren'Py sürümü
kullanılsa bile dosyalar geri alınıyor.
"""

# `compile` çalıştırmadan önce/sonra karşılaştırdığımız betik uzantıları.
_BETIK_UZANTILARI = (".rpyc", ".rpymc")


def script_snapshot(project_root: Path) -> set[Path]:
    """Projedeki derlenmiş betiklerin YOLLARINI kaydeder."""
    game = Path(project_root) / "game"
    if not game.is_dir():
        return set()
    bulunan: set[Path] = set()
    for uzanti in _BETIK_UZANTILARI:
        bulunan.update(game.rglob("*" + uzanti))
    return bulunan


def restore_orphan_backups(project_root: Path, onceki: set[Path]) -> list[str]:
    """
    Ren'Py'nin `.bak`'a çevirdiği betikleri geri alır.

    YALNIZCA bizim çalıştırmamızdan ÖNCE var olan dosyalar geri
    alınıyor. Oyunun kendi içinde gelen `.rpyc.bak` dosyaları (mod
    yapımcıları taban oyunu böyle devre dışı bırakıyor) oldukları gibi
    bırakılıyor — onlara dokunmak oyunu bozardı.

    Döner: geri alınan dosyaların game/ içindeki göreli adları.
    """
    game = Path(project_root) / "game"
    geri: list[str] = []
    for yol in onceki:
        if yol.exists():
            continue
        yedek = yol.with_name(yol.name + ".bak")
        if not yedek.is_file():
            continue
        try:
            yedek.replace(yol)
        except OSError:
            continue
        try:
            geri.append(str(yol.relative_to(game)))
        except ValueError:
            geri.append(yol.name)
    geri.sort()
    return geri


def run_check(
    renpy_bin: Path,
    project_root: Path,
    timeout: int = 900,
    keep_orphan_rpyc: bool = True,
    restored_out: Optional[list[str]] = None,
) -> tuple[Optional[int], str, str]:
    """
    `renpy <proje> compile --keep-orphan-rpyc` çalıştırır.

    `compile` komutu (renpy/arguments.py -> compile) `False` döndürüyor,
    yani oyun BAŞLATILMIYOR: yalnızca script yükleniyor, init kodu
    çalışıyor ve süreç kapanıyor. Ekran gerekmiyor — DISPLAY olmadan
    da çalıştığı ölçüldü.

    Ekran KASITLI olarak kapatılıyor (`SDL_VIDEODRIVER=dummy`, DISPLAY
    yok): bu adımın tek işi script'i denetlemek, hiçbir pencere açması
    gerekmiyor. Ekransız çalıştırmak, oyunun init kodundaki bir hatanın
    etkileşimli bir hata ekranı açıp süreci asılı bırakma ihtimalini de
    tamamen ortadan kaldırıyor.

    Döner: (çıkış kodu ya da None [zaman aşımı/çalıştırılamadı], çıktı,
    başarısızlık ayrıntısı).
    """
    # Mutlak yola çeviriyoruz: alt süreci projenin içinde çalıştırdığımız
    # için göreli bir yol orada aranır ve "dosya yok" hatası verirdi.
    cmd = [str(Path(renpy_bin).resolve()), str(Path(project_root).resolve()), "compile"]
    if keep_orphan_rpyc:
        # OYUNU YOK ETMEYİ ÖNLEYEN BAYRAK — ayrıntı için _ORPHAN_NOTU.
        cmd.append("--keep-orphan-rpyc")
    env = dict(os.environ)
    env.pop("DISPLAY", None)
    env["SDL_VIDEODRIVER"] = "dummy"
    env["SDL_AUDIODRIVER"] = "dummy"

    # KOŞULSUZ GÜVENLİK AĞI. Bayrağı geçiyoruz ama ona GÜVENMİYORUZ:
    # bayrağı tanımayan eski bir Ren'Py sürümü seçilirse ya da ileride
    # başka bir kod yolu aynı şeyi yaparsa, betikleri yine geri alıyoruz.
    # Bu adımın atlanması oyunun TÜM script'ini yok ediyor ve çıkış kodu
    # 0 olduğu için hiçbir yerde hata görünmüyor.
    onceki_betikler = script_snapshot(project_root)

    def _geri_yukle() -> None:
        geri = restore_orphan_backups(project_root, onceki_betikler)
        if geri and restored_out is not None:
            restored_out.extend(geri)

    try:
        proc = subprocess.run(
            cmd,
            cwd=str(project_root),
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            timeout=timeout,
            check=False,
            env=env,
        )
    except subprocess.TimeoutExpired as exc:
        _geri_yukle()
        cikti = exc.output or b""
        if isinstance(cikti, str):
            cikti = cikti.encode("utf-8", "replace")
        return (
            None,
            cikti.decode("utf-8", "replace"),
            f"{timeout} saniyede bitmedi (zaman aşımı). Komut: {' '.join(cmd)}",
        )
    except OSError as exc:
        # Yanlış mimarideki bir ikiliyi çalıştırmak ENOEXEC verir. Çağıran
        # taraf bunu görüp SIRADAKI adaya geçebilsin diye ayırt edilebilir
        # bir işaret koyuyoruz.
        _geri_yukle()
        isaret = _ENOEXEC_MARK if getattr(exc, "errno", None) == errno.ENOEXEC else ""
        return (
            None,
            "",
            f"{isaret}Ren'Py başlatılamadı ({type(exc).__name__}: {exc}). "
            f"Komut: {' '.join(cmd)}",
        )

    _geri_yukle()
    return proc.returncode, proc.stdout.decode("utf-8", "replace"), ""


def parse_init_errors(text: str) -> list[InitError]:
    """
    Ren'Py'nin çıktısından init/çalışma anı istisnalarını çıkarır.

    Gerçek biçim (kullanıcının derleme günlüğünden birebir):

        Full traceback:
          File "game/splash.rpy", line 5, in script
          File "renpy/ast.py", line 1193, in execute
          ...
          File "game/splash.rpy", line 10, in <module>
        Exception: DDEK arşiv dosyaları /game klasöründe bulunamadı.

    Konum olarak OYUNA ait son kareyi alıyoruz: Ren'Py'nin kendi
    dosyalarını (renpy/…) göstermek kullanıcıyı yanlış yere yollardı.
    """
    bulunan: list[InitError] = []
    gorulen: set[tuple[str, int, str]] = set()
    lines = text.splitlines()

    i = 0
    while i < len(lines):
        if not _FULL_TRACEBACK_RE.match(lines[i]):
            i += 1
            continue

        son_oyun_karesi: Optional[tuple[str, int, str]] = None
        j = i + 1
        while j < len(lines):
            satir = lines[j]
            kare = _FRAME_RE.match(satir)
            if kare:
                yol = kare.group("file").replace("\\", "/")
                if yol.startswith("game/"):
                    # Karenin hemen altındaki satır, o satırın kaynağı
                    # olabiliyor (başka bir kare ya da işaretçi değilse).
                    kaynak = ""
                    if j + 1 < len(lines):
                        aday = lines[j + 1]
                        if (
                            aday.startswith((" ", "\t"))
                            and not _FRAME_RE.match(aday)
                            and aday.strip()
                            and set(aday.strip()) - set("~^ ")
                        ):
                            kaynak = aday
                    try:
                        son_oyun_karesi = (yol, int(kare.group("line")), kaynak)
                    except ValueError:
                        pass
                j += 1
                continue

            if not satir.strip() or satir.startswith((" ", "\t")):
                # Yığın izinin gövdesi (kaynak satırı, `~~~^^^` işaretleri).
                j += 1
                continue

            istisna = _EXC_RE.match(satir.strip())
            if istisna and son_oyun_karesi is not None:
                kayit = InitError(
                    file=son_oyun_karesi[0],
                    line=son_oyun_karesi[1],
                    exception=satir.strip(),
                    source=son_oyun_karesi[2],
                )
                if kayit.key not in gorulen:
                    gorulen.add(kayit.key)
                    bulunan.append(kayit)
            break

        i = j + 1

    return bulunan


def _collect_issues(project_root: Path, output: str) -> list[ParseIssue]:
    """Alt süreç çıktısını ve `errors.txt`i birleştirerek hataları toplar."""
    issues = parse_errors(output)

    hata_dosyasi = project_root / "errors.txt"
    if hata_dosyasi.is_file():
        try:
            metin = hata_dosyasi.read_text(encoding="utf-8", errors="replace")
        except OSError:
            metin = ""
        mevcut = {i.key for i in issues}
        for issue in parse_errors(metin):
            if issue.key not in mevcut:
                mevcut.add(issue.key)
                issues.append(issue)

    return issues


def _temizle(project_root: Path) -> None:
    """Ön denetimin bıraktığı geçici dosyaları siler."""
    for ad in ("errors.txt", "traceback.txt"):
        try:
            (project_root / ad).unlink()
        except OSError:
            pass


# --------------------------------------------------------------------------
# Düzeltme kuralları
# --------------------------------------------------------------------------


def _resolve(project_root: Path, reported: str) -> Optional[Path]:
    """
    Bildirilen yolu proje içindeki gerçek dosyaya çevirir.

    Ren'Py yolu proje köküne göre bildiriyor ("game/x.rpy"). Yine de
    mutlak yol gelme ihtimaline karşı ikisini de kabul ediyor, sonucun
    proje kökünün DIŞINA çıkmadığını doğruluyoruz — hata metni de
    sonuçta güvenilmez bir girdi.
    """
    temiz = reported.replace("\\", "/").strip()
    if not temiz:
        return None

    aday = Path(temiz)
    if not aday.is_absolute():
        aday = project_root / aday

    try:
        cozulmus = aday.resolve()
        kok = project_root.resolve()
    except OSError:
        return None

    if cozulmus != kok and kok not in cozulmus.parents:
        return None
    if cozulmus.suffix.lower() not in (".rpy", ".rpym"):
        return None
    if not cozulmus.is_file():
        return None
    return cozulmus


def _read_lines(path: Path) -> Optional[list[str]]:
    """
    Dosyayı satırlara böler.

    `surrogateescape` bilinçli: dokunmadığımız satırlar geri yazıldığında
    BAYT BAYT aynı kalsın istiyoruz. Bozuk kodlanmış bir bayt yüzünden
    oyunun başka bir yerini bozmak kabul edilemez.
    """
    try:
        ham = path.read_bytes()
    except OSError:
        return None
    return ham.decode("utf-8", "surrogateescape").splitlines(keepends=True)


def _write_lines(path: Path, lines: list[str]) -> bool:
    try:
        path.write_bytes("".join(lines).encode("utf-8", "surrogateescape"))
    except OSError:
        return False
    return True


def _drop_trailing_colon(lines: list[str], start: int) -> Optional[tuple[int, str]]:
    """Mantıksal satırın sonundaki `:` işaretini siler."""
    end = _logical_line_end(lines, start)
    satir = lines[end]
    ending = _line_ending(satir)
    govde = satir[: len(satir) - len(ending)]

    kod = _code_part(satir)
    if not kod.rstrip().endswith(":"):
        return None

    kesim = kod.rstrip()
    yeni_kod = kesim[:-1].rstrip()
    # Satırdaki yorum aynen korunuyor; araya en az bir boşluk konuyor ki
    # `scene x:  # not` -> `scene x  # not` olsun, `scene x# not` değil.
    kuyruk = govde[len(kod):]
    if kuyruk:
        bosluk = kod[len(kesim):] or " "
        yeni = yeni_kod + bosluk + kuyruk + ending
    else:
        yeni = yeni_kod + ending
    return end, yeni


def _insert_pass(lines: list[str], start: int) -> Optional[tuple[int, str]]:
    """Boş bloğun yerine `pass` satırı koyar."""
    end = _logical_line_end(lines, start)
    girinti = _indent(lines[start])
    ending = _line_ending(lines[end]) or "\n"
    if not lines[end].endswith(("\n", "\r\n")):
        # Dosya son satırında satır sonu yoksa önce onu tamamlıyoruz.
        lines[end] = lines[end] + "\n"
    return end + 1, girinti + "    pass" + ending


def _add_colon_above(
    lines: list[str], indented: int, keyword: str
) -> Optional[tuple[int, str]]:
    """
    Girintili satırın ÜSTÜNDEKİ ifadeye `:` ekler.

    Yalnızca üstteki ifade gerçekten `:` kabul eden bir ifadeyse ve
    girintisi bu satırdan azsa dokunuyoruz.
    """
    hedef = None
    for i in range(indented - 1, -1, -1):
        if _is_blank_or_comment(lines[i]):
            continue
        hedef = i
        break
    if hedef is None:
        return None

    start = _logical_line_start(lines, hedef)
    if len(_indent(lines[start])) >= len(_indent(lines[indented])):
        return None

    kod = _code_part(lines[start]).strip()
    if not re.match(rf"^{re.escape(keyword)}\b", kod):
        return None

    satir = lines[hedef]
    ending = _line_ending(satir)
    govde = satir[: len(satir) - len(ending)]
    hedef_kod = _code_part(satir)
    if hedef_kod.rstrip().endswith(":"):
        return None

    kuyruk = govde[len(hedef_kod):]
    yeni = hedef_kod.rstrip() + ":"
    if kuyruk:
        yeni += (hedef_kod[len(hedef_kod.rstrip()):] or " ") + kuyruk
    yeni += ending
    return hedef, yeni


def _plan_fix(
    lines: list[str], issue: ParseIssue
) -> Optional[tuple[str, int, str, bool]]:
    """
    Tek bir hata için düzeltme planlar.

    Döner: (kural adı, dizin, yeni satır, ekleme mi). Ekleme değilse
    o dizindeki satırın YERİNE yazılır.
    """
    idx = issue.line - 1
    if idx < 0 or idx >= len(lines):
        return None

    bos_blok = _EMPTY_BLOCK_RE.match(issue.message)
    if bos_blok:
        stmt = bos_blok.group("stmt").strip()
        if stmt in COLON_OPTIONAL:
            sonuc = _drop_trailing_colon(lines, idx)
            if sonuc is not None:
                return ("fazladan ':' silindi", sonuc[0], sonuc[1], False)
            return None
        if stmt in NO_SAFE_FIX:
            return None
        # Geri kalan her "blok zorunlu" ifadede boş bloğun karşılığı
        # `pass`. Doğruluğunu biz değil, bir sonraki ayrıştırma turunda
        # Ren'Py'nin kendisi onaylıyor.
        sonuc = _insert_pass(lines, idx)
        if sonuc is not None:
            return ("boş bloğa 'pass' eklendi", sonuc[0], sonuc[1], True)
        return None

    noblock = _NOBLOCK_RE.match(issue.message)
    if noblock:
        stmt = noblock.group("stmt").strip()
        # Mesaj "the preceding show statement statement" biçiminde geliyor;
        # yakalanan parça "show statement" oluyor.
        keyword = COLON_OPTIONAL.get(stmt)
        if keyword is None:
            return None
        sonuc = _add_colon_above(lines, idx, keyword)
        if sonuc is not None:
            return ("eksik ':' eklendi", sonuc[0], sonuc[1], False)
        return None

    return None


def apply_fixes(
    project_root: Path, issues: list[ParseIssue]
) -> tuple[list[Fix], dict[Path, bytes], set[tuple[str, int, str]]]:
    """
    Düzeltilebilen hataları uygular.

    Döner: (uygulanan düzeltmeler, dokunulan dosyaların ÖNCEKİ içeriği,
    düzeltmeye ÇALIŞILAN hataların kimlikleri). Önceki içerik geri alma
    için, kimlikler ise "denediğim düzeltme işe yaradı mı?" sorusunu
    hata SAYISINA değil hatanın KENDİSİNE bakarak yanıtlamak için lazım.
    """
    fixes: list[Fix] = []
    yedek: dict[Path, bytes] = {}
    denenen: set[tuple[str, int, str]] = set()

    # Aynı dosyadaki hataları birlikte, satır numarası BÜYÜKTEN KÜÇÜĞE
    # işliyoruz: `pass` eklemek alttaki satır numaralarını kaydırıyor,
    # tersten gidince kayma hiç oluşmuyor.
    gruplar: dict[Path, list[ParseIssue]] = {}
    for issue in issues:
        path = _resolve(project_root, issue.file)
        if path is None:
            continue
        gruplar.setdefault(path, []).append(issue)

    for path, grup in gruplar.items():
        lines = _read_lines(path)
        if lines is None:
            continue

        onceki = "".join(lines).encode("utf-8", "surrogateescape")
        dosya_fixes: list[Fix] = []
        dosya_denenen: set[tuple[str, int, str]] = set()

        for issue in sorted(grup, key=lambda i: i.line, reverse=True):
            plan = _plan_fix(lines, issue)
            if plan is None:
                continue
            kural, idx, yeni, ekleme = plan
            if ekleme:
                eski = ""
                lines.insert(idx, yeni)
            else:
                eski = lines[idx]
                lines[idx] = yeni
            dosya_fixes.append(
                Fix(
                    file=issue.file,
                    line=issue.line,
                    rule=kural,
                    before=eski if eski else "(boş blok)",
                    after=yeni,
                )
            )
            dosya_denenen.add(issue.key)

        if not dosya_fixes:
            continue

        if not _write_lines(path, lines):
            continue

        yedek[path] = onceki
        fixes.extend(dosya_fixes)
        denenen |= dosya_denenen

    return fixes, yedek, denenen


def source_context(
    project_root: Path, issue_file: str, line: int, span: int = 6
) -> list[str]:
    """
    Hatalı satırın çevresini döndürür ("  12 | kod" biçiminde).

    784 MB'lık bir ZIP'in içindeki 29. satırı görmek için kullanıcının
    dosyayı elle çıkarması gerekiyordu. Günlüğe koymak, hatayı doğrudan
    konuşulabilir hale getiriyor.
    """
    path = _resolve(project_root, issue_file)
    if path is None:
        return []
    lines = _read_lines(path)
    if lines is None:
        return []

    bas = max(0, line - 1 - span)
    son = min(len(lines), line + span)
    cikti: list[str] = []
    for i in range(bas, son):
        isaret = ">>" if i == line - 1 else "  "
        cikti.append(f"{isaret} {i + 1:5d} | {lines[i].rstrip()}")
    return cikti


def _drop_rpyc(path: Path) -> None:
    """
    Bir `.rpy` dosyasının yanındaki derlenmiş `.rpyc` kopyasını siler.

    İçeriğini değiştirdiğimiz her dosya için şart: Ren'Py, `.rpyc` kopyayı
    kaynaktan daha yeniyse doğrudan kullanabiliyor. Eski bir `.rpyc`
    kalırsa, geri aldığımız ya da düzelttiğimiz kaynak yerine onun
    derlenmiş hâli okunur ve sonuç öngörülemez olurdu. Silmek zararsız:
    kaynak duruyor, Ren'Py yeniden derler.
    """
    try:
        path.with_suffix(path.suffix + "c").unlink(missing_ok=True)
    except OSError:
        pass


def _geri_al(yedek: dict[Path, bytes]) -> None:
    for path, veri in yedek.items():
        try:
            path.write_bytes(veri)
        except OSError:
            continue
        _drop_rpyc(path)


# --------------------------------------------------------------------------
# Tüm hataların dökümü
# --------------------------------------------------------------------------


def _inventory(
    project_root: Path,
    ilk_issues: list[ParseIssue],
    denetle,
    sure_doldu,
    max_files: int = 40,
) -> tuple[list[ParseIssue], bool]:
    """
    Projedeki TÜM ayrıştırma hatalarını toplar.

    Neden ayrı bir taramaya ihtiyaç var: Ren'Py'nin script yükleyicisi ilk
    hatalı dosyadan sonra duruyor (`renpy/script.py` -> `load_script`,
    `if priority != last_priority: if has_parse_errors(): break`). Yani tek
    bir çalıştırma, yalnızca İLK hatalı dosyanın hatalarını gösteriyor.
    Kullanıcı ise "beş dosyayı beş turda öğrenmek" yerine hepsini bir arada
    görmek istiyor — haklı olarak.

    Yöntem: hatası bildirilen dosyaları GEÇİCİ olarak boşaltıp taramayı
    tekrarlıyoruz; böylece sıradaki hatalı dosya ortaya çıkıyor. Sonunda
    boşaltılan her dosya bayt bayt geri yazılıyor.

    Boşaltmak neden güvenli: ayrıştırma hataları dosya BAZINDA bulunuyor.
    Boş bir `.rpy` geçerlidir ve başka bir dosyada YENİ bir ayrıştırma
    hatası doğurmaz (eksik etiket/ekran gibi şeyler ayrıştırma değil,
    çalışma anı sorunudur ve burada hiç bakılmıyor). Yani bu yöntem en
    fazla bir hatayı GÖRMEMEYE yol açabilir, uydurmaya değil.

    Döner: (bulunan tüm hatalar, tarama yarıda mı kaldı).
    """
    bulunan: list[ParseIssue] = list(ilk_issues)
    gorulen = {i.key for i in ilk_issues}
    bosaltilan: dict[Path, bytes] = {}
    yarim = False
    yeni = ilk_issues

    try:
        for _ in range(max_files):
            # Bu turda hata bildiren dosyaları sustur.
            ilerleme = False
            for issue in yeni:
                path = _resolve(project_root, issue.file)
                if path is None or path in bosaltilan:
                    continue
                try:
                    bosaltilan[path] = path.read_bytes()
                    path.write_bytes(b"")
                except OSError:
                    bosaltilan.pop(path, None)
                    continue
                _drop_rpyc(path)
                ilerleme = True

            if not ilerleme:
                # Bildirilen hataların dosyası çözülemedi; devam etmenin
                # anlamı yok, sonsuz döngüye girerdik.
                break

            if sure_doldu():
                yarim = True
                break

            _code, issues = denetle()
            yeni = [i for i in issues if i.key not in gorulen]
            if not yeni:
                break
            for issue in yeni:
                gorulen.add(issue.key)
            bulunan.extend(yeni)
        else:
            yarim = True
    finally:
        # Ne olursa olsun her dosya geri yazılıyor.
        _geri_al(bosaltilan)

    bulunan.sort(key=lambda i: (i.file, i.line))
    return bulunan, yarim


# --------------------------------------------------------------------------
# Init hatalarını onarma denemesi
# --------------------------------------------------------------------------


def _try_init_repairs(
    project_root: Path,
    ilk_hatalar: list[InitError],
    denetle_init,
    sure_doldu,
    yedek: dict[Path, bytes],
    kayit,
    max_rounds: int = 6,
    max_attempts: int = 10,
) -> tuple[list[Fix], list[Fix], list[InitError]]:
    """
    Init hatalarını, Ren'Py'yi hakem tutarak onarmayı dener.

    Her tur: kalan ilk hatayı al, o satır için aday düzeltmeler üret,
    adayları TEK TEK uygulayıp script'i yeniden çalıştır. Hata
    kaybolduysa aday kalır; kaybolmadıysa satır geri alınır.

    `max_attempts` toplam Ren'Py çalıştırma sayısını sınırlar: her deneme
    büyük bir oyunda 5-15 saniye sürüyor ve sınırsız denemek, hızlı cevap
    vermek için eklenen bu adımı yavaş bir işkenceye çevirirdi.

    Döner: (kalıcı düzeltmeler, geri alınanlar, kalan hatalar).
    """
    fixes: list[Fix] = []
    geri_alinan: list[Fix] = []
    hatalar = list(ilk_hatalar)
    denemeler = 0

    for _tur in range(max_rounds):
        if not hatalar or denemeler >= max_attempts or sure_doldu():
            break

        hata = hatalar[0]
        path = _resolve(project_root, hata.file)
        if path is None:
            break

        lines = _read_lines(path)
        if lines is None or not (1 <= hata.line <= len(lines)):
            break

        idx = hata.line - 1
        ozgun_satir = lines[idx]
        adaylar = py23.candidates(ozgun_satir, hata.exception)
        if not adaylar:
            break

        yedek.setdefault(path, "".join(lines).encode("utf-8", "surrogateescape"))

        basarili: Optional[Fix] = None
        for aday in adaylar:
            if denemeler >= max_attempts or sure_doldu():
                break
            denemeler += 1

            ending = _line_ending(ozgun_satir)
            yeni_satir = aday.line.rstrip("\r\n") + ending
            lines[idx] = yeni_satir
            if not _write_lines(path, lines):
                lines[idx] = ozgun_satir
                break
            _drop_rpyc(path)

            kayit(f"    deneme {denemeler}: {aday.description}")
            yeni_hatalar = denetle_init()

            if hata.key not in {h.key for h in yeni_hatalar}:
                basarili = Fix(
                    file=hata.file,
                    line=hata.line,
                    rule=aday.description,
                    before=ozgun_satir,
                    after=yeni_satir,
                )
                hatalar = yeni_hatalar
                break

            # İşe yaramadı: satırı geri al ve bir sonraki adayı dene.
            lines[idx] = ozgun_satir
            _write_lines(path, lines)
            _drop_rpyc(path)
            geri_alinan.append(
                Fix(
                    file=hata.file,
                    line=hata.line,
                    rule=aday.description,
                    before=ozgun_satir,
                    after=yeni_satir,
                )
            )

        if basarili is None:
            break

        fixes.append(basarili)
        kayit(f"    onarıldı: {hata.file}:{hata.line} ({basarili.rule})")

    return fixes, geri_alinan, hatalar


# --------------------------------------------------------------------------
# Ana döngü
# --------------------------------------------------------------------------


def repair(
    sdk_root: Path,
    project_root: Path,
    max_rounds: int = 12,
    timeout: int = 420,
    budget: int = 900,
    logger=None,
) -> RepairResult:
    """
    Söz dizimi ön denetimi + otomatik onarım döngüsü.

    Döngünün hakemi Ren'Py'nin kendisi: her düzeltmeden sonra script
    yeniden ayrıştırılıyor.

    TEMEL GÜVENCE: sonuç ya "Ren'Py projeyi tertemiz ayrıştırıyor"dur, ya
    da HİÇBİR DOSYAYA DOKUNULMAMIŞTIR. Yarım kalmış bir onarım bırakmak,
    kullanıcının kendi dosyasını tanıyamaz hâle getirirdi; hem de derleme
    zaten duracağı için hiçbir faydası olmazdı.

    `max_rounds` neden yüksek: Ren'Py'nin script yükleyicisi ilk hatalı
    dosyadan sonra duruyor (`renpy/script.py`, `load_script` içindeki
    `if priority != last_priority: if has_parse_errors(): break`), yani
    hatalar dosya dosya ortaya çıkıyor. Beş dosyada hata varsa beş tur
    gerekiyor. `budget` toplam saniye sınırıdır; aşılırsa döngü durur.
    """
    res = RepairResult()

    if _kapali(os.environ.get(_ORTAM_KAPATMA, "")):
        res.note = f"{_ORTAM_KAPATMA}=0 verildiği için söz dizimi ön denetimi atlandı."
        res.inconclusive = True
        return res

    adaylar = renpy_binaries(sdk_root)
    if not adaylar:
        res.note = f"Ren'Py yorumlayıcısı bulunamadı ({sdk_root}); ön denetim atlandı."
        res.inconclusive = True
        return res
    renpy_bin = adaylar[0]

    def kayit(mesaj: str) -> None:
        if logger is not None:
            logger(mesaj)

    res.ran = True
    baslangic = time.monotonic()

    # Son çalıştırmanın ham çıktısı; init hatalarını ondan çıkarıyoruz.
    son_cikti = ""

    def denetle() -> tuple[Optional[int], list[ParseIssue]]:
        nonlocal son_cikti, renpy_bin
        # Her denetimden ÖNCE de temizliyoruz: kullanıcının paketinde
        # kendi makinesinden kalma eski bir `errors.txt` olabilir ve onu
        # okumak, var olmayan hataları bildirmek olurdu.
        _temizle(project_root)
        code, output, detail = run_check(
            renpy_bin, project_root, timeout=timeout,
            restored_out=res.orphan_restored,
        )

        # İkili bu makinenin mimarisine uymuyorsa sıradaki adayı dene.
        # (renutil kurulumunda hem aarch64 hem x86_64 bulunabiliyor.)
        while detail.startswith(_ENOEXEC_MARK) and renpy_bin in adaylar:
            sonraki = adaylar.index(renpy_bin) + 1
            if sonraki >= len(adaylar):
                break
            renpy_bin = adaylar[sonraki]
            code, output, detail = run_check(
                renpy_bin, project_root, timeout=timeout,
                restored_out=res.orphan_restored,
            )

        son_cikti = output
        res.failure_detail = detail
        issues = _collect_issues(project_root, output)
        _temizle(project_root)
        res.seconds = time.monotonic() - baslangic
        return code, issues

    code, issues = denetle()

    if not issues:
        # Söz dizimi hatası yok. Peki oyunun INIT kodu çalışıyor mu?
        # Çalışmıyorsa derleme kesin başarısız olacak (gerekçe: InitError).
        init_hatalari = parse_init_errors(son_cikti)
        if init_hatalari and _init_denetimi_acik():
            # Mekanik olarak düzeltilebilecek Python 2 -> 3 satırlarını
            # dene. Hakem yine Ren'Py: her deneme yeniden çalıştırılıyor.
            init_yedek: dict[Path, bytes] = {}

            def denetle_init() -> list[InitError]:
                denetle()
                return parse_init_errors(son_cikti)

            kayit(
                f"  {len(init_hatalari)} başlangıç (init) hatası bulundu; "
                "mekanik olarak düzeltilebilenler deneniyor…"
            )
            yeni_fixes, geri_alinan, kalan = _try_init_repairs(
                project_root,
                init_hatalari,
                denetle_init,
                lambda: (time.monotonic() - baslangic) > budget,
                init_yedek,
                kayit,
            )
            res.init_reverted = geri_alinan

            if not kalan:
                res.init_fixes = yeni_fixes
                res.ok = True
                return res

            # Tamamen temizlenemedi: söz dizimi onarımındaki güvencenin
            # aynısı geçerli — ya hepsi ya hiçbiri.
            res.init_fixes = yeni_fixes
            if init_yedek:
                _geri_al(init_yedek)
                res.init_rolled_back = bool(yeni_fixes)
            res.init_errors = kalan
            return res

        if code == 0:
            res.ok = True
            return res

        # Ne ayrıştırma hatası var ne de tanıyabildiğimiz bir init hatası.
        # Bu bizim alanımız değil: derlemeyi ENGELLEMİYORUZ. Bizim adımımız
        # yüzünden, çalışabilecek bir derleme durdurulmamalı.
        res.inconclusive = True
        if code is None:
            res.note = (
                "Ön denetim tamamlanamadı. Derlemeye normal şekilde devam "
                "ediliyor.\n  Ayrıntı: " + (res.failure_detail or "bilinmiyor")
            )
        else:
            res.note = (
                f"Ön denetim sıfır dışında bir kodla bitti ({code}) ama "
                "çıktıda söz dizimi hatası yok. Sebep başka bir yerde; "
                "derlemeye normal şekilde devam ediliyor."
            )
        return res

    # --- Tüm hataların dökümü --------------------------------------------
    # Ren'Py tek çalıştırmada yalnızca ilk hatalı dosyayı gösteriyor.
    # Onarıma girişmeden önce projenin TAMAMINI tarayıp bütün hataları
    # çıkarıyoruz; hem kullanıcı hepsini bir arada görsün hem de hepsini
    # TEK turda düzeltmeyi deneyebilelim.
    kayit("  Hatalar bulundu; projenin tamamı taranıyor (tüm hatalar için)…")

    def sure_doldu() -> bool:
        return (time.monotonic() - baslangic) > budget

    issues, yarim = _inventory(project_root, issues, denetle, sure_doldu)
    res.all_issues = list(issues)
    res.inventory_partial = yarim

    dosya_sayisi = len({i.file for i in issues})
    kayit(
        f"  Tarama bitti: {len(issues)} söz dizimi hatası, "
        f"{dosya_sayisi} dosyada."
        + (" (tarama yarıda kaldı, daha fazlası olabilir)" if yarim else "")
    )

    # Dokunduğumuz her dosyanın EN İLK hâli. Sonuç temiz çıkmazsa hepsi
    # buradan geri yazılıyor.
    tum_yedek: dict[Path, bytes] = {}

    for tur in range(1, max_rounds + 1):
        res.rounds = tur
        kayit(
            f"  Tur {tur}: {len(issues)} söz dizimi hatası var, "
            "mekanik olarak düzeltilebilenler onarılıyor…"
        )

        fixes, yedek, denenen = apply_fixes(project_root, issues)
        for yol, veri in yedek.items():
            tum_yedek.setdefault(yol, veri)

        if not fixes:
            # Kalan hataların hiçbirinin güvenli bir karşılığı yok.
            res.remaining = issues
            break

        # Uygulanan düzeltmeyi Ren'Py'ye onaylatıyoruz: hakem biz değiliz,
        # oyunun gerçekten derleneceği yorumlayıcının kendisi.
        yeni_code, yeni_issues = denetle()

        if yeni_code == 0 and not yeni_issues:
            res.fixes.extend(fixes)
            res.ok = True
            return res

        res.fixes.extend(fixes)
        yeni_anahtarlar = {i.key for i in yeni_issues}

        if denenen & yeni_anahtarlar:
            # Düzeltmeye ÇALIŞTIĞIMIZ hata hâlâ duruyor: denediğimiz yol
            # işe yaramamış. Hata SAYISINA bakmak yanıltıcı olurdu, çünkü
            # Ren'Py hataları dosya dosya açığa çıkarıyor.
            res.remaining = yeni_issues
            res.note = (
                "Denenen otomatik düzeltme hatayı gidermedi; bu yüzden "
                "hiçbir dosya değiştirilmiş olarak bırakılmadı."
            )
            break

        issues = yeni_issues

        if time.monotonic() - baslangic > budget:
            res.remaining = issues
            res.note = (
                "Söz dizimi onarımı için ayrılan süre doldu; dosyalar "
                "eski hâline geri alındı."
            )
            break
    else:
        res.remaining = issues

    # Buraya düşmek "temiz sonuca ulaşılamadı" demek. Güvence gereği her
    # şeyi geri alıyoruz: kullanıcı kendi dosyasını bulduğu gibi bulsun.
    if tum_yedek:
        _geri_al(tum_yedek)
        res.reverted = res.fixes
        res.fixes = []
    return res
