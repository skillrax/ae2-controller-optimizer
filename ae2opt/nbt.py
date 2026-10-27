"""Minimal Named Binary Tag (NBT) reader/writer, enough for structure files.

Python values map to tags like this:
  dict -> Compound          str -> String
  Byte/Short/Int/Long/Float/Double wrappers -> that tag
  plain int -> Int, plain float -> Double, bool -> Byte
  List(tag_type, items) -> List of that tag type
"""

from __future__ import annotations

import gzip
import io
import struct

END, BYTE, SHORT, INT, LONG, FLOAT, DOUBLE, BYTE_ARRAY, STRING, LIST, COMPOUND, INT_ARRAY, LONG_ARRAY = range(13)


class _Num:
    tag = None
    fmt = None

    def __init__(self, value):
        self.value = value

    def __eq__(self, other):
        return type(other) is type(self) and other.value == self.value

    def __repr__(self):
        return f"{type(self).__name__}({self.value!r})"


class Byte(_Num):
    tag, fmt = BYTE, ">b"


class Short(_Num):
    tag, fmt = SHORT, ">h"


class Int(_Num):
    tag, fmt = INT, ">i"


class Long(_Num):
    tag, fmt = LONG, ">q"


class Float(_Num):
    tag, fmt = FLOAT, ">f"


class Double(_Num):
    tag, fmt = DOUBLE, ">d"


class List:
    def __init__(self, tag: int, items):
        self.tag_type = tag
        self.items = list(items)

    def __eq__(self, other):
        return isinstance(other, List) and other.tag_type == self.tag_type and other.items == self.items

    def __repr__(self):
        return f"List({self.tag_type}, {self.items!r})"


def _tag_of(v) -> int:
    if isinstance(v, _Num):
        return v.tag
    if isinstance(v, bool):
        return BYTE
    if isinstance(v, int):
        return INT
    if isinstance(v, float):
        return DOUBLE
    if isinstance(v, str):
        return STRING
    if isinstance(v, dict):
        return COMPOUND
    if isinstance(v, List):
        return LIST
    raise TypeError(f"cannot store {type(v).__name__} in NBT")


def _write_payload(out: io.BytesIO, tag: int, v) -> None:
    if tag == COMPOUND:
        for name, child in v.items():
            ct = _tag_of(child)
            out.write(struct.pack(">b", ct))
            _write_string(out, name)
            _write_payload(out, ct, child)
        out.write(struct.pack(">b", END))
    elif tag == LIST:
        out.write(struct.pack(">bi", v.tag_type if v.items else END, len(v.items)))
        for item in v.items:
            _write_payload(out, v.tag_type, item)
    elif tag == STRING:
        _write_string(out, v)
    elif tag in (BYTE, SHORT, INT, LONG, FLOAT, DOUBLE):
        fmt = {BYTE: ">b", SHORT: ">h", INT: ">i", LONG: ">q", FLOAT: ">f", DOUBLE: ">d"}[tag]
        raw = v.value if isinstance(v, _Num) else (int(v) if tag == BYTE else v)
        out.write(struct.pack(fmt, raw))
    else:
        raise TypeError(f"unsupported tag {tag}")


def _write_string(out: io.BytesIO, s: str) -> None:
    b = s.encode("utf-8")
    out.write(struct.pack(">H", len(b)))
    out.write(b)


def dumps(root: dict, name: str = "") -> bytes:
    """Uncompressed NBT bytes for a root compound."""
    out = io.BytesIO()
    out.write(struct.pack(">b", COMPOUND))
    _write_string(out, name)
    _write_payload(out, COMPOUND, root)
    return out.getvalue()


def save(path, root: dict) -> None:
    """Write a gzip-compressed NBT file (the format of .nbt structure files)."""
    with open(path, "wb") as f:
        f.write(gzip.compress(dumps(root), mtime=0))


# --------------------------------------------------------------------------- reading (for tests / inspection)

_READ = {BYTE: (Byte, ">b", 1), SHORT: (Short, ">h", 2), INT: (Int, ">i", 4), LONG: (Long, ">q", 8),
         FLOAT: (Float, ">f", 4), DOUBLE: (Double, ">d", 8)}


def _read_payload(data: memoryview, pos: int, tag: int):
    if tag in _READ:
        cls, fmt, n = _READ[tag]
        return cls(struct.unpack_from(fmt, data, pos)[0]), pos + n
    if tag == STRING:
        n = struct.unpack_from(">H", data, pos)[0]
        return bytes(data[pos + 2:pos + 2 + n]).decode("utf-8"), pos + 2 + n
    if tag == LIST:
        t, n = struct.unpack_from(">bi", data, pos)
        pos += 5
        items = []
        for _ in range(n):
            item, pos = _read_payload(data, pos, t)
            items.append(item)
        return List(t, items), pos
    if tag == COMPOUND:
        d = {}
        while True:
            t = struct.unpack_from(">b", data, pos)[0]
            pos += 1
            if t == END:
                return d, pos
            n = struct.unpack_from(">H", data, pos)[0]
            name = bytes(data[pos + 2:pos + 2 + n]).decode("utf-8")
            pos += 2 + n
            d[name], pos = _read_payload(data, pos, t)
    if tag == INT_ARRAY:
        n = struct.unpack_from(">i", data, pos)[0]
        return list(struct.unpack_from(f">{n}i", data, pos + 4)), pos + 4 + 4 * n
    raise ValueError(f"unsupported tag {tag}")


def loads(raw: bytes) -> dict:
    if raw[:2] == b"\x1f\x8b":
        raw = gzip.decompress(raw)
    data = memoryview(raw)
    if data[0] != COMPOUND:
        raise ValueError("root tag is not a compound")
    n = struct.unpack_from(">H", data, 1)[0]
    root, _ = _read_payload(data, 3 + n, COMPOUND)
    return root


def load(path) -> dict:
    with open(path, "rb") as f:
        return loads(f.read())
