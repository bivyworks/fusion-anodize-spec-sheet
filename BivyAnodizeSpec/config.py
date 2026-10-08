# Application Global Variables
#
# All tunables for the Anodize Spec Sheet add-in live here so they can be
# adjusted without touching the code. This module is imported both inside
# Fusion and by the test harness, so it must never import adsk.

import os

# ---------------------------------------------------------------- general

# Debug mode writes every log line to Fusion's Text Commands window. Set it to
# False once the add-in is stable; errors are always written to the log file.
DEBUG = True

# Name of the add-in, taken from the folder name (Fusion requires the folder,
# the .py and the .manifest to share one name).
ADDIN_NAME = os.path.basename(os.path.dirname(__file__))
COMPANY_NAME = 'BivyWorks'
ADDIN_VERSION = '0.5.0'   # shown on the dialog's status line and in the log

# Shared "Bivy Works" toolbar panel in the Design workspace -> UTILITIES tab.
# Other Bivy Works add-ins (CAMTemplateCopier, ...) may create the same panel.
# The panel is matched by ID first and then by its visible name, so the two
# add-ins share one panel even if their IDs differ. This add-in only ever adds
# and removes its own button, and deletes the panel only if it created the
# panel itself and the panel is empty when the add-in stops.
WORKSPACE_ID = 'FusionSolidEnvironment'   # Design workspace
TAB_ID = 'ToolsTab'                       # UTILITIES tab
PANEL_ID = 'BivyWorksPanel'
PANEL_NAME = 'Bivy Works'

DISPLAY_UNITS = 'in'                      # only "in" is implemented in v1
PERSIST_SPEC_IN_DESIGN = True             # False: never write attributes


# Attribute group / names used on the body and on masked faces.
ATTR_GROUP = 'BivyWorks.AnodizeSpec'
ATTR_SPEC_NAME = 'spec'
ATTR_MASK_NAME = 'mask'

# Custom event used to run rendering after the command dialog has closed.
CUSTOM_EVENT_ID = f'{COMPANY_NAME}_{ADDIN_NAME}_generate'

# ---------------------------------------------------------------- rendering

RENDER_PX = 2400       # width of the plan-view capture in pixels
PART_GRAY = 227        # body tone (0-255), applied as equal R/G/B
MASK_GRAY = 64         # masked faces (0-255)

# 'throwaway': copy the body into a throwaway document (primary approach).
# 'inplace':   hide everything else in the user's document (fallback).
# Decided by the measurements in docs/spike-notes.md.
RENDER_APPROACH = 'throwaway'

# Image format written by Viewport.saveAsImageFileWithOptions. The spike
# (Oct 6, 2026, Fusion 2705.1.25 on macOS, Python 3.14) showed: PNG keeps the
# alpha channel and decodes in ~0.1 s; TIFF is Deflate-compressed with alpha
# (also fine); BMP is not written at all; JPEG has no alpha. 'auto' probes
# CAPTURE_PROBE_ORDER on the first run and remembers the first format that
# keeps alpha in the machine-local settings file.
CAPTURE_FORMAT = 'png'
CAPTURE_PROBE_ORDER = ('png', 'tif')

# The capture keeps the viewport's aspect ratio (that is the only way the
# fitted framing is reproduced exactly), so on a wide viewport a squarish
# part would only fill part of the frame. The capture is therefore widened
# until the part spans about RENDER_PX pixels, up to MAX_CAPTURE_PX.
MAX_CAPTURE_PX = 6000
CAPTURE_FIT_MARGIN = 1.10      # Fusion's fit view leaves ~7 % around the part

# Print tones. The spike showed the renderer's lighting darkens the body
# appearance a little (227 came out as ~208) and the base appearance tints it
# warm. With NORMALIZE_TONES the captures are converted to neutral gray and the
# dominant light tone is mapped to exactly PART_GRAY (masked faces to
# MASK_GRAY) before they go on the sheet.
NORMALIZE_TONES = True

# Fusion draws a small green camera-pivot marker at the camera target (the
# centre of every capture). The pivot preference is switched off while
# rendering and restored afterwards; semi-transparent green pixels inside this
# fraction of the capture size around the centre are removed as a safety net.
PIVOT_MARKER_RADIUS_FRACTION = 0.01

# The layout grid did not appear in transparent captures in the spike, so the
# grid dodge is off. If it ever shows, set the axis ('x', 'y' or 'z'): the
# throwaway copy is then moved along it by GRID_DODGE_FACTOR times its largest
# dimension so the grid falls outside the fitted frame.
GRID_DODGE_FACTOR = 2.0
GRID_DODGE_AXIS = ''

# Appearance library / appearance / colour-property names are matched by
# substring, in order, because they differ between Fusion builds.
APPEARANCE_LIBRARY_HINTS = ('Fusion Appearance Library', 'Fusion 360 Appearance Library', 'Appearance Library')
APPEARANCE_NAME_HINTS = ('Plastic - Matte', 'Paint - Matte', 'Matte', 'Plastic - Glossy', 'Plastic', 'Paint')
COLOR_PROPERTY_HINTS = ('Color', 'Colour', 'color')

# Margin (px) kept around the part when cropping a capture. 0 keeps the crop
# exactly on the silhouette, which makes the dimension callouts exact.
CROP_MARGIN_PX = 0

# Show the generation progress bar modally (blocks the Fusion UI while the
# views are captured, so a stray click cannot move the camera or land in the
# scratch document). Off until it has been tried in Fusion: the spike only
# tested the non-modal bar alongside document creation and capture.
PROGRESS_MODAL = False

# Isometric context view: captured from the (+X, -Y, +Z) octant of the
# canonical frame (the Z sign follows the plan view) with the thickness axis
# up, and drawn not to scale beside the orthographic views (or below them
# when a long part leaves room there). Set ISO_VIEW = False to drop it.
ISO_VIEW = True
ISO_DIRECTION = (1.0, -1.0, 1.0)
ISO_BOX_FRACTION = 0.36        # share of the view column reserved beside the orthographic views
ISO_MIN_W_PT = 85.0            # narrowest box worth drawing beside (the views shrink at most 10 % for it)
ISO_MIN_BELOW_PT = 100.0       # free height needed to place it below instead

# ---------------------------------------------------------------- sheet defaults

DEFAULT_COMPANY = 'BIVY WORKS'
WORDMARK = 'BIVY WORKS'            # top-right wordmark on the sheet
DEFAULT_SPEC = 'MIL-PRF-8625'
DEFAULT_TEMPER = 'T6'
DEFAULT_VENDOR_NOTE = 'Parts are cosmetic — please avoid scratches and dents and hide racking marks.'
DEFAULT_ANODIZE_TYPE = 'Type II'
DEFAULT_ANODIZE_CLASS = 'Class 2'
DEFAULT_COLOR = 'BLACK'
DEFAULT_SEAL = 'Vendor standard'
DEFAULT_DIMS_CONDITION = 'Before anodize'

TYPE_OPTIONS = ('Type I', 'Type II', 'Type III')
CLASS_OPTIONS = ('Class 1', 'Class 2')
TEMPER_OPTIONS = ('T6', 'T651', 'T6511', 'T73', 'Other')
SEAL_OPTIONS = ('Vendor standard', 'Hot DI water', 'Nickel acetate', 'Dichromate', 'No seal')
DIMS_OPTIONS = ('Before anodize', 'After anodize')
BRITE_DIP_SECONDS_OPTIONS = (30, 45, 60)
QUANTITY_MIN, QUANTITY_MAX = 1, 100000

# ---------------------------------------------------------------- lookup tables

# Substring of the Fusion material name (case-insensitive) -> display name.
# The first match wins, so put the more specific keys first.
MATERIAL_MAP = {
    'mic-6': 'MIC-6 Aluminum',
    'mic6': 'MIC-6 Aluminum',
    '6061': '6061 Aluminum',
    '6063': '6063 Aluminum',
    '7075': '7075 Aluminum',
    '2024': '2024 Aluminum',
    '5052': '5052 Aluminum',
    'ti-6al-4v': 'Ti-6Al-4V Titanium',
    '6al-4v': 'Ti-6Al-4V Titanium',
    'titanium': 'Titanium',
}

# Dropdown entries, in display order. "Other…" is appended by the dialog.
MATERIAL_OPTIONS = (
    '6061 Aluminum', '6063 Aluminum', '7075 Aluminum', '2024 Aluminum',
    '5052 Aluminum', 'MIC-6 Aluminum', 'Ti-6Al-4V Titanium', 'Titanium',
)

# Materials that contain none of these words raise a soft warning.
ANODIZABLE_KEYWORDS = ('aluminum', 'aluminium', 'titanium')

# Coating thickness sanity ranges in inches (min, max) per anodize type.
# These only drive soft warnings on the status line; adjust them to your
# vendor's data.
THICKNESS_SANITY = {
    'Type II': (0.0001, 0.001),
    'Type III': (0.0005, 0.003),
}

# ---------------------------------------------------------------- sheet style
# Black-and-white laser print rules: black ink, one secondary gray, no fills.

INK = 0.0            # all primary text and lines
INK2 = 0.30          # labels and secondary text (70 % black); never lighter
MIN_FONT_PT = 7.5
MIN_STROKE_PT = 0.5

PAGE_W, PAGE_H = 792.0, 612.0       # Letter landscape, points
MARGIN = 36.0                       # 0.5 in

FONT_PART_NAME = 22.0
FONT_CALLOUT_START = 30.0           # callout values start here ...
FONT_CALLOUT_FLOOR = 16.0           # ... and never shrink below this
FONT_CALLOUT_DESC = 9.0
FONT_CALLOUT_DESC_STRONG = 11.0     # the brite dip descriptor: bold and black, so '30 SEC' reads as brite dip
FONT_CALLOUT_TITLE = 10.0
FONT_QUANTITY = 30.0                # fixed, not auto-fit
FONT_SECTION = 11.0
FONT_ROW_VALUE = 10.0
FONT_ROW_LABEL = 8.5
FONT_FIELD_LABEL = 7.5
FONT_FIELD_VALUE = 10.5
FONT_NOTES = 10.0
FONT_NOTES_TITLE = 10.0
FONT_MASK_SUB = 8.5
FONT_DIM = 9.0
FONT_BALLOON = 7.5
FONT_CAPTION = 8.5
FONT_FOOTER = 7.5
FONT_WORDMARK = 12.0

CALLOUT_GAP_MIN = 24.0              # minimum gap between callout cells
QUANTITY_BOX_W = 92.0
LEFT_COL_W = 262.0
VIEW_COL_X = 330.0
DIM_ROOM_RIGHT = 44.0               # room for the W / T dimension callouts
MASK_DESC_MAX_LINES = 8
