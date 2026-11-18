"""在 zip 压缩包内直接随机读取 HDF5（不解压到磁盘）。

h5py 需要一个可 seek 的类文件对象；``zipfile.ZipFile.open`` 返回的流支持 seek，
但反向 seek 需要重解开压流，且 HDF5 常做「跨块大读」。这里实现一个
**块级缓存 + 保证填满缓冲区** 的适配器：

* 顺序读：沿着解压流一路向前，不重复解压；
* 随机读：命中块缓存则零成本，未命中则从最近的流位置向前推；
* ``readinto`` 一定填满调用方要求的字节数（HDF5 依赖这一点，短读会被当成 EOF
  并触发 "filter returned failure during read"）。
"""

from __future__ import annotations

import io
import zipfile

DEFAULT_BLOCK = 8 << 20  # 8 MiB


class ZipMemberFile(io.RawIOBase):
    """把 zip 中的单个条目暴露成可随机访问的只读文件对象。"""

    def __init__(self, zf: zipfile.ZipFile, name: str, block: int = DEFAULT_BLOCK,
                 cache_blocks: int = 16):
        self.name = name
        self.size = zf.getinfo(name).file_size
        self._zf = zf
        self._block = block
        self._cache: dict[int, bytes] = {}
        self._order: list[int] = []
        self._cache_blocks = cache_blocks
        self._pos = 0
        self._stream = None
        self._stream_base = 0

    # --- io 接口 -------------------------------------------------------
    def readable(self) -> bool:
        return True

    def seekable(self) -> bool:
        return True

    def tell(self) -> int:
        return self._pos

    def seek(self, offset: int, whence: int = 0) -> int:
        if whence == 0:
            self._pos = offset
        elif whence == 1:
            self._pos += offset
        elif whence == 2:
            self._pos = self.size + offset
        else:  # pragma: no cover
            raise ValueError(f"invalid whence: {whence}")
        self._pos = max(0, self._pos)
        return self._pos

    def readinto(self, b) -> int:
        want = min(len(b), self.size - self._pos) if self._pos < self.size else 0
        if want <= 0:
            return 0
        view = memoryview(b)
        filled = 0
        while filled < want:
            index = self._pos // self._block
            off = self._pos % self._block
            data = self._load(index)
            if not data:
                break
            take = min(len(data) - off, want - filled)
            if take <= 0:
                break
            view[filled:filled + take] = data[off:off + take]
            filled += take
            self._pos += take
        return filled

    def close(self) -> None:
        if self._stream is not None:
            try:
                self._stream.close()
            finally:
                self._stream = None
        self._cache.clear()
        self._order.clear()
        super().close()

    # --- 内部 ---------------------------------------------------------
    def _load(self, index: int) -> bytes:
        if index in self._cache:
            return self._cache[index]
        start = index * self._block
        if self._stream is None or start < self._stream_base:
            if self._stream is not None:
                self._stream.close()
            self._stream = self._zf.open(self.name)
            self._stream_base = 0
        if start > self._stream_base:
            self._stream.seek(start)
            self._stream_base = start
        data = self._stream.read(self._block)
        self._stream_base = start + len(data)
        self._cache[index] = data
        self._order.append(index)
        while len(self._order) > self._cache_blocks:
            self._cache.pop(self._order.pop(0), None)
        return data


def open_zip_member(zf: zipfile.ZipFile, name: str, **kwargs) -> ZipMemberFile:
    return ZipMemberFile(zf, name, **kwargs)
