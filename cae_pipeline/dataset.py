"""CAE 数据集访问层：压缩包清单 + 按需（选择性）访问，不做全量解压。

数据集是整包 zip（本仓库约 1.9 GB / 解压后 2.1 GB）。本模块只读取 zip 的
中央目录得到清单，解析某个算例时再按需打开其中 ``.cas.h5`` / ``.dat.h5``：

* ``access="stream"``（默认）：借助 :class:`~cae_pipeline.h5stream.ZipMemberFile`
  直接在压缩包内随机读取 HDF5，**完全不落盘**；
* ``access="extract"``：只把用到的条目解到缓存目录（仍然不是全量解压）。
"""

from __future__ import annotations

import contextlib
import dataclasses
import os
import zipfile
from typing import Iterator

import h5py

from .h5stream import ZipMemberFile

CAS_SUFFIX = ".cas.h5"
DAT_SUFFIX = ".dat.h5"
CASE_SUFFIXES = (CAS_SUFFIX, DAT_SUFFIX)
VIDEO_SUFFIXES = (".avi", ".mp4", ".mov")
TEXT_SUFFIXES = (".txt", ".md", ".csv")


@dataclasses.dataclass
class CaseEntry:
    """一个仿真算例：同一目录下的 case 文件 + 一个或多个 data 快照。"""

    key: str                 # "Dry_surface/K86_S01"
    group: str               # "Dry_surface"
    name: str                # "K86_S01"
    cas: str | None
    dats: tuple[str, ...]
    videos: tuple[str, ...] = ()

    @property
    def cas_bytes(self) -> int:
        return self._sizes.get(self.cas, 0) if self.cas else 0

    @property
    def dat_bytes(self) -> int:
        return sum(self._sizes.get(d, 0) for d in self.dats)

    _sizes: dict = dataclasses.field(default_factory=dict, repr=False, compare=False)

    def to_dict(self) -> dict:
        return {
            "key": self.key,
            "group": self.group,
            "name": self.name,
            "case_file": os.path.basename(self.cas) if self.cas else None,
            "data_files": [os.path.basename(d) for d in self.dats],
            "case_bytes": self.cas_bytes,
            "data_bytes": self.dat_bytes,
            "videos": [os.path.basename(v) for v in self.videos],
        }


class CaeDataset:
    """zip 数据集（或已解压目录）的清单与按需访问。"""

    def __init__(self, source: str, cache_dir: str | None = None, access: str = "stream",
                 block_mb: int = 8, cache_blocks: int = 16):
        self.source = os.path.abspath(source)
        if not os.path.exists(self.source):
            raise FileNotFoundError(self.source)
        self.access = access
        self.cache_dir = os.path.abspath(cache_dir) if cache_dir else None
        self.block_mb = block_mb
        self.cache_blocks = cache_blocks
        self._zip: zipfile.ZipFile | None = None
        self._sizes: dict[str, int] = {}
        self._cases: list[CaseEntry] = []
        self._scan()

    # --- 清单 ---------------------------------------------------------
    @property
    def is_zip(self) -> bool:
        return zipfile.is_zipfile(self.source)

    def _scan(self) -> None:
        members: list[str] = []
        if self.is_zip:
            self._zip = zipfile.ZipFile(self.source)
            for info in self._zip.infolist():
                if info.is_dir():
                    continue
                members.append(info.filename)
                self._sizes[info.filename] = info.file_size
        else:
            for root, _dirs, names in os.walk(self.source):
                for n in names:
                    full = os.path.join(root, n)
                    rel = os.path.relpath(full, self.source).replace("\\", "/")
                    members.append(rel)
                    self._sizes[rel] = os.path.getsize(full)
        self._members = sorted(members)
        self._cases = self._group_cases(self._members)

    @staticmethod
    def _group_cases(members: list[str]) -> list[CaseEntry]:
        buckets: dict[str, dict] = {}
        for m in members:
            low = m.lower()
            if not low.endswith(CASE_SUFFIXES):
                continue
            folder = os.path.dirname(m).replace("\\", "/")
            bucket = buckets.setdefault(folder, {"cas": [], "dat": [], "videos": []})
            if low.endswith(CAS_SUFFIX):
                bucket["cas"].append(m)
            else:
                bucket["dat"].append(m)
        for m in members:
            if m.lower().endswith(VIDEO_SUFFIXES):
                folder = os.path.dirname(m).replace("\\", "/")
                if folder in buckets:
                    buckets[folder]["videos"].append(m)

        cases: list[CaseEntry] = []
        for folder in sorted(buckets):
            b = buckets[folder]
            parts = folder.split("/") if folder else ["<root>"]
            cases.append(
                CaseEntry(
                    key=folder,
                    group=parts[0] if len(parts) > 1 else "",
                    name=parts[-1],
                    cas=b["cas"][0] if b["cas"] else None,
                    dats=tuple(sorted(b["dat"])),
                    videos=tuple(sorted(b["videos"])),
                )
            )
        return cases

    def list_cases(self) -> list[CaseEntry]:
        for c in self._cases:
            c._sizes = self._sizes
        return list(self._cases)

    def get_case(self, key: str) -> CaseEntry:
        """按 key / 目录名 / 算例名匹配算例。"""
        cases = self.list_cases()
        norm = key.replace("\\", "/").strip("/")
        for c in cases:
            if c.key == norm:
                return c
        for c in cases:
            if c.name == norm or os.path.basename(c.key) == norm:
                return c
        hits = [c for c in cases if norm.lower() in c.key.lower()]
        if len(hits) == 1:
            return hits[0]
        if not hits:
            raise KeyError(f"未找到算例 {key!r}；可用：{', '.join(c.key for c in cases[:8])} ...")
        raise KeyError(f"{key!r} 命中多个算例：{', '.join(c.key for c in hits)}")

    def manifest(self) -> dict:
        cases = self.list_cases()
        total = sum(self._sizes.values())
        return {
            "source": self.source,
            "kind": "zip" if self.is_zip else "directory",
            "entries": len(self._members),
            "uncompressed_bytes": total,
            "cases": [c.to_dict() for c in cases],
            "texts": [m for m in self._members if m.lower().endswith(TEXT_SUFFIXES)],
            "videos": [m for m in self._members if m.lower().endswith(VIDEO_SUFFIXES)],
        }

    # --- 访问 ---------------------------------------------------------
    def member_size(self, member: str) -> int:
        return self._sizes.get(member, 0)

    def read_text(self, member: str) -> str:
        raw = self._read_bytes(member)
        for enc in ("utf-8", "latin-1"):
            try:
                return raw.decode(enc)
            except UnicodeDecodeError:
                continue
        return raw.decode("utf-8", errors="replace")

    def _read_bytes(self, member: str) -> bytes:
        if self._zip is not None:
            return self._zip.read(member)
        with open(os.path.join(self.source, member.replace("/", os.sep)), "rb") as f:
            return f.read()

    def extract(self, member: str, force: bool = False) -> str:
        """把单个条目解到缓存目录（选择性解压）。"""
        if self.cache_dir is None:
            raise ValueError("extract 模式需要 cache_dir")
        target = os.path.join(self.cache_dir, member.replace("/", os.sep))
        if os.path.isfile(target) and not force and os.path.getsize(target) == self.member_size(member):
            return target
        os.makedirs(os.path.dirname(target), exist_ok=True)
        if self._zip is None:
            import shutil

            shutil.copy2(os.path.join(self.source, member.replace("/", os.sep)), target)
            return target
        tmp = target + ".part"
        with self._zip.open(member) as src, open(tmp, "wb") as dst:
            while True:
                chunk = src.read(1 << 22)
                if not chunk:
                    break
                dst.write(chunk)
        os.replace(tmp, target)
        return target

    @contextlib.contextmanager
    def open_entry(self, member: str, mode: str = "r") -> Iterator[h5py.File]:
        """打开包内 HDF5 条目；stream 模式不落盘，extract 模式用选择性缓存。"""
        if self.access == "extract" and self._zip is not None:
            path = self.extract(member)
            with h5py.File(path, mode) as f:
                yield f
            return
        if self._zip is None:
            with h5py.File(os.path.join(self.source, member.replace("/", os.sep)), mode) as f:
                yield f
            return
        # 场很多时需要反复随机访问同一文件：按文件大小自适应块缓存，避免重解开压流
        size = self.member_size(member)
        blocks = min(64, max(self.cache_blocks, size // (self.block_mb << 20) + 2))
        fobj = ZipMemberFile(self._zip, member, block=self.block_mb << 20,
                             cache_blocks=blocks)
        try:
            with h5py.File(fobj, mode, driver="fileobj") as f:
                yield f
        finally:
            fobj.close()

    def close(self) -> None:
        if self._zip is not None:
            self._zip.close()
            self._zip = None

    def __enter__(self) -> "CaeDataset":
        return self

    def __exit__(self, *exc) -> None:
        self.close()
