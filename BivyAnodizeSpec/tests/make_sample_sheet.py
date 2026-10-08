"""Build tests/out/sample.pdf from the fixtures, without Fusion.

    python tests/make_sample_sheet.py            # default fixtures
    python tests/make_sample_sheet.py --no-mask  # no masked faces
    python tests/make_sample_sheet.py --long     # long text / overflow case
    python tests/make_sample_sheet.py --unicode  # part name with CJK characters
    python tests/make_sample_sheet.py --block    # a thick block, isometric view beside the others
    python tests/make_sample_sheet.py --bar      # a long bar, isometric view below the others
    python tests/make_sample_sheet.py --no-iso   # without the isometric view
    python tests/make_sample_sheet.py --spike    # the real Fusion captures from the spike (no isometric)

Then rasterize it in grayscale with  pdftoppm -gray -r 110 -png tests/out/sample.pdf tests/out/sample
and compare against reference/anodize_sheet_mockup.pdf."""

import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / 'commands'))
sys.path.insert(0, str(HERE))

from anodizeSpec import raster  # noqa: E402
from anodizeSpec.render import iso_camera  # noqa: E402
from anodizeSpec.sheet_layout import MaskGroup, SheetData, ViewImage, draw_sheet, iso_view_image  # noqa: E402
from anodizeSpec.spec import AnodizeSpec, format_date  # noqa: E402

FIX = HERE / 'fixtures'
OUT = HERE / 'out'
L_IN, W_IN, T_IN = 29.0, 20.98, 0.37


def load_view(name: str, extent):
    img = raster.load_image(FIX / name)
    cropped, box = raster.crop_to_content(img, margin=0)
    return ViewImage(cropped, box, extent)


def load_iso(name: str, L, W, T):
    img = raster.load_image(FIX / name)
    cropped, box = raster.crop_to_content(img, margin=0)
    return iso_view_image(cropped, box, iso_camera(L, W, T)[2])


def synth_iso_view(L, W, T, width_px=420):
    import imgtools as T_
    w, h, rgb, alpha = T_.synth_iso(width_px, L, W, T)
    return iso_view_image(raster.RasterImage(w, h, rgb, alpha, 'png', True), (0, 0, w, h), iso_camera(L, W, T)[2])


def build(variant: str = 'default') -> SheetData:
    spec = AnodizeSpec.from_dict(json.loads((FIX / 'sample_spec.json').read_text(encoding='utf-8')))
    groups = [
        MaskGroup('M1', '4× Ø0.500 bore', 'Cylindrical, through, 2.33 sq in total', 2.33, 4, (1.2, 6.1, 0.0), 'plan'),
    ]
    masked = 2.33
    if variant == 'no-mask':
        groups, masked = [], 0.0
        spec.masking_note = ''
    elif variant == 'long':
        spec.part_name = 'Camera Plate cheese 2 with an unreasonably long descriptive name for testing'
        spec.color = 'CUSTOM METALLIC GUNMETAL'
        spec.brite_dip = 120
        spec.customer_project = 'Some Customer / Project 2026-10 Lot 7'
        spec.vendor_notes = ('Parts are cosmetic. Please avoid scratches and dents, and hide racking marks. ' * 6).strip()
        spec.masking_note = 'Mask every tapped hole and the datum surface. ' * 3
        groups = groups + [
            MaskGroup('M2', 'Planar face — bottom, 3.12 sq in', 'Datum A', 3.12, 1, (0.0, 0.0, -0.185), 'none'),
            MaskGroup('M3', '2× Ø0.600 bore', 'Cylindrical, through', 0.7, 2, (9.0, 0.0, 0.0), 'plan'),
            MaskGroup('M4', 'Planar face — side, 0.42 sq in', '', 0.42, 1, (-14.5, 0.0, 0.0), 'side'),
            MaskGroup('M5', 'Conical face, 0.10 sq in', '', 0.10, 1, (-1.2, -6.1, 0.1), 'plan'),
            MaskGroup('M6', 'Fillet / toroidal face, 0.05 sq in', '', 0.05, 1, None, 'none'),
            MaskGroup('M7', 'Freeform face, 0.90 sq in', '', 0.9, 1, None, 'none'),
            MaskGroup('M8', 'Planar face — top, 1.00 sq in', '', 1.0, 1, (-9.0, 0.0, 0.185), 'plan'),
            MaskGroup('M9', 'Planar face — top, 0.50 sq in', '', 0.5, 1, None, 'none'),
            MaskGroup('M10', 'Planar face — top, 0.25 sq in', '', 0.25, 1, None, 'none'),
        ]
        masked = sum(g.area_in2 for g in groups)
    elif variant == 'unicode':
        spec.part_name = '相机板 Camera Plate 2'
        spec.customer_project = '客户 Test'
    elif variant == 'block':
        # A thick block: 4 x 3 x 2 in, one masked side face visible in the side view only.
        import imgtools as T
        sys.path.insert(0, str(HERE))
        spec.part_name = 'Clamp Block'
        spec.part_number = 'BW-2001'
        spec.anodize_type, spec.anodize_class, spec.color = 'Type II', 'Class 1', 'CLEAR'
        spec.thickness_min_in, spec.thickness_max_in = None, 0.0008
        spec.brite_dip = 45
        spec.quantity = 250
        spec.masking_note = ''
        groups = [MaskGroup('M1', 'Planar face — side, 6.00 sq in', 'Datum B', 6.0, 1, (0.0, -1.5, 0.0), 'side')]
        pw_, ph_, prgb, palpha = T.synth_side(800, 4.0, 3.0, scale=200)
        sw_, sh_, srgb, salpha = T.synth_side(800, 4.0, 2.0, scale=200)
        return SheetData(spec=spec, surface_area_total_in2=52.0, masked_area_in2=6.0, envelope_in=(4.0, 3.0, 2.0),
                         mask_groups=groups, doc_name='Clamp Block', doc_version='v3', body_name='Body1',
                         date_text=format_date(),
                         plan=ViewImage(raster.RasterImage(pw_, ph_, prgb, palpha, 'png', True), (0, 0, pw_, ph_), (4.0, 3.0)),
                         side=ViewImage(raster.RasterImage(sw_, sh_, srgb, salpha, 'png', True), (0, 0, sw_, sh_), (4.0, 2.0)),
                         iso=synth_iso_view(4.0, 3.0, 2.0))
    elif variant == 'bar':
        # A long thin bar: 10 x 1 x 0.5 in. Width-limited, so the isometric view goes below.
        import imgtools as T
        spec.part_name = 'Guide Bar'
        spec.part_number = 'BW-3010'
        spec.brite_dip = 30
        spec.masking_note = ''
        groups = [MaskGroup('M1', 'Planar face — end, 0.50 sq in', '', 0.5, 1, (5.0, 0.0, 0.0), 'plan')]
        pw_, ph_, prgb, palpha = T.synth_side(1000, 10.0, 1.0, scale=100)
        sw_, sh_, srgb, salpha = T.synth_side(1000, 10.0, 0.5, scale=100)
        return SheetData(spec=spec, surface_area_total_in2=31.0, masked_area_in2=0.5, envelope_in=(10.0, 1.0, 0.5),
                         mask_groups=groups, doc_name='Guide Bar', doc_version='v2', body_name='Body1',
                         date_text=format_date(),
                         plan=ViewImage(raster.RasterImage(pw_, ph_, prgb, palpha, 'png', True), (0, 0, pw_, ph_), (10.0, 1.0)),
                         side=ViewImage(raster.RasterImage(sw_, sh_, srgb, salpha, 'png', True), (0, 0, sw_, sh_), (10.0, 0.5)),
                         iso=synth_iso_view(10.0, 1.0, 0.5))
    return SheetData(
        spec=spec,
        surface_area_total_in2=542.30,
        masked_area_in2=masked,
        envelope_in=(L_IN, W_IN, T_IN),
        mask_groups=groups,
        doc_name='Camera Plate cheese 2',
        doc_version='v12',
        body_name='Body1',
        date_text=format_date(),
        plan=load_view('plan.png', (L_IN, W_IN)),
        side=load_view('side.png', (L_IN, T_IN)),
        iso=None if variant == 'no-iso' else load_iso('iso.png', L_IN, W_IN, T_IN),
    )


def build_spike() -> SheetData:
    """The real Fusion captures from reference/spike-2026-10-06 (a 2.36 in
    bezel ring, test-masked top face) through the production post-processing."""
    from anodizeSpec import render
    spike = HERE.parent / 'reference' / 'spike-2026-10-06'
    plan = raster.load_image(spike / 'plan_transparent.png')
    side = raster.load_image(spike / 'side_transparent.png')
    plan, side = render.postprocess(plan, side, has_masks=True)
    L_, W_, T_ = 2.3583, 2.3583, 0.2264
    spec = AnodizeSpec(part_name='Bezel Ring', part_number='BW-0417', revision='B', customer_project='Spike test',
                       quantity=25, material='6061 Aluminum', anodize_type='Type II', anodize_class='Class 2',
                       color='BLACK', thickness_min_in=0.0005, thickness_max_in=0.0008, brite_dip=30,
                       masking_note='Mask the lapped seating face only.')
    groups = [MaskGroup('M1', 'Planar face — top, 0.64 sq in', 'Lapped seating face', 0.64, 1, (0.0, 1.10, 0.113), 'plan')]
    pc, pbox = raster.crop_to_content(plan)
    sc, sbox = raster.crop_to_content(side)
    return SheetData(spec=spec, surface_area_total_in2=6.57, masked_area_in2=0.64, envelope_in=(L_, W_, T_),
                     mask_groups=groups, doc_name='Main Body', doc_version='v7', body_name='Body1',
                     date_text=format_date(), plan=ViewImage(pc, pbox, (L_, W_)), side=ViewImage(sc, sbox, (L_, T_)))


def main(argv):
    variant = 'default'
    for a in argv:
        if a.startswith('--'):
            variant = a[2:]
    OUT.mkdir(exist_ok=True)
    name = 'sample.pdf' if variant == 'default' else f'sample_{variant}.pdf'
    data = build_spike() if variant == 'spike' else build(variant)
    pdf, warnings = draw_sheet(data, OUT / name)
    print(f'wrote {OUT / name} ({len(pdf)} bytes)')
    for w in warnings:
        print('warning:', w)
    return 0


if __name__ == '__main__':
    sys.exit(main(sys.argv[1:]))
