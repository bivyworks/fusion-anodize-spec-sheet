"""Generate the test fixtures: a sample spec and synthetic plan / side / isometric renders.

Run once:  python tests/make_fixtures.py
The images stand in for Fusion captures (light gray body, black edges, dark
masked faces, transparent background) so the sheet can be built without
Fusion. Standard library only."""

import json
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import imgtools as T  # noqa: E402

FIX = HERE / 'fixtures'
FIX.mkdir(exist_ok=True)

L_IN, W_IN, T_IN = 29.0, 20.98, 0.37
PLAN_PX = 1200

sample_spec = {
    'spec_version': 1,
    'part_name': 'Camera Plate cheese 2',
    'part_number': 'BW-1042',
    'revision': 'A',
    'customer_project': 'Test',
    'company': 'BIVY WORKS',
    'quantity': 1,
    'material': '6061 Aluminum',
    'temper': 'T6',
    'spec_standard': 'MIL-PRF-8625',
    'anodize_type': 'Type III',
    'anodize_class': 'Class 2',
    'color': 'BLACK',
    'thickness_min_in': 0.001,
    'thickness_max_in': 0.0015,
    'seal': 'Vendor standard',
    'brite_dip': 'none',
    'dims_condition': 'Before anodize',
    'masking_note': 'Tab mounting holes. Plug both sides.',
    'vendor_notes': 'Parts are cosmetic. Please avoid scratches and dents, and hide racking marks.',
    'flip_view': False,
}
(FIX / 'sample_spec.json').write_text(json.dumps(sample_spec, indent=2, ensure_ascii=False) + '\n', encoding='utf-8')

# Four masked tab holes (Ø0.500) plus two unmasked ones, in canonical inches.
holes = [(-1.2, 6.1, 0.25, True), (1.2, 6.1, 0.25, True), (-1.2, -6.1, 0.25, True), (1.2, -6.1, 0.25, True),
         (9.0, 0.0, 0.3, False), (-9.0, 0.0, 0.3, False)]
t0 = time.perf_counter()
w, h, rgb, alpha = T.synth_plate(PLAN_PX, L_IN, W_IN, holes=holes)
# Surround with a transparent margin so cropping has something to do.
mw, mh = w + 80, h + 60
rgb_m = bytearray(mw * mh * 3)
alpha_m = bytearray(mw * mh)
for y in range(h):
    src = y * w
    dst = (y + 30) * mw + 40
    rgb_m[dst * 3:(dst + w) * 3] = rgb[src * 3:(src + w) * 3]
    alpha_m[dst:dst + w] = alpha[src:src + w]
T.write_png(FIX / 'plan.png', mw, mh, bytes(rgb_m), bytes(alpha_m), 'adaptive')
print(f'plan.png {mw}x{mh} in {time.perf_counter() - t0:.1f}s')

sw, sh, srgb, salpha = T.synth_side(w, L_IN, T_IN, scale=w / L_IN)
T.write_png(FIX / 'side.png', sw, sh, srgb, salpha, 'adaptive')
print(f'side.png {sw}x{sh}')

t1 = time.perf_counter()
iw, ih, irgb, ialpha = T.synth_iso(700, L_IN, W_IN, T_IN)
T.write_png(FIX / 'iso.png', iw, ih, irgb, ialpha, 'adaptive')
print(f'iso.png {iw}x{ih} in {time.perf_counter() - t1:.1f}s')

# Small variants of the same plan image in the other containers, for the decoder tests.
small_w, small_h, srgb2, salpha2 = T.synth_plate(160, L_IN, W_IN, holes=holes, supersample=1)
T.write_bmp(FIX / 'plan_small.bmp', small_w, small_h, srgb2, salpha2)
T.write_tiff(FIX / 'plan_small.tif', small_w, small_h, srgb2, salpha2, 'lzw', 2)
T.write_png(FIX / 'plan_small.png', small_w, small_h, srgb2, salpha2, 'adaptive')
print('small variants written')
