"""Anodize Spec Sheet command: toolbar button, right-click entry, the dialog,
validation, attribute persistence and the deferred generation step.

Structure follows Fusion's Python add-in template: ``start()`` / ``stop()``
register and remove the UI, ``command_created`` builds the dialog and connects
the command events, and every handler is wrapped by ``futil.add_handler`` so
exceptions are logged instead of killing the add-in.

Generation (rendering, PDF) never runs inside a command event: ``execute``
stores the job and fires a custom event, and ``on_custom_event`` does the work
after the command has closed, because creating and closing documents is not
allowed while a command transaction is open.
"""

import html
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

import adsk.core
import adsk.fusion

from ...lib import fusionAddInUtils as futil
from ... import config
from . import geometry, persistence, raster, render, sheet_layout
from .spec import (AnodizeSpec, BRITE_NONE, apply_preset, default_filename, format_date,
                   format_envelope, format_thickness, load_presets, map_material, normalize_brite_dip,
                   parse_float, soft_warnings, validate)

app = adsk.core.Application.get()
ui = app.userInterface

CMD_ID = f'{config.COMPANY_NAME}_{config.ADDIN_NAME}_anodizeSpec'
CMD_NAME = 'Anodize Spec Sheet'
CMD_DESCRIPTION = ('Generate a one-page anodize finishing specification PDF for the selected solid body, '
                   'with plan and side views, masking callouts and the process spec.')
IS_PROMOTED = True
ICON_FOLDER = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'resources', '')
ADDINS_PANEL_ID = 'SolidScriptsAddinsPanel'     # the ADD-INS panel; it lives on the UTILITIES tab

# Input IDs
ID_STATUS, ID_TAB_PART, ID_TAB_ANOD, ID_TAB_MASK = 'status', 'tab_part', 'tab_anodize', 'tab_mask'
ID_BODY, ID_PART_NAME, ID_PART_NUMBER, ID_REVISION = 'body', 'part_name', 'part_number', 'revision'
ID_CUSTOMER, ID_COMPANY, ID_QTY, ID_MATERIAL, ID_MATERIAL_OTHER, ID_TEMPER, ID_INFO = (
    'customer', 'company', 'quantity', 'material', 'material_other', 'temper', 'info')
ID_PRESET, ID_TYPE, ID_CLASS, ID_COLOR, ID_THICK_MIN, ID_THICK_MAX, ID_SPEC_STD = (
    'preset', 'anod_type', 'anod_class', 'color', 'thick_min', 'thick_max', 'spec_std')
ID_SEAL, ID_BRITE, ID_BRITE_CUSTOM, ID_DIMS = 'seal', 'brite', 'brite_custom', 'dims'
ID_MASK_FACES, ID_MASK_NOTE, ID_MASK_SUMMARY, ID_VENDOR_NOTES, ID_FLIP, ID_OPEN = (
    'mask_faces', 'mask_note', 'mask_summary', 'vendor_notes', 'flip_view', 'open_pdf')

MATERIAL_OTHER = 'Other…'
BRITE_CHOOSE, BRITE_NO, BRITE_CUSTOM = 'Choose…', 'Do not brite dip', 'Custom…'

local_handlers = []
_custom_event = None
_created_panel = False
_state = {}          # per-dialog state: 'body', 'faces', 'presets', 'context_body', 'job', 'geom_cache'


# ====================================================================== UI registration

def _panel_collection_on_utilities_tab(workspace):
    """The UTILITIES tab's ID is not documented, so find the tab that owns the
    ADD-INS panel; fall back to the configured ID, then to the workspace-level
    collection (which Fusion documents as targeting the Tools tab)."""
    for i in range(workspace.toolbarTabs.count):
        tab = workspace.toolbarTabs.item(i)
        if tab.toolbarPanels.itemById(ADDINS_PANEL_ID) is not None:
            return tab.toolbarPanels
    tab = workspace.toolbarTabs.itemById(config.TAB_ID)
    return tab.toolbarPanels if tab is not None else workspace.toolbarPanels


def _get_or_create_panel(workspace):
    """Shared "Bivy Works" panel: reuse by ID, then by visible name, else create."""
    global _created_panel
    panel = workspace.toolbarPanels.itemById(config.PANEL_ID)
    if panel is not None:
        return panel
    panels = _panel_collection_on_utilities_tab(workspace)
    for i in range(panels.count):
        cand = panels.item(i)
        if cand.name == config.PANEL_NAME:
            return cand
    panel = panels.add(config.PANEL_ID, config.PANEL_NAME, ADDINS_PANEL_ID, False)
    _created_panel = panel is not None
    return panel


def start():
    global _custom_event
    workspace = ui.workspaces.itemById(config.WORKSPACE_ID)
    panel = _get_or_create_panel(workspace) if workspace is not None else None
    cmd_def = ui.commandDefinitions.itemById(CMD_ID)
    if cmd_def is not None:          # stale definition from an earlier load
        # Its toolbar button would stay bound to the deleted definition:
        # remove the button first, then the definition.
        old = panel.controls.itemById(CMD_ID) if panel is not None else None
        if old is not None:
            old.deleteMe()
        cmd_def.deleteMe()
    cmd_def = ui.commandDefinitions.addButtonDefinition(CMD_ID, CMD_NAME, CMD_DESCRIPTION, ICON_FOLDER)
    futil.add_handler(cmd_def.commandCreated, command_created)

    if panel is not None:
        control = panel.controls.itemById(CMD_ID)
        if control is None:
            control = panel.controls.addCommand(cmd_def)
        if control is not None:
            control.isPromotedByDefault = IS_PROMOTED
            control.isPromoted = IS_PROMOTED

    # Right-click menu entry on solid bodies.
    futil.add_handler(ui.markingMenuDisplaying, on_marking_menu)

    # Custom event that runs the generation after the dialog has closed.
    try:
        app.unregisterCustomEvent(config.CUSTOM_EVENT_ID)
    except Exception:
        pass
    _custom_event = app.registerCustomEvent(config.CUSTOM_EVENT_ID)
    if _custom_event is None:
        # Returns None when the id is already registered (add-in reloaded
        # without stop() running). Generation would never run: say so loudly.
        futil.log(f'{CMD_NAME}: registerCustomEvent({config.CUSTOM_EVENT_ID}) returned None',
                  adsk.core.LogLevels.ErrorLogLevel)
    else:
        futil.add_handler(_custom_event, on_custom_event)
    futil.log(f'{CMD_NAME} started')


def stop():
    workspace = ui.workspaces.itemById(config.WORKSPACE_ID)
    if workspace is not None:
        panel = workspace.toolbarPanels.itemById(config.PANEL_ID)
        if panel is None:
            panels = _panel_collection_on_utilities_tab(workspace)
            for i in range(panels.count):
                if panels.item(i).name == config.PANEL_NAME:
                    panel = panels.item(i)
                    break
        if panel is not None:
            control = panel.controls.itemById(CMD_ID)
            if control is not None:
                control.deleteMe()
            # Only remove the shared panel when this add-in created it and
            # nothing else is left in it.
            if _created_panel and panel.controls.count == 0:
                panel.deleteMe()
    cmd_def = ui.commandDefinitions.itemById(CMD_ID)
    if cmd_def is not None:
        cmd_def.deleteMe()
    try:
        app.unregisterCustomEvent(config.CUSTOM_EVENT_ID)
    except Exception:
        pass


def _solid_of(entity):
    """The solid body an entity belongs to: the body itself, or the parent
    body of a face, edge or vertex (a canvas click selects the face under the
    cursor, not the body). None for anything else."""
    body = adsk.fusion.BRepBody.cast(entity)
    if body is None:
        for cls in (adsk.fusion.BRepFace, adsk.fusion.BRepEdge, adsk.fusion.BRepVertex):
            sub = cls.cast(entity)
            if sub is not None:
                try:
                    body = sub.body
                except Exception:
                    body = None
                break
    try:
        return body if body is not None and body.isSolid else None
    except Exception:
        return None


def _selected_solid():
    """The solid body behind the current selection (first match), or None."""
    sels = ui.activeSelections
    for i in range(sels.count):
        body = _solid_of(sels.item(i).entity)
        if body is not None:
            return body
    return None


def on_marking_menu(args: adsk.core.MarkingMenuEventArgs):
    """Add "Anodize Spec Sheet" to the right-click menu when a solid body (or
    one of its faces, edges or vertices) is under the cursor. The right-click
    selects that entity, so the dialog pre-fills from the active selection."""
    body = None
    for ent in args.selectedEntities:
        body = _solid_of(ent)
        if body is not None:
            break
    if body is None:
        return
    cmd_def = ui.commandDefinitions.itemById(CMD_ID)
    if cmd_def is None:
        return
    controls = args.linearMarkingMenu.controls
    if controls.itemById(CMD_ID) is None:
        controls.addCommand(cmd_def)


# ====================================================================== helpers

def _all_inputs(args):
    """The command's whole input collection for any command event.

    ``InputChangedEventArgs.inputs`` is only the collection the changed input
    sits in (a tab's children), so ``itemById`` on it misses the other tabs;
    ``Command.commandInputs`` searches tabs and groups. ``Event.sender`` is
    the command for every command event."""
    cmd = None
    try:
        cmd = args.command
    except Exception:
        pass
    if cmd is None:
        try:
            cmd = adsk.core.Command.cast(args.firingEvent.sender)
        except Exception:
            cmd = None
    if cmd is not None:
        return cmd.commandInputs
    return _state.get('inputs')


def _inp(inputs, input_id):
    return inputs.itemById(input_id)


def _dropdown(inputs, input_id):
    return adsk.core.DropDownCommandInput.cast(inputs.itemById(input_id))


def _select_item(dropdown, name: str, fallback_index: int = 0) -> None:
    """ListItems has no itemByName: iterate. Logs when the selection did not
    take, so a Fusion-side refusal is visible in the Text Commands window."""
    if dropdown is None:
        futil.log(f'{CMD_NAME}: dropdown missing when selecting {name!r}', adsk.core.LogLevels.WarningLogLevel)
        return
    items = dropdown.listItems
    for i in range(items.count):
        if items.item(i).name == name:
            items.item(i).isSelected = True
            if _selected_name(dropdown) != name:
                futil.log(f'{CMD_NAME}: selecting {name!r} in {dropdown.id} did not take (now {_selected_name(dropdown)!r})',
                          adsk.core.LogLevels.WarningLogLevel)
            return
    futil.log(f'{CMD_NAME}: {name!r} is not an option of {dropdown.id}', adsk.core.LogLevels.WarningLogLevel)
    if items.count:
        items.item(min(fallback_index, items.count - 1)).isSelected = True


def _selected_name(dropdown) -> str:
    item = dropdown.selectedItem
    return item.name if item is not None else ''


def _add_dropdown(parent, input_id, label, options, selected=None):
    dd = parent.addDropDownCommandInput(input_id, label, adsk.core.DropDownStyles.TextListDropDownStyle)
    for opt in options:
        dd.listItems.add(opt, opt == selected, '')
    if dd.selectedItem is None and dd.listItems.count:
        dd.listItems.item(0).isSelected = True
    return dd


def _set_text(inputs, input_id, value: str) -> None:
    inp = adsk.core.StringValueCommandInput.cast(inputs.itemById(input_id))
    if inp is not None:
        inp.value = value or ''


def _get_text(inputs, input_id) -> str:
    inp = adsk.core.StringValueCommandInput.cast(inputs.itemById(input_id))
    return (inp.value or '').strip() if inp is not None else ''


def _set_box(inputs, input_id, text: str, formatted: bool = True) -> None:
    box = adsk.core.TextBoxCommandInput.cast(inputs.itemById(input_id))
    if box is None:
        return
    if formatted:
        box.formattedText = text
    else:
        box.text = text


def _native_body(entity):
    body = adsk.fusion.BRepBody.cast(entity)
    if body is None:
        return None
    return geometry.native_of(body)


def _selected_body(inputs):
    sel = adsk.core.SelectionCommandInput.cast(inputs.itemById(ID_BODY))
    if sel is None or sel.selectionCount == 0:
        return None
    return adsk.fusion.BRepBody.cast(sel.selection(0).entity)


def _selected_faces(inputs):
    sel = adsk.core.SelectionCommandInput.cast(inputs.itemById(ID_MASK_FACES))
    faces = []
    if sel is None:
        return faces
    for i in range(sel.selectionCount):
        f = adsk.fusion.BRepFace.cast(sel.selection(i).entity)
        if f is not None:
            faces.append(f)
    return faces


def _same_body(face, body) -> bool:
    """Does ``face`` belong to ``body`` (either may be a proxy)? The native
    bodies are compared with ``==`` (the documented way to test whether two
    variables reference the same Fusion object), then by identity key. A face
    of a twin body (same tempIds, same areas) must never pass, so there is no
    geometric fallback."""
    try:
        return _same_native_body(geometry.native_of(face.body), geometry.native_of(body))
    except Exception:
        return False


def _document_part_name() -> str:
    """The design's file name (without the ' vN' version suffix), or '' for an
    unsaved/untitled document."""
    doc = app.activeDocument
    if doc is None:
        return ''
    name = ''
    try:
        if doc.isSaved:
            name = doc.dataFile.name or ''
    except Exception:
        name = ''
    if not name:
        try:
            name = doc.name or ''
        except Exception:
            name = ''
    name = re.sub(r'\s+v\d+$', '', name).strip()
    return '' if name.lower().startswith('untitled') else name


def _model_part_name(native_body) -> str:
    """Part name default: the file name; else the body name if the user
    renamed it; else the component name."""
    file_name = _document_part_name()
    if file_name:
        return file_name
    name = native_body.name or ''
    if name and not re.match(r'^Body\d+$', name):
        return name
    try:
        return native_body.parentComponent.name or name
    except Exception:
        return name


def _model_part_number(native_body) -> str:
    try:
        return native_body.parentComponent.partNumber or ''
    except Exception:
        return ''


def _model_material(native_body) -> str:
    try:
        mat = native_body.material
        return mat.name if mat is not None else ''
    except Exception:
        return ''


def _document_version_text(modified: bool = False) -> str:
    """'v12', 'v12 + unsaved changes' or 'unsaved'. ``modified`` is sampled
    by the caller before the add-in writes attributes (isModified stays True
    after the user edits the model, and the sheet shows that model)."""
    doc = app.activeDocument
    if doc is None:
        return 'unsaved'
    try:
        if doc.isSaved:
            ver = f'v{doc.dataFile.versionNumber}'
            return ver + ' + unsaved changes' if modified else ver
    except Exception:
        pass
    return 'unsaved'


def _document_is_modified() -> bool:
    try:
        doc = app.activeDocument
        return bool(doc is not None and doc.isModified)
    except Exception:
        return False


def _open_file(path: Path) -> None:
    try:
        if sys.platform == 'darwin':
            subprocess.run(['open', str(path)], check=False)
        elif sys.platform.startswith('win'):
            os.startfile(str(path))  # type: ignore[attr-defined]
        else:
            subprocess.run(['xdg-open', str(path)], check=False)
    except Exception:
        futil.handle_error('open PDF')


# ====================================================================== dialog

def command_created(args: adsk.core.CommandCreatedEventArgs):
    futil.log(f'{CMD_NAME} v{config.ADDIN_VERSION} created')
    cmd = args.command
    cmd.setDialogInitialSize(470, 660)
    cmd.setDialogMinimumSize(420, 520)
    cmd.okButtonText = 'Generate'
    cmd.isExecutedWhenPreEmpted = False   # never generate because the user clicked another command
    cmd.isRepeatable = False
    inputs = cmd.commandInputs
    _state['inputs'] = inputs
    settings = persistence.load_settings()
    presets = load_presets()
    # Per-dialog state. 'context_body' (set by the right-click handler) and a
    # pending 'job' must survive this reset.
    _state['presets'] = presets
    _state['geom_cache'] = {}
    _state['body_obj'] = None
    _state['body_key'] = ''

    # Status line, always visible above the tabs.
    status = inputs.addTextBoxCommandInput(ID_STATUS, '', f'v{config.ADDIN_VERSION} · Select a solid body.', 2, True)
    status.isFullWidth = True

    # ------------------------------------------------------------ Part tab
    tab = inputs.addTabCommandInput(ID_TAB_PART, 'Part')
    t = tab.children
    sel = t.addSelectionInput(ID_BODY, 'Body', 'Select the solid body for this sheet')
    sel.addSelectionFilter('SolidBodies')
    sel.setSelectionLimits(1, 1)
    t.addStringValueInput(ID_PART_NAME, 'Part name', '')
    t.addStringValueInput(ID_PART_NUMBER, 'Part number', '')
    t.addStringValueInput(ID_REVISION, 'Revision', '')
    t.addStringValueInput(ID_CUSTOMER, 'Customer / Project', settings.get('last_customer', '') or '')
    t.addStringValueInput(ID_COMPANY, 'Company', settings.get('last_company') or config.DEFAULT_COMPANY)
    t.addIntegerSpinnerCommandInput(ID_QTY, 'Quantity', config.QUANTITY_MIN, config.QUANTITY_MAX, 1, 1)
    _add_dropdown(t, ID_MATERIAL, 'Material', list(config.MATERIAL_OPTIONS) + [MATERIAL_OTHER], config.MATERIAL_OPTIONS[0])
    other = t.addStringValueInput(ID_MATERIAL_OTHER, 'Material (other)', '')
    other.isVisible = False
    _add_dropdown(t, ID_TEMPER, 'Temper', config.TEMPER_OPTIONS, config.DEFAULT_TEMPER)
    info = t.addTextBoxCommandInput(ID_INFO, '', 'Surface area and envelope appear here once a body is selected.', 2, True)
    info.isFullWidth = True

    # ------------------------------------------------------------ Anodize tab
    tab2 = inputs.addTabCommandInput(ID_TAB_ANOD, 'Anodize')
    a = tab2.children
    _add_dropdown(a, ID_PRESET, 'Preset', [p['label'] for p in presets], presets[0]['label'])
    _add_dropdown(a, ID_TYPE, 'Type', config.TYPE_OPTIONS, config.DEFAULT_ANODIZE_TYPE)
    _add_dropdown(a, ID_CLASS, 'Class', config.CLASS_OPTIONS, config.DEFAULT_ANODIZE_CLASS)
    a.addStringValueInput(ID_COLOR, 'Color', config.DEFAULT_COLOR)
    a.addStringValueInput(ID_THICK_MIN, 'Min thickness (in)', '')
    a.addStringValueInput(ID_THICK_MAX, 'Max thickness (in)', '')
    a.addStringValueInput(ID_SPEC_STD, 'Spec', config.DEFAULT_SPEC)
    _add_dropdown(a, ID_SEAL, 'Seal', config.SEAL_OPTIONS, config.DEFAULT_SEAL)
    brite_options = [BRITE_CHOOSE, BRITE_NO] + [f'{s} s' for s in config.BRITE_DIP_SECONDS_OPTIONS] + [BRITE_CUSTOM]
    _add_dropdown(a, ID_BRITE, 'Brite dip', brite_options, BRITE_CHOOSE)
    custom = a.addIntegerSpinnerCommandInput(ID_BRITE_CUSTOM, 'Brite dip seconds', 1, 3600, 5, 45)
    custom.isVisible = False
    _add_dropdown(a, ID_DIMS, 'Dimensions apply', config.DIMS_OPTIONS, config.DEFAULT_DIMS_CONDITION)

    # ------------------------------------------------------------ Masking & Notes tab
    tab3 = inputs.addTabCommandInput(ID_TAB_MASK, 'Masking & Notes')
    m = tab3.children
    faces = m.addSelectionInput(ID_MASK_FACES, 'Masked faces', 'Select faces on the body to mask (optional)')
    faces.addSelectionFilter('Faces')
    faces.setSelectionLimits(0)
    # A face selected before the command started is how the user picked the
    # part, not a mask: keep Fusion from pushing it into this input.
    faces.isUseCurrentSelections = False
    note = m.addTextBoxCommandInput(ID_MASK_NOTE, 'Masking note', '', 3, False)
    note.text = ''
    summary = m.addTextBoxCommandInput(ID_MASK_SUMMARY, '', 'No faces masked.', 1, True)
    summary.isFullWidth = True
    vendor = m.addTextBoxCommandInput(ID_VENDOR_NOTES, 'Notes for vendor', '', 4, False)
    vendor.text = config.DEFAULT_VENDOR_NOTE
    m.addBoolValueInput(ID_FLIP, 'Flip view', True, '', False)
    m.addBoolValueInput(ID_OPEN, 'Open PDF when done', True, '', True)

    tab.activate()

    futil.add_handler(cmd.activate, command_activate, local_handlers=local_handlers)
    futil.add_handler(cmd.inputChanged, command_input_changed, local_handlers=local_handlers)
    futil.add_handler(cmd.validateInputs, command_validate_inputs, local_handlers=local_handlers)
    futil.add_handler(cmd.preSelect, command_pre_select, local_handlers=local_handlers)
    futil.add_handler(cmd.select, command_selection_changed, local_handlers=local_handlers)
    futil.add_handler(cmd.unselect, command_selection_changed, local_handlers=local_handlers)
    futil.add_handler(cmd.execute, command_execute, local_handlers=local_handlers)
    futil.add_handler(cmd.destroy, command_destroy, local_handlers=local_handlers)


def command_activate(args: adsk.core.CommandEventArgs):
    """addSelection is only legal from activate onward. Pre-fill the body from
    the active selection (a body, or the body behind a selected face, edge or
    vertex: both the toolbar-after-click and the right-click paths), then
    load its data."""
    inputs = _all_inputs(args)
    sel = adsk.core.SelectionCommandInput.cast(inputs.itemById(ID_BODY))
    if sel.selectionCount == 0:
        candidate = _selected_solid()
        if candidate is not None:
            added = sel.addSelection(candidate)
            futil.log(f'{CMD_NAME}: pre-selected body {candidate.name!r} added: {added}')
    _sync_body(inputs)
    _refresh_status(inputs)


_KEY_UNKNOWN = '\x00'   # cannot occur in a token, id or name


def _body_key(native) -> str:
    """A string that identifies a body for the lifetime of the dialog without
    relying on object equality: entity token, plus component id and name."""
    parts = []
    for getter in (lambda: native.entityToken, lambda: native.parentComponent.id, lambda: native.name):
        try:
            parts.append(str(getter()))
        except Exception:
            parts.append(_KEY_UNKNOWN)
    return '|'.join(parts)


def _same_key(key: str, other: str) -> bool:
    """Equal keys, or equal component + name when the tokens differ (tokens
    are not guaranteed to be stable as strings) and nothing is unknown."""
    if key == other:
        return True
    tail, other_tail = key.split('|', 1)[1], other.split('|', 1)[1]
    return tail == other_tail and _KEY_UNKNOWN not in key and _KEY_UNKNOWN not in other


def _same_native_body(a, b) -> bool:
    """Are the native bodies ``a`` and ``b`` the same body?"""
    try:
        if a == b:
            return True
    except Exception:
        pass
    return _same_key(_body_key(a), _body_key(b))


def _same_as_loaded(native) -> bool:
    """Is ``native`` the body the dialog was last filled from?"""
    previous = _state.get('body_obj')
    if previous is None:
        return False
    if _same_key(_body_key(native), _state.get('body_key') or ''):
        return True
    try:
        return bool(previous.isValid and previous == native)
    except Exception:
        return False


def _sync_body(inputs) -> None:
    """Make the dialog reflect the body in the Body input. Fusion fills the
    input itself when a body was selected before the command started, and
    that fires no inputChanged event (and may happen after activate), so this
    runs from every event that can follow a selection change and only does
    work when the body actually differs from the one the dialog was filled
    from."""
    body = _selected_body(inputs)
    native = geometry.native_of(body) if body is not None else None
    previous = _state.get('body_obj')
    if native is None and previous is None:
        return
    if native is not None and previous is not None and _same_as_loaded(native):
        return
    _on_body_changed(inputs)


def _on_body_changed(inputs) -> None:
    """Re-read model data and the saved spec for the newly selected body,
    pre-fill the dialog (saved spec > model > last used > defaults) and
    restore the masked-face selection from attributes. Any failure is shown
    on the status line and the next event retries."""
    body = _selected_body(inputs)
    faces_input = adsk.core.SelectionCommandInput.cast(inputs.itemById(ID_MASK_FACES))
    if body is None:
        _state['body_obj'] = None
        _state['body_key'] = ''
        faces_input.clearSelection()
        _set_box(inputs, ID_INFO, 'Surface area and envelope appear here once a body is selected.')
        _update_mask_summary(inputs)
        return
    native = geometry.native_of(body)
    if _state.get('body_obj') is not None and _same_as_loaded(native):
        return
    _state['body_obj'] = native
    _state['body_key'] = _body_key(native)
    try:
        _load_body(inputs, body, native, faces_input)
    except Exception as e:
        _state['body_obj'] = None            # retry on the next event
        _state['body_key'] = ''
        futil.handle_error('load body')
        _set_box(inputs, ID_INFO, 'Could not read the body: ' + html.escape(str(e)[:200]))


def _load_body(inputs, body, native, faces_input) -> None:
    futil.log(f'{CMD_NAME}: body changed to {native.name!r}')
    faces_input.clearSelection()

    # Surface area and envelope first, so nothing later can keep them blank.
    cache = _state.setdefault('geom_cache', {})
    try:
        cache_key = native.entityToken
    except Exception:
        cache_key = id(native)
    if cache_key not in cache:
        cache[cache_key] = (geometry.surface_area_in2(native), geometry.envelope_in(native))
    area, env = cache[cache_key]
    _set_box(inputs, ID_INFO, f'Surface area {area:.2f} in² · Envelope {format_envelope(env)}')
    futil.log(f'{CMD_NAME}: surface area {area:.2f} in², envelope {format_envelope(env)}')

    settings = persistence.load_settings()
    saved = None
    try:
        saved = persistence.read_spec_from_body(native)
    except Exception:
        futil.handle_error('read saved spec')
    spec = saved if saved is not None else AnodizeSpec()
    if saved is None:
        spec.part_name = _model_part_name(native)
        spec.part_number = _model_part_number(native)
        mapped = map_material(_model_material(native))
        spec.material = mapped or _model_material(native)
        spec.customer_project = settings.get('last_customer', '') or ''
        spec.company = settings.get('last_company') or config.DEFAULT_COMPANY
    futil.log(f'{CMD_NAME}: pre-fill from {"saved spec" if saved else "model"}: '
              f'name={spec.part_name!r} pn={spec.part_number!r} material={spec.material!r}')
    _fill_inputs_from_spec(inputs, spec)

    # Restore masked faces from attributes (as proxies when the body is one).
    try:
        occ = body.assemblyContext
        for nf in persistence.masked_faces_of(native):
            f = nf.createForAssemblyContext(occ) if occ is not None else nf
            if f is None or not faces_input.addSelection(f):
                faces_input.addSelection(nf)
    except Exception:
        futil.handle_error('restore masked faces')
    _update_mask_summary(inputs)


def _fill_inputs_from_spec(inputs, spec: AnodizeSpec) -> None:
    _set_text(inputs, ID_PART_NAME, spec.part_name)
    _set_text(inputs, ID_PART_NUMBER, spec.part_number)
    _set_text(inputs, ID_REVISION, spec.revision)
    _set_text(inputs, ID_CUSTOMER, spec.customer_project)
    _set_text(inputs, ID_COMPANY, spec.company or config.DEFAULT_COMPANY)
    qty = adsk.core.IntegerSpinnerCommandInput.cast(inputs.itemById(ID_QTY))
    qty.value = max(config.QUANTITY_MIN, min(config.QUANTITY_MAX, int(spec.quantity or 1)))
    mat_dd = _dropdown(inputs, ID_MATERIAL)
    other = inputs.itemById(ID_MATERIAL_OTHER)
    if spec.material in config.MATERIAL_OPTIONS:
        _select_item(mat_dd, spec.material)
        other.isVisible = False
        _set_text(inputs, ID_MATERIAL_OTHER, '')
    elif spec.material:
        _select_item(mat_dd, MATERIAL_OTHER)
        other.isVisible = True
        _set_text(inputs, ID_MATERIAL_OTHER, spec.material)
    else:
        _select_item(mat_dd, config.MATERIAL_OPTIONS[0])
        other.isVisible = False
    _select_item(_dropdown(inputs, ID_TEMPER), spec.temper or config.DEFAULT_TEMPER)
    _apply_process_fields(inputs, spec)
    _select_item(_dropdown(inputs, ID_SEAL), spec.seal or config.DEFAULT_SEAL)
    _select_item(_dropdown(inputs, ID_DIMS), spec.dims_condition or config.DEFAULT_DIMS_CONDITION)
    _set_text(inputs, ID_SPEC_STD, spec.spec_standard or config.DEFAULT_SPEC)
    _set_box(inputs, ID_MASK_NOTE, spec.masking_note or '', formatted=False)
    _set_box(inputs, ID_VENDOR_NOTES, spec.vendor_notes if spec.vendor_notes is not None else config.DEFAULT_VENDOR_NOTE,
             formatted=False)
    adsk.core.BoolValueCommandInput.cast(inputs.itemById(ID_FLIP)).value = bool(spec.flip_view)
    _select_item(_dropdown(inputs, ID_PRESET), _state['presets'][0]['label'])


def _apply_process_fields(inputs, spec: AnodizeSpec, brite: bool = True) -> None:
    """Type / Class / Color / thickness (and brite dip) from a spec."""
    _select_item(_dropdown(inputs, ID_TYPE), spec.anodize_type or config.DEFAULT_ANODIZE_TYPE)
    _select_item(_dropdown(inputs, ID_CLASS), spec.anodize_class or config.DEFAULT_ANODIZE_CLASS)
    _set_text(inputs, ID_COLOR, (spec.color or '').upper())
    _set_text(inputs, ID_THICK_MIN, format_thickness(spec.thickness_min_in))
    _set_text(inputs, ID_THICK_MAX, format_thickness(spec.thickness_max_in))
    if brite:
        _set_brite(inputs, spec.brite_dip)


def _set_brite(inputs, value) -> None:
    dd = _dropdown(inputs, ID_BRITE)
    custom = adsk.core.IntegerSpinnerCommandInput.cast(inputs.itemById(ID_BRITE_CUSTOM))
    v = normalize_brite_dip(value)
    if v is None:
        _select_item(dd, BRITE_CHOOSE)
        custom.isVisible = False
    elif v == BRITE_NONE:
        _select_item(dd, BRITE_NO)
        custom.isVisible = False
    elif v in config.BRITE_DIP_SECONDS_OPTIONS:
        _select_item(dd, f'{v} s')
        custom.isVisible = False
    else:
        _select_item(dd, BRITE_CUSTOM)
        custom.value = int(v)
        custom.isVisible = True


def _brite_from_inputs(inputs):
    name = _selected_name(_dropdown(inputs, ID_BRITE))
    if name == BRITE_NO:
        return BRITE_NONE
    if name == BRITE_CUSTOM:
        custom = adsk.core.IntegerSpinnerCommandInput.cast(inputs.itemById(ID_BRITE_CUSTOM))
        return int(custom.value) if custom.value > 0 else None
    m = re.match(r'^(\d+) s$', name or '')
    return int(m.group(1)) if m else None


def _spec_from_inputs(inputs) -> AnodizeSpec:
    spec = AnodizeSpec()
    spec.part_name = _get_text(inputs, ID_PART_NAME)
    spec.part_number = _get_text(inputs, ID_PART_NUMBER)
    spec.revision = _get_text(inputs, ID_REVISION)
    spec.customer_project = _get_text(inputs, ID_CUSTOMER)
    spec.company = _get_text(inputs, ID_COMPANY) or config.DEFAULT_COMPANY
    spec.quantity = int(adsk.core.IntegerSpinnerCommandInput.cast(inputs.itemById(ID_QTY)).value)
    mat = _selected_name(_dropdown(inputs, ID_MATERIAL))
    spec.material = _get_text(inputs, ID_MATERIAL_OTHER) if mat == MATERIAL_OTHER else mat
    spec.temper = _selected_name(_dropdown(inputs, ID_TEMPER))
    spec.spec_standard = _get_text(inputs, ID_SPEC_STD) or config.DEFAULT_SPEC
    spec.anodize_type = _selected_name(_dropdown(inputs, ID_TYPE))
    spec.anodize_class = _selected_name(_dropdown(inputs, ID_CLASS))
    spec.color = _get_text(inputs, ID_COLOR).upper()
    spec.thickness_min_in = parse_float(_get_text(inputs, ID_THICK_MIN))
    spec.thickness_max_in = parse_float(_get_text(inputs, ID_THICK_MAX))
    spec.seal = _selected_name(_dropdown(inputs, ID_SEAL))
    spec.brite_dip = _brite_from_inputs(inputs)
    spec.dims_condition = _selected_name(_dropdown(inputs, ID_DIMS))
    spec.masking_note = (adsk.core.TextBoxCommandInput.cast(inputs.itemById(ID_MASK_NOTE)).text or '').strip()
    spec.vendor_notes = (adsk.core.TextBoxCommandInput.cast(inputs.itemById(ID_VENDOR_NOTES)).text or '').strip()
    spec.flip_view = bool(adsk.core.BoolValueCommandInput.cast(inputs.itemById(ID_FLIP)).value)
    return spec


def _thickness_errors(inputs):
    errors = []
    for input_id, label in ((ID_THICK_MIN, 'Min thickness'), (ID_THICK_MAX, 'Max thickness')):
        text = _get_text(inputs, input_id)
        inp = adsk.core.StringValueCommandInput.cast(inputs.itemById(input_id))
        bad = bool(text) and parse_float(text) is None
        inp.isValueError = bad
        if bad:
            errors.append(f'{label} "{text}" is not a number.')
    return errors


def _update_mask_summary(inputs) -> None:
    faces = _selected_faces(inputs)
    if not faces:
        _set_box(inputs, ID_MASK_SUMMARY, 'No faces masked.')
        return
    area = sum(geometry.cm2_to_in2(float(f.area)) for f in faces)
    n = len(faces)
    _set_box(inputs, ID_MASK_SUMMARY, f'{n} face{"s" if n != 1 else ""} masked · {area:.2f} in²')


def _refresh_status(inputs) -> list:
    """Errors first (blocking), then soft warnings. Returns the error list."""
    body = _selected_body(inputs)
    spec = _spec_from_inputs(inputs)
    errors = _thickness_errors(inputs) + validate(spec, body_selected=body is not None)
    if errors:
        text = '<b>' + html.escape(errors[0]) + '</b>'
        if len(errors) > 1:
            text += f' (+{len(errors) - 1} more)'
    else:
        warnings = soft_warnings(spec)
        if warnings:
            text = 'Warning: ' + html.escape(' '.join(warnings))
        else:
            text = f'Ready. Click Generate to render the views and save the PDF. (v{config.ADDIN_VERSION})'
    _set_box(inputs, ID_STATUS, text)
    return errors


def command_input_changed(args: adsk.core.InputChangedEventArgs):
    changed = args.input
    cid = changed.id
    if cid in (ID_STATUS, ID_INFO, ID_MASK_SUMMARY):
        return      # our own writes to the read-only boxes fire this event too
    inputs = _all_inputs(args)
    futil.log(f'{CMD_NAME}: input changed: {cid}')
    if cid == ID_BODY:
        _sync_body(inputs)
    elif cid == ID_PRESET:
        label = _selected_name(_dropdown(inputs, ID_PRESET))
        preset = next((p for p in _state.get('presets', []) if p['label'] == label), None)
        futil.log(f'{CMD_NAME}: preset {label!r} -> {preset}')
        if preset:
            current = _spec_from_inputs(inputs)
            apply_preset(current, preset)
            _apply_process_fields(inputs, current, brite='brite_dip' in preset and preset['brite_dip'] is not None)
            futil.log(f'{CMD_NAME}: after preset: type={_selected_name(_dropdown(inputs, ID_TYPE))!r} '
                      f'class={_selected_name(_dropdown(inputs, ID_CLASS))!r} color={_get_text(inputs, ID_COLOR)!r} '
                      f'thickness={_get_text(inputs, ID_THICK_MIN)!r}-{_get_text(inputs, ID_THICK_MAX)!r}')
    elif cid == ID_MATERIAL:
        other = inputs.itemById(ID_MATERIAL_OTHER)
        other.isVisible = _selected_name(_dropdown(inputs, ID_MATERIAL)) == MATERIAL_OTHER
    elif cid == ID_BRITE:
        custom = inputs.itemById(ID_BRITE_CUSTOM)
        custom.isVisible = _selected_name(_dropdown(inputs, ID_BRITE)) == BRITE_CUSTOM
    elif cid == ID_MASK_FACES:
        _update_mask_summary(inputs)
    _refresh_status(inputs)


def command_validate_inputs(args: adsk.core.ValidateInputsEventArgs):
    # Cheap unless the body changed: the sync only works when it did.
    inputs = _all_inputs(args)
    _sync_body(inputs)
    errors = _refresh_status(inputs)
    args.areInputsValid = not errors


def command_selection_changed(args: adsk.core.SelectionEventArgs):
    """select / unselect: keep the body data and the mask summary current."""
    inputs = _all_inputs(args)
    _sync_body(inputs)
    _update_mask_summary(inputs)


def command_pre_select(args: adsk.core.SelectionEventArgs):
    """Only faces on the chosen body may be masked."""
    active = args.activeInput
    if active is None or active.id != ID_MASK_FACES:
        return
    inputs = _all_inputs(args)
    body = _selected_body(inputs)
    face = adsk.fusion.BRepFace.cast(args.selection.entity)
    if body is None or face is None or not _same_body(face, body):
        args.isSelectable = False


def command_execute(args: adsk.core.CommandEventArgs):
    """OK: persist, collect the job and hand it to the custom event."""
    inputs = _all_inputs(args)
    body = _selected_body(inputs)
    if body is None:
        args.executeFailed = True
        args.executeFailedMessage = 'No body selected.'
        return
    spec = _spec_from_inputs(inputs)
    native = geometry.native_of(body)
    faces = [geometry.native_of(f) for f in _selected_faces(inputs) if _same_body(f, body)]
    futil.log(f'{CMD_NAME} spec: {spec.to_json()}')
    doc_modified = _document_is_modified()      # before our own attribute writes
    if config.PERSIST_SPEC_IN_DESIGN:
        persistence.write_spec_to_body(native, spec)
        persistence.write_mask_attributes(native, faces)
    persistence.remember_last_used(spec)
    _state['job'] = {
        'spec': spec,
        'body': body,
        'native': native,
        'faces': faces,
        'open_pdf': bool(adsk.core.BoolValueCommandInput.cast(inputs.itemById(ID_OPEN)).value),
        'body_name': body.name,
        'doc_modified': doc_modified,
    }
    app.fireCustomEvent(config.CUSTOM_EVENT_ID, 'generate')


def command_destroy(args: adsk.core.CommandEventArgs):
    global local_handlers
    local_handlers = []


# ====================================================================== generation (after the command closed)

def on_custom_event(args: adsk.core.CustomEventArgs):
    job = _state.pop('job', None)
    if job is None:
        return
    progress = ui.progressBar
    shown = False
    tmp_dir = None
    t_start = time.perf_counter()
    try:
        spec = job['spec']
        native = job['native']
        if not native.isValid:
            raise RuntimeError('The selected body is no longer valid.')

        # Ask where to save first, so a cancel costs nothing.
        settings = persistence.load_settings()
        dialog = ui.createFileDialog()
        dialog.title = 'Save anodize spec sheet'
        dialog.filter = 'PDF files (*.pdf);;All files (*.*)'
        dialog.filterIndex = 0
        dialog.isMultiSelectEnabled = False
        last_dir = settings.get('last_save_dir') or ''
        if last_dir and Path(last_dir).is_dir():
            dialog.initialDirectory = last_dir
        dialog.initialFilename = default_filename(spec)
        if dialog.showSave() != adsk.core.DialogResults.DialogOK:
            futil.log(f'{CMD_NAME}: save cancelled')
            return
        out_path = Path(dialog.filename)
        if out_path.suffix.lower() != '.pdf':
            out_path = out_path.with_suffix('.pdf')
        persistence.save_settings({'last_save_dir': str(out_path.parent)})

        progress.show('Anodize spec sheet: %p%', 0, 100, bool(config.PROGRESS_MODAL))
        shown = True

        def report(message: str, pct: int) -> None:
            progress.message = f'{message} %p%'
            progress.progressValue = pct
            adsk.doEvents()

        report('Measuring the body…', 5)
        t0 = time.perf_counter()
        geom = geometry.compute_part_geometry(native, job['faces'], flip_view=spec.flip_view)
        futil.log(f'geometry: {time.perf_counter() - t0:.2f} s; envelope {geom.envelope_in}; '
                  f'masked {len(geom.masked_indices)} of {len(job["faces"])} faces matched')

        report('Rendering views…', 20)
        tmp_dir = Path(tempfile.mkdtemp(prefix='BivyAnodize_'))
        result, plan_img, side_img, iso_img = render.render_views(geom, job['body'], tmp_dir, report)
        for n in result.notes:
            futil.log(f'render: {n}')
        futil.log(f'render timings: {result.timings}')

        report('Cropping…', 72)
        L, W, T = geom.envelope_in
        plan_c, plan_box = raster.crop_to_content(plan_img, config.CROP_MARGIN_PX)
        side_c, side_box = raster.crop_to_content(side_img, config.CROP_MARGIN_PX)
        plan_view = sheet_layout.ViewImage(plan_c, plan_box, (L, W), flipped=not geom.view_from_plus_z)
        side_view = sheet_layout.ViewImage(side_c, side_box, (L, T))
        iso_view = None
        if iso_img is not None:
            iso_c, iso_box = raster.crop_to_content(iso_img, config.CROP_MARGIN_PX)
            iso_view = sheet_layout.iso_view_image(iso_c, iso_box, render.iso_camera(L, W, T, geom.view_from_plus_z)[2])

        report('Laying out the sheet…', 85)
        groups = [sheet_layout.MaskGroup(g.label, g.description, g.sub, g.area_in2, g.count, g.anchor_in, g.view)
                  for g in geom.groups]
        doc = app.activeDocument
        data = sheet_layout.SheetData(
            spec=spec,
            surface_area_total_in2=geom.surface_area_total_in2,
            masked_area_in2=geom.masked_area_in2,
            envelope_in=geom.envelope_in,
            mask_groups=groups,
            doc_name=doc.name if doc is not None else '',
            doc_version=_document_version_text(bool(job.get('doc_modified'))),
            body_name=job.get('body_name') or native.name,
            date_text=format_date(),
            plan=plan_view,
            side=side_view,
            iso=iso_view,
        )
        pdf, warnings = sheet_layout.draw_sheet(data)
        for w in warnings:
            futil.log(f'sheet: {w}', adsk.core.LogLevels.WarningLogLevel)

        report('Writing PDF…', 95)
        out_path.write_bytes(pdf)
        futil.log(f'{CMD_NAME}: wrote {out_path} ({len(pdf)} bytes) in {time.perf_counter() - t_start:.1f} s')
        report('Done.', 100)
        if job.get('open_pdf'):
            _open_file(out_path)
    except Exception as e:
        futil.handle_error(CMD_NAME)
        if shown:                       # never show the error box under a (modal) progress bar
            try:
                progress.hide()
            except Exception:
                pass
            shown = False
        ui.messageBox(f'The anodize spec sheet could not be generated.\n\n{e}\n\nDetails are in the Text Commands window.',
                      CMD_NAME, adsk.core.MessageBoxButtonTypes.OKButtonType, adsk.core.MessageBoxIconTypes.WarningIconType)
    finally:
        if shown:
            try:
                progress.hide()
            except Exception:
                pass
        if tmp_dir is not None:
            if config.DEBUG:
                futil.log(f'captures kept in {tmp_dir}')
            else:
                shutil.rmtree(tmp_dir, ignore_errors=True)
