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
import binascii
import hashlib
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
# `translation_pack` takma adı bilinçli: /api/build uç noktasının
# `translation` adlı bir dosya parametresi var ve modülü gölgelerdi.
from aerokey import patch_rapt  # noqa: E402  (yol ayarından sonra gelmeli)
from aerokey import translation as translation_pack  # noqa: E402
from aerokey import rpa as rpa_archive  # noqa: E402
from aerokey import display as virtual_display  # noqa: E402
from aerokey import build_dump  # noqa: E402
from aerokey import resources  # noqa: E402

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


def _resolve_data_dir() -> tuple[Path, bool]:
    """
    Derlemeler arasında KALICI olması gereken veriler (imza anahtarı, oyun
    kimliği kaydı) için yazılabilir bir dizin seçer.

    Döner: (dizin, gerçekten_kalıcı_mı)

    KRİTİK: "yazılabilir" ile "kalıcı" aynı şey DEĞİL. Hugging Face
    Space'inde kalıcı disk yoksa /data yazılamaz ve ev dizinine düşeriz;
    ev dizini yazılabilir ama Space her yeniden başladığında SIFIRLANIR.
    Eski sürüm ikisini ayırmıyordu: ev dizinini "kalıcı" sayıp uyarıyı hiç
    göstermiyordu. Sonuç: her yeniden başlatmada yeni imza anahtarı
    üretiliyor, bu da her oyuna FARKLI bir cihaz kimliği veriyordu
    (ANDROID_ID imza anahtarına bağlıdır) ve oyun kimliği kaydı da
    sıfırlandığı için numaralar yeniden kullanılabiliyordu.

    Yalnızca açıkça yapılandırılmış PORTER_DATA_DIR ve /data kalıcı sayılır.
    """
    explicit = os.environ.get("PORTER_DATA_DIR")
    # (aday, kalıcı sayılır mı)
    candidates: list[tuple[str, bool]] = []
    if explicit:
        candidates.append((explicit, True))
    candidates.append(("/data", True))
    candidates.append((str(Path.home() / ".renpy_porter"), False))

    for candidate, persistent in candidates:
        path = Path(candidate)
        try:
            path.mkdir(parents=True, exist_ok=True)
            probe = path / ".write_test"
            probe.write_text("ok", encoding="utf-8")
            probe.unlink()
            return path, persistent
        except OSError:
            continue

    fallback = Path(tempfile.gettempdir()) / "renpy_porter_data"
    fallback.mkdir(parents=True, exist_ok=True)
    return fallback, False


DATA_DIR, DATA_IS_PERSISTENT = _resolve_data_dir()

# --- Sanal ekran -------------------------------------------------------
# Modül seviyesinde çağırıyoruz ki hem `python3 app.py` hem de
# `uvicorn app:app` yolunda kurulsun. Derleme alt süreçleri os.environ'u
# miras aldığı için DISPLAY'i burada ayarlamak yeterli.
#
# Ren'Py, APK üretmeden ÖNCE projeyi bir kez grafiksel olarak açıp kapatır
# (build meta verisini toplamak için) ve bu adım atlanamaz. Ekran sunucusu
# olmayan bir konteynerde bu adım segfault ile çöker; ayrıntılı gerekçe
# aerokey/display.py başında.
DISPLAY_INFO = virtual_display.ensure_virtual_display()
if DISPLAY_INFO.active:
    print(f"[ekran] Sanal ekran hazır: {DISPLAY_INFO.display} ({DISPLAY_INFO.note})")
else:
    print(f"[ekran] UYARI: sanal ekran kurulamadı. {DISPLAY_INFO.note}", file=sys.stderr)

# --- Steam entegrasyonunu kapat ---------------------------------------
# Ren'Py, python çalıştırılabilirinin YANINDA libsteam_api.so varsa Steam'i
# başlatmayı dener (renpy/common/00steam.rpy):
#
#     dll_path = os.path.join(os.path.dirname(sys.executable), dll_name)
#     has_steam = os.path.exists(dll_path)
#
# Bu dosya Ren'Py SDK'sının kendi lib klasöründe bulunur. Yani PROJENİN
# Steam ile hiçbir ilgisi olmasa bile, bu SDK ile yapılan HER derlemede
# Steam'in yerel (native) kodu çağrılıyor. Steam çalışmayan bir
# konteynerde InitFlat() başarısız oluyor ve süreç bunun ardından
# segfault verebiliyor ("Launch failed (returned -11)") — Python
# seviyesinde hiçbir iz bırakmadan.
#
# Ren'Py bunun için belgelenmiş bir kapı sunuyor: RENPY_NO_STEAM tanımlıysa
# Steam'e hiç dokunulmuyor. Android APK'sında Steam zaten bulunmadığı için
# kapatmak yalnızca güvenli değil, doğru olanı.
#
# Bunu burada ayarlamak yeterli: derleme alt süreçleri (renconstruct ->
# Launcher -> oyun) ortamı miras alıyor ve Launcher alt süreci
# `dict(os.environ)` ile kuruyor.
os.environ["RENPY_NO_STEAM"] = "1"

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


def _extract_rpa_archives(job: BuildJob, project_root: Path) -> bool:
    """
    `game/` altındaki RPA arşivlerini açar.

    Ren'Py arşivi çalışma anında kendisi okuyabilir, yani paketleme için
    açmak şart değil. Bizim hattımız için ŞART, çünkü çeviri kurulumu
    kanca etiketinin boş olduğunu `.rpyc` dosyalarını tarayarak
    doğruluyor: dosyalar arşivin içindeyse tarayıcı hiçbir şey göremez,
    etiketi boş sanar ve aynı etiketi ikinci kez tanımlar — bu da oyunun
    hiç açılmaması demektir.

    Arşiv okunamazsa derlemeyi DURDURUYORUZ: yarım açılmış bir oyunla
    devam etmek sessizce bozuk bir APK üretirdi.

    Döner: devam edilebilir mi.
    """
    game_dir = project_root / "game"
    archives = rpa_archive.find_archives(game_dir)
    if not archives:
        return True

    job.log(
        f"\nSıkıştırılmış oyun verisi bulundu ({len(archives)} arşiv). "
        "Dosyalar açılıp yerine yerleştiriliyor…"
    )

    try:
        results = rpa_archive.extract_all(game_dir, remove=True)
    except rpa_archive.RpaError as exc:
        job.log(
            f"Hata: RPA arşivi açılamadı.\n{exc}\n"
            "Arşiv bozuk ya da desteklenmeyen bir biçimde olabilir. "
            "Oyunu arşivsiz (klasör hâlinde) yükleyip tekrar deneyin."
        )
        job.status = "error"
        return False
    except OSError as exc:
        job.log(f"Hata: RPA arşivi açılırken disk hatası: {exc}")
        job.status = "error"
        return False

    total = 0
    for result in results:
        total += result.files
        detail = f"  - {result.archive.name}: {result.files} dosya"
        if result.overwritten:
            detail += f", {result.overwritten} tanesi zaten mevcut olduğu için atlandı"
        if result.skipped:
            detail += f", {len(result.skipped)} güvensiz ad atlandı"
        job.log(detail)

    job.log(
        f"  Toplam {total} dosya açıldı; arşivler silindi "
        "(aksi halde aynı veri APK'ya iki kez girerdi)."
    )
    return True


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

# Bir ikon kaynağının makul üst sınırı (~8000x8000). Bunun üstü bir ikon
# değil, kazadır: çözüldüğünde gigabaytlarca bellek isteyebilir ve RAPT'ın
# pygame tabanlı ikon üreticisini iz bırakmadan düşürebilir.
_ICON_MAX_SOURCE_PIXELS = 64_000_000


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


def _open_icon_source(path: Path):
    """
    Bir görseli güvenli biçimde açıp RGBA'ya çevirir.

    Piksel sınırı bilinçli: RAPT'ın ikon üreticisi (rapt/iconmaker.py) bu
    dosyaları pygame ile açar — `pygame.image.load` + `convert_alpha` +
    `smoothscale`, hepsi YEREL kod. Bozuk ya da olağandışı büyük bir
    görüntüde bu çağrılar iz bırakmadan çökebilir. Kendi tarafımızda
    Pillow ile açıp yeniden kodlayarak pygame'e her zaman küçük, sağlam ve
    öngörülebilir bir PNG veriyoruz.
    """
    from PIL import Image

    with Image.open(path) as img:
        pixels = img.width * img.height
        if pixels > _ICON_MAX_SOURCE_PIXELS:
            raise ValueError(
                f"{img.width}x{img.height} piksel, bir ikon için makul "
                f"sınırın ({_ICON_MAX_SOURCE_PIXELS:,} piksel) üstünde"
            )
        return img.convert("RGBA")


def _icon_canvas_from(img, fit_ratio: Optional[float]):
    """
    Görseli 432x432'lik bir tuvale yerleştirir.

    `fit_ratio`:
      None  -> tuvali tamamen KAPLA (arka plan katmanı; saydam kenar
               kalması istenmez)
      1.0   -> tuvalin tamamına SIĞDIR, oranı koru, kalanı saydam bırak
               (projenin kendi ön plan katmanını yeniden kodlarken)
      0.66  -> Android'in adaptif ikon güvenli alanına sığdır (rastgele
               bir uygulama görselinden ön plan katmanı ÜRETİRKEN)

    1.0 ile 0.66 ayrımı önemli: zaten 432x432 olan bir katmanı güvenli
    alana sığdırmak onu küçültürdü ve fonksiyon her çalıştığında biraz
    daha küçülürdü. 1.0'da yeniden boyutlandırma etkisizdir, yani işlem
    KARARLIDIR.
    """
    from PIL import Image

    resample = getattr(Image, "Resampling", Image).LANCZOS
    ratio = img.width / img.height

    if fit_ratio is not None:
        safe = int(_ICON_CANVAS * fit_ratio)
        if ratio >= 1:
            new_w, new_h = safe, max(1, int(safe / ratio))
        else:
            new_h, new_w = safe, max(1, int(safe * ratio))
    else:
        if ratio >= 1:
            new_h, new_w = _ICON_CANVAS, max(1, int(_ICON_CANVAS * ratio))
        else:
            new_w, new_h = _ICON_CANVAS, max(1, int(_ICON_CANVAS / ratio))

    resized = img.resize((new_w, new_h), resample)
    canvas = Image.new("RGBA", (_ICON_CANVAS, _ICON_CANVAS), (0, 0, 0, 0))
    canvas.paste(
        resized,
        ((_ICON_CANVAS - new_w) // 2, (_ICON_CANVAS - new_h) // 2),
        resized,
    )
    return canvas


def _prepare_android_icon(
    project_root: Path, uploaded_icon_path: Optional[str]
) -> Optional[str]:
    """
    Ren'Py, Android ikonunu proje kökündeki iki 432x432 PNG dosyasından
    üretir: android-icon_foreground.png ve android-icon_background.png.

    Bu fonksiyon iki iş yapar:

      1. Proje kendi ikon katmanlarını sağlıyorsa onları YENİDEN KODLAR
         (432x432 RGBA PNG). İçerik korunur, yalnızca kap standartlaşır.
      2. Eksik katmanları, yüklenen ya da imaja gömülü görselden üretir.

    (1) neden gerekli: eskiden proje kendi ikonlarını sağladığında bu
    fonksiyon hiç devreye girmiyordu ve o dosyalar olduğu gibi RAPT'ın
    pygame tabanlı ikon üreticisine gidiyordu. Bozuk ya da devasa bir PNG
    orada, Python tarafında hiçbir iz bırakmadan çökebilir. Artık pygame'e
    giden dosya HER ZAMAN bizim ürettiğimiz normalleştirilmiş PNG.

    Not: `project_root` geçici bir kopyadır; kullanıcının özgün dosyaları
    değişmez.
    """
    layers = (
        # (dosya, mevcut katmanı yeniden kodlarken, kaynaktan üretirken, ad)
        (
            project_root / "android-icon_foreground.png",
            1.0,                 # var olanı tuvale sığdır (küçültme YOK)
            _ICON_SAFE_RATIO,    # kaynaktan üretirken güvenli alana çek
            "ön plan",
        ),
        (
            project_root / "android-icon_background.png",
            None,                # var olanı tuvali kaplayacak şekilde ölçekle
            None,                # üretim ayrı ele alınıyor (düz beyaz)
            "arka plan",
        ),
    )

    existing = [path for path, _, _, _ in layers if path.exists()]
    source = Path(uploaded_icon_path) if uploaded_icon_path else _find_bundled_icon()
    have_source = source is not None and source.exists()

    if not existing and not have_source:
        return None  # ne projede ikon var ne de kaynak -> Ren'Py varsayılanı

    try:
        from PIL import Image
    except ImportError:
        if existing:
            # Normalleştiremiyoruz ama projenin kendi dosyaları duruyor;
            # derlemeyi durdurmak yerine riski açıkça bildiriyoruz.
            return (
                "Uyarı: Pillow kurulu olmadığı için projenin ikon dosyaları "
                "doğrulanamadı; Ren'Py onları olduğu gibi kullanacak."
            )
        return (
            "Uyarı: İkon kaynağı bulundu ama Pillow kurulu değil, ikon "
            "üretimi atlandı."
        )

    notes: list[str] = []
    for path, keep_ratio, make_ratio, label in layers:
        try:
            if path.exists():
                # Projenin kendi katmanı: içeriği koru, kabı standartlaştır.
                with _open_icon_source(path) as img:
                    canvas = _icon_canvas_from(img, keep_ratio)
                canvas.save(path)
                notes.append(f"{label}: projenin kendi görseli doğrulandı")
                continue

            if not have_source:
                # Eksik katman + kaynak yok: RAPT kendi şablonundaki
                # varsayılana düşer, bu geçerli bir durumdur.
                continue

            if make_ratio is None:
                # Arka plan için kaynağı büyütmek yerine düz beyaz
                # kullanıyoruz: adaptif ikonlarda arka plan sade olmalı.
                canvas = Image.new(
                    "RGBA", (_ICON_CANVAS, _ICON_CANVAS), (255, 255, 255, 255)
                )
                notes.append(f"{label}: düz beyaz üretildi")
            else:
                with _open_icon_source(source) as img:
                    canvas = _icon_canvas_from(img, make_ratio)
                notes.append(f'{label}: "{source.name}" kaynağından üretildi')
            canvas.save(path)

        except Exception as exc:  # noqa: BLE001
            # Tek bir katmanın başarısız olması derlemeyi düşürmemeli:
            # RAPT eksik katman için kendi şablonundaki varsayılana düşer.
            try:
                path.unlink()
            except OSError:
                pass
            notes.append(
                f"{label}: HATA ({exc}); Ren'Py varsayılanı kullanılacak"
            )

    if not notes:
        return None
    return "Android ikonu (432x432) — " + "; ".join(notes) + "."


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


# Ren'Py Launcher'ın KENDİ dosyaları. Bir yığın izinin en alt satırı
# bunlardan birine düşüyorsa hata kullanıcının oyununda değil, Launcher'ın
# içindedir — "projenizin kodunda hata var" demek yanlış yönlendirme olur.
_LAUNCHER_FILES = (
    "game/interface.rpy",
    "game/project.rpy",
    "game/android.rpy",
    "game/distribute.rpy",
    "game/front_page.rpy",
)

# Ekransız (X sunucusuz) ortamda oluşan çökmenin imzaları.
_HEADLESS_FAILURE_PATTERNS = [
    re.compile(pattern, re.IGNORECASE)
    for pattern in (
        r"OpenGL support is either not configured in SDL",
        r"SDL video driver \(dummy\)",
        r"Could not get pygame screen",
        r"Launch failed \(returned -11\)",
        r"unable to open a display",
        r"Could not initialize SDL",
    )
]


# Steam yerel kodunun konteynerde cokmesinin imzalari.
_STEAM_FAILURE_PATTERNS = [
    re.compile(pattern, re.IGNORECASE)
    for pattern in (
        r"Failed to initialize steam",
        r"SteamAPI_Init\(\)",
        r"steamclient\.so",
    )
]


def _looks_like_steam_failure(log: str) -> Optional[str]:
    """
    Çökmenin arkasında Steam entegrasyonu mu var?

    Ren'Py, SDK'nın lib klasöründe libsteam_api.so bulunca Steam'i
    başlatmayı dener; Steam'siz bir konteynerde bu çağrı başarısız olur ve
    süreç ardından segfault verebilir. Bu, oyuncunun projesiyle ilgili
    DEĞİLDİR ve RENPY_NO_STEAM ile tamamen kapatılır.
    """
    for pattern in _STEAM_FAILURE_PATTERNS:
        match = pattern.search(log)
        if match:
            return match.group(0)
    return None


def _looks_like_headless_failure(log: str) -> Optional[str]:
    """
    Çökme, ekran sunucusu olmamasından mı kaynaklanıyor?

    Ren'Py APK üretmeden önce projeyi bir kez grafiksel olarak açar. Ekran
    sunucusu yoksa SDL "dummy" sürücüsüne düşer, OpenGL bulunmadığı için
    render başlatılamaz ve süreç segfault ile ölür. Bu, kullanıcının
    oyunuyla ilgili DEĞİLDİR; ortam eksikliğidir.
    """
    for pattern in _HEADLESS_FAILURE_PATTERNS:
        match = pattern.search(log)
        if match:
            return match.group(0)
    return None


# RAPT'ın Android paketleme aşamasının adımları, GERÇEKLEŞME SIRASIYLA.
# Kaynak: rapt/buildlib/rapt/build.py -> build(). "[aerokey] adim:" ile
# başlayanlar bizim enjekte ettiğimiz ara işaretçilerdir; ötekiler RAPT'ın
# kendi mesajları.
_RAPT_STEPS = (
    ("Updating project.", "proje güncelleniyor"),
    ("Creating assets directory.", "varlık (assets) klasörü oluşturuluyor"),
    ("Packaging internal data.", "iç veriler paketleniyor"),
    ("[aerokey] adim: private.mp3 arsivi", "private.mp3 arşivi oluşturuluyor"),
    ("[aerokey] adim: private.mp3 ozeti", "private.mp3 özeti (md5) hesaplanıyor"),
    ("[aerokey] adim: sablonlar", "şablonlar işleniyor"),
    ("[aerokey] adim: uygulama ikonu", "uygulama ikonu üretiliyor"),
    ("[aerokey] adim: acilis gorselleri", "açılış görselleri kopyalanıyor"),
    ("I'm using Gradle to build the package.", "Gradle derlemeyi yürütüyor"),
)

# renutil, alt süreç bir SİNYALLE öldüğünde (SIGKILL/SIGSEGV) çıkış kodu
# alamaz ve `.unwrap_or(1)` ile 1 basar. Yani buradaki "Status 1", gerçek
# bir 1 çıkış kodundan ayırt edilemez; ayırt edici olan, günlükte bir
# Python yığın izinin BULUNMAMASIDIR.
# Kaynak: renkit/src/renutil.rs -> launch()
_RENUTIL_STATUS_RE = re.compile(r"Unable to launch Ren'Py: Status (-?\d+)")


def _last_build_step(log: str) -> Optional[str]:
    """
    Paketleme sırasında ulaşılan SON adımı döner.

    Bir sinyal ölümünde Python yığın izi oluşmaz; elimizdeki tek ipucu,
    günlüğe en son hangi adımın yazıldığıdır.
    """
    son = None
    son_konum = -1
    for marker, label in _RAPT_STEPS:
        konum = log.rfind(marker)
        if konum > son_konum:
            son_konum, son = konum, label
    return son


def _looks_like_silent_death(log: str) -> Optional[str]:
    """
    Süreç, Python tarafında iz bırakmadan mı öldü?

    Böyle bir ölümün iki tipik sebebi var:
      * belleğin bitmesi (çekirdeğin OOM-killer'ı SIGKILL gönderir),
      * yerel (native) bir kütüphanenin çökmesi (SIGSEGV).

    İkisi de Python'a hiç uğramaz, bu yüzden yığın izi YOKTUR. Yığın izi
    varsa bu fonksiyon None döner: o zaman gerçek bir Python hatası vardır
    ve onu bildirmek daha doğrudur.
    """
    match = _RENUTIL_STATUS_RE.search(log)
    if not match:
        return None
    if _TRACEBACK_BLOCK_RE.search(log):
        return None
    return match.group(0)


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
        if path.startswith("game/") and path not in _LAUNCHER_FILES:
            game_file_match = (path, gm.group(2))

    if game_file_match:
        return f"{exc_line.strip()}  (konum: {game_file_match[0]}:{game_file_match[1]})"
    return exc_line.strip()


def _root_cause_is_user_project(full_log: str) -> bool:
    """
    Kök neden gerçekten kullanıcının projesinde mi?

    Yığın izinin YALNIZCA Launcher dosyalarına düştüğü durumlarda hatayı
    kullanıcının oyununa yıkmak yanlıştı: örneğin ekransız ortamdaki
    çökmede Launcher kendi hata penceresini çizmeye çalışırken
    `KeyError: \'bottom\'` veriyor ve konum `game/interface.rpy` oluyor.
    """
    for match in re.finditer(r'File "(game/[^"]+)", line \d+', full_log):
        if match.group(1) not in _LAUNCHER_FILES:
            return True
    return False


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


_TRAILING_COMMA_RE = re.compile(r",(\s*[}\]])")


def _quarantine_corrupt_registry(raw: str) -> None:
    """
    Onarılamayan kaydı SİLMEK yerine yanına yedekler.

    Bu kayıt "bir kimlik asla yeniden verilmesin" garantisinin tek
    kaynağıdır. Onu sessizce boşaltıp üzerine yazmak, önceden atanmış
    kimlikleri unutup yeniden dağıtmak demektir — üstelik oyuncuların
    oynama süresi de kimliğe göre tutulduğu için kayıtlar karışır. Bozuk
    dosyayı sakladığımızda en azından elle kurtarılabilir.
    """
    try:
        stamp = time.strftime("%Y%m%d-%H%M%S")
        backup = GAME_ID_REGISTRY.with_name(f"game_ids.json.corrupt-{stamp}")
        backup.write_text(raw, encoding="utf-8")
        print(
            f"[oyun_kimligi] UYARI: {GAME_ID_REGISTRY} okunamadı, boş kayıtla "
            f"başlanıyor. Bozuk içerik {backup} olarak yedeklendi.",
            file=sys.stderr,
        )
    except OSError:
        pass


def _load_registry() -> dict:
    """
    Oyun kimliği kaydını okur.

    Bir JSON söz dizimi hatasında hemen pes ETMİYORUZ: elle düzenlerken en
    sık yapılan hata — dizi/nesnenin son öğesinden sonra fazladan virgül
    bırakmak — otomatik onarılıp dosyaya doğru biçimde geri yazılır. Bu da
    başarısız olursa dosya YOK SAYILMAZ, yedeklenir (bkz.
    _quarantine_corrupt_registry) ki veri sessizce kaybolmasın.
    """
    if not GAME_ID_REGISTRY.is_file():
        return {"assignments": {}, "used": []}

    try:
        raw = GAME_ID_REGISTRY.read_text(encoding="utf-8")
    except OSError:
        return {"assignments": {}, "used": []}

    try:
        data = json.loads(raw)
    except ValueError:
        repaired = _TRAILING_COMMA_RE.sub(r"\1", raw)
        try:
            data = json.loads(repaired)
        except ValueError:
            _quarantine_corrupt_registry(raw)
            return {"assignments": {}, "used": []}
        else:
            if isinstance(data, dict):
                data.setdefault("assignments", {})
                data.setdefault("used", [])
                # Onarılmış biçimi kalıcı olarak geri yaz; aksi halde bir
                # sonraki okuma aynı onarımı sessizce tekrarlar.
                _save_registry(data)
                return data
            _quarantine_corrupt_registry(raw)
            return {"assignments": {}, "used": []}

    if isinstance(data, dict):
        data.setdefault("assignments", {})
        data.setdefault("used", [])
        return data

    _quarantine_corrupt_registry(raw)
    return {"assignments": {}, "used": []}


def _save_registry(data: dict) -> None:
    """
    Kaydı diske yazar.

    Yazma İŞLEM OLARAK ATOMİKTİR: önce geçici bir dosyaya yazılır, sonra
    `os.replace` ile hedefin üzerine taşınır. HF Space'in yazma sırasında
    yeniden başlaması (OOM, yeniden dağıtım) gibi durumlarda düz bir
    `write_text` yarım kalmış/bozuk bir dosya bırakabilirdi; `os.replace`
    tek bir dosya sistemi işlemi olduğu için ya eski içerik ya da tam yeni
    içerik kalır, asla ikisinin karışımı kalmaz.
    """
    try:
        GAME_ID_REGISTRY.parent.mkdir(parents=True, exist_ok=True)
        tmp = GAME_ID_REGISTRY.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
        os.replace(tmp, GAME_ID_REGISTRY)
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


ENV_KEYSTORE_B64 = "AEROKEY_KEYSTORE_B64"
ENV_KEYSTORE_ALIAS = "AEROKEY_KEYSTORE_ALIAS"
ENV_KEYSTORE_PASSWORD = "AEROKEY_KEYSTORE_PASSWORD"


def keystore_fingerprint(path: Path, alias: str, password: str) -> str:
    """
    Anahtarın SHA-256 parmak izi.

    Buna ihtiyaç var çünkü cihaz kimliği (ANDROID_ID) imza anahtarına
    bağlı: anahtar sessizce değişirse tüm oyuncuların kimliği sıfırlanır.
    Parmak izini her derleme günlüğüne yazarak bu değişimi GÖRÜNÜR
    kılıyoruz — iki derlemede farklıysa sebebi hemen anlaşılır.
    """
    try:
        result = subprocess.run(
            ["keytool", "-list", "-v",
             "-keystore", str(path), "-alias", alias, "-storepass", password],
            capture_output=True, text=True, timeout=60,
        )
        for line in (result.stdout or "").splitlines():
            stripped = line.strip()
            if stripped.startswith("SHA256:"):
                return stripped.split("SHA256:", 1)[1].strip()
    except (OSError, subprocess.SubprocessError):
        pass
    # keytool bir sebeple konuşmazsa dosyanın özetine düşeriz; yine de
    # "değişti mi" sorusunu yanıtlar.
    try:
        return "dosya-sha256:" + hashlib.sha256(path.read_bytes()).hexdigest()[:32]
    except OSError:
        return "bilinmiyor"


def _keystore_from_env() -> Optional[tuple[Path, str, str]]:
    """
    Anahtarı ortam değişkeninden (HF Space Secret) yükler.

    Kalıcı disk olmayan Space'lerde TEK güvenilir yol budur: kalıcı disk
    yoksa dosya sistemine yazılan anahtar her yeniden başlatmada kaybolur
    ve yeniden üretilir. Secret ise Space'in kendisinde durur, yeniden
    başlatmadan etkilenmez.
    """
    raw = os.environ.get(ENV_KEYSTORE_B64, "").strip()
    if not raw:
        return None

    alias = os.environ.get(ENV_KEYSTORE_ALIAS, "").strip()
    password = os.environ.get(ENV_KEYSTORE_PASSWORD, "").strip()
    if not alias or not password:
        raise RuntimeError(
            f"{ENV_KEYSTORE_B64} tanımlı ama {ENV_KEYSTORE_ALIAS} ve/veya "
            f"{ENV_KEYSTORE_PASSWORD} eksik. Üçünü birlikte tanımlayın."
        )

    try:
        blob = base64.b64decode(raw, validate=True)
    except (ValueError, binascii.Error) as exc:
        raise RuntimeError(
            f"{ENV_KEYSTORE_B64} geçerli bir base64 değeri değil: {exc}"
        ) from exc

    if not blob:
        raise RuntimeError(f"{ENV_KEYSTORE_B64} boş çözümlendi.")

    SIGNING_DIR.mkdir(parents=True, exist_ok=True)
    target = SIGNING_DIR / "env.keystore"
    target.write_bytes(blob)
    return target, alias, password


def get_or_create_auto_keystore() -> tuple[Path, str, str, bool]:
    """
    Otomatik imzalama anahtarını döner; yoksa bir kez üretip saklar.

    Kritik nokta: bu anahtar KALICI OLMALI. Anahtar değişirse yalnızca
    "APK'ler birbirinin üzerine kurulmaz" olmakla kalmaz — Android'in
    ANDROID_ID değeri imza anahtarına bağlı olduğu için TÜM oyuncuların
    cihaz kimliği ve dolayısıyla profili sıfırlanır.

    Öncelik sırası:
      1. Ortam değişkeni (HF Space Secret) — yeniden başlatmadan etkilenmez
      2. Kalıcı diskteki dosya
      3. Yeni üretim

    Döner: (yol, alias, şifre, yeni_mi_üretildi)
    """
    with _keystore_lock:
        from_env = _keystore_from_env()
        if from_env is not None:
            return from_env[0], from_env[1], from_env[2], False

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
    translation_path: Optional[Path]
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
    translation_mode: str


# Arayüzdeki dil modu değerlerinin okunur karşılıkları.
_TRANSLATION_MODE_LABELS = {
    "ask": "açılışta dil sorulacak",
    "force": "oyun doğrudan çeviri dilinde açılacak",
    "files_only": "yalnızca dosyalar eklenecek, dile dokunulmayacak",
}

# Ren'Py dil kodu -> menüde gösterilecek ad. Listede olmayan bir dil,
# kodunun baş harfi büyütülerek gösterilir.
_LANGUAGE_LABELS = {
    "turkish": "Türkçe",
    "english": "English",
    "german": "Deutsch",
    "french": "Français",
    "spanish": "Español",
    "italian": "Italiano",
    "russian": "Русский",
    "portuguese": "Português",
    "japanese": "日本語",
    "korean": "한국어",
    "schinese": "简体中文",
    "tchinese": "繁體中文",
}


def _install_translation(job: BuildJob, project_root: Path, req: BuildRequest) -> bool:
    """
    Çeviri paketini kurar ve günlüğe ne yapıldığını yazar.

    Başarısızlık ÖLÜMCÜLdür: çeviri isteyip çevirisiz bir APK almak,
    kullanıcının ancak oyunu açınca fark edeceği sessiz bir hata olurdu.
    """
    mode = req.translation_mode
    job.log(
        f"\nÇeviri paketi kuruluyor ({_TRANSLATION_MODE_LABELS.get(mode, mode)})…"
    )
    try:
        result = translation_pack.install_pack(
            project_root,
            Path(req.translation_path),
            mode=mode,
            language_labels=_LANGUAGE_LABELS,
        )
    except translation_pack.TranslationError as exc:
        job.log(f"Hata: Çeviri paketi kurulamadı.\n{exc}")
        return False
    except Exception as exc:  # noqa: BLE001
        job.log(f"Hata: Çeviri paketi kurulurken beklenmeyen bir sorun: {exc!r}")
        return False

    job.log(f"  - diller           : {', '.join(result.languages)}")
    job.log(f"  - kopyalanan dosya : {result.copied_files}")

    for name in result.skipped_loaders:
        job.log(
            f'  - "{name}" ALINMADI: bu betik JSON\'u düz open() ile okuyor, '
            "bu da Android'de çalışmaz (orada oyun verisi Ren'Py'nin varlık "
            "katmanından okunur) ve hatayı sessizce yutuyor. Yerine aynı işi "
            "doğru yapan bir betik üretildi."
        )

    for language, dropped in sorted(result.dropped_dialogue.items()):
        if dropped:
            job.log(
                f"  - {language}: {dropped:,} diyalog kaydı zaten "
                "`translate ... strings:` bloklarıyla karşılanıyor, JSON'a "
                "gerek kalmadı (APK gereksiz yere büyümüyor)."
            )
    for language, extra in sorted(result.extra_dialogue.items()):
        job.log(
            f"  - {language}: {extra:,} diyalog string bloklarında yok, "
            "bunlar için yedek bir filtre eklendi."
        )

    if result.hook_label:
        job.log(
            f"  - dil seçimi `{result.hook_label}` etiketine bağlandı "
            "(oyunda tanımlı olmadığı doğrulandı)."
        )
    if result.forced_language:
        job.log(f'  - oyun doğrudan "{result.forced_language}" dilinde açılacak.')
    for note in result.notes:
        job.log(f"  - UYARI: {note}")

    return True


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
            req.zip_path, req.icon_path, req.banner_path,
            req.translation_path, req.keystore_path,
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

    # --- Sıkıştırılmış oyun verisi ---------------------------------------
    # Kimlik çözümlemesinden ÖNCE açıyoruz: options.rpy arşivin içindeyse
    # açtıktan sonra okunabilir hale gelir.
    if not _extract_rpa_archives(job, project_root):
        return

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

    # --- Çeviri paketi ---------------------------------------------------
    if req.translation_path is not None:
        if not _install_translation(job, project_root, req):
            job.status = "error"
            return

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

        # Avatarlar depodaki aerokey/avatars/ klasöründen gelir; klasör
        # boşsa avatar adımı hiç gösterilmez (rozet kullanılır).
        avatar_sources = patch_rapt.collect_avatar_sources()
        if avatar_sources:
            avatar_note = (
                f"{len(avatar_sources)} adet ("
                + ", ".join(p.name for p in avatar_sources[:4])
                + (", …" if len(avatar_sources) > 4 else "")
                + ")"
            )
        else:
            avatar_note = "yok (adın ilk harfinden rozet üretilecek)"

        job.log(
            f"\nAeroKey lisans ekranı ETKİN.\n"
            f"  - sunucu   : {req.aerokey_base_url}\n"
            f'  - oyun_id  : "{game_id}"  ({id_note})\n'
            f"  - afiş     : {banner_note}\n"
            f"  - avatarlar: {avatar_note}"
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

        from_secret = bool(os.environ.get(ENV_KEYSTORE_B64, "").strip())
        if from_secret:
            job.log(
                "\nİmzalama: anahtar Space Secret'ından (" + ENV_KEYSTORE_B64 +
                ") okundu. Bu anahtar yeniden başlatmalardan etkilenmez."
            )
        elif created:
            job.log(
                "\nİmzalama: otomatik imza anahtarı ilk kez üretildi ve saklandı."
            )
        else:
            job.log("\nİmzalama: saklı otomatik imza anahtarı kullanılıyor.")

        # Parmak izini HER derlemede yazıyoruz. Cihaz kimliği (ANDROID_ID)
        # imza anahtarına bağlı olduğu için, bu satır iki derleme arasında
        # değiştiyse oyuncuların profili de sıfırlanmış demektir.
        job.log(f"  anahtar parmak izi (SHA-256): {keystore_fingerprint(ks_path, alias_final, password_final)}")
        job.log(
            "  Bu satır derlemeler arasında AYNI kalmalı; değişirse tüm "
            "oyuncuların cihaz kimliği ve profili sıfırlanır."
        )

        if not from_secret and not DATA_IS_PERSISTENT:
            job.log(
                "\n  !!! KRİTİK: Kalıcı disk YOK. Bu anahtar Space yeniden\n"
                "  başlatılınca kaybolur ve yenisi üretilir. Yeni anahtar =\n"
                "  yeni cihaz kimliği = tüm oyuncuların profili sıfırlanır.\n"
                "  Çözüm: 'Anahtarı indir' ile indirin, base64'e çevirip\n"
                f"  Space ayarlarında {ENV_KEYSTORE_B64} secret'ı olarak\n"
                f"  kaydedin ({ENV_KEYSTORE_ALIAS} ve {ENV_KEYSTORE_PASSWORD}\n"
                "  ile birlikte). Ayrıntı için README'ye bakın."
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

    # --- Derleme meta verisi (navigation.json) ---------------------------
    # Ren'Py normalde bu veriyi toplamak için oyunu bir alt süreçte AÇIP
    # kapatır; o alt süreç oyunun kendi init python bloklarını çalıştırır ve
    # bazı oyunlarda ekransız konteynerde segfault veriyor. Dosyayı biz
    # yazınca Launcher (yamalı hâliyle) alt süreci hiç başlatmıyor.
    _sdk_list = _sdk_roots()
    dump_result = build_dump.write_dump(
        project_root,
        _sdk_list[0] if _sdk_list else None,
        identity.version,
    )
    if dump_result.path is not None:
        if dump_result.permissions:
            izinler = ", ".join(dump_result.permissions)
        else:
            izinler = "yok (Ren'Py'nin varsayılanları kullanılacak)"
        job.log(
            f"\nDerleme meta verisi hazırlandı ({dump_result.scanned_files} "
            f"betik tarandı).\n"
            f"  - sürüm : {identity.version}\n"
            f"  - izinler: {izinler}\n"
            "  Bu sayede Ren'Py, meta veri toplamak için oyunu ayrıca "
            "açmak zorunda kalmıyor."
        )
    else:
        job.log(
            f"\nUyarı: derleme meta verisi hazırlanamadı ({dump_result.note}). "
            "Ren'Py bunu kendisi toplamayı deneyecek."
        )

    # --- Kaynak durumu ----------------------------------------------------
    # Paketleme adımı (private.mp3 arşivi + Gradle'ın JVM yığını) derlemenin
    # en bellek yoğun kısmıdır. Süreç bellek yetersizliğinden öldürülürse
    # Python tarafında HİÇBİR iz kalmaz; o yüzden başlangıç durumunu şimdi
    # kaydediyoruz ki hata sonrasında sebebi tartışmak zorunda kalmayalım.
    resource_note = resources.summary([str(job_dir), "/tmp"])
    job.log(f"\nKaynak durumu: {resource_note}")

    low_memory = resources.low_memory_warning()
    if low_memory:
        job.log(f"Uyarı: {low_memory}")

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
        elif _looks_like_steam_failure(full_log) and "returned -11" in full_log:
            # Steam yerel kodu çöktü. Bu, projenin Steam ile ilgisi olmasa
            # bile olabiliyor: libsteam_api.so Ren'Py SDK'sının kendi lib
            # klasöründe duruyor ve her derlemede yükleniyor.
            job.log(
                f"\nDerleme HATA ile sonuçlandı (kod {return_code}).\n"
                f"Sebep: STEAM entegrasyonu ({_looks_like_steam_failure(full_log)!r}).\n"
                "Ren'Py, SDK'nın lib klasöründe libsteam_api.so bulduğu için "
                "Steam'i başlatmaya çalışıyor; Steam olmayan bir konteynerde "
                "bu çağrı başarısız oluyor ve süreç segfault veriyor.\n"
                "Bu hata projenizin kodundan KAYNAKLANMIYOR.\n"
                "Paketleyici bunu RENPY_NO_STEAM ile kapatıyor; bu mesajı "
                "görüyorsanız app.py güncel değil ya da ortam değişkeni alt "
                "sürece ulaşmıyor demektir."
            )
        elif _looks_like_headless_failure(full_log):
            # Ekransız ortam çökmesi. Bunu "projenizde hata var" diye
            # sunmak yanlış yönlendirme olurdu: günlükte görünen hata
            # (genelde KeyError: 'bottom') asıl sebebin üstünü örten
            # İKİNCİL bir çökmedir — Launcher, kendi hata penceresini
            # çizemeyince o da çöker.
            signature = _looks_like_headless_failure(full_log)
            display_state = (
                f"şu an aktif ({DISPLAY_INFO.display})"
                if DISPLAY_INFO.active
                else f"KURULAMADI — {DISPLAY_INFO.note}"
            )
            job.log(
                f"\nDerleme HATA ile sonuçlandı (kod {return_code}).\n"
                f"Sebep: SDL VİDEO SÜRÜCÜSÜ sorunu ({signature!r}).\n"
                "Ren'Py, APK üretmeden önce projeyi bir kez açıp kapatıyor "
                "(derleme meta verisini toplamak için) ve bu adım atlanamıyor. "
                "O alt süreçte SDL 'dummy' sürücüsüne düşerse OpenGL "
                "bulunamıyor, çöken yazılım render'ına iniliyor ve süreç "
                "segfault veriyor.\n"
                f"Sanal ekran durumu: {display_state}\n"
                "Bu hata projenizin kodundan KAYNAKLANMIYOR.\n"
                "Kontrol listesi:\n"
                "  1. Docker imajında 'xvfb' ve 'libgl1-mesa-dri' kurulu mu?\n"
                "  2. Space günlüğünde '[ekran] Sanal ekran hazır' satırı var mı?\n"
                "  3. Launcher yaması uygulandı mı? İmaj derleme günlüğünde "
                "'[aerokey] SDK yamalanıyor' satırını arayın. Bu yama, Ren'Py'nin "
                "alt süreç için sürücüyü 'dummy' seçmesini engelliyor.\n"
                "Üçü de tamamsa Space'i yeniden derleyin (Factory rebuild)."
            )
        elif _looks_like_silent_death(full_log):
            # Süreç Python'a hiç uğramadan öldü: yığın izi yok. Elimizdeki
            # tek ipucu, günlüğe en son yazılan adım ile o andaki kaynak
            # durumu. Buradaki "Status 1" gerçek bir çıkış kodu DEĞİL,
            # renutil'in sinyal ölümünde bastığı yedek değerdir.
            signature = _looks_like_silent_death(full_log)
            step = _last_build_step(full_log)
            step_text = (
                f"Ulaşılan son adım: {step}.\n" if step
                else "Günlükte adım işaretçisi bulunamadı.\n"
            )
            job.log(
                f"\nDerleme HATA ile sonuçlandı (kod {return_code}).\n"
                f"Sebep: alt süreç, hata vermeden ÖLDÜRÜLDÜ ({signature!r}).\n"
                f"{step_text}"
                "Günlükte hiçbir Python yığın izi yok. Bu, sürecin bir "
                "istisna fırlatarak değil, doğrudan bir SİNYALLE "
                "sonlandırıldığı anlamına gelir. İki tipik sebebi vardır:\n"
                "  1. BELLEK yetersizliği — çekirdeğin OOM-killer'ı süreci "
                "sessizce öldürür.\n"
                "  2. Yerel (native) bir kütüphanenin çökmesi (görüntü "
                "çözücü, ses aygıtı, yazı tipi…).\n"
                f"Derleme başlarkenki kaynak durumu: {resource_note}\n"
                "Space'inizde kalıcı disk/bellek yükseltmesi mümkünse "
                "denemeye değer; bellek darsa aynı proje daha boş bir anda "
                "sorunsuz derlenebilir."
            )
        else:
            root_cause = _find_likely_root_cause(full_log)
            if root_cause and _root_cause_is_user_project(full_log):
                hint = (
                    f"\nOlası kök neden (otomatik tespit, kesin teşhis değildir): "
                    f"{root_cause}\nBu genelde projenizin kendi başlangıç kodundaki "
                    "bir hatadan kaynaklanır.\n"
                )
            elif root_cause:
                # Yığın izi yalnızca Ren'Py Launcher'ın kendi dosyalarına
                # düşüyor; suçu kullanıcının projesine yıkmıyoruz.
                hint = (
                    f"\nOlası kök neden (otomatik tespit, kesin teşhis değildir): "
                    f"{root_cause}\nHata Ren'Py Launcher'ın kendi içinde oluştu; "
                    "bu genelde asıl sebebin üstünü örten ikincil bir çökmedir. "
                    "Günlüğün DAHA ÜST kısımlarına bakın.\n"
                )
            else:
                hint = ""
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
            "keystore_from_secret": bool(os.environ.get(ENV_KEYSTORE_B64, "").strip()),
        }
    )


@app.get("/api/keystore/auto/secret")
async def api_auto_keystore_secret() -> JSONResponse:
    """
    Anahtarı, Space Secret'ı olarak yapıştırılmaya hazır biçimde döner.

    Kalıcı disk olmayan Space'lerde anahtarı sabit tutmanın tek yolu bu:
    dosya sistemine yazılan anahtar her yeniden başlatmada kaybolur ve
    yeniden üretilir. Anahtar değişince ANDROID_ID de değiştiği için tüm
    oyuncuların profili sıfırlanır.
    """
    try:
        path, alias, password, _ = get_or_create_auto_keystore()
    except RuntimeError as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc

    try:
        blob = path.read_bytes()
    except OSError as exc:
        raise HTTPException(status_code=500, detail=f"Anahtar okunamadı: {exc}") from exc

    return JSONResponse(
        {
            "active": bool(os.environ.get(ENV_KEYSTORE_B64, "").strip()),
            "fingerprint": keystore_fingerprint(path, alias, password),
            "secrets": {
                ENV_KEYSTORE_B64: base64.b64encode(blob).decode("ascii"),
                ENV_KEYSTORE_ALIAS: alias,
                ENV_KEYSTORE_PASSWORD: password,
            },
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
    translation: Optional[UploadFile] = File(None),
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
    translation_mode: str = Form("ask"),
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
    translation_path = await _save_upload(translation, ".zip")
    keystore_path = await _save_upload(keystore, ".keystore")

    request = BuildRequest(
        zip_path=zip_path,
        icon_path=icon_path,
        banner_path=banner_path,
        translation_path=translation_path,
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
        translation_mode=(translation_mode or "ask").strip() or "ask",
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
