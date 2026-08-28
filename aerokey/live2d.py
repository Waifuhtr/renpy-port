"""
Live2D Cubism desteğini Android derlemesinde çalışır hâle getirir.

SORUN
-----
Live2D kullanan bir oyun, script'lerinde şöyle satırlar taşır:

    image Ain = Live2D("images/Ain", default_fade=0.0, loop=True)

Bu satır INIT ZAMANINDA değerlendirilir. `Live2D.__init__` içinde
`self.common` çağrılır, o da `Live2DCommon.__init__` -> `init()` ->
`onetime_init()` zincirini tetikler (renpy/gl2/live2d.py). `onetime_init`
yerel Cubism Core kütüphanesini Python çalıştırılabilirinin YANINDA arar:

    fn = os.path.join(os.path.dirname(sys.executable), "libLive2DCubismCore.so")

Bu kütüphane Ren'Py ile GELMEZ. Live2D, Inc. ile ayrı bir lisans
gerektirdiği için Ren'Py Launcher'ın "Install libraries" ekranından ELLE
kurulur. Bizim otomatik `renutil` kurulumumuz bu adımı hiç yapmaz, yani
SDK'da o dosya YOKTUR.

Sonuç: derleme, meta veri toplamak için oyunu bir kez açtığında yerel kod
NULL bir işaretçiyi çağırır ve süreç SIGSEGV ile ölür. Python'a hiç
uğramadığı için yığın izi OLUŞMAZ; günlükte yalnızca

    Launch failed (returned -11).

satırı kalır. Bu, kullanıcı açısından tamamen sessiz bir ölümdür.

SÜRÜM UYUMU — İKİNCİ VE DAHA SİNSİ KATMAN
------------------------------------------
Çekirdek kütüphaneyi bulup kurmak TEK BAŞINA yetmeyebilir. Ren'Py'nin
derlenmiş kodu, kütüphaneden belirli `csm*` sembollerini `dlsym` ile
çeker. Sembol yoksa işaretçi NULL kalır ve İLK ÇAĞRIDA yine segfault
olur — üstelik bu kez çekirdek yüklenmiş, sürüm satırı bile basılmış
olduğu için sebep daha da gizlenir.

Gerçek bir örnek (bu modülün yazılma sebebi): Ren'Py 8.5.3'ün değişiklik
günlüğü "Ren'Py now supports and requires Live2D 5.3" diyor. Cubism Core
5.1.0 ise `csmGetRenderOrders` sembolünü dışa açmıyor. Oyunun kendi
dağıtımıyla gelen 5.1.0'ı 8.5.3'e kurmak, çökmeyi ortadan KALDIRMAZ;
yalnızca yerini değiştirir.

Bu yüzden burada, kurmadan ÖNCE sembol denetimi yapıyoruz:

  - Ren'Py'nin `librenpython.so` dosyasından, gerçekte `dlsym` edilen
    `csm*` adlarını çıkarıyoruz (ikili içindeki metin sabitleri).
  - Adayın ELF dinamik sembol tablosunu SAF PYTHON ile okuyup neyi dışa
    açtığını buluyoruz.

ELF'i elle ayrıştırmak bilinçli bir tercih: `ctypes.CDLL` ile açmak
kullanıcıdan gelen YABANCI YEREL KODU kendi sürecimizde çalıştırmak
demekti (kütüphane yükleyicisi constructor'ları çalıştırır). Salt okuma
yaparak bu riski tamamen ortadan kaldırıyoruz.

ANDROID TARAFI
--------------
Telefonda `sys.executable` işe yaramaz; Ren'Py kütüphaneyi düz adıyla
arar ve Android yükleyicisi onu uygulamanın yerel kütüphane klasöründe
bulur. Oraya girmesi için dosyanın şurada olması gerekir:

    rapt/prototype/renpyandroid/src/main/jniLibs/<abi>/libLive2DCubismCore.so

RAPT'ın `copy_libs()` fonksiyonu bu ağacı her derlemede `project/` içine
kopyalar (rapt/buildlib/rapt/build.py), yani oraya konan dosya APK'ya
girer. Ren'Py Launcher'ın kendi kurulumu da tam olarak bu yolu kullanır
(launcher/game/install.rpy).

Masaüstü dağıtım paketleri ARM kütüphanesi TAŞIMAZ (yalnızca x86-64
Linux/Windows). ARM sürümü ancak Live2D'nin resmi "Cubism SDK for Native"
paketinden gelir; kullanıcı o ZIP'i projeye koyarsa buradan çıkarıyoruz.
"""

from __future__ import annotations

import re
import struct
import zipfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable, Optional

CORE_SO = "libLive2DCubismCore.so"
CORE_DLL = "Live2DCubismCore.dll"
CORE_DYLIB = "libLive2DCubismCore.dylib"

# Ren'Py'nin masaüstü çekirdeği aradığı klasör (renutil SDK'sı içinde).
LINUX_LIB_DIR = "lib/py3-linux-x86_64"

# RAPT'ın APK'ya taşıdığı yerel kütüphane ağacı.
JNILIBS = "rapt/prototype/renpyandroid/src/main/jniLibs"

# Live2D Android'de x86_64'ü DESTEKLEMEZ (Ren'Py Launcher'ın kendi
# uyarısı: "Live2D doesn't support Android x86_64").
ANDROID_ABIS = ("arm64-v8a", "armeabi-v7a")

# ELF makine kodu -> bizim kullandığımız mimari adı.
_ELF_MACHINE = {
    0x03: "x86",
    0x28: "armeabi-v7a",
    0x3E: "x86_64",
    0xB7: "arm64-v8a",
}


# --------------------------------------------------------------------------
# ELF okuma (salt okuma — yabancı yerel kod ÇALIŞTIRILMAZ)
# --------------------------------------------------------------------------

@dataclass
class ElfInfo:
    """Bir paylaşımlı kütüphaneden okuduğumuz her şey."""

    arch: str = ""
    exports: frozenset[str] = frozenset()


def read_elf(path: Path) -> Optional[ElfInfo]:
    """
    ELF dosyasının mimarisini ve dışa açtığı sembolleri döndürür.

    Dosya ELF değilse ya da beklenmedik bir biçimdeyse None döner: burada
    "anlamadım" demek, yanlış bir şey iddia etmekten iyidir.
    """
    try:
        data = path.read_bytes()
    except OSError:
        return None

    if len(data) < 64 or data[:4] != b"\x7fELF":
        return None

    is64 = data[4] == 2
    little = data[5] == 1
    endian = "<" if little else ">"

    try:
        machine = struct.unpack_from(endian + "H", data, 18)[0]

        if is64:
            shoff = struct.unpack_from(endian + "Q", data, 0x28)[0]
            shentsize = struct.unpack_from(endian + "H", data, 0x3A)[0]
            shnum = struct.unpack_from(endian + "H", data, 0x3C)[0]
        else:
            shoff = struct.unpack_from(endian + "I", data, 0x20)[0]
            shentsize = struct.unpack_from(endian + "H", data, 0x2E)[0]
            shnum = struct.unpack_from(endian + "H", data, 0x30)[0]

        if not shoff or not shnum:
            return ElfInfo(arch=_ELF_MACHINE.get(machine, ""))

        # Bölüm başlıklarını oku.
        sections = []
        for i in range(shnum):
            off = shoff + i * shentsize
            if off + shentsize > len(data):
                return None
            if is64:
                sh_type = struct.unpack_from(endian + "I", data, off + 4)[0]
                sh_offset = struct.unpack_from(endian + "Q", data, off + 0x18)[0]
                sh_size = struct.unpack_from(endian + "Q", data, off + 0x20)[0]
                sh_link = struct.unpack_from(endian + "I", data, off + 0x28)[0]
                sh_entsize = struct.unpack_from(endian + "Q", data, off + 0x38)[0]
            else:
                sh_type = struct.unpack_from(endian + "I", data, off + 4)[0]
                sh_offset = struct.unpack_from(endian + "I", data, off + 0x10)[0]
                sh_size = struct.unpack_from(endian + "I", data, off + 0x14)[0]
                sh_link = struct.unpack_from(endian + "I", data, off + 0x18)[0]
                sh_entsize = struct.unpack_from(endian + "I", data, off + 0x24)[0]
            sections.append((sh_type, sh_offset, sh_size, sh_link, sh_entsize))

        names: set[str] = set()
        sym_size = 24 if is64 else 16

        for sh_type, sh_offset, sh_size, sh_link, sh_entsize in sections:
            # YALNIZCA SHT_DYNSYM (11). `.symtab` (2) okunmamalı: oradaki
            # yerel/iç semboller `dlsym` ile BULUNAMAZ, dolayısıyla onları
            # saymak "bu kütüphane sembolü sağlıyor" diye yanlış bir sonuca
            # götürürdü. Burada tam olarak `nm -D --defined-only` ile aynı
            # kümeyi üretiyoruz.
            if sh_type != 11:
                continue
            if sh_link >= len(sections):
                continue

            _, str_off, str_size, _, _ = sections[sh_link]
            strtab = data[str_off: str_off + str_size]
            entsize = sh_entsize or sym_size

            for k in range(sh_size // entsize):
                soff = sh_offset + k * entsize
                if soff + entsize > len(data):
                    break
                st_name = struct.unpack_from(endian + "I", data, soff)[0]
                if is64:
                    st_info = data[soff + 4]
                    st_shndx = struct.unpack_from(endian + "H", data, soff + 6)[0]
                else:
                    st_info = data[soff + 12]
                    st_shndx = struct.unpack_from(endian + "H", data, soff + 14)[0]

                # SHN_UNDEF: bu kütüphanenin İSTEDİĞİ sembol, verdiği değil.
                if st_shndx == 0:
                    continue
                # STB_LOCAL (0) bağlamalı semboller dışarıdan çözülemez.
                if (st_info >> 4) == 0:
                    continue
                end = strtab.find(b"\x00", st_name)
                if end < 0:
                    continue
                names.add(strtab[st_name:end].decode("utf-8", "ignore"))

        return ElfInfo(arch=_ELF_MACHINE.get(machine, ""), exports=frozenset(names))
    except (struct.error, IndexError, ValueError):
        return None


_CSM_NAME = re.compile(rb"\x00(csm[A-Za-z0-9_]{2,60})\x00")


def required_symbols(librenpython: Path) -> frozenset[str]:
    """
    Ren'Py'nin ikilisinin gerçekten `dlsym` ettiği `csm*` adları.

    Kaynak `.pxi` dosyasını okumak yanıltıcı olurdu: SDK ile gelen kaynak,
    derlenmiş ikiliden farklı bir revizyona ait olabiliyor. Tek güvenilir
    kaynak ikilinin kendisidir.
    """
    try:
        data = librenpython.read_bytes()
    except OSError:
        return frozenset()
    return frozenset(m.group(1).decode("ascii") for m in _CSM_NAME.finditer(data))


# --------------------------------------------------------------------------
# Oyunda Live2D kullanılıyor mu
# --------------------------------------------------------------------------

_LIVE2D_CALL = re.compile(r"(?:^|\W)Live2D\s*\(")


def uses_live2d(game_dir: Path, script_text: str = "") -> list[str]:
    """
    Oyunun Live2D kullandığına dair KANITLAR.

    İki bağımsız iz arıyoruz; ikisi de tek başına yeterli sayılmıyor
    denecek kadar güçlü değil, ama birlikte oldukça kesin:
      - script'lerde `Live2D(` çağrısı,
      - `game/` altında `.moc3` model dosyası.
    """
    kanit: list[str] = []

    if script_text and _LIVE2D_CALL.search(script_text):
        kanit.append("script'lerde Live2D(...) çağrısı var")

    try:
        for path in game_dir.rglob("*.moc3"):
            kanit.append(f"Live2D modeli: {path.relative_to(game_dir).as_posix()}")
            break
    except OSError:
        pass

    return kanit


# --------------------------------------------------------------------------
# Çekirdek kütüphaneyi bulma
# --------------------------------------------------------------------------

@dataclass
class CoreCandidate:
    """Bulunan bir Cubism Core dosyası."""

    path: Path
    arch: str
    exports: frozenset[str] = frozenset()
    source: str = ""


def find_cores(roots: Iterable[Path]) -> list[CoreCandidate]:
    """
    Verilen klasörlerde duran Cubism Core kütüphanelerini bulur.

    Masaüstü dağıtımının `lib/` klasörü de buna dahildir — oyun Live2D
    kullanıyorsa çekirdek oradadır. O klasör derleme sırasında siliniyor
    (Android'de işe yaramayan motor dosyaları), bu yüzden silinmeden önce
    kurtarılan kopyanın klasörü de burada aranabilir.
    """
    bulunan: list[CoreCandidate] = []
    gorulen: set[str] = set()

    def ekle(path: Path, kaynak: str) -> None:
        info = read_elf(path)
        if info is None or not info.arch:
            return
        try:
            boyut = path.stat().st_size
        except OSError:
            return
        anahtar = f"{info.arch}:{boyut}"
        if anahtar in gorulen:
            return
        gorulen.add(anahtar)
        bulunan.append(CoreCandidate(
            path=path, arch=info.arch, exports=info.exports, source=kaynak,
        ))

    for root in roots:
        if not root or not root.is_dir():
            continue
        try:
            for path in sorted(root.rglob(CORE_SO)):
                if not path.is_file():
                    continue
                try:
                    nereden = path.relative_to(root).as_posix()
                except ValueError:
                    nereden = path.name
                ekle(path, nereden)
        except OSError:
            continue

    return bulunan


def rescue_cores(project_root: Path, stash_dir: Path) -> int:
    """
    Çekirdek kütüphaneleri, `lib/` silinmeden ÖNCE güvenli bir yere kopyalar.

    Derlenmiş masaüstü paketlerinde çekirdek `lib/py3-linux-x86_64/` altında
    durur; oysa o klasörün tamamı Android derlemesinden çıkarılıyor. Sırayı
    ters çevirmek yerine (temizlik gerçekten gerekli) dosyayı önce alıyoruz.

    Döner: kurtarılan dosya sayısı.
    """
    sayi = 0
    try:
        for path in sorted(project_root.rglob(CORE_SO)):
            if not path.is_file():
                continue
            try:
                rel = path.relative_to(project_root).as_posix()
            except ValueError:
                rel = path.name
            # Göreli yapı KORUNUYOR. Dosya adını düzleştirmek (ör.
            # "lib_py3-..._libLive2DCubismCore.so") sonradan yapılan
            # `rglob(CORE_SO)` aramasının onu bulamamasına yol açardı;
            # klasör yapısı hem adı korur hem de çakışmayı önler.
            hedef = stash_dir / rel
            try:
                hedef.parent.mkdir(parents=True, exist_ok=True)
                hedef.write_bytes(path.read_bytes())
                sayi += 1
            except OSError:
                continue
    except OSError:
        pass
    return sayi


def extract_from_sdk_zip(project_root: Path, out_dir: Path) -> list[CoreCandidate]:
    """
    "CubismSdkForNative-*.zip" içinden çekirdek kütüphaneleri çıkarır.

    Ren'Py Launcher'ın kendi kurulum desenlerinin (install.rpy) aynısını
    kullanıyoruz: Core/dll/<platform>/... yolları.
    """
    adaylar: list[CoreCandidate] = []

    try:
        zipler = sorted(project_root.glob("CubismSdkForNative-*.zip"))
    except OSError:
        return adaylar

    for zpath in zipler:
        try:
            with zipfile.ZipFile(zpath) as zf:
                for name in zf.namelist():
                    if not name.endswith(CORE_SO):
                        continue
                    # Yalnızca linux/x86_64 ve android/<abi> ilgimizi çekiyor.
                    if "/Core/dll/" not in ("/" + name):
                        continue
                    # Dosya adı korunuyor (bkz. rescue_cores'daki gerekçe).
                    hedef = out_dir / zpath.stem / name.lstrip("/")
                    try:
                        hedef.parent.mkdir(parents=True, exist_ok=True)
                        with zf.open(name) as src:
                            hedef.write_bytes(src.read())
                    except (OSError, zipfile.BadZipFile, KeyError):
                        continue
                    info = read_elf(hedef)
                    if info is None or not info.arch:
                        continue
                    adaylar.append(CoreCandidate(
                        path=hedef, arch=info.arch, exports=info.exports,
                        source=f"{zpath.name}:{name}",
                    ))
        except (OSError, zipfile.BadZipFile):
            continue

    return adaylar


# --------------------------------------------------------------------------
# Sonuç ve ana akış
# --------------------------------------------------------------------------

@dataclass
class Live2DResult:
    """`prepare` çağrısının sonucu."""

    evidence: list[str] = field(default_factory=list)
    "Oyunun Live2D kullandığına dair kanıtlar (boşsa Live2D yok)."

    desktop_installed: Optional[Path] = None
    "Derleme makinesine kurulan çekirdek."

    android_installed: list[str] = field(default_factory=list)
    "APK'ya konan ABI'ler."

    missing_symbols: list[str] = field(default_factory=list)
    "Aday çekirdekte eksik olan, Ren'Py'nin ihtiyaç duyduğu semboller."

    found: list[str] = field(default_factory=list)
    "Bulunan tüm çekirdekler (mimari + nereden geldiği)."

    fatal: str = ""
    "Doluysa derleme BAŞLATILMAMALI: kesin çökme sebebi."

    warning: str = ""
    "Derleme sürebilir ama kullanıcı bilmeli."

    note: str = ""

    @property
    def used(self) -> bool:
        return bool(self.evidence)


def prepare(
    project_root: Path,
    sdk_roots: Iterable[Path],
    work_dir: Path,
    script_text: str = "",
    extra_search: Iterable[Path] = (),
) -> Live2DResult:
    """
    Live2D için gereken yerel kütüphaneleri bulur, denetler ve kurar.

    ASLA hata fırlatmaz. Live2D kullanılmıyorsa hiçbir şey yapmaz ve
    tamamen sessiz döner — oyunların büyük çoğunluğu bu durumdadır.
    """
    game_dir = project_root / "game"
    kanit = uses_live2d(game_dir, script_text)
    if not kanit:
        return Live2DResult(note="Live2D kullanılmıyor.")

    res = Live2DResult(evidence=kanit)

    sdk_list = [s for s in sdk_roots if s.is_dir()]
    if not sdk_list:
        res.warning = (
            "Live2D kullanılıyor ama kurulu Ren'Py SDK'sı bulunamadı, "
            "çekirdek kütüphane yerleştirilemedi."
        )
        return res

    # Adayları topla: önce projedeki dosyalar, sonra Cubism SDK ZIP'i.
    adaylar = find_cores([project_root, *extra_search])
    try:
        work_dir.mkdir(parents=True, exist_ok=True)
        adaylar += extract_from_sdk_zip(project_root, work_dir / "cubism")
    except OSError:
        pass

    res.found = [f"{c.arch} ({c.source})" for c in adaylar]

    if not adaylar:
        res.fatal = (
            "Oyun Live2D kullanıyor ama Cubism Core yerel kütüphanesi "
            "(libLive2DCubismCore.so) projede BULUNAMADI.\n"
            "  Ren'Py bu kütüphaneyi kendisi getirmez; Live2D, Inc. ile ayrı "
            "bir lisans gerektirdiği için elle kurulur. Kütüphane olmadan "
            "Ren'Py, derleme sırasında oyunu açtığı anda yerel kod seviyesinde "
            "çöker (iz bırakmayan segfault).\n"
            "  Çözüm: Live2D'nin resmi 'Cubism SDK for Native' paketini "
            "(CubismSdkForNative-5-r.*.zip) proje klasörünün köküne koyup "
            "yeniden yükleyin. Hem derleme hem de telefon için gereken "
            "kütüphaneler oradan otomatik çıkarılır."
        )
        return res

    # --- Masaüstü (derleme makinesi) çekirdeği ---------------------------
    sdk = sdk_list[0]
    librenpython = sdk / LINUX_LIB_DIR / "librenpython.so"
    gerekli = required_symbols(librenpython)

    x86 = [c for c in adaylar if c.arch == "x86_64"]
    if not x86:
        res.fatal = (
            "Oyun Live2D kullanıyor ama derleme makinesi için gereken "
            "64-bit Linux Cubism Core kütüphanesi bulunamadı "
            f"(bulunanlar: {', '.join(res.found) or 'yok'}).\n"
            "  Çözüm: CubismSdkForNative-5-r.*.zip dosyasını proje klasörünün "
            "köküne koyup yeniden yükleyin."
        )
        return res

    aday = x86[0]
    eksik = sorted(gerekli - aday.exports) if gerekli else []

    if eksik:
        # Kurmak çökmeyi ORTADAN KALDIRMAZ, yalnızca yerini değiştirir:
        # sembol NULL kalır ve ilk çağrıda segfault olur. Bu yüzden
        # kurmuyor, derlemeyi burada durduruyoruz.
        res.missing_symbols = eksik
        res.fatal = (
            "Live2D Cubism Core sürümü, seçilen Ren'Py sürümüyle UYUMSUZ.\n"
            f"  Bulunan kütüphane : {aday.source}\n"
            f"  Eksik sembol(ler) : {', '.join(eksik)}\n"
            "  Ren'Py'nin derlenmiş kodu bu sembolleri kütüphaneden çekiyor; "
            "bulamayınca işaretçi boş kalıyor ve ilk çağrıda süreç segfault "
            "veriyor (günlükte yalnızca 'returned -11' görünür).\n"
            "  Ren'Py 8.5.3 kendi değişiklik günlüğünde Live2D Cubism 5.3 "
            "gerektirdiğini yazıyor; oyununuzun dağıtımıyla gelen kütüphane "
            "daha eski.\n"
            "  İki çözümden biri:\n"
            "    1. Cubism SDK'nın Ren'Py sürümünüzle uyumlu (5.3+) sürümünü "
            "CubismSdkForNative-5-r.*.zip olarak proje köküne koyun, ya da\n"
            "    2. Arayüzden oyununuzun yapıldığı Ren'Py sürümünü seçin "
            "(bu oyun Ren'Py 8.2.x ile yapılmış görünüyor)."
        )
        return res

    hedef = sdk / LINUX_LIB_DIR / CORE_SO
    try:
        hedef.parent.mkdir(parents=True, exist_ok=True)
        veri = aday.path.read_bytes()
        if not hedef.is_file() or hedef.read_bytes() != veri:
            hedef.write_bytes(veri)
            hedef.chmod(0o755)
        res.desktop_installed = hedef
    except OSError as exc:
        res.fatal = f"Cubism Core kurulamadı: {exc}"
        return res

    # --- Android (telefon) çekirdekleri ----------------------------------
    for abi in ANDROID_ABIS:
        uygun = [c for c in adaylar if c.arch == abi]
        if not uygun:
            continue
        hedef = sdk / JNILIBS / abi / CORE_SO
        try:
            hedef.parent.mkdir(parents=True, exist_ok=True)
            veri = uygun[0].path.read_bytes()
            if not hedef.is_file() or hedef.read_bytes() != veri:
                hedef.write_bytes(veri)
            res.android_installed.append(abi)
        except OSError:
            continue

    if not res.android_installed:
        res.warning = (
            "Derleme makinesi için Cubism Core kuruldu, ama TELEFON için "
            "gereken ARM sürümü bulunamadı.\n"
            "  Masaüstü dağıtım paketleri yalnızca x86-64 kütüphane taşır; "
            "ARM sürümü ancak Live2D'nin resmi Cubism SDK for Native "
            "paketinden gelir.\n"
            "  APK üretilecek ve oyun açılacak, ancak Live2D karakterleri "
            "telefonda GÖRÜNMEYECEK.\n"
            "  Düzeltmek için CubismSdkForNative-5-r.*.zip dosyasını proje "
            "klasörünün köküne koyup yeniden yükleyin."
        )

    res.note = "hazır"
    return res
