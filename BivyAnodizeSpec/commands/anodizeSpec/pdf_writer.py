"""Minimal PDF 1.4 writer. Standard library only, no ``adsk`` imports.

Just enough PDF for the anodize sheet:

* one or more Letter-landscape pages (792 x 612 pt),
* the base-14 fonts Helvetica and Helvetica-Bold with WinAnsiEncoding, so
  nothing is embedded; text is encoded with the ``cp1252`` codec, which covers
  the special characters the sheet uses (², ×, —, Ø, °, ±, ·). Characters that
  will not encode become "?" and are reported in ``PDFWriter.warnings``,
* text with size, gray level and character spacing (the ``Tc`` operator),
* lines, rectangles, filled polygons, Bézier paths, circles and line caps,
* 8-bit RGB images with an alpha channel (as an SMask),
* a Flate-compressed content stream and a correct xref table.

Glyph widths for the two fonts come from the Adobe Core 14 AFM metrics,
indexed by WinAnsi code, so strings can be measured, wrapped and aligned.
"""

from __future__ import annotations

import datetime as _dt
import zlib
from typing import Iterable, List, Optional, Sequence, Tuple

PAGE_W, PAGE_H = 792.0, 612.0
_KAPPA = 0.5522847498307936  # Bézier circle constant

FONT_REGULAR = 'Helvetica'
FONT_BOLD = 'Helvetica-Bold'

# Glyph widths (1/1000 em) for WinAnsiEncoding codes 0-255, from the Adobe
# Core 14 AFM files for Helvetica and Helvetica-Bold.
_W_HELVETICA = (
       0,    0,    0,    0,    0,    0,    0,    0,    0,    0,    0,    0,    0,    0,    0,    0,  # 0x00
       0,    0,    0,    0,    0,    0,    0,    0,    0,    0,    0,    0,    0,    0,    0,    0,  # 0x10
     278,  278,  355,  556,  556,  889,  667,  191,  333,  333,  389,  584,  278,  333,  278,  278,  # 0x20
     556,  556,  556,  556,  556,  556,  556,  556,  556,  556,  278,  278,  584,  584,  584,  556,  # 0x30
    1015,  667,  667,  722,  722,  667,  611,  778,  722,  278,  500,  667,  556,  833,  722,  778,  # 0x40
     667,  778,  722,  667,  611,  722,  667,  944,  667,  667,  611,  278,  278,  278,  469,  556,  # 0x50
     333,  556,  556,  500,  556,  556,  278,  556,  556,  222,  222,  500,  222,  833,  556,  556,  # 0x60
     556,  556,  333,  500,  278,  556,  500,  722,  500,  500,  500,  334,  260,  334,  584,  350,  # 0x70
     556,  350,  222,  556,  333, 1000,  556,  556,  333, 1000,  667,  333, 1000,  350,  611,  350,  # 0x80
     350,  222,  222,  333,  333,  350,  556, 1000,  333, 1000,  500,  333,  944,  350,  500,  667,  # 0x90
     278,  333,  556,  556,  556,  556,  260,  556,  333,  737,  370,  556,  584,  333,  737,  333,  # 0xA0
     400,  584,  333,  333,  333,  556,  537,  278,  333,  333,  365,  556,  834,  834,  834,  611,  # 0xB0
     667,  667,  667,  667,  667,  667, 1000,  722,  667,  667,  667,  667,  278,  278,  278,  278,  # 0xC0
     722,  722,  778,  778,  778,  778,  778,  584,  778,  722,  722,  722,  722,  667,  667,  611,  # 0xD0
     556,  556,  556,  556,  556,  556,  889,  500,  556,  556,  556,  556,  278,  278,  278,  278,  # 0xE0
     556,  556,  556,  556,  556,  556,  556,  584,  611,  556,  556,  556,  556,  500,  556,  500,  # 0xF0
)

_W_HELVETICA_BOLD = (
       0,    0,    0,    0,    0,    0,    0,    0,    0,    0,    0,    0,    0,    0,    0,    0,  # 0x00
       0,    0,    0,    0,    0,    0,    0,    0,    0,    0,    0,    0,    0,    0,    0,    0,  # 0x10
     278,  333,  474,  556,  556,  889,  722,  238,  333,  333,  389,  584,  278,  333,  278,  278,  # 0x20
     556,  556,  556,  556,  556,  556,  556,  556,  556,  556,  333,  333,  584,  584,  584,  611,  # 0x30
     975,  722,  722,  722,  722,  667,  611,  778,  722,  278,  556,  722,  611,  833,  722,  778,  # 0x40
     667,  778,  722,  667,  611,  722,  667,  944,  667,  667,  611,  333,  278,  333,  584,  556,  # 0x50
     333,  556,  611,  556,  611,  556,  333,  611,  611,  278,  278,  556,  278,  889,  611,  611,  # 0x60
     611,  611,  389,  556,  333,  611,  556,  778,  556,  556,  500,  389,  280,  389,  584,  350,  # 0x70
     556,  350,  278,  556,  500, 1000,  556,  556,  333, 1000,  667,  333, 1000,  350,  611,  350,  # 0x80
     350,  278,  278,  500,  500,  350,  556, 1000,  333, 1000,  556,  333,  944,  350,  500,  667,  # 0x90
     278,  333,  556,  556,  556,  556,  280,  556,  333,  737,  370,  556,  584,  333,  737,  333,  # 0xA0
     400,  584,  333,  333,  333,  611,  556,  278,  333,  333,  365,  556,  834,  834,  834,  611,  # 0xB0
     722,  722,  722,  722,  722,  722, 1000,  722,  667,  667,  667,  667,  278,  278,  278,  278,  # 0xC0
     722,  722,  778,  778,  778,  778,  778,  584,  778,  722,  722,  722,  722,  667,  667,  611,  # 0xD0
     556,  556,  556,  556,  556,  556,  889,  556,  556,  556,  556,  556,  278,  278,  278,  278,  # 0xE0
     611,  611,  611,  611,  611,  611,  611,  584,  611,  611,  611,  611,  611,  556,  611,  556,  # 0xF0
)

# Characters outside cp1252 that have an acceptable stand-in.
_UNICODE_FALLBACKS = {
    '\u2212': '-',   # minus sign
    '\u2010': '-',   # hyphen
    '\u2011': '-',   # non-breaking hyphen
    '\u00a0': ' ',   # no-break space
    '\u2009': ' ',   # thin space
    '\u202f': ' ',   # narrow no-break space
    '\u3000': ' ',   # ideographic space
    '\u2032': "'",   # prime
    '\u2033': '"',   # double prime
    '\u2044': '/',   # fraction slash
    '\u2264': '<=',
    '\u2265': '>=',
    '\u2248': '~',
    '\u03bc': '\u00b5',  # Greek mu -> micro sign
    '\t': '    ',
    '\r': '',
    '\n': ' ',
}


def encode_text(text: str) -> Tuple[bytes, List[str]]:
    """Encode ``text`` for the WinAnsi fonts. Returns the bytes and the list of
    characters that had to be replaced with '?'."""
    s = ''.join(_UNICODE_FALLBACKS.get(ch, ch) for ch in str(text))
    try:
        return s.encode('cp1252'), []
    except UnicodeEncodeError:
        out = bytearray()
        bad: List[str] = []
        for ch in s:
            try:
                out += ch.encode('cp1252')
            except UnicodeEncodeError:
                out += b'?'
                bad.append(ch)
        return bytes(out), bad


def string_width(text: str, size: float, bold: bool = False, spacing: float = 0.0) -> float:
    """Width in points of ``text`` set at ``size``. ``spacing`` is extra space
    between glyphs (the ``Tc`` operator); as in the mockup it is counted
    between glyphs only, not after the last one."""
    data, _ = encode_text(text)
    table = _W_HELVETICA_BOLD if bold else _W_HELVETICA
    w = sum(table[b] for b in data) * size / 1000.0
    if spacing and len(data) > 1:
        w += spacing * (len(data) - 1)
    return w


def _f(n: float) -> str:
    """Format a number for the content stream (max 3 decimals, no trailing zeros)."""
    if abs(n - round(n)) < 5e-4:
        v = int(round(n))
        return '0' if v == 0 else str(v)
    s = f'{n:.3f}'.rstrip('0').rstrip('.')
    return '0' if s in ('-0', '') else s


def _escape(data: bytes) -> bytes:
    return (data.replace(b'\\', b'\\\\').replace(b'(', b'\\(').replace(b')', b'\\)')
            .replace(b'\r', b'\\r').replace(b'\n', b'\\n'))


def _text_string(text: str) -> bytes:
    """A PDF text string for metadata: UTF-16BE with BOM, so any character works."""
    return b'(' + _escape(b'\xfe\xff' + str(text).encode('utf-16-be')) + b')'


class _Path:
    """Path builder returned by ``PDFWriter.path()``."""

    def __init__(self, writer: 'PDFWriter'):
        self._w = writer
        self._ops: List[str] = []

    def move_to(self, x: float, y: float) -> '_Path':
        self._ops.append(f'{_f(x)} {_f(y)} m')
        return self

    def line_to(self, x: float, y: float) -> '_Path':
        self._ops.append(f'{_f(x)} {_f(y)} l')
        return self

    def curve_to(self, x1, y1, x2, y2, x3, y3) -> '_Path':
        self._ops.append(f'{_f(x1)} {_f(y1)} {_f(x2)} {_f(y2)} {_f(x3)} {_f(y3)} c')
        return self

    def close(self) -> '_Path':
        self._ops.append('h')
        return self

    def stroke(self) -> None:
        self._end('S')

    def fill(self) -> None:
        self._end('f')

    def fill_stroke(self) -> None:
        self._end('B')

    def _end(self, op: str) -> None:
        if self._ops:
            self._w._emit(' '.join(self._ops) + ' ' + op)
        self._ops = []


class PDFWriter:
    """Builds a single-page (or multi-page) PDF in memory.

    Coordinates are PDF points with the origin at the bottom-left corner.
    Colors are gray levels 0.0 (black) .. 1.0 (white): the sheet is black and
    white by design.
    """

    def __init__(self, width: float = PAGE_W, height: float = PAGE_H, title: str = '',
                 author: str = '', subject: str = '', creator: str = 'Bivy Works Anodize Spec Sheet',
                 compress: bool = True):
        self.width = float(width)
        self.height = float(height)
        self.title, self.author, self.subject, self.creator = title, author, subject, creator
        self.compress = compress
        self.warnings: List[str] = []
        self._unencodable: set = set()
        self._pages: List[List[bytes]] = [[]]
        self._page_images: List[List[str]] = [[]]
        self._images: List[tuple] = []      # (name, w, h, rgb_z, alpha_z)
        self._state: dict = {}
        self._stack: List[dict] = []

    # ------------------------------------------------------------ pages
    def new_page(self) -> None:
        self._pages.append([])
        self._page_images.append([])
        self._state = {}
        self._stack = []

    @property
    def page_count(self) -> int:
        return len(self._pages)

    # ------------------------------------------------------------ low level
    def _emit(self, op: str) -> None:
        self._pages[-1].append(op.encode('latin-1'))

    def _emit_bytes(self, data: bytes) -> None:
        self._pages[-1].append(data)

    def _set(self, key: str, value, op: str) -> None:
        if self._state.get(key) != value:
            self._state[key] = value
            self._emit(op)

    # ------------------------------------------------------------ graphics state
    def set_fill_gray(self, gray: float) -> None:
        self._set('fill', round(gray, 4), f'{_f(gray)} g')

    def set_stroke_gray(self, gray: float) -> None:
        self._set('stroke', round(gray, 4), f'{_f(gray)} G')

    def set_line_width(self, width: float) -> None:
        self._set('lw', round(width, 4), f'{_f(width)} w')

    def set_line_cap(self, cap: int) -> None:
        """0 = butt, 1 = round, 2 = projecting square."""
        self._set('cap', int(cap), f'{int(cap)} J')

    def set_line_join(self, join: int) -> None:
        """0 = miter, 1 = round, 2 = bevel."""
        self._set('join', int(join), f'{int(join)} j')

    def save_state(self) -> None:
        self._stack.append(dict(self._state))
        self._emit('q')

    def restore_state(self) -> None:
        self._emit('Q')
        self._state = self._stack.pop() if self._stack else {}

    # ------------------------------------------------------------ text
    def _note_unencodable(self, bad: Iterable[str], text: str) -> None:
        fresh = [ch for ch in bad if ch not in self._unencodable]
        if fresh:
            self._unencodable.update(fresh)
            shown = ''.join(fresh[:10])
            self.warnings.append(f'Characters not in the Helvetica WinAnsi set were printed as "?": '
                                 f'{shown!r} in {text[:40]!r}')

    def text(self, x: float, y: float, text: str, size: float, bold: bool = False,
             gray: float = 0.0, anchor: str = 'l', spacing: float = 0.0) -> float:
        """Draw one line of text with its baseline at ``y``. ``anchor`` is
        'l', 'c' or 'r'. Returns the measured width."""
        data, bad = encode_text(text)
        if bad:
            self._note_unencodable(bad, text)
        if not data:
            return 0.0
        width = string_width(text, size, bold, spacing)
        if anchor == 'r':
            x -= width
        elif anchor == 'c':
            x -= width / 2.0
        self.set_fill_gray(gray)
        font = '/F2' if bold else '/F1'
        self._emit_bytes(
            f'BT {font} {_f(size)} Tf {_f(spacing)} Tc 1 0 0 1 {_f(x)} {_f(y)} Tm ('.encode('latin-1')
            + _escape(data) + b') Tj ET')
        return width

    def string_width(self, text: str, size: float, bold: bool = False, spacing: float = 0.0) -> float:
        return string_width(text, size, bold, spacing)

    def wrap_text(self, text: str, size: float, bold: bool, max_width: float,
                  spacing: float = 0.0) -> List[str]:
        """Greedy word wrap. Explicit newlines start new lines; words longer
        than ``max_width`` are broken by character."""
        lines: List[str] = []
        normalized = str(text).replace('\r\n', '\n').replace('\r', '\n')
        for para in normalized.split('\n'):
            cur = ''
            for word in para.split():
                trial = f'{cur} {word}' if cur else word
                if string_width(trial, size, bold, spacing) <= max_width:
                    cur = trial
                    continue
                if cur:
                    lines.append(cur)
                while len(word) > 1 and string_width(word, size, bold, spacing) > max_width:
                    n = len(word)
                    while n > 1 and string_width(word[:n], size, bold, spacing) > max_width:
                        n -= 1
                    lines.append(word[:n])
                    word = word[n:]
                cur = word
            lines.append(cur)
        return lines

    def truncate(self, text: str, size: float, bold: bool, max_width: float,
                 ellipsis: str = '…') -> str:
        if string_width(text, size, bold) <= max_width:
            return text
        n = len(text)
        while n > 0 and string_width(text[:n].rstrip() + ellipsis, size, bold) > max_width:
            n -= 1
        return text[:n].rstrip() + ellipsis

    # ------------------------------------------------------------ shapes
    def line(self, x0: float, y0: float, x1: float, y1: float) -> None:
        self._emit(f'{_f(x0)} {_f(y0)} m {_f(x1)} {_f(y1)} l S')

    def polyline(self, points: Sequence[Tuple[float, float]], close: bool = False) -> None:
        if len(points) < 2:
            return
        ops = [f'{_f(points[0][0])} {_f(points[0][1])} m']
        ops += [f'{_f(x)} {_f(y)} l' for x, y in points[1:]]
        if close:
            ops.append('h')
        self._emit(' '.join(ops) + ' S')

    @staticmethod
    def _paint_op(stroke: bool, fill: bool) -> str:
        if stroke and fill:
            return 'B'
        if fill:
            return 'f'
        if stroke:
            return 'S'
        return 'n'

    def rect(self, x: float, y: float, w: float, h: float, stroke: bool = True, fill: bool = False) -> None:
        self._emit(f'{_f(x)} {_f(y)} {_f(w)} {_f(h)} re {self._paint_op(stroke, fill)}')

    def polygon(self, points: Sequence[Tuple[float, float]], stroke: bool = False, fill: bool = True) -> None:
        if len(points) < 3:
            return
        ops = [f'{_f(points[0][0])} {_f(points[0][1])} m']
        ops += [f'{_f(x)} {_f(y)} l' for x, y in points[1:]]
        ops.append('h')
        self._emit(' '.join(ops) + ' ' + self._paint_op(stroke, fill))

    def circle(self, cx: float, cy: float, r: float, stroke: bool = True, fill: bool = False) -> None:
        k = _KAPPA * r
        p = self.path()
        p.move_to(cx + r, cy)
        p.curve_to(cx + r, cy + k, cx + k, cy + r, cx, cy + r)
        p.curve_to(cx - k, cy + r, cx - r, cy + k, cx - r, cy)
        p.curve_to(cx - r, cy - k, cx - k, cy - r, cx, cy - r)
        p.curve_to(cx + k, cy - r, cx + r, cy - k, cx + r, cy)
        p.close()
        p._end(self._paint_op(stroke, fill))

    def path(self) -> _Path:
        return _Path(self)

    # ------------------------------------------------------------ images
    def image(self, rgb: bytes, alpha: Optional[bytes], px_w: int, px_h: int,
              x: float, y: float, width: float, height: float, grayscale: bool = False) -> str:
        """Place an 8-bit RGB image (row-major, top row first) with an optional
        8-bit alpha channel so that it fills the box (x, y, width, height).
        With ``grayscale`` the green channel is stored as a DeviceGray image
        (a third of the size; the sheet's renders are neutral gray anyway)."""
        if len(rgb) != px_w * px_h * 3:
            raise ValueError(f'rgb has {len(rgb)} bytes, expected {px_w * px_h * 3}')
        if alpha is not None and len(alpha) != px_w * px_h:
            raise ValueError(f'alpha has {len(alpha)} bytes, expected {px_w * px_h}')
        name = f'Im{len(self._images) + 1}'
        data = rgb[1::3] if grayscale else rgb
        rgb_z = zlib.compress(data, 6)
        alpha_z = zlib.compress(alpha, 6) if alpha is not None else None
        self._images.append((name, px_w, px_h, rgb_z, alpha_z, grayscale))
        self._page_images[-1].append(name)
        self._emit(f'q {_f(width)} 0 0 {_f(height)} {_f(x)} {_f(y)} cm /{name} Do Q')
        return name

    # ------------------------------------------------------------ output
    def to_bytes(self) -> bytes:
        objects: List[bytes] = []

        def add(obj: bytes) -> int:
            objects.append(obj)
            return len(objects)

        def stream(dict_entries: str, data: bytes) -> bytes:
            return (f'<< {dict_entries} /Length {len(data)} >>\nstream\n'.encode('latin-1')
                    + data + b'\nendstream')

        # Fixed object numbers so the page tree can reference them up front.
        n_catalog, n_pages = 1, 2
        n_font_regular = 3
        n_font_bold = 4
        n_info = 5
        objects.extend([b''] * 5)
        objects[n_catalog - 1] = f'<< /Type /Catalog /Pages {n_pages} 0 R >>'.encode('latin-1')
        objects[n_font_regular - 1] = (
            f'<< /Type /Font /Subtype /Type1 /BaseFont /{FONT_REGULAR} /Encoding /WinAnsiEncoding >>'.encode('latin-1'))
        objects[n_font_bold - 1] = (
            f'<< /Type /Font /Subtype /Type1 /BaseFont /{FONT_BOLD} /Encoding /WinAnsiEncoding >>'.encode('latin-1'))

        now = _dt.datetime.now()
        info = [f'/Producer {_text_string(self.creator).decode("latin-1")}',
                f'/Creator {_text_string(self.creator).decode("latin-1")}',
                f'/CreationDate (D:{now:%Y%m%d%H%M%S})']
        if self.title:
            info.append('/Title ' + _text_string(self.title).decode('latin-1'))
        if self.author:
            info.append('/Author ' + _text_string(self.author).decode('latin-1'))
        if self.subject:
            info.append('/Subject ' + _text_string(self.subject).decode('latin-1'))
        objects[n_info - 1] = ('<< ' + ' '.join(info) + ' >>').encode('latin-1')

        # Images (each needs an object number before the pages reference them).
        image_numbers = {}
        for name, w, h, rgb_z, alpha_z, grayscale in self._images:
            smask = ''
            if alpha_z is not None:
                n_mask = add(stream(
                    f'/Type /XObject /Subtype /Image /Width {w} /Height {h} /ColorSpace /DeviceGray '
                    f'/BitsPerComponent 8 /Interpolate true /Filter /FlateDecode', alpha_z))
                smask = f' /SMask {n_mask} 0 R'
            space = 'DeviceGray' if grayscale else 'DeviceRGB'
            n_img = add(stream(
                f'/Type /XObject /Subtype /Image /Width {w} /Height {h} /ColorSpace /{space} '
                f'/BitsPerComponent 8 /Interpolate true{smask} /Filter /FlateDecode', rgb_z))
            image_numbers[name] = n_img

        page_numbers: List[int] = []
        for ops, names in zip(self._pages, self._page_images):
            content = b'\n'.join(ops) + b'\n'
            if self.compress:
                n_content = add(stream('/Filter /FlateDecode', zlib.compress(content, 6)))
            else:
                n_content = add(stream('', content))
            xobjects = ''
            if names:
                xobjects = ' /XObject << ' + ' '.join(f'/{n} {image_numbers[n]} 0 R' for n in names) + ' >>'
            page = (f'<< /Type /Page /Parent {n_pages} 0 R /MediaBox [0 0 {_f(self.width)} {_f(self.height)}] '
                    f'/Resources << /Font << /F1 {n_font_regular} 0 R /F2 {n_font_bold} 0 R >>{xobjects} '
                    f'/ProcSet [/PDF /Text /ImageC /ImageB] >> /Contents {n_content} 0 R >>')
            page_numbers.append(add(page.encode('latin-1')))

        kids = ' '.join(f'{n} 0 R' for n in page_numbers)
        objects[n_pages - 1] = f'<< /Type /Pages /Kids [{kids}] /Count {len(page_numbers)} >>'.encode('latin-1')

        out = bytearray(b'%PDF-1.4\n%\xe2\xe3\xcf\xd3\n')
        offsets: List[int] = []
        for i, obj in enumerate(objects, start=1):
            offsets.append(len(out))
            out += f'{i} 0 obj\n'.encode('latin-1') + obj + b'\nendobj\n'
        xref_pos = len(out)
        out += f'xref\n0 {len(objects) + 1}\n'.encode('latin-1')
        out += b'0000000000 65535 f \n'
        for off in offsets:
            out += f'{off:010d} 00000 n \n'.encode('latin-1')
        out += (f'trailer\n<< /Size {len(objects) + 1} /Root {n_catalog} 0 R /Info {n_info} 0 R >>\n'
                f'startxref\n{xref_pos}\n%%EOF\n').encode('latin-1')
        return bytes(out)

    def save(self, path) -> None:
        with open(path, 'wb') as fh:
            fh.write(self.to_bytes())
