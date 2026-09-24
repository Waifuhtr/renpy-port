"""
Oyunun YAZILDIĞI Ren'Py sürümünü tespit eder ve korur.

Neden gerekli
-------------
Ren'Py, eski sürümler için yazılmış oyunlara geriye dönük uyumluluk
ayarları uyguluyor. Hangi sürüme göre davranacağını şöyle buluyor
(gerçek kaynak, `renpy/common/00compat.rpy`, init -1000):

    1. game/script_version.txt  -> ast.literal_eval ile tuple okunur
    2. yoksa: <proje>/renpy/__init__.py okunur
         "version_tuple = (6, 99, 12, 4, vc_version)"  -> (6, 99, 12, 4)
         'version = "Ren\'Py X.Y.Z"'                   -> (X, Y, Z)

İkinci yol, masaüstü dağıtım paketleriyle gelen oyunlar için tek yoldur
(Ren'Py 6.99.12.4 `script_version.txt` yazmıyordu).

SORUN: bizim hattımız, Android'de kullanılmayan `renpy/` ve `lib/`
klasörlerini derleme kopyasından siliyor. Bu doğru bir temizlik, ama yan
etkisi var — silindikten sonra 2. yol çalışamıyor ve Ren'Py oyunun eski
bir sürüme ait olduğunu ANLAYAMIYOR. Oyun, kullanıcının bilgisayarında
uygulanan uyumluluk ayarları OLMADAN paketleniyor.

Çözüm: silmeden ÖNCE sürümü okuyup `game/script_version.txt` olarak
yazıyoruz. Bu, Ren'Py'nin kendi 1. yolu; yani bilgiyi uydurmuyoruz,
yalnızca silinmek üzere olan yerden okuyup Ren'Py'nin beklediği yere
taşıyoruz.

Ayrıca: eski (Python 2 döneminden) bir oyunu Ren'Py 8 (Python 3) ile
paketlemek, oyunun kendi kodunda Python 3'te geçersiz olan satırlar
varsa derlemeyi düşürüyor. Bu bilgiyi kullanıcıya söyleyebilmek için de
aynı sürüm tespitini kullanıyoruz.
"""

from __future__ import annotations

import ast
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

_VERSION_FILE = "script_version.txt"

# renpy/__init__.py içindeki iki biçim (gerçek kaynaktan birebir).
_TUPLE_6_99_12_4 = "version_tuple = (6, 99, 12, 4, vc_version)"
_VERSION_LINE_RE = re.compile(r"""version\s*=\s*["']Ren'Py ([\d.]+)""")

# Ren'Py 8.0 ile Python 3'e geçildi; 7.x serisi Python 2 olarak sürdü.
PYTHON3_FROM = 8


@dataclass
class LegacyInfo:
    """Oyunun yazıldığı sürüm hakkında bulunanlar."""

    version: Optional[tuple[int, ...]] = None
    source: str = ""
    preserved: bool = False
    note: str = ""

    @property
    def pretty(self) -> str:
        if not self.version:
            return "bilinmiyor"
        return ".".join(str(i) for i in self.version)

    def is_python2_era(self) -> bool:
        """Oyun, Python 2 dönemindeki bir Ren'Py ile mi yazılmış?"""
        return bool(self.version) and self.version[0] < PYTHON3_FROM


def _read_existing(game_dir: Path) -> Optional[tuple[int, ...]]:
    """`game/script_version.txt` zaten varsa onu okur."""
    path = game_dir / _VERSION_FILE
    if not path.is_file():
        return None
    try:
        value = ast.literal_eval(path.read_text(encoding="utf-8").strip())
    except (OSError, ValueError, SyntaxError):
        return None
    if isinstance(value, (list, tuple)) and all(isinstance(i, int) for i in value):
        return tuple(value)
    return None


def _read_from_renpy_dir(project_root: Path) -> Optional[tuple[int, ...]]:
    """
    `<proje>/renpy/__init__.py` dosyasından sürümü çıkarır.

    Mantık `00compat.rpy` ile birebir aynı; tek farkı, oyunu çalıştırmak
    yerine dosyayı doğrudan okumamız.
    """
    init_py = project_root / "renpy" / "__init__.py"
    if not init_py.is_file():
        return None
    try:
        data = init_py.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None

    if _TUPLE_6_99_12_4 in data:
        return (6, 99, 12, 4)

    for line in data.splitlines():
        match = _VERSION_LINE_RE.match(line.strip())
        if match:
            try:
                return tuple(int(i) for i in match.group(1).split("."))
            except ValueError:
                return None
    return None


def detect(project_root: Path) -> LegacyInfo:
    """Oyunun yazıldığı Ren'Py sürümünü bulur (dosyaya dokunmaz)."""
    game_dir = project_root / "game"

    mevcut = _read_existing(game_dir)
    if mevcut is not None:
        return LegacyInfo(version=mevcut, source="game/script_version.txt")

    from_dir = _read_from_renpy_dir(project_root)
    if from_dir is not None:
        return LegacyInfo(version=from_dir, source="renpy/__init__.py")

    return LegacyInfo()


def preserve(project_root: Path) -> LegacyInfo:
    """
    Sürümü tespit eder ve gerekiyorsa `game/script_version.txt` yazar.

    `renpy/` klasörü SİLİNMEDEN ÖNCE çağrılmalı. Dosya zaten varsa hiçbir
    şey yapılmaz: oyunun kendi dosyasının üzerine yazmak, bilmediğimiz bir
    kararı bozmak olurdu.
    """
    info = detect(project_root)

    if info.version is None:
        info.note = "Oyunun yazıldığı Ren'Py sürümü tespit edilemedi."
        return info

    if info.source == _VERSION_FILE or info.source.endswith(_VERSION_FILE):
        info.note = "Sürüm dosyası zaten projede vardı; dokunulmadı."
        return info

    game_dir = project_root / "game"
    if not game_dir.is_dir():
        info.note = "game/ klasörü yok; sürüm dosyası yazılamadı."
        return info

    try:
        (game_dir / _VERSION_FILE).write_text(
            repr(info.version) + "\n", encoding="utf-8"
        )
    except OSError as exc:
        info.note = f"Sürüm dosyası yazılamadı: {exc}"
        return info

    info.preserved = True
    info.note = (
        f"Sürüm {info.source} dosyasından okunup game/{_VERSION_FILE} "
        "olarak korundu."
    )
    return info
