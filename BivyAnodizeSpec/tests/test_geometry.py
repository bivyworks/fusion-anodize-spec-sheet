import math
import sys
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / 'commands'))

from anodizeSpec import geometry as G  # noqa: E402


def face(kind='plane', area=1.0, normal=(0, 0, 1), center=(0, 0, 0), ext=(1, 1, 0), radius=None, axis=None,
         internal=None, masked=True, index=0):
    mn = tuple(c - e / 2 for c, e in zip(center, ext))
    mx = tuple(c + e / 2 for c, e in zip(center, ext))
    return G.FaceInfo(index, kind, area, mn, mx, normal, center, radius, axis, center if axis else None, internal, masked)


class Frame(unittest.TestCase):
    def test_sort_axes_right_handed(self):
        axes = [(2.0, (0, 1, 0)), (0.5, (0, 0, 1)), (5.0, (1, 0, 0))]
        x, y, z, (L, W, T) = G.sort_axes(axes)
        self.assertEqual((L, W, T), (5.0, 2.0, 0.5))
        self.assertEqual(x, (1.0, 0.0, 0.0))
        self.assertEqual(y, (0.0, 1.0, 0.0))
        self.assertEqual(z, (0.0, 0.0, 1.0))
        # left-handed input direction for T is ignored; z is always x cross y
        axes = [(5.0, (1, 0, 0)), (2.0, (0, 1, 0)), (0.5, (0, 0, -1))]
        x, y, z, _ = G.sort_axes(axes)
        self.assertEqual(z, (0.0, 0.0, 1.0))
        # rotated frame
        s = math.sqrt(0.5)
        axes = [(0.5, (0, 0, 1)), (5.0, (s, s, 0)), (2.0, (-s, s, 0))]
        x, y, z, (L, W, T) = G.sort_axes(axes)
        self.assertAlmostEqual(G.dot(x, y), 0.0)
        self.assertAlmostEqual(G.norm(z), 1.0)
        self.assertAlmostEqual(z[2], 1.0)

    def test_canonical_rows_maps_center_and_axes(self):
        s = math.sqrt(0.5)
        x, y, z = (s, s, 0.0), (-s, s, 0.0), (0.0, 0.0, 1.0)
        c = (10.0, 20.0, 5.0)
        rows = G.canonical_rows(c, x, y, z)
        self.assertEqual(len(rows), 16)
        p = G.apply_rows(rows, c)
        self.assertTrue(all(abs(v) < 1e-12 for v in p))
        p = G.apply_rows(rows, (c[0] + 3 * x[0], c[1] + 3 * x[1], c[2] + 3 * x[2]))
        self.assertAlmostEqual(p[0], 3.0)
        self.assertAlmostEqual(p[1], 0.0)
        p = G.apply_rows(rows, (c[0], c[1], c[2] + 2))
        self.assertAlmostEqual(p[2], 2.0)
        self.assertEqual(rows[12:], [0.0, 0.0, 0.0, 1.0])


class Flip(unittest.TestCase):
    def test_scores_and_tie(self):
        faces = [face(normal=(0, 0, 1), masked=False), face(normal=(0, 0, -1), masked=False),
                 face(normal=(1, 0, 0), masked=False)]
        self.assertEqual(G.plan_side_scores(faces), (1.0, 1.0))
        self.assertTrue(G.auto_view_from_plus_z(faces))          # tie -> +Z
        faces.append(face(normal=(0, 0, -1), masked=True))         # masked face: +3
        self.assertEqual(G.plan_side_scores(faces), (1.0, 5.0))
        self.assertFalse(G.auto_view_from_plus_z(faces))
        faces += [face(normal=(0, 0.5, 0.87), masked=False)] * 5
        self.assertTrue(G.auto_view_from_plus_z(faces))


class Groups(unittest.TestCase):
    def test_descriptions_and_anchors(self):
        env = (29.0, 20.98, 0.37)
        bores = [face('cylinder', 0.58, normal=(1, 0, 0), center=(x, y, 0), ext=(0.5, 0.5, 0.37), radius=0.25,
                      axis=(0, 0, 1), internal=True, index=i) for i, (x, y) in enumerate([(-1.2, 6.1), (1.2, 6.1), (-1.2, -6.1), (1.2, -6.1)])]
        boss = face('cylinder', 2.0, normal=(1, 0, 0), center=(9, 0, 0), ext=(1, 1, 0.2), radius=0.5, axis=(0, 0, 1),
                    internal=False, index=10)
        bottom = face('plane', 3.12, normal=(0, 0, -1), center=(0, 0, -0.185), ext=(3, 1, 0), index=11)
        side = face('plane', 0.42, normal=(-1, 0, 0), center=(-14.5, 0, 0), ext=(0, 2, 0.37), index=12)
        fillet = face('torus', 0.05, normal=(0.7, 0, 0.7), center=(5, 5, 0.1), ext=(0.2, 0.2, 0.1), index=13)
        unmasked = face('plane', 500.0, normal=(0, 0, 1), masked=False, index=14)
        faces = bores + [boss, bottom, side, fillet, unmasked]
        groups = G.build_groups(faces, env, from_plus_z=True)
        labels = {g.description: g for g in groups}
        self.assertEqual([g.label for g in groups], ['M1', 'M2', 'M3', 'M4', 'M5'])
        self.assertIn('Planar face — bottom, 3.12 sq in', labels)
        self.assertEqual(labels['Planar face — bottom, 3.12 sq in'].view, 'none')
        b = labels['4× Ø0.500 bore, through']
        self.assertEqual(b.count, 4)
        self.assertEqual(b.view, 'plan')
        self.assertAlmostEqual(b.area_in2, 4 * 0.58)
        self.assertEqual(b.sub, 'Cylindrical, 2.32 sq in total')
        self.assertEqual(b.anchor_in, (-1.2, 6.1, 0.0))
        self.assertEqual(labels['Ø1.000 boss'].view, 'plan')
        s = labels['Planar face — side, 0.42 sq in']
        self.assertEqual(s.view, 'none')      # faces -X: hidden in plan (+Z) and side (-Y) views
        self.assertEqual(labels['Fillet / toroidal face, 0.05 sq in'].view, 'plan')
        # bottom becomes visible when the plan view is flipped
        groups = G.build_groups(faces, env, from_plus_z=False)
        self.assertEqual({g.description: g.view for g in groups}['Planar face — bottom, 3.12 sq in'], 'plan')

    def test_side_view_and_multiples(self):
        env = (4.0, 3.0, 2.0)
        f1 = face('plane', 6.0, normal=(0, -1, 0), center=(0, -1.5, 0), ext=(4, 0, 2), index=0)
        f2 = face('plane', 6.0, normal=(0, 1, 0), center=(0, 1.5, 0), ext=(4, 0, 2), index=1)
        groups = G.build_groups([f1, f2], env, True)
        self.assertEqual(len(groups), 1)
        self.assertEqual(groups[0].description, '2× Planar face — side, 6.00 sq in each')
        self.assertEqual(groups[0].sub, '12.00 sq in total')
        self.assertEqual(groups[0].view, 'side')
        self.assertEqual(groups[0].anchor_in, (0.0, -1.5, 0.0))
        self.assertEqual(G.choose_view(f2, True), 'none')

    def test_through_detection(self):
        env = (10.0, 5.0, 1.0)
        short = face('cylinder', 0.3, center=(0, 0, 0.25), ext=(0.5, 0.5, 0.5), radius=0.25, axis=(0, 0, 1), internal=True)
        self.assertFalse(G._through(short, env))
        full = face('cylinder', 0.6, center=(0, 0, 0), ext=(0.5, 0.5, 1.0), radius=0.25, axis=(0, 0, 1), internal=True)
        self.assertTrue(G._through(full, env))
        tilted = face('cylinder', 0.6, center=(0, 0, 0), ext=(0.5, 0.5, 1.0), radius=0.25, axis=(0.7, 0, 0.7), internal=True)
        self.assertFalse(G._through(tilted, env))

    def test_units(self):
        self.assertAlmostEqual(G.cm_to_in(2.54), 1.0)
        self.assertAlmostEqual(G.cm2_to_in2(6.4516), 1.0)


if __name__ == '__main__':
    unittest.main()
