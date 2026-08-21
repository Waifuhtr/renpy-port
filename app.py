"""
Ren'Py -> Android (APK/AAB) Paketleyici — Gradio arayüzü
=========================================================

Bu uygulama bir Ren'Py oyun projesinin ZIP'ini alır ve arka planda
"renkit" (renutil + renconstruct) araçlarıyla resmi Ren'Py/RAPT + Gradle
derleme hattını çalıştırarak Android APK ve/veya AAB dosyası üretir.

  renkit: https://github.com/kobaltcore/renkit  (MIT lisans)

Bu betik, Ren'Py SDK'sının ve Java 21'in Docker imajı içinde zaten kurulu
olduğunu varsayar (bkz. Dockerfile). Tek başına, sıradan bir Python
ortamında çalıştırılmak için tasarlanmamıştır.
"""

from __future__ import annotations

import base64
import json
import os
import re
import secrets
import shutil
import subprocess
import tempfile
import time
import uuid
from pathlib import Path
from typing import Optional

import gradio as gr

DEFAULT_RENPY_VERSION = os.environ.get("RENPY_VERSION", "8.5.3")
VERSION_RE = re.compile(r"^\d+\.\d+\.\d+$")

# Yapı sırasında açılan proje dosyaları / Gradle ara çıktıları buraya gider
# ve her işten sonra silinir.
WORK_ROOT = Path(tempfile.gettempdir()) / "renpy_android_jobs"
# Üretilen APK/AAB dosyaları buraya kopyalanır ve Gradio bunları kullanıcıya
# sunana kadar SİLİNMEZ (yalnızca eskiyince, aşağıdaki temizlik ile silinir).
RESULTS_ROOT = Path(tempfile.gettempdir()) / "renpy_android_results"
WORK_ROOT.mkdir(parents=True, exist_ok=True)
RESULTS_ROOT.mkdir(parents=True, exist_ok=True)


def _cleanup_old_dirs(root: Path, max_age_hours: float = 6.0) -> None:
    """Belirtilen saatten eski iş klasörlerini arka planda temizler.
    Disk alanının, Space uzun süre yeniden başlamadan dolmasını önler."""
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


def _filepath(uploaded) -> Optional[str]:
    """Gradio sürümüne göre bir dosya girdisi doğrudan bir yol (str) ya da
    `.name` özniteliği taşıyan bir nesne olabilir; ikisini de destekler."""
    if uploaded is None:
        return None
    if isinstance(uploaded, str):
        return uploaded
    name = getattr(uploaded, "name", None)
    return name if name else None


_DEFINE_STRING_RE_TEMPLATE = r'define\s+{}\s*=\s*_?\(?\s*([\'"])(.*?)\1'
_BAD_DIRNAME_CHARS = set(" :;")


def _extract_defined_string(text: str, varname: str) -> Optional[str]:
    """Metinde 'define <varname> = "..."' ya da '_("...")' gibi kalıpları
    arar ve değeri döner (bulunamazsa None)."""
    pattern = re.compile(_DEFINE_STRING_RE_TEMPLATE.format(re.escape(varname)))
    m = pattern.search(text)
    return m.group(2) if m else None


def _read_game_rpy_text(project_root: Path) -> str:
    """game/ içindeki tüm .rpy dosyalarının metnini birleştirip döner
    (options.rpy varsa en başta). build.name/config.name/build.package gibi
    tanımlar bazı projelerde options.rpy dışında bir dosyada da olabilir;
    bu yüzden yalnızca options.rpy'ye değil, tüm .rpy dosyalarına bakıyoruz.
    Okunamayan/olmayan dosyalar sessizce atlanır.
    """
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


def _fix_build_directory_name(project_root: Path) -> Optional[str]:
    """Ren'Py'nin Android derlemesi, build.directory_name (açıkça
    tanımlanmadıysa build.name/config.name'den türetilen) değerinde boşluk,
    ':' veya ';' varsa derlemeyi reddediyor. Bu fonksiyon o durumu, YALNIZCA
    bu derleme için kullanılan GEÇİCİ proje kopyasını düzenleyerek otomatik
    giderir — kullanıcının orijinal ZIP'ine/projesine kesinlikle
    dokunulmaz. Arama yalnızca options.rpy ile sınırlı değildir: bazı
    projeler bu tanımları başka bir .rpy dosyasında tutar. Bir düzeltme
    yapıldıysa günlüğe yazılacak açıklayıcı bir mesaj döner, gerek yoksa
    None döner.
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
            "Otomatik düzeltme: game/options.rpy içindeki build.directory_name "
            f'değeri ("{current}") Android derlemesiyle uyumsuz olduğu için, '
            f'yalnızca bu derlemenin GEÇİCİ ÇALIŞMA KOPYASINDA "{fixed}" '
            "olarak değiştirildi. Orijinal proje dosyalarınıza dokunulmadı; "
            "kalıcı olsun isterseniz kendi projenizde de aynı satırı "
            "güncelleyebilirsiniz."
        )

    # directory_name options.rpy'de açıkça tanımlı değil (ya da dosya hiç
    # yok) -> build.name/config.name için TÜM .rpy dosyalarına bakıyoruz,
    # çünkü bazı projeler bu tanımı başka bir dosyada tutuyor olabilir.
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
            # options.rpy hiç yoksa (örn. yalnızca .rpyc'li bir dağıtım
            # yüklendiyse), küçük, ayrı bir override dosyası oluşturuyoruz.
            override_path = game_dir / "zz_android_builder_overrides.rpy"
            try:
                game_dir.mkdir(parents=True, exist_ok=True)
                override_path.write_text(
                    f'define build.directory_name = "{fixed}"\n', encoding="utf-8"
                )
            except OSError:
                return None
            where = f"yeni bir {override_path.name} dosyasına (game/options.rpy bulunamadı)"
        return (
            "Otomatik düzeltme: build.directory_name hiçbir yerde tanımlı "
            f'değildi ve build.name/config.name ("{name}") boşluk içerdiği '
            f"için Android derlemesi başarısız olurdu. Bu derlemenin GEÇİCİ "
            f'ÇALIŞMA KOPYASINDA {where} "define build.directory_name = '
            f'\\"{fixed}\\"" satırı eklendi. Orijinal proje dosyalarınıza '
            "dokunulmadı."
        )
    return None


# Gerçekten yayınlanmış bir Ren'Py Android projesinden alınmış, çalıştığı
# doğrulanmış bir android.json şablonu. Yalnızca proje-özel alanlar
# (name/icon_name/package/version/numeric_version) değiştirilir, geri kalanı
# bu haliyle korunur.
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
    """'1.2.3' -> '10203' gibi basit, artan bir versionCode türetir (RAPT'in
    eski configure.py'sindeki mantıkla aynı)."""
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
    """Kullanıcının arayüzden girdiği paket adı önekini (örn.
    'com.riaslinkfun') güvenli, nokta-ayraçlı bir Android paket öneki
    biçimine getirir. Boş ya da tamamen geçersizse güvenli bir varsayılana
    döner."""
    parts = [p for p in (prefix or "").strip().lower().split(".") if p]
    parts = [_sanitize_package_component(p) for p in parts]
    parts = [p for p in parts if p]
    return ".".join(parts) if parts else _DEFAULT_PACKAGE_PREFIX


def _ensure_android_json(
    project_root: Path, package_prefix: str = _DEFAULT_PACKAGE_PREFIX
) -> Optional[str]:
    """Ren'Py'nin Android derlemesi, proje kök dizininde bir android.json
    (ya da eski adıyla .android.json) bulamazsa "Run configure before
    attempting to build the app" diyerek reddediyor — bu dosya normalde
    yalnızca Ren'Py Launcher'ın interaktif 'Configure' adımından geçilince
    oluşuyor. Bu fonksiyon, dosya yoksa, game/ içindeki .rpy dosyalarından
    (varsa) türetilmiş makul varsayılanlarla YALNIZCA bu derlemenin GEÇİCİ
    ÇALIŞMA KOPYASINA bir tane yazar. Paket adı, aynı oyun için derlemeler
    arasında SABİT kalacak şekilde (rastgelelik kullanmadan) oyunun
    adından türetilir. Bir şey oluşturulduysa günlüğe yazılacak açıklayıcı
    bir mesaj döner, dosya zaten varsa None döner.
    """
    if (project_root / "android.json").exists() or (
        project_root / ".android.json"
    ).exists():
        return None

    text = _read_game_rpy_text(project_root)

    name = (
        _extract_defined_string(text, "build.name")
        or _extract_defined_string(text, "config.name")
        or "Ren'Py Game"
    )
    package_from_rpy = _extract_defined_string(text, "build.package")
    version = _extract_defined_string(text, "config.version") or "1.0"

    if package_from_rpy:
        package = package_from_rpy.strip().lower()
    else:
        comp = _sanitize_package_component(re.sub(r"[^a-zA-Z0-9]", "", name)) or "game"
        prefix = _sanitize_package_prefix(package_prefix)
        # Sabit (rastgele olmayan) bir alt alan adı: aynı oyun adı her
        # derlemede aynı paket adını üretir, böylece güncellemeler bozulmaz.
        package = f"{prefix}.{comp}"

    data = dict(_ANDROID_JSON_DEFAULTS)
    data.update(
        {
            "name": name,
            "icon_name": name[:30],
            "package": package,
            "version": version,
            "numeric_version": _derive_version_code(version),
        }
    )

    try:
        (project_root / "android.json").write_text(
            json.dumps(data, indent=4), encoding="utf-8"
        )
    except OSError:
        return None

    return (
        "Otomatik oluşturma: Bu proje daha önce Ren'Py Launcher üzerinden "
        "Android için hiç 'Configure' edilmemiş (android.json yok). Bu "
        "derlemenin GEÇİCİ ÇALIŞMA KOPYASINA aşağıdaki varsayılanlarla bir "
        "android.json oluşturuldu (orijinal dosyalarınıza dokunulmadı):\n"
        f'  - name: "{name}"\n'
        f'  - package: "{package}"\n'
        f'  - version: "{version}"\n'
        "NOT: package adı otomatik türetildi. Google Play'e yüklemeyi "
        "düşünüyorsanız, kalıcı ve size ait bir paket adı için kendi "
        "projenizde Ren'Py Launcher > Android > Configure adımını "
        "tamamlayıp oluşan android.json dosyasını projenizin köküne "
        "(game/ ile aynı seviyeye) ekleyip zip'e dahil edin — bu araç "
        "varsa o dosyaya dokunmaz, olduğu gibi kullanır."
    )


_ICON_SOURCE_DIR = Path("/app/icon_source")
_ICON_CANVAS = 432
_ICON_SAFE_RATIO = 0.66  # Android adaptif ikon güvenli alanına kaba bir yaklaşım


def _find_bundled_icon() -> Optional[Path]:
    """Dockerfile'ın /app/icon_source/ altına kopyaladığı, Space'e
    (icon.png / icon.jpg / icon.jpeg olarak) gömülmüş ikonu varsa döner."""
    if not _ICON_SOURCE_DIR.is_dir():
        return None
    for name in ("icon.png", "icon.jpg", "icon.jpeg", "icon.PNG", "icon.JPG"):
        candidate = _ICON_SOURCE_DIR / name
        if candidate.is_file():
            return candidate
    return None


def _prepare_android_icon(
    project_root: Path, uploaded_icon_path: Optional[str]
) -> Optional[str]:
    """Ren'Py, Android ikonunu proje kökündeki iki 432x432 PNG dosyasından
    üretir: android-icon_foreground.png (şeffaf ön katman) ve
    android-icon_background.png (opak arka plan) — bkz.
    https://www.renpy.org/doc/html/android.html#icon-and-presplash-images

    Bu fonksiyon:
    - Proje zaten bu iki dosyayı sağlıyorsa HİÇ DOKUNMAZ (tam kullanıcı
      kontrolü önceliklidir).
    - Aksi halde, bu derleme için yüklenen bir ikon varsa onu, yoksa
      Space imajına gömülü icon.png/.jpg dosyasını kaynak alıp, YALNIZCA
      bu derlemenin GEÇİCİ ÇALIŞMA KOPYASINA iki katmanlı ikonu otomatik
      üretir (arka plan düz beyaz).
    - Uygun bir kaynak/kütüphane yoksa sessizce None döner; Ren'Py bu
      durumda kendi varsayılan ikonunu kullanır, derleme bozulmaz.
    """
    fg_path = project_root / "android-icon_foreground.png"
    bg_path = project_root / "android-icon_background.png"
    if fg_path.exists() and bg_path.exists():
        return None  # proje zaten kendi ikonlarını sağlamış, dokunma

    source = Path(uploaded_icon_path) if uploaded_icon_path else _find_bundled_icon()
    if source is None or not source.exists():
        return None  # hiçbir ikon kaynağı yok -> Ren'Py varsayılanını kullanır

    try:
        from PIL import Image
    except ImportError:
        return (
            "Uyarı: İkon kaynağı bulundu ama Pillow kütüphanesi kurulu "
            "değil, otomatik ikon üretimi atlandı."
        )

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

        bg_canvas = Image.new(
            "RGBA", (_ICON_CANVAS, _ICON_CANVAS), (255, 255, 255, 255)
        )
        bg_canvas.save(bg_path)
    except Exception as exc:  # noqa: BLE001
        return (
            f"Uyarı: İkon otomatik oluşturulurken hata oluştu ({exc!r}); "
            "Ren'Py varsayılan ikonu kullanacak, derleme yine de devam ediyor."
        )

    return (
        f'Otomatik ikon: "{source.name}" kaynağından, Ren\'Py\'nin beklediği '
        "iki katmanlı adaptif ikon (android-icon_foreground.png + "
        "android-icon_background.png, 432x432, arka plan düz beyaz) "
        "GEÇİCİ ÇALIŞMA KOPYASINA otomatik oluşturuldu. Farklı bir arka "
        "plan rengi ya da tam kontrol isterseniz, bu iki dosyayı kendiniz "
        "hazırlayıp projenizin köküne (game/ ile aynı seviyeye) "
        "ekleyebilirsiniz — o zaman bu adım hiç devreye girmez."
    )


def _scan_local_py_imports(project_root: Path) -> Optional[str]:
    """game/ içindeki düz .py dosyalarını (_ren.py hariç) bulur ve .rpy
    dosyalarında nasıl kullanıldıklarına bakar.

    Gözlem: böyle bir dosyanın normal 'import modul' ile içe aktarılması,
    bazı Android derlemelerinde ModuleNotFoundError'a yol açabiliyor
    (bu sohbette bir örnekte gözlemlendi). BU FONKSİYON OYUN KODUNU ASLA
    DEĞİŞTİRMEZ — modülün gerçekten kullanılıp kullanılmadığına dair statik
    bir iz sürüp yalnızca bilgilendirici bir uyarı döner. Kullanılmayan bir
    içe aktarmayı otomatik silmek bile risklidir (modülün import anında yan
    etkisi olabilir), kullanılan birini hiç otomatik değiştirmiyoruz.
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
            continue  # .py dosyası var ama hiç import edilmiyor -> zararsız

        text_without_import_lines = "\n".join(
            line for line in all_text.splitlines() if not import_line_re.match(line)
        )
        usage_re = re.compile(esc + r"\.\w")
        if usage_re.search(text_without_import_lines):
            used_but_risky.append(mod)
        else:
            unused.append(mod)

    if not unused and not used_but_risky:
        return None

    lines = [
        "Bilgi: game/ klasöründe düz .py dosyası olarak import edilen "
        "modül(ler) tespit edildi (Android'de bazen ModuleNotFoundError'a "
        "yol açabiliyor). BU ARAÇ OYUN KODUNUZU DEĞİŞTİRMEZ, yalnızca "
        "bilgilendirir:"
    ]
    if unused:
        mods = ", ".join(f'"{m}"' for m in unused)
        lines.append(
            f"  - {mods}: import ediliyor ama .rpy dosyalarında kullanıldığına "
            "dair iz bulunamadı (muhtemelen ölü kod). Sorun çıkarsa, ilgili "
            "'import ...' satırını silmeyi deneyebilirsiniz."
        )
    if used_but_risky:
        mods = ", ".join(f'"{m}"' for m in used_but_risky)
        lines.append(
            f"  - {mods}: import ediliyor VE kullanıldığına dair iz var. "
            "Android derlemesi/çalışması bu yüzden başarısız olursa, bu "
            "dosyanın içeriğini game/ içindeki bir \"init python:\" "
            f'bloğuna taşımayı ya da dosyayı "{{isim}}_ren.py" olarak '
            "yeniden adlandırıp Ren'Py'nin kendi derleme hattına dahil "
            "etmeyi deneyin."
        )
    return "\n".join(lines)


def _find_project_root(extracted_dir: Path) -> Optional[Path]:
    """Ren'Py projesinin 'game/' klasörünü barındıran kök dizini bulur.

    Kullanıcılar genelde proje klasörünü doğrudan zip'ler (game/ kökte)
    ya da klasörü bir üst dizinle birlikte zip'ler (proje_adi/game/).
    Her iki durumu da, ve nadir görülen bir seviye daha derin durumları da
    destekler.
    """
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


def _detect_possible_distribution_build(project_root: Path) -> Optional[str]:
    """Ren'Py Launcher'ın ürettiği HAZIR bir masaüstü dağıtım paketi
    (örn. "OyunAdı-1.2.1-pc" klasörü), ham proje kaynağından farklıdır:
    genelde .rpy kaynak dosyaları çıkarılıp yalnızca derlenmiş .rpyc
    bırakılır, ve renpy/ + lib/ (motor + native kütüphaneler) dahil edilir.
    Böyle bir paketi bu araca yüklemek şu sorunlara yol açabilir: uygulama
    adı/paketi doğru okunamaz (çünkü options.rpy kaynak olarak yok),
    ve varlık/arşivleme davranışı beklenmedik şekilde çalışabilir.

    Bu fonksiyon KESİN bir teşhis koymaz, yalnızca güçlü sinyalleri
    toplayıp (varsa) açıklayıcı bir uyarı döner; derlemeyi durdurmaz,
    çünkü yanlış pozitif olma ihtimali sıfır değildir ve kullanıcı yine
    de bir sonuç görmek isteyebilir.
    """
    game_dir = project_root / "game"
    signals = []

    options_rpy = game_dir / "options.rpy"
    options_rpyc = game_dir / "options.rpyc"
    source_stripped = (not options_rpy.is_file()) and options_rpyc.is_file()
    if source_stripped:
        signals.append(
            "game/options.rpy (kaynak) yok ama game/options.rpyc (derlenmiş "
            "hali) var — proje kaynağı çıkarılmış olabilir"
        )

    has_sdk_siblings = (project_root / "renpy").is_dir() or (project_root / "lib").is_dir()
    if has_sdk_siblings:
        signals.append(
            "game/ ile aynı seviyede renpy/ ve/veya lib/ klasörü var "
            "(genelde yalnızca hazır dağıtım paketlerinde bulunur)"
        )

    dist_suffix = bool(_DIST_SUFFIX_RE.search(project_root.name))
    if dist_suffix:
        signals.append(
            f'proje klasör adı ("{project_root.name}") Ren\'Py\'nin masaüstü '
            "dağıtım paketlerine verdiği isimlendirmeyle eşleşiyor"
        )

    if not (source_stripped or (dist_suffix and has_sdk_siblings)):
        return None

    return (
        "Uyarı — bu, HAM PROJE KAYNAĞI değil, hazır bir masaüstü dağıtım "
        "paketi gibi görünüyor:\n"
        + "\n".join(f"  - {s}" for s in signals)
        + "\nBu durumda uygulama adı yanlış/eksik çıkabilir (çünkü "
        "options.rpy kaynak olarak bulunamıyor) ve görsel/ses varlıkları "
        "beklenmedik şekilde davranabilir. ÖNERİ: Ren'Py Launcher'da "
        "projenizi açıp 'Build Distributions' YAPMADAN, doğrudan proje "
        "klasörünüzü (üzerinde çalıştığınız, .rpy kaynak dosyalarının "
        "gerçekten var olduğu klasörü) zip'leyip yükleyin — yalnızca "
        "game/ klasörü yeterli, renpy/ ve lib/ gerekmez. Derleme yine de "
        "devam ediyor, ama sonuç muhtemelen bu yüzden bozuk çıkacaktır."
    )


_TRACEBACK_BLOCK_RE = re.compile(
    r"Full traceback:\n((?:.*\n)*?)^(\S[\w.]*(?:Error|Exception|Warning)\b.*)$",
    re.MULTILINE,
)


def _find_likely_root_cause(full_log: str) -> Optional[str]:
    """Uzun bir derleme günlüğünde, jenerik "Could not get build data from
    the project" gibi kapanış hatasının ARKASINDA gizli kalan asıl Python
    hatasını bulmaya çalışır (Ren'Py, bir projenin kendi init-time kodu
    hata verdiğinde önce o hatayı, sonra ayrı bir jenerik "build data
    alınamadı" hatasını basar). Bu KESİN bir teşhis değildir — bulunan en
    olası adayı, varsa ilgili game/ dosyası+satırıyla birlikte tek satırlık
    bir ipucu olarak döner; hiçbir gerçek aday yoksa None döner.
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
            game_file_match = (path, gm.group(2))  # en son (en spesifik) eşleşmeyi tut

    if game_file_match:
        return f"{exc_line.strip()}  (konum: {game_file_match[0]}:{game_file_match[1]})"
    return exc_line.strip()


def _stream_subprocess(cmd, cwd=None, env=None):
    """Bir komutu çalıştırır ve stdout/stderr satırlarını üretildikçe verir
    (generator). Son üretilen değer, sürecin dönüş kodudur (int); önceki
    tüm değerler birer satırdır (str). Tüketen kod `isinstance(x, int)`
    ile ikisini ayırt eder.
    """
    process = subprocess.Popen(
        cmd,
        cwd=cwd,
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        bufsize=1,
    )
    assert process.stdout is not None
    for line in process.stdout:
        yield line
    process.wait()
    yield process.returncode


def generate_keystore(alias: str, password: str):
    """'Yeni Anahtar Oluştur' düğmesi için: keytool ile kalıcı olarak
    saklanabilecek bir .keystore dosyası üretir ve doğrudan derleme
    formundaki keystore alanlarına doldurur. Kullanıcı dosyayı indirip
    güvenli bir yerde sakladığı sürece, sonraki tüm derlemelerde aynı
    anahtarı tekrar yükleyerek tutarlı imzalama sağlayabilir.
    """
    alias = (alias or "").strip() or "renpyupload"
    password = (password or "").strip() or secrets.token_urlsafe(16)

    gen_dir = Path(tempfile.mkdtemp(prefix="genkey_", dir=WORK_ROOT))
    ks_path = gen_dir / f"{alias}.keystore"

    cmd = [
        "keytool", "-genkeypair", "-v",
        "-keystore", str(ks_path),
        "-alias", alias,
        "-keyalg", "RSA", "-keysize", "2048", "-validity", "10000",
        "-storepass", password,
        "-keypass", password,
        "-dname", "CN=RenpyAndroidBuilder, OU=Dev, O=Dev, L=NA, S=NA, C=US",
    ]
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0 or not ks_path.exists():
        raise gr.Error(
            "Anahtar üretilemedi: "
            + (result.stderr or result.stdout or "bilinmeyen hata")[-500:]
        )

    return str(ks_path), alias, password


def build_android_package(
    project_zip,
    renpy_version: str,
    want_apk: bool,
    want_aab: bool,
    package_prefix: str,
    custom_icon_file,
    keystore_file,
    keystore_alias: str,
    keystore_password: str,
):
    log_lines: list[str] = []

    def log(msg: str) -> str:
        log_lines.append(msg)
        return "\n".join(log_lines)

    zip_path = _filepath(project_zip)
    if zip_path is None:
        yield log("Hata: Önce bir Ren'Py proje ZIP dosyası yüklemelisiniz."), None
        return

    if not want_apk and not want_aab:
        yield log("Hata: En az bir çıktı formatı seçmelisiniz (APK ve/veya AAB)."), None
        return

    renpy_version = (renpy_version or "").strip() or DEFAULT_RENPY_VERSION
    if not VERSION_RE.match(renpy_version):
        yield log(
            f"Hata: '{renpy_version}' geçerli bir Ren'Py sürüm numarası gibi "
            "görünmüyor. Örnek biçim: 8.5.3"
        ), None
        return

    ks_path = _filepath(keystore_file)
    any_keystore_field = bool(ks_path) or bool(keystore_alias) or bool(keystore_password)
    keystore_provided = bool(ks_path) and bool(keystore_alias) and bool(keystore_password)
    if any_keystore_field and not keystore_provided:
        yield log(
            "Uyarı: Keystore alanlarının üçü de (dosya, alias, şifre) "
            "doldurulmadığı için özel imzalama anahtarı YOK SAYILIYOR; "
            "otomatik/geçici bir anahtar üretilecek."
        ), None

    job_dir = Path(tempfile.mkdtemp(prefix="job_", dir=WORK_ROOT))
    job_id = job_dir.name
    project_extract_dir = job_dir / "extracted"
    # Nihai çıktı klasörü kasıtlı olarak job_dir'in DIŞINDA: build bittiğinde
    # job_dir'i silsek bile burada kalan APK/AAB dosyaları Gradio tarafından
    # kullanıcıya sunulmaya devam edebilsin.
    out_dir = RESULTS_ROOT / job_id
    out_dir.mkdir(parents=True, exist_ok=True)

    try:
        yield log("Proje ZIP dosyası açılıyor..."), None
        import zipfile

        try:
            with zipfile.ZipFile(zip_path) as zf:
                zf.extractall(project_extract_dir)
        except zipfile.BadZipFile:
            yield log("Hata: Yüklenen dosya geçerli bir ZIP arşivi değil."), None
            return

        project_root = _find_project_root(project_extract_dir)
        if project_root is None:
            yield log(
                "Hata: ZIP içinde bir 'game/' klasörü bulunamadı.\n"
                "Lütfen Ren'Py proje klasörünü (içinde 'game' klasörü olan "
                "klasörü) doğrudan zip'leyip tekrar deneyin."
            ), None
            return
        yield log(f"Proje bulundu: {project_root.relative_to(job_dir)}"), None

        dist_warning = _detect_possible_distribution_build(project_root)
        if dist_warning:
            yield log(dist_warning), None

        dirname_fix_msg = _fix_build_directory_name(project_root)
        if dirname_fix_msg:
            yield log(dirname_fix_msg), None

        android_json_msg = _ensure_android_json(project_root, package_prefix=package_prefix)
        if android_json_msg:
            yield log(android_json_msg), None

        icon_path = _filepath(custom_icon_file)
        icon_msg = _prepare_android_icon(project_root, icon_path)
        if icon_msg:
            yield log(icon_msg), None

        py_import_msg = _scan_local_py_imports(project_root)
        if py_import_msg:
            yield log(py_import_msg), None

        if renpy_version != DEFAULT_RENPY_VERSION:
            yield log(
                f"İmaja önceden gömülü sürüm ({DEFAULT_RENPY_VERSION}) ile "
                f"istenen sürüm ({renpy_version}) farklı. '{renpy_version}' "
                "indiriliyor — bu birkaç dakika sürebilir..."
            ), None
            return_code = None
            for chunk in _stream_subprocess(["renutil", "install", renpy_version]):
                if isinstance(chunk, int):
                    return_code = chunk
                else:
                    yield log(chunk.rstrip("\n")), None
            if return_code != 0:
                yield log(
                    f"Hata: 'renutil install {renpy_version}' başarısız oldu "
                    f"(kod {return_code}). Sürüm numarasını kontrol edin "
                    "(https://www.renpy.org/release_list.html)."
                ), None
                return

        config_path = job_dir / "renconstruct.toml"
        toml_lines = [
            "[build]",
            f"android_apk = {str(bool(want_apk)).lower()}",
            f"android_aab = {str(bool(want_aab)).lower()}",
            "pc = false",
            "win = false",
            "linux = false",
            "mac = false",
            "web = false",
            "steam = false",
            "market = false",
            "",
            "[renutil]",
            f'version = "{renpy_version}"',
            "",
            "[options]",
            "clear_output_dir = true",
            "",
            # renconstruct, Android hedeflerinde keystore görevinin AKTİF
            # olmasını VE gerçek anahtar verisini (alias/password/keystore_apk/
            # keystore_aab alanlarını) TOML içinde bulmasını zorunlu tutuyor;
            # ne görev atlanabiliyor ne de alanlar boş bırakılabiliyor. Bu
            # yüzden özel bir keystore verilmediyse aşağıda kendimiz
            # (keytool ile) geçici bir tane üretip alanları dolduruyoruz.
            "[tasks.keystore]",
            'type = "keystore"',
            "enabled = true",
            'on_builds = ["android_apk", "android_aab"]',
        ]

        if keystore_provided:
            ks_final_path = ks_path
            alias_final = keystore_alias
            password_final = keystore_password
            yield log("Özel imzalama anahtarınız kullanılacak."), None
        else:
            yield log(
                "Bilgi: Özel keystore verilmedi -> bu derlemeye özel, "
                "GEÇİCİ bir imzalama anahtarı otomatik üretiliyor "
                "(keytool ile). Bu anahtar saklanmaz: farklı bir "
                "derlemede yeniden üretilecek APK, bununla imzalanmış "
                "önceki APK'nin üzerine kurulamaz. Google Play'e "
                "yüklemeyi veya mevcut bir uygulamayı güncellemeyi "
                "düşünüyorsanız kendi keystore'unuzu yükleyin, ya da "
                "yukarıdaki 'Gelişmiş' bölümdeki 'Yeni Anahtar Oluştur' "
                "düğmesiyle kalıcı bir tane üretip saklayın."
            ), None

            generated_ks_path = job_dir / "auto_generated.keystore"
            alias_final = "renpyupload"
            password_final = secrets.token_urlsafe(16)

            keytool_cmd = [
                "keytool", "-genkeypair", "-v",
                "-keystore", str(generated_ks_path),
                "-alias", alias_final,
                "-keyalg", "RSA", "-keysize", "2048", "-validity", "10000",
                "-storepass", password_final,
                "-keypass", password_final,
                "-dname", "CN=RenpyAndroidBuilder, OU=Dev, O=Dev, L=NA, S=NA, C=US",
            ]
            ks_return_code = None
            for chunk in _stream_subprocess(keytool_cmd):
                if isinstance(chunk, int):
                    ks_return_code = chunk
                # keytool'un kendi çıktısını günlüğe basmıyoruz (gürültülü).

            if ks_return_code != 0 or not generated_ks_path.exists():
                yield log(
                    "Hata: Geçici imzalama anahtarı otomatik üretilemedi "
                    "(keytool başarısız oldu). Lütfen kendi keystore'unuzu "
                    "yükleyerek tekrar deneyin."
                ), None
                return
            ks_final_path = generated_ks_path

        with open(ks_final_path, "rb") as f:
            keystore_b64 = base64.b64encode(f.read()).decode("ascii")
        toml_lines += [
            f'alias = "{alias_final}"',
            f'password = "{password_final}"',
            f'keystore_apk = "{keystore_b64}"',
            f'keystore_aab = "{keystore_b64}"',
        ]

        config_path.write_text("\n".join(toml_lines) + "\n", encoding="utf-8")

        yield log(
            "\nAndroid derlemesi başlatılıyor. İLK ÇALIŞTIRMADA Android SDK "
            "bileşenlerinin indirilmesi nedeniyle bu adım uzun sürebilir "
            "(muhtemelen birkaç dakika ila on dakikanın üzerinde); sonraki "
            "derlemeler daha hızlı olacaktır."
        ), None

        cmd = [
            "renconstruct", "build",
            "-c", str(config_path),
            str(project_root), str(out_dir),
        ]

        return_code = None
        for chunk in _stream_subprocess(cmd):
            if isinstance(chunk, int):
                return_code = chunk
            else:
                yield log(chunk.rstrip("\n")), None

        if return_code != 0:
            root_cause = _find_likely_root_cause("\n".join(log_lines))
            hint = (
                f"\nOlası kök neden (günlükte otomatik tespit edildi, kesin "
                f"teşhis değildir): {root_cause}\nBu genelde projenizin "
                "kendi başlangıç kodundaki (options.rpy/script.rpy/init "
                "python bloklarındaki) bir hatadan kaynaklanır ve bu "
                "aracın kendisiyle ilgili değildir.\n"
                if root_cause
                else ""
            )
            yield log(
                f"\nDerleme HATA ile sonuçlandı (kod {return_code}).\n"
                f"{hint}"
                "Yukarıdaki günlüğü inceleyin; README'deki 'Sorun Giderme' "
                "bölümüne de bakabilirsiniz (Java sürümü, Gradle bellek "
                "sorunları, proje yapılandırması gibi olası nedenler için)."
            ), None
            return

        result_files = sorted(
            str(p) for p in out_dir.rglob("*") if p.suffix.lower() in (".apk", ".aab")
        )
        if not result_files:
            yield log(
                "\nDerleme hata vermeden bitti ama çıktı klasöründe bir "
                ".apk/.aab dosyası bulunamadı. Günlüğü kontrol edin."
            ), None
            return

        names = "\n".join(f"  - {Path(p).name}" for p in result_files)
        yield log(f"\nBitti! {len(result_files)} dosya üretildi:\n{names}"), result_files

    except Exception as exc:  # noqa: BLE001 - kullanıcıya okunabilir hata göstermek için
        yield log(f"\nBeklenmeyen bir hata oluştu: {exc!r}"), None
    finally:
        # Sadece ara/derleme çalışma alanını temizliyoruz; out_dir
        # (RESULTS_ROOT altında) kasıtlı olarak dokunulmadan bırakılıyor.
        shutil.rmtree(job_dir, ignore_errors=True)


with gr.Blocks(title="Ren'Py Android Paketleyici") as demo:
    gr.Markdown(
        f"""
# 🎮 Ren'Py → Android (APK / AAB) Paketleyici

Bir Ren'Py oyun **projesinin ZIP dosyasını** yükleyin; arka planda
[renkit](https://github.com/kobaltcore/renkit) aracılığıyla resmi
Ren'Py/RAPT + Gradle derleme hattı çalıştırılır ve size indirilebilir bir
**APK ve/veya AAB** dosyası üretilir.

> **Küçük bir düzeltme:** `renpy/renpy-build` reposu Ren'Py **motorunun
> kendisini** (ve Android/iOS/web için gereken alt bileşenlerini) çeşitli
> platformlar için derleyen bir sistemdir — tek bir *oyunu* paketlemek için
> doğrudan kullanılmaz ve kullanıcı arayüzü de yoktur. Bir oyunu Android'e
> paketlemenin asıl yolu, Ren'Py SDK'sıyla gelen **RAPT** (Ren'Py Android
> Packaging Tool) + Gradle derleme hattıdır — bu araç da tam olarak onu,
> `renkit` üzerinden otomatikleştirir.

**İmaja gömülü varsayılan Ren'Py sürümü:** `{DEFAULT_RENPY_VERSION}`
(farklı bir sürüm de girebilirsiniz, ilk kullanımda ayrıca indirilir)
        """
    )

    with gr.Row():
        with gr.Column():
            project_zip = gr.File(
                label="Ren'Py Proje ZIP dosyası (içinde 'game' klasörü olmalı)",
                file_types=[".zip"],
                type="filepath",
            )
            renpy_version = gr.Textbox(
                label="Ren'Py sürümü",
                value=DEFAULT_RENPY_VERSION,
                placeholder="örn. 8.5.3",
            )
            package_prefix = gr.Textbox(
                label="Paket adı öneki (projenizde build.package tanımlı "
                "değilse kullanılır; sonuç: önek.oyunadı)",
                value="com.riaslinkfun",
                placeholder="örn. com.riaslinkfun",
            )
            with gr.Row():
                want_apk = gr.Checkbox(label="APK üret (yan yükleme / test)", value=True)
                want_aab = gr.Checkbox(label="AAB üret (Google Play)", value=False)

            with gr.Accordion("Gelişmiş: Uygulama İkonu", open=False):
                gr.Markdown(
                    "Boş bırakırsanız (varsa) Space'e gömülü `icon.png`/"
                    "`icon.jpg` kullanılır, o da yoksa Ren'Py'nin kendi "
                    "varsayılan ikonu kullanılır. Buraya bir resim "
                    "yüklerseniz, yalnızca BU derleme için Space'e gömülü "
                    "olanın yerine geçer. Tek bir kare görsel yeterli — "
                    "Ren'Py'nin istediği iki katmanlı adaptif ikon (opak "
                    "arka plan beyaz) otomatik üretilir."
                )
                custom_icon_file = gr.File(label="İkon (isteğe bağlı)", type="filepath")

            with gr.Accordion("Gelişmiş: Özel İmzalama Anahtarı (Keystore)", open=False):
                gr.Markdown(
                    "Boş bırakırsanız her derlemede rastgele/geçici bir "
                    "anahtar üretilir (test için yeterlidir ama Play "
                    "Store'a yüklemek ya da mevcut bir uygulamayı "
                    "güncellemek için **aynı keystore'u** tekrar tekrar "
                    "kullanmanız gerekir — aksi halde Android/Play bunu "
                    "farklı bir uygulama sayar)."
                )

                with gr.Accordion("Kalıcı olarak saklamak için yeni bir anahtar üret", open=False):
                    gr.Markdown(
                        "Aşağıya tıkladığınızda bu Space üzerinde `keytool` ile "
                        "gerçek bir `.keystore` dosyası üretilir ve hemen altındaki "
                        "üç alana otomatik doldurulur. **Üretilen dosyayı indirip "
                        "güvenli bir yerde saklayın** (şifreyle birlikte) — "
                        "kaybederseniz geri getirilemez. Sonraki her derlemede "
                        "aynı dosyayı tekrar yükleyerek tutarlı imzalama elde "
                        "edersiniz."
                    )
                    with gr.Row():
                        gen_alias_in = gr.Textbox(label="Alias", value="renpyupload")
                        gen_password_in = gr.Textbox(
                            label="Şifre (boş = rastgele üretilir)", type="password"
                        )
                    gen_btn = gr.Button("Yeni Anahtar Oluştur")

                keystore_file = gr.File(label=".keystore / .jks dosyası", type="filepath")
                keystore_alias = gr.Textbox(label="Anahtar Alias'ı")
                keystore_password = gr.Textbox(
                    label="Anahtar Şifresi (üretilince buraya yazılır, kopyalayıp saklayın)"
                )

                gen_btn.click(
                    fn=generate_keystore,
                    inputs=[gen_alias_in, gen_password_in],
                    outputs=[keystore_file, keystore_alias, keystore_password],
                )

            build_btn = gr.Button("Android Paketini Oluştur", variant="primary")

        with gr.Column():
            log_box = gr.Textbox(
                label="Derleme Günlüğü", lines=22, max_lines=45, autoscroll=True
            )
            output_files = gr.File(label="İndirilebilir dosyalar", file_count="multiple")

    build_btn.click(
        fn=build_android_package,
        inputs=[
            project_zip, renpy_version, want_apk, want_aab,
            package_prefix, custom_icon_file,
            keystore_file, keystore_alias, keystore_password,
        ],
        outputs=[log_box, output_files],
        concurrency_limit=1,  # Aynı anda tek derleme: Android/Gradle derlemeleri ağırdır.
    )

if __name__ == "__main__":
    demo.queue(max_size=10).launch(server_name="0.0.0.0", server_port=7860)
