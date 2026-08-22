"""
Ren'Py -> Android (APK/AAB) Paketleyici
=======================================

Bir Ren'Py oyun projesinin ZIP'ini alır ve arka planda "renkit"
(renutil + renconstruct) araçlarıyla resmi Ren'Py/RAPT + Gradle derleme
hattını çalıştırarak Android APK ve/veya AAB dosyası üretir.

  renkit: https://github.com/kobaltcore/renkit  (MIT lisans)

Arayüz, Gradio yerine FastAPI + düz HTML/CSS/JS ile yazılmıştır: derleme
günlüğü Server-Sent Events ile canlı akar, dosyalar normal HTTP ile
indirilir. Bu betik, Ren'Py SDK'sının ve Java 21'in Docker imajı içinde
zaten kurulu olduğunu varsayar (bkz. Dockerfile).
"""

from __future__ import annotations

import asyncio
import base64
import json
import os
import re
import secrets
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import uuid
import zipfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable, Optional

from fastapi import FastAPI, Form, HTTPException, Request, UploadFile, File
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles

sys.path.insert(0, str(Path(__file__).resolve().parent))
from aerokey import patch_rapt  # noqa: E402  (yol ayarından sonra gelmeli)

APP_DIR = Path(__file__).resolve().parent
WEB_DIR = APP_DIR / "web"

DEFAULT_RENPY_VERSION = os.environ.get("RENPY_VERSION", "8.5.3")
VERSION_RE = re.compile(r"^\d+\.\d+\.\d+$")

# Yapı sırasında açılan proje dosyaları / Gradle ara çıktıları buraya gider
# ve her işten sonra silinir.
WORK_ROOT = Path(tempfile.gettempdir()) / "renpy_android_jobs"
# Üretilen APK/AAB dosyaları buraya kopyalanır ve kullanıcıya sunulana kadar
# SİLİNMEZ (yalnızca eskiyince, aşağıdaki temizlik ile silinir).
RESULTS_ROOT = Path(tempfile.gettempdir()) / "renpy_android_results"
WORK_ROOT.mkdir(parents=True, exist_ok=True)
RESULTS_ROOT.mkdir(parents=True, exist_ok=True)


def _resolve_data_dir() -> Path:
    """
    Derlemeler arasında KALICI olması gereken veriler (imza anahtarı, oyun
    kimliği kaydı) için yazılabilir bir dizin seçer.

    Hugging Face Space'lerinde kalıcı disk etkinse /data yazılabilir olur ve
    Space yeniden başlasa bile içeriği korunur. Değilse geçici bir dizine
    düşeriz — bu durumda Space her yeniden başladığında imza anahtarı
    değişir, bu yüzden anahtarı indirip saklamak önemlidir (arayüzde
    açıkça uyarıyoruz).
    """
    candidates = [os.environ.get("PORTER_DATA_DIR"), "/data", str(Path.home() / ".renpy_porter")]
    for candidate in candidates:
        if not candidate:
            continue
        path = Path(candidate)
        try:
            path.mkdir(parents=True, exist_ok=True)
            probe = path / ".write_test"
            probe.write_text("ok", encoding="utf-8")
            probe.unlink()
            return path
        except OSError:
            continue
    fallback = Path(tempfile.gettempdir()) / "renpy_porter_data"
    fallback.mkdir(parents=True, exist_ok=True)
    return fallback


DATA_DIR = _resolve_data_dir()
DATA_IS_PERSISTENT = str(DATA_DIR) not in (
    str(Path(tempfile.gettempdir()) / "renpy_porter_data"),
)

GAME_ID_PREFIX = os.environ.get("AEROKEY_GAME_ID_PREFIX", "riaslink_oyun_")
GAME_ID_REGISTRY = DATA_DIR / "game_ids.json"
SIGNING_DIR = DATA_DIR / "signing"
AUTO_KEYSTORE = SIGNING_DIR / "auto.keystore"
AUTO_KEYSTORE_META = SIGNING_DIR / "auto.json"

DEFAULT_AEROKEY_BASE_URL = os.environ.get("AEROKEY_BASE_URL", "https://riaslink.fun")
DEFAULT_AEROKEY_KEY_PAGE = os.environ.get("AEROKEY_KEY_PAGE", "https://riaslink.fun/bilgi")


def _cleanup_old_dirs(root: Path, max_age_hours: float = 6.0) -> None:
    """Belirtilen saatten eski iş klasörlerini temizler; Space uzun süre
    yeniden başlamadan diskin dolmasını önler."""
    now = time.time()
    try:
        for child in root.iterdir():
            try:
                if child.is_dir() and (now - child.stat().st_mtime) > max_age_hours * 3600:
                    shutil.rmtree(child, ignore_errors=True)
            except OSError:
                pass
    except FileNotFoundError:
        pass


_cleanup_old_dirs(WORK_ROOT)
_cleanup_old_dirs(RESULTS_ROOT)


# ===========================================================================
# Ren'Py proje çözümleme yardımcıları
# ===========================================================================

_DEFINE_STRING_RE_TEMPLATE = r'define\s+{}\s*=\s*_?\(?\s*([\'"])(.*?)\1'
_BAD_DIRNAME_CHARS = set(" :;")


def _extract_defined_string(text: str, varname: str) -> Optional[str]:
    """Metinde 'define <varname> = "..."' kalıbını arar ve değeri döner."""
    pattern = re.compile(_DEFINE_STRING_RE_TEMPLATE.format(re.escape(varname)))
    m = pattern.search(text)
    return m.group(2) if m else None


def _read_game_rpy_text(project_root: Path) -> str:
    """game/ içindeki tüm .rpy dosyalarının metnini birleştirip döner
    (options.rpy varsa en başta). build.name/config.name/build.package gibi
    tanımlar bazı projelerde options.rpy dışında bir dosyada da olabilir."""
    game_dir = project_root / "game"
    if not game_dir.is_dir():
        return ""

    paths = sorted(
        game_dir.rglob("*.rpy"), key=lambda p: (p.name != "options.rpy", str(p))
    )
    chunks = []
    for p in paths:
        try:
            chunks.append(p.read_text(encoding="utf-8", errors="ignore"))
        except OSError:
            continue
    return "\n".join(chunks)


def _find_project_root(extracted_dir: Path) -> Optional[Path]:
    """Ren'Py projesinin 'game/' klasörünü barındıran kök dizini bulur."""
    if (extracted_dir / "game").is_dir():
        return extracted_dir

    subdirs = [p for p in extracted_dir.iterdir() if p.is_dir()]
    if len(subdirs) == 1 and (subdirs[0] / "game").is_dir():
        return subdirs[0]

    for candidate in extracted_dir.rglob("game"):
        if candidate.is_dir():
            return candidate.parent

    return None


_DIST_SUFFIX_RE = re.compile(r"-(pc|linux|mac|win|market)$", re.IGNORECASE)


@dataclass
class DistributionInfo:
    """Yüklenen şeyin ham kaynak mı yoksa derlenmiş dağıtım paketi mi
    olduğuna dair tespit sonucu."""
    is_distribution: bool
    source_stripped: bool
    signals: list[str] = field(default_factory=list)


def _detect_distribution_build(project_root: Path) -> DistributionInfo:
    """
    Ren'Py Launcher'ın ürettiği HAZIR bir masaüstü dağıtım paketi (örn.
    "OyunAdı-1.2.1-pc") ham proje kaynağından farklıdır: .rpy kaynakları
    çıkarılıp yalnızca derlenmiş .rpyc bırakılır ve renpy/ + lib/ (motor +
    native kütüphaneler) eklenir.

    Bu araç artık böyle paketleri de destekliyor; bu fonksiyon yalnızca
    durumu TESPİT eder, böylece uygulama adı/paketi gibi bilgilerin
    kaynaktan okunamayacağını bilip kullanıcıdan isteyebiliriz.
    """
    game_dir = project_root / "game"
    signals: list[str] = []

    options_rpy = game_dir / "options.rpy"
    options_rpyc = game_dir / "options.rpyc"
    source_stripped = (not options_rpy.is_file()) and options_rpyc.is_file()
    if source_stripped:
        signals.append(
            "game/options.rpy (kaynak) yok ama game/options.rpyc (derlenmiş hali) var"
        )

    has_sdk_siblings = (project_root / "renpy").is_dir() or (project_root / "lib").is_dir()
    if has_sdk_siblings:
        signals.append("game/ ile aynı seviyede renpy/ ve/veya lib/ klasörü var")

    dist_suffix = bool(_DIST_SUFFIX_RE.search(project_root.name))
    if dist_suffix:
        signals.append(
            f'klasör adı ("{project_root.name}") masaüstü dağıtım paketi biçiminde'
        )

    is_distribution = source_stripped or has_sdk_siblings or (dist_suffix and source_stripped)
    return DistributionInfo(is_distribution, source_stripped, signals)


# Masaüstü dağıtım paketleriyle gelen, Android derlemesinde işe yaramayan
# ve boşuna kopyalanıp taranan klasör/dosyalar.
_DESKTOP_ONLY_DIRS = ("renpy", "lib")
_DESKTOP_ONLY_SUFFIXES = (".exe", ".app", ".sh", ".dmg")


def _strip_desktop_extras(project_root: Path) -> Optional[str]:
    """
    Derlenmiş bir masaüstü paketiyle gelen motor/ikili dosyalarını GEÇİCİ
    çalışma kopyasından siler.

    Bunlar Android paketine hiçbir şey katmaz; ama yüzlerce megabayt
    tutabildikleri ve Ren'Py'nin kendi kaynak ağacıyla karışabildikleri
    için derlemeyi hem yavaşlatır hem de riske atarlar. Orijinal ZIP'e
    dokunulmaz — yalnızca bu derlemenin kopyası temizlenir.
    """
    removed: list[str] = []

    for name in _DESKTOP_ONLY_DIRS:
        target = project_root / name
        if target.is_dir():
            shutil.rmtree(target, ignore_errors=True)
            removed.append(f"{name}/")

    for child in project_root.iterdir():
        try:
            if child.is_file() and child.suffix.lower() in _DESKTOP_ONLY_SUFFIXES:
                child.unlink()
                removed.append(child.name)
        except OSError:
            continue

    if not removed:
        return None
    return (
        "Temizlik: masaüstü dağıtım paketiyle gelen ve Android derlemesinde "
        "kullanılmayan " + ", ".join(sorted(removed)) + " öğeleri bu derlemenin "
        "GEÇİCİ ÇALIŞMA KOPYASINDAN çıkarıldı (orijinal dosyalarınıza dokunulmadı)."
    )


def _fix_build_directory_name(project_root: Path) -> Optional[str]:
    """
    Ren'Py'nin Android derlemesi, build.directory_name değerinde boşluk,
    ':' veya ';' varsa derlemeyi reddediyor. Bu fonksiyon o durumu YALNIZCA
    bu derleme için kullanılan GEÇİCİ proje kopyasını düzenleyerek giderir.
    """
    game_dir = project_root / "game"
    options_path = game_dir / "options.rpy"

    text = ""
    if options_path.is_file():
        try:
            text = options_path.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            text = ""

    pattern_dirname = re.compile(
        _DEFINE_STRING_RE_TEMPLATE.format(re.escape("build.directory_name"))
    )
    m = pattern_dirname.search(text)
    if m:
        current = m.group(2)
        if not any(c in _BAD_DIRNAME_CHARS for c in current):
            return None  # açıkça tanımlı ve zaten temiz
        fixed = re.sub(r"[ :;]", "_", current) or "renpy_android_build"
        new_text = text[: m.start(2)] + fixed + text[m.end(2):]
        try:
            options_path.write_text(new_text, encoding="utf-8")
        except OSError:
            return None
        return (
            "Otomatik düzeltme: build.directory_name değeri "
            f'("{current}") Android derlemesiyle uyumsuz olduğu için bu '
            f'derlemenin geçici kopyasında "{fixed}" olarak değiştirildi.'
        )

    all_text = _read_game_rpy_text(project_root)
    name = _extract_defined_string(all_text, "build.name") or _extract_defined_string(
        all_text, "config.name"
    )
    if name and any(c in _BAD_DIRNAME_CHARS for c in name):
        fixed = re.sub(r"[ :;]", "_", name) or "renpy_android_build"
        if options_path.is_file():
            new_text = text.rstrip("\n") + f'\ndefine build.directory_name = "{fixed}"\n'
            try:
                options_path.write_text(new_text, encoding="utf-8")
            except OSError:
                return None
            where = "game/options.rpy dosyasına"
        else:
            override_path = game_dir / "zz_android_builder_overrides.rpy"
            try:
                game_dir.mkdir(parents=True, exist_ok=True)
                override_path.write_text(
                    f'define build.directory_name = "{fixed}"\n', encoding="utf-8"
                )
            except OSError:
                return None
            where = f"yeni bir {override_path.name} dosyasına"
        return (
            "Otomatik düzeltme: build.directory_name tanımlı değildi ve "
            f'build.name/config.name ("{name}") boşluk içerdiği için derleme '
            f'başarısız olurdu. Geçici kopyada {where} düzeltme eklendi.'
        )
    return None


# Gerçekten yayınlanmış bir Ren'Py Android projesinden alınmış android.json
# şablonu. Yalnızca proje-özel alanlar değiştirilir.
_ANDROID_JSON_DEFAULTS = {
    "expansion": False,
    "google_play_key": None,
    "google_play_salt": None,
    "heap_size": "3",
    "include_pil": False,
    "include_sqlite": False,
    "layout": None,
    "orientation": "sensorLandscape",
    "permissions": ["VIBRATE", "INTERNET"],
    "source": False,
    "store": "none",
    "target_version": 14,
    "update_always": True,
    "update_icons": True,
    "update_keystores": True,
}


def _derive_version_code(version: str) -> str:
    """'1.2.3' -> '10203' gibi basit, artan bir versionCode türetir."""
    try:
        v = 0
        for part in version.split("."):
            v = v * 100 + int(part)
        return str(v) if v > 0 else "1"
    except (ValueError, AttributeError):
        return "1"


def _sanitize_package_component(s: str) -> str:
    s = re.sub(r"[^a-zA-Z0-9_]", "", s).lower()
    if not s or not re.match(r"[a-zA-Z_]", s):
        s = "a" + s
    return s or "app"


_DEFAULT_PACKAGE_PREFIX = "com.renpyandroidbuilder"


def _sanitize_package_prefix(prefix: str) -> str:
    """Kullanıcının girdiği paket önekini güvenli bir biçime getirir."""
    parts = [p for p in (prefix or "").strip().lower().split(".") if p]
    parts = [_sanitize_package_component(p) for p in parts]
    parts = [p for p in parts if p]
    return ".".join(parts) if parts else _DEFAULT_PACKAGE_PREFIX


def _sanitize_package_name(package: str) -> Optional[str]:
    """Kullanıcının elle girdiği tam paket adını doğrular/temizler."""
    parts = [p for p in (package or "").strip().lower().split(".") if p]
    if len(parts) < 2:
        return None
    return ".".join(_sanitize_package_component(p) for p in parts)


@dataclass
class ProjectIdentity:
    """Derlenen oyunun kimliği: nereden geldiği bilgisiyle birlikte."""
    name: str
    package: str
    version: str
    name_source: str
    package_source: str
    version_source: str


def _resolve_identity(
    project_root: Path,
    package_prefix: str,
    manual_name: str,
    manual_package: str,
    manual_version: str,
) -> ProjectIdentity:
    """
    Uygulama adı / paket adı / sürümü belirler.

    Öncelik sırası: kullanıcının elle girdiği değer > projenin kendi
    tanımları (.rpy kaynağından) > türetilmiş varsayılan. Derlenmiş
    paketlerde .rpy kaynağı bulunmadığı için elle giriş kritik hale gelir;
    bu yüzden her alanın nereden geldiğini de kaydedip günlüğe yazıyoruz.
    """
    text = _read_game_rpy_text(project_root)

    rpy_name = _extract_defined_string(text, "build.name") or _extract_defined_string(
        text, "config.name"
    )
    rpy_package = _extract_defined_string(text, "build.package")
    rpy_version = _extract_defined_string(text, "config.version")

    manual_name = (manual_name or "").strip()
    manual_version = (manual_version or "").strip()
    clean_manual_package = _sanitize_package_name(manual_package)

    if manual_name:
        name, name_source = manual_name, "arayüzden elle girildi"
    elif rpy_name:
        name, name_source = rpy_name, "projenin .rpy kaynağından okundu"
    else:
        # Klasör adı, dağıtım son ekinden ve sürüm numarasından arındırılır.
        guess = _DIST_SUFFIX_RE.sub("", project_root.name)
        guess = re.sub(r"-\d+(\.\d+)*$", "", guess).replace("-", " ").strip()
        name = guess or "Ren'Py Game"
        name_source = "klasör adından tahmin edildi"

    if clean_manual_package:
        package, package_source = clean_manual_package, "arayüzden elle girildi"
    elif rpy_package:
        package, package_source = rpy_package.strip().lower(), "projenin .rpy kaynağından okundu"
    else:
        comp = _sanitize_package_component(re.sub(r"[^a-zA-Z0-9]", "", name)) or "game"
        package = f"{_sanitize_package_prefix(package_prefix)}.{comp}"
        package_source = "oyun adından türetildi"

    if manual_version:
        version, version_source = manual_version, "arayüzden elle girildi"
    elif rpy_version:
        version, version_source = rpy_version, "projenin .rpy kaynağından okundu"
    else:
        version, version_source = "1.0", "varsayılan"

    return ProjectIdentity(
        name, package, version, name_source, package_source, version_source
    )


def _required_permissions(need_internet: bool, need_notifications: bool) -> list[str]:
    """
    AeroKey'in çalışması için android.json'da bulunması gereken izinler.

    POST_NOTIFICATIONS yalnızca Android 13+ tarafından zorunlu tutulur; daha
    eski sürümler onu görmezden gelir, o yüzden koşulsuz eklemek güvenlidir.
    """
    permissions: list[str] = []
    if need_internet:
        permissions.append("INTERNET")
    if need_notifications:
        permissions.append("POST_NOTIFICATIONS")
    return permissions


def _ensure_android_json(
    project_root: Path,
    identity: ProjectIdentity,
    need_internet: bool,
    need_notifications: bool = False,
) -> Optional[str]:
    """
    Ren'Py'nin Android derlemesi, proje kökünde bir android.json bulamazsa
    "Run configure before attempting to build the app" diyerek reddediyor.
    Bu fonksiyon dosya yoksa makul varsayılanlarla bir tane yazar; varsa
    yalnızca gerekli izni ekler.
    """
    existing_path = None
    for candidate in ("android.json", ".android.json"):
        if (project_root / candidate).exists():
            existing_path = project_root / candidate
            break

    required = _required_permissions(need_internet, need_notifications)

    if existing_path is not None:
        if not required:
            return None
        # Kullanıcının kendi dosyasında eksik olan izinleri ekleriz, başka
        # hiçbir alanına dokunmayız.
        try:
            data = json.loads(existing_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None
        permissions = data.get("permissions") or []
        missing = [p for p in required if p not in permissions]
        if not missing:
            return None
        permissions.extend(missing)
        data["permissions"] = permissions
        try:
            existing_path.write_text(json.dumps(data, indent=4), encoding="utf-8")
        except OSError:
            return None
        return (
            f"Otomatik düzeltme: AeroKey için gereken {', '.join(missing)} izni "
            f"{existing_path.name} dosyasına eklendi (geçici kopyada)."
        )

    data = dict(_ANDROID_JSON_DEFAULTS)
    data.update(
        {
            "name": identity.name,
            "icon_name": identity.name[:30],
            "package": identity.package,
            "version": identity.version,
            "numeric_version": _derive_version_code(identity.version),
        }
    )
    for permission in required:
        if permission not in data["permissions"]:
            data["permissions"].append(permission)

    try:
        (project_root / "android.json").write_text(
            json.dumps(data, indent=4), encoding="utf-8"
        )
    except OSError:
        return None

    return (
        "Otomatik oluşturma: Bu proje daha önce Android için 'Configure' "
        "edilmemiş (android.json yok). Geçici çalışma kopyasına şu "
        "değerlerle bir tane oluşturuldu:\n"
        f'  - name    : "{identity.name}"  ({identity.name_source})\n'
        f'  - package : "{identity.package}"  ({identity.package_source})\n'
        f'  - version : "{identity.version}"  ({identity.version_source})'
    )


_ICON_SOURCE_DIR = Path("/app/icon_source")
_BANNER_SOURCE_DIR = Path("/app/banner_source")
_BANNER_EXTENSIONS = (".gif", ".png", ".jpg", ".jpeg", ".webp")
_ICON_CANVAS = 432
_ICON_SAFE_RATIO = 0.66  # Android adaptif ikon güvenli alanına kaba bir yaklaşım


def _find_bundled_icon() -> Optional[Path]:
    """Dockerfile'ın /app/icon_source/ altına kopyaladığı gömülü ikon."""
    if not _ICON_SOURCE_DIR.is_dir():
        return None
    for name in ("icon.png", "icon.jpg", "icon.jpeg", "icon.PNG", "icon.JPG"):
        candidate = _ICON_SOURCE_DIR / name
        if candidate.is_file():
            return candidate
    return None


def _find_bundled_banner() -> Optional[Path]:
    """
    Dockerfile'in /app/banner_source/ altina kopyaladigi, depoya eklenmis
    afis gorseli (banner.gif / .png / ...). Yoksa None doner ve giris ekrani
    afissiz cizilir.
    """
    if not _BANNER_SOURCE_DIR.is_dir():
        return None
    for ext in _BANNER_EXTENSIONS:
        candidate = _BANNER_SOURCE_DIR / f"banner{ext}"
        if candidate.is_file():
            return candidate
        candidate = _BANNER_SOURCE_DIR / f"banner{ext.upper()}"
        if candidate.is_file():
            return candidate
    return None


def _prepare_android_icon(
    project_root: Path, uploaded_icon_path: Optional[str]
) -> Optional[str]:
    """
    Ren'Py, Android ikonunu proje kökündeki iki 432x432 PNG dosyasından
    üretir: android-icon_foreground.png ve android-icon_background.png.
    Proje bunları sağlıyorsa dokunmayız; sağlamıyorsa yüklenen/gömülü
    görselden otomatik üretiriz.
    """
    fg_path = project_root / "android-icon_foreground.png"
    bg_path = project_root / "android-icon_background.png"
    if fg_path.exists() and bg_path.exists():
        return None  # proje zaten kendi ikonlarını sağlamış

    source = Path(uploaded_icon_path) if uploaded_icon_path else _find_bundled_icon()
    if source is None or not source.exists():
        return None  # kaynak yok -> Ren'Py varsayılanını kullanır

    try:
        from PIL import Image
    except ImportError:
        return "Uyarı: İkon kaynağı bulundu ama Pillow kurulu değil, ikon üretimi atlandı."

    try:
        resample = getattr(Image, "Resampling", Image).LANCZOS

        img = Image.open(source).convert("RGBA")
        safe = int(_ICON_CANVAS * _ICON_SAFE_RATIO)

        ratio = img.width / img.height
        if ratio >= 1:
            new_w, new_h = safe, max(1, int(safe / ratio))
        else:
            new_h, new_w = safe, max(1, int(safe * ratio))
        resized = img.resize((new_w, new_h), resample)

        fg_canvas = Image.new("RGBA", (_ICON_CANVAS, _ICON_CANVAS), (0, 0, 0, 0))
        fg_canvas.paste(
            resized,
            ((_ICON_CANVAS - new_w) // 2, (_ICON_CANVAS - new_h) // 2),
            resized,
        )
        fg_canvas.save(fg_path)

        bg_canvas = Image.new("RGBA", (_ICON_CANVAS, _ICON_CANVAS), (255, 255, 255, 255))
        bg_canvas.save(bg_path)
    except Exception as exc:  # noqa: BLE001
        return (
            f"Uyarı: İkon oluşturulurken hata ({exc!r}); Ren'Py varsayılan "
            "ikonu kullanılacak, derleme devam ediyor."
        )

    return (
        f'Otomatik ikon: "{source.name}" kaynağından iki katmanlı adaptif '
        "ikon (432x432, arka plan beyaz) geçici kopyaya üretildi."
    )


def _scan_local_py_imports(project_root: Path) -> Optional[str]:
    """
    game/ içindeki düz .py dosyalarını (_ren.py hariç) bulup .rpy
    dosyalarında nasıl kullanıldıklarına bakar. Bu tür içe aktarmalar bazı
    Android derlemelerinde ModuleNotFoundError'a yol açabiliyor.
    BU FONKSİYON OYUN KODUNU DEĞİŞTİRMEZ, yalnızca bilgilendirir.
    """
    game_dir = project_root / "game"
    if not game_dir.is_dir():
        return None

    local_modules = sorted(
        p.stem for p in game_dir.glob("*.py") if not p.name.endswith("_ren.py")
    )
    if not local_modules:
        return None

    all_text = ""
    for rp in game_dir.rglob("*.rpy"):
        try:
            all_text += rp.read_text(encoding="utf-8", errors="ignore") + "\n"
        except OSError:
            continue

    unused, used_but_risky = [], []
    for mod in local_modules:
        esc = re.escape(mod)
        import_line_re = re.compile(
            r"^\s*(import\s+" + esc + r"\b|from\s+" + esc + r"\s+import\b)",
            re.MULTILINE,
        )
        if not import_line_re.search(all_text):
            continue

        text_without_import_lines = "\n".join(
            line for line in all_text.splitlines() if not import_line_re.match(line)
        )
        if re.compile(esc + r"\.\w").search(text_without_import_lines):
            used_but_risky.append(mod)
        else:
            unused.append(mod)

    if not unused and not used_but_risky:
        return None

    lines = [
        "Bilgi: game/ klasöründe düz .py dosyası olarak import edilen "
        "modül(ler) var (Android'de ModuleNotFoundError'a yol açabiliyor). "
        "Oyun kodunuz DEĞİŞTİRİLMEDİ:"
    ]
    if unused:
        mods = ", ".join(f'"{m}"' for m in unused)
        lines.append(f"  - {mods}: import ediliyor ama kullanıldığına dair iz yok.")
    if used_but_risky:
        mods = ", ".join(f'"{m}"' for m in used_but_risky)
        lines.append(
            f"  - {mods}: import ediliyor VE kullanılıyor. Sorun çıkarsa "
            'dosyayı "{isim}_ren.py" olarak yeniden adlandırmayı deneyin.'
        )
    return "\n".join(lines)


_TRACEBACK_BLOCK_RE = re.compile(
    r"Full traceback:\n((?:.*\n)*?)^(\S[\w.]*(?:Error|Exception|Warning)\b.*)$",
    re.MULTILINE,
)


def _find_likely_root_cause(full_log: str) -> Optional[str]:
    """
    Uzun bir derleme günlüğünde, jenerik kapanış hatasının arkasında gizli
    kalan asıl Python hatasını bulmaya çalışır.
    """
    blocks = list(_TRACEBACK_BLOCK_RE.finditer(full_log))
    if not blocks:
        return None

    candidate = None
    for m in blocks:
        body, exc_line = m.group(1), m.group(2)
        if "Could not get build data" in exc_line or "project runs" in exc_line:
            continue
        candidate = (body, exc_line)

    if candidate is None:
        return None

    body, exc_line = candidate
    game_file_match = None
    for gm in re.finditer(r'File "([^"]+)", line (\d+)', body):
        path = gm.group(1)
        if path.startswith("game/"):
            game_file_match = (path, gm.group(2))

    if game_file_match:
        return f"{exc_line.strip()}  (konum: {game_file_match[0]}:{game_file_match[1]})"
    return exc_line.strip()


# Geçici ağ arızalarının imzaları. Bunlardan biri görülürse derlemeyi
# yeniden denemek mantıklıdır; proje kaynaklı hatalarda tekrar denemek
# yalnızca zaman kaybettirir.
_NETWORK_FAILURE_PATTERNS = [
    re.compile(pattern, flags)
    for pattern, flags in (
        (r"Server returned HTTP response code: 5\d\d", re.IGNORECASE),
        (r"\b(502|503|504)\b.*(gateway|timeout|unavailable)", re.IGNORECASE),
        (r"gateway time-?out", re.IGNORECASE),
        (r"Could not (download|resolve)", re.IGNORECASE),
        # HTTP fiilleri BÜYÜK harfle eşleşmeli: harf duyarsız bir kural,
        # Ren'Py'nin "Could not get build data from the project" mesajını
        # (ki bu tamamen proje kaynaklı bir hatadır) ağ arızası sanır ve
        # hem boşuna yeniden dener hem de kullanıcıyı yanlış yönlendirir.
        (r"Could not (GET|HEAD|PUT|POST)\b", 0),
        (r"Connection (reset|refused|timed out)", re.IGNORECASE),
        (r"Read timed out", re.IGNORECASE),
        (r"UnknownHostException", re.IGNORECASE),
        (r"Network is unreachable", re.IGNORECASE),
        (r"Failed to connect to", re.IGNORECASE),
        (r"gradle-[\d.]+-(bin|all)\.zip", re.IGNORECASE),
        (r"SSLException|SSLHandshakeException", re.IGNORECASE),
        (r"Premature end of Content-Length", re.IGNORECASE),
    )
]


def _looks_like_network_failure(log_text: str) -> Optional[str]:
    """Günlükte geçici ağ arızası imzası varsa ilk eşleşmeyi döner."""
    # Yalnızca son kısma bakıyoruz: hata genelde sonda olur ve tüm günlüğü
    # taramak büyük derlemelerde gereksiz yere pahalıdır.
    tail = log_text[-20000:]
    for pattern in _NETWORK_FAILURE_PATTERNS:
        match = pattern.search(tail)
        if match:
            return match.group(0)
    return None


# ===========================================================================
# Oyun kimliği kaydı (riaslink_oyun_001, 002, ...)
# ===========================================================================

_registry_lock = threading.Lock()


def _load_registry() -> dict:
    try:
        data = json.loads(GAME_ID_REGISTRY.read_text(encoding="utf-8"))
        if isinstance(data, dict):
            data.setdefault("assignments", {})
            data.setdefault("used", [])
            return data
    except (OSError, ValueError):
        pass
    return {"assignments": {}, "used": []}


def _save_registry(data: dict) -> None:
    try:
        GAME_ID_REGISTRY.parent.mkdir(parents=True, exist_ok=True)
        GAME_ID_REGISTRY.write_text(json.dumps(data, indent=2), encoding="utf-8")
    except OSError:
        pass


def _format_game_id(number: int) -> str:
    return f"{GAME_ID_PREFIX}{number:03d}"


def _next_free_number(used: Iterable[str]) -> int:
    """
    Kullanılmamış en küçük sırayı bulur.

    Daha önce verilmiş bir kimlik ASLA yeniden verilmez; sunucudaki oynama
    süresi kayıtları oyun kimliğine göre tutulduğu için, aynı kimliği iki
    farklı oyuna vermek iki oyunun sürelerini birbirine karıştırırdı.
    """
    taken = set()
    pattern = re.compile(rf"^{re.escape(GAME_ID_PREFIX)}(\d+)$")
    for item in used:
        match = pattern.match(item or "")
        if match:
            taken.add(int(match.group(1)))

    number = 1
    while number in taken:
        number += 1
    return number


def peek_next_game_id(package: Optional[str] = None) -> dict:
    """Bir sonraki kimliği REZERVE ETMEDEN önerir (arayüzün ön izlemesi)."""
    with _registry_lock:
        registry = _load_registry()
        if package and package in registry["assignments"]:
            return {
                "game_id": registry["assignments"][package],
                "reused": True,
                "package": package,
            }
        return {
            "game_id": _format_game_id(_next_free_number(registry["used"])),
            "reused": False,
            "package": package,
        }


def assign_game_id(package: str, requested: Optional[str] = None) -> tuple[str, str]:
    """
    Bu paket için kalıcı bir oyun kimliği belirler ve kaydeder.

    Döner: (kimlik, açıklama). Açıklama, kimliğin nasıl seçildiğini
    günlükte göstermek içindir.
    """
    requested = (requested or "").strip()

    with _registry_lock:
        registry = _load_registry()
        assignments = registry["assignments"]
        used = registry["used"]

        if requested:
            existing_owner = next(
                (pkg for pkg, gid in assignments.items() if gid == requested and pkg != package),
                None,
            )
            assignments[package] = requested
            if requested not in used:
                used.append(requested)
            _save_registry(registry)

            if existing_owner:
                note = (
                    f'UYARI: "{requested}" kimliği daha önce başka bir pakete '
                    f'("{existing_owner}") verilmişti. Elle girdiğiniz için yine de '
                    "kullanılıyor, ama iki oyunun oynama süreleri sunucuda "
                    "birbirine karışacaktır."
                )
            else:
                note = "arayüzden elle girildi"
            return requested, note

        if package in assignments:
            return assignments[package], "bu paket için daha önce atanmıştı, aynısı korunuyor"

        game_id = _format_game_id(_next_free_number(used))
        assignments[package] = game_id
        used.append(game_id)
        _save_registry(registry)
        return game_id, "yeni kimlik olarak otomatik atandı"


# ===========================================================================
# İmzalama anahtarı
# ===========================================================================

_keystore_lock = threading.Lock()

_KEYTOOL_DNAME = "CN=RenpyAndroidBuilder, OU=Dev, O=Dev, L=NA, S=NA, C=US"


def _run_keytool(keystore_path: Path, alias: str, password: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        [
            "keytool", "-genkeypair", "-v",
            "-keystore", str(keystore_path),
            "-alias", alias,
            "-keyalg", "RSA", "-keysize", "2048", "-validity", "10000",
            "-storepass", password,
            "-keypass", password,
            "-dname", _KEYTOOL_DNAME,
        ],
        capture_output=True,
        text=True,
    )


def get_or_create_auto_keystore() -> tuple[Path, str, str, bool]:
    """
    Otomatik imzalama anahtarını döner; yoksa bir kez üretip saklar.

    Kritik nokta: bu anahtar KALICIDIR. Eski sürümde her derlemede rastgele
    bir anahtar üretiliyordu ve bu, arka arkaya alınan iki APK'nin
    birbirinin üzerine kurulamaması demekti. Artık aynı anahtar tekrar
    kullanıldığı için güncellemeler sorunsuz kurulur.

    Döner: (yol, alias, şifre, yeni_mi_üretildi)
    """
    with _keystore_lock:
        SIGNING_DIR.mkdir(parents=True, exist_ok=True)

        if AUTO_KEYSTORE.is_file() and AUTO_KEYSTORE_META.is_file():
            try:
                meta = json.loads(AUTO_KEYSTORE_META.read_text(encoding="utf-8"))
                return AUTO_KEYSTORE, meta["alias"], meta["password"], False
            except (OSError, ValueError, KeyError):
                # Bozuk meta: yeniden üretmek zorundayız.
                AUTO_KEYSTORE.unlink(missing_ok=True)

        alias = "renpyauto"
        password = secrets.token_urlsafe(18)
        result = _run_keytool(AUTO_KEYSTORE, alias, password)
        if result.returncode != 0 or not AUTO_KEYSTORE.is_file():
            raise RuntimeError(
                "Otomatik imza anahtarı üretilemedi: "
                + (result.stderr or result.stdout or "bilinmeyen hata")[-400:]
            )

        AUTO_KEYSTORE_META.write_text(
            json.dumps({"alias": alias, "password": password, "created": time.time()}, indent=2),
            encoding="utf-8",
        )
        return AUTO_KEYSTORE, alias, password, True


# ===========================================================================
# Ren'Py SDK / AeroKey yapılandırması
# ===========================================================================

def _sdk_roots() -> list[Path]:
    try:
        return patch_rapt.find_sdk_roots()
    except Exception:  # noqa: BLE001
        return []


def stamp_aerokey_config(
    values: dict, banner_source: Optional[Path] = None
) -> list[str]:
    """
    AeroKey yapılandırmasını, kurulu TÜM Ren'Py SDK'larına damgalar.

    Kullanıcı imaja gömülü olandan farklı bir Ren'Py sürümü seçtiyse o sürüm
    çalışma anında indirilir ve henüz yamalı değildir; bu yüzden burada
    önce yamayı uygular, sonra yapılandırmayı yazarız.
    """
    roots = _sdk_roots()
    if not roots:
        # Bunu sessizce geçmek, AeroKey'siz bir APK üretip "eklendi" demek
        # olurdu; kullanıcı bunu ancak oyunu açtığında fark ederdi.
        raise RuntimeError(
            "Hiçbir Ren'Py SDK kurulumu bulunamadı, bu yüzden lisans ekranı "
            "Android projesine yerleştirilemez."
        )

    written: list[str] = []
    for sdk in roots:
        try:
            # Yama zaten uygulanmışsa fonksiyonlar kendiliğinden atlar.
            patch_rapt.apply_all(sdk, skip_gradle_warm=True)
            for path in patch_rapt.stamp_config(sdk, values, banner_source):
                written.append(str(path))
        except patch_rapt.PatchError as exc:
            raise RuntimeError(f"{sdk} yamalanamadı: {exc}") from exc

    if not written:
        raise RuntimeError(
            "Lisans ekranı yapılandırması hiçbir SDK'ya yazılamadı."
        )
    return written


# ===========================================================================
# İş (job) yönetimi
# ===========================================================================

@dataclass
class BuildJob:
    id: str
    status: str = "queued"  # queued | running | success | error
    lines: list[str] = field(default_factory=list)
    files: list[Path] = field(default_factory=list)
    created_at: float = field(default_factory=time.time)
    lock: threading.Lock = field(default_factory=threading.Lock)

    def log(self, message: str) -> None:
        with self.lock:
            for line in str(message).splitlines() or [""]:
                self.lines.append(line)

    def snapshot(self, from_index: int) -> tuple[list[str], str, list[str]]:
        with self.lock:
            return (
                self.lines[from_index:],
                self.status,
                [p.name for p in self.files],
            )

    def full_log(self) -> str:
        with self.lock:
            return "\n".join(self.lines)


JOBS: dict[str, BuildJob] = {}
_jobs_lock = threading.Lock()
# Android/Gradle derlemeleri ağırdır; dahası RAPT tüm oyunlar için TEK bir
# `rapt/project/` çalışma dizini kullandığından, aynı anda iki derleme
# birbirinin dosyalarını bozar. Bu kilit bir zarafet tercihi değil,
# doğruluk şartıdır.
_build_lock = threading.Lock()


def _register_job() -> BuildJob:
    job = BuildJob(id=uuid.uuid4().hex[:12])
    with _jobs_lock:
        # Eski işleri hafızadan düşür (dosyaları diskte kalmaya devam eder,
        # onları _cleanup_old_dirs temizler).
        stale = [
            jid for jid, j in JOBS.items()
            if time.time() - j.created_at > 6 * 3600
        ]
        for jid in stale:
            JOBS.pop(jid, None)
        JOBS[job.id] = job
    return job


def _stream_subprocess(cmd, job: BuildJob, cwd=None, env=None) -> int:
    """Komutu çalıştırır, çıktısını satır satır işin günlüğüne yazar."""
    try:
        process = subprocess.Popen(
            cmd,
            cwd=cwd,
            env=env,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            bufsize=1,
        )
    except FileNotFoundError:
        # Çıplak bir FileNotFoundError, hangi programın eksik olduğunu
        # söylemediği için okuyanı yanıltır; açıkça yazıyoruz.
        job.log(
            f"Hata: '{cmd[0]}' komutu bulunamadı. Bu araç, Docker imajında "
            "kurulu olan renkit'e (renutil + renconstruct) ihtiyaç duyar — "
            "imajın Dockerfile ile doğru şekilde derlendiğinden emin olun."
        )
        return 127
    assert process.stdout is not None
    for line in process.stdout:
        job.log(line.rstrip("\n"))
    process.wait()
    return process.returncode


@dataclass
class BuildRequest:
    """Arayüzden gelen, tek bir derlemeyi tanımlayan tüm girdiler."""
    zip_path: Path
    icon_path: Optional[Path]
    banner_path: Optional[Path]
    keystore_path: Optional[Path]
    renpy_version: str
    want_apk: bool
    want_aab: bool
    package_prefix: str
    manual_name: str
    manual_package: str
    manual_version: str
    keystore_alias: str
    keystore_password: str
    aerokey_enabled: bool
    aerokey_base_url: str
    aerokey_key_page: str
    aerokey_game_id: str
    aerokey_leaderboard: bool
    aerokey_survey: bool
    aerokey_profile: bool
    aerokey_bug_report: bool
    aerokey_notifications: bool


def run_build(job: BuildJob, req: BuildRequest) -> None:
    """Derlemeyi baştan sona yürütür (arka plan iş parçacığında çalışır)."""
    job_dir = Path(tempfile.mkdtemp(prefix="job_", dir=WORK_ROOT))
    project_extract_dir = job_dir / "extracted"
    # Çıktı klasörü kasıtlı olarak job_dir'in DIŞINDA: derleme bitince
    # job_dir silinse de üretilen dosyalar indirilebilir kalsın.
    out_dir = RESULTS_ROOT / job.id
    out_dir.mkdir(parents=True, exist_ok=True)

    try:
        job.status = "running"
        job.log("Sıraya alındı, derleme yuvası bekleniyor…")

        with _build_lock:
            job.log("Derleme başlıyor.\n")
            _execute_build(job, req, job_dir, project_extract_dir, out_dir)

    except Exception as exc:  # noqa: BLE001
        job.log(f"\nBeklenmeyen bir hata oluştu: {exc!r}")
        job.status = "error"
    finally:
        if job.status == "running":
            job.status = "error"
        shutil.rmtree(job_dir, ignore_errors=True)
        for temp_input in (
            req.zip_path, req.icon_path, req.banner_path, req.keystore_path
        ):
            if temp_input is not None:
                try:
                    Path(temp_input).unlink(missing_ok=True)
                except OSError:
                    pass


def _execute_build(
    job: BuildJob,
    req: BuildRequest,
    job_dir: Path,
    project_extract_dir: Path,
    out_dir: Path,
) -> None:
    job.log("Proje ZIP dosyası açılıyor…")
    try:
        with zipfile.ZipFile(req.zip_path) as zf:
            zf.extractall(project_extract_dir)
    except zipfile.BadZipFile:
        job.log("Hata: Yüklenen dosya geçerli bir ZIP arşivi değil.")
        job.status = "error"
        return

    project_root = _find_project_root(project_extract_dir)
    if project_root is None:
        job.log(
            "Hata: ZIP içinde bir 'game/' klasörü bulunamadı.\n"
            "Lütfen Ren'Py proje klasörünü (içinde 'game' klasörü olan "
            "klasörü) doğrudan zip'leyip tekrar deneyin."
        )
        job.status = "error"
        return
    job.log(f"Proje bulundu: {project_root.relative_to(job_dir)}")

    # --- Derlenmiş dağıtım paketi mi? ------------------------------------
    dist = _detect_distribution_build(project_root)
    if dist.is_distribution:
        job.log(
            "\nBu yükleme, ham proje kaynağı değil DERLENMİŞ bir dağıtım "
            "paketi gibi görünüyor:"
        )
        for signal in dist.signals:
            job.log(f"  - {signal}")
        job.log(
            "Bu desteklenen bir durumdur; derlemeye devam ediliyor. Ancak "
            ".rpy kaynakları bulunmadığı için uygulama adı/paket/sürüm "
            "otomatik okunamayabilir — arayüzdeki 'Derlenmiş proje' "
            "alanlarını doldurmanız önerilir.\n"
        )
        cleanup_msg = _strip_desktop_extras(project_root)
        if cleanup_msg:
            job.log(cleanup_msg)

    # --- Kimlik ----------------------------------------------------------
    identity = _resolve_identity(
        project_root,
        req.package_prefix,
        req.manual_name,
        req.manual_package,
        req.manual_version,
    )
    job.log(
        f'\nUygulama kimliği:\n'
        f'  - ad     : "{identity.name}"  ({identity.name_source})\n'
        f'  - paket  : "{identity.package}"  ({identity.package_source})\n'
        f'  - sürüm  : "{identity.version}"  ({identity.version_source})'
    )

    dirname_fix = _fix_build_directory_name(project_root)
    if dirname_fix:
        job.log(dirname_fix)

    android_json_msg = _ensure_android_json(
        project_root,
        identity,
        need_internet=req.aerokey_enabled,
        need_notifications=req.aerokey_enabled and req.aerokey_notifications,
    )
    if android_json_msg:
        job.log(android_json_msg)

    icon_msg = _prepare_android_icon(
        project_root, str(req.icon_path) if req.icon_path else None
    )
    if icon_msg:
        job.log(icon_msg)

    py_import_msg = _scan_local_py_imports(project_root)
    if py_import_msg:
        job.log(py_import_msg)

    # --- Ren'Py sürümü ---------------------------------------------------
    if req.renpy_version != DEFAULT_RENPY_VERSION:
        job.log(
            f"\nİmaja gömülü sürüm ({DEFAULT_RENPY_VERSION}) ile istenen sürüm "
            f"({req.renpy_version}) farklı. '{req.renpy_version}' indiriliyor — "
            "bu birkaç dakika sürebilir…"
        )
        code = _stream_subprocess(["renutil", "install", req.renpy_version], job)
        if code != 0:
            job.log(
                f"Hata: 'renutil install {req.renpy_version}' başarısız oldu (kod {code}). "
                "Sürüm numarasını kontrol edin (https://www.renpy.org/release_list.html)."
            )
            job.status = "error"
            return

    # --- AeroKey ---------------------------------------------------------
    if req.aerokey_enabled:
        game_id, id_note = assign_game_id(identity.package, req.aerokey_game_id)
        banner_source = req.banner_path or _find_bundled_banner()
        if banner_source is None:
            banner_note = "yok (giriş ekranı afişsiz çizilecek)"
        else:
            origin = "bu derleme için yüklendi" if req.banner_path else "Space imajına gömülü"
            banner_note = f'"{banner_source.name}" ({origin})'
        job.log(
            f"\nAeroKey lisans ekranı ETKİN.\n"
            f"  - sunucu   : {req.aerokey_base_url}\n"
            f'  - oyun_id  : "{game_id}"  ({id_note})\n'
            f"  - afiş     : {banner_note}"
        )
        try:
            stamp_aerokey_config(
                {
                    "ENABLED": True,
                    "BASE_URL": req.aerokey_base_url.rstrip("/"),
                    "KEY_PAGE_URL": req.aerokey_key_page,
                    "GAME_ID": game_id,
                    "GAME_TITLE": identity.name,
                    "FEATURE_LEADERBOARD": req.aerokey_leaderboard,
                    "FEATURE_SURVEY": req.aerokey_survey,
                    "FEATURE_PROFILE": req.aerokey_profile,
                    "FEATURE_BUG_REPORT": req.aerokey_bug_report,
                    "NOTIFICATIONS_ENABLED": req.aerokey_notifications,
                },
                banner_source=banner_source,
            )
        except RuntimeError as exc:
            job.log(f"Hata: AeroKey entegrasyonu uygulanamadı.\n{exc}")
            job.status = "error"
            return
        job.log("  - giriş ekranı Android projesine yerleştirildi.")
    else:
        job.log("\nAeroKey lisans ekranı devre dışı (uygulama doğrudan oyuna açılır).")
        try:
            stamp_aerokey_config({"ENABLED": False})
        except RuntimeError as exc:
            job.log(f"Uyarı: AeroKey kapatma bayrağı yazılamadı: {exc}")

    # --- İmzalama --------------------------------------------------------
    keystore_provided = bool(
        req.keystore_path and req.keystore_alias and req.keystore_password
    )
    if keystore_provided:
        ks_path = Path(req.keystore_path)
        alias_final = req.keystore_alias
        password_final = req.keystore_password
        job.log("\nİmzalama: yüklediğiniz özel anahtar kullanılacak.")
    else:
        try:
            ks_path, alias_final, password_final, created = get_or_create_auto_keystore()
        except RuntimeError as exc:
            job.log(f"\nHata: {exc}")
            job.status = "error"
            return

        if created:
            job.log(
                "\nİmzalama: kalıcı otomatik imza anahtarı ilk kez üretildi ve "
                "saklandı. Bundan sonraki tüm derlemeler aynı anahtarla "
                "imzalanacak, yani ürettiğiniz APK'ler birbirinin üzerine "
                "sorunsuz kurulur."
            )
        else:
            job.log("\nİmzalama: saklı otomatik imza anahtarı kullanılıyor (her derlemede aynı).")

        if not DATA_IS_PERSISTENT:
            job.log(
                "  UYARI: Kalıcı disk bulunamadığı için bu anahtar Space "
                "yeniden başlatılınca kaybolur. 'Anahtarı indir' düğmesiyle "
                "yedekleyin ya da Space ayarlarından kalıcı disk açın."
            )

    with open(ks_path, "rb") as f:
        keystore_b64 = base64.b64encode(f.read()).decode("ascii")

    # --- renconstruct yapılandırması -------------------------------------
    config_path = job_dir / "renconstruct.toml"
    toml_lines = [
        "[build]",
        f"android_apk = {str(bool(req.want_apk)).lower()}",
        f"android_aab = {str(bool(req.want_aab)).lower()}",
        "pc = false",
        "win = false",
        "linux = false",
        "mac = false",
        "web = false",
        "steam = false",
        "market = false",
        "",
        "[renutil]",
        f'version = "{req.renpy_version}"',
        "",
        "[options]",
        "clear_output_dir = true",
        "",
        # renconstruct, Android hedeflerinde keystore görevinin AKTİF olmasını
        # VE anahtar verisinin TOML içinde bulunmasını zorunlu tutuyor.
        "[tasks.keystore]",
        'type = "keystore"',
        "enabled = true",
        'on_builds = ["android_apk", "android_aab"]',
        f'alias = "{alias_final}"',
        f'password = "{password_final}"',
        f'keystore_apk = "{keystore_b64}"',
        f'keystore_aab = "{keystore_b64}"',
    ]
    config_path.write_text("\n".join(toml_lines) + "\n", encoding="utf-8")

    job.log(
        "\nAndroid derlemesi başlatılıyor. İlk çalıştırmada Android SDK "
        "bileşenlerinin indirilmesi nedeniyle bu adım uzun sürebilir; "
        "sonraki derlemeler daha hızlı olacaktır.\n"
    )

    cmd = [
        "renconstruct", "build",
        "-c", str(config_path),
        str(project_root), str(out_dir),
    ]

    # --- Derleme + ağ hatalarında otomatik yeniden deneme -----------------
    max_attempts = 3
    return_code = None
    for attempt in range(1, max_attempts + 1):
        marker = len(job.lines)
        return_code = _stream_subprocess(cmd, job)
        if return_code == 0:
            break

        attempt_log = "\n".join(job.lines[marker:])
        network_hint = _looks_like_network_failure(attempt_log)
        if not network_hint or attempt == max_attempts:
            break

        delay = 5 * attempt
        job.log(
            f"\nGeçici bir ağ arızası tespit edildi ({network_hint!r}). "
            "Bu hata projenizle ilgili değildir — Gradle/Android SDK "
            f"indirmesi sırasında sunucuya ulaşılamamış. {delay} saniye "
            f"beklenip yeniden denenecek (deneme {attempt + 1}/{max_attempts})…\n"
        )
        time.sleep(delay)

    if return_code != 0:
        full_log = job.full_log()
        network_hint = _looks_like_network_failure(full_log)
        if network_hint:
            job.log(
                f"\nDerleme HATA ile sonuçlandı (kod {return_code}).\n"
                f"Sebep büyük olasılıkla geçici bir AĞ SORUNU: {network_hint!r}\n"
                "Gradle ya da Android SDK bileşenleri indirilemedi. Projenizde "
                "bir sorun olduğu anlamına gelmez — birkaç dakika sonra tekrar "
                f"deneyin ({max_attempts} otomatik deneme zaten yapıldı)."
            )
        else:
            root_cause = _find_likely_root_cause(full_log)
            hint = (
                f"\nOlası kök neden (otomatik tespit, kesin teşhis değildir): "
                f"{root_cause}\nBu genelde projenizin kendi başlangıç kodundaki "
                "bir hatadan kaynaklanır.\n"
                if root_cause
                else ""
            )
            job.log(
                f"\nDerleme HATA ile sonuçlandı (kod {return_code}).\n{hint}"
                "Yukarıdaki günlüğü inceleyin; README'deki 'Sorun Giderme' "
                "bölümüne de bakabilirsiniz."
            )
        job.status = "error"
        return

    result_files = sorted(
        p for p in out_dir.rglob("*") if p.suffix.lower() in (".apk", ".aab")
    )
    if not result_files:
        job.log(
            "\nDerleme hata vermeden bitti ama çıktı klasöründe bir .apk/.aab "
            "dosyası bulunamadı. Günlüğü kontrol edin."
        )
        job.status = "error"
        return

    with job.lock:
        job.files = result_files

    names = "\n".join(f"  - {p.name}" for p in result_files)
    job.log(f"\nBitti! {len(result_files)} dosya üretildi:\n{names}")
    job.status = "success"


# ===========================================================================
# HTTP arayüzü
# ===========================================================================

app = FastAPI(title="Ren'Py Android Paketleyici")

if WEB_DIR.is_dir():
    app.mount("/static", StaticFiles(directory=str(WEB_DIR)), name="static")


def _bool_form(value: Optional[str]) -> bool:
    return str(value).lower() in ("1", "true", "on", "yes")


async def _save_upload(upload: Optional[UploadFile], suffix: str) -> Optional[Path]:
    """Yüklenen dosyayı diske alır; boş yükleme None döner."""
    if upload is None or not upload.filename:
        return None
    fd, temp_path = tempfile.mkstemp(suffix=suffix, dir=WORK_ROOT)
    os.close(fd)
    path = Path(temp_path)
    with path.open("wb") as target:
        while True:
            chunk = await upload.read(1024 * 1024)
            if not chunk:
                break
            target.write(chunk)
    await upload.close()
    return path


@app.get("/", response_class=HTMLResponse)
async def index() -> HTMLResponse:
    index_path = WEB_DIR / "index.html"
    if not index_path.is_file():
        raise HTTPException(status_code=500, detail="Arayüz dosyası bulunamadı.")
    return HTMLResponse(index_path.read_text(encoding="utf-8"))


@app.get("/api/config")
async def api_config() -> JSONResponse:
    return JSONResponse(
        {
            "renpy_version": DEFAULT_RENPY_VERSION,
            "aerokey_base_url": DEFAULT_AEROKEY_BASE_URL,
            "aerokey_key_page": DEFAULT_AEROKEY_KEY_PAGE,
            "game_id_prefix": GAME_ID_PREFIX,
            "suggested_game_id": peek_next_game_id()["game_id"],
            "persistent_storage": DATA_IS_PERSISTENT,
            "auto_keystore_exists": AUTO_KEYSTORE.is_file(),
        }
    )


@app.get("/api/game-id")
async def api_game_id(package: str = "") -> JSONResponse:
    return JSONResponse(peek_next_game_id(package.strip() or None))


@app.get("/api/game-ids")
async def api_game_ids() -> JSONResponse:
    """Şimdiye kadar atanmış tüm oyun kimlikleri (arayüzdeki liste için)."""
    with _registry_lock:
        registry = _load_registry()
    entries = [
        {"package": pkg, "game_id": gid}
        for pkg, gid in sorted(registry["assignments"].items(), key=lambda kv: kv[1])
    ]
    return JSONResponse({"entries": entries, "used": sorted(registry["used"])})


@app.get("/api/keystore/auto")
async def api_download_auto_keystore():
    """
    Otomatik imza anahtarını indirir.

    Kullanıcı bunu yedeklemelidir: Space'in kalıcı diski yoksa anahtar
    yeniden başlatmada kaybolur ve daha önce yayınlanan APK'ler bir daha
    güncellenemez.
    """
    try:
        path, alias, password, _ = get_or_create_auto_keystore()
    except RuntimeError as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc

    return FileResponse(
        str(path),
        filename="renpy-porter-auto.keystore",
        media_type="application/octet-stream",
        headers={"X-Keystore-Alias": alias, "X-Keystore-Password": password},
    )


@app.get("/api/keystore/auto/info")
async def api_auto_keystore_info() -> JSONResponse:
    try:
        _, alias, password, created = get_or_create_auto_keystore()
    except RuntimeError as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc
    return JSONResponse(
        {
            "alias": alias,
            "password": password,
            "just_created": created,
            "persistent": DATA_IS_PERSISTENT,
        }
    )


@app.post("/api/build")
async def api_build(
    project_zip: UploadFile = File(...),
    icon: Optional[UploadFile] = File(None),
    banner: Optional[UploadFile] = File(None),
    keystore: Optional[UploadFile] = File(None),
    renpy_version: str = Form(DEFAULT_RENPY_VERSION),
    want_apk: str = Form("true"),
    want_aab: str = Form("false"),
    package_prefix: str = Form("com.riaslinkfun"),
    manual_name: str = Form(""),
    manual_package: str = Form(""),
    manual_version: str = Form(""),
    keystore_alias: str = Form(""),
    keystore_password: str = Form(""),
    aerokey_enabled: str = Form("false"),
    aerokey_base_url: str = Form(DEFAULT_AEROKEY_BASE_URL),
    aerokey_key_page: str = Form(DEFAULT_AEROKEY_KEY_PAGE),
    aerokey_game_id: str = Form(""),
    aerokey_leaderboard: str = Form("false"),
    aerokey_survey: str = Form("false"),
    aerokey_profile: str = Form("false"),
    aerokey_bug_report: str = Form("false"),
    aerokey_notifications: str = Form("false"),
) -> JSONResponse:
    version = (renpy_version or "").strip() or DEFAULT_RENPY_VERSION
    if not VERSION_RE.match(version):
        raise HTTPException(
            status_code=400,
            detail=f"'{version}' geçerli bir Ren'Py sürüm numarası değil. Örnek: 8.5.3",
        )

    apk = _bool_form(want_apk)
    aab = _bool_form(want_aab)
    if not apk and not aab:
        raise HTTPException(
            status_code=400, detail="En az bir çıktı formatı seçmelisiniz (APK ve/veya AAB)."
        )

    zip_path = await _save_upload(project_zip, ".zip")
    if zip_path is None:
        raise HTTPException(status_code=400, detail="Bir Ren'Py proje ZIP dosyası yükleyin.")

    icon_path = await _save_upload(icon, ".img")
    banner_path = await _save_upload(banner, ".gif")
    keystore_path = await _save_upload(keystore, ".keystore")

    request = BuildRequest(
        zip_path=zip_path,
        icon_path=icon_path,
        banner_path=banner_path,
        keystore_path=keystore_path,
        renpy_version=version,
        want_apk=apk,
        want_aab=aab,
        package_prefix=package_prefix,
        manual_name=manual_name,
        manual_package=manual_package,
        manual_version=manual_version,
        keystore_alias=keystore_alias.strip(),
        keystore_password=keystore_password.strip(),
        aerokey_enabled=_bool_form(aerokey_enabled),
        aerokey_base_url=(aerokey_base_url or DEFAULT_AEROKEY_BASE_URL).strip(),
        aerokey_key_page=(aerokey_key_page or DEFAULT_AEROKEY_KEY_PAGE).strip(),
        aerokey_game_id=aerokey_game_id.strip(),
        aerokey_leaderboard=_bool_form(aerokey_leaderboard),
        aerokey_survey=_bool_form(aerokey_survey),
        aerokey_profile=_bool_form(aerokey_profile),
        aerokey_bug_report=_bool_form(aerokey_bug_report),
        aerokey_notifications=_bool_form(aerokey_notifications),
    )

    job = _register_job()
    threading.Thread(
        target=run_build, args=(job, request), daemon=True, name=f"build-{job.id}"
    ).start()

    return JSONResponse({"job_id": job.id})


@app.get("/api/jobs/{job_id}/stream")
async def api_job_stream(job_id: str, request: Request) -> StreamingResponse:
    job = JOBS.get(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="İş bulunamadı.")

    async def event_source():
        index = 0
        # Basit anket (polling) döngüsü: iş parçacıkları arası kuyruk
        # kurmaya gerek bırakmaz ve derleme günlüğü için 250 ms gecikme
        # fazlasıyla yeterlidir.
        while True:
            if await request.is_disconnected():
                return

            lines, status, files = job.snapshot(index)
            index += len(lines)

            for line in lines:
                yield f"event: log\ndata: {json.dumps(line)}\n\n"

            if status in ("success", "error"):
                payload = {
                    "status": status,
                    "files": [
                        {"name": name, "url": f"/api/jobs/{job_id}/files/{i}"}
                        for i, name in enumerate(files)
                    ],
                }
                yield f"event: done\ndata: {json.dumps(payload)}\n\n"
                return

            if not lines:
                # Ara sunucuların bağlantıyı kesmemesi için canlılık sinyali.
                yield ": keep-alive\n\n"

            await asyncio.sleep(0.25)

    return StreamingResponse(
        event_source(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@app.get("/api/jobs/{job_id}/files/{index}")
async def api_job_file(job_id: str, index: int):
    job = JOBS.get(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="İş bulunamadı.")
    with job.lock:
        files = list(job.files)
    if index < 0 or index >= len(files):
        raise HTTPException(status_code=404, detail="Dosya bulunamadı.")
    path = files[index]
    if not path.is_file():
        raise HTTPException(status_code=410, detail="Dosya artık sunucuda yok (temizlenmiş).")
    return FileResponse(
        str(path), filename=path.name, media_type="application/octet-stream"
    )


@app.get("/api/jobs/{job_id}/log")
async def api_job_log(job_id: str):
    """Tüm günlüğü düz metin olarak indirir (hata paylaşmak için kullanışlı)."""
    job = JOBS.get(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="İş bulunamadı.")
    return HTMLResponse(job.full_log(), media_type="text/plain")


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=int(os.environ.get("PORT", "7860")))
