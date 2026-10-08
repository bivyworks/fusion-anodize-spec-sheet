import json
import sys
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / 'commands'))

from anodizeSpec import spec as S  # noqa: E402


class SpecRoundTrip(unittest.TestCase):
    def test_round_trip(self):
        a = S.AnodizeSpec(part_name='X', part_number='PN', revision='B', quantity=12, material='7075 Aluminum',
                          anodize_type='Type III', anodize_class='Class 2', color='red',
                          thickness_min_in=0.001, thickness_max_in=0.0015, brite_dip=45, flip_view=True)
        b = S.AnodizeSpec.from_json(a.to_json())
        self.assertEqual(b, S.AnodizeSpec.from_dict(a.to_dict()))
        self.assertEqual(b.color, 'RED')
        self.assertEqual(b.brite_dip, 45)
        self.assertTrue(b.flip_view)
        self.assertEqual(json.loads(a.to_json())['spec_version'], S.SPEC_VERSION)

    def test_unknown_and_missing_keys(self):
        s = S.AnodizeSpec.from_dict({'part_name': 'Old', 'future_field': 1, 'spec_version': 99})
        self.assertEqual(s.part_name, 'Old')
        self.assertEqual(s.quantity, 1)
        self.assertEqual(s.company, S.config.DEFAULT_COMPANY)
        self.assertIsNone(s.brite_dip)
        self.assertEqual(S.AnodizeSpec.from_dict('garbage'), S.AnodizeSpec())
        self.assertEqual(S.AnodizeSpec.from_json('{not json'), S.AnodizeSpec())

    def test_coercions(self):
        s = S.AnodizeSpec.from_dict({'quantity': '0', 'thickness_min_in': '0.0015 in', 'thickness_max_in': 'abc',
                                     'flip_view': 'yes', 'brite_dip': 'NONE', 'part_number': None})
        self.assertEqual(s.quantity, 1)
        self.assertEqual(s.thickness_min_in, 0.0015)
        self.assertIsNone(s.thickness_max_in)
        self.assertTrue(s.flip_view)
        self.assertEqual(s.brite_dip, 'none')
        self.assertEqual(s.part_number, '')

    def test_brite_dip_normalization(self):
        for raw, want in [(None, None), ('', None), ('none', 'none'), ('Do not dip', 'none'), (45, 45), ('45 s', 45),
                          ('60 seconds', 60), (0, None), (-5, None), ('zero', None), (True, None), (30.0, 30)]:
            self.assertEqual(S.normalize_brite_dip(raw), want, raw)


class Presets(unittest.TestCase):
    def test_seed_file_loads(self):
        presets = S.load_presets()
        self.assertEqual(presets[0]['label'], '— Custom —')
        self.assertEqual(len(presets), 7)
        self.assertEqual(presets[1]['thickness_max_in'], 0.0015)
        self.assertIsNone(presets[2]['thickness_min_in'])

    def test_missing_file_falls_back(self):
        presets = S.load_presets(HERE / 'does_not_exist.json')
        self.assertEqual(len(presets), 7)

    def test_apply_preset_leaves_nulls_alone(self):
        s = S.AnodizeSpec(thickness_min_in=0.002, thickness_max_in=0.003, brite_dip=30)
        S.apply_preset(s, S.load_presets()[2])  # Type III · Class 1 · Clear, null thickness
        self.assertEqual((s.anodize_type, s.anodize_class, s.color), ('Type III', 'Class 1', 'CLEAR'))
        self.assertEqual((s.thickness_min_in, s.thickness_max_in), (0.002, 0.003))
        self.assertEqual(s.brite_dip, 30)
        S.apply_preset(s, S.load_presets()[1])
        self.assertEqual((s.thickness_min_in, s.thickness_max_in), (0.001, 0.0015))
        S.apply_preset(s, {'label': 'x', 'brite_dip': 'none'})
        self.assertEqual(s.brite_dip, 'none')
        S.apply_preset(s, S.load_presets()[0])  # Custom: no change
        self.assertEqual(s.color, 'BLACK')


class Formatting(unittest.TestCase):
    def test_thickness(self):
        self.assertEqual(S.format_thickness(0.0015), '0.0015')
        self.assertEqual(S.thickness_text(0.001, 0.0015), '0.0010–0.0015')
        self.assertEqual(S.thickness_text(0.001, None), '0.0010 min')
        self.assertEqual(S.thickness_text(None, 0.0015), '0.0015 max')
        self.assertEqual(S.thickness_text(None, None), 'Not specified')

    def test_callout_cells(self):
        s = S.AnodizeSpec(anodize_type='Type III', anodize_class='Class 2', color='black',
                          thickness_min_in=0.001, thickness_max_in=0.0015, brite_dip='none')
        self.assertEqual(S.callout_cells(s), [('TYPE III', 'Hardcoat'), ('CLASS 2', 'Dyed'), ('BLACK', 'Color'),
                                              ('0.0010–0.0015', 'Coating thickness, in'),
                                              ('NONE', 'NO BRITE DIP')])
        s.brite_dip, s.color, s.anodize_type, s.anodize_class = 45, 'clear', 'Type I', 'Class 1'
        self.assertEqual(S.callout_cells(s)[4], ('45 SEC', 'BRITE DIP'))
        self.assertEqual(S.callout_cells(s)[2], ('CLEAR', 'Undyed / natural'))
        self.assertEqual(S.callout_cells(s)[0], ('TYPE I', 'Chromic'))
        self.assertEqual(S.callout_cells(s)[1], ('CLASS 1', 'Undyed'))

    def test_material(self):
        self.assertEqual(S.map_material('Aluminum 6061-T6'), '6061 Aluminum')
        self.assertEqual(S.map_material('MIC-6 cast plate'), 'MIC-6 Aluminum')
        self.assertEqual(S.map_material('Titanium 6Al-4V'), 'Ti-6Al-4V Titanium')
        self.assertEqual(S.map_material('Steel'), '')
        self.assertEqual(S.material_line(S.AnodizeSpec(material='6061 Aluminum', temper='T6')), '6061-T6 aluminum')
        self.assertEqual(S.material_line(S.AnodizeSpec(material='6061 Aluminum', temper='Other')), '6061 aluminum')
        self.assertEqual(S.material_line(S.AnodizeSpec(material='Titanium', temper='T6')), 'Titanium')
        self.assertTrue(S.is_anodizable('6061 Aluminum'))
        self.assertFalse(S.is_anodizable('Stainless Steel'))

    def test_date_and_area(self):
        import datetime
        self.assertEqual(S.format_date(datetime.date(2026, 10, 6)), 'Oct 6, 2026')
        self.assertRegex(S.format_date(), r'^[A-Z][a-z]{2} \d{1,2}, \d{4}$')
        self.assertEqual(S.format_area(542.3), '542.30 sq in')
        self.assertEqual(S.format_envelope((29.0, 20.98, 0.37)), '29.000 × 20.980 × 0.370 in')

    def test_filenames(self):
        s = S.AnodizeSpec(part_name='Camera Plate cheese 2', part_number='BW-1042', revision='A')
        self.assertEqual(S.default_filename(s), 'BW-1042_A_Camera Plate cheese 2_ANODIZE.pdf')
        self.assertEqual(S.default_filename(S.AnodizeSpec(part_name='Bracket')), 'Bracket_ANODIZE.pdf')
        self.assertEqual(S.default_filename(S.AnodizeSpec()), 'part_ANODIZE.pdf')
        bad = S.default_filename(S.AnodizeSpec(part_name='a/b:c*?"<>|', part_number='CON'))
        self.assertNotRegex(bad, r'[<>:"/\\|?*]')
        self.assertTrue(bad.endswith('_ANODIZE.pdf'))
        self.assertEqual(S.sanitize_filename('  name. '), 'name')
        self.assertEqual(S.sanitize_filename('CON'), '_CON')
        self.assertEqual(S.sanitize_filename(''), 'untitled')


class Validation(unittest.TestCase):
    def test_blocking(self):
        s = S.AnodizeSpec()
        self.assertEqual(S.validate(s, body_selected=False)[0], 'Select one solid body.')
        self.assertIn('Choose a brite dip option.', S.validate(s))
        s.brite_dip = 'none'
        self.assertEqual(S.validate(s), [])
        s.thickness_min_in, s.thickness_max_in = 0.002, 0.001
        self.assertIn('Min thickness is greater than max thickness.', S.validate(s))
        s.quantity = 0
        self.assertIn('Quantity must be at least 1.', S.validate(s))

    def test_soft(self):
        s = S.AnodizeSpec(anodize_type='Type III', thickness_min_in=0.01, anodize_class='Class 2', color='CLEAR',
                          material='Steel')
        w = S.soft_warnings(s)
        self.assertEqual(len(w), 3)
        self.assertTrue(any('outside the usual' in x for x in w))
        self.assertTrue(any('CLEAR' in x for x in w))
        self.assertTrue(any('not aluminum' in x for x in w))
        self.assertEqual(S.soft_warnings(S.AnodizeSpec(anodize_class='Class 1', color='CLEAR', material='6061 Aluminum')), [])
        self.assertEqual(len(S.soft_warnings(S.AnodizeSpec(anodize_class='Class 1', color='BLUE'))), 1)


if __name__ == '__main__':
    unittest.main()
