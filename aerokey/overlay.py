"""
Kullanıcının hazırladığı DÜZELTME dosyalarını projenin üzerine koyar.

Neden gerekli
-------------
Oyunun kendi kodundaki bir hatayı düzeltmek çoğu zaman tek bir satır
meselesi. Ama o satır 784 MB'lık bir ZIP'in içindeki bir dosyada
duruyor: kullanıcının arşivi açması, dosyayı düzeltmesi, her şeyi
yeniden sıkıştırması ve yeniden yüklemesi gerekiyordu.

Bu adım, yalnızca DEĞİŞEN dosyaları kabul ediyor. Birkaç kilobaytlık
küçük bir ZIP (ya da tek bir `.rpy`) yükleniyor ve derleme kopyasının
üzerine yazılıyor. Orijinal proje paketine dokunulmuyor.

Yol çözümü
----------
ZIP'in içinde üst düzeyde bir `game/` klasörü varsa yollar PROJE KÖKÜNE
göre kabul ediliyor (`game/x.rpy` -> `<proje>/game/x.rpy`). Yoksa
yollar `game/` altına yazılıyor (`x.rpy` -> `<proje>/game/x.rpy`), çünkü
düzeltilen dosyalar neredeyse her zaman oradan geliyor.

Güvenlik
--------
ZIP güvenilmez veri: `../../bir_yer` gibi adlar hedef klasörün dışına
yazmaya çalışırdı. Böyle girdiler atılıyor (bkz. `_safe_relative`).
"""

from __future__ import annotations

import zipfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

# Bir seferde kabul edilen en büyük düzeltme paketi. Düzeltmeler küçük
# olmalı; yüzlerce megabaytlık bir "düzeltme" aslında yeni bir projedir
# ve normal yükleme yolundan gitmeli.
MAX_TOTAL_BYTES = 64 * 1024 * 1024
MAX_FILES = 2000


class OverlayError(Exception):
    """Düzeltme paketi uygulanamadığında yükseltilir."""


@dataclass
class OverlayResult:
    """Uygulanan düzeltmelerin sonucu."""

    written: list[str] = field(default_factory=list)
    replaced: list[str] = field(default_factory=list)
    skipped: list[str] = field(default_factory=list)
    dropped_rpyc: int = 0
    total_bytes: int = 0


def _safe_relative(name: str) -> Optional[Path]:
    """Arşiv içindeki adı, hedefin DIŞINA çıkamayacak göreli yola çevirir."""
    cleaned = name.replace("\\", "/").strip()
    if not cleaned or cleaned.endswith("/"):
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


def _drop_rpyc(path: Path) -> bool:
    """
    Yazılan `.rpy` dosyasının derlenmiş kopyasını siler.

    ŞART: Ren'Py, `.rpyc` kopyayı kaynaktan yeniyse doğrudan kullanıyor.
    Eski `.rpyc` kalırsa kullanıcının düzeltmesi hiç okunmaz ve "düzelttim
    ama değişmedi" durumu oluşurdu.
    """
    if path.suffix.lower() not in (".rpy", ".rpym"):
        return False
    derlenmis = path.with_suffix(path.suffix + "c")
    try:
        if derlenmis.is_file():
            derlenmis.unlink()
            return True
    except OSError:
        pass
    return False


def _target_for(
    project_root: Path, relative: Path, root_relative: bool
) -> Optional[Path]:
    taban = project_root if root_relative else project_root / "game"
    hedef = (taban / relative).resolve()
    kok = project_root.resolve()
    if hedef != kok and kok not in hedef.parents:
        return None
    return hedef


def _write(project_root: Path, relative: Path, data: bytes,
           root_relative: bool, res: OverlayResult) -> None:
    hedef = _target_for(project_root, relative, root_relative)
    if hedef is None:
        res.skipped.append(str(relative))
        return

    vardi = hedef.is_file()
    try:
        hedef.parent.mkdir(parents=True, exist_ok=True)
        hedef.write_bytes(data)
    except OSError:
        res.skipped.append(str(relative))
        return

    if _drop_rpyc(hedef):
        res.dropped_rpyc += 1

    gorunen = str(hedef.relative_to(project_root))
    res.total_bytes += len(data)
    if vardi:
        res.replaced.append(gorunen)
    else:
        res.written.append(gorunen)


def apply_zip(project_root: Path, archive: Path) -> OverlayResult:
    """Bir ZIP içindeki düzeltmeleri projeye uygular."""
    res = OverlayResult()

    with zipfile.ZipFile(archive) as zf:
        girdiler = [i for i in zf.infolist() if not i.is_dir()]
        if len(girdiler) > MAX_FILES:
            raise OverlayError(
                f"Düzeltme paketinde çok fazla dosya var ({len(girdiler)}); "
                f"en fazla {MAX_FILES} kabul ediliyor."
            )
        toplam = sum(i.file_size for i in girdiler)
        if toplam > MAX_TOTAL_BYTES:
            raise OverlayError(
                "Düzeltme paketi çok büyük "
                f"({toplam / (1024 * 1024):.1f} MB); en fazla "
                f"{MAX_TOTAL_BYTES // (1024 * 1024)} MB kabul ediliyor. "
                "Bu kadar büyük bir değişiklik düzeltme değil, yeni bir "
                "projedir — normal yükleme alanını kullanın."
            )

        # ZIP'in içinde üst düzey bir `game/` varsa yollar proje köküne
        # göredir; yoksa dosyalar doğrudan `game/` altına gider.
        kok_gorece = any(
            i.filename.replace("\\", "/").split("/")[0] == "game" for i in girdiler
        )

        for info in girdiler:
            relative = _safe_relative(info.filename)
            if relative is None:
                res.skipped.append(info.filename)
                continue
            try:
                data = zf.read(info)
            except (OSError, zipfile.BadZipFile, RuntimeError):
                res.skipped.append(info.filename)
                continue
            _write(project_root, relative, data, kok_gorece, res)

    return res


def apply_file(project_root: Path, path: Path, name: str) -> OverlayResult:
    """Tek bir düzeltme dosyasını `game/` altına koyar."""
    res = OverlayResult()
    relative = _safe_relative(name)
    if relative is None:
        raise OverlayError(f"Geçersiz dosya adı: {name!r}")
    try:
        data = path.read_bytes()
    except OSError as exc:
        raise OverlayError(f"Düzeltme dosyası okunamadı: {exc}") from exc
    if len(data) > MAX_TOTAL_BYTES:
        raise OverlayError("Düzeltme dosyası çok büyük.")
    _write(project_root, relative, data, False, res)
    return res


def apply(project_root: Path, path: Path, name: str = "") -> OverlayResult:
    """
    Düzeltmeleri uygular. ZIP ise açar, değilse tek dosya olarak koyar.
    """
    if not path.is_file():
        raise OverlayError("Düzeltme dosyası bulunamadı.")
    if zipfile.is_zipfile(path):
        return apply_zip(project_root, path)
    return apply_file(project_root, path, name or path.name)
