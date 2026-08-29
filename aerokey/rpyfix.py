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

import os
import re
import subprocess
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

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

_ORTAM_KAPATMA = "AEROKEY_SYNTAX_FIX"


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
    # Denenmiş ama sonuç temiz çıkmadığı için GERİ ALINMIŞ düzeltmeler.
    # Kullanıcıya "şunları otomatik halledebiliyordum ama şu satır elde
    # kaldığı için hepsini geri aldım" diyebilmek için tutuluyor.
    reverted: list[Fix] = field(default_factory=list)
    note: str = ""
    # Ayrıştırma hatası DIŞINDA bir sebeple çalışmadıysa burası dolar; bu
    # durumda derlemeyi ASLA durdurmuyoruz (bizim adımımız yüzünden
    # çalışabilecek bir derleme engellenmemeli).
    inconclusive: bool = False


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


def find_renpy_binary(sdk_root: Path) -> Optional[Path]:
    """
    SDK içindeki yerel Ren'Py yorumlayıcısını bulur.

    `renpy.sh`, `lib/py3-<platform>/renpy` ikilisini exec ediyor ve ikili
    kendi konumundan SDK kökünü çıkarıyor; yani ikiliyi doğrudan çağırmak
    kabuk betiğini çağırmakla aynı şey. `uname` farklarına takılmamak için
    önce ikiliyi arıyoruz.
    """
    if not sdk_root.is_dir():
        return None

    lib = sdk_root / "lib"
    if lib.is_dir():
        for kalip in ("py3-linux-*", "linux-*"):
            for aday in sorted(lib.glob(kalip)):
                exe = aday / "renpy"
                if exe.is_file() and os.access(exe, os.X_OK):
                    return exe

    sh = sdk_root / "renpy.sh"
    if sh.is_file() and os.access(sh, os.X_OK):
        return sh

    return None


def run_check(
    renpy_bin: Path,
    project_root: Path,
    timeout: int = 900,
) -> tuple[Optional[int], str]:
    """
    `renpy <proje> compile` çalıştırır.

    `compile` komutu (renpy/arguments.py -> compile) `False` döndürüyor,
    yani oyun BAŞLATILMIYOR: yalnızca script yükleniyor, init kodu
    çalışıyor ve süreç kapanıyor. Ekran gerekmiyor — DISPLAY olmadan
    da çalıştığı ölçüldü.

    Döner: (çıkış kodu ya da None [zaman aşımı/çalıştırılamadı], çıktı).
    """
    cmd = [str(renpy_bin), str(project_root), "compile"]
    try:
        proc = subprocess.run(
            cmd,
            cwd=str(project_root),
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            timeout=timeout,
            check=False,
        )
    except subprocess.TimeoutExpired as exc:
        cikti = exc.output or b""
        if isinstance(cikti, str):
            cikti = cikti.encode("utf-8", "replace")
        return None, cikti.decode("utf-8", "replace")
    except OSError as exc:
        return None, f"[rpyfix] Ren'Py çalıştırılamadı: {exc}"

    return proc.returncode, proc.stdout.decode("utf-8", "replace")


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


def _geri_al(yedek: dict[Path, bytes]) -> None:
    for path, veri in yedek.items():
        try:
            path.write_bytes(veri)
        except OSError:
            pass


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

    kapali = os.environ.get(_ORTAM_KAPATMA, "").strip().lower()
    if kapali in ("0", "false", "off", "hayir"):
        res.note = f"{_ORTAM_KAPATMA}=0 verildiği için söz dizimi ön denetimi atlandı."
        res.inconclusive = True
        return res

    renpy_bin = find_renpy_binary(sdk_root)
    if renpy_bin is None:
        res.note = f"Ren'Py yorumlayıcısı bulunamadı ({sdk_root}); ön denetim atlandı."
        res.inconclusive = True
        return res

    def kayit(mesaj: str) -> None:
        if logger is not None:
            logger(mesaj)

    res.ran = True
    baslangic = time.monotonic()

    def denetle() -> tuple[Optional[int], list[ParseIssue]]:
        # Her denetimden ÖNCE de temizliyoruz: kullanıcının paketinde
        # kendi makinesinden kalma eski bir `errors.txt` olabilir ve onu
        # okumak, var olmayan hataları bildirmek olurdu.
        _temizle(project_root)
        code, output = run_check(renpy_bin, project_root, timeout=timeout)
        issues = _collect_issues(project_root, output)
        _temizle(project_root)
        res.seconds = time.monotonic() - baslangic
        return code, issues

    code, issues = denetle()

    if code == 0 and not issues:
        res.ok = True
        return res

    if not issues:
        # Ayrıştırma hatası yok ama süreç yine de başarısız (ya da zaman
        # aşımı). Bu bizim alanımız değil: derlemeyi ENGELLEMİYORUZ. Bizim
        # adımımız yüzünden, çalışabilecek bir derleme durdurulmamalı.
        res.inconclusive = True
        if code is None:
            res.note = (
                "Ön denetim tamamlanamadı (zaman aşımı ya da süreç "
                "çalıştırılamadı). Derlemeye normal şekilde devam ediliyor."
            )
        else:
            res.note = (
                f"Ön denetim sıfır dışında bir kodla bitti ({code}) ama "
                "çıktıda söz dizimi hatası yok. Sebep başka bir yerde; "
                "derlemeye normal şekilde devam ediliyor."
            )
        return res

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
