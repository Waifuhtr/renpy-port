#!/usr/bin/env python3
"""
Ren'Py SDK'sının Android şablonuna (RAPT) AeroKey giriş ekranını enjekte eder.

NEDEN BÖYLE BİR ŞEY GEREKİYOR?
------------------------------
Ren'Py, bir oyunu Android'e paketlerken SDK içindeki hazır bir Android
Studio/Gradle projesini kullanır:

    <sdk>/rapt/prototype/   -> salt okunur ŞABLON (SDK ile birlikte gelir)
    <sdk>/rapt/templates/   -> Jinja2 şablonları (manifest, build.gradle, ...)
    <sdk>/rapt/project/     -> ilk derlemede prototype'tan kopyalanan ÇALIŞMA kopyası

Önemli ayrıntı: `rapt/project/` bir kez oluşturulup sonraki derlemelerde
yeniden kullanılır; ama `app/src/main/AndroidManifest.xml`, `app/build.gradle`
ve `res/values/strings.xml` gibi dosyalar HER derlemede `rapt/templates/`
içindeki Jinja2 şablonlarından yeniden üretilir. Yani üretilmiş dosyaları
düzenlemek işe yaramaz — bir sonraki derlemede sessizce geri alınır.

Bu yüzden yamayı DOĞRU KATMANA uyguluyoruz:
  * Kotlin kaynakları  -> rapt/prototype/renpyandroid/src/main/java/...
  * Kotlin eklentisi   -> rapt/prototype/**/build.gradle (bunlar üretilmiyor)
  * Manifest değişikliği -> rapt/templates/*AndroidManifest.xml (üretilen dosyanın KAYNAĞI)

Böylece Docker imajı kurulurken bir kez uygulanan yama, o SDK ile derlenen
HER oyunda otomatik olarak geçerli olur.

Kullanım:
    python3 patch_rapt.py                 # SDK'yı bulup yamayı uygular
    python3 patch_rapt.py --sdk /yol/sdk  # belirli bir SDK'ya uygular
    python3 patch_rapt.py --warm-gradle   # Gradle dağıtımını önden indirir
"""

from __future__ import annotations

import argparse
import os
import re
import shutil
import subprocess
import sys
import time
from pathlib import Path
from typing import Iterable, Optional

# Yamanın daha önce uygulandığını anlamak için kullandığımız imza.
MARKER = "AEROKEY-PATCH"

KOTLIN_PACKAGE_PATH = "com/riaslink/aerokey"
GATE_ACTIVITY = "com.riaslink.aerokey.AeroKeyGateActivity"
JOB_SERVICE = "com.riaslink.aerokey.AeroKeyNotificationJobService"

DEFAULT_KOTLIN_VERSION = "2.2.20"

# Kotlin kaynaklarının bu betiğe göre konumu.
KOTLIN_SOURCE_DIR = Path(__file__).resolve().parent / "kotlin"
AVATAR_SOURCE_DIR = Path(__file__).resolve().parent / "avatars"


class PatchError(RuntimeError):
    """Yama güvenli biçimde uygulanamadığında yükseltilir.

    Bunu bilinçli olarak ÖLÜMCÜL bir hata yapıyoruz: sessizce atlanan bir
    yama, lisans ekranı olmayan bir APK üretir ve bu ancak kullanıcı oyunu
    açtığında fark edilir. Docker imajı derlenirken gürültülü biçimde
    patlaması çok daha iyidir.
    """


# ---------------------------------------------------------------------------
# SDK bulma
# ---------------------------------------------------------------------------

def candidate_bases() -> Iterable[Path]:
    """
    renutil'in Ren'Py SDK'larını kurduğu "registry" dizini adayları.

    renkit'in kendi kaynağına göre (src/renutil.rs, `get_registry`) varsayılan
    registry `$HOME/.renutil`'dir ve her sürüm onun altında kendi sürüm
    numarasıyla bir klasöre açılır:

        $HOME/.renutil/8.5.3/rapt/...

    Aşağıdaki diğer yollar yalnızca birer emniyet payıdır; ilki gerçek
    varsayılandır.
    """
    for env_name in ("RENUTIL_REGISTRY", "RENUTIL_HOME"):
        value = os.environ.get(env_name)
        if value:
            yield Path(value)

    home = Path.home()
    yield home / ".renutil"          # renkit'in gerçek varsayılanı
    yield Path("/root/.renutil")
    yield home / ".local" / "share" / "renutil"
    yield home / ".cache" / "renutil"


def _is_sdk_dir(path: Path) -> bool:
    """
    Bir dizinin yamalanabilir bir Ren'Py SDK'sı olup olmadığını söyler.

    Yalnızca `rapt/` aramak yetmez: Ren'Py'nin arşivlenmiş eski `rapt`
    deposunda ve renpy-build kaynak ağacında da `rapt/` vardır ama içinde
    bizim yamaladığımız `prototype/` şablonu bulunmaz (o düzende doğrudan
    `project/` işlenir). Böyle bir dizini SDK sanmak, gerçek SDK dururken
    yamanın yanlış yerde patlamasına yol açar. Bu yüzden ölçütümüz doğrudan
    ihtiyaç duyduğumuz şey: `rapt/prototype/`.
    """
    return (path / "rapt" / "prototype").is_dir()


def _has_legacy_rapt(path: Path) -> bool:
    """`rapt/` var ama desteklemediğimiz eski düzende (prototype yok)."""
    return (path / "rapt").is_dir() and not (path / "rapt" / "prototype").is_dir()


def _is_installed_sdk(path: Path) -> bool:
    """
    Otomatik keşifte aradığımız ölçüt: KURULU bir Ren'Py SDK'sı.

    `rapt/prototype/` tek başına yeterli bir imza değil — Ren'Py'nin
    `renpy-build` kaynak deposunda da o klasör bulunur, ama orası bir SDK
    değildir ve yamalanması hem anlamsız hem de yanıltıcıdır. Kurulu bir
    SDK'yı ayıran kesin işaret, kökündeki `renpy.py` giriş noktasıdır;
    renkit de derlemeyi tam olarak onu çağırarak başlatır.

    Kullanıcı --sdk ile açık bir yol verdiyse bu ek koşulu aramayız: orada
    ne yaptığını bildiğini varsayarız.
    """
    return _is_sdk_dir(path) and (path / "renpy.py").is_file()


def _roots_from_candidate_bases() -> list[Path]:
    """Bilinen registry dizinlerini ve onların birinci seviye alt klasörlerini tarar."""
    found: list[Path] = []
    for base in candidate_bases():
        if not base.is_dir():
            continue
        # Hem base'in kendisi (doğrudan SDK'ya işaret ediyor olabilir) hem de
        # altındaki sürüm klasörleri (asıl yerleşim budur).
        try:
            children = sorted(p for p in base.iterdir() if p.is_dir())
        except OSError:
            children = []
        for candidate in (base, *children):
            if _is_installed_sdk(candidate):
                found.append(candidate)
    return found


def _roots_from_renutil() -> list[Path]:
    """
    renutil'e kurulumun nerede olduğunu DOĞRUDAN sorar.

    `renutil show <sürüm>` çıktısında "Location: <yol>" satırı bulunur. Yolu
    tahmin etmek yerine aracın kendisine sormak, renkit ileride varsayılan
    registry'sini değiştirse bile çalışmayı sürdürmemizi sağlar.
    """
    version = os.environ.get("RENPY_VERSION", "").strip()
    if not version:
        return []

    try:
        result = subprocess.run(
            ["renutil", "show", version],
            capture_output=True,
            text=True,
            timeout=60,
        )
    except (OSError, subprocess.SubprocessError):
        return []

    found: list[Path] = []
    for line in (result.stdout or "").splitlines():
        if line.strip().lower().startswith("location:"):
            path = Path(line.split(":", 1)[1].strip())
            if _is_installed_sdk(path):
                found.append(path)
    return found


# Dosya sistemi taramasında hiç girilmemesi gereken, büyük ve alakasız dallar.
_SCAN_SKIP_DIRS = {
    ".git", "__pycache__", "node_modules", "proc", "sys", "dev", "run",
    "var", "usr", "lib", "lib64", "bin", "sbin", "boot", "media", "mnt",
    "android-sdk", "gradle-home", ".gradle",
}
_SCAN_ROOTS = ("/root", "/home", "/opt", "/srv", "/data", "/app", "/usr/local")
_SCAN_MAX_DEPTH = 4


def _roots_from_filesystem() -> list[Path]:
    """
    Son çare: makul kökler altında sınırlı derinlikte bir tarama yapar.

    Bilinen yolların hiçbiri tutmadığında devreye girer; böylece renutil'in
    kurulum yerini değiştirmesi ya da alışılmadık bir HOME ayarı yamayı
    tamamen kırmak yerine yalnızca biraz yavaşlatır.
    """
    found: list[Path] = []
    for root in _SCAN_ROOTS:
        root_path = Path(root)
        if not root_path.is_dir():
            continue
        base_depth = len(root_path.parts)
        for dirpath, dirnames, _ in os.walk(root_path, followlinks=False):
            current = Path(dirpath)

            # Eşleşme kontrolü budamadan ÖNCE yapılmalı: azami derinlikteki
            # bir dizinde dirnames'i boşaltmak, tam orada duran geçerli bir
            # SDK'yı gözden kaçırmamıza yol açardı.
            if "rapt" in dirnames and _is_installed_sdk(current):
                found.append(current)
                dirnames[:] = []  # SDK'nın içine girmeye gerek yok
                continue

            if len(current.parts) - base_depth >= _SCAN_MAX_DEPTH:
                dirnames[:] = []
            else:
                dirnames[:] = [d for d in dirnames if d not in _SCAN_SKIP_DIRS]
    return found


def find_sdk_roots() -> list[Path]:
    """
    Yamalanabilir tüm Ren'Py SDK dizinlerini bulur.

    Üç katmanlı arama yapar (ucuzdan pahalıya): bilinen registry yolları,
    renutil'e sorma, sınırlı dosya sistemi taraması. İlk sonuç veren katman
    kazanır; hiçbiri bulamazsa boş liste döner.
    """
    seen: set[Path] = set()
    result: list[Path] = []

    for layer in (_roots_from_candidate_bases, _roots_from_renutil, _roots_from_filesystem):
        for path in layer():
            try:
                resolved = path.resolve()
            except OSError:
                continue
            if resolved not in seen:
                seen.add(resolved)
                result.append(resolved)
        if result:
            break

    return result


def resolve_sdk(explicit: Optional[str]) -> Path:
    if explicit:
        sdk = Path(explicit).expanduser().resolve()
        if not _is_sdk_dir(sdk):
            if _has_legacy_rapt(sdk):
                raise PatchError(
                    f"{sdk} içinde 'rapt/' var ama 'rapt/prototype/' yok. Bu, "
                    "desteklemediğimiz eski RAPT düzeni (Ren'Py 7.4 öncesi ya "
                    "da renpy-build kaynak ağacı). AeroKey yaması Gradle "
                    "tabanlı güncel şablonu gerektirir."
                )
            raise PatchError(
                f"{sdk} altında bir 'rapt/prototype' klasörü yok — burası bir "
                "Ren'Py SDK'sı değil."
            )
        return sdk

    roots = find_sdk_roots()
    if not roots:
        raise no_sdk_error()
    if len(roots) > 1:
        print(f"[aerokey] {len(roots)} SDK bulundu, hepsi yamalanacak.")
    return roots[0]


def no_sdk_error() -> PatchError:
    """
    Hiçbir SDK bulunamadığında kullanılacak, tanılamaya yarayan hata.

    Ayrı bir fonksiyon olmasının sebebi, hem tek-SDK hem de --all kod
    yollarının AYNI ayrıntılı mesajı vermesi; yalnızca "SDK bulunamadı"
    demek, sorunu ayıklamaya çalışan kişiye hiçbir şey söylemiyordu.
    """
    return PatchError(
        "Ren'Py SDK bulunamadı.\n"
        "  Bakılan registry yolları: "
        + ", ".join(str(b) for b in candidate_bases())
        + "\n  'renutil show $RENPY_VERSION' da bir konum vermedi "
        f"(RENPY_VERSION={os.environ.get('RENPY_VERSION', '<tanımsız>')}).\n"
        "  Şu kökler altında tarama da sonuç vermedi: "
        + ", ".join(_SCAN_ROOTS)
        + "\n  Aranan imza: <dizin>/rapt/prototype/ VE <dizin>/renpy.py "
        "(ikisi birden). İçinde yalnızca 'rapt/' olan dizinler kasıtlı "
        "atlanır — eski RAPT düzeni ve renpy-build kaynak ağacı böyledir.\n"
        "  Önce 'renutil install <sürüm>' çalıştırıldığından emin olun, "
        "ya da --sdk <yol> ile konumu açıkça belirtin."
    )


# ---------------------------------------------------------------------------
# Küçük dosya yardımcıları
# ---------------------------------------------------------------------------

def read(path: Path) -> str:
    return path.read_text(encoding="utf-8", errors="ignore")


def write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def find_files(root: Path, predicate) -> list[Path]:
    matches: list[Path] = []
    for path in root.rglob("*"):
        try:
            if path.is_file() and predicate(path):
                matches.append(path)
        except OSError:
            continue
    return matches


# ---------------------------------------------------------------------------
# 1) Kotlin kaynaklarını yerleştir
# ---------------------------------------------------------------------------

def kotlin_target_dirs(sdk: Path) -> list[Path]:
    """
    Kotlin kaynaklarının kopyalanacağı tüm konumlar.

    `prototype` her zaman hedeftir (ilk derlemede buradan kopyalanır);
    `project` yalnızca daha önce bir derleme yapıldıysa vardır ve prototype'tan
    otomatik tazelenmediği için oraya da yazmamız gerekir.
    """
    targets = []
    for module_root in (sdk / "rapt" / "prototype", sdk / "rapt" / "project"):
        module = module_root / "renpyandroid" / "src" / "main" / "java"
        if module_root.is_dir():
            targets.append(module / KOTLIN_PACKAGE_PATH)
    return targets


def asset_target_dirs(sdk: Path) -> list[Path]:
    """
    Afiş görselinin kopyalanacağı assets klasörleri.

    `renpyandroid` modülünün assets'ini kullanıyoruz, `app` modülünün
    değil: `app/src/main/assets/` Ren'Py'nin oyun arşivini koyduğu yerdir ve
    her derlemede yeniden üretilir — oraya dosya koymak kırılgan olurdu.
    Kütüphane modülünün assets'i ise APK'ya olduğu gibi birleştirilir.
    """
    targets = []
    for module_root in _gradle_project_roots(sdk):
        targets.append(module_root / "renpyandroid" / "src" / "main" / "assets")
    return targets


# Afiş her derlemede değişebildiği için sabit bir tabanla saklıyoruz;
# uzantı kaynağa göre belirlenir (ImageDecoder içeriğe bakar, ama uzantıyı
# korumak dosyayı elle incelerken işi kolaylaştırır).
BANNER_STEM = "aerokey_banner"


def install_banner(sdk: Path, source: Optional[Path]) -> Optional[str]:
    """
    Giriş ekranı afişini APK assets'ine kopyalar.

    Her çağrıda önce eski afişler temizlenir; böylece afiş kaldırıldığında
    APK'da bayat bir kopya kalmaz. Kaynak yoksa None döner ve giriş ekranı
    afişsiz çizilir.
    """
    asset_name: Optional[str] = None

    for target in asset_target_dirs(sdk):
        target.mkdir(parents=True, exist_ok=True)
        for stale in target.glob(f"{BANNER_STEM}.*"):
            try:
                stale.unlink()
            except OSError:
                pass

        if source is not None and source.is_file():
            suffix = source.suffix.lower() or ".gif"
            asset_name = f"{BANNER_STEM}{suffix}"
            shutil.copy2(source, target / asset_name)

    return asset_name


# Avatarlar da afiş gibi assets'e kopyalanır. Kaynak dosya adlarını
# OLDUĞU GİBİ kullanmıyoruz: kullanıcı boşluklu/Türkçe karakterli adlar
# koyabilir ve bu adlar hem Kotlin sabitine hem sunucuya gideceği için
# sadeleştirilmiş, sıralı adlarla yeniden yazıyoruz.
AVATAR_STEM = "aerokey_avatar"
AVATAR_SUFFIXES = (".gif", ".png", ".webp", ".jpg", ".jpeg")


def collect_avatar_sources(extra_dir: Optional[Path] = None) -> list[Path]:
    """
    Paketlenecek avatar dosyalarını toplar.

    Öncelik sırası: derleme sırasında yüklenen klasör (varsa), yoksa
    depodaki `aerokey/avatars/`. Alfabetik sıralıyoruz ki avatar numaraları
    derlemeden derlemeye kaymasın — numara oyuncunun seçimi olarak
    sunucuda saklandığı için sıranın kararlı olması gerekiyor.
    """
    roots = [d for d in (extra_dir, AVATAR_SOURCE_DIR) if d is not None and d.is_dir()]
    for root in roots:
        found = sorted(
            (p for p in root.iterdir()
             if p.is_file() and p.suffix.lower() in AVATAR_SUFFIXES),
            key=lambda p: p.name.lower(),
        )
        if found:
            return found
    return []


def install_avatars(sdk: Path, sources: list[Path]) -> list[str]:
    """
    Avatar görsellerini APK assets'ine kopyalar ve varlık adlarını döner.

    Her çağrıda önce eski avatarlar silinir; böylece bir avatar kaldırıldığında
    APK'da bayat bir kopya kalmaz.
    """
    asset_names: list[str] = []

    for target in asset_target_dirs(sdk):
        target.mkdir(parents=True, exist_ok=True)
        for stale in target.glob(f"{AVATAR_STEM}_*"):
            try:
                stale.unlink()
            except OSError:
                pass

        names: list[str] = []
        for index, source in enumerate(sources, start=1):
            suffix = source.suffix.lower() or ".gif"
            name = f"{AVATAR_STEM}_{index:02d}{suffix}"
            shutil.copy2(source, target / name)
            names.append(name)
        asset_names = names

    return asset_names


def install_kotlin_sources(sdk: Path) -> int:
    if not KOTLIN_SOURCE_DIR.is_dir():
        raise PatchError(f"Kotlin kaynak klasörü bulunamadı: {KOTLIN_SOURCE_DIR}")

    sources = sorted(KOTLIN_SOURCE_DIR.glob("*.kt"))
    if not sources:
        raise PatchError(f"{KOTLIN_SOURCE_DIR} içinde .kt dosyası yok.")

    count = 0
    for target in kotlin_target_dirs(sdk):
        target.mkdir(parents=True, exist_ok=True)
        for src in sources:
            shutil.copy2(src, target / src.name)
            count += 1
        print(f"[aerokey] {len(sources)} Kotlin dosyası -> {target}")
    return count


# ---------------------------------------------------------------------------
# 2) Gradle: Kotlin eklentisini devreye al
# ---------------------------------------------------------------------------

def detect_kotlin_version(sdk: Path) -> str:
    """
    Şablonda zaten bulunan kotlin-stdlib sürümünü yakalayıp eklenti sürümü
    olarak kullanırız. Böylece Ren'Py sürüm yükselttiğinde biz de otomatik
    uyum sağlarız ve stdlib/derleyici sürüm uyuşmazlığı yaşanmaz.
    """
    pattern = re.compile(r"org\.jetbrains\.kotlin:kotlin-stdlib[\w-]*:([\d.]+)")
    for gradle_file in (sdk / "rapt").rglob("*.gradle"):
        try:
            match = pattern.search(read(gradle_file))
        except OSError:
            continue
        if match:
            return match.group(1)
    return DEFAULT_KOTLIN_VERSION


# --- Kotlin/Java JVM hedefi hizalaması ------------------------------------
#
# Kotlin ve Java görevleri aynı JVM hedefinde derlemezse Gradle şu hatayla
# durur:
#
#   Inconsistent JVM-target compatibility detected for tasks
#   'compileReleaseJavaWithJavac' (1.8) and 'compileReleaseKotlin' (17).
#
# Bu değeri BURADA, yamalama anında sabit bir sayı olarak yazmak yanlıştı:
# Ren'Py'nin renpyandroid modülü `compileOptions` bloğunu hiç tanımlamıyor
# olabilir (bu durumda AGP kendi varsayılanını -- ki 1.8 -- uygular), ya da
# Ren'Py/AGP sürümleri arasında değişebilir. Dosyayı okuyup tahmin etmek
# yerine, hizalamayı GRADLE'IN KENDİSİNE yaptırıyoruz: aşağıdaki blok,
# yapılandırma anında modülün gerçek Java hedefini okuyup Kotlin'i tam
# olarak ona eşitliyor. Böylece değer ne olursa olsun ikisi asla ayrışamaz.
JVM_TARGET_BEGIN = f"// {MARKER}-JVMTARGET"
JVM_TARGET_END = f"// {MARKER}-JVMTARGET-END"

JVM_TARGET_BLOCK = f"""
{JVM_TARGET_BEGIN}: Kotlin'in JVM hedefini modülün Java hedefiyle eşitler.
// Sabit bir sürüm numarası YAZMIYORUZ; değeri Gradle yapılandırma anında
// projenin kendisinden okuyoruz, çünkü Ren'Py bu modülde compileOptions'ı
// hiç belirtmeyebilir (o zaman AGP varsayılanı geçerli olur) ve bu
// varsayılan sürümler arasında değişebilir.
afterEvaluate {{
    def javaMajor = android.compileOptions.targetCompatibility.majorVersion
    // Kotlin, Java 8'i "1.8" olarak adlandırır; diğerlerinde sayı aynıdır.
    def targetName = (javaMajor == "8") ? "1.8" : javaMajor
    def kotlinTarget = org.jetbrains.kotlin.gradle.dsl.JvmTarget.fromTarget(targetName)
    tasks.withType(org.jetbrains.kotlin.gradle.tasks.KotlinCompile).configureEach {{
        compilerOptions.jvmTarget.set(kotlinTarget)
    }}
}}
{JVM_TARGET_END}
"""

# Bu betiğin ESKİ bir sürümünün yazdığı, sabit numaralı blok. Yamamız
# imza (MARKER) ile korunduğu için, düzeltilmiş sürüm çalıştığında dosya
# "zaten yamalı" sayılıp bozuk blok olduğu gibi kalırdı; bu yüzden onu
# açıkça tanıyıp söküyoruz.
_LEGACY_JVM_BLOCK_RE = re.compile(
    r"\n*//\s*" + re.escape(MARKER) + r":\s*Kotlin ve Java aynı JVM hedefinde olmalı\.\s*\n"
    r"kotlin\s*\{\s*\n"
    r"\s*compilerOptions\s*\{\s*\n"
    r"\s*jvmTarget\s*=\s*org\.jetbrains\.kotlin\.gradle\.dsl\.JvmTarget\.JVM_\w+\s*\n"
    r"\s*\}\s*\n"
    r"\s*\}\s*\n?"
)

# Bizim güncel bloğumuz (yeniden yazabilmek için sökülür).
_JVM_BLOCK_RE = re.compile(
    r"\n*" + re.escape(JVM_TARGET_BEGIN) + r"[\s\S]*?" + re.escape(JVM_TARGET_END) + r"\n?"
)


def _strip_jvm_target_blocks(text: str) -> str:
    """Daha önce eklenmiş (eski ya da güncel) JVM hedefi bloklarını çıkarır."""
    text = _LEGACY_JVM_BLOCK_RE.sub("", text)
    text = _JVM_BLOCK_RE.sub("", text)
    return text


def _gradle_project_roots(sdk: Path) -> list[Path]:
    """
    Gerçekten Gradle ile derlenen kök dizinler.

    KRİTİK: Gradle derlemesi `rapt/project/` içinde çalışır — `rapt/prototype/`
    yalnızca `project/` ilk kez oluşturulurken kaynak olarak kopyalanan bir
    şablondur. `project/` bir kez var olduktan sonra `renpyandroid/build.gradle`
    ve kök `build.gradle` gibi dosyalar prototype'tan OTOMATİK tazelenmez
    (yalnızca Jinja2 şablonundan üretilen manifest/build.gradle/strings.xml
    her derlemede yenilenir, bkz. dosyanın en üstündeki not).

    Bu yüzden Kotlin eklentisini SADECE prototype'a yazmak yetmez: `project/`
    zaten varsa (ki neredeyse her zaman öyledir — ilk derlemeden sonra kalıcı
    kalır) onun kendi build.gradle dosyaları da ayrıca yamalanmalıdır, yoksa
    gerçekte derlenen kopya Kotlin eklentisinden habersiz kalır. Sonuç: .kt
    dosyaları diskte durur ama hiçbir zaman derlenmez, build hatasız biter,
    ve APK'de sınıf eksik olduğu için uygulama ClassNotFoundException ile
    çöker — Kotlin derleyicisi hiç devreye girmediği için Gradle bunu
    HİÇBİR ŞEKİLDE hata olarak bildirmez.
    """
    roots = [sdk / "rapt" / "prototype"]
    project_root = sdk / "rapt" / "project"
    if project_root.is_dir():
        roots.append(project_root)
    return roots


def _patch_root_gradle_file(root_gradle: Path, kotlin_version: str) -> bool:
    if not root_gradle.is_file():
        raise PatchError(f"Kök build.gradle bulunamadı: {root_gradle}")

    text = read(root_gradle)
    if MARKER in text:
        print(f"[aerokey] Zaten yamalı, atlanıyor: {root_gradle}")
        return False

    if "org.jetbrains.kotlin.android" in text:
        print(f"[aerokey] Kotlin eklentisini zaten tanıyor: {root_gradle}")
        return False

    plugins_match = re.search(r"plugins\s*\{", text)
    if plugins_match:
        insert_at = plugins_match.end()
        addition = (
            f"\n    // {MARKER}: AeroKey giriş ekranı Kotlin ile yazıldı.\n"
            f"    id 'org.jetbrains.kotlin.android' version '{kotlin_version}' apply false\n"
        )
        text = text[:insert_at] + addition + text[insert_at:]
    else:
        # Eski tarz buildscript{} kullanan şablonlar için classpath ekle.
        buildscript_deps = re.search(r"buildscript\s*\{[\s\S]*?dependencies\s*\{", text)
        if not buildscript_deps:
            raise PatchError(
                f"{root_gradle} içinde ne 'plugins {{' ne de 'buildscript {{ dependencies {{' "
                "bloğu bulunabildi; şablon beklenmedik biçimde değişmiş olabilir."
            )
        insert_at = buildscript_deps.end()
        addition = (
            f"\n        // {MARKER}\n"
            f"        classpath 'org.jetbrains.kotlin:kotlin-gradle-plugin:{kotlin_version}'\n"
        )
        text = text[:insert_at] + addition + text[insert_at:]

    write(root_gradle, text)
    print(f"[aerokey] Yamalandı (Kotlin {kotlin_version}): {root_gradle}")
    return True


def patch_root_gradle(sdk: Path, kotlin_version: str) -> bool:
    """
    Kök build.gradle'a Kotlin eklentisini (apply false) tanıtır.

    HEM `rapt/prototype/build.gradle` (şablon) HEM DE — varsa —
    `rapt/project/build.gradle` (gerçekte derlenen kopya) yamalanır; bkz.
    `_gradle_project_roots` neden ikisinin de gerekli olduğu için.
    """
    patched_any = False
    for root in _gradle_project_roots(sdk):
        if _patch_root_gradle_file(root / "build.gradle", kotlin_version):
            patched_any = True
    return patched_any


def _patch_module_gradle_file(module_gradle: Path) -> bool:
    """
    renpyandroid modülünü yamalar ve GEREKİRSE ESKİ YAMAYI ONARIR.

    İkinci kısım önemli: yama imzayla (MARKER) korunduğu için, betiğin eski
    bir sürümünün yazdığı bozuk bir blok "zaten yamalı" sayılıp sonsuza dek
    olduğu gibi kalırdı. Bu yüzden Kotlin eklentisi satırını yalnızca bir
    kez ekliyoruz, ama JVM hedefi bloğunu HER çalıştırmada söküp güncel
    haliyle yeniden yazıyoruz. Dosya zaten güncelse hiçbir şey yazılmaz.
    """
    if not module_gradle.is_file():
        raise PatchError(f"renpyandroid/build.gradle bulunamadı: {module_gradle}")

    original = read(module_gradle)
    text = original

    if MARKER in text:
        text = _strip_jvm_target_blocks(text).rstrip("\n") + "\n" + JVM_TARGET_BLOCK
        if text == original:
            print(f"[aerokey] Zaten güncel, atlanıyor: {module_gradle}")
            return False
        write(module_gradle, text)
        print(f"[aerokey] Eski JVM hedefi bloğu güncellendi: {module_gradle}")
        return True

    plugins_match = re.search(r"plugins\s*\{", text)
    if plugins_match:
        # Kotlin eklentisini Android eklentisinden SONRA uyguluyoruz.
        # Kotlin'in Android eklentisi, kendisinden önce bir Android
        # eklentisinin uygulanmış olmasını bekler; sırayı ters kurmak
        # bazı sürümlerde "Android Gradle plugin was not applied" hatası
        # verir.
        android_plugin = re.search(
            r"^[ \t]*id\s+['\"]com\.android\.(library|application)['\"].*$",
            text[plugins_match.end():],
            re.MULTILINE,
        )
        if android_plugin:
            insert_at = plugins_match.end() + android_plugin.end()
        else:
            insert_at = plugins_match.end()
        text = (
            text[:insert_at]
            + f"\n    // {MARKER}\n    id 'org.jetbrains.kotlin.android'"
            + text[insert_at:]
        )
    else:
        apply_match = re.search(r"apply\s+plugin:\s*['\"]com\.android\.library['\"]", text)
        if not apply_match:
            raise PatchError(
                f"{module_gradle} içinde Kotlin eklentisinin ekleneceği yer bulunamadı."
            )
        insert_at = apply_match.end()
        text = (
            text[:insert_at]
            + f"\n// {MARKER}\napply plugin: 'org.jetbrains.kotlin.android'"
            + text[insert_at:]
        )

    # Kotlin'in JVM hedefini Java'nınkiyle hizala; aksi halde AGP
    # "Inconsistent JVM-target compatibility" hatasıyla derlemeyi durdurur.
    text = _strip_jvm_target_blocks(text).rstrip("\n") + "\n" + JVM_TARGET_BLOCK

    write(module_gradle, text)
    print(f"[aerokey] Yamalandı: {module_gradle}")
    return True


def patch_module_gradle(sdk: Path) -> bool:
    """
    renpyandroid modülüne Kotlin eklentisini uygular.

    HEM `rapt/prototype/renpyandroid/build.gradle` HEM DE — varsa —
    `rapt/project/renpyandroid/build.gradle` yamalanır; aynı gerekçe için
    bkz. `_gradle_project_roots`.
    """
    patched_any = False
    for root in _gradle_project_roots(sdk):
        if _patch_module_gradle_file(root / "renpyandroid" / "build.gradle"):
            patched_any = True
    return patched_any


# ---------------------------------------------------------------------------
# 3) Manifest şablonu: giriş ekranını launcher yap
# ---------------------------------------------------------------------------

GATE_ACTIVITY_XML = f"""
        <!-- {MARKER}: AeroKey lisans geçidi. Uygulamanın açılış ekranı
             budur; doğrulama başarılı olunca Ren'Py'nin kendi
             PythonSDLActivity ekranını kendisi başlatır. -->
        <activity
            android:name="{GATE_ACTIVITY}"
            android:exported="true"
            android:launchMode="singleTask"
            android:theme="@android:style/Theme.Black.NoTitleBar.Fullscreen"
            android:configChanges="orientation|screenSize|keyboardHidden|screenLayout|smallestScreenSize|uiMode">
            <intent-filter>
                <action android:name="android.intent.action.MAIN" />
                <category android:name="android.intent.category.LAUNCHER" />
                <category android:name="android.intent.category.LEANBACK_LAUNCHER" />
            </intent-filter>
        </activity>
"""


def find_manifest_template(sdk: Path) -> Path:
    """
    Üretilen app manifestinin kaynağı olan Jinja2 şablonunu bulur.

    İsimlendirme Ren'Py sürümleri arasında değişebildiği için dosya adına
    değil, İÇERİĞE bakıyoruz: hem `<application` hem de bir LAUNCHER
    kategorisi içeren şablon aradığımız dosyadır.
    """
    templates_dir = sdk / "rapt" / "templates"
    search_roots = [templates_dir] if templates_dir.is_dir() else [sdk / "rapt"]

    def looks_like_app_manifest(path: Path) -> bool:
        if "androidmanifest" not in path.name.lower():
            return False
        try:
            text = read(path)
        except OSError:
            return False
        return "<application" in text and "android.intent.category.LAUNCHER" in text

    matches: list[Path] = []
    for root in search_roots:
        matches.extend(find_files(root, looks_like_app_manifest))

    # rapt/project/ altındaki ÜRETİLMİŞ kopyaları eleriz: onlara yazmak
    # bir sonraki derlemede geri alınır.
    matches = [m for m in matches if "/project/" not in str(m).replace(os.sep, "/")]

    if not matches:
        raise PatchError(
            "LAUNCHER içeren bir AndroidManifest şablonu bulunamadı. "
            f"Aranan yer: {[str(r) for r in search_roots]}"
        )
    if len(matches) > 1:
        # En olası aday: adında 'app' geçen.
        preferred = [m for m in matches if "app" in m.name.lower()]
        if len(preferred) == 1:
            return preferred[0]
        raise PatchError(
            "Birden fazla aday manifest şablonu bulundu, hangisinin "
            f"yamalanacağı belirsiz: {[str(m) for m in matches]}"
        )
    return matches[0]


JOB_SERVICE_XML = f"""
        <!-- {MARKER}: Oyun kapalıyken de duyuru alabilmek için düzenli
             çalışan arka plan işi. Dışa kapalı; yalnızca sistemin
             JobScheduler'ı bağlayabilir. -->
        <service
            android:name="{JOB_SERVICE}"
            android:exported="false"
            android:permission="android.permission.BIND_JOB_SERVICE" />
"""


def _top_up_manifest(template: Path, text: str) -> bool:
    """
    Daha önce yamalanmış bir manifeste, sonradan eklenen parçaları tamamlar.

    Şu an tek eksik olabilecek parça bildirim servisi; ileride başka bir şey
    eklenirse aynı kalıpla buraya girer.
    """
    if JOB_SERVICE in text:
        print(f"[aerokey] Manifest şablonu zaten güncel: {template}")
        return False

    closing = re.search(r"([ \t]*)</application>", text)
    if not closing:
        raise PatchError(f"{template} içinde </application> etiketi bulunamadı.")

    text = (
        text[: closing.start()]
        + JOB_SERVICE_XML.rstrip("\n")
        + "\n\n"
        + closing.group(1)
        + "</application>"
        + text[closing.end():]
    )
    write(template, text)
    print(f"[aerokey] Manifest şablonuna bildirim servisi eklendi: {template}")
    return True


def patch_manifest_template(sdk: Path) -> bool:
    template = find_manifest_template(sdk)
    text = read(template)

    if MARKER in text:
        # Yama imzalı olduğu için "zaten yamalı" deyip geçmek, betiğin ESKİ
        # bir sürümüyle yamalanmış bir şablona sonradan eklenen parçaların
        # (örn. bildirim servisi) hiç girmemesi demek olurdu. O yüzden
        # eksik parçaları burada tamamlıyoruz.
        return _top_up_manifest(template, text)

    launcher_count = text.count("android.intent.category.LAUNCHER")
    if launcher_count == 0:
        raise PatchError(f"{template} içinde LAUNCHER kategorisi yok.")

    # Mevcut launcher kategorilerini tamamen kaldırıyoruz. Bunları DEFAULT'a
    # çevirmek de mümkündü, ama LAUNCHER ve LEANBACK_LAUNCHER aynı
    # intent-filter içinde yan yana durduğunda iki özdeş DEFAULT satırı
    # oluşurdu. Kategorinin silinmesi, activity'yi yalnızca uygulama
    # çekmecesinden çıkarır; kod içinden açıkça başlatılmaya devam eder —
    # geçidimiz de tam olarak bunu yapıyor.
    category_re = re.compile(
        r"[ \t]*<category\s+[^>]*android:name\s*=\s*"
        r"[\"']android\.intent\.category\.(?:LEANBACK_)?LAUNCHER[\"']"
        r"[^>]*(?:/>|>\s*</category>)[ \t]*\r?\n?",
        re.IGNORECASE,
    )
    text, removed = category_re.subn("", text)
    if removed == 0:
        raise PatchError(
            f"{template} içindeki LAUNCHER kategorisi tanınan bir biçimde "
            "değil, güvenle kaldırılamadı."
        )

    # Geçit activity'sini </application> kapanışından hemen önce, o satırın
    # kendi girintisini koruyarak ekle.
    closing = re.search(r"([ \t]*)</application>", text)
    if not closing:
        raise PatchError(f"{template} içinde </application> etiketi bulunamadı.")
    text = (
        text[: closing.start()]
        + (GATE_ACTIVITY_XML + JOB_SERVICE_XML).rstrip("\n")
        + "\n\n"
        + closing.group(1)
        + "</application>"
        + text[closing.end():]
    )

    # --- Doğrulama: geriye kalan TEK launcher bizimki olmalı -------------
    # Beklenen sayıyı sabit yazmak yerine eklediğimiz bloktan sayıyoruz;
    # blok ileride değişirse doğrulama da kendiliğinden uyum sağlar.
    for category in ("LAUNCHER", "LEANBACK_LAUNCHER"):
        needle = f"android.intent.category.{category}"
        expected = GATE_ACTIVITY_XML.count(needle)
        actual = text.count(needle)
        if actual != expected:
            raise PatchError(
                f"Manifest yaması beklenen sonucu vermedi: yamadan sonra "
                f"{actual} adet {category} kaydı var ({expected} bekleniyordu). "
                "Şablon biçimi değişmiş olabilir, yama güvenli değil."
            )
    if GATE_ACTIVITY not in text:
        raise PatchError("Geçit activity'si manifeste eklenemedi.")
    if JOB_SERVICE not in text:
        raise PatchError("Bildirim arka plan servisi manifeste eklenemedi.")

    write(template, text)
    print(f"[aerokey] Manifest şablonu yamalandı: {template}")
    print(f"[aerokey]   -> launcher artık {GATE_ACTIVITY}")
    return True


# ---------------------------------------------------------------------------
# 4) Derleme başına yapılandırma damgalama
# ---------------------------------------------------------------------------

def _kotlin_literal(value) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, int):
        return str(value)
    escaped = (
        str(value)
        .replace("\\", "\\\\")
        .replace('"', '\\"')
        .replace("$", "\\$")
        .replace("\n", " ")
    )
    return f'"{escaped}"'


def render_config(values: dict) -> str:
    """AeroKeyConfig.kt dosyasının içeriğini üretir."""
    defaults = {
        "ENABLED": False,
        "BASE_URL": "https://riaslink.fun",
        "KEY_PAGE_URL": "https://riaslink.fun/bilgi",
        "GAME_ID": "riaslink_oyun_001",
        "GAME_TITLE": "Ren'Py Game",
        "HAS_BANNER": False,
        "BANNER_ASSET": "aerokey_banner.gif",
        "AVATARS": "",
        "NOTIFICATIONS_ENABLED": False,
        "FEATURE_LEADERBOARD": False,
        "FEATURE_SURVEY": False,
        "FEATURE_PROFILE": False,
        "FEATURE_BUG_REPORT": False,
        "SYNC_INTERVAL_SECONDS": 60,
        "LICENSE_RECHECK_SECONDS": 600,
        "NETWORK_TIMEOUT_MS": 15000,
    }
    defaults.update(values or {})

    types = {
        "ENABLED": "Boolean",
        "BASE_URL": "String",
        "KEY_PAGE_URL": "String",
        "GAME_ID": "String",
        "GAME_TITLE": "String",
        "HAS_BANNER": "Boolean",
        "BANNER_ASSET": "String",
        "AVATARS": "String",
        "NOTIFICATIONS_ENABLED": "Boolean",
        "FEATURE_LEADERBOARD": "Boolean",
        "FEATURE_SURVEY": "Boolean",
        "FEATURE_PROFILE": "Boolean",
        "FEATURE_BUG_REPORT": "Boolean",
        "SYNC_INTERVAL_SECONDS": "Int",
        "LICENSE_RECHECK_SECONDS": "Int",
        "NETWORK_TIMEOUT_MS": "Int",
    }

    lines = [
        "package com.riaslink.aerokey",
        "",
        "// Bu dosya Ren'Py Android Paketleyici tarafından HER DERLEMEDE",
        "// yeniden üretilir. Elle yapılan değişiklikler korunmaz.",
        "internal object AeroKeyConfig {",
    ]
    for name, kotlin_type in types.items():
        lines.append(f"    const val {name}: {kotlin_type} = {_kotlin_literal(defaults[name])}")
    lines.append("}")
    return "\n".join(lines) + "\n"


def stamp_config(
    sdk: Path,
    values: dict,
    banner_source: Optional[Path] = None,
    avatar_dir: Optional[Path] = None,
) -> list[Path]:
    """
    Derlemeye özel AeroKeyConfig.kt dosyasını, ilgili TÜM konumlara yazar.

    Hem `prototype` (ilk derleme buradan kopyalanır) hem de varsa `project`
    (sonraki derlemeler burayı kullanır ve prototype'tan tazelenmez)
    güncellenmelidir; yalnızca birine yazmak eski yapılandırmayla derleme
    yapılmasına yol açar.
    """
    # Afişi kur ve sonucu yapılandırmaya yansıt: hangi dosyanın gerçekten
    # paketlendiğini tek yerden bilmek, Kotlin tarafının var olmayan bir
    # varlığı açmaya çalışmasını engeller.
    asset_name = install_banner(sdk, banner_source)
    values = dict(values or {})
    values["HAS_BANNER"] = asset_name is not None
    if asset_name:
        values["BANNER_ASSET"] = asset_name

    # Avatarlar: hangi dosyaların gerçekten paketlendiğini tek yerden
    # bilmek, Kotlin tarafının var olmayan bir varlığı açmasını engeller.
    avatar_names = install_avatars(sdk, collect_avatar_sources(avatar_dir))
    values["AVATARS"] = ",".join(avatar_names)

    content = render_config(values)
    written: list[Path] = []
    for target_dir in kotlin_target_dirs(sdk):
        target_dir.mkdir(parents=True, exist_ok=True)
        path = target_dir / "AeroKeyConfig.kt"
        write(path, content)
        written.append(path)
    return written


# ---------------------------------------------------------------------------
# 5) Gradle dağıtımını önden indirme
# ---------------------------------------------------------------------------

def warm_gradle(sdk: Path, attempts: int = 4) -> bool:
    """
    Gradle wrapper'ın indireceği dağıtımı, imaj derlenirken önden çeker.

    Kullanıcının yaşadığı hata tam olarak buydu: ilk gerçek derlemede
    wrapper `gradle-9.1.0-bin.zip` dosyasını indirmeye çalışıyor ve sunucu
    504 dönünce tüm derleme çöküyordu. Wrapper'ı burada bir kez çalıştırmak,
    dağıtımı GRADLE_USER_HOME önbelleğine yerleştirir; çalışma anında
    indirilecek bir şey kalmaz.
    """
    wrappers = sorted(
        p for p in (sdk / "rapt").rglob("gradlew")
        if p.is_file() and "/project/" not in str(p).replace(os.sep, "/")
    )
    if not wrappers:
        print("[aerokey] UYARI: gradlew bulunamadı, Gradle ön belleklemesi atlanıyor.")
        return False

    wrapper = wrappers[0]
    try:
        wrapper.chmod(0o755)
    except OSError:
        pass

    env = dict(os.environ)
    env.setdefault("GRADLE_USER_HOME", "/opt/gradle-home")
    Path(env["GRADLE_USER_HOME"]).mkdir(parents=True, exist_ok=True)

    for attempt in range(1, attempts + 1):
        print(f"[aerokey] Gradle dağıtımı indiriliyor (deneme {attempt}/{attempts})…")
        result = subprocess.run(
            [str(wrapper), "--version", "--no-daemon"],
            cwd=str(wrapper.parent),
            env=env,
            capture_output=True,
            text=True,
        )
        if result.returncode == 0:
            print("[aerokey] Gradle önbelleğe alındı:")
            for line in result.stdout.splitlines():
                if line.strip().startswith("Gradle "):
                    print(f"[aerokey]   {line.strip()}")
            return True

        tail = (result.stdout + result.stderr).strip().splitlines()[-6:]
        print(f"[aerokey] Deneme {attempt} başarısız:")
        for line in tail:
            print(f"[aerokey]   {line}")
        if attempt < attempts:
            delay = 2 ** attempt
            print(f"[aerokey] {delay} sn beklenip yeniden denenecek…")
            time.sleep(delay)

    # Bu ölümcül DEĞİL: dağıtım çalışma anında da indirilebilir; yalnızca
    # ilk derleme yavaş olur ve ağ sorununa açık kalır.
    print("[aerokey] UYARI: Gradle önden indirilemedi. İlk derleme daha yavaş olabilir.")
    return False


# ---------------------------------------------------------------------------
# Giriş noktası
# ---------------------------------------------------------------------------

def apply_all(sdk: Path, skip_gradle_warm: bool = False) -> None:
    print(f"[aerokey] SDK yamalanıyor: {sdk}")
    kotlin_version = detect_kotlin_version(sdk)

    install_kotlin_sources(sdk)
    patch_root_gradle(sdk, kotlin_version)
    patch_module_gradle(sdk)
    patch_manifest_template(sdk)

    # Varsayılan (devre dışı) yapılandırmayı yaz ki, paketleyici herhangi bir
    # şey damgalamadan derleme yapılsa bile proje derlenebilsin.
    stamp_config(sdk, {"ENABLED": False})

    if not skip_gradle_warm:
        warm_gradle(sdk)

    print("[aerokey] Yama tamamlandı.")


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description="RAPT şablonuna AeroKey geçidini ekler.")
    parser.add_argument("--sdk", help="Ren'Py SDK kök dizini (varsayılan: otomatik bul)")
    parser.add_argument("--all", action="store_true", help="Bulunan tüm SDK'ları yamala")
    parser.add_argument("--warm-gradle", action="store_true",
                        help="Yalnızca Gradle dağıtımını önden indir")
    parser.add_argument("--skip-gradle-warm", action="store_true",
                        help="Gradle ön belleklemesini atla")
    args = parser.parse_args(argv)

    try:
        if args.all and not args.sdk:
            sdks = find_sdk_roots()
            if not sdks:
                raise no_sdk_error()
        else:
            sdks = [resolve_sdk(args.sdk)]

        for sdk in sdks:
            if args.warm_gradle:
                warm_gradle(sdk)
            else:
                apply_all(sdk, skip_gradle_warm=args.skip_gradle_warm)
    except PatchError as exc:
        print(f"[aerokey] HATA: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
