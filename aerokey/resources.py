"""
Konteynerin GERÇEK bellek ve disk durumunu raporlar.

NEDEN AYRI BİR MODÜL
--------------------
Bir derleme, Python tarafında hiçbir iz bırakmadan ölebiliyor: süreç bir
sinyalle (SIGKILL/SIGSEGV) öldürüldüğünde yığın izi oluşmaz. Bunun en sık
sebebi belleğin bitmesidir — çekirdeğin OOM-killer'ı süreci sessizce
öldürür. Böyle bir ölüm, dışarıdan bakıldığında "sebepsiz" görünür.

Bu yüzden derlemeden ÖNCE ve hata sonrasında kaynak durumunu günlüğe
yazıyoruz: bir daha olursa sebep ilk bakışta belli olsun, yeni bir tur
araştırma gerekmesin.

NEDEN /proc/meminfo TEK BAŞINA YETMEZ
-------------------------------------
Konteyner içinde `/proc/meminfo` HOST makinenin belleğini gösterir. 64 GB
RAM'li bir sunucuda çalışan, 2 GB sınırlı bir konteyner "62 GB boş" der ve
sınırına çarpıp ölür. Gerçek sınır cgroup'ta yazar; önce oraya bakıyoruz,
yoksa /proc/meminfo'ya düşüyoruz.
"""

from __future__ import annotations

import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

# cgroup v2 (modern) ve v1 (eski) yolları. Hangisi varsa o kullanılır.
_CGROUP_V2_MAX = Path("/sys/fs/cgroup/memory.max")
_CGROUP_V2_CURRENT = Path("/sys/fs/cgroup/memory.current")
_CGROUP_V1_MAX = Path("/sys/fs/cgroup/memory/memory.limit_in_bytes")
_CGROUP_V1_CURRENT = Path("/sys/fs/cgroup/memory/memory.usage_in_bytes")

# cgroup v1 "sınır yok" durumunu astronomik bir sayıyla anlatır (genelde
# 2^63'e yakın). Böyle bir değeri gerçek sınır sanmamak gerekiyor.
_NO_LIMIT_THRESHOLD = 1 << 62


@dataclass
class MemoryStatus:
    """Belleğin o anki durumu. Bilinmeyen alanlar None kalır."""

    limit: Optional[int] = None       # toplam kullanılabilir bellek (bayt)
    used: Optional[int] = None        # şu an kullanılan (bayt)
    available: Optional[int] = None   # kalan (bayt)
    source: str = "bilinmiyor"        # "cgroup v2" / "cgroup v1" / "/proc/meminfo"


def human(size: Optional[int]) -> str:
    """Bayt sayısını okunabilir hâle getirir."""
    if size is None:
        return "?"
    value = float(size)
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if value < 1024 or unit == "TB":
            if unit == "B":
                return f"{int(value)} {unit}"
            return f"{value:.1f} {unit}"
        value /= 1024
    return f"{value:.1f} TB"  # pragma: no cover - döngü zaten dönüyor


def _read_int(path: Path) -> Optional[int]:
    """Tek satırlık bir sayı dosyasını okur; okunamazsa None."""
    try:
        text = path.read_text(encoding="utf-8", errors="ignore").strip()
    except OSError:
        return None
    if not text or text == "max":  # cgroup v2'de sınırsızın karşılığı
        return None
    try:
        return int(text)
    except ValueError:
        return None


def _meminfo() -> dict:
    """/proc/meminfo'yu bayt cinsinden bir sözlüğe çevirir."""
    values: dict = {}
    try:
        raw = Path("/proc/meminfo").read_text(encoding="utf-8", errors="ignore")
    except OSError:
        return values

    for line in raw.splitlines():
        key, _, rest = line.partition(":")
        parts = rest.split()
        if not parts:
            continue
        try:
            # /proc/meminfo değerleri kB cinsindendir.
            values[key.strip()] = int(parts[0]) * 1024
        except ValueError:
            continue
    return values


def memory_status() -> MemoryStatus:
    """
    Belleğin durumunu, konteyner sınırlarını dikkate alarak döner.

    Hiçbir şey okunamazsa boş bir MemoryStatus döner; ASLA hata fırlatmaz —
    bu bir teşhis yardımcısıdır, derlemeyi düşürmemeli.
    """
    for max_path, cur_path, label in (
        (_CGROUP_V2_MAX, _CGROUP_V2_CURRENT, "cgroup v2"),
        (_CGROUP_V1_MAX, _CGROUP_V1_CURRENT, "cgroup v1"),
    ):
        limit = _read_int(max_path)
        if limit is None or limit >= _NO_LIMIT_THRESHOLD:
            continue

        used = _read_int(cur_path)
        available = None
        if used is not None:
            available = max(0, limit - used)
        return MemoryStatus(
            limit=limit, used=used, available=available, source=label
        )

    info = _meminfo()
    total = info.get("MemTotal")
    available = info.get("MemAvailable")
    used = None
    if total is not None and available is not None:
        used = max(0, total - available)

    return MemoryStatus(
        limit=total,
        used=used,
        available=available,
        source="/proc/meminfo (konteyner sınırı bulunamadı)",
    )


def disk_free(path: Path) -> Optional[int]:
    """`path`'in bulunduğu bölümde kalan boş alan (bayt)."""
    try:
        return shutil.disk_usage(path).free
    except OSError:
        return None


def directory_size(path: Path, limit_files: int = 200_000) -> Optional[int]:
    """
    Bir klasörün toplam boyutu.

    `limit_files` bir emniyet sübabı: beklenmedik biçimde devasa bir ağaçta
    saymak uğruna derlemeyi yavaşlatmayalım diye sayım o noktada kesilir
    (bu durumda dönen değer bir ALT SINIRDIR, yine de fikir verir).
    """
    if not path.is_dir():
        return None

    total = 0
    seen = 0
    try:
        for item in path.rglob("*"):
            try:
                if item.is_file() and not item.is_symlink():
                    total += item.stat().st_size
                    seen += 1
                    if seen >= limit_files:
                        break
            except OSError:
                continue
    except OSError:
        return None
    return total


def summary(disk_paths: Optional[list] = None) -> str:
    """
    Tek satırlık, günlüğe basılmaya uygun kaynak özeti.

    Örnek:
        bellek 1.2 GB boş / 2.0 GB (cgroup v2), disk 14.3 GB boş
    """
    mem = memory_status()
    parts = [
        f"bellek {human(mem.available)} boş / {human(mem.limit)} ({mem.source})"
    ]

    for path in disk_paths or []:
        free = disk_free(Path(path))
        if free is not None:
            parts.append(f"disk({path}) {human(free)} boş")

    return ", ".join(parts)


def low_memory_warning(threshold: int = 1024 * 1024 * 1024) -> Optional[str]:
    """
    Bellek, Android paketleme adımının rahatça sığacağı kadar bol değilse
    uyarı metni döner; bolsa None.

    Eşik neden 1 GB: paketleme sırasında en büyük tek seferlik yük
    private.mp3 arşivi (motor + dört mimari için native kütüphaneler) ve
    Gradle'ın JVM yığınıdır. 1 GB'ın altında bu adımlar sıkışmaya başlar.
    """
    mem = memory_status()
    if mem.available is None or mem.available >= threshold:
        return None
    return (
        f"Kullanılabilir bellek düşük: {human(mem.available)} "
        f"(toplam {human(mem.limit)}, kaynak: {mem.source}). "
        "Android paketleme adımı bellek yoğundur; süreç sessizce "
        "öldürülebilir."
    )
