"""Uncompressed AVI writer (no extra packages): 24-bit frames, any size, any rate.

    avi = RawAvi('run.avi', 288, 240, fps=59.922743)
    avi.add(rgb_bytes)        # width*height*3 bytes, top row first, R G B
    avi.close()

Files are split before they reach 1.9 GB (run.avi, run_002.avi, ...) so every
part stays a plain AVI 1.0 file that any player or editor opens.
"""
import struct
from fractions import Fraction

LIMIT = 1_900_000_000


class RawAvi:
    def __init__(self, path, width, height, fps=59.922743):
        self.base, self.w, self.h = path, width, height
        r = Fraction(fps).limit_denominator(1_000_000)
        self.rate, self.scale = r.numerator, r.denominator
        self.stride = (width * 3 + 3) & ~3
        self.part = 0
        self.frames_total = 0
        self.f = None
        self._open()

    def _name(self):
        if self.part == 0:
            return self.base
        stem, dot, ext = self.base.rpartition('.')
        return f'{stem}_{self.part + 1:03d}.{ext}' if dot else f'{self.base}_{self.part + 1:03d}'

    def _open(self):
        self.f = open(self._name(), 'wb')
        self.index = []
        self.frames = 0
        size = self.stride * self.h
        usec = round(1_000_000 * self.scale / self.rate)
        avih = struct.pack('<IIIIIIIIIIIIII', usec, size * self.rate // self.scale, 0, 0x10,
                           0, 0, 1, size, self.w, self.h, 0, 0, 0, 0)
        strh = struct.pack('<4s4sIHHIIIIIIIIhhhh', b'vids', b'DIB ', 0, 0, 0, 0,
                           self.scale, self.rate, 0, 0, size, 0xffffffff, 0, 0, 0, self.w, self.h)
        strf = struct.pack('<IiiHHIIiiII', 40, self.w, self.h, 1, 24, 0, size, 0, 0, 0, 0)
        strl = b'strl' + self._chunk(b'strh', strh) + self._chunk(b'strf', strf)
        hdrl = b'hdrl' + self._chunk(b'avih', avih) + self._chunk(b'LIST', strl)
        self.f.write(b'RIFF\0\0\0\0AVI ')
        self.f.write(self._chunk(b'LIST', hdrl))
        self.movi_at = self.f.tell()
        self.f.write(b'LIST\0\0\0\0movi')

    @staticmethod
    def _chunk(tag, data):
        pad = b'\0' if len(data) & 1 else b''
        return tag + struct.pack('<I', len(data)) + data + pad

    def add(self, rgb):
        """One frame: width*height*3 bytes, rows top to bottom, R G B."""
        if self.f.tell() > LIMIT:
            self._finish()
            self.part += 1
            self._open()
        row = self.w * 3
        pad = b'\0' * (self.stride - row)
        out = bytearray()
        for y in range(self.h - 1, -1, -1):          # AVI DIB frames are bottom-up BGR
            line = bytearray(rgb[y * row:(y + 1) * row])
            line[0::3], line[2::3] = line[2::3], line[0::3]
            out += line + pad
        off = self.f.tell() - self.movi_at - 8
        self.f.write(self._chunk(b'00db', bytes(out)))
        self.index.append(off)
        self.frames += 1
        self.frames_total += 1

    def _finish(self):
        f = self.f
        end_movi = f.tell()
        size = self.stride * self.h
        f.write(b'idx1' + struct.pack('<I', 16 * len(self.index)))
        for off in self.index:
            f.write(struct.pack('<4sIII', b'00db', 0x10, off, size))
        end = f.tell()
        f.seek(4); f.write(struct.pack('<I', end - 8))
        f.seek(self.movi_at + 4); f.write(struct.pack('<I', end_movi - self.movi_at - 8))
        f.seek(12 + 12 + 8 + 16)                     # avih: dwTotalFrames
        f.write(struct.pack('<I', self.frames))
        f.seek(12 + 12 + 8 + 56 + 12 + 8 + 32)        # strh: dwLength
        f.write(struct.pack('<I', self.frames))
        f.close()

    def close(self):
        if self.f:
            self._finish()
            self.f = None
