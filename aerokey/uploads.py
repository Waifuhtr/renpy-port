"""
Yüklenen proje ZIP'lerini Space kapanana kadar saklayan önbellek.

Neden gerekli
-------------
Derleme hattı, her denemede aynı ZIP'in yeniden yüklenmesini gerektiriyordu:
`run_build` işi bitirince yüklenen dosyayı siliyordu. Bir oyunu birkaç kez
denemek (hata düzelt -> tekrar derle) gerektiğinde, 300 MB'lık bir projeyi
her seferinde baştan yüklemek dakikalar alıyor ve hata çözümünü
yavaşlatıyor.

Artık yüklenen dosya ayrı bir önbellek klasöründe duruyor ve bir sonraki
derlemede yeniden yüklenmeden kullanılabiliyor.

Ömür
----
Önbellek `/tmp` altında, yani KONTEYNERIN ömrü kadar yaşıyor. Space
yeniden başladığında (ya da uyku sonrası yeniden kurulduğunda) kendiliğinden
boşalıyor. Bu bilinçli: kalıcı diske yazmak, kullanıcının ödediği kalıcı
alanı oyun dosyalarıyla doldururdu.

Süreç yeniden başlar ama konteyner yaşamaya devam ederse (uvicorn'un kendini
yeniden başlatması gibi) bellekteki kayıt kaybolur; bu yüzden her girdinin
yanına `meta.json` yazıyoruz ve açılışta diskten yeniden okuyoruz.

Yer doluluğu
------------
Sınırsız büyümesine izin verilmiyor: toplam boyut sınırı aşılırsa EN ESKİ
KULLANILAN girdiler siliniyor (LRU). Şu anda bir derlemede KULLANILAN
girdiye dokunulmuyor — silinirse derleme yarıda kalırdı.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import threading
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

# Dosya adından türetilen görüntü adı için güvenli karakterler.
_UNSAFE_NAME_RE = re.compile(r"[^A-Za-z0-9._()\[\]-]+")
_ID_RE = re.compile(r"^[0-9a-f]{12,32}$")

_META_NAME = "meta.json"
_DATA_NAME = "proje.zip"

# Varsayılan sınırlar. Ortam değişkenleriyle değiştirilebilir.
_DEFAULT_MAX_GB = float(os.environ.get("AEROKEY_UPLOAD_CACHE_GB", "6"))
# Diskte bu kadar boş kalmayacaksa yeni yükleme almadan önce yer açılır.
_MIN_FREE_GB = float(os.environ.get("AEROKEY_UPLOAD_MIN_FREE_GB", "3"))


def safe_display_name(name: str) -> str:
    """Kullanıcının dosya adını, gösterime uygun ve zararsız hâle getirir."""
    temiz = _UNSAFE_NAME_RE.sub("_", (name or "").strip())
    temiz = temiz.strip("._ ") or "proje.zip"
    return temiz[:120]


@dataclass
class CachedUpload:
    """Önbellekteki tek bir yükleme."""

    id: str
    name: str
    size: int
    sha256: str
    created_at: float
    last_used: float
    uses: int = 0
    # Şu anda kaç derleme bu dosyayı kullanıyor. Sıfırdan büyükse
    # yer açmak için bile SİLİNMEZ.
    active: int = field(default=0, compare=False)
    # Yükleme bittiğinde meta.json'a yazılan boyut. `size` diskteki
    # gerçek boyut olduğu için ikisinin farkı = dosya diskte kısalmış.
    recorded_size: int = field(default=0, compare=False)

    def truncated(self) -> bool:
        """Dosya, yüklendiği andakinden KISA mı? (bozulma işareti)"""
        return bool(self.recorded_size) and self.size != self.recorded_size

    def public(self) -> dict:
        return {
            "id": self.id,
            "name": self.name,
            "size": self.size,
            "sha256": self.sha256,
            "created_at": self.created_at,
            "last_used": self.last_used,
            "uses": self.uses,
        }


class UploadCache:
    """Yüklenen ZIP'leri saklayan, iş parçacığı güvenli önbellek."""

    def __init__(self, root: Path, max_bytes: Optional[int] = None) -> None:
        self.root = root
        self.max_bytes = (
            int(_DEFAULT_MAX_GB * 1024 ** 3) if max_bytes is None else max_bytes
        )
        self._lock = threading.Lock()
        self._entries: dict[str, CachedUpload] = {}
        self.root.mkdir(parents=True, exist_ok=True)
        self._reload()

    # -- disk <-> bellek ---------------------------------------------------

    def _dir(self, entry_id: str) -> Path:
        return self.root / entry_id

    def path_of(self, entry_id: str) -> Path:
        return self._dir(entry_id) / _DATA_NAME

    def _reload(self) -> None:
        """
        Diskteki girdileri belleğe okur.

        Süreç yeniden başladığında önbelleğin kaybolmaması için var:
        dosyalar duruyorsa kayıtları da geri kurabiliyoruz.
        """
        for child in sorted(self.root.iterdir()) if self.root.is_dir() else []:
            if not child.is_dir() or not _ID_RE.match(child.name):
                continue
            meta_path = child / _META_NAME
            data_path = child / _DATA_NAME
            if not data_path.is_file():
                shutil.rmtree(child, ignore_errors=True)
                continue
            try:
                meta = json.loads(meta_path.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                meta = {}
            try:
                boyut = data_path.stat().st_size
            except OSError:
                continue
            # Boyut olarak DISKTEKI gerçek boyutu alıyoruz.
            #
            # Eskiden meta.json'daki değer tercih ediliyordu; bu, yarım
            # kalmış ya da bozulmuş bir dosyayı "tam boyutlu" gösterip
            # arızayı GİZLİYORDU: arayüz 784 MB yazarken diskteki dosya
            # eksik olabiliyordu. Fark varsa kaydı işaretliyoruz ki
            # derleme başlamadan önce anlaşılır bir hata verilebilsin.
            meta_boyut = int(meta.get("recorded_size") or meta.get("size") or 0)
            self._entries[child.name] = CachedUpload(
                id=child.name,
                name=safe_display_name(str(meta.get("name") or _DATA_NAME)),
                size=boyut,
                sha256=str(meta.get("sha256") or ""),
                created_at=float(meta.get("created_at") or time.time()),
                last_used=float(meta.get("last_used") or time.time()),
                uses=int(meta.get("uses") or 0),
                recorded_size=meta_boyut,
            )

    def _write_meta(self, entry: CachedUpload) -> None:
        # `recorded_size` AYRI yazılıyor: `size` diskteki güncel boyut
        # olduğu için, bozulmuş bir dosyanın meta'sı yeniden yazıldığında
        # (ör. her kullanımda) doğru boyut kaybolurdu — ve dosyanın
        # kısaldığını gösteren tek kanıt silinirdi.
        veri = dict(entry.public())
        veri["recorded_size"] = entry.recorded_size or entry.size
        try:
            (self._dir(entry.id) / _META_NAME).write_text(
                json.dumps(veri, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
        except OSError:
            pass

    # -- sorgulama ---------------------------------------------------------

    def get(self, entry_id: str) -> Optional[CachedUpload]:
        if not entry_id or not _ID_RE.match(entry_id):
            return None
        with self._lock:
            entry = self._entries.get(entry_id)
            if entry is None:
                return None
            if not self.path_of(entry_id).is_file():
                # Dosya elle silinmiş; kaydı da düşür.
                self._entries.pop(entry_id, None)
                return None
            return entry

    def list(self) -> list[CachedUpload]:
        """En son kullanılan en başta olacak şekilde listeler."""
        with self._lock:
            entries = [
                e for e in self._entries.values() if self.path_of(e.id).is_file()
            ]
        entries.sort(key=lambda e: e.last_used, reverse=True)
        return entries

    def total_bytes(self) -> int:
        with self._lock:
            return sum(e.size for e in self._entries.values())

    def find_by_hash(self, sha256: str) -> Optional[CachedUpload]:
        """Aynı içerik zaten yüklenmişse onu döner (yeniden yükleme yok)."""
        if not sha256:
            return None
        with self._lock:
            for entry in self._entries.values():
                if entry.sha256 == sha256 and self.path_of(entry.id).is_file():
                    return entry
        return None

    # -- ekleme / silme ----------------------------------------------------

    def new_staging_path(self) -> tuple[str, Path]:
        """
        Yükleme sırasında yazılacak geçici yolu üretir.

        Doğrudan hedef klasöre yazmıyoruz: yarıda kesilen bir yükleme,
        önbellekte bozuk bir ZIP olarak kalırdı.
        """
        entry_id = uuid.uuid4().hex[:16]
        hedef = self._dir(entry_id)
        hedef.mkdir(parents=True, exist_ok=True)
        return entry_id, hedef / (_DATA_NAME + ".part")

    def commit(
        self, entry_id: str, part_path: Path, name: str, sha256: str
    ) -> CachedUpload:
        """
        Tamamlanan yüklemeyi önbelleğe alır.

        Aynı içerik (aynı SHA-256) zaten varsa YENİSİ ATILIR ve var olan
        kayıt döner: kullanıcı aynı dosyayı yeniden yüklediyse önbellekte
        iki kopya tutmanın anlamı yok.
        """
        try:
            boyut = part_path.stat().st_size
        except OSError:
            boyut = 0

        mevcut = self.find_by_hash(sha256)
        if mevcut is not None:
            shutil.rmtree(self._dir(entry_id), ignore_errors=True)
            self.touch(mevcut.id)
            return mevcut

        data_path = self._dir(entry_id) / _DATA_NAME
        part_path.replace(data_path)
        # Yeniden adlandırmayı da diske indir. Kalıcı disk (Storage
        # Buckets) bir ağ birimi; konteyner beklenmedik şekilde kapanırsa
        # yalnızca bellekte duran bir değişiklik KAYBOLUR ve geriye
        # yarım yazılmış bir dosya kalır.
        try:
            dir_fd = os.open(str(self._dir(entry_id)), os.O_RDONLY)
            try:
                os.fsync(dir_fd)
            finally:
                os.close(dir_fd)
        except OSError:
            pass

        simdi = time.time()
        entry = CachedUpload(
            id=entry_id,
            name=safe_display_name(name),
            size=boyut,
            sha256=sha256,
            created_at=simdi,
            last_used=simdi,
            recorded_size=boyut,
        )
        with self._lock:
            self._entries[entry_id] = entry
            # Yer açma sırasında YENİ girdinin kurban seçilmesini
            # engelliyoruz: tek başına sınırı aşan büyük bir dosya,
            # "yüklendi" denip hemen silinirdi.
            entry.active += 1
        self._write_meta(entry)
        try:
            self.enforce_limits()
        finally:
            self.release(entry_id)
        return entry

    def abort(self, entry_id: str) -> None:
        """Yarıda kalan yüklemenin izlerini siler."""
        shutil.rmtree(self._dir(entry_id), ignore_errors=True)
        with self._lock:
            self._entries.pop(entry_id, None)

    def remove(self, entry_id: str) -> bool:
        with self._lock:
            entry = self._entries.get(entry_id)
            if entry is None:
                return False
            if entry.active > 0:
                return False
            self._entries.pop(entry_id, None)
        shutil.rmtree(self._dir(entry_id), ignore_errors=True)
        return True

    def touch(self, entry_id: str) -> None:
        with self._lock:
            entry = self._entries.get(entry_id)
            if entry is None:
                return
            entry.last_used = time.time()
        self._write_meta(entry)

    def acquire(self, entry_id: str) -> Optional[CachedUpload]:
        """
        Girdiyi bir derleme için "kullanımda" işaretler.

        Kullanımdaki bir girdi, yer açmak için bile silinmiyor; aksi halde
        derleme, okuduğu dosya ayağının altından çekilince çökerdi.
        """
        with self._lock:
            entry = self._entries.get(entry_id)
            if entry is None or not self.path_of(entry_id).is_file():
                return None
            entry.active += 1
            entry.uses += 1
            entry.last_used = time.time()
        self._write_meta(entry)
        return entry

    def release(self, entry_id: str) -> None:
        with self._lock:
            entry = self._entries.get(entry_id)
            if entry is not None and entry.active > 0:
                entry.active -= 1

    # -- yer yönetimi ------------------------------------------------------

    def free_bytes(self) -> int:
        try:
            usage = shutil.disk_usage(str(self.root))
            return int(usage.free)
        except OSError:
            return 0

    def enforce_limits(self) -> list[str]:
        """
        Boyut sınırını ve asgari boş alanı sağlar; gerekirse LRU siler.

        Döner: silinen girdi kimlikleri.
        """
        silinen: list[str] = []
        min_free = int(_MIN_FREE_GB * 1024 ** 3)

        while True:
            with self._lock:
                toplam = sum(e.size for e in self._entries.values())
                adaylar = [
                    e for e in self._entries.values() if e.active == 0
                ]
            bos = self.free_bytes()
            if (toplam <= self.max_bytes and bos >= min_free) or not adaylar:
                break
            adaylar.sort(key=lambda e: e.last_used)
            kurban = adaylar[0]
            if not self.remove(kurban.id):
                break
            silinen.append(kurban.id)

        return silinen
