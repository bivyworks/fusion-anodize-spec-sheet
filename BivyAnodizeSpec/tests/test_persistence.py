import sys
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / 'commands'))

from anodizeSpec import persistence as P  # noqa: E402
from anodizeSpec.spec import AnodizeSpec  # noqa: E402


class LocalSettings(unittest.TestCase):
    def test_path_is_outside_addin_folder(self):
        p = P.settings_path()
        self.assertEqual(p.name, 'anodize_spec.json')
        self.assertEqual(p.parent.name, 'BivyWorks')
        self.assertNotIn(str(HERE.parent), str(p))

    def test_round_trip_and_merge(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / 'sub' / 'anodize_spec.json'
            self.assertEqual(P.load_settings(p), {})
            self.assertTrue(P.save_settings({'last_customer': 'ACME', 'last_company': 'BW'}, p))
            self.assertTrue(P.save_settings({'last_save_dir': '/tmp/x', 'last_company': None}, p))
            data = P.load_settings(p)
            self.assertEqual(data, {'last_customer': 'ACME', 'last_company': 'BW', 'last_save_dir': '/tmp/x'})
            self.assertTrue(P.remember_last_used(AnodizeSpec(customer_project='C1', company='Co'), '/out', p))
            self.assertEqual(P.load_settings(p)['last_customer'], 'C1')
            self.assertEqual(P.load_settings(p)['last_save_dir'], '/out')
            p.write_text('not json', encoding='utf-8')
            self.assertEqual(P.load_settings(p), {})

    def test_fusion_half_refuses_outside_fusion(self):
        if P.adsk is None:
            with self.assertRaises(RuntimeError):
                P.read_spec_from_body(object())


if __name__ == '__main__':
    unittest.main()
