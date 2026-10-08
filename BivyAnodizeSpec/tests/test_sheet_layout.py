import re
import sys
import unittest
import zlib
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / 'commands'))
sys.path.insert(0, str(HERE))

import make_sample_sheet as MS  # noqa: E402
from anodizeSpec.sheet_layout import draw_sheet, _scale_bar_length  # noqa: E402


def page_text(pdf: bytes) -> bytes:
    out = b''
    for m in re.finditer(rb'<< /Filter /FlateDecode /Length (\d+) >>\nstream\n', pdf):
        out += zlib.decompress(pdf[m.end():m.end() + int(m.group(1))])
    return out


def image_boxes(pdf: bytes):
    """(left, bottom, width, height) of every placed image, in drawing order."""
    boxes = []
    for m in re.finditer(rb'q ([\d.-]+) 0 0 ([\d.-]+) ([\d.-]+) ([\d.-]+) cm /Im\d+ Do Q', page_text(pdf)):
        w, h, x, y = (float(v) for v in m.groups())
        boxes.append((x, y, w, h))
    return boxes


class Variants(unittest.TestCase):
    def test_default(self):
        pdf, warnings = draw_sheet(MS.build('default'))
        self.assertEqual(warnings, [])
        self.assertIn(b'/Count 1', pdf)
        t = page_text(pdf)
        for s in (b'(TYPE III)', b'(CLASS 2)', b'(BLACK)', b'(0.0010\x960.0015)', b'(NONE)', b'(NO BRITE DIP)',
                  b'(542.30 sq in total)', b'(539.97 sq in anodized)', b'(29.000 \xd7 20.980 \xd7 0.370 in)',
                  b'(M1)', b'(Masked surface)', b'(5 in)', b'(29.000)', b'(20.980)', b'(0.370)',
                  b'(ANODIZE PER MIL-PRF-8625)', b'(QUANTITY)', b'(pcs)', b'(Page 1 of 1)', b'(Isometric view)',
                  b'isometric view not to scale', b'Anodize Spec Sheet v'):
            self.assertIn(s, t, s)
        self.assertEqual(t.count(b'/Im'), 3)
        self.assertLess(len(pdf), 3 * 1024 * 1024)

    def test_no_mask(self):
        pdf, warnings = draw_sheet(MS.build('no-mask'))
        t = page_text(pdf)
        self.assertIn(b'(None. Anodize all surfaces.)', t)
        self.assertNotIn(b'(Masked surface)', t)
        self.assertNotIn(b'anodized)', t)

    def test_long_stays_on_one_page(self):
        pdf, warnings = draw_sheet(MS.build('long'))
        self.assertIn(b'/Count 1', pdf)
        self.assertTrue(any('truncated' in w or 'tight' in w for w in warnings))
        t = page_text(pdf)
        self.assertIn(b'(120 SEC)', t)
        self.assertIn(b'(BRITE DIP)', t)
        self.assertIn(b'more)', t)

    def test_unicode_warns_but_valid(self):
        pdf, warnings = draw_sheet(MS.build('unicode'))
        self.assertTrue(any('printed as "?"' in w for w in warnings))
        self.assertIn(b'(??? Camera Plate 2)', page_text(pdf))

    def test_block(self):
        pdf, warnings = draw_sheet(MS.build('block'))
        self.assertEqual(warnings, [])
        t = page_text(pdf)
        self.assertIn(b'(0.0008 max)', t)
        self.assertIn(b'(45 SEC)', t)
        self.assertIn(b'(250)', t)
        self.assertIn(b'(1 in)', t)

    def test_no_iso(self):
        pdf, warnings = draw_sheet(MS.build('no-iso'))
        self.assertEqual(warnings, [])
        t = page_text(pdf)
        self.assertEqual(t.count(b'/Im'), 2)
        self.assertNotIn(b'(Isometric view)', t)
        self.assertIn(b'(Orthographic views, both at the same scale. Not to print scale.)', t)

    def test_iso_beside_for_thick_part(self):
        # Height-limited views: the isometric box sits to their right, top-aligned.
        pdf, warnings = draw_sheet(MS.build('block'))
        self.assertEqual(warnings, [])
        plan, side, iso = image_boxes(pdf)
        self.assertGreater(iso[0], plan[0] + plan[2] + 40)            # right of the plan and its W dimension
        self.assertAlmostEqual(iso[1] + iso[3], plan[1] + plan[3], delta=0.5)   # same top
        self.assertGreater(iso[2], 100)                               # a useful size

    def test_iso_below_for_long_part(self):
        # Width-limited views (a long bar) leave height: the isometric box goes below.
        pdf, warnings = draw_sheet(MS.build('bar'))
        self.assertEqual(warnings, [])
        plan, side, iso = image_boxes(pdf)
        self.assertLess(iso[1] + iso[3], side[1] - 20)                # below the side view
        self.assertGreaterEqual(iso[0], plan[0] - 0.5)
        self.assertAlmostEqual(plan[2], side[2], delta=0.5)           # orthographic scale unchanged

    def test_iso_narrows_before_shrinking_the_views(self):
        # The camera plate is height-limited but wide: the box narrows and the
        # views give up at most 10 %.
        with_iso, _ = draw_sheet(MS.build('default'))
        without, _ = draw_sheet(MS.build('no-iso'))
        plan_w_iso = image_boxes(with_iso)[0][2]
        plan_w_no = image_boxes(without)[0][2]
        self.assertGreaterEqual(plan_w_iso, 0.9 * plan_w_no - 0.5)
        self.assertLess(plan_w_iso, plan_w_no)

    def test_missing_images(self):
        data = MS.build('default')
        data.plan = None
        data.side = None
        pdf, warnings = draw_sheet(data)
        self.assertIn('No plan view image.', warnings)
        self.assertIn(b'/Count 1', pdf)


class ScaleBar(unittest.TestCase):
    def test_rounding(self):
        self.assertEqual(_scale_bar_length(29.0), 5.0)
        self.assertEqual(_scale_bar_length(4.0), 1.0)
        self.assertEqual(_scale_bar_length(100.0), 20.0)
        self.assertEqual(_scale_bar_length(12.0), 2.0)


if __name__ == '__main__':
    unittest.main()
