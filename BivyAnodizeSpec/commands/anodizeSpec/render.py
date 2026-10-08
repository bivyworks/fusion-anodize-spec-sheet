"""Render the plan and side views of the body as transparent images.

Primary approach (``config.RENDER_APPROACH == 'throwaway'``): the canonical
temporary copy of the body is placed in a brand-new, never-saved design
document, given neutral gray appearances (dark gray on the masked faces),
captured twice with an orthographic camera and the document is closed without
saving. Fallback (``'inplace'``): everything but the body is hidden in the
user's document, masked faces are highlighted with custom graphics, and the
captures are taken there.

Both paths run inside ``try/finally`` blocks: the throwaway document is always
closed, the original document re-activated and its camera, visual style and
visibility restored, even when a capture fails.

This module must only be called from a custom event handler, never from a
command event: ``Documents.add`` and ``Document.close`` are not allowed while
a command transaction is open.
"""

from __future__ import annotations

import math
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Dict, List, Optional, Tuple

from . import geometry, persistence, raster
from .spec import config

try:
    import adsk.core  # type: ignore
    import adsk.fusion  # type: ignore
except ImportError:  # pragma: no cover
    adsk = None  # type: ignore

# Locale-independent IDs from the official samples (see research notes):
# the Fusion Appearance Library and its "generic" appearance, and the ID of
# the "Color" property.
FUSION_APPEARANCE_LIB_ID = 'BA5EE55E-9982-449B-9D66-9F036540E140'
GENERIC_APPEARANCE_ID = 'Prism-129'
COLOR_PROPERTY_ID = 'opaque_albedo'

Progress = Optional[Callable[[str, int], None]]   # (message, percent)


class RenderError(RuntimeError):
    pass


@dataclass
class CaptureInfo:
    path: Path
    width_px: int
    height_px: int
    extents_cm: Optional[Tuple[float, float]] = None     # camera (width, height) after the fit, if available
    seconds: float = 0.0


@dataclass
class RenderResult:
    plan: CaptureInfo
    side: CaptureInfo
    capture_format: str
    approach: str
    timings: Dict[str, float] = field(default_factory=dict)
    notes: List[str] = field(default_factory=list)
    iso: Optional[CaptureInfo] = None


# ---------------------------------------------------------------- helpers

def _app():
    if adsk is None:
        raise RenderError('The Fusion API (adsk) is not available outside Fusion.')
    return adsk.core.Application.get()


def _progress(cb: Progress, message: str, pct: int) -> None:
    if cb is not None:
        try:
            cb(message, pct)
        except Exception:
            pass
    if adsk is not None:
        adsk.doEvents()


def ensure_no_active_command(ui) -> None:
    """Threading_UM pattern: a custom event handler must not do document work
    while a command is still running; switching to the Select command ends it."""
    try:
        if ui.activeCommand != 'SelectCommand':
            sel = ui.commandDefinitions.itemById('SelectCommand')
            if sel is not None:
                sel.execute()
    except Exception:
        pass


def find_appearance_library(app):
    libs = app.materialLibraries
    lib = libs.itemById(FUSION_APPEARANCE_LIB_ID)
    if lib is not None:
        return lib
    for hint in config.APPEARANCE_LIBRARY_HINTS:
        for i in range(libs.count):
            cand = libs.item(i)
            if hint.lower() in (cand.name or '').lower() and cand.appearances.count > 0:
                return cand
    for i in range(libs.count):
        cand = libs.item(i)
        if cand.appearances.count > 0:
            return cand
    return None


def _color_property(appearance):
    props = appearance.appearanceProperties
    prop = props.itemById(COLOR_PROPERTY_ID)
    if prop is None:
        for hint in config.COLOR_PROPERTY_HINTS:
            prop = props.itemByName(hint)
            if prop is not None:
                break
    if prop is None:
        for i in range(props.count):
            cand = props.item(i)
            if adsk.core.ColorProperty.cast(cand) is not None:
                prop = cand
                break
    return adsk.core.ColorProperty.cast(prop) if prop is not None else None


def _albedo_properties(appearance):
    """Every editable colour property that defines the surface colour (the
    Prism appearances have both 'opaque_albedo' and 'surface_albedo'; the
    spike showed that setting only the first leaves a warm tint)."""
    props = appearance.appearanceProperties
    out = []
    for i in range(props.count):
        cand = adsk.core.ColorProperty.cast(props.item(i))
        if cand is None or cand.isReadOnly:
            continue
        pid = (cand.id or '').lower()
        if pid.endswith('albedo') or pid == COLOR_PROPERTY_ID:
            out.append(cand)
    return out


def find_base_appearance(lib):
    """A plain appearance to copy: the samples' generic 'Prism-129', else the
    first library appearance whose name matches a matte/plastic/paint hint,
    else any appearance with an editable colour property."""
    apps = lib.appearances
    base = apps.itemById(GENERIC_APPEARANCE_ID)
    if base is not None and _color_property(base) is not None:
        return base
    for hint in config.APPEARANCE_NAME_HINTS:
        for i in range(apps.count):
            cand = apps.item(i)
            if hint.lower() in (cand.name or '').lower() and not cand.hasTexture and _color_property(cand) is not None:
                return cand
    for i in range(apps.count):
        cand = apps.item(i)
        if not cand.hasTexture and _color_property(cand) is not None:
            return cand
    return None


def make_gray_appearance(design, name: str, gray: int, notes: Optional[List[str]] = None):
    """Copy a plain library appearance into ``design`` and set its colour to
    an equal-RGB gray. Returns None (and notes why) when that is impossible."""
    app = _app()
    existing = design.appearances.itemByName(name)
    if existing is not None:
        return existing
    lib = find_appearance_library(app)
    if lib is None:
        if notes is not None:
            notes.append('No appearance library found; rendering with default appearances.')
        return None
    base = find_base_appearance(lib)
    if base is None:
        if notes is not None:
            notes.append(f'No usable base appearance in "{lib.name}".')
        return None
    appearance = design.appearances.addByCopy(base, name)
    if appearance is None:
        if notes is not None:
            notes.append('Appearances.addByCopy failed.')
        return None
    targets = _albedo_properties(appearance)
    prop = _color_property(appearance)
    if prop is not None and not prop.isReadOnly and all(t.id != prop.id for t in targets):
        targets.append(prop)
    if not targets:
        if notes is not None:
            notes.append(f'Colour property not editable on "{base.name}" (id {base.id}).')
        return appearance
    g = max(0, min(255, int(gray)))
    for t in targets:
        try:
            if t.hasConnectedTexture:
                t.hasConnectedTexture = False
        except Exception:
            pass
        t.value = adsk.core.Color.create(g, g, g, 255)
    if notes is not None:
        notes.append(f'Appearance "{name}" copied from "{base.name}" ({base.id}); colour set on '
                     + ', '.join(t.id for t in targets) + '.')
    return appearance


def hide_component_folders(component) -> dict:
    """Hide origin, sketches and construction in a component (the folder
    light bulbs and the origin entities themselves); return the old state so
    it can be restored."""
    names = ('isOriginFolderLightBulbOn', 'isSketchFolderLightBulbOn', 'isConstructionFolderLightBulbOn',
             'isJointsFolderLightBulbOn', 'isCanvasFolderLightBulbOn', 'isDecalFolderLightBulbOn')
    old = {}
    for n in names:
        try:
            old[n] = getattr(component, n)
            setattr(component, n, False)
        except Exception:
            pass
    for n in ('originConstructionPoint', 'xConstructionAxis', 'yConstructionAxis', 'zConstructionAxis',
              'xYConstructionPlane', 'xZConstructionPlane', 'yZConstructionPlane'):
        try:
            ent = getattr(component, n)
            old[n] = ent.isLightBulbOn
            ent.isLightBulbOn = False
        except Exception:
            pass
    return old


def restore_component_folders(component, old: dict) -> None:
    for n, v in old.items():
        try:
            if n.startswith('is'):
                setattr(component, n, v)
            else:
                getattr(component, n).isLightBulbOn = v
        except Exception:
            pass


class _PivotMarkerOff:
    """Temporarily disable the camera pivot preference (Fusion draws a green
    pivot marker at the camera target, which ends up in captures)."""

    def __init__(self, app):
        self.prefs = None
        self.old = None
        try:
            self.prefs = app.preferences.generalPreferences
            self.old = self.prefs.isCameraPivotEnabled
            if self.old:
                self.prefs.isCameraPivotEnabled = False
        except Exception:
            self.prefs = None

    def restore(self) -> None:
        if self.prefs is not None and self.old is not None:
            try:
                self.prefs.isCameraPivotEnabled = bool(self.old)
            except Exception:
                pass


def set_orthographic_camera(viewport, eye, target, up) -> Optional[Tuple[float, float]]:
    """Point the viewport's camera (a copy that must be assigned back) at
    ``target`` from ``eye`` with ``up``, orthographic, fitted to the visible
    graphics, no animation. Returns the fitted camera extents (cm) if the API
    provides them."""
    cam = viewport.camera
    cam.cameraType = adsk.core.CameraTypes.OrthographicCameraType
    cam.eye = adsk.core.Point3D.create(*eye)
    cam.target = adsk.core.Point3D.create(*target)
    cam.upVector = adsk.core.Vector3D.create(*up)
    cam.isFitView = True
    cam.isSmoothTransition = False
    viewport.camera = cam
    adsk.doEvents()
    viewport.refresh()
    adsk.doEvents()
    try:
        ok, w, h = viewport.camera.getExtents()
        return (float(w), float(h)) if ok else None
    except Exception:
        return None


def capture(viewport, path: Path, width: int, height: int, transparent: bool = True,
            antialias: bool = True) -> CaptureInfo:
    t0 = time.perf_counter()
    opts = adsk.core.SaveImageFileOptions.create(str(path))
    opts.width = int(width)
    opts.height = int(height)
    opts.isBackgroundTransparent = bool(transparent)
    opts.isAntiAliased = bool(antialias)
    adsk.doEvents()
    viewport.refresh()
    ok = viewport.saveAsImageFileWithOptions(opts)
    dt = time.perf_counter() - t0
    if not ok or not path.exists() or path.stat().st_size == 0:
        raise RenderError(f'Viewport.saveAsImageFileWithOptions failed for {path.name}')
    return CaptureInfo(path, width, height, None, dt)


def plan_capture_size(vp_w: int, vp_h: int, part_aspect: float, render_px: int, max_px: int,
                      margin: float) -> Tuple[int, int]:
    """Capture size (w, h) that keeps the viewport's aspect ratio (so the
    fitted framing is reproduced exactly) while giving the part about
    ``render_px`` pixels across. When the part is less wide than the viewport
    the fit is governed by height and the part only spans
    part_aspect / vp_aspect of the width, so the capture is widened by the
    inverse of that, capped at ``max_px``."""
    vp_w, vp_h = max(1, int(vp_w)), max(1, int(vp_h))
    vp_aspect = vp_w / vp_h
    part_aspect = max(1e-6, float(part_aspect))
    width = render_px * margin
    if part_aspect < vp_aspect:
        width *= vp_aspect / part_aspect
    width = int(round(min(max(width, render_px), max_px)))
    height = max(16, int(round(width / vp_aspect)))
    return width, height


def capture_size(viewport, width_px: int, part_aspect: Optional[float] = None) -> Tuple[int, int]:
    """Capture size for this viewport; see ``plan_capture_size``."""
    vw, vh = max(1, int(viewport.width)), max(1, int(viewport.height))
    if part_aspect is None:
        return int(width_px), max(16, int(round(width_px * vh / vw)))
    return plan_capture_size(vw, vh, part_aspect, width_px, config.MAX_CAPTURE_PX, config.CAPTURE_FIT_MARGIN)


class _ViewportState:
    """Snapshot of the viewport state that is restored in ``finally``."""

    def __init__(self, viewport):
        self.viewport = viewport
        self.camera = viewport.camera          # a copy
        self.visual_style = viewport.visualStyle

    def restore(self, viewport=None) -> None:
        vp = viewport if viewport is not None else self.viewport
        try:
            vp.visualStyle = self.visual_style
        except Exception:
            pass
        try:
            cam = self.camera
            cam.isFitView = False
            cam.isSmoothTransition = False
            vp.camera = cam
            vp.refresh()
        except Exception:
            pass


def _cross(a, b):
    return (a[1] * b[2] - a[2] * b[1], a[2] * b[0] - a[0] * b[2], a[0] * b[1] - a[1] * b[0])


def _unit(v):
    n = math.sqrt(v[0] * v[0] + v[1] * v[1] + v[2] * v[2]) or 1.0
    return (v[0] / n, v[1] / n, v[2] / n)


def iso_camera(L: float, W: float, T: float, from_plus_z: bool = True,
               direction: Tuple[float, float, float] = None):
    """The isometric context view in the canonical frame.

    Returns (eye_dir, up, proj_w, proj_h): the unit direction from the part
    centre to the eye, the camera up vector (the thickness axis, following
    the plan view's side), and the projected width and height of the
    L × W × T box in the same units as the extents. Pure math, no adsk."""
    dx, dy, dz = direction or config.ISO_DIRECTION
    pz = 1.0 if from_plus_z else -1.0
    eye_dir = _unit((dx, dy, dz * pz))
    up0 = (0.0, 0.0, pz)
    forward = (-eye_dir[0], -eye_dir[1], -eye_dir[2])
    right = _unit(_cross(forward, up0))
    up = _cross(right, forward)
    ws, hs = [], []
    for sx in (-0.5, 0.5):
        for sy in (-0.5, 0.5):
            for sz in (-0.5, 0.5):
                p = (sx * L, sy * W, sz * T)
                ws.append(p[0] * right[0] + p[1] * right[1] + p[2] * right[2])
                hs.append(p[0] * up[0] + p[1] * up[1] + p[2] * up[2])
    return eye_dir, up0, max(ws) - min(ws), max(hs) - min(hs)


def _views(geom: geometry.PartGeometry, center: Tuple[float, float, float]):
    """Camera placements (eye, target, up) for the plan, side and isometric
    views around ``center`` (cm)."""
    L, W, T = geom.extents_cm
    dist = 3.0 * max(L, W, T, 1.0)
    cx, cy, cz = center
    plan_dir = 1.0 if geom.view_from_plus_z else -1.0
    plan = ((cx, cy, cz + plan_dir * dist), (cx, cy, cz), (0.0, 1.0, 0.0))
    side = ((cx, cy - dist, cz), (cx, cy, cz), (0.0, 0.0, 1.0))
    d, up, _, _ = iso_camera(L, W, T, geom.view_from_plus_z)
    iso = ((cx + d[0] * dist, cy + d[1] * dist, cz + d[2] * dist), (cx, cy, cz), up)
    return plan, side, iso


def _capture_iso(vp, geom: geometry.PartGeometry, iso_cam, out_dir: Path, capture_format: str,
                 progress: Progress, timings: Dict[str, float], notes: List[str]) -> Optional[CaptureInfo]:
    """The isometric context view, or None when disabled or when its capture
    fails (the sheet is still produced without it)."""
    if not config.ISO_VIEW:
        return None
    L, W, T = geom.extents_cm
    _, _, pw_, ph_ = iso_camera(L, W, T, geom.view_from_plus_z)
    size = capture_size(vp, config.RENDER_PX, pw_ / max(ph_, 1e-6))
    _progress(progress, 'Capturing the isometric view…', 66)
    try:
        ext = set_orthographic_camera(vp, *iso_cam)
        iso = capture(vp, out_dir / f'iso.{capture_format}', *size)
        iso.extents_cm = ext
        timings['capture_iso_s'] = iso.seconds
        return iso
    except Exception as e:      # the two orthographic views are the deliverable
        notes.append(f'isometric view skipped: {e}')
        return None


# ---------------------------------------------------------------- throwaway document

def render_throwaway(geom: geometry.PartGeometry, out_dir: Path, capture_format: str,
                     progress: Progress = None, grid_dodge_axis: str = '') -> RenderResult:
    app = _app()
    ui = app.userInterface
    ensure_no_active_command(ui)
    timings: Dict[str, float] = {}
    notes: List[str] = []
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    orig_doc = app.activeDocument
    orig_state = _ViewportState(app.activeViewport) if app.activeViewport is not None else None
    pivot = None
    doc = None
    try:
        # Everything that changes the user's Fusion state happens inside this
        # try, so the finally restores it on every exit path (a failed
        # Documents.add included).
        pivot = _PivotMarkerOff(app)
        t0 = time.perf_counter()
        doc = app.documents.add(adsk.core.DocumentTypes.FusionDesignDocumentType)
        if doc is None:
            raise RenderError('Could not create the throwaway document.')
        timings['create_doc_s'] = time.perf_counter() - t0
        design = adsk.fusion.Design.cast(doc.products.itemByProductType('DesignProductType'))
        if design is None:
            raise RenderError('The throwaway document has no design product.')
        design.designType = adsk.fusion.DesignTypes.DirectDesignType
        root = design.rootComponent
        hide_component_folders(root)

        # Optional grid dodge: move the copy away from the origin so the layout
        # grid (if it shows up in captures) falls outside the fitted frame.
        center = (0.0, 0.0, 0.0)
        tmp_body = geom.tmp_body
        mask_bodies = list(geom.tmp_mask_bodies)
        axis = (grid_dodge_axis or '').lower()
        if axis in ('x', 'y', 'z') and config.GRID_DODGE_FACTOR > 0:
            shift = config.GRID_DODGE_FACTOR * max(geom.extents_cm)
            vec = {'x': (shift, 0.0, 0.0), 'y': (0.0, shift, 0.0), 'z': (0.0, 0.0, shift)}[axis]
            m = adsk.core.Matrix3D.create()
            m.translation = adsk.core.Vector3D.create(*vec)
            tbm = adsk.fusion.TemporaryBRepManager.get()
            tmp_body = tbm.copy(tmp_body)
            tbm.transform(tmp_body, m)
            moved = []
            for fb in mask_bodies:
                c = tbm.copy(fb)
                tbm.transform(c, m)
                moved.append(c)
            mask_bodies = moved
            center = vec
            notes.append(f'Grid dodge: body moved {shift:.1f} cm along {axis}.')

        _progress(progress, 'Placing the body in a scratch document…', 30)
        t1 = time.perf_counter()
        body = root.bRepBodies.add(tmp_body)
        if body is None:
            raise RenderError('BRepBodies.add failed in the throwaway document.')
        try:
            body.name = 'AnodizeRender'
        except Exception:
            pass
        timings['add_body_s'] = time.perf_counter() - t1

        # Appearances: light gray body, dark gray masked faces.
        t2 = time.perf_counter()
        light = make_gray_appearance(design, 'BivyAnodize Part Gray', config.PART_GRAY, notes)
        dark = make_gray_appearance(design, 'BivyAnodize Mask Gray', config.MASK_GRAY, notes)
        if light is not None:
            body.appearance = light
        matched = geometry.match_faces(body, mask_bodies)
        n_masked = 0
        if dark is not None:
            for idx in matched:
                if idx >= 0:
                    body.faces.item(idx).appearance = dark
                    n_masked += 1
        if any(i < 0 for i in matched):
            notes.append(f'{sum(1 for i in matched if i < 0)} masked face(s) could not be matched on the render copy.')
        timings['appearances_s'] = time.perf_counter() - t2
        timings['masked_faces_rendered'] = n_masked

        vp = app.activeViewport
        if vp is None:
            raise RenderError('No active viewport for the throwaway document.')
        vp.visualStyle = adsk.core.VisualStyles.ShadedWithVisibleEdgesOnlyVisualStyle
        L, W, T = geom.extents_cm
        plan_size = capture_size(vp, config.RENDER_PX, L / max(W, 1e-6))
        side_size = capture_size(vp, config.RENDER_PX, L / max(T, 1e-6))
        notes.append(f'capture sizes: plan {plan_size[0]}×{plan_size[1]}, side {side_size[0]}×{side_size[1]} '
                     f'(viewport {vp.width}×{vp.height})')
        plan_cam, side_cam, iso_cam = _views(geom, center)

        _progress(progress, 'Capturing the plan view…', 45)
        ext = set_orthographic_camera(vp, *plan_cam)
        plan = capture(vp, out_dir / f'plan.{capture_format}', *plan_size)
        plan.extents_cm = ext
        timings['capture_plan_s'] = plan.seconds

        _progress(progress, 'Capturing the side view…', 60)
        ext = set_orthographic_camera(vp, *side_cam)
        side = capture(vp, out_dir / f'side.{capture_format}', *side_size)
        side.extents_cm = ext
        timings['capture_side_s'] = side.seconds

        iso = _capture_iso(vp, geom, iso_cam, out_dir, capture_format, progress, timings, notes)
        return RenderResult(plan, side, capture_format, 'throwaway', timings, notes, iso=iso)
    finally:
        t3 = time.perf_counter()
        if doc is not None:
            try:
                doc.close(False)
            except Exception:
                notes.append('Closing the throwaway document raised an exception.')
        try:
            if orig_doc is not None and orig_doc.isValid:
                orig_doc.activate()
        except Exception:
            pass
        if orig_state is not None:
            orig_state.restore(app.activeViewport)
        if pivot is not None:
            pivot.restore()
        timings['cleanup_s'] = time.perf_counter() - t3


# ---------------------------------------------------------------- in-place fallback

class _VisibilityState:
    """Light-bulb state of everything in a design, for the in-place fallback."""

    def __init__(self, design):
        self.items: List[Tuple[object, str, object]] = []
        root = design.rootComponent
        comps = [root] + [design.allComponents.item(i) for i in range(design.allComponents.count)]
        seen = set()
        for comp in comps:
            try:
                key = comp.id
            except Exception:
                key = id(comp)
            if key in seen:
                continue
            seen.add(key)
            for name in ('isOriginFolderLightBulbOn', 'isSketchFolderLightBulbOn', 'isConstructionFolderLightBulbOn',
                         'isJointsFolderLightBulbOn', 'isCanvasFolderLightBulbOn', 'isDecalFolderLightBulbOn',
                         'isJointOriginsFolderLightBulbOn'):
                self._record(comp, name)
            for b in comp.bRepBodies:
                self._record(b, 'isLightBulbOn')
            try:
                for mb in comp.meshBodies:
                    self._record(mb, 'isLightBulbOn')
            except Exception:
                pass
        for occ in root.allOccurrences:
            self._record(occ, 'isLightBulbOn')

    def _record(self, obj, name: str) -> None:
        try:
            self.items.append((obj, name, getattr(obj, name)))
        except Exception:
            pass

    def hide_all(self) -> None:
        for obj, name, _ in self.items:
            try:
                setattr(obj, name, False)
            except Exception:
                pass

    def restore(self) -> None:
        for obj, name, value in reversed(self.items):
            try:
                setattr(obj, name, value)
            except Exception:
                pass


def _canonical_to_world(geom: geometry.PartGeometry, body):
    """(canonical -> native, native -> world) matrices. The second is the
    identity for a body at the root."""
    to_native = geom.canonical_matrix.copy()
    to_native.invert()
    to_world = adsk.core.Matrix3D.create()
    try:
        occ = body.assemblyContext
        if occ is not None:
            to_world = occ.transform2.copy()
    except Exception:
        pass
    return to_native, to_world


def _world_point(p, to_native, to_world):
    pt = adsk.core.Point3D.create(*p)
    pt.transformBy(to_native)
    pt.transformBy(to_world)
    return (pt.x, pt.y, pt.z)


def _world_vector(v, to_native, to_world):
    vec = adsk.core.Vector3D.create(*v)
    vec.transformBy(to_native)
    vec.transformBy(to_world)
    vec.normalize()
    return (vec.x, vec.y, vec.z)


def render_inplace(geom: geometry.PartGeometry, body, out_dir: Path, capture_format: str,
                   progress: Progress = None) -> RenderResult:
    """Fallback: capture the body in the user's own document."""
    app = _app()
    ui = app.userInterface
    ensure_no_active_command(ui)
    timings: Dict[str, float] = {}
    notes: List[str] = ['In-place render (fallback approach).']
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    design = adsk.fusion.Design.cast(app.activeProduct)
    if design is None:
        raise RenderError('The active product is not a design.')
    vp = app.activeViewport
    state = _ViewportState(vp)
    vis = _VisibilityState(design)
    pivot = _PivotMarkerOff(app)
    group = None
    try:
        vis.hide_all()
        # Show just this body (and its parents).
        native = geometry.native_of(body)
        try:
            native.isLightBulbOn = True
            vis._record(native.parentComponent, 'isBodiesFolderLightBulbOn')   # restored with the rest
            native.parentComponent.isBodiesFolderLightBulbOn = True
        except Exception:
            pass
        occ = None
        try:
            occ = body.assemblyContext
        except Exception:
            pass
        while occ is not None:
            try:
                occ.isLightBulbOn = True
                occ = occ.assemblyContext
            except Exception:
                break

        # Highlight the masked faces with custom graphics in world coordinates:
        # canonical copy -> native space -> world space (two transforms, no
        # matrix composition, so the array layout never matters).
        to_native, to_world = _canonical_to_world(geom, body)
        if geom.tmp_mask_bodies:
            tbm = adsk.fusion.TemporaryBRepManager.get()
            group = design.rootComponent.customGraphicsGroups.add()
            g = config.MASK_GRAY
            effect = adsk.fusion.CustomGraphicsSolidColorEffect.create(adsk.core.Color.create(g, g, g, 255))
            for fb in geom.tmp_mask_bodies:
                c = tbm.copy(fb)
                tbm.transform(c, to_native)
                tbm.transform(c, to_world)
                cg = group.addBRepBody(c)
                cg.color = effect
                try:
                    cg.depthPriority = 1
                except Exception:
                    pass

        # Camera directions: canonical axes expressed in world coordinates.
        xw = _world_vector((1.0, 0.0, 0.0), to_native, to_world)
        yw = _world_vector((0.0, 1.0, 0.0), to_native, to_world)
        zw = _world_vector((0.0, 0.0, 1.0), to_native, to_world)
        center = _world_point((0.0, 0.0, 0.0), to_native, to_world)
        L, W, T = geom.extents_cm
        dist = 3.0 * max(L, W, T, 1.0)
        pz = 1.0 if geom.view_from_plus_z else -1.0
        plan_eye = tuple(center[i] + pz * dist * zw[i] for i in range(3))
        side_eye = tuple(center[i] - dist * yw[i] for i in range(3))
        d, up_c, _, _ = iso_camera(L, W, T, geom.view_from_plus_z)
        iso_eye = tuple(center[i] + dist * (d[0] * xw[i] + d[1] * yw[i] + d[2] * zw[i]) for i in range(3))
        iso_up = tuple(up_c[2] * zw[i] for i in range(3))

        vp.visualStyle = adsk.core.VisualStyles.ShadedWithVisibleEdgesOnlyVisualStyle
        plan_size = capture_size(vp, config.RENDER_PX, L / max(W, 1e-6))
        side_size = capture_size(vp, config.RENDER_PX, L / max(T, 1e-6))
        _progress(progress, 'Capturing the plan view…', 45)
        ext = set_orthographic_camera(vp, plan_eye, center, yw)
        plan = capture(vp, out_dir / f'plan.{capture_format}', *plan_size)
        plan.extents_cm = ext
        _progress(progress, 'Capturing the side view…', 60)
        ext = set_orthographic_camera(vp, side_eye, center, zw)
        side = capture(vp, out_dir / f'side.{capture_format}', *side_size)
        side.extents_cm = ext
        iso = _capture_iso(vp, geom, (iso_eye, center, iso_up), out_dir, capture_format, progress, timings, notes)
        return RenderResult(plan, side, capture_format, 'inplace', timings, notes, iso=iso)
    finally:
        if group is not None:
            try:
                group.deleteMe()
            except Exception:
                pass
        vis.restore()
        state.restore()
        pivot.restore()


# ---------------------------------------------------------------- entry point

def render_views(geom: geometry.PartGeometry, body, out_dir: Path, progress: Progress = None,
                 approach: Optional[str] = None
                 ) -> Tuple[RenderResult, raster.RasterImage, raster.RasterImage, Optional[raster.RasterImage]]:
    """Render the views and decode them. Probes the capture format when
    ``config.CAPTURE_FORMAT`` is 'auto' (first run only; remembered in the
    machine-local settings). Returns the render result and the decoded,
    *uncropped* plan, side and (optional) isometric images with usable alpha."""
    approach = approach or config.RENDER_APPROACH
    settings = persistence.load_settings()
    fmt = config.CAPTURE_FORMAT
    candidates: List[str]
    if fmt == 'auto':
        remembered = settings.get('capture_format')
        order = list(config.CAPTURE_PROBE_ORDER)
        if remembered in order:
            order.remove(remembered)
            order.insert(0, remembered)
        candidates = order
    else:
        candidates = [fmt]
    errors: List[str] = []
    for ext in candidates:
        try:
            if approach == 'inplace':
                result = render_inplace(geom, body, out_dir, ext, progress)
            else:
                result = render_throwaway(geom, out_dir, ext, progress, grid_dodge_axis=config.GRID_DODGE_AXIS)
        except RenderError as e:
            # A format Fusion cannot write fails here; try the next one.
            if fmt == 'auto' and ext != candidates[-1]:
                errors.append(f'{ext}: {e}')
                continue
            raise
        _progress(progress, 'Decoding the captures…', 70)
        try:
            t0 = time.perf_counter()
            plan_img = raster.load_image(result.plan.path)
            side_img = raster.load_image(result.side.path)
            result.timings['decode_s'] = time.perf_counter() - t0
        except raster.RasterError as e:
            errors.append(f'{ext}: {e}')
            continue
        if plan_img.alpha is None:
            if fmt == 'auto' and ext != candidates[-1]:
                errors.append(f'{ext}: no usable alpha channel')
                continue
            result.notes.append(f'{ext}: no alpha channel in the capture; background keyed from the corner colour.')
            plan_img = raster.ensure_alpha(plan_img)
            side_img = raster.ensure_alpha(side_img)
        iso_img = None
        if result.iso is not None:
            try:
                iso_img = raster.load_image(result.iso.path)
                if iso_img.alpha is None:
                    iso_img = raster.ensure_alpha(iso_img)
            except raster.RasterError as e:
                result.notes.append(f'isometric view skipped: {e}')
                iso_img = None
        if fmt == 'auto' and settings.get('capture_format') != ext:
            persistence.save_settings({'capture_format': ext})
        result.notes.extend(errors)
        has_masks = bool(geom.masked_indices)
        plan_img, side_img = postprocess(plan_img, side_img, has_masks, result)
        if iso_img is not None:
            iso_img = postprocess_image('iso', iso_img, has_masks, result)
        return result, plan_img, side_img, iso_img
    raise RenderError('No capture format produced a usable image: ' + '; '.join(errors))


def postprocess_image(label: str, img: raster.RasterImage, has_masks: bool,
                      result: Optional[RenderResult] = None) -> raster.RasterImage:
    """Remove the pivot marker and normalise the tones of one capture."""
    img, cleared = raster.clear_center_marker(img, config.PIVOT_MARKER_RADIUS_FRACTION)
    if cleared and result is not None:
        result.notes.append(f'{label}: removed {cleared} pivot-marker pixels at the centre.')
    if config.NORMALIZE_TONES:
        img, info = raster.normalize_tones(img, config.PART_GRAY, config.MASK_GRAY, expect_dark=has_masks)
        if result is not None:
            result.notes.append(f'{label}: tones normalised {info}.')
    return img


def postprocess(plan_img: raster.RasterImage, side_img: raster.RasterImage, has_masks: bool,
                result: Optional[RenderResult] = None) -> Tuple[raster.RasterImage, raster.RasterImage]:
    """Remove the pivot marker and normalise the tones of both orthographic captures."""
    t0 = time.perf_counter()
    plan_img = postprocess_image('plan', plan_img, has_masks, result)
    side_img = postprocess_image('side', side_img, has_masks, result)
    if result is not None:
        result.timings['postprocess_s'] = time.perf_counter() - t0
    return plan_img, side_img
