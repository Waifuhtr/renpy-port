"""
Oyunun saf-Python eklenti klasörlerini Android'de çalışır hâle getirir.

SORUN
-----
Bazı oyunlar saf Python kütüphanelerini ayrı bir klasörde tutar ve o klasörü
kendi elleriyle `sys.path`'e ekleyip düz `import X` ile çağırır:

    game/01_settings/preset/lovense.rpy:
        init -1 python:
            import sys
            sys.path.append(config.gamedir + "/lovenseplugin")
            import LovenseRemoteSDK

    game/lovenseplugin/LovenseRemoteSDK.py

Masaüstünde bu çalışır: `config.gamedir` GERÇEK bir klasördür, `sys.path`'e
eklenince Python'un standart dosya-sistemi bulucusu modülü orada bulur.

Android'de ÇALIŞMAZ. Orada oyun dosyaları APK'nın içindedir; `config.gamedir`
diye gezilebilir bir klasör yoktur. `sys.path.append(...)` sessizce hiçbir işe
yaramaz (var olmayan bir yol eklemek hata vermez, sadece etkisizdir) ve
`import LovenseRemoteSDK` Ren'Py'nin kendi bulucusuna düşer:

    renpy/importer.py -> RenpyImporter._get_module_info(fullname)

O bulucu modülleri YALNIZCA `renpy.loader.game_files` içinde, kayıtlı
"önek"lerin altında arar. `init_importer()` açılışta iki önek kaydeder:

    add_python_directory("python-packages/")
    add_python_directory("")

Yani Android'de düz `import LovenseRemoteSDK` ancak dosya
`game/python-packages/LovenseRemoteSDK.py` ya da `game/LovenseRemoteSDK.py`
ise bulunur. Dosya `game/lovenseplugin/` altındayken modülün bulucudaki
anahtarı `lovenseplugin.LovenseRemoteSDK` olur ve düz ad eşleşmez ->
ModuleNotFoundError.

ÇÖZÜM: İKİ BAĞIMSIZ AĞ
----------------------
Yalnızca GEÇİCİ ÇALIŞMA KOPYASINDA; oyunun kendi dosyalarına dokunulmaz.

  AĞ 1 (asıl güvence) — modülleri `game/python-packages/` içine KOPYALA.
      Bu önek Ren'Py'nin kendisi tarafından, `init_importer()` içinde,
      bootstrap sırasında kaydedilir. Yani HİÇBİR init bloğuna, hiçbir
      sıralamaya, bizim ürettiğimiz hiçbir betiğin paketlenmiş olmasına
      bağlı değildir. Oyunun kendi `build.classify` kuralları `.rpy`
      kaynaklarını atsa bile bu yol ayakta kalır.

  AĞ 2 (ikinci katman) — `init -500` önceliğinde tek bir betik üretip
      klasörü Ren'Py'nin BELGELENMİŞ API'siyle modül yoluna ekle:

          renpy.add_python_directory("lovenseplugin")

      Ad çakışması yüzünden AĞ 1'in atladığı modülleri ve
      `import lovenseplugin.X` biçimindeki alt-modül çağrılarını kurtarır.

NEREYE BAKILIR
--------------
Eklenti klasörü üç şekilde bulunur:

  1. `sys.path` İPUCU — script'lerde `sys.path.append(config.gamedir + "/X")`
     benzeri satırlar aranır. Oyunun niyetini doğrudan söyleyen en güçlü
     kanıt budur; ad tahminine gerek kalmaz.
  2. `game/` İÇİ — `game/` altındaki (en fazla iki seviye) klasörlerden
     içinde `.py` olanlar.
  3. PROJE KÖKÜ — `game/` ile aynı seviyedeki klasörler. Bunlar Ren'Py'nin
     varlık taramasına hiç girmediği için önce `game/` içine TAŞINIR.

2 ve 3 için ek bir koşul var: oyun script'lerinde o modüllerin gerçekten
`import` edildiğine dair iz olmalı. Böylece alakasız klasörler (docs/ gibi)
boşuna kopyalanmaz. 1. yoldan gelenlerde bu koşul aranmaz — oyun klasörü
zaten açıkça `sys.path`'e eklemiş, niyet ortadadır.
"""

from __future__ import annotations

import re
import shutil
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable, Optional

# RPYC çözücüsü build_dump ile ORTAK: aynı biçimi üçüncü kez yazmak yerine
# tek bir uygulamayı paylaşıyoruz.
from .build_dump import _rpyc_payloads as rpyc_payloads

# Üretilen betiğin adı. "aaa" öneki, dosyanın diğer betiklerden ÖNCE
# yüklenmesini sağlar. Asıl güvence init önceliğidir (aşağıda), bu yalnızca
# ikinci bir katman.
GENERATED_SCRIPT = "aaa_aerokey_pymodules.rpy"

# Modül yolunu kaydeden init bloğunun önceliği. Oyunun kendi `init python`
# blokları varsayılan olarak 0 önceliktedir; incelediğimiz gerçek oyunda ise
# `init -1` kullanılıyordu. -500 ikisinden de küçüktür, yani bizim kaydımız
# ONLARDAN ÖNCE çalışır (renpy/script.py: initcode önceliğe göre sıralanır).
INIT_PRIORITY = -500

# Ren'Py'nin AÇILIŞTA kendi kaydettiği önek (renpy/importer.py,
# init_importer). Buraya kopyalanan bir modül hiçbir init koduna gerek
# kalmadan düz adıyla import edilebilir.
PACKAGES_DIR = "python-packages"

# Kökte durup da ASLA Python eklentisi olmayan klasörler.
_SKIP_ROOT_DIRS = {
    "game", "renpy", "lib", "cache", "saves", "tl", "update",
    "old-game", "base", "archived", "python-packages",
}

# game/ altında eklenti aramanın anlamsız olduğu klasörler. Buradaki amaç
# hem yanlış pozitifi önlemek hem de büyük varlık ağaçlarını boşuna
# taramamaktır.
_SKIP_GAME_DIRS = {
    "cache", "saves", "tl", "python-packages", "images", "image",
    "audio", "sounds", "sound", "music", "bgm", "se", "voice", "voices",
    "video", "videos", "movies", "movie", "gui", "fonts", "font",
}

# game/ altında kaç seviye derine inileceği. 1 = game/X, 2 = game/X/Y.
_MAX_GAME_DEPTH = 2

# Standart kütüphane adları. Bir eklenti modülü bunlardan biriyle aynı adı
# taşıyorsa python-packages/ içine KOPYALANMAZ: RenpyImporter sys.meta_path'in
# BAŞINDA durur, yani böyle bir kopya standart kütüphaneyi gölgeler ve
# oyunun tamamını bozabilirdi.
_STDLIB_NAMES = frozenset(getattr(sys, "stdlib_module_names", ()))


@dataclass
class PyPackage:
    """İşlenen tek bir Python eklenti klasörü."""

    name: str
    "Klasörün adı (ör. 'lovenseplugin')."

    rel: str
    "game/ içine göre göreli yolu (ör. 'lovenseplugin' ya da 'libs/lovense')."

    origin: str
    "Nasıl bulundu: 'sys.path', 'game' ya da 'kök'."

    modules: list[str] = field(default_factory=list)
    "Düz `import X` ile çağrılabilecek modül adları."

    imported: list[str] = field(default_factory=list)
    "Script'lerde gerçekten import edildiği görülenler."

    exported: list[str] = field(default_factory=list)
    "python-packages/ içine kopyalanabilen modül adları (AĞ 1)."

    moved: bool = False
    "Proje kökünden game/ içine taşındı mı."

    files: int = 0
    "Klasördeki dosya sayısı."

    notes: list[str] = field(default_factory=list)
    "Bu pakete özgü uyarılar."


@dataclass
class PyModuleResult:
    """
    İşlemin sonucu.

    `candidates`: eklenti klasörü olarak GÖRÜLEN her şeyin adı — işlensin
    işlenmesin. Bu alan, "hiçbir şey yoktu" ile "aday vardı ama bir yerde
    durduk" durumlarını ayırt etmek için var: ikisi de `packages` boş
    bırakır, ama biri teşhis edilebilir, öteki normal.
    """

    packages: list[PyPackage] = field(default_factory=list)
    script: Optional[Path] = None
    skipped: list[str] = field(default_factory=list)
    candidates: list[str] = field(default_factory=list)
    note: str = ""

    @property
    def ok(self) -> bool:
        return bool(self.packages)

    @property
    def moved(self) -> list[PyPackage]:
        """Proje kökünden game/ içine fiilen taşınanlar."""
        return [p for p in self.packages if p.moved]


# --------------------------------------------------------------------------
# Script metnini toplama
# --------------------------------------------------------------------------

def _script_text(game_dir: Path) -> str:
    """
    Oyunun tüm script metni (.rpy ve derlenmiş .rpyc birlikte).

    .rpyc'yi de taramak ZORUNLU: derlenmiş bir dağıtım paketinde `.rpy`
    kaynakları hiç bulunmaz, oysa `import` satırları derlenmiş kodda düz
    metin olarak durur (renpy/ast.py: PyCode.source pickle'da saklanır).
    """
    parts: list[str] = []

    for path in game_dir.rglob("*"):
        suffix = path.suffix.lower()
        if suffix not in (".rpy", ".rpym", ".rpyc", ".rpymc"):
            continue
        try:
            data = path.read_bytes()
        except OSError:
            continue

        if suffix in (".rpy", ".rpym"):
            parts.append(data.decode("utf-8", "ignore"))
        else:
            for blob in rpyc_payloads(data):
                parts.append(blob.decode("utf-8", "ignore"))

    return "\n".join(parts)


# --------------------------------------------------------------------------
# Aday klasörleri bulma
# --------------------------------------------------------------------------

# sys.path.append(...) / sys.path.insert(...) çağrısının parantez içi.
# İç içe tek bir parantez seviyesine (os.path.join(...)) izin verir.
_SYS_PATH_CALL = re.compile(
    r"sys\s*\.\s*path\s*\.\s*(?:append|insert)\s*"
    r"\(((?:[^()]|\([^()]*\))*)\)"
)

# Çağrının içindeki metin sabitleri.
_STRING_LITERAL = re.compile(r"""['"]([^'"\n]*)['"]""")


def _sys_path_hints(text: str) -> list[tuple[str, str]]:
    """
    Script'lerdeki `sys.path.append(...)` satırlarından klasör ipuçları.

    Döndürülen her öğe (taban, göreli_yol) çiftidir; taban "game" ya da
    "kök"tür. Oyunun kendi niyetini doğrudan okuduğumuz için burada ad
    tahmini yapmıyoruz.
    """
    hints: list[tuple[str, str]] = []
    seen: set[tuple[str, str]] = set()

    for match in _SYS_PATH_CALL.finditer(text):
        args = match.group(1)

        # config.gamedir / renpy.config.gamedir -> game/ tabanlı
        # config.basedir / renpy.config.basedir -> proje kökü tabanlı
        if "gamedir" in args:
            taban = "game"
        elif "basedir" in args:
            taban = "kök"
        else:
            # Tabanı belli değil (ör. mutlak yol ya da değişken). Böyle bir
            # ipucundan klasör adı çıkarmak tahmin olurdu; atlıyoruz.
            continue

        for raw in _STRING_LITERAL.findall(args):
            rel = raw.replace("\\", "/").strip("/").strip()
            if not rel or rel in (".", ".."):
                continue
            # ".." içeren yollar proje dışına çıkabilir; kabul etmiyoruz.
            if ".." in rel.split("/"):
                continue
            key = (taban, rel)
            if key not in seen:
                seen.add(key)
                hints.append(key)

    return hints


def _has_py(directory: Path) -> bool:
    try:
        for path in directory.iterdir():
            if path.is_file() and path.suffix == ".py":
                return True
    except OSError:
        return False
    return False


def _root_candidate_dirs(project_root: Path) -> Iterable[Path]:
    """Proje kökünde duran, içinde .py bulunan aday klasörler."""
    try:
        children = sorted(project_root.iterdir())
    except OSError:
        return

    for child in children:
        try:
            if not child.is_dir() or child.is_symlink():
                continue
        except OSError:
            continue
        if child.name.lower() in _SKIP_ROOT_DIRS or child.name.startswith("."):
            continue
        if not _has_py(child):
            continue
        yield child


def _game_candidate_dirs(game_dir: Path) -> Iterable[Path]:
    """game/ altında (en fazla _MAX_GAME_DEPTH seviye) aday klasörler."""

    def walk(directory: Path, depth: int) -> Iterable[Path]:
        if depth > _MAX_GAME_DEPTH:
            return
        try:
            children = sorted(directory.iterdir())
        except OSError:
            return

        for child in children:
            try:
                if not child.is_dir() or child.is_symlink():
                    continue
            except OSError:
                continue
            if child.name.startswith(".") or child.name.lower() in _SKIP_GAME_DIRS:
                continue
            if _has_py(child):
                yield child
            yield from walk(child, depth + 1)

    yield from walk(game_dir, 1)


def _module_names(directory: Path) -> list[str]:
    """
    Bir klasörün düz `import X` ile çağrılabilecek modül adları.

    `_ren.py` dosyaları dışarıda: onlar Ren'Py'nin "Python-in-rpy" biçimidir
    ve importer da onları zaten atlar (renpy/importer.py: _cache_entries).
    """
    names = []
    for path in sorted(directory.glob("*.py")):
        if path.name.endswith("_ren.py"):
            continue
        if path.stem == "__init__":
            # __init__.py varsa klasörün KENDİSİ bir paket adıdır.
            names.append(directory.name)
            continue
        names.append(path.stem)
    return names


def _imported_names(text: str, candidates: list[str]) -> list[str]:
    """`text` içinde gerçekten import edilen modül adları."""
    found = []
    for name in candidates:
        esc = re.escape(name)
        pattern = re.compile(
            r"(?:^|\W)(?:import\s+" + esc + r"\b|from\s+" + esc + r"[\s.])",
        )
        if pattern.search(text):
            found.append(name)
    return found


# --------------------------------------------------------------------------
# AĞ 1: python-packages/ içine kopyalama
# --------------------------------------------------------------------------

def _ignore_pycache(_dir: str, names: list[str]) -> set[str]:
    return {n for n in names if n == "__pycache__" or n.endswith(".pyc")}


def _export_to_packages(paket: PyPackage, source: Path, game_dir: Path) -> None:
    """
    Eklentinin modüllerini `game/python-packages/` içine kopyalar.

    Bu, Ren'Py'nin açılışta kendi kaydettiği önektir; buraya konan bir modül
    hiçbir init koduna gerek kalmadan düz adıyla import edilir. Kopyalama
    başarısız olursa sessizce geçilir — AĞ 2 hâlâ devrededir.
    """
    hedef_kok = game_dir / PACKAGES_DIR

    if hedef_kok.exists() and not hedef_kok.is_dir():
        paket.notes.append(
            f"game/{PACKAGES_DIR} bir klasör değil, kopyalama atlandı"
        )
        return

    try:
        hedef_kok.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        paket.notes.append(f"game/{PACKAGES_DIR} oluşturulamadı: {exc}")
        return

    # __init__.py varsa bu gerçek bir pakettir: klasörün tamamı, adıyla
    # birlikte kopyalanır.
    if (source / "__init__.py").is_file():
        hedef = hedef_kok / paket.name
        if hedef.exists():
            paket.notes.append(
                f"game/{PACKAGES_DIR}/{paket.name} zaten var, üzerine yazılmadı"
            )
            return
        try:
            shutil.copytree(source, hedef, ignore=_ignore_pycache)
        except (OSError, shutil.Error) as exc:
            paket.notes.append(f"{paket.name} kopyalanamadı: {exc}")
            return
        paket.exported.append(paket.name)
        return

    # Düz klasör: her modül tek tek kopyalanır.
    for path in sorted(source.glob("*.py")):
        if path.name.endswith("_ren.py"):
            continue

        ad = path.stem

        if ad in _STDLIB_NAMES:
            # RenpyImporter sys.meta_path'in BAŞINDADIR; buraya konan
            # `json.py` gerçek `json` modülünü gölgeler ve oyunun tamamını
            # bozardı. AĞ 2 bu modülü yine de kurtarır.
            paket.notes.append(
                f"{ad}: standart kütüphaneyle aynı ad, python-packages'a alınmadı"
            )
            continue

        if (game_dir / f"{ad}.py").is_file():
            # game/ kökünde aynı adda bir modül var. Ren'Py önce
            # python-packages/ önekine bakar, yani kopyamız oyunun kendi
            # modülünü gölgelerdi.
            paket.notes.append(
                f"{ad}: game/{ad}.py zaten var, python-packages'a alınmadı"
            )
            continue

        hedef = hedef_kok / path.name
        if hedef.exists():
            try:
                ayni = hedef.read_bytes() == path.read_bytes()
            except OSError:
                ayni = False
            if ayni:
                # Aynı dosya (ör. yeniden derleme): sorun yok.
                paket.exported.append(ad)
            else:
                paket.notes.append(
                    f"{ad}: python-packages içinde farklı bir dosya var, "
                    "üzerine yazılmadı"
                )
            continue

        try:
            shutil.copy2(path, hedef)
        except (OSError, shutil.Error) as exc:
            paket.notes.append(f"{ad} kopyalanamadı: {exc}")
            continue

        paket.exported.append(ad)


# --------------------------------------------------------------------------
# AĞ 2: üretilen betik
# --------------------------------------------------------------------------

def _render_script(packages: list[PyPackage]) -> str:
    """Modül yolunu kaydeden betiği üretir."""
    lines = [
        "# Bu dosya Ren'Py Android Paketleyici tarafından ÜRETİLMİŞTİR.",
        "#",
        "# Oyun, ayrı bir klasördeki saf-Python modüllerini düz `import X`",
        "# ile çağırıyor ve o klasörü kendi elleriyle sys.path'e ekliyor.",
        "# Masaüstünde bu çalışır; Android'de oyun dosyaları APK'nın içinde",
        "# olduğu için gezilebilir bir klasör yoktur ve sys.path'e eklenen",
        "# yol hiçbir işe yaramaz.",
        "#",
        "# Aşağıda aynı klasör, Ren'Py'nin BELGELENMİŞ API'siyle modül arama",
        "# yoluna ekleniyor. Oyunun kendi dosyalarına DOKUNULMADI.",
        "#",
        "# Not: modüller ayrıca game/python-packages/ içine de kopyalandı.",
        "# Orası Ren'Py'nin açılışta kendi kaydettiği önektir, yani asıl",
        "# güvence odur; buradaki kayıt ikinci bir katmandır.",
        "#",
        f"# init {INIT_PRIORITY}: oyunun kendi `init python` blokları 0 (bazen",
        "# -1) önceliktedir; bu değer kaydın onlardan ÖNCE çalışmasını",
        "# garantiler.",
        "",
        f"init {INIT_PRIORITY} python:",
    ]
    for paket in packages:
        escaped = paket.rel.replace("\\", "\\\\").replace('"', '\\"')
        lines.append(f'    renpy.add_python_directory("{escaped}")')
    return "\n".join(lines) + "\n"


# --------------------------------------------------------------------------
# Ana giriş noktası
# --------------------------------------------------------------------------

def prepare_python_packages(project_root: Path) -> PyModuleResult:
    """
    Oyunun saf-Python eklenti klasörlerini Android'de import edilebilir hâle
    getirir.

    ASLA hata fırlatmaz: bir sorun çıkarsa hiçbir şey yapılmaz ve derleme
    eskisi gibi sürer. En kötü ihtimalle "hiçbir şey yapmamış" oluruz.
    """
    game_dir = project_root / "game"
    if not game_dir.is_dir():
        return PyModuleResult(note="game/ klasörü yok.")

    try:
        metin = _script_text(game_dir)
    except Exception as exc:  # noqa: BLE001
        return PyModuleResult(note=f"Oyun script'leri taranırken hata: {exc}")

    # --- Adayları topla ---------------------------------------------------
    # (yol, köken, ipucundan_mi) üçlüleri. Aynı klasör birden çok yoldan
    # bulunabilir; ilk (en güçlü) köken kazanır.
    adaylar: list[tuple[Path, str, bool]] = []
    gorulen_yollar: set[Path] = set()

    def ekle(yol: Path, koken: str, ipucu: bool) -> None:
        try:
            anahtar = yol.resolve()
        except OSError:
            anahtar = yol
        if anahtar in gorulen_yollar:
            return
        gorulen_yollar.add(anahtar)
        adaylar.append((yol, koken, ipucu))

    # 1. sys.path ipuçları — oyunun niyetini doğrudan söyleyen en güçlü kanıt.
    for taban, rel in _sys_path_hints(metin):
        yol = (game_dir if taban == "game" else project_root) / rel
        try:
            if yol.is_dir() and _has_py(yol):
                ekle(yol, "sys.path", True)
        except OSError:
            continue

    # 2. game/ içindeki klasörler.
    try:
        for yol in _game_candidate_dirs(game_dir):
            ekle(yol, "game", False)
    except OSError as exc:
        return PyModuleResult(note=f"game/ taranamadı: {exc}")

    # 3. Proje kökündeki kardeş klasörler.
    try:
        for yol in _root_candidate_dirs(project_root):
            ekle(yol, "kök", False)
    except OSError as exc:
        return PyModuleResult(note=f"Proje kökü taranamadı: {exc}")

    if not adaylar:
        # ÇOK YAYGIN, ZARARSIZ durum: oyunun böyle bir klasörü yok. Sessizce
        # dönüyoruz ki her normal derlemede boş yere log basılmasın.
        return PyModuleResult(note="Python eklenti klasörü yok.")

    aday_adlari = [f"{y.name} ({k})" for y, k, _ in adaylar]

    if not metin:
        return PyModuleResult(
            candidates=aday_adlari,
            note="Oyun script'lerinden hiç metin çıkarılamadı "
                 "(.rpy/.rpyc bulunamadı ya da hepsi boş).",
        )

    # --- Adayları işle ----------------------------------------------------
    paketler: list[PyPackage] = []
    atlanan: list[str] = []

    for yol, koken, ipucundan in adaylar:
        moduller = _module_names(yol)
        if not moduller:
            # İçinde .py var (aksi hâlde aday olmazdı) ama hepsi _ren.py:
            # Ren'Py'nin kendi "Python-in-rpy" biçimi, importer bunları
            # zaten atlar.
            atlanan.append(f"{yol.name} (yalnızca _ren.py dosyaları var)")
            continue

        kullanilan = _imported_names(metin, moduller)
        if not kullanilan and not ipucundan:
            # Oyun bu klasörü import etmiyor: dokunmuyoruz. Alakasız bir
            # klasörü kopyalamak APK'yı gereksiz büyütür ve varlık
            # taramasını kirletirdi. sys.path ipucundan gelenlerde bu koşulu
            # aramıyoruz: oyun klasörü açıkça sys.path'e eklemiş.
            atlanan.append(yol.name)
            continue

        kaynak = yol
        tasindi = False

        if koken == "kök":
            # Kökteki klasör Ren'Py'nin varlık taramasına HİÇ girmez; önce
            # game/ içine taşınması gerekir.
            hedef = game_dir / yol.name
            if hedef.exists():
                # game/ içinde aynı adda bir şey zaten var; üzerine yazmak
                # oyunun kendi dosyasını yok edebilirdi. Var olanı
                # kullanmaya devam ediyoruz.
                kaynak = hedef
            else:
                try:
                    shutil.move(str(yol), str(hedef))
                except (OSError, shutil.Error) as exc:
                    atlanan.append(f"{yol.name} (taşınamadı: {exc})")
                    continue
                kaynak = hedef
                tasindi = True

        try:
            rel = kaynak.relative_to(game_dir).as_posix()
        except ValueError:
            atlanan.append(f"{yol.name} (game/ dışında kaldı)")
            continue

        try:
            dosya_sayisi = sum(1 for p in kaynak.rglob("*") if p.is_file())
        except OSError:
            dosya_sayisi = 0

        paket = PyPackage(
            name=kaynak.name,
            rel=rel,
            origin=koken,
            modules=moduller,
            imported=kullanilan,
            moved=tasindi,
            files=dosya_sayisi,
        )

        # AĞ 1 — asıl güvence.
        _export_to_packages(paket, kaynak, game_dir)

        paketler.append(paket)

    if not paketler:
        return PyModuleResult(
            skipped=atlanan,
            candidates=aday_adlari,
            note="İşlenecek eklenti klasörü bulunamadı.",
        )

    # AĞ 2 — ikinci katman.
    script_path = game_dir / GENERATED_SCRIPT
    try:
        script_path.write_text(_render_script(paketler), encoding="utf-8")
    except OSError as exc:
        return PyModuleResult(
            packages=paketler,
            skipped=atlanan,
            candidates=aday_adlari,
            note=f"Betik yazılamadı: {exc}",
        )

    return PyModuleResult(
        packages=paketler,
        script=script_path,
        skipped=atlanan,
        candidates=aday_adlari,
        note="hazır",
    )


# Eski ad: dışarıdan bu adla çağıran bir yer kalmışsa kırılmasın.
relocate_python_packages = prepare_python_packages

# Oyunun tüm script metnini çıkarma işi başka modüllerin de (ör. live2d
# tespiti) işine yarıyor. Aynı .rpyc çözümlemesini ikinci kez yazmak yerine
# aynı uygulamayı genel bir adla paylaşıyoruz.
script_text = _script_text
