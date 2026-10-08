"""Design reference for the Bivy Works anodize spec sheet (B&W, Letter landscape).

REFERENCE ONLY. Uses ReportLab for convenience; the Fusion add-in must NOT import it.
Reproduce this layout with the add-in's stdlib pdf_writer.py.
Uses only what that writer supports: Helvetica / Helvetica-Bold, gray fills,
lines, rects, bezier paths, circles. The part drawing is stand-in geometry;
the real views are the Fusion renders (light gray body, black edges, dark masked faces)."""
import math
from reportlab.pdfgen import canvas
from reportlab.lib.pagesizes import letter, landscape

W, H = landscape(letter)          # 792 x 612
M = 36                            # 0.5 in margins
INK = (0, 0, 0)
INK2 = (0.30, 0.30, 0.30)        # secondary text (70% black)
RULE = (0, 0, 0)
PART = (0.89, 0.89, 0.89)        # part body tone
MASK = (0.25, 0.25, 0.25)        # masked faces

c = canvas.Canvas("anodize_sheet_mockup.pdf", pagesize=(W, H))
c.setTitle("Anodize specification - Camera Plate cheese 2")

def g(rgb): c.setFillColorRGB(*rgb)
def s(rgb): c.setStrokeColorRGB(*rgb)

def text(x, y, t, size, bold=False, color=INK, anchor="l", spacing=0):
    g(color)
    font = "Helvetica-Bold" if bold else "Helvetica"
    c.setFont(font, size)
    w = c.stringWidth(t, font, size) + spacing * max(len(t) - 1, 0)
    if anchor == "r": x -= w
    elif anchor == "c": x -= w / 2
    to = c.beginText(x, y); to.setFont(font, size); to.setCharSpace(spacing); to.textLine(t)
    c.drawText(to)
    return w

def field(x, y, label, value, vsize=10.5, lsize=7.5):
    text(x, y, label, lsize, color=INK2)
    text(x, y - vsize - 3, value, vsize, bold=True)

# ---------------------------------------------------------------- header
top = H - M
text(M, top - 22, "Camera Plate cheese 2", 22, bold=True)
fx = M
for label, value in [("Part number", "BW-1042"), ("Revision", "A"),
                     ("Customer / project", "Test"), ("Company", "BIVY WORKS")]:
    field(fx, top - 42, label, value)
    fx += max(78, c.stringWidth(value, "Helvetica-Bold", 10.5) + 28)

text(W - M, top - 14, "BIVY WORKS", 12, bold=True, anchor="r", spacing=1.2)
text(W - M, top - 28, "Anodize specification", 9, anchor="r")
text(W - M, top - 40, "Oct 6, 2026", 9, color=INK2, anchor="r")
text(W - M, top - 52, "Page 1 of 1", 9, color=INK2, anchor="r")

# ---------------------------------------------------------------- process callout
cb_top, cb_bot = top - 76, top - 168
qty_w = 92
cb_right = W - M - qty_w - 12
s(INK); c.setLineWidth(2)
c.rect(M, cb_bot, cb_right - M, cb_top - cb_bot, stroke=1, fill=0)
c.rect(cb_right + 12, cb_bot, qty_w, cb_top - cb_bot, stroke=1, fill=0)

text(M + 14, cb_top - 18, "ANODIZE PER MIL-PRF-8625", 10, bold=True, spacing=0.6)
cells = [("TYPE III", "Hardcoat"), ("CLASS 2", "Dyed"), ("BLACK", "Color"),
         ("0.0010–0.0015", "Coating thickness, in"), ("NONE", "Brite dip: do not dip")]
inner = cb_right - M - 28
big = 30.0
while True:
    widths = [max(c.stringWidth(b, "Helvetica-Bold", big), c.stringWidth(sm, "Helvetica", 9)) for b, sm in cells]
    gap = (inner - sum(widths)) / (len(cells) - 1)
    if gap >= 24 or big <= 16: break
    big -= 0.5
cx = M + 14
for (b, sm), wcell in zip(cells, widths):
    text(cx, cb_bot + 32, b, big, bold=True)
    text(cx, cb_bot + 16, sm, 9, color=INK2)
    cx += wcell + gap
cx = M + 14
s(INK); c.setLineWidth(0.75)
for wcell in widths[:-1]:
    cx += wcell + gap
    dx = cx - gap / 2
    c.line(dx, cb_bot + 14, dx, cb_bot + 60)
print("callout size", big, "gap", round(gap, 1))

qx = cb_right + 12 + qty_w / 2
text(qx, cb_top - 18, "QUANTITY", 10, bold=True, anchor="c", spacing=0.6)
text(qx, cb_bot + 32, "1", 30, bold=True, anchor="c")
text(qx, cb_bot + 16, "pcs", 9, color=INK2, anchor="c")

# ---------------------------------------------------------------- left column
lx, lw = M, 262
y = cb_bot - 30

def section(title, y):
    text(lx, y, title, 11, bold=True)
    s(INK); c.setLineWidth(1); c.line(lx, y - 5, lx + lw, y - 5)
    return y - 20

def row(label, value, y, value2=None):
    text(lx, y, label, 8.5, color=INK2)
    text(lx + 92, y, value, 10, bold=True)
    if value2:
        text(lx + 92, y - 12, value2, 10, bold=True)
        return y - 28
    return y - 16

y = section("Process", y)
y = row("Seal", "Vendor standard", y)
y = row("Print dimensions", "Before anodize", y)
y -= 8
y = section("Part", y)
y = row("Material", "6061-T6 aluminum", y)
y = row("Envelope", "29.000 × 20.980 × 0.370 in", y)
y = row("Surface area", "542.30 sq in total", y, "541.51 sq in anodized")
y -= 8
y = section("Masking", y)

def balloon(x, y, label, r=7.5):
    s(INK); g((1, 1, 1)); c.setLineWidth(0.9)
    c.circle(x, y, r, stroke=1, fill=1)
    text(x, y - 2.6, label, 7.5, bold=True, anchor="c")

balloon(lx + 7.5, y + 3, "M1")
text(lx + 22, y, "4× Ø0.500 bore, through", 10, bold=True)
text(lx + 22, y - 12, "Tab mounting holes. Plug both sides.", 8.5, color=INK2)
y -= 30
y -= 6

# handling notes box
nb_h = 58
s(INK); c.setLineWidth(1.25)
c.rect(lx, y - nb_h + 12, lw, nb_h, stroke=1, fill=0)
text(lx + 10, y - 4, "Notes for vendor", 10, bold=True)
note = ["Parts are cosmetic. Please avoid scratches and",
        "dents, and hide racking marks."]
ny = y - 20
for ln in note:
    text(lx + 10, ny, ln, 10)
    ny -= 13

# ---------------------------------------------------------------- views (stand-in geometry)
vx0, vx1 = 330, W - M
vy_top = cb_bot - 30
L_in, W_in, T_in = 29.0, 20.98, 0.37
dim_room_r = 44
plan_w = (vx1 - vx0) - dim_room_r
scale = plan_w / L_in
plan_h = W_in * scale
# fit vertically
avail = vy_top - (M + 40) - 24 - 18 - 30 - 26 - 30
if plan_h > avail:
    scale = avail / W_in
    plan_w, plan_h = L_in * scale, W_in * scale

text(vx0, vy_top, "Plan view", 11, bold=True)
pl_top = vy_top - 34
pcx = vx0 + plan_w / 2
pcy = pl_top - plan_h / 2

def P(x, y):  # part inches -> page
    return pcx + x * scale, pcy + y * scale

def ellipse_path(a, b, n=180):
    p = c.beginPath()
    for i in range(n + 1):
        t = 2 * math.pi * i / n
        X, Y = P(a * math.cos(t), b * math.sin(t))
        (p.moveTo if i == 0 else p.lineTo)(X, Y)
    p.close()
    return p

A_o, B_o = L_in / 2, W_in / 2
A_i, B_i = 11.0, 7.0
# ring body
p = ellipse_path(A_o, B_o)
for i in range(181):
    t = -2 * math.pi * i / 180
    X, Y = P(A_i * math.cos(t), B_i * math.sin(t))
    (p.moveTo if i == 0 else p.lineTo)(X, Y)
p.close()
g(PART); s(INK); c.setLineWidth(0.8)
c.drawPath(p, stroke=1, fill=1, fillMode=1)

# tabs (top and bottom), with masked holes
def tab(sign):
    x0, x1 = -2.2, 2.2
    y_in = sign * B_i * math.sqrt(1 - (x1 / A_i) ** 2)
    y_tip = sign * 5.2
    y_over = sign * (B_i + 0.3)
    g(PART)
    X0, Ya = P(x0, y_tip); X1, Yb = P(x1, y_over)
    c.rect(min(X0, X1), min(Ya, Yb), abs(X1 - X0), abs(Yb - Ya), stroke=0, fill=1)
    s(INK); c.setLineWidth(0.8)
    c.line(*P(x0, y_in), *P(x0, y_tip)); c.line(*P(x0, y_tip), *P(x1, y_tip)); c.line(*P(x1, y_tip), *P(x1, y_in))
    for hx in (-1.2, 1.2):
        X, Y = P(hx, sign * 6.1)
        g(MASK); c.circle(X, Y, 0.25 * scale, stroke=1, fill=1)
tab(+1); tab(-1)

# curved through-slots in the ring band
def slot(a, b, t0, t1, width_in=0.55):
    pts = [P(a * math.cos(math.radians(t)), b * math.sin(math.radians(t)))
           for t in [t0 + (t1 - t0) * k / 40 for k in range(41)]]
    for lw_, col in ((width_in * scale + 1.6, INK), (width_in * scale - 0.0, (1, 1, 1))):
        s(col); c.setLineWidth(lw_); c.setLineCap(1)
        pp = c.beginPath(); pp.moveTo(*pts[0])
        for q in pts[1:]: pp.lineTo(*q)
        c.drawPath(pp, stroke=1, fill=0)
    c.setLineCap(0)
for t0, t1 in [(18, 68), (112, 162), (198, 248), (292, 342)]:
    slot(12.9, 8.85, t0, t1)

# dimensions
def arrow(x, y, ang):
    s(INK); g(INK); L_, w_ = 5.5, 1.8
    pa = c.beginPath()
    pa.moveTo(x, y)
    pa.lineTo(x - L_ * math.cos(ang) + w_ * math.sin(ang), y - L_ * math.sin(ang) - w_ * math.cos(ang))
    pa.lineTo(x - L_ * math.cos(ang) - w_ * math.sin(ang), y - L_ * math.sin(ang) + w_ * math.cos(ang))
    pa.close(); c.drawPath(pa, stroke=0, fill=1)

def hdim(x0, x1, y, ext_from_y, label):
    s(INK); c.setLineWidth(0.5)
    c.line(x0, ext_from_y + 3, x0, y + 4); c.line(x1, ext_from_y + 3, x1, y + 4)
    c.line(x0, y, x1, y); arrow(x0, y, math.pi); arrow(x1, y, 0)
    tw = c.stringWidth(label, "Helvetica-Bold", 9) + 8
    g((1, 1, 1)); c.rect((x0 + x1) / 2 - tw / 2, y - 5, tw, 10, stroke=0, fill=1)
    text((x0 + x1) / 2, y - 3, label, 9, bold=True, anchor="c")

def vdim(x, y0, y1, ext_from_x, label):
    s(INK); c.setLineWidth(0.5)
    c.line(ext_from_x + 3, y0, x + 4, y0); c.line(ext_from_x + 3, y1, x + 4, y1)
    if abs(y1 - y0) >= 16:
        c.line(x, y0, x, y1); arrow(x, y0, -math.pi / 2); arrow(x, y1, math.pi / 2)
    else:  # small span: arrows outside pointing in
        c.line(x, y0 - 12, x, y0); c.line(x, y1, x, y1 + 12)
        arrow(x, y0, math.pi / 2); arrow(x, y1, -math.pi / 2)
    text(x + 7, (y0 + y1) / 2 - 3, label, 9, bold=True)

left, right = pcx - plan_w / 2, pcx + plan_w / 2
hdim(left, right, pl_top + 14, pcy, "29.000")
vdim(right + 14, pcy - plan_h / 2, pcy + plan_h / 2, pcx, "20.980")

# balloon M1 with leader to a masked hole
hx, hy = P(1.2, 6.1)
bx, by = hx + 70, pl_top + 2
s(INK); c.setLineWidth(0.6)
c.line(hx + 0.25 * scale * 0.7, hy + 0.25 * scale * 0.7, bx - 5.5, by - 5.5)
g(INK); c.circle(hx, hy, 1.1, stroke=0, fill=1)
balloon(bx, by, "M1")

# side view, same scale
sv_label_y = pcy - plan_h / 2 - 26
text(vx0, sv_label_y, "Side view", 11, bold=True)
sv_y = sv_label_y - 18
g(PART); s(INK); c.setLineWidth(0.8)
c.rect(left, sv_y, plan_w, T_in * scale, stroke=1, fill=1)
vdim(right + 14, sv_y, sv_y + T_in * scale, right, "0.370")

# legend + scale bar
lg_y = sv_y - 30
g(MASK); c.rect(vx0, lg_y - 1, 9, 9, stroke=0, fill=1)
text(vx0 + 15, lg_y, "Masked surface", 8.5)
bar_in = 5
bar = bar_in * scale
bxr = right
s(INK); c.setLineWidth(0.8)
c.line(bxr - bar, lg_y + 3, bxr, lg_y + 3)
for xx in (bxr - bar, bxr - bar / 2, bxr):
    c.line(xx, lg_y, xx, lg_y + 6)
text(bxr - bar - 6, lg_y, "5 in", 8.5, bold=True, anchor="r")
text(vx0, lg_y - 16, "Orthographic views, both at the same scale. Not to print scale.", 8.5, color=INK2)

# ---------------------------------------------------------------- footer
s(INK); c.setLineWidth(0.5); c.line(M, M + 14, W - M, M + 14)
text(M, M + 2, "Generated Oct 6, 2026 from Camera Plate cheese 2 (v12), Body1", 7.5, color=INK2)
text(W - M, M + 2, "Mockup with stand-in geometry. Actual views are rendered from the Fusion body.", 7.5, color=INK2, anchor="r")

c.showPage(); c.save()
print("ok")
