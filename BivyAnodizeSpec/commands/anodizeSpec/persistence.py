"""Persistence for the anodize spec.

Two halves:

* **Machine-local settings** (last customer, last company, last save folder,
  the capture format that worked) in a small JSON file outside the add-in
  folder. This half has no ``adsk`` dependency and is covered by the tests.
* **Design attributes**: the spec as JSON on the native body and a ``mask``
  marker on every masked native face, under the attribute group
  ``config.ATTR_GROUP``. Nothing is written when
  ``config.PERSIST_SPEC_IN_DESIGN`` is False.
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

from .spec import AnodizeSpec, config

try:  # adsk only exists inside Fusion; the settings half must work without it.
    import adsk.core  # type: ignore
    import adsk.fusion  # type: ignore
except ImportError:  # pragma: no cover - exercised only outside Fusion
    adsk = None  # type: ignore


# ---------------------------------------------------------------- local settings

SETTINGS_FILENAME = 'anodize_spec.json'
SETTINGS_FOLDER = 'BivyWorks'


def settings_path() -> Path:
    """macOS: ~/Library/Application Support/BivyWorks/anodize_spec.json
    Windows: %APPDATA%\\BivyWorks\\anodize_spec.json
    Elsewhere (dev): $XDG_CONFIG_HOME or ~/.config/BivyWorks/anodize_spec.json"""
    if sys.platform == 'darwin':
        base = Path.home() / 'Library' / 'Application Support'
    elif sys.platform.startswith('win'):
        appdata = os.environ.get('APPDATA')
        base = Path(appdata) if appdata else Path.home() / 'AppData' / 'Roaming'
    else:
        xdg = os.environ.get('XDG_CONFIG_HOME')
        base = Path(xdg) if xdg else Path.home() / '.config'
    return base / SETTINGS_FOLDER / SETTINGS_FILENAME


def load_settings(path: Optional[Path] = None) -> Dict[str, Any]:
    """Return the saved settings dict, or {} when the file is missing/invalid."""
    p = Path(path) if path else settings_path()
    try:
        data = json.loads(p.read_text(encoding='utf-8'))
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def save_settings(values: Dict[str, Any], path: Optional[Path] = None) -> bool:
    """Merge ``values`` into the settings file. Returns False on I/O errors
    (never raises: losing a convenience value must not break the command)."""
    p = Path(path) if path else settings_path()
    data = load_settings(p)
    data.update({k: v for k, v in values.items() if v is not None})
    try:
        p.parent.mkdir(parents=True, exist_ok=True)
        tmp = p.with_suffix('.json.tmp')
        tmp.write_text(json.dumps(data, indent=2, ensure_ascii=False, sort_keys=True), encoding='utf-8')
        os.replace(tmp, p)
        return True
    except OSError:
        return False


def remember_last_used(spec: AnodizeSpec, save_dir: Optional[str] = None, path: Optional[Path] = None) -> bool:
    values: Dict[str, Any] = {
        'last_customer': spec.customer_project or '',
        'last_company': spec.company or '',
    }
    if save_dir:
        values['last_save_dir'] = str(save_dir)
    return save_settings(values, path)


# ---------------------------------------------------------------- design attributes

def _require_adsk() -> None:
    if adsk is None:
        raise RuntimeError('The Fusion API (adsk) is not available outside Fusion.')


def native(entity):
    """The native (non-proxy) object of a body or face, or the entity itself."""
    try:
        n = entity.nativeObject
        return n if n is not None else entity
    except Exception:
        return entity


def read_spec_from_body(body) -> Optional[AnodizeSpec]:
    """The spec saved on this body, or None."""
    _require_adsk()
    attr = native(body).attributes.itemByName(config.ATTR_GROUP, config.ATTR_SPEC_NAME)
    if attr is None or not attr.value:
        return None
    spec = AnodizeSpec.from_json(attr.value)
    return spec


def write_spec_to_body(body, spec: AnodizeSpec) -> bool:
    """Save the spec JSON on the native body, overwriting any previous value.
    Returns False when persistence is disabled."""
    _require_adsk()
    if not config.PERSIST_SPEC_IN_DESIGN:
        return False
    nb = native(body)
    # Attributes.add replaces the value of an existing attribute with the same
    # group and name (see Attributes.add in the API reference).
    nb.attributes.add(config.ATTR_GROUP, config.ATTR_SPEC_NAME, spec.to_json())
    return True


def masked_faces_of(body) -> List[Any]:
    """Native faces of this body that carry the mask marker.

    Uses Design.findAttributes (one call for the whole design, then a parent
    check per hit) and falls back to walking the body's faces."""
    _require_adsk()
    nb = native(body)
    try:
        design = nb.parentComponent.parentDesign
        out = []
        for attr in design.findAttributes(config.ATTR_GROUP, config.ATTR_MASK_NAME):
            parent = attr.parent
            face = adsk.fusion.BRepFace.cast(parent) if parent is not None else None
            if face is None:
                continue
            face = native(face)
            if face.body == nb:
                out.append(face)
        return out
    except Exception:
        pass
    out = []
    for face in nb.faces:
        if face.attributes.itemByName(config.ATTR_GROUP, config.ATTR_MASK_NAME) is not None:
            out.append(face)
    return out


def write_mask_attributes(body, masked_faces) -> bool:
    """Mark the given native faces and clear the marker from every other face
    of the body. Returns False when persistence is disabled."""
    _require_adsk()
    if not config.PERSIST_SPEC_IN_DESIGN:
        return False
    nb = native(body)
    # tempId is unique within a body and stable while the body is unmodified,
    # which holds for the lifetime of the dialog. (Entity tokens are documented
    # as not reliably comparable as strings.)
    wanted = {native(f).tempId for f in masked_faces}
    for face in nb.faces:
        attr = face.attributes.itemByName(config.ATTR_GROUP, config.ATTR_MASK_NAME)
        if face.tempId in wanted:
            if attr is None:
                face.attributes.add(config.ATTR_GROUP, config.ATTR_MASK_NAME, '1')
        elif attr is not None:
            attr.deleteMe()
    return True


def clear_body(body) -> None:
    """Remove every attribute this add-in wrote on the body and its faces."""
    _require_adsk()
    nb = native(body)
    attr = nb.attributes.itemByName(config.ATTR_GROUP, config.ATTR_SPEC_NAME)
    if attr is not None:
        attr.deleteMe()
    for face in nb.faces:
        a = face.attributes.itemByName(config.ATTR_GROUP, config.ATTR_MASK_NAME)
        if a is not None:
            a.deleteMe()
