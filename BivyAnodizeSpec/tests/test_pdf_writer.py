import re
import sys
import unittest
import zlib
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / 'commands'))

from anodizeSpec import pdf_writer as pw  # noqa: E402


def parse_objects(pdf: bytes):
    """Minimal structural check: every xref offset must point at 'N 0 obj'."""
    assert pdf.startswith(b'%PDF-1.4\n')
    m = re.search(rb'startxref\n(\d+)\n%%EOF\n$', pdf)
    assert m, 'startxref missing'
    xref_pos = int(m.group(1))
    assert pdf[xref_pos:xref_pos + 4] == b'xref'
    header = re.match(rb'xref\n0 (\d+)\n', pdf[xref_pos:])
    n = int(header.group(1))
    table = pdf[xref_pos + header.end():]
    entries = [table[i * 20:(i + 1) * 20] for i in range(n)]
    offsets = []
    for i, e in enumerate(entries[1:], start=1):
        off = int(e[:10])
        assert pdf[off:].startswith(f'{i} 0 obj'.encode()), (i, off, pdf[off:off + 12])
        offsets.append(off)
    return n - 1


def content_streams(pdf: bytes):
    out = []
    for m in re.finditer(rb'<< /Filter /FlateDecode /Length (\d+) >>\nstream\n', pdf):
        length = int(m.group(1))
        out.append(zlib.decompress(pdf[m.end():m.end() + length]))
    return out


class Metrics(unittest.TestCase):
    def test_afm_widths(self):
        self.assertEqual(pw._W_HELVETICA[32], 278)
        self.assertEqual(pw._W_HELVETICA[ord('A')], 667)
        self.assertEqual(pw._W_HELVETICA_BOLD[ord('A')], 722)
        self.assertEqual(pw._W_HELVETICA[0xD8], 778)   # Oslash
        self.assertEqual(pw._W_HELVETICA[0xD7], 584)   # multiply
        self.assertAlmostEqual(pw.string_width('0.0010–0.0015', 30, True), 200.16, places=2)
        self.assertAlmostEqual(pw.string_width('Camera Plate cheese 2', 30, True), 325.17, places=2)
        self.assertAlmostEqual(pw.string_width('AB', 10, False, 1.5), (667 + 667) / 100 + 1.5)
        self.assertEqual(pw.string_width('', 10), 0.0)

    def test_encoding(self):
        data, bad = pw.encode_text('² × — Ø ° ± · – …')
        self.assertEqual(bad, [])
        self.assertEqual(data, '² × — Ø ° ± · – …'.encode('cp1252'))
        data, bad = pw.encode_text('中文 A−B C')
        self.assertEqual(bad, ['中', '文'])
        self.assertEqual(data, b'?? A-B C')


class Writer(unittest.TestCase):
    def test_structure_and_text(self):
        w = pw.PDFWriter(title='T – 中', author='me')
        w.text(10, 10, 'Hello (world) \\ test', 10, bold=True, anchor='r', spacing=0.5)
        w.rect(0, 0, 10, 10, fill=True)
        w.circle(5, 5, 2)
        w.polygon([(0, 0), (1, 0), (0, 1)])
        p = w.path().move_to(0, 0).curve_to(1, 1, 2, 2, 3, 3)
        p.stroke()
        w.set_line_cap(1)
        w.image(bytes(12), bytes(4), 2, 2, 0, 0, 10, 10)
        w.image(bytes(12), None, 2, 2, 0, 0, 10, 10)
        pdf = w.to_bytes()
        n = parse_objects(pdf)
        self.assertEqual(n, 5 + 3 + 1 + 1)     # fixed + image/smask/image + content + page
        self.assertIn(b'/BaseFont /Helvetica-Bold', pdf)
        self.assertIn(b'/Encoding /WinAnsiEncoding', pdf)
        self.assertIn(b'/SMask', pdf)
        self.assertIn(b'/Count 1', pdf)
        content = content_streams(pdf)[0]
        self.assertIn(b'(Hello \\(world\\) \\\\ test) Tj', content)
        self.assertIn(b'/F2 10 Tf 0.5 Tc', content)
        self.assertIn(b'1 J', content)
        self.assertIn(b'/Im1 Do', content)
        self.assertEqual(w.warnings, [])
        with self.assertRaises(ValueError):
            w.image(bytes(5), None, 2, 2, 0, 0, 1, 1)

    def test_warnings_for_unencodable(self):
        w = pw.PDFWriter()
        w.text(0, 0, '相机板', 10)
        w.text(0, 0, '相机板 again', 10)
        self.assertEqual(len(w.warnings), 1)
        parse_objects(w.to_bytes())

    def test_wrap_and_truncate(self):
        w = pw.PDFWriter()
        lines = w.wrap_text('Parts are cosmetic — please avoid scratches and dents and hide racking marks.', 10, False, 200)
        self.assertGreaterEqual(len(lines), 2)
        for ln in lines:
            self.assertLessEqual(pw.string_width(ln, 10), 200)
        self.assertEqual(w.wrap_text('a\n\nb', 10, False, 100), ['a', '', 'b'])
        long_word = 'x' * 200
        self.assertTrue(all(pw.string_width(l, 10) <= 50 for l in w.wrap_text(long_word, 10, False, 50)))
        t = w.truncate('Parts are cosmetic', 10, False, 60)
        self.assertTrue(t.endswith('…'))
        self.assertLessEqual(pw.string_width(t, 10), 60)
        self.assertEqual(w.truncate('short', 10, False, 600), 'short')

    def test_number_format(self):
        self.assertEqual(pw._f(0), '0')
        self.assertEqual(pw._f(-0.0004), '0')
        self.assertEqual(pw._f(7.5), '7.5')
        self.assertEqual(pw._f(792.0), '792')
        self.assertEqual(pw._f(0.12345), '0.123')

    def test_state_cache_with_q_Q(self):
        w = pw.PDFWriter()
        w.set_fill_gray(0.3)
        w.save_state()
        w.set_fill_gray(0.3)          # cached: no op emitted
        w.set_fill_gray(1.0)
        w.restore_state()
        w.set_fill_gray(0.3)          # back to 0.3 after Q: no op needed
        w.set_fill_gray(1.0)
        content = content_streams(w.to_bytes())[0]
        self.assertEqual(content.count(b'0.3 g'), 1)
        self.assertEqual(content.count(b'1 g'), 2)


if __name__ == '__main__':
    unittest.main()
