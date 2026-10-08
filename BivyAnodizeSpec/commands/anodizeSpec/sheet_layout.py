"""Lays out the one-page anodize spec sheet. No ``adsk`` imports.

The geometry of the page follows ``reference/anodize_sheet_mockup.py``:
Letter landscape, 0.5 in margins, a header, the bold process callout with the
quantity box, a 262 pt left column (Process / Part / Masking / Notes) and a
right column with the plan and side views, dimension callouts, balloons, a
legend row and a caption. Everything is black on white; the only other tone
is ``INK2`` for labels.

Inputs come in as a ``SheetData`` so this module can be exercised from the
test harness with synthetic images.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import List, Optional, Sequence, Tuple

from . import pdf_writer as pw
from .raster import RasterImage
from .spec import (AnodizeSpec, callout_cells, config, format_area, format_date,
                   format_envelope, material_line)

W, H = config.PAGE_W, config.PAGE_H
M = config.MARGIN
INK, INK2 = config.INK, config.INK2
MASK_TONE = config.MASK_GRAY / 255.0
WHITE = 1.0


# ---------------------------------------------------------------- inputs

@dataclass
class MaskGroup:
    """One line of the masking list and (optionally) one balloon."""
    label: str                      # 'M1'
    description: str                # '4× Ø0.500 bore'
    sub: str = ''                   # optional secondary line (INK2)
    area_in2: float = 0.0
    count: int = 1
    anchor_in: Optional[Tuple[float, float, float]] = None   # canonical (x, y, z), inches
    view: str = 'none'              # 'plan' | 'side' | 'none'


@dataclass
class ViewImage:
    """A cropped capture plus how its tight content box maps to inches."""
    image: RasterImage
    content_box: Tuple[int, int, int, int]       # x0, y0, x1, y1 in image px (tight silhouette)
    extent_in: Tuple[float, float]               # physical size of the content box: (L, W) or (L, T)
    flipped: bool = False                        # plan view seen from -Z (page x = -canonical x)


@dataclass
class SheetData:
    spec: AnodizeSpec
    surface_area_total_in2: float
    masked_area_in2: float
    envelope_in: Tuple[float, float, float]      # L, W, T sorted descending
    mask_groups: List[MaskGroup] = field(default_factory=list)
    doc_name: str = ''
    doc_version: str = 'unsaved'                 # 'v12' or 'unsaved'
    body_name: str = ''
    date_text: str = ''
    plan: Optional[ViewImage] = None
    side: Optional[ViewImage] = None
    iso: Optional[ViewImage] = None              # isometric context view, drawn not to scale


def iso_view_image(image: RasterImage, content_box: Tuple[int, int, int, int], nominal_w_in: float) -> ViewImage:
    """A ViewImage for the isometric capture. It is never drawn to scale, so
    the extent only carries the content box's pixel aspect ratio, on a
    nominal width (the projected envelope width)."""
    x0, y0, x1, y1 = content_box
    cw, ch = max(1, x1 - x0), max(1, y1 - y0)
    nominal_w_in = max(1e-6, float(nominal_w_in))
    return ViewImage(image, content_box, (nominal_w_in, nominal_w_in * ch / cw))


# ---------------------------------------------------------------- helpers

def _arrow(c: pw.PDFWriter, x: float, y: float, ang: float) -> None:
    """Filled arrowhead with its tip at (x, y), pointing along ``ang``.
    5.5 pt long, 3.6 pt wide."""
    length, half = 5.5, 1.8
    ca, sa = math.cos(ang), math.sin(ang)
    c.set_fill_gray(INK)
    c.polygon([(x, y),
               (x - length * ca + half * sa, y - length * sa - half * ca),
               (x - length * ca - half * sa, y - length * sa + half * ca)], stroke=False, fill=True)


def _hdim(c: pw.PDFWriter, x0: float, x1: float, y: float, ext_y0: float, ext_y1: float, label: str) -> None:
    """Horizontal dimension at height ``y`` between x0 and x1. Extension lines
    rise from ext_y0 / ext_y1 (the part's top edge at each end)."""
    c.set_stroke_gray(INK)
    c.set_line_width(config.MIN_STROKE_PT)
    c.line(x0, ext_y0 + 3, x0, y + 4)
    c.line(x1, ext_y1 + 3, x1, y + 4)
    if x1 - x0 >= 16:
        c.line(x0, y, x1, y)
        _arrow(c, x0, y, math.pi)
        _arrow(c, x1, y, 0)
    else:
        c.line(x0 - 12, y, x0, y)
        c.line(x1, y, x1 + 12, y)
        _arrow(c, x0, y, 0)
        _arrow(c, x1, y, math.pi)
    tw = pw.string_width(label, config.FONT_DIM, True) + 8
    c.set_fill_gray(WHITE)
    c.rect((x0 + x1) / 2 - tw / 2, y - 5, tw, 10, stroke=False, fill=True)
    c.text((x0 + x1) / 2, y - 3, label, config.FONT_DIM, bold=True, anchor='c')


def _vdim(c: pw.PDFWriter, x: float, y0: float, y1: float, ext_x0: float, ext_x1: float, label: str) -> float:
    """Vertical dimension at ``x`` between y0 (bottom) and y1 (top). Extension
    lines run from ext_x0 (at y0) / ext_x1 (at y1) to the dimension line.
    Returns the right edge of the label."""
    c.set_stroke_gray(INK)
    c.set_line_width(config.MIN_STROKE_PT)
    c.line(ext_x0 + 3, y0, x + 4, y0)
    c.line(ext_x1 + 3, y1, x + 4, y1)
    if abs(y1 - y0) >= 16:
        c.line(x, y0, x, y1)
        _arrow(c, x, y0, -math.pi / 2)
        _arrow(c, x, y1, math.pi / 2)
    else:  # small span: arrows outside, pointing in
        c.line(x, y0 - 12, x, y0)
        c.line(x, y1, x, y1 + 12)
        _arrow(c, x, y0, math.pi / 2)
        _arrow(c, x, y1, -math.pi / 2)
    w = c.text(x + 7, (y0 + y1) / 2 - 3, label, config.FONT_DIM, bold=True)
    return x + 7 + w


def _balloon(c: pw.PDFWriter, x: float, y: float, label: str, r: float = 7.5) -> None:
    c.set_stroke_gray(INK)
    c.set_fill_gray(WHITE)
    c.set_line_width(0.9)
    c.circle(x, y, r, stroke=True, fill=True)
    c.text(x, y - 2.6, label, config.FONT_BALLOON, bold=True, anchor='c')


def _scale_bar_length(view_width_in: float) -> float:
    """Round length (1, 2, 5, 10 or 20 in) closest to 20 % of the view width."""
    target = max(1e-6, 0.2 * view_width_in)
    return min((1.0, 2.0, 5.0, 10.0, 20.0), key=lambda v: abs(math.log(v) - math.log(target)))


def _fmt_scale_bar(length_in: float) -> str:
    return f'{int(length_in)} in' if float(length_in).is_integer() else f'{length_in:g} in'


class _Placed:
    """Page placement of one view image."""

    def __init__(self, view: ViewImage, left: float, top: float, scale: float):
        self.view = view
        img = view.image
        x0, y0, x1, y1 = view.content_box
        cw, ch = max(1, x1 - x0), max(1, y1 - y0)
        self.ext_w, self.ext_h = view.extent_in
        # points per content pixel, from the known physical extent
        self.pt_per_px_x = (self.ext_w * scale) / cw
        self.pt_per_px_y = (self.ext_h * scale) / ch
        self.scale = scale
        self.content_left = left
        self.content_top = top
        self.content_w = self.ext_w * scale
        self.content_h = self.ext_h * scale
        self.content_right = left + self.content_w
        self.content_bottom = top - self.content_h
        # full image box (content box plus any margin pixels)
        self.img_left = left - x0 * self.pt_per_px_x
        self.img_top = top + y0 * self.pt_per_px_y
        self.img_w = img.width * self.pt_per_px_x
        self.img_h = img.height * self.pt_per_px_y
        self.img_bottom = self.img_top - self.img_h

    def draw(self, c: pw.PDFWriter) -> None:
        img = self.view.image
        c.image(img.rgb, img.alpha, img.width, img.height, self.img_left, self.img_bottom, self.img_w, self.img_h,
                grayscale=True)

    # canonical inches (u along the page x axis, v along page y) -> page point
    def to_page(self, u_in: float, v_in: float) -> Tuple[float, float]:
        if self.view.flipped:
            u_in = -u_in
        return (self.content_left + (u_in + self.ext_w / 2) * self.scale,
                self.content_bottom + (v_in + self.ext_h / 2) * self.scale)

    def alpha_at(self, x: float, y: float) -> int:
        """Alpha of the image pixel under a page point (0 outside the image)."""
        img = self.view.image
        if img.alpha is None:
            return 0
        px = int((x - self.img_left) / self.pt_per_px_x)
        py = int((self.img_top - y) / self.pt_per_px_y)
        if 0 <= px < img.width and 0 <= py < img.height:
            return img.alpha[py * img.width + px]
        return 0

    def edge_profile(self) -> Tuple[float, float, float, float]:
        """Page y of the topmost opaque pixel in the leftmost / rightmost
        content columns, and page x of the rightmost opaque pixel in the top /
        bottom content rows: where the extension lines start."""
        img = self.view.image
        x0, y0, x1, y1 = self.view.content_box
        a, w = img.alpha, img.width
        if not a:
            return self.content_top, self.content_top, self.content_right, self.content_right

        def first_row(col: int) -> int:
            column = a[col::w]
            for yy in range(y0, min(y1, len(column))):
                if column[yy]:
                    return yy
            return y0

        def last_col(row: int) -> int:
            r = a[row * w:(row + 1) * w]
            stripped = r.rstrip(b'\x00')
            return max(x0, len(stripped) - 1) if stripped else x1 - 1

        left_y = self.img_top - first_row(x0) * self.pt_per_px_y
        right_y = self.img_top - first_row(max(x0, x1 - 1)) * self.pt_per_px_y
        top_x = self.img_left + (last_col(y0) + 1) * self.pt_per_px_x
        bottom_x = self.img_left + (last_col(max(y0, y1 - 1)) + 1) * self.pt_per_px_x
        return left_y, right_y, top_x, bottom_x


# ---------------------------------------------------------------- drawing

def draw_sheet(data: SheetData, out_path=None) -> Tuple[bytes, List[str]]:
    """Draw the sheet. Returns (pdf bytes, warnings) and writes the file when
    ``out_path`` is given."""
    spec = data.spec
    warnings: List[str] = []
    title = f'Anodize specification - {spec.part_name or spec.part_number or "part"}'
    c = pw.PDFWriter(W, H, title=title, author=spec.company or config.DEFAULT_COMPANY,
                     subject='Anodize finishing specification')
    date_text = data.date_text or format_date()

    # ------------------------------------------------------------ header
    top = H - M
    right_block_w = 150.0
    name = spec.part_name or spec.part_number or 'Untitled part'
    name_size = config.FONT_PART_NAME
    max_name_w = W - 2 * M - right_block_w
    while name_size > 14 and pw.string_width(name, name_size, True) > max_name_w:
        name_size -= 1
    name = c.truncate(name, name_size, True, max_name_w)
    c.text(M, top - 22, name, name_size, bold=True)

    fx = M
    field_limit = W - M - right_block_w
    for label, value in (('Part number', spec.part_number), ('Revision', spec.revision),
                         ('Customer / project', spec.customer_project), ('Company', spec.company)):
        value = (value or '').strip()
        if not value:
            continue
        room = field_limit - fx
        if room < 60:
            warnings.append(f'Header field "{label}" did not fit and was dropped.')
            break
        value = c.truncate(value, config.FONT_FIELD_VALUE, True, room - 8)
        c.text(fx, top - 42, label, config.FONT_FIELD_LABEL, gray=INK2)
        c.text(fx, top - 42 - config.FONT_FIELD_VALUE - 3, value, config.FONT_FIELD_VALUE, bold=True)
        fx += max(78.0, pw.string_width(value, config.FONT_FIELD_VALUE, True) + 28)

    c.text(W - M, top - 14, config.WORDMARK, config.FONT_WORDMARK, bold=True, anchor='r', spacing=1.2)
    c.text(W - M, top - 28, 'Anodize specification', 9, anchor='r')
    c.text(W - M, top - 40, date_text, 9, gray=INK2, anchor='r')
    c.text(W - M, top - 52, 'Page 1 of 1', 9, gray=INK2, anchor='r')

    # ------------------------------------------------------------ process callout
    cb_top, cb_bot = top - 76, top - 168
    qty_w = config.QUANTITY_BOX_W
    cb_right = W - M - qty_w - 12
    c.set_stroke_gray(INK)
    c.set_line_width(2)
    c.rect(M, cb_bot, cb_right - M, cb_top - cb_bot)
    c.rect(cb_right + 12, cb_bot, qty_w, cb_top - cb_bot)

    standard = (spec.spec_standard or config.DEFAULT_SPEC).strip()
    c.text(M + 14, cb_top - 18, f'ANODIZE PER {standard}'.upper(), config.FONT_CALLOUT_TITLE, bold=True, spacing=0.6)

    cells = callout_cells(spec)
    inner = cb_right - M - 28
    big = config.FONT_CALLOUT_START
    # The brite dip descriptor (last cell) is set bold and black at a larger
    # size, so the seconds value is read as the brite dip time.
    desc_styles = [(config.FONT_CALLOUT_DESC, False, INK2)] * (len(cells) - 1) + \
                  [(config.FONT_CALLOUT_DESC_STRONG, True, INK)]

    def cell_widths():
        return [max(pw.string_width(b, big, True), pw.string_width(s, size, bold))
                for (b, s), (size, bold, _) in zip(cells, desc_styles)]

    while True:
        widths = cell_widths()
        gap = (inner - sum(widths)) / (len(cells) - 1)
        if gap >= config.CALLOUT_GAP_MIN or big <= config.FONT_CALLOUT_FLOOR:
            break
        big -= 0.5
    if gap < 8:
        # Even at the floor the free-text cells are too long: shorten the colour.
        b, s = cells[2]
        max_w = max(20.0, widths[2] - (8 - gap) * (len(cells) - 1))
        cells[2] = (c.truncate(b, big, True, max_w), s)
        warnings.append('Callout text is too long for the frame; the colour was shortened.')
        widths = cell_widths()
        gap = max(4.0, (inner - sum(widths)) / (len(cells) - 1))
    cx = M + 14
    for (b, s), wcell, (size, bold, gray) in zip(cells, widths, desc_styles):
        c.text(cx, cb_bot + 32, b, big, bold=True)
        c.text(cx, cb_bot + 16, s, size, bold=bold, gray=gray)
        cx += wcell + gap
    cx = M + 14
    c.set_stroke_gray(INK)
    c.set_line_width(0.75)
    for wcell in widths[:-1]:
        cx += wcell + gap
        dx = cx - gap / 2
        c.line(dx, cb_bot + 14, dx, cb_bot + 60)

    qx = cb_right + 12 + qty_w / 2
    c.text(qx, cb_top - 18, 'QUANTITY', config.FONT_CALLOUT_TITLE, bold=True, anchor='c', spacing=0.6)
    qty_text = f'{int(spec.quantity):d}' if spec.quantity else '1'
    qsize = config.FONT_QUANTITY
    while qsize > 12 and pw.string_width(qty_text, qsize, True) > qty_w - 10:
        qsize -= 1
    c.text(qx, cb_bot + 32, qty_text, qsize, bold=True, anchor='c')
    c.text(qx, cb_bot + 16, 'pcs', config.FONT_CALLOUT_DESC, gray=INK2, anchor='c')

    # ------------------------------------------------------------ left column
    lx, lw = M, config.LEFT_COL_W
    footer_rule_y = M + 14
    left_bottom_limit = footer_rule_y + 12
    y_start = cb_bot - 30
    masked = data.masked_area_in2 > 1e-9 or bool(data.mask_groups)
    groups = list(data.mask_groups)

    def left_column(cfg: dict, dry: bool) -> float:
        """Draw (or just measure, when ``dry``) the left column with the given
        degradation settings. Returns the bottom edge of the notes box."""
        notes_size, sub_size = cfg['notes_size'], cfg['sub_size']
        shown = groups[:cfg['n_shown']]
        hidden = len(groups) - len(shown)
        show_subs = cfg['show_subs']
        mask_note = spec.masking_note.strip()
        mask_lines = c.wrap_text(mask_note, sub_size, False, lw) if mask_note else []
        if cfg['mask_note_cap'] is not None and len(mask_lines) > cfg['mask_note_cap']:
            mask_lines = mask_lines[:cfg['mask_note_cap']]
            if mask_lines:
                mask_lines[-1] = c.truncate(mask_lines[-1] + '…', sub_size, False, lw)
        notes = spec.vendor_notes.strip()
        notes_lines = c.wrap_text(notes, notes_size, False, lw - 20) if notes else []
        if cfg['notes_cap'] is not None and len(notes_lines) > cfg['notes_cap']:
            notes_lines = notes_lines[:max(1, cfg['notes_cap'])]
            notes_lines[-1] = c.truncate(notes_lines[-1] + '…', notes_size, False, lw - 20)
        leading = notes_size * 1.3

        def text(x, y, t, size, **kw):
            if not dry:
                c.text(x, y, t, size, **kw)

        def section(title: str, y: float) -> float:
            text(lx, y, title, config.FONT_SECTION, bold=True)
            if not dry:
                c.set_stroke_gray(INK)
                c.set_line_width(1)
                c.line(lx, y - 5, lx + lw, y - 5)
            return y - 20

        def row(label: str, value: str, y: float, value2: Optional[str] = None) -> float:
            text(lx, y, label, config.FONT_ROW_LABEL, gray=INK2)
            text(lx + 92, y, c.truncate(value, config.FONT_ROW_VALUE, True, lw - 92),
                 config.FONT_ROW_VALUE, bold=True)
            if value2:
                text(lx + 92, y - 12, c.truncate(value2, config.FONT_ROW_VALUE, True, lw - 92),
                     config.FONT_ROW_VALUE, bold=True)
                return y - 28
            return y - 16

        y = y_start
        y = section('Process', y)
        y = row('Seal', spec.seal or config.DEFAULT_SEAL, y)
        y = row('Print dimensions', spec.dims_condition or config.DEFAULT_DIMS_CONDITION, y)
        y -= 8
        y = section('Part', y)
        y = row('Material', material_line(spec), y)
        y = row('Envelope', format_envelope(data.envelope_in), y)
        anodized = max(0.0, data.surface_area_total_in2 - data.masked_area_in2)
        y = row('Surface area', f'{format_area(data.surface_area_total_in2)} total', y,
                f'{format_area(anodized)} anodized' if masked else None)
        y -= 8
        y = section('Masking', y)
        if shown:
            for g in shown:
                if not dry:
                    _balloon(c, lx + 7.5, y + 3, g.label)
                text(lx + 22, y, c.truncate(g.description, config.FONT_ROW_VALUE, True, lw - 22),
                     config.FONT_ROW_VALUE, bold=True)
                if g.sub and show_subs:
                    text(lx + 22, y - 12, c.truncate(g.sub, sub_size, False, lw - 22), sub_size, gray=INK2)
                    y -= 30
                else:
                    y -= 18
            if hidden:
                text(lx + 22, y, f'+ {hidden} more', sub_size, gray=INK2)
                y -= 14
        else:
            text(lx, y, 'None. Anodize all surfaces.', config.FONT_ROW_VALUE, bold=True)
            y -= 18
        if mask_lines:
            for ln in mask_lines:
                text(lx, y, ln, sub_size)
                y -= sub_size * 1.3
            y -= 2
        y -= 6
        nb_h = 12 + 8 + leading * max(1, len(notes_lines)) + 12
        if not dry:
            c.set_stroke_gray(INK)
            c.set_line_width(1.25)
            c.rect(lx, y - nb_h + 12, lw, nb_h)
            c.text(lx + 10, y - 4, 'Notes for vendor', config.FONT_NOTES_TITLE, bold=True)
            ny = y - 20
            for ln in notes_lines:
                c.text(lx + 10, ny, ln, notes_size)
                ny -= leading
        return y - nb_h + 12

    # Degrade in this order until the column fits above the footer: shrink the
    # notes and sub-lines, drop sub-lines, shorten the masking list (max 8,
    # then fewer), cap the masking note, and finally truncate the notes.
    candidates = []
    base = {'n_shown': min(len(groups), config.MASK_DESC_MAX_LINES), 'show_subs': True,
            'mask_note_cap': None, 'notes_cap': None}
    size_steps = [(config.FONT_NOTES, config.FONT_MASK_SUB), (9.5, 8.5), (9.0, 8.0), (8.5, 8.0),
                  (8.0, 7.5), (config.MIN_FONT_PT, config.MIN_FONT_PT)]
    for ns, ss in size_steps:
        candidates.append(dict(base, notes_size=ns, sub_size=ss))
    smallest = dict(candidates[-1])
    candidates.append(dict(smallest, show_subs=False))
    for n in range(min(len(groups), config.MASK_DESC_MAX_LINES) - 1, 2, -1):
        candidates.append(dict(smallest, show_subs=False, n_shown=n))
    fewest = dict(candidates[-1])
    for cap in (3, 2, 1):
        candidates.append(dict(fewest, mask_note_cap=cap))
    chosen = candidates[0]
    for cfg in candidates:
        chosen = cfg
        if left_column(cfg, dry=True) >= left_bottom_limit:
            break
    if left_column(chosen, dry=True) < left_bottom_limit:
        # Last resort: cut the vendor notes down to what fits (one line minimum).
        for cap in range(12, 0, -1):
            chosen = dict(chosen, notes_cap=cap)
            if left_column(chosen, dry=True) >= left_bottom_limit:
                break
        warnings.append('Left column overflowed: vendor notes were truncated to keep the sheet on one page.')
    if chosen is not candidates[0]:
        if chosen.get('notes_size') != config.FONT_NOTES:
            warnings.append(f'Left column was tight: notes reduced to {chosen["notes_size"]} pt.')
        if not chosen['show_subs']:
            warnings.append('Left column was tight: masking sub-lines were dropped.')
        if chosen['n_shown'] < min(len(groups), config.MASK_DESC_MAX_LINES):
            warnings.append(f'Left column was tight: only {chosen["n_shown"]} masking groups are listed.')
        if chosen['mask_note_cap'] is not None:
            warnings.append('Left column was tight: the masking note was shortened.')
    left_column(chosen, dry=False)

    # ------------------------------------------------------------ views
    vx0, vx1 = config.VIEW_COL_X, W - M
    vy_top = cb_bot - 30
    L_in, W_in, T_in = data.envelope_in
    L_in, W_in, T_in = max(L_in, 1e-6), max(W_in, 1e-6), max(T_in, 1e-6)
    col_w = vx1 - vx0
    caption_clearance = 14
    chain = 34 + 26 + 13 + 30 + 16                       # label, gap, label, legend, caption
    avail = vy_top - chain - (footer_rule_y + caption_clearance)
    full_room = col_w - config.DIM_ROOM_RIGHT

    def ortho_scale(width_room: float) -> float:
        s = width_room / L_in
        if (W_in + T_in) * s > avail:
            s = avail / (W_in + T_in)
        return s

    scale = ortho_scale(full_room)
    # The isometric view goes beside the orthographic views when those are
    # limited by height anyway (the usual case), below them when a long part
    # leaves room there, and beside them (shrinking them) otherwise.
    iso_mode = 'none'
    iso_w = col_w * config.ISO_BOX_FRACTION
    iso_gap = 18.0
    free_below = avail - (W_in + T_in) * scale
    if data.iso is not None:
        beside_scale = ortho_scale(full_room - iso_w - iso_gap)
        iso_w_fit = full_room - 0.9 * scale * L_in - iso_gap      # widest box that costs at most 10 %
        if beside_scale >= 0.9 * scale:
            iso_mode, scale = 'beside', beside_scale
        elif iso_w_fit >= config.ISO_MIN_W_PT:
            iso_mode, iso_w = 'beside', iso_w_fit
            scale = ortho_scale(full_room - iso_w - iso_gap)
        elif free_below >= config.ISO_MIN_BELOW_PT:
            iso_mode = 'below'
        else:
            iso_mode, scale = 'beside', beside_scale
            warnings.append('Orthographic views were drawn smaller to make room for the isometric view.')
    plan_w = L_in * scale
    plan_h, side_h = W_in * scale, T_in * scale

    c.text(vx0, vy_top, 'Plan view', config.FONT_SECTION, bold=True)
    pl_top = vy_top - 34
    plan_bottom = pl_top - plan_h
    side_label_y = plan_bottom - 26
    side_top = side_label_y - 13
    side_bottom = side_top - side_h
    if iso_mode == 'below':
        iso_label_y = side_bottom - 26
        iso_box = (vx0, iso_label_y - 13, full_room, free_below - 39)       # x, top, w, h
        lg_y = iso_box[1] - iso_box[3] - 30
    else:
        lg_y = side_bottom - 30
    caption_y = lg_y - 16
    if iso_mode == 'beside':
        iso_label_y = vy_top
        iso_box = (vx1 - iso_w, pl_top, iso_w, pl_top - (lg_y + 20))

    balloons: List[Tuple[float, float]] = []
    keep_out: List[Tuple[float, float, float, float]] = []   # rectangles balloons must avoid

    placed_plan = placed_side = None
    if data.plan is not None:
        placed_plan = _Placed(data.plan, vx0, pl_top, scale)
        placed_plan.draw(c)
    else:
        c.text(vx0, pl_top - 14, 'Plan view image unavailable', config.FONT_CAPTION, gray=INK2)
        warnings.append('No plan view image.')
    c.text(vx0, side_label_y, 'Side view', config.FONT_SECTION, bold=True)
    if data.side is not None:
        placed_side = _Placed(data.side, vx0, side_top, scale)
        placed_side.draw(c)
    else:
        c.text(vx0, side_top - 10, 'Side view image unavailable', config.FONT_CAPTION, gray=INK2)
        warnings.append('No side view image.')
    if iso_mode != 'none':
        bx_, btop, bw_, bh_ = iso_box
        ew, eh = data.iso.extent_in
        s_iso = min(bw_ / max(ew, 1e-6), bh_ / max(eh, 1e-6))
        cw_ = ew * s_iso
        placed_iso = _Placed(data.iso, bx_ + (bw_ - cw_) / 2, btop, s_iso)
        placed_iso.draw(c)
        c.text(bx_, iso_label_y, 'Isometric view', config.FONT_SECTION, bold=True)
        keep_out.append((bx_ - 4, placed_iso.content_bottom - 4, bx_ + bw_ + 4, iso_label_y + 10))

    left, right = vx0, vx0 + plan_w
    # Dimension callouts (drawn over the images).
    if placed_plan is not None:
        ly, ry, tx, bx = placed_plan.edge_profile()
    else:
        ly = ry = plan_bottom
        tx = bx = right
    dim_y = pl_top + 14
    _hdim(c, left, right, dim_y, min(ly, pl_top), min(ry, pl_top), f'{L_in:.3f}')
    tw = pw.string_width(f'{L_in:.3f}', config.FONT_DIM, True) + 8
    keep_out.append((left - 4, dim_y - 6, right + 4, dim_y + 6))
    keep_out.append(((left + right) / 2 - tw / 2, dim_y - 6, (left + right) / 2 + tw / 2, dim_y + 6))
    wdim_x = right + 14
    label_right = _vdim(c, wdim_x, plan_bottom, pl_top, min(bx, right), min(tx, right), f'{W_in:.3f}')
    keep_out.append((wdim_x - 6, plan_bottom - 4, label_right + 2, pl_top + 4))
    if placed_side is not None:
        sly, sry, stx, sbx = placed_side.edge_profile()
    else:
        stx = sbx = right
    tlabel_right = _vdim(c, wdim_x, side_bottom, side_top, min(sbx, right), min(stx, right), f'{T_in:.3f}')
    keep_out.append((wdim_x - 6, side_bottom - 14, tlabel_right + 2, side_top + 14))
    # Labels and the other column content are off limits too.
    keep_out.append((vx0 - 2, vy_top - 4, vx0 + 60, vy_top + 10))
    keep_out.append((vx0 - 2, side_label_y - 4, vx0 + 60, side_label_y + 10))
    keep_out.append((vx0 - 2, lg_y - 20, vx1, lg_y + 10))

    # Balloons with leaders.
    r = 7.5
    for g in groups:
        if g.view not in ('plan', 'side') or g.anchor_in is None:
            continue
        placed = placed_plan if g.view == 'plan' else placed_side
        if placed is None:
            continue
        ax, ay, az = g.anchor_in
        px, py = placed.to_page(ax, ay) if g.view == 'plan' else placed.to_page(ax, az)
        pos = _place_balloon(placed, px, py, r, balloons, keep_out, (vx0, caption_y + 20, vx1, vy_top + 8))
        if pos is None:
            warnings.append(f'No free spot for balloon {g.label}; drawn without a leader.')
            continue
        bxp, byp = pos
        dx, dy = px - bxp, py - byp
        dist = math.hypot(dx, dy) or 1.0
        sx, sy = bxp + dx / dist * r, byp + dy / dist * r
        c.set_stroke_gray(INK)
        c.set_line_width(0.6)
        c.line(sx, sy, px, py)
        c.set_fill_gray(INK)
        c.circle(px, py, 1.1, stroke=False, fill=True)
        _balloon(c, bxp, byp, g.label, r)
        balloons.append((bxp, byp))

    # Legend row: masked square (only when masked) and the scale bar.
    if masked:
        c.set_fill_gray(MASK_TONE)
        c.rect(vx0, lg_y - 1, 9, 9, stroke=False, fill=True)
        c.text(vx0 + 15, lg_y, 'Masked surface', config.FONT_CAPTION)
    bar_in = _scale_bar_length(L_in)
    bar = bar_in * scale
    bxr = right
    c.set_stroke_gray(INK)
    c.set_line_width(0.8)
    c.line(bxr - bar, lg_y + 3, bxr, lg_y + 3)
    for xx in (bxr - bar, bxr - bar / 2, bxr):
        c.line(xx, lg_y, xx, lg_y + 6)
    c.text(bxr - bar - 6, lg_y, _fmt_scale_bar(bar_in), config.FONT_CAPTION, bold=True, anchor='r')
    caption = 'Orthographic views, both at the same scale. Not to print scale.'
    if iso_mode != 'none':
        caption = 'Orthographic views at the same scale; isometric view not to scale. Not to print scale.'
    c.text(vx0, caption_y, caption, config.FONT_CAPTION, gray=INK2)

    # ------------------------------------------------------------ footer
    c.set_stroke_gray(INK)
    c.set_line_width(config.MIN_STROKE_PT)
    c.line(M, footer_rule_y, W - M, footer_rule_y)
    doc = data.doc_name or 'unsaved design'
    ver = data.doc_version or 'unsaved'
    body = data.body_name or 'body'
    footer = f'Generated {date_text} from {doc} ({ver}), {body} \u00b7 Anodize Spec Sheet v{config.ADDIN_VERSION}'
    c.text(M, M + 2, c.truncate(footer, config.FONT_FOOTER, False, W - 2 * M), config.FONT_FOOTER, gray=INK2)

    warnings.extend(c.warnings)
    pdf = c.to_bytes()
    if out_path is not None:
        with open(out_path, 'wb') as fh:
            fh.write(pdf)
    return pdf, warnings


def _place_balloon(placed: _Placed, ax: float, ay: float, r: float, balloons: Sequence[Tuple[float, float]],
                   keep_out: Sequence[Tuple[float, float, float, float]],
                   bounds: Tuple[float, float, float, float]) -> Optional[Tuple[float, float]]:
    """Fixed-offset search for a balloon centre near the anchor that lies
    outside the part silhouette, clear of dimension callouts, labels, other
    balloons and the column bounds. Candidates are tried nearest first,
    preferring up and to the right so leaders read naturally."""
    bx0, by0, bx1, by1 = bounds
    angles = [60, 120, 30, 150, 90, 0, 180, -30, -150, -60, -120, -90]
    radii = [26, 36, 48, 62, 78, 96, 118, 142]
    for rad in radii:
        for ang in angles:
            a = math.radians(ang)
            x, y = ax + rad * math.cos(a), ay + rad * math.sin(a)
            if not (bx0 + r <= x <= bx1 - r and by0 + r <= y <= by1 - r):
                continue
            if any(math.hypot(x - ox, y - oy) < 2 * r + 4 for ox, oy in balloons):
                continue
            if any(x + r > kx0 and x - r < kx1 and y + r > ky0 and y - r < ky1 for kx0, ky0, kx1, ky1 in keep_out):
                continue
            # Outside the part: sample the centre and four points on the rim.
            if any(placed.alpha_at(x + dx, y + dy) for dx, dy in ((0, 0), (r, 0), (-r, 0), (0, r), (0, -r))):
                continue
            return x, y
    return None
