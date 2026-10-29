"""Lectura seekable por rangos: no descarga el PST completo a /tmp ni a RAM."""
import io
from collections import OrderedDict


class RangeReader(io.RawIOBase):
    def __init__(self, size, fetch, block_size=64 * 1024, cache_blocks=512):
        super().__init__()
        if size < 0 or block_size <= 0 or cache_blocks < 1:
            raise ValueError("Rango invalido")
        self.size, self.fetch = size, fetch
        self.block_size, self.cache_blocks = block_size, cache_blocks
        self.position, self.cache = 0, OrderedDict()
        self.failed = False

    def readable(self):
        return True

    def seekable(self):
        return True

    def tell(self):
        return self.position

    def seek(self, offset, whence=io.SEEK_SET):
        position = offset if whence == io.SEEK_SET else self.position + offset if whence == io.SEEK_CUR else self.size + offset if whence == io.SEEK_END else -1
        if position < 0:
            raise ValueError("Posicion invalida")
        self.position = position
        return position

    def read(self, size=-1):
        if self.closed:
            raise ValueError("Lector cerrado")
        remaining = max(0, self.size - self.position) if size is None or size < 0 else min(size, max(0, self.size-self.position))
        # libpff hace lecturas acotadas. Un read() accidental de todo un PST se rechaza.
        if remaining > 16 * 1024 * 1024:
            raise ValueError("Lectura individual demasiado grande")
        output = bytearray()
        while remaining:
            index, offset = divmod(self.position, self.block_size)
            if index not in self.cache:
                start = index * self.block_size
                expected = min(self.block_size, self.size-start)
                try:
                    value = self.fetch(start, expected)
                except Exception:
                    self.failed = True
                    raise
                if len(value) != expected:
                    self.failed = True
                    raise OSError("RANGO_INCOMPLETO")
                self.cache[index] = value
                if len(self.cache) > self.cache_blocks:
                    self.cache.popitem(last=False)
            self.cache.move_to_end(index)
            value = self.cache[index]
            count = min(remaining, len(value)-offset)
            output.extend(value[offset:offset+count])
            self.position += count
            remaining -= count
        return bytes(output)

    def readinto(self, buffer):
        value = self.read(len(buffer))
        buffer[:len(value)] = value
        return len(value)

    def close(self):
        self.cache.clear()
        super().close()
