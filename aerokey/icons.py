"""
Android mipmap ikonlarını Ren'Py'ye bırakmadan KENDİMİZ üretiriz.

NEDEN
-----
RAPT, APK'nın ikon dosyalarını `rapt/buildlib/rapt/iconmaker.py` içinde
üretir ve bunu **pygame** ile yapar:

    pygame.image.load(...)      -> yerel görüntü çözücü
    surface.convert_alpha()     -> yerel yüzey dönüşümü
    pygame.transform.smoothscale -> yerel ölçekleyici
    surface.blit(..., BLEND_RGBA_MULT)

Hepsi yerel (native) koddur. Yerel kod çöktüğünde Python tarafında hiçbir
iz kalmaz: süreç doğrudan bir sinyalle ölür ve dışarıdan görünen tek şey
`Unable to launch Ren'Py: Status 1` olur. Bu adım tüm derlemeyle AYNI
süreçte çalıştığı için, oradaki bir çökme koca derlemeyi götürür.

Kullanıcının derlemesi tam olarak burada, "uygulama ikonu üretiliyor"
adımında ölüyordu.

ÇÖZÜM
-----
Aynı ikonları derlemeden ÖNCE, kendi sürecimizde, Pillow ile üretiyoruz.
RAPT tarafındaki yama bu hazır dosyaları bulunca `IconMaker`'ı hiç
çağırmıyor — yani pygame'e dayanan o kod yolu çalıştırılmıyor.

Neden Pillow burada olup RAPT tarafında olamıyor: Ren'Py'nin gömülü
python'unda PIL kurulu DEĞİL (doğrulandı). Bu yüzden üretimi biz yapıp
RAPT'a yalnızca dosya kopyalatıyoruz.

ÜRETİLEN DOSYALAR
-----------------
Beş yoğunluk (dpi) × üç ad = 15 PNG:

    mipmap-<dpi>/icon_background.png    108 * ölçek piksel
    mipmap-<dpi>/icon_foreground.png    108 * ölçek piksel
    mipmap-<dpi>/icon.png                48 * ölçek piksel

Üçü de zorunludur: `app-AndroidManifest.xml` `@mipmap/icon` diyor ve
`mipmap-anydpi-v26/icon.xml` (adaptif ikon) `@mipmap/icon_background` ile
`@mipmap/icon_foreground` diyor. RAPT'ın prototip ağacında bunların
hiçbirinin hazır kopyası YOKTUR, yani üretilmezlerse Gradle derlemesi
kaynak bulunamadığı için başarısız olur.

DAVRANIŞ EŞİTLİĞİ
-----------------
`iconmaker.py`'nin mantığı birebir taklit edildi: aşamalı yarılama ile
ölçekleme, 1.5 kat büyük tuvalde birleştirme, %25 kırpma ve maske ile
kanal bazında çarpma. Ölçekleyicinin kendisi bit düzeyinde aynı olamaz
(pygame'in smoothscale'i ile Pillow'un süzgeci farklı uygulamalar), ama
sonuç görsel olarak ayırt edilemez — sınamalarda ortalama kanal farkı
ölçülüyor.
"""

from __future__ import annotations

import shutil
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

# iconmaker.py'deki `sizes` listesiyle birebir aynı.
_DENSITIES = (
    ("mdpi", 1.0),
    ("hdpi", 1.5),
    ("xhdpi", 2.0),
    ("xxhdpi", 3.0),
    ("xxxhdpi", 4.0),
)

# iconmaker.write_dpi() içindeki taban boyutlar.
_LAYER_BASE = 108   # icon_background / icon_foreground
_ICON_BASE = 48     # icon (birleşik, maskelenmiş)

_FOREGROUND = "android-icon_foreground.png"
_BACKGROUND = "android-icon_background.png"
_MASK = "android-icon_mask.png"

# Üretilen dosya adları (mipmap klasörünün içinde).
_NAMES = ("icon_background", "icon_foreground", "icon")


@dataclass
class MipmapResult:
    """Mipmap üretiminin sonucu."""

    directory: Optional[Path] = None
    written: int = 0
    sources: dict = field(default_factory=dict)
    note: str = ""

    @property
    def ok(self) -> bool:
        """Yama tarafının kabul edeceği eksiksiz bir küme üretildi mi."""
        return self.directory is not None and self.written == len(_DENSITIES) * len(_NAMES)


def _scale(img, size: int):
    """
    `iconmaker.IconMaker.scale` ile aynı aşamalı yarılama.

    Özgün kod (pygame):

        while True:
            w, h = surf.get_size()
            if (w == size) and (h == size):
                break
            w = max(w // 2, size)
            h = max(h // 2, size)
            surf = pygame.transform.smoothscale(surf, (w, h))

    Tek adımda küçültmek yerine yarılayarak inmek, kenar yumuşaklığını
    korur. Kaynak hedeften KÜÇÜKSE `max(w // 2, size)` doğrudan `size`
    verir, yani tek adımda büyütülür. Aynı davranışı koruyoruz.

    Dikkat: en/boy ayrı ayrı `size`'a yakınsadığı için sonuç HER ZAMAN
    kare olur; kare olmayan bir kaynak bilinçli olarak esnetilir. Bu da
    özgün davranıştır.
    """
    from PIL import Image

    resample = getattr(Image, "Resampling", Image).BILINEAR

    while True:
        w, h = img.size
        if w == size and h == size:
            return img
        w = max(w // 2, size)
        h = max(h // 2, size)
        img = img.resize((w, h), resample)


def _load(directory: Path, templates: Optional[Path], name: str):
    """
    `iconmaker.IconMaker.load_image` ile aynı arama sırası: önce projenin
    kendi dosyası, yoksa RAPT'ın şablonu.
    """
    from PIL import Image

    candidates = [directory / name]
    if templates is not None:
        candidates.append(templates / name)

    for path in candidates:
        if path.is_file():
            with Image.open(path) as img:
                return img.convert("RGBA"), path

    raise FileNotFoundError(
        f"{name} ne projede ne de RAPT şablonlarında bulunamadı "
        f"(bakılan yerler: {', '.join(str(c) for c in candidates)})"
    )


def _make_icon(fg_src, bg_src, mask_src, size: int):
    """
    `iconmaker.IconMaker.make_icon` ile aynı birleştirme.

    Özgün kod (pygame):

        bigsize = int(1.5 * size)
        fg = self.load_foreground(bigsize)
        icon = self.load_background(bigsize)
        icon.blit(fg, (0, 0))
        offset = int(.25 * size)
        icon = icon.subsurface((offset, offset, size, size))
        mask = self.scale(self.load_image("android-icon_mask.png"), size)
        icon.blit(mask, (0, 0), None, pygame.BLEND_RGBA_MULT)

    Yani: %50 daha büyük bir tuvalde ön planı arka planın üstüne yerleştir,
    ortadan `size` kadarlık bir pencere kırp, sonra maskeyle kanal bazında
    çarp. Kırpma, adaptif ikonun kenar boşluğunu üretir.

    `mask_src` None olabilir. Maske yalnızca RAPT'ın şablon klasöründe
    bulunur; o klasör bulunamazsa maskesiz devam ediyoruz. Sonuç, köşeleri
    yuvarlatılmamış kare bir ikondur — Android 8+ zaten adaptif ikonu
    kendi maskesiyle kırpar, yani fark yalnızca çok eski sürümlerde
    görülür. Bu, ikon üretimini tümden bırakıp çökme riski olan pygame
    yoluna dönmekten çok daha iyidir.
    """
    from PIL import ImageChops

    bigsize = int(1.5 * size)

    fg = _scale(fg_src, bigsize)
    icon = _scale(bg_src, bigsize)

    # blit: ön planı kendi alfasıyla arka planın üstüne yerleştir.
    icon = icon.copy()
    icon.alpha_composite(fg, (0, 0))

    offset = int(0.25 * size)
    icon = icon.crop((offset, offset, offset + size, offset + size))

    if mask_src is None:
        return icon

    mask = _scale(mask_src, size)

    # BLEND_RGBA_MULT: dört kanalın da (alfa dahil) çarpılması.
    return ImageChops.multiply(icon, mask)


def generate_mipmaps(
    project_root: Path,
    templates_dir: Optional[Path],
    out_dir: Path,
) -> MipmapResult:
    """
    15 mipmap PNG'sini `out_dir` altına, RAPT'ın beklediği klasör yapısıyla
    yazar:

        out_dir/mipmap-mdpi/icon.png
        out_dir/mipmap-mdpi/icon_background.png
        ...

    ASLA hata fırlatmaz. Bir sorun çıkarsa `ok` False döner ve çağıran
    taraf RAPT'ın kendi IconMaker'ına geri düşer — yani bu modül en kötü
    ihtimalle "hiçbir şey yapmamış" olur, derlemeyi bozmaz.
    """
    try:
        from PIL import Image  # noqa: F401
    except ImportError:
        return MipmapResult(note="Pillow kurulu değil, mipmap üretimi atlandı.")

    try:
        fg_src, fg_path = _load(project_root, templates_dir, _FOREGROUND)
        bg_src, bg_path = _load(project_root, templates_dir, _BACKGROUND)
    except Exception as exc:  # noqa: BLE001
        return MipmapResult(note=f"İkon kaynağı okunamadı: {exc}")

    # Maske İSTEĞE BAĞLI: bulunamazsa maskesiz devam ediyoruz. Bunun için
    # ikon üretimini tümden bırakmak, bizi çökme riski olan pygame yoluna
    # geri gönderirdi — yani çareyi derde tercih etmiş olurduk.
    try:
        mask_src, mask_path = _load(project_root, templates_dir, _MASK)
    except Exception:  # noqa: BLE001
        mask_src, mask_path = None, None

    sources = {
        "ön plan": str(fg_path),
        "arka plan": str(bg_path),
        "maske": str(mask_path) if mask_path else "yok (maskesiz üretiliyor)",
    }

    # Yarım kalmış bir küme, eksiksiz bir kümeden daha tehlikelidir:
    # RAPT tarafındaki yama "hepsi var mı" diye bakıyor, ama yine de
    # temiz bir klasörle başlıyoruz.
    try:
        if out_dir.exists():
            shutil.rmtree(out_dir)
        out_dir.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        return MipmapResult(note=f"Mipmap klasörü hazırlanamadı: {exc}")

    written = 0
    try:
        for dpi, scale in _DENSITIES:
            target_dir = out_dir / f"mipmap-{dpi}"
            target_dir.mkdir(parents=True, exist_ok=True)

            layer_size = int(scale * _LAYER_BASE)
            icon_size = int(scale * _ICON_BASE)

            # Projenin kendi hazır dosyası varsa (android-icon-hdpi.png gibi)
            # iconmaker onu olduğu gibi kopyalar; aynısını yapıyoruz.
            for name, size, producer in (
                ("icon_background", layer_size, lambda s: _scale(bg_src, s)),
                ("icon_foreground", layer_size, lambda s: _scale(fg_src, s)),
                ("icon", icon_size,
                 lambda s: _make_icon(fg_src, bg_src, mask_src, s)),
            ):
                override = project_root / f"android-{name}-{dpi}.png"
                target = target_dir / f"{name}.png"

                if override.is_file():
                    shutil.copy(override, target)
                else:
                    producer(size).save(target)

                written += 1
    except Exception as exc:  # noqa: BLE001
        return MipmapResult(
            directory=out_dir,
            written=written,
            sources=sources,
            note=f"Mipmap üretimi yarıda kesildi: {exc}",
        )

    return MipmapResult(
        directory=out_dir,
        written=written,
        sources=sources,
        note="üretildi",
    )
