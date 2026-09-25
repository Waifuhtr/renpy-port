"""
Python 2 döneminden kalma satırları Python 3'e uyarlamayı DENER.

Neden gerekli
-------------
Ren'Py 8 Python 3 kullanıyor. Ren'Py 6/7 için yazılmış oyunların kendi
`init python` blokları bazen Python 3'te patlıyor ve bu, derlemeyi
tamamen düşürüyor (istisna -> navigation.json'a `error: true` ->
Launcher paketlemeyi reddediyor).

Nasıl çalışıyor
---------------
Ren'Py'nin bildirdiği istisnaya ve HATANIN OLDUĞU SATIRA bakıp aday
düzeltmeler üretiyoruz. Hiçbir adaya "doğrudur" demiyoruz: her aday
uygulanıp script YENİDEN Ren'Py'ye ayrıştırtılıyor. Hata kaybolduysa
aday kalıyor, kaybolmadıysa satır bayt bayt geri alınıyor.

Yani hakem biz değiliz; oyunun gerçekten derleneceği yorumlayıcı.

Sınırlar
--------
Bu bir "Python 2'den 3'e çevirici" DEĞİL. Yalnızca tek satırlık,
anlamı belli, mekanik durumları deniyor. Bir satırın ne yapmak
istediğini anlamak gerekiyorsa dokunmuyoruz — yanlış tahminle sessizce
bozuk bir APK üretmek, hata vermekten kötüdür.
"""

from __future__ import annotations

import io
import re
import tokenize
from dataclasses import dataclass
from typing import Callable, Optional

# Karma (hash) fonksiyonları: Python 3'te bytes istiyorlar.
_HASH_CALL_RE = re.compile(
    r"(?P<pre>\bhashlib\.(?:md5|sha1|sha224|sha256|sha384|sha512)\(\s*)"
    r"(?P<arg>[^()]+?)"
    r"(?P<post>\s*\))"
)
_UPDATE_CALL_RE = re.compile(
    r"(?P<pre>\.update\(\s*)(?P<arg>[^()]+?)(?P<post>\s*\))"
)

# `.decode(...)` / `.encode(...)` çağrısının tamamı.
_DECODE_RE = re.compile(r"\.decode\(\s*[^()]*\s*\)")
_ENCODE_RE = re.compile(r"\.encode\(\s*[^()]*\s*\)")

_HAS_KEY_RE = re.compile(
    r"(?P<obj>[A-Za-z_][\w.\[\]'\"]*)\.has_key\(\s*(?P<key>[^()]+?)\s*\)"
)

_ENCODE_SUFFIX = '.encode("utf-8")'


@dataclass
class Candidate:
    """Denenecek tek bir satır düzeltmesi."""

    line: str
    description: str


def _string_tokens(line: str) -> list[tokenize.TokenInfo]:
    """
    Satırdaki metin sabitlerini bulur.

    Python'un kendi çözümleyicisini kullanıyoruz: elle yazılmış bir
    tırnak tarayıcısı kaçış dizileri, f-string'ler ve üç tırnaklı
    metinlerde yanılırdı.
    """
    govde = line.strip()
    if not govde:
        return []
    try:
        parcalar = list(tokenize.generate_tokens(io.StringIO(govde).readline))
    except (tokenize.TokenError, IndentationError, SyntaxError):
        return []
    return [t for t in parcalar if t.type == tokenize.STRING]


def _replace_token(line: str, token: tokenize.TokenInfo, yeni: str) -> Optional[str]:
    """Tek bir metin sabitini, satırın kalanına dokunmadan değiştirir."""
    govde = line.strip()
    girinti = line[: len(line) - len(line.lstrip())]
    kuyruk = line[len(girinti) + len(govde):]

    bas, son = token.start[1], token.end[1]
    if token.start[0] != 1 or token.end[0] != 1:
        # Birden çok satıra yayılan sabit; tek satırlık düzeltme yapamayız.
        return None
    if govde[bas:son] != token.string:
        return None
    return girinti + govde[:bas] + yeni + govde[son:] + kuyruk


def _bytes_literal(metin: str) -> Optional[str]:
    """`"abc"` -> `b"abc"`. Zaten bytes ya da f-string ise None."""
    onek = re.match(r"^[A-Za-z]*", metin).group(0).lower()
    if "b" in onek or "f" in onek:
        return None
    return "b" + metin


def _encode_hash_arg(line: str, _exc: str) -> list[Candidate]:
    """`hashlib.md5(x)` / `h.update(x)` argümanına `.encode()` ekler."""
    adaylar: list[Candidate] = []
    for kalip, ad in ((_HASH_CALL_RE, "hashlib"), (_UPDATE_CALL_RE, "update")):
        eslesme = kalip.search(line)
        if not eslesme:
            continue
        arg = eslesme.group("arg").strip()
        if not arg or ".encode(" in arg:
            continue
        yeni_arg = f"({arg}){_ENCODE_SUFFIX}"
        yeni = line[: eslesme.start("arg")] + yeni_arg + line[eslesme.end("arg"):]
        adaylar.append(
            Candidate(yeni, f"{ad} çağrısının argümanına .encode(\"utf-8\") eklendi")
        )
    return adaylar


def _drop_decode(line: str, _exc: str) -> list[Candidate]:
    """Python 3'te `str.decode()` yok; çağrıyı kaldırıyoruz."""
    if not _DECODE_RE.search(line):
        return []
    return [
        Candidate(_DECODE_RE.sub("", line, count=1), "gereksiz .decode() kaldırıldı")
    ]


def _drop_encode(line: str, _exc: str) -> list[Candidate]:
    """Python 3'te `bytes.encode()` yok; çağrıyı kaldırıyoruz."""
    if not _ENCODE_RE.search(line):
        return []
    return [
        Candidate(_ENCODE_RE.sub("", line, count=1), "gereksiz .encode() kaldırıldı")
    ]


def _dict_methods(line: str, exc: str) -> list[Candidate]:
    """`iteritems/iterkeys/itervalues` -> Python 3 karşılıkları."""
    adaylar = []
    for eski, yeni in (
        ("iteritems", "items"),
        ("iterkeys", "keys"),
        ("itervalues", "values"),
    ):
        if f"'{eski}'" in exc and f".{eski}(" in line:
            adaylar.append(
                Candidate(
                    line.replace(f".{eski}(", f".{yeni}(", 1),
                    f".{eski}() -> .{yeni}()",
                )
            )
    return adaylar


def _has_key(line: str, _exc: str) -> list[Candidate]:
    """`d.has_key(k)` -> `k in d`."""
    eslesme = _HAS_KEY_RE.search(line)
    if not eslesme:
        return []
    yeni = (
        line[: eslesme.start()]
        + f"{eslesme.group('key').strip()} in {eslesme.group('obj')}"
        + line[eslesme.end():]
    )
    return [Candidate(yeni, "has_key(x) -> x in ...")]


def _str_bytes_mix(line: str, _exc: str) -> list[Candidate]:
    """
    Satırda str ile bytes karışmış. Hangi tarafın değişmesi gerektiğini
    BİLMİYORUZ, o yüzden birkaç aday üretip Ren'Py'ye sorduruyoruz.
    """
    adaylar: list[Candidate] = []
    sabitler = _string_tokens(line)

    # 1) Metin sabitlerini bytes sabitine çevir (tek tek).
    for token in sabitler:
        bytes_hali = _bytes_literal(token.string)
        if bytes_hali is None:
            continue
        yeni = _replace_token(line, token, bytes_hali)
        if yeni and yeni != line:
            adaylar.append(
                Candidate(yeni, f"{token.string} -> {bytes_hali} (bytes sabiti)")
            )

    # 2) Metin sabitlerine .encode() ekle (tek tek).
    for token in sabitler:
        onek = re.match(r"^[A-Za-z]*", token.string).group(0).lower()
        if "b" in onek:
            continue
        yeni = _replace_token(line, token, f"({token.string}){_ENCODE_SUFFIX}")
        if yeni and yeni != line:
            adaylar.append(
                Candidate(yeni, f"{token.string} sabitine .encode(\"utf-8\") eklendi")
            )

    # 3) Karma/güncelleme çağrısı varsa onun argümanı.
    adaylar.extend(_encode_hash_arg(line, _exc))

    return adaylar


def _bytes_to_str(line: str, _exc: str) -> list[Candidate]:
    """Tersi durum: bytes verilmiş ama str bekleniyor."""
    adaylar: list[Candidate] = []
    for token in _string_tokens(line):
        onek = re.match(r"^[A-Za-z]*", token.string).group(0).lower()
        if "b" not in onek:
            continue
        yeni = _replace_token(line, token, token.string[len(onek):] if onek == "b"
                              else token.string.replace("b", "", 1))
        if yeni and yeni != line:
            adaylar.append(Candidate(yeni, f"{token.string} -> metin sabiti"))
    if _DECODE_RE.search(line) is None and "decode" not in line:
        adaylar.extend(_drop_encode(line, _exc))
    return adaylar


# İstisna metni -> aday üretici. Sıra önemli: ilk eşleşen kullanılır.
_RULES: tuple[tuple[str, Callable[[str, str], list[Candidate]]], ...] = (
    ("Strings must be encoded before hashing", _encode_hash_arg),
    ("object has no attribute 'decode'", _drop_decode),
    ("object has no attribute 'encode'", _drop_encode),
    ("object has no attribute 'iteritems'", _dict_methods),
    ("object has no attribute 'iterkeys'", _dict_methods),
    ("object has no attribute 'itervalues'", _dict_methods),
    ("object has no attribute 'has_key'", _has_key),
    ("a bytes-like object is required", _str_bytes_mix),
    ("can't concat str to bytes", _str_bytes_mix),
    ("must be bytes or a tuple of bytes", _str_bytes_mix),
    ("cannot use a string pattern on a bytes-like object", _str_bytes_mix),
    ("argument should be integer or bytes-like object", _str_bytes_mix),
    ("cannot use a bytes pattern on a string-like object", _bytes_to_str),
    ("argument must be str, not bytes", _bytes_to_str),
    ("Can't convert 'bytes' object to str implicitly", _bytes_to_str),
)


def candidates(line: str, exception: str, limit: int = 6) -> list[Candidate]:
    """
    Bir satır ve onun verdiği istisna için aday düzeltmeler üretir.

    Hiçbir aday "doğru" diye işaretlenmiyor: çağıran taraf her birini
    uygulayıp Ren'Py'ye doğrulatmak zorunda.
    """
    if not line.strip():
        return []

    bulunan: list[Candidate] = []
    gorulen: set[str] = {line}

    for imza, uretici in _RULES:
        if imza not in exception:
            continue
        for aday in uretici(line, exception):
            if aday.line in gorulen:
                continue
            gorulen.add(aday.line)
            bulunan.append(aday)
        if bulunan:
            break

    return bulunan[:limit]
