"""Decode a captured image into RGB + alpha bytes and crop it to the part.

Standard library only, no ``adsk`` imports. Three container formats are
understood, because which one Fusion writes with a transparent background is
decided by the spike run (docs/spike-notes.md):

* **BMP** 24/32-bit (BI_RGB or byte-aligned BI_BITFIELDS), bottom-up or
  top-down rows, row padding handled. Channels are split with extended
  slicing, so no per-pixel Python loops.
* **TIFF** 8-bit RGB/RGBA/gray, uncompressed, LZW, Deflate or PackBits,
  horizontal predictor, strips (tiles are not supported).
* **PNG** 8/16-bit gray, RGB, palette, gray+alpha, RGBA, non-interlaced.
  Scanline unfiltering runs at C speed for the None, Sub and Up filters
  (big-integer SWAR arithmetic); Average and Paeth rows need a per-byte
  Python loop and are timed, so the spike can report what Fusion's encoder
  produces.

Cropping finds the rows and columns with any non-zero alpha without visiting
pixels in Python: rows are compared as slices, columns are OR-ed together as
big integers.
"""

from __future__ import annotations

import struct
import time
import zlib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional, Tuple


class RasterError(ValueError):
    """The file could not be decoded (unsupported or corrupt)."""


@dataclass
class RasterImage:
    width: int
    height: int
    rgb: bytes                       # width*height*3 bytes, top row first
    alpha: Optional[bytes]           # width*height bytes or None (no channel)
    source_format: str = ''
    has_real_alpha: bool = False     # True when the file's alpha is usable
    stats: Dict[str, float] = field(default_factory=dict)

    @property
    def size(self) -> Tuple[int, int]:
        return self.width, self.height


# ---------------------------------------------------------------- SWAR helpers
# Rows are handled as little-endian big integers: byte i of the row sits at
# bits 8i..8i+7. Per-byte modular addition is done lane-wise with masks so
# that carries never cross into the next byte.

_mask_cache: Dict[int, Tuple[int, int]] = {}


def _masks(n: int) -> Tuple[int, int]:
    m = _mask_cache.get(n)
    if m is None:
        full = (1 << (8 * n)) - 1
        even = int.from_bytes(b'\xff\x00' * ((n + 1) // 2), 'little') & full
        m = (full, even)
        _mask_cache[n] = m
    return m


def _add8(a: int, b: int, n: int) -> int:
    """Per-byte (a + b) mod 256 over n-byte little-endian integers."""
    _, m = _masks(n)
    lo = ((a & m) + (b & m)) & m
    hi = (((a >> 8) & m) + ((b >> 8) & m)) & m
    return lo | (hi << 8)


def _prefix_sum_mod256(row: bytes, stride: int) -> bytes:
    """out[x] = sum(row[x], row[x-stride], row[x-2*stride], ...) mod 256.
    This undoes the PNG 'Sub' filter and the TIFF horizontal predictor.
    Hillis-Steele scan: log2(len) big-integer operations, no Python loop
    over bytes."""
    n = len(row)
    if n == 0:
        return row
    full, _ = _masks(n)
    a = int.from_bytes(row, 'little')
    shift = 8 * stride
    limit = 8 * n
    while shift < limit:
        a = _add8(a, (a << shift) & full, n)
        shift *= 2
    return a.to_bytes(n, 'little')


def _add_rows(row: bytes, prev: bytes) -> bytes:
    """Per-byte (row + prev) mod 256: undoes the PNG 'Up' filter."""
    n = len(row)
    return _add8(int.from_bytes(row, 'little'), int.from_bytes(prev, 'little'), n).to_bytes(n, 'little')


def _unfilter_average(row: bytes, prev: bytes, bpp: int) -> bytes:
    out = bytearray(row)
    n = len(out)
    for x in range(min(bpp, n)):
        out[x] = (out[x] + (prev[x] >> 1)) & 255
    for x in range(bpp, n):
        out[x] = (out[x] + ((out[x - bpp] + prev[x]) >> 1)) & 255
    return bytes(out)


def _unfilter_paeth(row: bytes, prev: bytes, bpp: int) -> bytes:
    out = bytearray(row)
    n = len(out)
    for x in range(min(bpp, n)):
        out[x] = (out[x] + prev[x]) & 255
    for x in range(bpp, n):
        a = out[x - bpp]
        b = prev[x]
        c = prev[x - bpp]
        pa = b - c if b >= c else c - b
        pb = a - c if a >= c else c - a
        pc = a + b - c - c
        if pc < 0:
            pc = -pc
        if pa <= pb and pa <= pc:
            p = a
        elif pb <= pc:
            p = b
        else:
            p = c
        out[x] = (out[x] + p) & 255
    return bytes(out)


# ---------------------------------------------------------------- channel ops

def _interleave3(r: bytes, g: bytes, b: bytes) -> bytes:
    out = bytearray(len(r) * 3)
    out[0::3] = r
    out[1::3] = g
    out[2::3] = b
    return bytes(out)


def _split_rgba(pixels: bytes, order: Tuple[int, int, int, int], bpp: int) -> Tuple[bytes, Optional[bytes]]:
    """Split interleaved pixels into RGB (+ alpha). ``order`` gives the byte
    index within a pixel of R, G, B and A (A index -1 when absent)."""
    ri, gi, bi, ai = order
    rgb = _interleave3(pixels[ri::bpp], pixels[gi::bpp], pixels[bi::bpp])
    alpha = pixels[ai::bpp] if ai >= 0 else None
    return rgb, alpha


def _alpha_is_real(alpha: Optional[bytes]) -> bool:
    """A usable alpha channel has both transparent and opaque pixels. An
    all-zero channel is an unused padding byte; an all-255 channel means the
    background was not transparent."""
    if not alpha:
        return False
    if alpha.count(0) == len(alpha):
        return False
    if alpha.count(255) == len(alpha):
        return False
    return True


# ---------------------------------------------------------------- BMP

def decode_bmp(data: bytes) -> RasterImage:
    if len(data) < 54 or data[:2] != b'BM':
        raise RasterError('not a BMP file')
    pixel_offset = struct.unpack_from('<I', data, 10)[0]
    dib_size = struct.unpack_from('<I', data, 14)[0]
    if dib_size < 40:
        raise RasterError(f'unsupported BMP header size {dib_size}')
    width, height, planes, bpp, compression = struct.unpack_from('<iiHHI', data, 18)
    if width <= 0 or height == 0:
        raise RasterError('BMP has an empty image')
    top_down = height < 0
    height = abs(height)
    if bpp not in (24, 32):
        raise RasterError(f'unsupported BMP depth {bpp}')
    if compression not in (0, 3):
        raise RasterError(f'unsupported BMP compression {compression}')
    byte_per_px = bpp // 8
    # Byte index of each channel inside a pixel (little-endian B, G, R, A).
    order = (2, 1, 0, 3 if bpp == 32 else -1)
    if compression == 3:
        masks_at = 54 if dib_size == 40 else 54  # masks follow a 40-byte header, or live in V4/V5 headers
        if dib_size >= 52:
            masks_at = 14 + 40
        r_mask, g_mask, b_mask = struct.unpack_from('<III', data, masks_at)
        a_mask = struct.unpack_from('<I', data, masks_at + 12)[0] if (dib_size >= 56 or bpp == 32) else 0

        def index_of(mask: int) -> int:
            for i in range(4):
                if mask == 0xFF << (8 * i):
                    return i
            return -1

        order = (index_of(r_mask), index_of(g_mask), index_of(b_mask), index_of(a_mask) if a_mask else -1)
        if -1 in order[:3]:
            raise RasterError('BMP uses channel masks that are not byte aligned')
    stride = ((bpp * width + 31) // 32) * 4
    row_bytes = width * byte_per_px
    if pixel_offset + stride * height > len(data) + 3:
        raise RasterError('BMP pixel data is truncated')
    if stride == row_bytes and top_down:
        pixels = data[pixel_offset:pixel_offset + row_bytes * height]
    else:
        rows = range(height) if top_down else range(height - 1, -1, -1)
        pixels = b''.join(data[pixel_offset + r * stride: pixel_offset + r * stride + row_bytes] for r in rows)
    rgb, alpha = _split_rgba(pixels, order, byte_per_px)
    real = _alpha_is_real(alpha)
    return RasterImage(width, height, rgb, alpha if real else None, 'bmp', real)


# ---------------------------------------------------------------- TIFF

_TIFF_TYPE_SIZES = {1: 1, 2: 1, 3: 2, 4: 4, 5: 8, 6: 1, 7: 1, 8: 2, 9: 4, 10: 8, 11: 4, 12: 8}


def _lzw_decode(data: bytes, expected: int) -> bytes:
    """TIFF-flavoured LZW (MSB-first codes, early change)."""
    out: List[bytes] = []
    total = 0
    table: List[bytes] = []
    bitbuf = 0
    bitcnt = 0
    pos = 0
    n = len(data)
    nbits = 9
    next_code = 258
    prev: Optional[bytes] = None
    base = [bytes((i,)) for i in range(256)] + [b'', b'']
    while total < expected:
        while bitcnt < nbits:
            if pos >= n:
                return b''.join(out)
            bitbuf = (bitbuf << 8) | data[pos]
            pos += 1
            bitcnt += 8
        bitcnt -= nbits
        code = (bitbuf >> bitcnt) & ((1 << nbits) - 1)
        bitbuf &= (1 << bitcnt) - 1
        if code == 256:                      # clear
            table = list(base)
            nbits = 9
            next_code = 258
            prev = None
            continue
        if code == 257:                      # end of information
            break
        if prev is None:
            if code >= 256:
                raise RasterError('corrupt LZW stream')
            entry = table[code]
        elif code < next_code:
            entry = table[code]
            table.append(prev + entry[:1])
            next_code += 1
        elif code == next_code:
            entry = prev + prev[:1]
            table.append(entry)
            next_code += 1
        else:
            raise RasterError('corrupt LZW stream')
        if next_code >= (1 << nbits) - 1 and nbits < 12:
            nbits += 1
        out.append(entry)
        total += len(entry)
        prev = entry
    return b''.join(out)


def _packbits_decode(data: bytes, expected: int) -> bytes:
    out = bytearray()
    i = 0
    n = len(data)
    while i < n and len(out) < expected:
        h = data[i]
        i += 1
        if h < 128:
            out += data[i:i + h + 1]
            i += h + 1
        elif h > 128:
            out += bytes((data[i],)) * (257 - h)
            i += 1
    return bytes(out)


def decode_tiff(data: bytes) -> RasterImage:
    if len(data) < 8 or data[:2] not in (b'II', b'MM'):
        raise RasterError('not a TIFF file')
    e = '<' if data[:2] == b'II' else '>'
    magic, ifd_offset = struct.unpack_from(e + 'HI', data, 2)
    if magic != 42:
        raise RasterError('unsupported TIFF variant (BigTIFF?)')
    count = struct.unpack_from(e + 'H', data, ifd_offset)[0]
    tags: Dict[int, list] = {}
    for i in range(count):
        tag, typ, n = struct.unpack_from(e + 'HHI', data, ifd_offset + 2 + i * 12)
        size = _TIFF_TYPE_SIZES.get(typ)
        if size is None:
            continue
        total = size * n
        off = ifd_offset + 2 + i * 12 + 8
        if total > 4:
            off = struct.unpack_from(e + 'I', data, off)[0]
        fmt = {1: 'B', 2: 'c', 3: 'H', 4: 'I', 6: 'b', 7: 'B', 8: 'h', 9: 'i', 11: 'f', 12: 'd'}.get(typ)
        if fmt is None:                      # rationals are not needed here
            continue
        tags[tag] = list(struct.unpack_from(e + fmt * n, data, off))

    def tag(num: int, default=None):
        v = tags.get(num)
        return v[0] if v else default

    width = tag(256)
    height = tag(257)
    if not width or not height:
        raise RasterError('TIFF has no image size')
    bps = tags.get(258, [8])
    spp = tag(277, 1)
    if any(b != 8 for b in bps):
        raise RasterError(f'unsupported TIFF bits per sample {bps}')
    compression = tag(259, 1)
    photometric = tag(262, 2)
    planar = tag(284, 1)
    predictor = tag(317, 1)
    if planar != 1:
        raise RasterError('planar TIFF is not supported')
    if 322 in tags or 324 in tags:
        raise RasterError('tiled TIFF is not supported')
    if compression not in (1, 5, 8, 32773, 32946):
        raise RasterError(f'unsupported TIFF compression {compression}')
    offsets = tags.get(273)
    counts = tags.get(279)
    if not offsets or not counts:
        raise RasterError('TIFF has no strips')
    rows_per_strip = tag(278, height)
    stride = width * spp
    raw_parts: List[bytes] = []
    remaining = height
    for off, cnt in zip(offsets, counts):
        rows = min(rows_per_strip, remaining)
        expected = rows * stride
        chunk = data[off:off + cnt]
        if compression == 5:
            chunk = _lzw_decode(chunk, expected)
        elif compression in (8, 32946):            # Deflate (zlib), what Fusion writes
            chunk = zlib.decompress(chunk)
        elif compression == 32773:
            chunk = _packbits_decode(chunk, expected)
        if len(chunk) < expected:
            raise RasterError('TIFF strip is short')
        if predictor == 2:
            chunk = b''.join(_prefix_sum_mod256(chunk[r * stride:(r + 1) * stride], spp) for r in range(rows))
        raw_parts.append(chunk[:expected])
        remaining -= rows
    raw = b''.join(raw_parts)
    if len(raw) < width * height * spp:
        raise RasterError('TIFF pixel data is truncated')
    if spp >= 3:
        rgb, alpha = _split_rgba(raw, (0, 1, 2, 3 if spp >= 4 else -1), spp)
    else:
        gray = raw[0::spp]
        if photometric == 0:                 # WhiteIsZero
            gray = bytes(255 - g for g in gray)
        rgb = _interleave3(gray, gray, gray)
        alpha = raw[1::spp] if spp == 2 else None
    real = _alpha_is_real(alpha)
    return RasterImage(width, height, rgb, alpha if real else None, 'tif', real)


# ---------------------------------------------------------------- PNG

_PNG_SIG = b'\x89PNG\r\n\x1a\n'


def decode_png(data: bytes) -> RasterImage:
    if not data.startswith(_PNG_SIG):
        raise RasterError('not a PNG file')
    pos = 8
    width = height = 0
    depth = ctype = interlace = 0
    palette = b''
    idat: List[bytes] = []
    while pos + 8 <= len(data):
        length, kind = struct.unpack_from('>I4s', data, pos)
        body = data[pos + 8: pos + 8 + length]
        pos += 12 + length
        if kind == b'IHDR':
            width, height, depth, ctype, _, _, interlace = struct.unpack('>IIBBBBB', body)
        elif kind == b'PLTE':
            palette = body
        elif kind == b'IDAT':
            idat.append(body)
        elif kind == b'IEND':
            break
    if not width or not height:
        raise RasterError('PNG has no IHDR')
    if interlace:
        raise RasterError('interlaced PNG is not supported')
    channels = {0: 1, 2: 3, 3: 1, 4: 2, 6: 4}.get(ctype)
    if channels is None or depth not in (8, 16) or (ctype == 3 and depth != 8):
        raise RasterError(f'unsupported PNG colour type {ctype} / depth {depth}')
    bps = depth // 8
    bpp = channels * bps
    stride = width * bpp
    t0 = time.perf_counter()
    raw = zlib.decompress(b''.join(idat))
    t_inflate = time.perf_counter() - t0
    if len(raw) < (stride + 1) * height:
        raise RasterError('PNG data is truncated')
    rows: List[bytes] = []
    prev = bytes(stride)
    counts = [0, 0, 0, 0, 0]
    timings = [0.0, 0.0, 0.0, 0.0, 0.0]
    for y in range(height):
        i = y * (stride + 1)
        ftype = raw[i]
        row = raw[i + 1: i + 1 + stride]
        t = time.perf_counter()
        if ftype == 0:
            out = row
        elif ftype == 1:
            out = _prefix_sum_mod256(row, bpp)
        elif ftype == 2:
            out = _add_rows(row, prev)
        elif ftype == 3:
            out = _unfilter_average(row, prev, bpp)
        elif ftype == 4:
            out = _unfilter_paeth(row, prev, bpp)
        else:
            raise RasterError(f'bad PNG filter type {ftype}')
        timings[ftype] += time.perf_counter() - t
        counts[ftype] += 1
        rows.append(out)
        prev = out
    pixels = b''.join(rows)
    if bps == 2:                                 # 16-bit: keep the high byte
        pixels = pixels[0::2]
        bpp = channels
    if ctype == 6:
        rgb, alpha = _split_rgba(pixels, (0, 1, 2, 3), 4)
    elif ctype == 2:
        rgb, alpha = pixels, None
    elif ctype == 4:
        g = pixels[0::2]
        rgb, alpha = _interleave3(g, g, g), pixels[1::2]
    elif ctype == 3:
        if not palette:
            raise RasterError('palette PNG without PLTE')
        r = pixels.translate(bytes(palette[min(i * 3, len(palette) - 3)] for i in range(256)))
        g = pixels.translate(bytes(palette[min(i * 3 + 1, len(palette) - 2)] for i in range(256)))
        b = pixels.translate(bytes(palette[min(i * 3 + 2, len(palette) - 1)] for i in range(256)))
        rgb, alpha = _interleave3(r, g, b), None
    else:
        rgb, alpha = _interleave3(pixels, pixels, pixels), None
    real = _alpha_is_real(alpha)
    stats = {'inflate_s': round(t_inflate, 4)}
    for ftype, name in enumerate(('none', 'sub', 'up', 'average', 'paeth')):
        stats[f'rows_{name}'] = counts[ftype]
        stats[f'unfilter_{name}_s'] = round(timings[ftype], 4)
    return RasterImage(width, height, rgb, alpha if real else None, 'png', real, stats)


# ---------------------------------------------------------------- public API

def decode(data: bytes) -> RasterImage:
    """Decode by magic number, not by extension."""
    if data[:2] == b'BM':
        return decode_bmp(data)
    if data[:4] in (b'II*\x00', b'MM\x00*'):
        return decode_tiff(data)
    if data[:8] == _PNG_SIG:
        return decode_png(data)
    if data[:2] == b'\xff\xd8':
        raise RasterError('JPEG has no alpha channel and is not supported')
    raise RasterError('unknown image format')


def load_image(path) -> RasterImage:
    t0 = time.perf_counter()
    data = Path(path).read_bytes()
    img = decode(data)
    img.stats['decode_s'] = round(time.perf_counter() - t0, 4)
    img.stats['file_bytes'] = len(data)
    return img


def background_color(img: RasterImage) -> Tuple[int, int, int]:
    """The most common of the four corner colours."""
    w, h = img.width, img.height
    corners = []
    for (x, y) in ((0, 0), (w - 1, 0), (0, h - 1), (w - 1, h - 1)):
        i = (y * w + x) * 3
        corners.append(tuple(img.rgb[i:i + 3]))
    return max(set(corners), key=corners.count)


def alpha_from_background(img: RasterImage, bg: Optional[Tuple[int, int, int]] = None) -> bytes:
    """Chroma-key alpha for an opaque capture: 0 where the pixel equals the
    background colour, 255 elsewhere. Done per channel with translate tables
    and a big-integer OR, so it stays at C speed."""
    bg = bg or background_color(img)
    n = img.width * img.height
    acc = 0
    for ch in range(3):
        table = bytes(0 if v == bg[ch] else 255 for v in range(256))
        acc |= int.from_bytes(img.rgb[ch::3].translate(table), 'big')
    return acc.to_bytes(n, 'big')


def ensure_alpha(img: RasterImage) -> RasterImage:
    """Return an image that has an alpha channel, deriving one from the
    background colour when the file had none."""
    if img.alpha is not None:
        return img
    alpha = alpha_from_background(img)
    return RasterImage(img.width, img.height, img.rgb, alpha, img.source_format, False, dict(img.stats))


def content_bounds(img: RasterImage) -> Optional[Tuple[int, int, int, int]]:
    """(x0, y0, x1, y1) of the pixels with non-zero alpha, exclusive on the
    right/bottom, or None for an empty image."""
    if img.alpha is None:
        img = ensure_alpha(img)
    w, h, a = img.width, img.height, img.alpha
    zero = bytes(w)
    y0 = next((y for y in range(h) if a[y * w:(y + 1) * w] != zero), None)
    if y0 is None:
        return None
    y1 = next(y for y in range(h - 1, -1, -1) if a[y * w:(y + 1) * w] != zero) + 1
    acc = 0
    for y in range(y0, y1):
        acc |= int.from_bytes(a[y * w:(y + 1) * w], 'big')
    cols = acc.to_bytes(w, 'big')
    x0 = len(cols) - len(cols.lstrip(b'\x00'))
    x1 = len(cols.rstrip(b'\x00'))
    return x0, y0, x1, y1


def crop(img: RasterImage, x0: int, y0: int, x1: int, y1: int) -> RasterImage:
    w = img.width
    cw = x1 - x0
    rgb = b''.join(img.rgb[(y * w + x0) * 3:(y * w + x1) * 3] for y in range(y0, y1))
    alpha = None
    if img.alpha is not None:
        alpha = b''.join(img.alpha[y * w + x0: y * w + x1] for y in range(y0, y1))
    return RasterImage(cw, y1 - y0, rgb, alpha, img.source_format, img.has_real_alpha, dict(img.stats))


def crop_to_content(img: RasterImage, margin: int = 0) -> Tuple[RasterImage, Tuple[int, int, int, int]]:
    """Crop to the non-transparent area plus ``margin`` pixels. Returns the
    cropped image and the tight content box (x0, y0, x1, y1) in the cropped
    image's pixel coordinates, so callers can map geometry exactly even when a
    margin is kept."""
    img = ensure_alpha(img)
    bounds = content_bounds(img)
    if bounds is None:
        return img, (0, 0, img.width, img.height)
    x0, y0, x1, y1 = bounds
    cx0, cy0 = max(0, x0 - margin), max(0, y0 - margin)
    cx1, cy1 = min(img.width, x1 + margin), min(img.height, y1 + margin)
    out = crop(img, cx0, cy0, cx1, cy1)
    return out, (x0 - cx0, y0 - cy0, x1 - cx0, y1 - cy0)


# ---------------------------------------------------------------- statistics

def pixel_stats(img: RasterImage, light_gray: int, dark_gray: int, tolerance: int = 12) -> Dict[str, float]:
    """Fractions of opaque pixels near ``light_gray`` / ``dark_gray`` and the
    mean gray of the opaque pixels, without per-pixel Python loops (translate
    tables and big-integer ANDs). Used by the spike to tell whether the
    appearance overrides made it into a capture."""
    n = img.width * img.height
    if n == 0:
        return {'opaque_fraction': 0.0, 'mean_gray_opaque': 0.0, 'dark_fraction_of_opaque': 0.0,
                'light_fraction_of_opaque': 0.0, 'alpha_distinct_values': 0}
    a = img.alpha if img.alpha is not None else bytes([255]) * n
    g = img.rgb[1::3]                                   # R = G = B for neutral grays
    opaque_tbl = bytes(1 if v == 255 else 0 for v in range(256))
    full_tbl = bytes(255 if v == 255 else 0 for v in range(256))
    dark_tbl = bytes(1 if abs(v - dark_gray) <= tolerance else 0 for v in range(256))
    light_tbl = bytes(1 if abs(v - light_gray) <= tolerance else 0 for v in range(256))
    opaque_mask = a.translate(opaque_tbl)
    opaque = opaque_mask.count(1)
    if opaque == 0:
        return {'opaque_fraction': 0.0, 'mean_gray_opaque': 0.0, 'dark_fraction_of_opaque': 0.0,
                'light_fraction_of_opaque': 0.0, 'alpha_distinct_values': len(set(a))}
    om = int.from_bytes(opaque_mask, 'big')
    dark = (int.from_bytes(g.translate(dark_tbl), 'big') & om).to_bytes(n, 'big').count(1)
    light = (int.from_bytes(g.translate(light_tbl), 'big') & om).to_bytes(n, 'big').count(1)
    gray_sum = sum((int.from_bytes(g, 'big') & int.from_bytes(a.translate(full_tbl), 'big')).to_bytes(n, 'big'))
    return {
        'opaque_fraction': opaque / n,
        'mean_gray_opaque': gray_sum / opaque,
        'dark_fraction_of_opaque': dark / opaque,
        'light_fraction_of_opaque': light / opaque,
        'alpha_distinct_values': len(set(a)),
    }


# ---------------------------------------------------------------- tone processing

def to_gray(img: RasterImage) -> RasterImage:
    """Neutral grayscale (R = G = B = luminance) at C speed: three translate
    tables (0.299 R, 0.587 G, 0.114 B, floors so the sum never overflows a
    byte) added lane-wise."""
    n = img.width * img.height
    if n == 0:
        return img
    tr = bytes(int(v * 0.299) for v in range(256))
    tg = bytes(int(v * 0.587) for v in range(256))
    tb = bytes(int(v * 0.114) for v in range(256))
    r = int.from_bytes(img.rgb[0::3].translate(tr), 'little')
    g = int.from_bytes(img.rgb[1::3].translate(tg), 'little')
    b = int.from_bytes(img.rgb[2::3].translate(tb), 'little')
    gray = _add8(_add8(r, g, n), b, n).to_bytes(n, 'little')
    return RasterImage(img.width, img.height, _interleave3(gray, gray, gray), img.alpha, img.source_format,
                       img.has_real_alpha, dict(img.stats))


def gray_histogram(img: RasterImage, opaque_only: bool = True) -> List[int]:
    """Histogram of the green channel (== gray after to_gray) over fully opaque pixels."""
    g = img.rgb[1::3]
    if opaque_only and img.alpha is not None:
        n = len(g)
        keep = img.alpha.translate(bytes(255 if v == 255 else 0 for v in range(256)))
        g = (int.from_bytes(g, 'big') & int.from_bytes(keep, 'big')).to_bytes(n, 'big')
        hist = [g.count(v) for v in range(256)]
        hist[0] -= keep.count(0)
        return hist
    return [g.count(v) for v in range(256)]


def normalize_tones(img: RasterImage, light_target: int, dark_target: int,
                    expect_dark: bool = False, min_fraction: float = 0.01) -> Tuple[RasterImage, Dict[str, int]]:
    """Remap gray levels so the dominant light tone (the body) prints exactly
    as ``light_target`` and, when ``expect_dark`` and a dark tone is present
    (masked faces), that tone prints as ``dark_target``. Black edges stay
    black and white stays white; everything in between is interpolated. The
    renderer's lighting darkens the appearance a little (227 came out as
    about 208 in the spike), so this makes the print tones deterministic."""
    gray = to_gray(img)
    hist = gray_histogram(gray)
    total = sum(hist)
    info: Dict[str, int] = {}
    if total == 0:
        return gray, info
    light_mode = max(range(128, 256), key=lambda v: hist[v])
    anchors = [(0, 0)]
    if hist[light_mode] >= min_fraction * total:
        info['light_mode'] = light_mode
    else:
        light_mode = None
    if expect_dark:
        dark_mode = max(range(16, 128), key=lambda v: hist[v])
        if hist[dark_mode] >= min_fraction * total and (light_mode is None or dark_mode < light_mode):
            anchors.append((dark_mode, dark_target))
            info['dark_mode'] = dark_mode
    if light_mode is not None:
        anchors.append((light_mode, light_target))
    anchors.append((255, 255))
    anchors.sort()
    lut = bytearray(256)
    for i in range(len(anchors) - 1):
        (x0, y0), (x1, y1) = anchors[i], anchors[i + 1]
        for x in range(x0, x1 + 1):
            t = (x - x0) / (x1 - x0) if x1 > x0 else 0.0
            lut[x] = max(0, min(255, int(round(y0 + t * (y1 - y0)))))
    table = bytes(lut)
    mapped = gray.rgb[1::3].translate(table)
    out = RasterImage(gray.width, gray.height, _interleave3(mapped, mapped, mapped), gray.alpha,
                      gray.source_format, gray.has_real_alpha, dict(gray.stats))
    return out, info


def clear_center_marker(img: RasterImage, radius_fraction: float = 0.01, tint: int = 15) -> Tuple[RasterImage, int]:
    """Fusion draws a small green camera-pivot marker at the camera target,
    which is the centre of our captures. Inside a small window around the
    centre, the marker is repaired: strongly green-tinted pixels and their
    immediate neighbours that deviate from the window's dominant tone are
    made transparent when they lie over the background, or repainted with the
    dominant opaque colour of the window when they lie over the part. Returns
    the repaired image and the number of pixels touched."""
    if img.alpha is None:
        return img, 0
    w, h = img.width, img.height
    r = max(3, int(round(max(w, h) * radius_fraction)))
    cx, cy = w // 2, h // 2
    src = img.rgb
    alpha_src = img.alpha
    xs = range(max(0, cx - r), min(w, cx + r + 1))
    ys = range(max(0, cy - r), min(h, cy + r + 1))

    def tint_of(j: int) -> int:
        return src[j * 3 + 1] - max(src[j * 3], src[j * 3 + 2])

    def lum(j: int) -> int:
        return (299 * src[j * 3] + 587 * src[j * 3 + 1] + 114 * src[j * 3 + 2]) // 1000

    strong = {y * w + x for y in ys for x in xs if alpha_src[y * w + x] and tint_of(y * w + x) >= tint}
    if not strong:
        return img, 0
    # Dominant opaque, untinted colour in the window: the part's local tone.
    counts: Dict[bytes, int] = {}
    for y in ys:
        for x in xs:
            j = y * w + x
            if alpha_src[j] == 255 and j not in strong:
                c = src[j * 3:j * 3 + 3]
                counts[c] = counts.get(c, 0) + 1
    mode = max(counts, key=counts.get) if counts else None
    mode_lum = (299 * mode[0] + 587 * mode[1] + 114 * mode[2]) // 1000 if mode else None
    marked = set(strong)
    for j in strong:                                   # the marker's blended fringe
        y0, x0 = divmod(j, w)
        for dy in (-2, -1, 0, 1, 2):
            for dx in (-2, -1, 0, 1, 2):
                xx, yy = x0 + dx, y0 + dy
                if not (0 <= xx < w and 0 <= yy < h):
                    continue
                k = yy * w + xx
                if k in marked or alpha_src[k] == 0:
                    continue
                if alpha_src[k] < 255:
                    marked.add(k)
                elif mode_lum is not None and lum(k) >= 60 and abs(lum(k) - mode_lum) > 12:
                    marked.add(k)
    alpha = bytearray(alpha_src)
    rgb = bytearray(src)
    for k in marked:
        if alpha[k] < 255:
            alpha[k] = 0
        elif mode is not None:
            rgb[k * 3:k * 3 + 3] = mode
    return RasterImage(w, h, bytes(rgb), bytes(alpha), img.source_format, img.has_real_alpha, dict(img.stats)), len(marked)
