import random
import sys
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / 'commands'))
sys.path.insert(0, str(HERE))

from anodizeSpec import raster as R  # noqa: E402
import imgtools as T  # noqa: E402


def sample_image(w=37, h=23, seed=1):
    rnd = random.Random(seed)
    rgb = bytearray(w * h * 3)
    alpha = bytearray(w * h)
    for y in range(h):
        for x in range(w):
            i = y * w + x
            rgb[i * 3:i * 3 + 3] = bytes(rnd.getrandbits(8) for _ in range(3))
            inside = 5 <= x < 30 and 4 <= y < 19
            alpha[i] = 255 if inside else 0
    alpha[6 * w + 7] = 128
    return w, h, bytes(rgb), bytes(alpha)


def naive_unfilter(ftype, row, prev, bpp):
    out = bytearray(row)
    for x in range(len(row)):
        a = out[x - bpp] if x >= bpp else 0
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
        out[x] = (row[x] + p) & 255
    return bytes(out)


class SwarHelpers(unittest.TestCase):
    def test_against_naive(self):
        rnd = random.Random(7)
        for n in (1, 3, 4, 7, 16, 97, 1024):
            for bpp in (1, 3, 4):
                row = bytes(rnd.getrandbits(8) for _ in range(n))
                prev = bytes(rnd.getrandbits(8) for _ in range(n))
                self.assertEqual(R._prefix_sum_mod256(row, bpp), naive_unfilter(1, row, bytes(n), bpp), (n, bpp))
                self.assertEqual(R._add_rows(row, prev), naive_unfilter(2, row, prev, bpp))
                self.assertEqual(R._unfilter_average(row, prev, bpp), naive_unfilter(3, row, prev, bpp))
                self.assertEqual(R._unfilter_paeth(row, prev, bpp), naive_unfilter(4, row, prev, bpp))


class Decoders(unittest.TestCase):
    def setUp(self):
        self.w, self.h, self.rgb, self.alpha = sample_image()
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def check(self, path, alpha_expected=True):
        img = R.load_image(path)
        self.assertEqual((img.width, img.height), (self.w, self.h))
        self.assertEqual(img.rgb, self.rgb, path)
        if alpha_expected:
            self.assertEqual(img.alpha, self.alpha, path)
            self.assertTrue(img.has_real_alpha)
        else:
            self.assertIsNone(img.alpha)
        return img

    def test_png_all_filters(self):
        for ft in ('adaptive', 0, 1, 2, 3, 4):
            p = self.dir / f'f{ft}.png'
            T.write_png(p, self.w, self.h, self.rgb, self.alpha, ft)
            img = self.check(p)
            self.assertEqual(img.source_format, 'png')
        T.write_png(self.dir / 'rgb.png', self.w, self.h, self.rgb, None)
        self.check(self.dir / 'rgb.png', alpha_expected=False)

    def test_bmp_variants(self):
        T.write_bmp(self.dir / 'a.bmp', self.w, self.h, self.rgb, self.alpha)
        self.check(self.dir / 'a.bmp')
        T.write_bmp(self.dir / 'b.bmp', self.w, self.h, self.rgb, self.alpha, top_down=True, bitfields=True)
        self.check(self.dir / 'b.bmp')
        T.write_bmp(self.dir / 'c.bmp', self.w, self.h, self.rgb, None)
        self.check(self.dir / 'c.bmp', alpha_expected=False)
        T.write_bmp(self.dir / 'd.bmp', self.w, self.h, self.rgb, None, top_down=True)
        self.check(self.dir / 'd.bmp', alpha_expected=False)

    def test_tiff_variants(self):
        for comp in ('none', 'lzw', 'packbits'):
            for pred in (1, 2):
                for be in (False, True):
                    p = self.dir / f'{comp}{pred}{be}.tif'
                    T.write_tiff(p, self.w, self.h, self.rgb, self.alpha, comp, pred, rows_per_strip=7, big_endian=be)
                    self.check(p)
        T.write_tiff(self.dir / 'rgb.tif', self.w, self.h, self.rgb, None, 'lzw', 2)
        self.check(self.dir / 'rgb.tif', alpha_expected=False)

    def test_lzw_table_growth(self):
        rnd = random.Random(3)
        w, h = 300, 60
        rgb = bytes(rnd.getrandbits(8) for _ in range(w * h * 3))
        T.write_tiff(self.dir / 'n.tif', w, h, rgb, None, 'lzw', 1)
        self.assertEqual(R.load_image(self.dir / 'n.tif').rgb, rgb)

    def test_degenerate_alpha_is_ignored(self):
        T.write_bmp(self.dir / 'o.bmp', self.w, self.h, self.rgb, bytes(self.w * self.h))
        self.assertIsNone(R.load_image(self.dir / 'o.bmp').alpha)
        T.write_bmp(self.dir / 'p.bmp', self.w, self.h, self.rgb, bytes([255]) * (self.w * self.h))
        self.assertIsNone(R.load_image(self.dir / 'p.bmp').alpha)

    def test_unknown_and_jpeg(self):
        with self.assertRaises(R.RasterError):
            R.decode(b'\xff\xd8\xff\xe0 jpeg')
        with self.assertRaises(R.RasterError):
            R.decode(b'nonsense')


class Cropping(unittest.TestCase):
    def test_crop_and_margin(self):
        w, h, rgb, alpha = sample_image()
        img = R.RasterImage(w, h, rgb, alpha, 'png', True)
        self.assertEqual(R.content_bounds(img), (5, 4, 30, 19))
        c, box = R.crop_to_content(img)
        self.assertEqual((c.width, c.height, box), (25, 15, (0, 0, 25, 15)))
        self.assertEqual(c.alpha[0], alpha[4 * w + 5])
        c2, box2 = R.crop_to_content(img, margin=3)
        self.assertEqual((c2.width, c2.height, box2), (31, 21, (3, 3, 28, 18)))
        c3, box3 = R.crop_to_content(img, margin=100)
        self.assertEqual((c3.width, c3.height), (w, h))
        self.assertEqual(box3, (5, 4, 30, 19))

    def test_empty_image(self):
        img = R.RasterImage(4, 4, bytes(48), bytes(16), 'png', True)
        self.assertIsNone(R.content_bounds(img))
        c, box = R.crop_to_content(img)
        self.assertEqual((c.width, c.height), (4, 4))

    def test_chroma_key(self):
        w, h, rgb, alpha = sample_image()
        rgb2 = bytearray(rgb)
        for i in range(w * h):
            if alpha[i] == 0:
                rgb2[i * 3:i * 3 + 3] = b'\x10\x20\x30'
        img = R.RasterImage(w, h, bytes(rgb2), None, 'bmp', False)
        self.assertEqual(R.background_color(img), (16, 32, 48))
        derived = R.alpha_from_background(img)
        self.assertEqual(derived, bytes(255 if a else 0 for a in alpha))
        c, box = R.crop_to_content(img)
        self.assertEqual(box, (0, 0, 25, 15))


class Fixtures(unittest.TestCase):
    def test_fixture_variants_agree(self):
        fx = HERE / 'fixtures'
        a = R.load_image(fx / 'plan_small.png')
        b = R.load_image(fx / 'plan_small.bmp')
        c = R.load_image(fx / 'plan_small.tif')
        self.assertEqual(a.rgb, b.rgb)
        self.assertEqual(a.rgb, c.rgb)
        self.assertEqual(a.alpha, b.alpha)
        self.assertEqual(a.alpha, c.alpha)
        big = R.load_image(fx / 'plan.png')
        self.assertTrue(big.has_real_alpha)
        cropped, box = R.crop_to_content(big)
        self.assertEqual((cropped.width, cropped.height), (1200, 868))


if __name__ == '__main__':
    unittest.main()


class PixelStats(unittest.TestCase):
    def test_fractions(self):
        w, h = 10, 4
        rgb = bytearray(w * h * 3)
        alpha = bytearray(w * h)
        for i in range(w * h):
            if i < 20:                      # opaque light
                rgb[i * 3:i * 3 + 3] = bytes((227, 227, 227)); alpha[i] = 255
            elif i < 30:                    # opaque dark
                rgb[i * 3:i * 3 + 3] = bytes((64, 64, 64)); alpha[i] = 255
            else:                           # transparent noise
                rgb[i * 3:i * 3 + 3] = bytes((9, 200, 9)); alpha[i] = 0
        img = R.RasterImage(w, h, bytes(rgb), bytes(alpha), 'png', True)
        st = R.pixel_stats(img, 227, 64)
        self.assertAlmostEqual(st['opaque_fraction'], 0.75)
        self.assertAlmostEqual(st['light_fraction_of_opaque'], 20 / 30)
        self.assertAlmostEqual(st['dark_fraction_of_opaque'], 10 / 30)
        self.assertAlmostEqual(st['mean_gray_opaque'], (20 * 227 + 10 * 64) / 30)
        self.assertEqual(st['alpha_distinct_values'], 2)
        empty = R.RasterImage(2, 2, bytes(12), bytes(4), 'png', True)
        self.assertEqual(R.pixel_stats(empty, 227, 64)['opaque_fraction'], 0.0)
