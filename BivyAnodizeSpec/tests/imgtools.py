"""Dev-only image helpers for the tests: PNG / BMP / TIFF writers and a
synthetic 'render' generator. Standard library only. Never imported by the
add-in."""

from __future__ import annotations

import math
import struct
import zlib
from typing import List, Optional, Sequence, Tuple


# ---------------------------------------------------------------- PNG

def _png_chunk(kind: bytes, body: bytes) -> bytes:
    return struct.pack('>I', len(body)) + kind + body + struct.pack('>I', zlib.crc32(kind + body) & 0xFFFFFFFF)


def _filter_row(ftype: int, row: bytes, prev: bytes, bpp: int) -> bytes:
    n = len(row)
    if ftype == 0:
        return row
    out = bytearray(n)
    for x in range(n):
        a = row[x - bpp] if x >= bpp else 0
        b = prev[x]
        c = prev[x - bpp] if x >= bpp else 0
        if ftype == 1:
            p = a
        elif ftype == 2:
            p = b
        elif ftype == 3:
            p = (a + b) >> 1
        else:
            q = a + b - c
            pa, pb, pc = abs(q - a), abs(q - b), abs(q - c)
            p = a if (pa <= pb and pa <= pc) else (b if pb <= pc else c)
        out[x] = (row[x] - p) & 255
    return bytes(out)


def write_png(path, width: int, height: int, rgb: bytes, alpha: Optional[bytes] = None,
              filter_type='adaptive') -> None:
    """filter_type: 0..4 to force one filter for every row, or 'adaptive'
    (cycles through all five filters so decoders see each one)."""
    channels = 4 if alpha is not None else 3
    ctype = 6 if alpha is not None else 2
    stride = width * channels
    if alpha is not None:
        px = bytearray(width * height * 4)
        px[0::4] = rgb[0::3]
        px[1::4] = rgb[1::3]
        px[2::4] = rgb[2::3]
        px[3::4] = alpha
        pixels = bytes(px)
    else:
        pixels = rgb
    out = bytearray()
    prev = bytes(stride)
    for y in range(height):
        row = pixels[y * stride:(y + 1) * stride]
        ft = (y % 5) if filter_type == 'adaptive' else int(filter_type)
        out.append(ft)
        out += _filter_row(ft, row, prev, channels)
        prev = row
    data = (b'\x89PNG\r\n\x1a\n' + _png_chunk(b'IHDR', struct.pack('>IIBBBBB', width, height, 8, ctype, 0, 0, 0))
            + _png_chunk(b'IDAT', zlib.compress(bytes(out), 6)) + _png_chunk(b'IEND', b''))
    with open(path, 'wb') as fh:
        fh.write(data)


# ---------------------------------------------------------------- BMP

def write_bmp(path, width: int, height: int, rgb: bytes, alpha: Optional[bytes] = None,
              top_down: bool = False, bitfields: bool = False) -> None:
    bpp = 32 if alpha is not None else 24
    bytes_px = bpp // 8
    stride = ((bpp * width + 31) // 32) * 4
    rows = []
    for y in range(height):
        row = bytearray(stride)
        r = rgb[y * width * 3:(y + 1) * width * 3]
        row[0:width * bytes_px:bytes_px] = r[2::3]
        row[1:width * bytes_px:bytes_px] = r[1::3]
        row[2:width * bytes_px:bytes_px] = r[0::3]
        if alpha is not None:
            row[3:width * bytes_px:bytes_px] = alpha[y * width:(y + 1) * width]
        rows.append(bytes(row))
    if not top_down:
        rows.reverse()
    pixel_data = b''.join(rows)
    if bitfields:
        dib = struct.pack('<IiiHHIIiiII', 108, width, -height if top_down else height, 1, bpp, 3,
                          len(pixel_data), 2835, 2835, 0, 0)
        dib += struct.pack('<IIII', 0x00FF0000, 0x0000FF00, 0x000000FF, 0xFF000000 if alpha is not None else 0)
        dib += b'sRGB' + bytes(108 - len(dib) - 4)
    else:
        dib = struct.pack('<IiiHHIIiiII', 40, width, -height if top_down else height, 1, bpp, 0,
                          len(pixel_data), 2835, 2835, 0, 0)
    offset = 14 + len(dib)
    header = b'BM' + struct.pack('<IHHI', offset + len(pixel_data), 0, 0, offset)
    with open(path, 'wb') as fh:
        fh.write(header + dib + pixel_data)


# ---------------------------------------------------------------- TIFF

def _lzw_encode(data: bytes) -> bytes:
    """TIFF LZW encoder (MSB-first, early change)."""
    out_bits: List[Tuple[int, int]] = []
    table = {bytes((i,)): i for i in range(256)}
    next_code = 258
    nbits = 9
    out_bits.append((256, nbits))
    w = b''
    for ch in data:
        c = bytes((ch,))
        wc = w + c
        if wc in table:
            w = wc
            continue
        out_bits.append((table[w], nbits))
        table[wc] = next_code
        next_code += 1
        # Same transitions as libtiff's encoder: widen when the next free
        # entry passes the current code range; clear when the table is full.
        if next_code == 4094:
            out_bits.append((256, nbits))
            table = {bytes((i,)): i for i in range(256)}
            next_code = 258
            nbits = 9
        elif next_code > (1 << nbits) - 1:
            nbits += 1
        w = c
    if w:
        out_bits.append((table[w], nbits))
    out_bits.append((257, nbits))
    buf = 0
    cnt = 0
    out = bytearray()
    for code, bits in out_bits:
        buf = (buf << bits) | code
        cnt += bits
        while cnt >= 8:
            out.append((buf >> (cnt - 8)) & 255)
            cnt -= 8
            buf &= (1 << cnt) - 1
    if cnt:
        out.append((buf << (8 - cnt)) & 255)
    return bytes(out)


def _packbits_encode(data: bytes) -> bytes:
    out = bytearray()
    i = 0
    n = len(data)
    while i < n:
        run = 1
        while i + run < n and run < 128 and data[i + run] == data[i]:
            run += 1
        if run >= 2:
            out += bytes((257 - run, data[i]))
            i += run
            continue
        j = i
        while j < n and j - i < 128 and not (j + 1 < n and data[j] == data[j + 1]):
            j += 1
        out += bytes((j - i - 1,)) + data[i:j]
        i = j
    return bytes(out)


def write_tiff(path, width: int, height: int, rgb: bytes, alpha: Optional[bytes] = None,
               compression: str = 'none', predictor: int = 1, rows_per_strip: Optional[int] = None,
               big_endian: bool = False) -> None:
    spp = 4 if alpha is not None else 3
    if alpha is not None:
        px = bytearray(width * height * 4)
        px[0::4] = rgb[0::3]
        px[1::4] = rgb[1::3]
        px[2::4] = rgb[2::3]
        px[3::4] = alpha
        pixels = bytes(px)
    else:
        pixels = rgb
    stride = width * spp
    rows_per_strip = rows_per_strip or height
    strips = []
    for y0 in range(0, height, rows_per_strip):
        chunk = pixels[y0 * stride:min(height, y0 + rows_per_strip) * stride]
        if predictor == 2:
            rows = []
            for r in range(len(chunk) // stride):
                row = bytearray(chunk[r * stride:(r + 1) * stride])
                for x in range(stride - 1, spp - 1, -1):
                    row[x] = (row[x] - row[x - spp]) & 255
                rows.append(bytes(row))
            chunk = b''.join(rows)
        if compression == 'lzw':
            chunk = _lzw_encode(chunk)
        elif compression == 'packbits':
            chunk = _packbits_encode(chunk)
        strips.append(chunk)
    e = '>' if big_endian else '<'
    comp_code = {'none': 1, 'lzw': 5, 'packbits': 32773}[compression]
    n_strips = len(strips)
    entries = []  # (tag, type, count, values)
    entries.append((256, 4, 1, [width]))
    entries.append((257, 4, 1, [height]))
    entries.append((258, 3, spp, [8] * spp))
    entries.append((259, 3, 1, [comp_code]))
    entries.append((262, 3, 1, [2]))
    entries.append((273, 4, n_strips, None))       # strip offsets, filled later
    entries.append((277, 3, 1, [spp]))
    entries.append((278, 4, 1, [rows_per_strip]))
    entries.append((279, 4, n_strips, [len(s) for s in strips]))
    entries.append((284, 3, 1, [1]))
    if predictor == 2:
        entries.append((317, 3, 1, [2]))
    if alpha is not None:
        entries.append((338, 3, 1, [2]))
    entries.sort(key=lambda t: t[0])
    header = struct.pack(e + '2sHI', b'MM' if big_endian else b'II', 42, 8)
    ifd_size = 2 + 12 * len(entries) + 4
    extra_pos = 8 + ifd_size
    extra = bytearray()
    # Lay out strips after the extra data.
    sizes = {3: 2, 4: 4}

    def pack_values(typ, vals):
        fmt = 'H' if typ == 3 else 'I'
        return struct.pack(e + fmt * len(vals), *vals)

    # First pass to compute where strips go: extra data for arrays > 4 bytes.
    extra_len = 0
    for tag, typ, cnt, vals in entries:
        if sizes[typ] * cnt > 4:
            extra_len += sizes[typ] * cnt
    strip_start = extra_pos + extra_len
    offsets = []
    p = strip_start
    for s in strips:
        offsets.append(p)
        p += len(s)
    ifd = bytearray(struct.pack(e + 'H', len(entries)))
    for tag, typ, cnt, vals in entries:
        if tag == 273:
            vals = offsets
        packed = pack_values(typ, vals)
        if len(packed) > 4:
            ifd += struct.pack(e + 'HHII', tag, typ, cnt, extra_pos + len(extra))
            extra += packed
        else:
            ifd += struct.pack(e + 'HHI', tag, typ, cnt) + packed.ljust(4, b'\x00')
    ifd += struct.pack(e + 'I', 0)
    with open(path, 'wb') as fh:
        fh.write(header + bytes(ifd) + bytes(extra) + b''.join(strips))


# ---------------------------------------------------------------- synthetic render

def synth_plate(width_px: int, L_in: float, W_in: float, part_gray: int = 227, mask_gray: int = 64,
                edge_px: float = 2.0, holes: Sequence[Tuple[float, float, float, bool]] = (),
                supersample: int = 2) -> Tuple[int, int, bytes, bytes]:
    """A stand-in plan-view render: an elliptical ring plate with two tabs and
    round holes, light gray with black edges, on a transparent background.
    holes: (x_in, y_in, radius_in, masked). Returns (w, h, rgb, alpha)."""
    scale = width_px / L_in
    height_px = max(1, int(round(W_in * scale)))
    ss = supersample
    a_o, b_o = L_in / 2, W_in / 2
    a_i, b_i = a_o * 0.76, b_o * 0.67
    tab_hw, tab_tip = L_in * 0.076, b_i * 0.74
    rgb = bytearray(width_px * height_px * 3)
    alpha = bytearray(width_px * height_px)
    e_in = edge_px / scale

    def inside(x, y):
        ro = (x / a_o) ** 2 + (y / b_o) ** 2
        ri = (x / a_i) ** 2 + (y / b_i) ** 2
        in_ring = ro <= 1.0 and ri >= 1.0
        in_tab = abs(x) <= tab_hw and tab_tip <= abs(y) <= b_i + 0.01
        return in_ring or in_tab

    def tone(x, y):
        if not inside(x, y):
            return None
        for hx, hy, hr, masked in holes:
            d = math.hypot(x - hx, y - hy)
            if d <= hr:
                return None if not masked else (mask_gray if d <= hr - e_in else 0)
            if d <= hr + e_in:
                return 0
        ro = (x / a_o) ** 2 + (y / b_o) ** 2
        ri = (x / a_i) ** 2 + (y / b_i) ** 2
        near_edge = (ro >= (1 - e_in / a_o * 2)) or (ri <= (1 + e_in / a_i * 2) and abs(x) > tab_hw)
        if abs(x) <= tab_hw and abs(y) >= tab_tip and abs(y) <= tab_tip + e_in:
            near_edge = True
        if abs(x) >= tab_hw - e_in and abs(x) <= tab_hw and abs(y) >= tab_tip:
            near_edge = True
        return 0 if near_edge else part_gray

    n2 = ss * ss
    for py in range(height_px):
        for px in range(width_px):
            cov = 0
            acc = 0
            for sy in range(ss):
                for sx in range(ss):
                    x = (px + (sx + 0.5) / ss) / scale - a_o
                    y = b_o - (py + (sy + 0.5) / ss) / scale
                    t = tone(x, y)
                    if t is not None:
                        cov += 1
                        acc += t
            i = py * width_px + px
            if cov:
                g = acc // cov
                rgb[i * 3:i * 3 + 3] = bytes((g, g, g))
                alpha[i] = (255 * cov) // n2
    return width_px, height_px, bytes(rgb), bytes(alpha)


def synth_side(width_px: int, L_in: float, T_in: float, part_gray: int = 227, edge_px: float = 2.0,
               scale: Optional[float] = None) -> Tuple[int, int, bytes, bytes]:
    """A stand-in side view: a thin slab with black edges."""
    scale = scale or width_px / L_in
    height_px = max(3, int(round(T_in * scale)))
    rgb = bytearray(width_px * height_px * 3)
    alpha = bytearray(width_px * height_px)
    e = max(1, int(round(edge_px)))
    for y in range(height_px):
        for x in range(width_px):
            edge = x < e or x >= width_px - e or y < e or y >= height_px - e
            g = 0 if edge else part_gray
            i = y * width_px + x
            rgb[i * 3:i * 3 + 3] = bytes((g, g, g))
            alpha[i] = 255
    return width_px, height_px, bytes(rgb), bytes(alpha)


def _unit3(v):
    n = math.sqrt(v[0] * v[0] + v[1] * v[1] + v[2] * v[2]) or 1.0
    return (v[0] / n, v[1] / n, v[2] / n)


def _cross3(a, b):
    return (a[1] * b[2] - a[2] * b[1], a[2] * b[0] - a[0] * b[2], a[0] * b[1] - a[1] * b[0])


def synth_iso(width_px: int, L_in: float, W_in: float, T_in: float, part_gray: int = 227, edge_px: float = 2.0,
              from_plus_z: bool = True) -> Tuple[int, int, bytes, bytes]:
    """A stand-in isometric render: the L x W x T box seen from the (+X, -Y,
    +Z) octant with Z up (the same camera as render.iso_camera), three shaded
    faces with black edges on a transparent background."""
    pz = 1.0 if from_plus_z else -1.0
    d = _unit3((1.0, -1.0, pz))
    f = (-d[0], -d[1], -d[2])
    r = _unit3(_cross3(f, (0.0, 0.0, pz)))
    u = _cross3(r, f)

    def proj(p):
        return (p[0] * r[0] + p[1] * r[1] + p[2] * r[2], p[0] * u[0] + p[1] * u[1] + p[2] * u[2])

    hx, hy, hz = L_in / 2, W_in / 2, T_in / 2
    # The three faces facing the eye: +X, -Y and +Z (or -Z when flipped), each
    # as a quad of 3D corners, with its tone.
    faces = [
        ([(hx, -hy, -hz), (hx, hy, -hz), (hx, hy, hz), (hx, -hy, hz)], int(part_gray * 0.76)),
        ([(-hx, -hy, -hz), (hx, -hy, -hz), (hx, -hy, hz), (-hx, -hy, hz)], int(part_gray * 0.88)),
        ([(-hx, -hy, pz * hz), (hx, -hy, pz * hz), (hx, hy, pz * hz), (-hx, hy, pz * hz)], part_gray),
    ]
    quads = [([proj(p) for p in q], tone) for q, tone in faces]
    xs = [pt[0] for q, _ in quads for pt in q]
    ys = [pt[1] for q, _ in quads for pt in q]
    x_min, x_max, y_min, y_max = min(xs), max(xs), min(ys), max(ys)
    scale = width_px / (x_max - x_min)
    height_px = max(3, int(round((y_max - y_min) * scale)))
    edges = []
    for q, _ in quads:
        for i in range(4):
            edges.append((q[i], q[(i + 1) % 4]))
    e_in = edge_px / scale

    def inside(q, x, y):
        sign = 0
        for i in range(4):
            (ax, ay), (bx, by) = q[i], q[(i + 1) % 4]
            cr = (bx - ax) * (y - ay) - (by - ay) * (x - ax)
            if abs(cr) < 1e-12:
                continue
            s_ = 1 if cr > 0 else -1
            if sign == 0:
                sign = s_
            elif s_ != sign:
                return False
        return True

    def near_edge(x, y):
        for (ax, ay), (bx, by) in edges:
            vx, vy = bx - ax, by - ay
            ll = vx * vx + vy * vy
            tt = 0.0 if ll == 0 else max(0.0, min(1.0, ((x - ax) * vx + (y - ay) * vy) / ll))
            dx, dy = x - (ax + tt * vx), y - (ay + tt * vy)
            if dx * dx + dy * dy <= e_in * e_in:
                return True
        return False

    rgb = bytearray(width_px * height_px * 3)
    alpha = bytearray(width_px * height_px)
    for py in range(height_px):
        y = y_max - (py + 0.5) / scale
        for px in range(width_px):
            x = x_min + (px + 0.5) / scale
            tone = None
            for q, t in quads:
                if inside(q, x, y):
                    tone = t
                    break
            if tone is None:
                continue
            g = 0 if near_edge(x, y) else tone
            i = py * width_px + px
            rgb[i * 3:i * 3 + 3] = bytes((g, g, g))
            alpha[i] = 255
    return width_px, height_px, bytes(rgb), bytes(alpha)
