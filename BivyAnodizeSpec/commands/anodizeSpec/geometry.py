"""Geometry for the sheet: areas, envelope, canonical transform, auto-flip,
masked-face descriptions and balloon anchors.

Fusion's internal unit is the centimetre; this module converts to inches in
one place (``cm_to_in`` / ``cm2_to_in2``) and everything it returns is in
inches. The pure helpers at the top have no ``adsk`` dependency and are unit
tested; the functions at the bottom need Fusion.

Everything is derived from the *native* body (never a proxy) and from
temporary copies of it: the canonical frame comes from the body's own
oriented minimum bounding box, so a body inside a rotated occurrence renders
exactly like the same body at the root.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Sequence, Tuple

try:  # adsk only exists inside Fusion; the pure helpers must import without it.
    import adsk.core  # type: ignore
    import adsk.fusion  # type: ignore
except ImportError:  # pragma: no cover
    adsk = None  # type: ignore

CM_PER_IN = 2.54
Vec = Tuple[float, float, float]


def cm_to_in(v: float) -> float:
    return v / CM_PER_IN


def cm2_to_in2(v: float) -> float:
    return v / (CM_PER_IN * CM_PER_IN)


# ---------------------------------------------------------------- vector math (pure)

def dot(a: Vec, b: Vec) -> float:
    return a[0] * b[0] + a[1] * b[1] + a[2] * b[2]


def cross(a: Vec, b: Vec) -> Vec:
    return (a[1] * b[2] - a[2] * b[1], a[2] * b[0] - a[0] * b[2], a[0] * b[1] - a[1] * b[0])


def norm(a: Vec) -> float:
    return math.sqrt(dot(a, a))


def unit(a: Vec) -> Vec:
    n = norm(a)
    return (a[0] / n, a[1] / n, a[2] / n) if n > 1e-12 else (0.0, 0.0, 0.0)


def sub(a: Vec, b: Vec) -> Vec:
    return (a[0] - b[0], a[1] - b[1], a[2] - b[2])


def scale(a: Vec, s: float) -> Vec:
    return (a[0] * s, a[1] * s, a[2] * s)


def sort_axes(axes: Sequence[Tuple[float, Vec]]) -> Tuple[Vec, Vec, Vec, Tuple[float, float, float]]:
    """``axes`` is [(extent, direction)] for the oriented bounding box. Returns
    (x_dir, y_dir, z_dir, (L, W, T)) with L >= W >= T and a right-handed frame
    (z = x × y), so the thinnest extent is always along z."""
    ordered = sorted(axes, key=lambda e: -e[0])
    (L, xd), (W, yd), (T, _) = ordered
    x = unit(xd)
    # Make y exactly perpendicular to x (guards against slightly skewed input).
    y = unit(sub(unit(yd), scale(x, dot(unit(yd), x))))
    z = unit(cross(x, y))
    return x, y, z, (float(L), float(W), float(T))


def canonical_rows(center: Vec, x: Vec, y: Vec, z: Vec) -> List[float]:
    """Row-major 4x4 matrix (16 floats, translation in the last column) that
    maps world points into the canonical frame: p' = R (p - c), where the rows
    of R are the x, y, z directions. Suitable for Matrix3D.setWithArray."""
    return [
        x[0], x[1], x[2], -dot(x, center),
        y[0], y[1], y[2], -dot(y, center),
        z[0], z[1], z[2], -dot(z, center),
        0.0, 0.0, 0.0, 1.0,
    ]


def apply_rows(rows: Sequence[float], p: Vec) -> Vec:
    return (
        rows[0] * p[0] + rows[1] * p[1] + rows[2] * p[2] + rows[3],
        rows[4] * p[0] + rows[5] * p[1] + rows[6] * p[2] + rows[7],
        rows[8] * p[0] + rows[9] * p[1] + rows[10] * p[2] + rows[11],
    )


# ---------------------------------------------------------------- face data (pure)

PLANE, CYLINDER, CONE, SPHERE, TORUS, ELLIPTICAL_CYLINDER, ELLIPTICAL_CONE, NURBS = (
    'plane', 'cylinder', 'cone', 'sphere', 'torus', 'elliptical_cylinder', 'elliptical_cone', 'nurbs')


@dataclass
class FaceInfo:
    """One face of the body in the canonical frame, in inches."""
    index: int
    surface_type: str
    area_in2: float
    bbox_min: Vec
    bbox_max: Vec
    normal: Vec                                  # outward normal at pointOnFace
    point: Vec                                   # pointOnFace
    radius_in: Optional[float] = None            # cylinder / sphere radius, torus minor radius
    axis: Optional[Vec] = None                   # cylinder / cone / torus axis
    axis_origin: Optional[Vec] = None
    is_internal: Optional[bool] = None           # cylinder: normal points at the axis (a bore)
    masked: bool = False

    @property
    def center(self) -> Vec:
        return tuple((a + b) / 2.0 for a, b in zip(self.bbox_min, self.bbox_max))  # type: ignore

    @property
    def extent(self) -> Vec:
        return tuple(b - a for a, b in zip(self.bbox_min, self.bbox_max))  # type: ignore


@dataclass
class MaskGroupInfo:
    label: str
    description: str
    sub: str
    area_in2: float
    count: int
    faces: List[FaceInfo]
    anchor_in: Optional[Vec] = None
    view: str = 'none'


@dataclass
class PartGeometry:
    envelope_in: Tuple[float, float, float]
    surface_area_total_in2: float
    masked_area_in2: float
    faces: List[FaceInfo]
    groups: List[MaskGroupInfo]
    view_from_plus_z: bool                       # final plan-view direction (after the user's flip)
    auto_from_plus_z: bool                       # what the heuristic chose
    canonical_rows: List[float]                  # world(cm) -> canonical(cm), row-major
    extents_cm: Tuple[float, float, float]
    canonical_matrix: object = None              # the same transform as an adsk Matrix3D
    tmp_body: object = None                      # transformed temporary copy (adsk BRepBody)
    tmp_mask_bodies: List[object] = field(default_factory=list)   # transformed single-face copies
    masked_indices: List[int] = field(default_factory=list)


def plan_side_scores(faces: Sequence[FaceInfo]) -> Tuple[float, float]:
    """Feature score of the +Z and -Z sides: 1 per face whose normal has
    dot(n, side) > 0.1, plus 3 more for each masked face facing that side."""
    plus = minus = 0.0
    for f in faces:
        w = 4.0 if f.masked else 1.0
        if f.normal[2] > 0.1:
            plus += w
        elif f.normal[2] < -0.1:
            minus += w
    return plus, minus


def auto_view_from_plus_z(faces: Sequence[FaceInfo]) -> bool:
    plus, minus = plan_side_scores(faces)
    return plus >= minus


def _fmt_area(a: float) -> str:
    return f'{a:.2f} sq in'


def _through(face: FaceInfo, envelope: Tuple[float, float, float]) -> bool:
    """A cylinder whose length along its (canonical-axis-aligned) axis spans
    the whole part in that direction is a through feature."""
    if face.axis is None:
        return False
    ax = face.axis
    for k in range(3):
        if abs(ax[k]) > 0.95:
            return face.extent[k] >= 0.98 * envelope[k] - 1e-6
    return False


def _plane_class(normal: Vec) -> str:
    if normal[2] > 0.7:
        return 'top'
    if normal[2] < -0.7:
        return 'bottom'
    return 'side'


def _group_key(f: FaceInfo, envelope: Tuple[float, float, float]) -> tuple:
    if f.surface_type == CYLINDER and f.radius_in is not None:
        return (CYLINDER, round(f.radius_in * 2, 3), bool(f.is_internal), _through(f, envelope))
    if f.surface_type == PLANE:
        return (PLANE, _plane_class(f.normal), round(f.area_in2, 2))
    return (f.surface_type, round(f.area_in2, 2))


def describe_group(key: tuple, faces: List[FaceInfo]) -> Tuple[str, str]:
    """(description, sub-line) for a group of identical masked faces."""
    n = len(faces)
    total = sum(f.area_in2 for f in faces)
    prefix = f'{n}× ' if n > 1 else ''
    kind = key[0]
    if kind == CYLINDER:
        d, internal, through = key[1], key[2], key[3]
        desc = f'{prefix}Ø{d:.3f} {"bore" if internal else "boss"}'
        if through:
            desc += ', through'
        sub = f'Cylindrical, {_fmt_area(total)} total' if n > 1 else f'Cylindrical, {_fmt_area(total)}'
        return desc, sub
    if kind == PLANE:
        where = key[1]
        each = ' each' if n > 1 else ''
        desc = f'{prefix}Planar face — {where}, {_fmt_area(faces[0].area_in2)}{each}'
        sub = f'{_fmt_area(total)} total' if n > 1 else ''
        return desc, sub
    names = {CONE: 'Conical face', TORUS: 'Fillet / toroidal face', SPHERE: 'Spherical face',
             ELLIPTICAL_CYLINDER: 'Elliptical face', ELLIPTICAL_CONE: 'Elliptical conical face',
             NURBS: 'Freeform face'}
    name = names.get(kind, 'Freeform face')
    each = ' each' if n > 1 else ''
    desc = f'{prefix}{name}, {_fmt_area(faces[0].area_in2)}{each}'
    sub = f'{_fmt_area(total)} total' if n > 1 else ''
    return desc, sub


def choose_view(face: FaceInfo, from_plus_z: bool) -> str:
    """Which view gets the balloon: 'plan' if the face points at the plan
    camera or is a cylinder seen end-on there; else 'side' by the same test
    (camera on -Y); else 'none'."""
    plan_dir = (0.0, 0.0, 1.0 if from_plus_z else -1.0)
    side_dir = (0.0, -1.0, 0.0)
    for name, d in (('plan', plan_dir), ('side', side_dir)):
        if dot(face.normal, d) > 0.1:
            return name
        if face.surface_type == CYLINDER and face.axis is not None and abs(dot(face.axis, d)) > 0.95:
            return name
    return 'none'


def build_groups(faces: Sequence[FaceInfo], envelope: Tuple[float, float, float],
                 from_plus_z: bool) -> List[MaskGroupInfo]:
    """Group the masked faces, describe them, pick a representative face per
    group for the balloon anchor and decide which view shows it."""
    buckets: Dict[tuple, List[FaceInfo]] = {}
    order: List[tuple] = []
    for f in faces:
        if not f.masked:
            continue
        k = _group_key(f, envelope)
        if k not in buckets:
            buckets[k] = []
            order.append(k)
        buckets[k].append(f)
    groups = []
    # Largest total area first, so M1 is the most significant masking.
    for k in sorted(order, key=lambda key: -sum(f.area_in2 for f in buckets[key])):
        members = buckets[k]
        desc, sub = describe_group(k, members)
        # Representative: prefer a member visible in the plan view, then side.
        rep = None
        view = 'none'
        for want in ('plan', 'side'):
            for f in members:
                if choose_view(f, from_plus_z) == want:
                    rep, view = f, want
                    break
            if rep is not None:
                break
        if rep is None:
            rep = members[0]
        # Anchor: the bounding-box centre for axis-symmetric features (a bore
        # seen end-on gets its leader at the hole centre), but a point that is
        # guaranteed to lie on the face for planar and freeform faces, so an
        # annular or holed face is never anchored in its own hole.
        anchor = rep.center if rep.surface_type in (CYLINDER, CONE, TORUS, SPHERE) else rep.point
        groups.append(MaskGroupInfo('', desc, sub, sum(f.area_in2 for f in members), len(members), members,
                                    anchor, view))
    for i, g in enumerate(groups, start=1):
        g.label = f'M{i}'
    return groups


# ---------------------------------------------------------------- Fusion side

def _require_adsk() -> None:
    if adsk is None:
        raise RuntimeError('The Fusion API (adsk) is not available outside Fusion.')


def native_of(entity):
    try:
        n = entity.nativeObject
        return n if n is not None else entity
    except Exception:
        return entity


def _v(vec) -> Vec:
    return (float(vec.x), float(vec.y), float(vec.z))


def surface_type_name(face) -> str:
    st = adsk.core.SurfaceTypes
    t = face.geometry.surfaceType
    return {
        st.PlaneSurfaceType: PLANE, st.CylinderSurfaceType: CYLINDER, st.ConeSurfaceType: CONE,
        st.SphereSurfaceType: SPHERE, st.TorusSurfaceType: TORUS,
        st.EllipticalCylinderSurfaceType: ELLIPTICAL_CYLINDER, st.EllipticalConeSurfaceType: ELLIPTICAL_CONE,
        st.NurbsSurfaceType: NURBS,
    }.get(t, NURBS)


def surface_area_in2(native_body) -> float:
    _require_adsk()
    return cm2_to_in2(float(native_body.area))


def envelope_in(native_body) -> Tuple[float, float, float]:
    """Sorted oriented-bounding-box extents in inches (L >= W >= T)."""
    _require_adsk()
    obb = native_body.orientedMinimumBoundingBox
    ext = sorted((float(obb.length), float(obb.width), float(obb.height)), reverse=True)
    return tuple(cm_to_in(e) for e in ext)  # type: ignore


def canonical_matrix(native_body):
    """(Matrix3D world->canonical, rows, extents_cm) from the body's oriented
    minimum bounding box: L along +X, W along +Y, T along +Z, centre at the
    origin, right-handed."""
    _require_adsk()
    obb = native_body.orientedMinimumBoundingBox
    axes = [(float(obb.length), _v(obb.lengthDirection)),
            (float(obb.width), _v(obb.widthDirection)),
            (float(obb.height), _v(obb.heightDirection))]
    x, y, z, extents = sort_axes(axes)
    center = _v(obb.centerPoint)
    rows = canonical_rows(center, x, y, z)
    # Build the matrix with setToAlignCoordinateSystems, which is independent
    # of the array layout: map the box frame (centre, L, W, T directions) onto
    # the world frame (origin, +X, +Y, +Z).
    P, V = adsk.core.Point3D.create, adsk.core.Vector3D.create
    m = adsk.core.Matrix3D.create()
    ok = m.setToAlignCoordinateSystems(
        P(*center), V(*x), V(*y), V(*z),
        P(0.0, 0.0, 0.0), V(1.0, 0.0, 0.0), V(0.0, 1.0, 0.0), V(0.0, 0.0, 1.0))
    if not ok:
        raise RuntimeError('Matrix3D.setToAlignCoordinateSystems failed')
    return m, rows, extents


def face_info(face, index: int, masked: bool = False) -> FaceInfo:
    """FaceInfo (inches) for a face of the *canonical* temporary body."""
    bb = face.boundingBox
    ok, n = face.evaluator.getNormalAtPoint(face.pointOnFace)
    normal = unit(_v(n)) if ok else (0.0, 0.0, 0.0)
    kind = surface_type_name(face)
    info = FaceInfo(
        index=index, surface_type=kind, area_in2=cm2_to_in2(float(face.area)),
        bbox_min=tuple(cm_to_in(c) for c in _v(bb.minPoint)),  # type: ignore
        bbox_max=tuple(cm_to_in(c) for c in _v(bb.maxPoint)),  # type: ignore
        normal=normal, point=tuple(cm_to_in(c) for c in _v(face.pointOnFace)),  # type: ignore
        masked=masked)
    geom = face.geometry
    if kind == CYLINDER:
        cyl = adsk.core.Cylinder.cast(geom)
        if cyl is not None:
            info.radius_in = cm_to_in(float(cyl.radius))
            info.axis = unit(_v(cyl.axis))
            info.axis_origin = tuple(cm_to_in(c) for c in _v(cyl.origin))  # type: ignore
            # Vector from the axis to the point on the face; a bore's outward
            # normal points back toward the axis.
            rel = sub(info.point, info.axis_origin)
            radial = sub(rel, scale(info.axis, dot(rel, info.axis)))
            info.is_internal = dot(normal, radial) < 0
    elif kind == CONE:
        cone = adsk.core.Cone.cast(geom)
        if cone is not None:
            info.axis = unit(_v(cone.axis))
            info.axis_origin = tuple(cm_to_in(c) for c in _v(cone.origin))  # type: ignore
    elif kind == TORUS:
        tor = adsk.core.Torus.cast(geom)
        if tor is not None:
            info.axis = unit(_v(tor.axis))
            info.radius_in = cm_to_in(float(tor.minorRadius))
    elif kind == SPHERE:
        sph = adsk.core.Sphere.cast(geom)
        if sph is not None:
            info.radius_in = cm_to_in(float(sph.radius))
    return info


def _bbox_cm(face) -> Tuple[Vec, Vec]:
    bb = face.boundingBox
    return _v(bb.minPoint), _v(bb.maxPoint)


def match_faces(body, single_face_bodies, bbox_tol_cm: float = 1e-4, area_rel_tol: float = 1e-6) -> List[int]:
    """Indices of ``body.faces`` matching each single-face temporary body
    (same surface type, area within 1e-6 relative, bounding box within
    1e-4 cm). Unmatched copies yield -1."""
    _require_adsk()
    keys = []
    for i, f in enumerate(body.faces):
        keys.append((f.geometry.surfaceType, float(f.area), _bbox_cm(f)))
    out = []
    for fb in single_face_bodies:
        if fb is None or fb.faces.count == 0:
            out.append(-1)
            continue
        f0 = fb.faces.item(0)
        st, area, (mn, mx) = f0.geometry.surfaceType, float(f0.area), _bbox_cm(f0)
        found = -1
        for i, (kst, karea, (kmn, kmx)) in enumerate(keys):
            if kst != st:
                continue
            if abs(karea - area) > area_rel_tol * max(area, 1e-9):
                continue
            if all(abs(a - b) <= bbox_tol_cm for a, b in zip(kmn, mn)) and \
                    all(abs(a - b) <= bbox_tol_cm for a, b in zip(kmx, mx)):
                found = i
                break
        out.append(found)
    return out


def compute_part_geometry(native_body, native_masked_faces, flip_view: bool = False) -> PartGeometry:
    """Everything the sheet and the renderer need, from the native body.

    Makes a temporary copy of the body and of every masked face, moves them
    into the canonical frame and reads all face data from the copies. The
    copies are returned in ``tmp_body`` / ``tmp_mask_bodies`` for the render
    step. Nothing in the user's design is modified."""
    _require_adsk()
    tbm = adsk.fusion.TemporaryBRepManager.get()
    m, rows, extents_cm = canonical_matrix(native_body)
    tmp_body = tbm.copy(native_body)
    if tmp_body is None or not tbm.transform(tmp_body, m):
        raise RuntimeError('Could not copy or transform the body.')
    mask_bodies = []
    for f in native_masked_faces:
        fb = tbm.copy(f)
        if fb is not None and tbm.transform(fb, m):
            mask_bodies.append(fb)
    matched = match_faces(tmp_body, mask_bodies)
    masked_idx = sorted({i for i in matched if i >= 0})
    masked_set = set(masked_idx)
    faces = [face_info(f, i, i in masked_set) for i, f in enumerate(tmp_body.faces)]
    envelope = tuple(cm_to_in(e) for e in extents_cm)
    auto_plus = auto_view_from_plus_z(faces)
    from_plus = (not auto_plus) if flip_view else auto_plus
    groups = build_groups(faces, envelope, from_plus)  # type: ignore[arg-type]
    total = cm2_to_in2(float(native_body.area))
    masked_area = sum(f.area_in2 for f in faces if f.masked)
    return PartGeometry(
        envelope_in=envelope, surface_area_total_in2=total, masked_area_in2=masked_area,  # type: ignore[arg-type]
        faces=faces, groups=groups, view_from_plus_z=from_plus, auto_from_plus_z=auto_plus,
        canonical_rows=rows, canonical_matrix=m, extents_cm=extents_cm, tmp_body=tmp_body,
        tmp_mask_bodies=mask_bodies,
        masked_indices=masked_idx)
