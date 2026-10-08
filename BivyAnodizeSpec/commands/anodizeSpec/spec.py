"""AnodizeSpec: the data model for one anodize spec sheet.

No ``adsk`` imports: this module is shared by the Fusion command and by the
test harness that runs with system Python. It owns

* the ``AnodizeSpec`` dataclass with tolerant ``to_dict`` / ``from_dict``,
* preset loading from ``presets.json`` and ``apply_preset``,
* the small formatting helpers the dialog and the sheet both need, and
* ``config`` access that works inside Fusion and from the tests.
"""

from __future__ import annotations

import datetime as _dt
import importlib.util
import json
import re
from dataclasses import asdict, dataclass, fields
from pathlib import Path
from typing import Any, Optional, Union

SPEC_VERSION = 1

ADDIN_ROOT = Path(__file__).resolve().parents[2]


def _load_config():
    """Return the add-in's config module.

    Inside Fusion the add-in is a package, so the relative import works. The
    test harness imports this package directly, so fall back to loading
    ``config.py`` by path.
    """
    try:
        from ... import config  # type: ignore
        return config
    except (ImportError, ValueError):
        path = ADDIN_ROOT / 'config.py'
        spec = importlib.util.spec_from_file_location('bivy_anodize_config', path)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)  # type: ignore[union-attr]
        return module


config = _load_config()

BRITE_NONE = 'none'
BriteDip = Union[None, str, int]


@dataclass
class AnodizeSpec:
    """Everything the user chooses in the dialog. Computed values (areas,
    envelope, descriptions) are never stored here."""

    part_name: str = ''
    part_number: str = ''
    revision: str = ''
    customer_project: str = ''
    company: str = config.DEFAULT_COMPANY
    quantity: int = 1
    material: str = ''
    temper: str = config.DEFAULT_TEMPER
    spec_standard: str = config.DEFAULT_SPEC
    anodize_type: str = config.DEFAULT_ANODIZE_TYPE
    anodize_class: str = config.DEFAULT_ANODIZE_CLASS
    color: str = config.DEFAULT_COLOR
    thickness_min_in: Optional[float] = None
    thickness_max_in: Optional[float] = None
    seal: str = config.DEFAULT_SEAL
    brite_dip: BriteDip = None          # None = not chosen yet; 'none' or int seconds
    dims_condition: str = config.DEFAULT_DIMS_CONDITION
    masking_note: str = ''
    vendor_notes: str = config.DEFAULT_VENDOR_NOTE
    flip_view: bool = False

    # ------------------------------------------------------------ serialisation
    def to_dict(self) -> dict:
        d = asdict(self)
        d['spec_version'] = SPEC_VERSION
        d['color'] = (self.color or '').upper()
        return d

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), ensure_ascii=False, sort_keys=True)

    @classmethod
    def from_dict(cls, d: Any) -> 'AnodizeSpec':
        """Build a spec from a dict. Unknown keys are ignored and missing keys
        keep their defaults, so specs saved by older versions still load."""
        spec = cls()
        if not isinstance(d, dict):
            return spec
        for f in fields(cls):
            if f.name in d:
                setattr(spec, f.name, _coerce(f.name, d[f.name], getattr(spec, f.name)))
        spec.color = (spec.color or '').upper()
        return spec

    @classmethod
    def from_json(cls, text: Any) -> 'AnodizeSpec':
        try:
            return cls.from_dict(json.loads(text))
        except (TypeError, ValueError):
            return cls()

    def copy(self) -> 'AnodizeSpec':
        return AnodizeSpec.from_dict(self.to_dict())


_FLOAT_FIELDS = {'thickness_min_in', 'thickness_max_in'}
_INT_FIELDS = {'quantity'}
_BOOL_FIELDS = {'flip_view'}


def _coerce(name: str, value: Any, default: Any) -> Any:
    if name in _FLOAT_FIELDS:
        return parse_float(value)
    if name in _INT_FIELDS:
        try:
            v = int(value)
        except (TypeError, ValueError):
            return default
        return v if v >= 1 else default
    if name in _BOOL_FIELDS:
        if isinstance(value, str):
            return value.strip().lower() in ('1', 'true', 'yes', 'on')
        return bool(value)
    if name == 'brite_dip':
        return normalize_brite_dip(value)
    if value is None:
        return ''
    return str(value)


def parse_float(value: Any) -> Optional[float]:
    """Parse a float from user text. Returns None for blank/None/invalid."""
    if value is None:
        return None
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    text = str(value).strip().replace(',', '.')
    if not text:
        return None
    # Accept a trailing unit the user may have typed, e.g. "0.0015 in".
    text = re.sub(r'\s*(in|inch|inches|")$', '', text, flags=re.IGNORECASE).strip()
    try:
        return float(text)
    except ValueError:
        return None


def normalize_brite_dip(value: Any) -> BriteDip:
    """None (unset), 'none' (do not dip) or a positive int of seconds."""
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return int(value) if value > 0 else None
    text = str(value).strip().lower()
    if not text:
        return None
    if text in (BRITE_NONE, 'no', 'do not dip', 'none.', 'off'):
        return BRITE_NONE
    m = re.match(r'^(\d+)\s*(s|sec|secs|seconds)?$', text)
    if m and int(m.group(1)) > 0:
        return int(m.group(1))
    return None


# ---------------------------------------------------------------- presets

_SEED_PRESETS = [
    {'label': '— Custom —'},
    {'label': 'Type III · Class 2 · Black', 'type': 'Type III', 'class': 'Class 2', 'color': 'BLACK',
     'thickness_min_in': 0.001, 'thickness_max_in': 0.0015},
    {'label': 'Type III · Class 1 · Clear', 'type': 'Type III', 'class': 'Class 1', 'color': 'CLEAR',
     'thickness_min_in': None, 'thickness_max_in': None},
    {'label': 'Type II · Class 2 · Black', 'type': 'Type II', 'class': 'Class 2', 'color': 'BLACK',
     'thickness_min_in': None, 'thickness_max_in': None},
    {'label': 'Type II · Class 1 · Clear', 'type': 'Type II', 'class': 'Class 1', 'color': 'CLEAR',
     'thickness_min_in': None, 'thickness_max_in': None},
    {'label': 'Type II · Class 2 · Red', 'type': 'Type II', 'class': 'Class 2', 'color': 'RED',
     'thickness_min_in': None, 'thickness_max_in': None},
    {'label': 'Type II · Class 2 · Blue', 'type': 'Type II', 'class': 'Class 2', 'color': 'BLUE',
     'thickness_min_in': None, 'thickness_max_in': None},
]

PRESETS_PATH = ADDIN_ROOT / 'presets.json'


def load_presets(path: Optional[Path] = None) -> list:
    """Read presets.json. Falls back to the seed list if the file is missing
    or invalid, so a broken edit never blocks the command."""
    path = Path(path) if path else PRESETS_PATH
    try:
        data = json.loads(path.read_text(encoding='utf-8'))
    except (OSError, ValueError):
        return [dict(p) for p in _SEED_PRESETS]
    presets = []
    if isinstance(data, list):
        for item in data:
            if isinstance(item, dict) and str(item.get('label', '')).strip():
                presets.append(item)
    return presets or [dict(p) for p in _SEED_PRESETS]


def apply_preset(spec: AnodizeSpec, preset: dict) -> AnodizeSpec:
    """Copy the preset's non-null values into the spec (in place) and return it.
    A null value, or a missing key, leaves that field unchanged."""
    if not isinstance(preset, dict):
        return spec
    if preset.get('type') is not None:
        spec.anodize_type = str(preset['type'])
    if preset.get('class') is not None:
        spec.anodize_class = str(preset['class'])
    if preset.get('color') is not None:
        spec.color = str(preset['color']).upper()
    if preset.get('thickness_min_in') is not None:
        spec.thickness_min_in = parse_float(preset['thickness_min_in'])
    if preset.get('thickness_max_in') is not None:
        spec.thickness_max_in = parse_float(preset['thickness_max_in'])
    if 'brite_dip' in preset and preset['brite_dip'] is not None:
        spec.brite_dip = normalize_brite_dip(preset['brite_dip'])
    return spec


# ---------------------------------------------------------------- material

def map_material(fusion_material_name: str) -> str:
    """Map a Fusion material name to a display name through config.MATERIAL_MAP
    by case-insensitive substring. Returns '' when nothing matches."""
    name = (fusion_material_name or '').lower()
    if not name:
        return ''
    for key, display in config.MATERIAL_MAP.items():
        if key.lower() in name:
            return display
    return ''


def is_anodizable(material: str) -> bool:
    m = (material or '').lower()
    return any(k in m for k in config.ANODIZABLE_KEYWORDS)


def material_line(spec: AnodizeSpec) -> str:
    """'6061-T6 aluminum' for aluminum alloys; other materials print as typed."""
    mat = (spec.material or '').strip()
    temper = (spec.temper or '').strip()
    if not mat:
        return '—'
    low = mat.lower()
    for word in ('aluminum', 'aluminium'):
        if low.endswith(' ' + word):
            alloy = mat[: -len(word) - 1].strip()
            if temper and temper.lower() != 'other':
                return f'{alloy}-{temper} aluminum'
            return f'{alloy} aluminum'
    return mat


# ---------------------------------------------------------------- formatting

def format_thickness(value: Optional[float]) -> str:
    """Thickness always prints with 4 decimals (0.0015 stays 0.0015)."""
    if value is None:
        return ''
    return f'{value:.4f}'


def thickness_text(t_min: Optional[float], t_max: Optional[float]) -> str:
    if t_min is not None and t_max is not None:
        return f'{format_thickness(t_min)}–{format_thickness(t_max)}'
    if t_min is not None:
        return f'{format_thickness(t_min)} min'
    if t_max is not None:
        return f'{format_thickness(t_max)} max'
    return 'Not specified'


def brite_dip_cell(value: BriteDip) -> tuple:
    """(big value, descriptor) for the callout. Validation guarantees a value;
    an unset value still prints something sensible rather than crashing."""
    v = normalize_brite_dip(value)
    if v == BRITE_NONE:
        return 'NONE', 'NO BRITE DIP'
    if isinstance(v, int):
        return f'{v} SEC', 'BRITE DIP'
    return 'NONE', 'NO BRITE DIP'


def brite_dip_label(value: BriteDip) -> str:
    """Short human form for the dialog / logs."""
    v = normalize_brite_dip(value)
    if v == BRITE_NONE:
        return 'Do not brite dip'
    if isinstance(v, int):
        return f'{v} s'
    return 'Choose…'


_TYPE_DESC = {'Type I': 'Chromic', 'Type II': 'Sulfuric', 'Type III': 'Hardcoat'}
_CLASS_DESC = {'Class 1': 'Undyed', 'Class 2': 'Dyed'}


def type_descriptor(anodize_type: str) -> str:
    return _TYPE_DESC.get((anodize_type or '').strip(), '')


def class_descriptor(anodize_class: str) -> str:
    return _CLASS_DESC.get((anodize_class or '').strip(), '')


def color_cell(color: str) -> tuple:
    c = (color or '').strip().upper() or '—'
    if c == 'CLEAR':
        return 'CLEAR', 'Undyed / natural'
    return c, 'Color'


def callout_cells(spec: AnodizeSpec) -> list:
    """The five (value, descriptor) pairs of the process callout."""
    return [
        ((spec.anodize_type or '').upper() or '—', type_descriptor(spec.anodize_type)),
        ((spec.anodize_class or '').upper() or '—', class_descriptor(spec.anodize_class)),
        color_cell(spec.color),
        (thickness_text(spec.thickness_min_in, spec.thickness_max_in), 'Coating thickness, in'),
        brite_dip_cell(spec.brite_dip),
    ]


def format_date(d: Optional[_dt.date] = None) -> str:
    """'Oct 6, 2026' on every platform (no %-d, which fails on Windows)."""
    d = d or _dt.date.today()
    return f'{d:%b} {d.day}, {d.year}'


def format_area(value_in2: float) -> str:
    return f'{value_in2:.2f} sq in'


def format_envelope(dims_in) -> str:
    L, W, T = dims_in
    return f'{L:.3f} × {W:.3f} × {T:.3f} in'


# ---------------------------------------------------------------- file names

_WINDOWS_RESERVED = {
    'CON', 'PRN', 'AUX', 'NUL',
    *(f'COM{i}' for i in range(1, 10)), *(f'LPT{i}' for i in range(1, 10)),
}


def sanitize_filename(name: str, max_len: int = 120) -> str:
    """Make a file name safe on both macOS and Windows. Spaces are kept."""
    text = str(name or '')
    text = re.sub(r'[<>:"/\\|?*\x00-\x1f]', '_', text)
    text = re.sub(r'\s+', ' ', text).strip(' .')
    if not text:
        text = 'untitled'
    stem = text.rsplit('.', 1)[0] if '.' in text else text
    if stem.upper() in _WINDOWS_RESERVED:
        text = '_' + text
    return text[:max_len].rstrip(' .') or 'untitled'


def default_filename(spec: AnodizeSpec) -> str:
    """'{PN}_{Rev}_{PartName}_ANODIZE.pdf' without the empty parts."""
    parts = [p.strip() for p in (spec.part_number, spec.revision, spec.part_name) if p and p.strip()]
    stem = '_'.join(sanitize_filename(p) for p in parts) if parts else 'part'
    return sanitize_filename(f'{stem}_ANODIZE') + '.pdf'


# ---------------------------------------------------------------- validation

def validate(spec: AnodizeSpec, body_selected: bool = True) -> list:
    """Blocking errors, in display order. Empty list means OK is allowed."""
    errors = []
    if not body_selected:
        errors.append('Select one solid body.')
    if spec.quantity is None or spec.quantity < 1:
        errors.append('Quantity must be at least 1.')
    if spec.thickness_min_in is not None and spec.thickness_max_in is not None \
            and spec.thickness_min_in > spec.thickness_max_in:
        errors.append('Min thickness is greater than max thickness.')
    bd = normalize_brite_dip(spec.brite_dip)
    if bd is None:
        errors.append('Choose a brite dip option.')
    return errors


def soft_warnings(spec: AnodizeSpec) -> list:
    """Warnings that never block OK."""
    warnings = []
    rng = config.THICKNESS_SANITY.get(spec.anodize_type)
    if rng:
        lo, hi = rng
        for label, v in (('Min', spec.thickness_min_in), ('Max', spec.thickness_max_in)):
            if v is not None and not (lo <= v <= hi):
                warnings.append(f'{label} thickness {format_thickness(v)} is outside the usual '
                                f'{spec.anodize_type} range {format_thickness(lo)}–{format_thickness(hi)}.')
    color = (spec.color or '').strip().upper()
    if spec.anodize_class == 'Class 2' and color == 'CLEAR':
        warnings.append('Class 2 is dyed, but the color is CLEAR.')
    if spec.anodize_class == 'Class 1' and color and color != 'CLEAR':
        warnings.append(f'Class 1 is undyed, but a dye color ({color}) is set.')
    if spec.material and not is_anodizable(spec.material):
        warnings.append(f'{spec.material} is not aluminum or titanium.')
    return warnings
