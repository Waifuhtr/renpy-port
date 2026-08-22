"""
Ren'Py çeviri paketlerini oyuna kurar ve dilin GERÇEKTEN seçilebilir
olmasını sağlar.

SORUN NEYDİ?
------------
Çeviri araçları genellikle şunu üretir:

    <paket>/tl/<dil>/*.rpy      -> `translate <dil> strings:` blokları
    <paket>/tl/<dil>/*.json     -> diyalog eşlemesi (derlenmiş oyunlar için)
    <paket>/*.rpy               -> JSON'u yükleyip say-filtresi kuran betik

Bu dosyaları `game/` içine kopyalamak TEK BAŞINA yetmez, çünkü:

1. `translate <dil> strings:` blokları yalnızca oyunun dili o dile
   AYARLANDIĞINDA devreye girer. Derlenmiş (.rpyc) bir oyunun ekranlarına
   dil seçici ekleyemezsiniz, dolayısıyla dili ayarlayacak hiçbir şey
   olmaz ve çeviri hiç görünmez.

2. Üretilen yükleyici betik JSON'u düz `open(config.gamedir + ...)` ile
   okur. Bu, PC'de çalışır ama Android'de çalışmaz: orada oyun verisi
   Ren'Py'nin kendi varlık/arşiv katmanından okunur. Üstelik hata
   `except Exception` ile yutulduğu için ortada hiçbir uyarı da çıkmaz.

Bu modül ikisini de çözer: dosyaları kurar, kırık yükleyiciyi kendi
sağlam sürümüyle değiştirir ve dili ayarlayan/sorduran bir betik üretir.

DİL KANCASI NEDEN `splashscreen`?
---------------------------------
`config.overlay_screens` ana menüde GİZLENİR (Ren'Py belgeleri), yani dil
seçimi oradan gösterilemez. Belgelenmiş `splashscreen` etiketi ise "oyun
ilk çalıştırıldığında, ana menü gösterilmeden önce" çağrılır — tam
aradığımız yer. Tek risk, oyunun o etiketi zaten tanımlamış olması
(Ren'Py'de aynı etiketin iki kez tanımlanması hatadır); bu yüzden derleme
anında oyunun .rpy/.rpyc dosyalarını tarayıp etiketin boş olduğunu
doğruluyoruz, değilse dili sormak yerine doğrudan uyguluyoruz.
"""

from __future__ import annotations

import json
import re
import shutil
import struct
import zipfile
import zlib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

# Üretilen betiğin adı. "zzz" öneki, dosyanın diğer betiklerden SONRA
# yüklenmesini sağlar; böylece varsa önceki say-filtrelerinin üzerine
# zincirleme yapabiliriz.
GENERATED_SCRIPT = "zzz_aerokey_translation.rpy"

# Üretilen yükleyicinin yazdığı, yalnızca `translate ... strings:` blokları
# tarafından KARŞILANMAYAN diyalogları içeren dosya.
TRIMMED_JSON_NAME = "aerokey_dialogue_extra.json"

# Dil kancası için kullanılabilecek etiketler (tercih sırasıyla).
CANDIDATE_LABELS = ("splashscreen", "before_main_menu")

# Paket içindeki üretilmiş yükleyiciyi tanıyan imza. Bu betiği kopyalamıyoruz;
# yerine kendi sağlam sürümümüzü koyuyoruz.
_LOADER_SIGNATURE = re.compile(r"say_menu_text_filter", re.IGNORECASE)

_TRANSLATE_STRINGS_RE = re.compile(
    r"^\s*translate\s+(\w+)\s+strings\s*:", re.MULTILINE
)
_OLD_RE = re.compile(r'^\s*old\s+"((?:[^"\\]|\\.)*)"', re.MULTILINE)


class TranslationError(RuntimeError):
    """Çeviri paketi kurulamadığında yükseltilir."""


@dataclass
class InstallResult:
    languages: list[str] = field(default_factory=list)
    copied_files: int = 0
    skipped_loaders: list[str] = field(default_factory=list)
    hook_label: Optional[str] = None
    forced_language: Optional[str] = None
    extra_dialogue: dict[str, int] = field(default_factory=dict)
    dropped_dialogue: dict[str, int] = field(default_factory=dict)
    notes: list[str] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Oyunun tanımladığı etiketleri bulma
# ---------------------------------------------------------------------------

def _rpyc_payloads(data: bytes) -> list[bytes]:
    """
    Bir .rpyc dosyasındaki sıkıştırılmış dilimleri açar.

    RPYC2 biçimi: "RENPY RPC2" başlığı + (slot, başlangıç, uzunluk) üçlüleri
    (slot == 0 ile biter) + zlib ile sıkıştırılmış veri. Etiket adları bu
    verinin içinde düz metin olarak geçer.
    """
    if not data.startswith(b"RENPY RPC2"):
        return []

    payloads: list[bytes] = []
    pos = 10
    try:
        while pos + 12 <= len(data):
            slot, start, length = struct.unpack("<III", data[pos:pos + 12])
            pos += 12
            if slot == 0:
                break
            chunk = data[start:start + length]
            try:
                payloads.append(zlib.decompress(chunk))
            except zlib.error:
                continue
    except struct.error:
        return payloads
    return payloads


def scan_defined_labels(game_dir: Path, names: tuple[str, ...]) -> set[str]:
    """
    Oyunun betiklerinde geçen etiket adlarını bulur.

    Bilinçli olarak TEMKİNLİdir: bir ad yalnızca başvuru olarak geçiyorsa
    (tanım değil) da "kullanılıyor" sayarız. Yanlış pozitifin bedeli, dil
    seçimi yerine dilin doğrudan uygulanması — yani yine çalışan bir sonuç.
    Yanlış negatifin bedeli ise ÇİFT ETİKET TANIMI, ki bu oyunu tamamen
    açılmaz hale getirirdi.
    """
    found: set[str] = set()
    needles = {name: name.encode("utf-8") for name in names}

    for path in game_dir.rglob("*"):
        if not path.is_file():
            continue
        suffix = path.suffix.lower()
        if suffix not in (".rpy", ".rpyc", ".rpym", ".rpymc"):
            continue
        # Kendi ürettiğimiz betik sayılmaz.
        if path.name == GENERATED_SCRIPT:
            continue

        try:
            data = path.read_bytes()
        except OSError:
            continue

        blobs = [data] if suffix in (".rpy", ".rpym") else _rpyc_payloads(data)
        for blob in blobs:
            for name, needle in needles.items():
                if name not in found and needle in blob:
                    found.add(name)

        if len(found) == len(names):
            break

    return found


# ---------------------------------------------------------------------------
# Paketi çözümleme
# ---------------------------------------------------------------------------

def _find_pack_root(extracted: Path) -> Path:
    """`tl/` klasörünü barındıran dizini bulur; yoksa kökü döner."""
    if (extracted / "tl").is_dir():
        return extracted
    for candidate in sorted(extracted.rglob("tl")):
        if candidate.is_dir():
            return candidate.parent
    subdirs = [p for p in extracted.iterdir() if p.is_dir()]
    if len(subdirs) == 1:
        return subdirs[0]
    return extracted


def _pack_languages(pack_root: Path) -> list[str]:
    tl = pack_root / "tl"
    if not tl.is_dir():
        return []
    return sorted(
        p.name for p in tl.iterdir()
        if p.is_dir() and p.name.lower() not in ("none", "__pycache__")
    )


def _unescape_renpy(text: str) -> str:
    """`old "..."` içindeki kaçışları çözer."""
    out = []
    i = 0
    while i < len(text):
        ch = text[i]
        if ch == "\\" and i + 1 < len(text):
            nxt = text[i + 1]
            out.append({"n": "\n", "t": "\t"}.get(nxt, nxt))
            i += 2
        else:
            out.append(ch)
            i += 1
    return "".join(out)


def _collect_string_translations(pack_root: Path, language: str) -> set[str]:
    """
    Bir dil için `translate <dil> strings:` bloklarındaki tüm `old` değerleri.

    Bunlar önemli: Ren'Py belgeleri, string çevirilerinin "diyalog olarak
    çevrilmemiş diyalog metinlerine de uygulandığını" söylüyor. Yani
    derlenmiş bir oyunda bu bloklar diyaloğu da çevirir ve JSON eşlemesinin
    büyük kısmı gereksiz hale gelir.
    """
    covered: set[str] = set()
    for path in (pack_root / "tl" / language).rglob("*.rpy"):
        try:
            text = path.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue
        if not _TRANSLATE_STRINGS_RE.search(text):
            continue
        for raw in _OLD_RE.findall(text):
            covered.add(_unescape_renpy(raw))
    return covered


def _load_json_map(path: Path) -> dict:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


# ---------------------------------------------------------------------------
# Üretilen Ren'Py betiği
# ---------------------------------------------------------------------------

def _rpy_string(value: str) -> str:
    escaped = value.replace("\\", "\\\\").replace('"', '\\"')
    return f'"{escaped}"'


def _render_script(
    languages: list[str],
    hook_label: Optional[str],
    forced_language: Optional[str],
    extra_json: dict[str, str],
    labels: dict[str, str],
) -> str:
    lines = [
        "# Ren'Py Android Paketleyici tarafından üretildi (AEROKEY-TR).",
        "#",
        "# Bu dosya, çeviri paketinin gerçekten devreye girmesini sağlar.",
        "# Elle düzenlemeyin: her derlemede yeniden üretilir.",
        "",
    ]

    if forced_language:
        lines += [
            "# Oyun doğrudan bu dilde açılır. config.language, kullanıcının",
            "# hatırlanan seçimini de geçersiz kılar (Ren'Py belgeleri).",
            f"define config.language = {_rpy_string(forced_language)}",
            "",
        ]

    if extra_json:
        lines += _render_dialogue_filter(extra_json)

    if hook_label:
        lines += _render_language_chooser(languages, hook_label, labels)

    return "\n".join(lines) + "\n"


def _render_dialogue_filter(extra_json: dict[str, str]) -> list[str]:
    """
    `translate ... strings:` bloklarının KARŞILAMADIĞI diyaloglar için yedek
    filtre. Karşılanan her şey için bu filtreyi hiç üretmiyoruz — gereksiz
    yere APK'yı büyütür ve çalışma anına iş ekler.
    """
    entries = ", ".join(
        f"{_rpy_string(lang)}: {_rpy_string(name)}"
        for lang, name in sorted(extra_json.items())
    )
    return [
        "# --- Yedek diyalog filtresi ------------------------------------",
        "# Bazı diyaloglar string çevirileriyle karşılanmıyor; onları burada",
        "# eşliyoruz. Dosyayı renpy.file() ile açıyoruz: düz open() Android'de",
        "# çalışmaz, çünkü orada oyun verisi Ren'Py'nin varlık katmanından",
        "# okunur.",
        "init python:",
        f"    _aerokey_tr_files = {{{entries}}}",
        "    _aerokey_tr_maps = {}",
        "",
        "    def _aerokey_tr_load(_lang, _fn):",
        "        import json",
        "        try:",
        "            handle = renpy.file(_fn)",
        "        except Exception:",
        "            return {}",
        "        try:",
        "            data = json.loads(handle.read().decode('utf-8'))",
        "        except Exception:",
        "            return {}",
        "        finally:",
        "            try:",
        "                handle.close()",
        "            except Exception:",
        "                pass",
        "        return data if isinstance(data, dict) else {}",
        "",
        "    for _l, _f in _aerokey_tr_files.items():",
        "        _aerokey_tr_maps[_l] = _aerokey_tr_load(_l, 'tl/' + _l + '/' + _f)",
        "",
        "    def _aerokey_tr_filter(text):",
        "        try:",
        "            _lang = _preferences.language",
        "        except Exception:",
        "            _lang = None",
        "        _m = _aerokey_tr_maps.get(_lang)",
        "        if _m:",
        "            return _m.get(text, text)",
        "        return text",
        "",
        "    # Güncel Ren'Py'de bu bir LİSTE (say_menu_text_filters); eski",
        "    # sürümlerde tekil bir fonksiyondu. İkisini de destekliyoruz ve",
        "    # varsa önceki filtreyi zincire alıyoruz.",
        "    if isinstance(getattr(config, 'say_menu_text_filters', None), list):",
        "        config.say_menu_text_filters.append(_aerokey_tr_filter)",
        "    else:",
        "        _aerokey_tr_previous = getattr(config, 'say_menu_text_filter', None)",
        "",
        "        def _aerokey_tr_chained(text):",
        "            if _aerokey_tr_previous is not None:",
        "                text = _aerokey_tr_previous(text)",
        "            return _aerokey_tr_filter(text)",
        "",
        "        config.say_menu_text_filter = _aerokey_tr_chained",
        "",
    ]


def _render_language_chooser(
    languages: list[str], hook_label: str, labels: dict[str, str]
) -> list[str]:
    choices = [(lang, labels.get(lang, lang.capitalize())) for lang in languages]
    choice_lines = ", ".join(
        f"({_rpy_string(code)}, {_rpy_string(label)})" for code, label in choices
    )

    return [
        "# --- Dil seçimi -----------------------------------------------",
        "# Ana menü gösterilmeden önce bir kez sorulur. Seçim persistent",
        "# olarak saklanır, bir daha sorulmaz.",
        "#",
        f"# Kanca olarak `{hook_label}` kullanılıyor ve bu etiketin oyunda",
        "# tanımlı OLMADIĞI derleme anında doğrulandı (Ren'Py'de aynı etiketi",
        "# iki kez tanımlamak hatadır).",
        f"define _aerokey_lang_choices = [{choice_lines}]",
        "",
        "default persistent.aerokey_language_chosen = False",
        "",
        "screen aerokey_language_chooser():",
        "    modal True",
        "    zorder 300",
        "    add \"#000000d9\"",
        "    frame:",
        "        align (0.5, 0.5)",
        "        padding (46, 38)",
        "        vbox:",
        "            spacing 12",
        "            xalign 0.5",
        "            text \"Dil / Language\" size 34 xalign 0.5",
        "            null height 10",
        "            for _code, _label in _aerokey_lang_choices:",
        "                textbutton _label:",
        "                    xalign 0.5",
        "                    action Return(_code)",
        "            textbutton \"English (original)\":",
        "                xalign 0.5",
        "                action Return(\"\")",
        "",
        f"label {hook_label}:",
        "    if not persistent.aerokey_language_chosen:",
        "        $ _aerokey_picked = renpy.call_screen(\"aerokey_language_chooser\")",
        "        $ persistent.aerokey_language_chosen = True",
        "        $ renpy.change_language(_aerokey_picked or None)",
        "    return",
        "",
    ]


# ---------------------------------------------------------------------------
# Ana kurulum
# ---------------------------------------------------------------------------

def install_pack(
    project_root: Path,
    pack_zip: Path,
    mode: str = "ask",
    language_labels: Optional[dict[str, str]] = None,
) -> InstallResult:
    """
    Çeviri paketini `game/` içine kurar ve dil kancasını üretir.

    mode:
      "ask"        -> ana menüden önce dil sorulur (kanca güvenliyse)
      "force"      -> oyun doğrudan paketin diline açılır
      "files_only" -> yalnızca dosyalar kopyalanır, dile dokunulmaz
    """
    game_dir = project_root / "game"
    if not game_dir.is_dir():
        raise TranslationError("Projede 'game/' klasörü yok.")

    work = project_root.parent / "_aerokey_tr_pack"
    if work.exists():
        shutil.rmtree(work, ignore_errors=True)
    work.mkdir(parents=True, exist_ok=True)

    try:
        with zipfile.ZipFile(pack_zip) as archive:
            archive.extractall(work)
    except zipfile.BadZipFile as exc:
        raise TranslationError("Çeviri paketi geçerli bir ZIP arşivi değil.") from exc

    pack_root = _find_pack_root(work)
    result = InstallResult()
    result.languages = _pack_languages(pack_root)

    if not result.languages:
        raise TranslationError(
            "Pakette 'tl/<dil>/' klasörü bulunamadı — bu bir Ren'Py çeviri "
            "paketi gibi görünmüyor."
        )

    # --- Dosyaları kopyala ------------------------------------------------
    for source in sorted(pack_root.rglob("*")):
        if not source.is_file():
            continue
        relative = source.relative_to(pack_root)

        # Kurulum talimatı gibi belgeler oyuna girmemeli.
        if relative.suffix.lower() in (".txt", ".md") and len(relative.parts) == 1:
            continue

        # Üretilmiş yükleyici betiğini almıyoruz: düz open() kullandığı için
        # Android'de sessizce başarısız oluyor ve filtreyi dilden bağımsız
        # uyguluyor. Yerine kendi sürümümüzü üretiyoruz.
        if relative.suffix.lower() == ".rpy":
            try:
                head = source.read_text(encoding="utf-8", errors="ignore")
            except OSError:
                head = ""
            if _LOADER_SIGNATURE.search(head):
                result.skipped_loaders.append(relative.as_posix())
                continue

        # JSON eşlemeleri aşağıda ayrıca ele alınıyor.
        if relative.suffix.lower() == ".json" and relative.parts[:1] == ("tl",):
            continue

        target = game_dir / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, target)
        result.copied_files += 1

    # --- JSON eşlemelerini buda ------------------------------------------
    extra_json: dict[str, str] = {}
    for language in result.languages:
        covered = _collect_string_translations(pack_root, language)
        merged: dict[str, str] = {}
        for json_path in sorted((pack_root / "tl" / language).rglob("*.json")):
            merged.update(_load_json_map(json_path))
        if not merged:
            continue

        leftover = {k: v for k, v in merged.items() if k not in covered}
        result.dropped_dialogue[language] = len(merged) - len(leftover)

        if leftover:
            target = game_dir / "tl" / language / TRIMMED_JSON_NAME
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(
                json.dumps(leftover, ensure_ascii=False), encoding="utf-8"
            )
            extra_json[language] = TRIMMED_JSON_NAME
            result.extra_dialogue[language] = len(leftover)

    # --- Dil kancası ------------------------------------------------------
    primary = result.languages[0]
    hook_label: Optional[str] = None
    forced: Optional[str] = None

    if mode == "force":
        forced = primary
    elif mode == "ask":
        taken = scan_defined_labels(game_dir, CANDIDATE_LABELS)
        for candidate in CANDIDATE_LABELS:
            if candidate not in taken:
                hook_label = candidate
                break
        if hook_label is None:
            # Etiketi ikinci kez tanımlamak oyunu açılmaz hale getirirdi;
            # sormak yerine dili doğrudan uyguluyoruz.
            forced = primary
            result.notes.append(
                "Oyun `" + "` ve `".join(CANDIDATE_LABELS) + "` etiketlerinin "
                "hepsini zaten kullanıyor, bu yüzden dil sorulamıyor — çift "
                "etiket tanımı oyunu açılmaz hale getirirdi. Onun yerine oyun "
                f"doğrudan '{primary}' dilinde açılacak."
            )

    result.hook_label = hook_label
    result.forced_language = forced

    if mode != "files_only" or extra_json:
        script = _render_script(
            result.languages, hook_label, forced, extra_json, language_labels or {}
        )
        (game_dir / GENERATED_SCRIPT).write_text(script, encoding="utf-8")

    shutil.rmtree(work, ignore_errors=True)
    return result
