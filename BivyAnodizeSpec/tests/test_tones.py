import re
import sys
import math
import unittest
import zlib
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / 'commands'))

from anodizeSpec import pdf_writer as pw  # noqa: E402
from anodizeSpec import raster as R  # noqa: E402
from anodizeSpec import render  # noqa: E402

SPIKE = HERE.parent / 'reference' / 'spike-2026-10-06'


def img_from(pixels, w, h):
    rgb = bytearray(w * h * 3)
    alpha = bytearray(w * h)
    for i, (r, g, b, a) in enumerate(pixels):
        rgb[i * 3:i * 3 + 3] = bytes((r, g, b))
        alpha[i] = a
    return R.RasterImage(w, h, bytes(rgb), bytes(alpha), 'png', True)


class ToneTools(unittest.TestCase):
    def test_to_gray_luminance(self):
        img = img_from([(255, 0, 0, 255), (0, 255, 0, 255), (0, 0, 255, 255), (213, 208, 195, 255)], 2, 2)
        g = R.to_gray(img)
        vals = [g.rgb[i * 3] for i in range(4)]
        self.assertEqual(vals, [76, 149, 29, int(213 * .299) + int(208 * .587) + int(195 * .114)])
        self.assertEqual(g.rgb[0::3], g.rgb[1::3])
        self.assertEqual(g.rgb[1::3], g.rgb[2::3])

    def test_normalize_maps_modes(self):
        w, h = 20, 10
        px = []
        for i in range(w * h):
            if i < 120:
                px.append((213, 208, 195, 255))     # body (lum 207)
            elif i < 160:
                px.append((70, 68, 64, 255))        # masked face (lum 67)
            elif i < 170:
                px.append((0, 0, 0, 255))           # edges
            else:
                px.append((9, 9, 9, 0))             # background
        img = img_from(px, w, h)
        out, info = R.normalize_tones(img, 227, 64, expect_dark=True)
        self.assertEqual(info, {'light_mode': 207, 'dark_mode': 66})
        g = out.rgb[1::3]
        self.assertEqual(g[0], 227)
        self.assertEqual(g[130], 64)
        self.assertEqual(g[165], 0)
        self.assertEqual(out.alpha, img.alpha)
        out2, info2 = R.normalize_tones(img, 227, 64, expect_dark=False)
        self.assertEqual(info2, {'light_mode': 207})
        self.assertEqual(out2.rgb[1::3][130], round(66 * 227 / 207))

    def test_clear_center_marker(self):
        w, h = 101, 51
        body = (208, 205, 196, 255)
        px = [body] * (w * h)
        for i in range(w * h):
            if i % w < 30:
                px[i] = (0, 0, 0, 0)                 # transparent background on the left
        c = (h // 2) * w + w // 2
        px[c] = (166, 198, 152, 255)             # marker over the part
        px[c + 1] = (177, 175, 167, 255)         # its darker fringe (not tinted, but off-tone)
        px[c - w] = (0, 0, 0, 255)               # a black edge pixel next to it: stays black
        t = (h // 2) * w + 22                      # inside the search window, over the background
        px[t] = (178, 228, 172, 89)              # marker over the background
        px[t + 1] = (200, 200, 200, 120)         # its semi-transparent fringe
        px[0] = (178, 228, 172, 89)              # green but far from the centre: left alone
        img = img_from(px, w, h)
        out, n = R.clear_center_marker(img, 0.3)
        self.assertGreaterEqual(n, 4)
        self.assertEqual(tuple(out.rgb[c * 3:c * 3 + 3]), (208, 205, 196))
        self.assertEqual(tuple(out.rgb[(c + 1) * 3:(c + 1) * 3 + 3]), (208, 205, 196))
        self.assertEqual(tuple(out.rgb[(c - w) * 3:(c - w) * 3 + 3]), (0, 0, 0))
        self.assertEqual(out.alpha[t], 0)
        self.assertEqual(out.alpha[t + 1], 0)
        self.assertEqual(out.alpha[0], 89)
        untouched, m = R.clear_center_marker(img_from([body] * 9, 3, 3), 0.3)
        self.assertEqual(m, 0)

    def test_real_spike_captures(self):
        if not (SPIKE / 'plan_transparent.png').exists():
            self.skipTest('spike captures not present')
        plan = R.load_image(SPIKE / 'plan_transparent.png')
        side = R.load_image(SPIKE / 'side_transparent.png')
        self.assertTrue(plan.has_real_alpha and side.has_real_alpha)
        side_clean, n = R.clear_center_marker(side, 0.01)
        self.assertGreater(n, 0)                         # the pivot marker is at the centre of the side view
        plan_n, info = R.normalize_tones(plan, 227, 64, expect_dark=True)
        self.assertEqual(info['dark_mode'], 66)      # luminance of the (70, 68, 64) masked face
        self.assertIn(info['light_mode'], range(200, 212))
        st = R.pixel_stats(plan_n, 227, 64)
        self.assertGreater(st['light_fraction_of_opaque'], 0.40)
        self.assertGreater(st['dark_fraction_of_opaque'], 0.35)
        tif = R.load_image(SPIKE / 'plan_transparent.tif')   # Fusion writes Deflate TIFF
        self.assertEqual((tif.width, tif.height), (2400, 931))
        self.assertTrue(tif.has_real_alpha)


class GrayImages(unittest.TestCase):
    def test_device_gray_image(self):
        w = pw.PDFWriter()
        w.image(bytes([10, 20, 30] * 4), bytes([255] * 4), 2, 2, 0, 0, 10, 10, grayscale=True)
        pdf = w.to_bytes()
        self.assertIn(b'/ColorSpace /DeviceGray /BitsPerComponent 8 /Interpolate true /SMask', pdf)
        self.assertIn(b'/ImageB', pdf)
        m = re.search(rb'/DeviceGray /BitsPerComponent 8 /Interpolate true /SMask \d+ 0 R /Filter /FlateDecode /Length (\d+) >>\nstream\n', pdf)
        data = zlib.decompress(pdf[m.end():m.end() + int(m.group(1))])
        self.assertEqual(data, bytes([20] * 4))


class CaptureSize(unittest.TestCase):
    def test_sizes(self):
        # Spike viewport 3420x1277 (aspect 2.68) and a square part: widen so the part spans ~2400 px.
        w, h = render.plan_capture_size(3420, 1277, 1.0, 2400, 6000, 1.1)
        self.assertEqual((w, h), (6000, 2240))
        # Camera plate 29 x 21 (aspect 1.38): width governed by the cap.
        w, h = render.plan_capture_size(3420, 1277, 29 / 20.98, 2400, 6000, 1.1)
        self.assertEqual(w, round(2400 * 1.1 * (3420 / 1277) / (29 / 20.98)))
        self.assertAlmostEqual(w / h, 3420 / 1277, places=2)
        # A side view (very wide part): just the margin.
        w, h = render.plan_capture_size(3420, 1277, 29 / 0.37, 2400, 6000, 1.1)
        self.assertEqual(w, 2640)
        # A tall viewport and a wide part: width governs, no widening.
        w, h = render.plan_capture_size(1000, 1500, 2.0, 2400, 6000, 1.1)
        self.assertEqual((w, h), (2640, 3960))
        # Never below RENDER_PX, never above the cap.
        self.assertEqual(render.plan_capture_size(1000, 1000, 1.0, 2400, 6000, 1.0)[0], 2400)
        self.assertEqual(render.plan_capture_size(4000, 1000, 0.1, 2400, 6000, 1.1)[0], 6000)


class IsoCamera(unittest.TestCase):
    def test_cube_is_a_vertex_up_hexagon(self):
        d, up, w, h = render.iso_camera(1.0, 1.0, 1.0)
        self.assertAlmostEqual(w, math.sqrt(2), places=6)
        self.assertAlmostEqual(h, 4 / math.sqrt(6), places=6)
        self.assertAlmostEqual(sum(v * v for v in d), 1.0, places=9)
        self.assertEqual(up, (0.0, 0.0, 1.0))
        self.assertTrue(d[0] > 0 and d[1] < 0 and d[2] > 0)          # +X, -Y, +Z octant

    def test_flipped_plan_looks_from_below(self):
        d, up, w, h = render.iso_camera(2.0, 1.0, 0.5, from_plus_z=False)
        self.assertLess(d[2], 0)
        self.assertEqual(up, (0.0, 0.0, -1.0))
        d2, _, w2, h2 = render.iso_camera(2.0, 1.0, 0.5, from_plus_z=True)
        self.assertAlmostEqual(w, w2)
        self.assertAlmostEqual(h, h2)

    def test_flat_plate_is_wider_than_tall(self):
        _, _, w, h = render.iso_camera(10.0, 10.0, 0.1)
        self.assertAlmostEqual(w, 10 * math.sqrt(2), places=6)
        self.assertGreater(w / h, 1.5)

    def test_iso_view_image_keeps_pixel_aspect(self):
        from anodizeSpec.sheet_layout import iso_view_image
        img = R.RasterImage(40, 20, bytes(40 * 20 * 3), bytes([255]) * 800, 'png', True)
        v = iso_view_image(img, (10, 5, 30, 15), 14.0)
        self.assertEqual(v.extent_in, (14.0, 7.0))


if __name__ == '__main__':
    unittest.main()
