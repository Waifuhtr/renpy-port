"""
Proje kökündeki Python eklenti klasörlerini Android'de çalışır hâle getirir.

SORUN
-----
Bazı oyunlar saf Python kütüphanelerini `game/` ile AYNI SEVİYEDE, ayrı bir
klasörde tutar ve script'lerinden düz `import X` ile çağırır:

    OyunKökü/
      game/
        01_settings/preset/lovense.rpy   ->  import LovenseRemoteSDK
      lovenseplugin/
        LovenseRemoteSDK.py

Masaüstünde bu çalışır, çünkü oyunun başlatıcısı (`<oyun>.py`) proje kökünü
`sys.path`'e ekler:

    renpy_base = path_to_renpy_base()
    sys.path.append(renpy_base)

Android'de böyle bir "gerçek dosya sistemi yolu" YOKTUR. Orada `import`,
Ren'Py'nin kendi `RenpyImporter`'ına düşer (`renpy/importer.py`) ve o da
modülleri YALNIZCA `renpy.loader.game_files` içinde arar — yani yalnızca
`game/` (ve `common/`) altında taranan dosyalarda. Proje kökündeki kardeş
klasörler bu taramaya hiç girmez.

Sonuç: oyun Android'de açılır açılmaz `ModuleNotFoundError` verir.

ÇÖZÜM
-----
İki adım, ikisi de yalnızca GEÇİCİ ÇALIŞMA KOPYASINDA:

  1. Klasörü `game/` içine taşı (böylece Ren'Py'nin varlık taramasına girer).
  2. Ren'Py'nin BELGELENMİŞ API'siyle o klasörü modül arama yoluna ekle:

         renpy.add_python_directory("lovenseplugin")

     Bu çağrı olmadan modül adı `lovenseplugin.LovenseRemoteSDK` olurdu;
     oyun ise düz `LovenseRemoteSDK` diye çağırıyor. Önek eklenince
     `RenpyImporter._get_module_info` doğru anahtarı buluyor.

Oyunun kendi `.rpy` dosyalarına DOKUNULMAZ; çağrı, ayrıca üretilen tek bir
betiğe konur (çeviri paketinde izlenen yaklaşımın aynısı).

NEDEN GENEL
-----------
Klasör adı sabit kodlanmaz. Kökteki her klasör için "içinde .py var mı" ve
"oyun script'leri bu modülleri gerçekten import ediyor mu" diye bakılır;
ikisi de doğruysa taşınır. Böylece aynı deseni kullanan başka oyunlar da
kendiliğinden çalışır, alakasız klasörler (docs/ gibi) boşuna taşınmaz.
"""

from __future__ import annotations

import re
import shutil
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
# blokları varsayılan olarak 0 önceliktedir; negatif değer bizimkinin ONLARDAN
# ÖNCE çalışmasını garantiler (renpy/script.py: initcode önceliğe göre
# sıralanır). Importer'ın kendisi bootstrap sırasında kurulduğu için
# (renpy/bootstrap.py) bu noktada çoktan hazırdır.
INIT_PRIORITY = -500

# Kökte durup da ASLA Python eklentisi olmayan klasörler.
_SKIP_DIRS = {
    "game", "renpy", "lib", "cache", "saves", "tl", "update",
    "old-game", "base", "archived", "python-packages",
}


@dataclass
class MovedPackage:
    """Taşınan tek bir eklenti klasörü."""

    name: str
    modules: list[str] = field(default_factory=list)
    imported: list[str] = field(default_factory=list)
    files: int = 0


@dataclass
class PyModuleResult:
    """İşlemin sonucu."""

    moved: list[MovedPackage] = field(default_factory=list)
    script: Optional[Path] = None
    skipped: list[str] = field(default_factory=list)
    note: str = ""

    @property
    def ok(self) -> bool:
        return bool(self.moved)


def _module_names(directory: Path) -> list[str]:
    """
    Bir klasörün düz `import X` ile çağrılabilecek modül adları.

    `_ren.py` dosyaları dışarıda: onlar Ren'Py'nin "Python-in-rpy" biçimidir
    ve importer da onları zaten atlar (`renpy/importer.py`).
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


def _candidate_dirs(project_root: Path) -> Iterable[Path]:
    """Kökte duran, içinde .py bulunan aday klasörler."""
    for child in sorted(project_root.iterdir()):
        try:
            if not child.is_dir() or child.is_symlink():
                continue
        except OSError:
            continue
        if child.name.lower() in _SKIP_DIRS or child.name.startswith("."):
            continue
        if not any(child.glob("*.py")):
            continue
        yield child


def _script_text(game_dir: Path) -> str:
    """
    Oyunun tüm script metni (.rpy ve derlenmiş .rpyc birlikte).

    .rpyc'yi de taramak ZORUNLU: derlenmiş bir dağıtım paketinde `.rpy`
    kaynakları hiç bulunmaz, oysa `import` satırları derlenmiş kodda düz
    metin olarak durur.
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


def _render_script(packages: list[str]) -> str:
    """Modül yolunu kaydeden betiği üretir."""
    lines = [
        "# Bu dosya Ren'Py Android Paketleyici tarafından ÜRETİLMİŞTİR.",
        "#",
        "# Oyun, proje kökündeki bir klasörden düz `import X` ile Python",
        "# modülü çağırıyor. Masaüstünde bu çalışır çünkü oyunun başlatıcısı",
        "# proje kökünü sys.path'e ekler. Android'de öyle bir yol yoktur;",
        "# `import` Ren'Py'nin kendi importer'ına düşer ve o da modülleri",
        "# yalnızca game/ altında arar.",
        "#",
        "# Bu yüzden klasör game/ içine taşındı ve aşağıda Ren'Py'nin",
        "# belgelenmiş API'siyle modül arama yoluna eklendi. Oyunun kendi",
        "# dosyalarına DOKUNULMADI.",
        "#",
        f"# init {INIT_PRIORITY}: oyunun kendi `init python` blokları varsayılan",
        "# olarak 0 önceliktedir; negatif değer bu kaydın onlardan ÖNCE",
        "# çalışmasını garantiler.",
        "",
        f"init {INIT_PRIORITY} python:",
    ]
    for name in packages:
        escaped = name.replace("\\", "\\\\").replace('"', '\\"')
        lines.append(f'    renpy.add_python_directory("{escaped}")')
    return "\n".join(lines) + "\n"


def relocate_python_packages(project_root: Path) -> PyModuleResult:
    """
    Kökteki Python eklenti klasörlerini `game/` içine taşır ve modül yolunu
    kaydeden betiği üretir.

    ASLA hata fırlatmaz: bir sorun çıkarsa hiçbir şey taşınmaz ve derleme
    eskisi gibi sürer. En kötü ihtimalle "hiçbir şey yapmamış" oluruz.
    """
    game_dir = project_root / "game"
    if not game_dir.is_dir():
        return PyModuleResult(note="game/ klasörü yok.")

    try:
        adaylar = list(_candidate_dirs(project_root))
    except OSError as exc:
        return PyModuleResult(note=f"Proje kökü taranamadı: {exc}")

    if not adaylar:
        return PyModuleResult(note="Kökte Python eklenti klasörü yok.")

    metin = _script_text(game_dir)
    if not metin:
        return PyModuleResult(note="Oyun script'leri okunamadı.")

    tasinan: list[MovedPackage] = []
    atlanan: list[str] = []

    for aday in adaylar:
        moduller = _module_names(aday)
        if not moduller:
            continue

        kullanilan = _imported_names(metin, moduller)
        if not kullanilan:
            # Oyun bu klasörü import etmiyor: dokunmuyoruz. Alakasız bir
            # klasörü game/ içine taşımak APK'yı gereksiz büyütür ve
            # varlık taramasını kirletirdi.
            atlanan.append(aday.name)
            continue

        hedef = game_dir / aday.name
        if hedef.exists():
            # game/ içinde aynı adda bir şey zaten var; üzerine yazmak
            # oyunun kendi dosyasını yok edebilirdi.
            atlanan.append(f"{aday.name} (game/ içinde aynı ad zaten var)")
            continue

        try:
            dosya_sayisi = sum(1 for p in aday.rglob("*") if p.is_file())
            shutil.move(str(aday), str(hedef))
        except (OSError, shutil.Error) as exc:
            atlanan.append(f"{aday.name} (taşınamadı: {exc})")
            continue

        tasinan.append(MovedPackage(
            name=aday.name,
            modules=moduller,
            imported=kullanilan,
            files=dosya_sayisi,
        ))

    if not tasinan:
        return PyModuleResult(
            skipped=atlanan,
            note="Taşınacak eklenti klasörü bulunamadı.",
        )

    script_path = game_dir / GENERATED_SCRIPT
    try:
        script_path.write_text(
            _render_script([p.name for p in tasinan]), encoding="utf-8"
        )
    except OSError as exc:
        return PyModuleResult(
            moved=tasinan,
            skipped=atlanan,
            note=f"Betik yazılamadı: {exc}",
        )

    return PyModuleResult(
        moved=tasinan,
        script=script_path,
        skipped=atlanan,
        note="taşındı",
    )
