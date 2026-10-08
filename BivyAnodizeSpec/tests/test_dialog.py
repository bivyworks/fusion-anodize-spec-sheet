"""Drive the Anodize Spec Sheet dialog through a fake ``adsk`` (tests/fake_adsk.py).

Covers behaviour seen in Fusion: the body pre-fill (part name
from the file name, part number, material, surface area and envelope), the
preset dropdown changing the Type / Class / Color / thickness fields and
*staying* changed through the validateInputs / select events that Fusion fires
afterwards, body switching, the right-click entry, attribute persistence on OK
and the masked-face preSelect filter.

Run:  python -m unittest tests.test_dialog   (from the BivyAnodizeSpec folder)
"""

import contextlib
import io
import json
import sys
import tempfile
import types
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
ADDIN = HERE.parent
sys.path.insert(0, str(HERE))

# Import the add-in folder as the package Fusion makes of it. Registering it
# by hand keeps BivyAnodizeSpec/BivyAnodizeSpec.py from shadowing the folder
# when the tests run from inside it.
if 'BivyAnodizeSpec' not in sys.modules:
    _pkg = types.ModuleType('BivyAnodizeSpec')
    _pkg.__path__ = [str(ADDIN)]
    _pkg.__package__ = 'BivyAnodizeSpec'
    sys.modules['BivyAnodizeSpec'] = _pkg

import fake_adsk  # noqa: E402

app = fake_adsk.install()

from BivyAnodizeSpec import config  # noqa: E402
from BivyAnodizeSpec.commands.anodizeSpec import entry as E  # noqa: E402
from BivyAnodizeSpec.commands.anodizeSpec import persistence as P  # noqa: E402

IN_CM = 2.54
PRESET_BLACK = 'Type III · Class 2 · Black'
PRESET_CLEAR = 'Type II · Class 1 · Clear'


def _quiet(fn, *args, **kwargs):
    with contextlib.redirect_stdout(io.StringIO()):
        return fn(*args, **kwargs)


class DialogHarness(unittest.TestCase):
    """Common setup: a saved document 'RING v4', one solid body, isolated settings."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        settings = Path(self._tmp.name) / 'anodize_spec.json'
        self._orig_settings_path = P.settings_path
        P.settings_path = lambda: settings
        fake_adsk._Entity.eq_works = True
        fake_adsk._Entity.token_varies = False
        app.log_lines.clear()
        app.custom_events.clear()
        app.userInterface.activeSelections.set([])
        app.activeDocument = fake_adsk.FakeDocument('RING v4', saved=True, file_name='RING v4')
        self.design = fake_adsk._Design()
        self.body = fake_adsk.FakeBody(self.design, component_name='Ring', body_name='Body1', part_number='BW-100',
                                       material='Aluminum 6061', area_cm2=10 * IN_CM * IN_CM,
                                       obb_cm=(10 * IN_CM, 5 * IN_CM, 1 * IN_CM))
        E._state.clear()

    def tearDown(self):
        P.settings_path = self._orig_settings_path
        self._tmp.cleanup()
        fake_adsk._Entity.eq_works = True
        fake_adsk._Entity.token_varies = False

    # -- helpers
    def open_dialog(self, preselect=None):
        if preselect is not None:
            app.userInterface.activeSelections.set([preselect])
        cmd = fake_adsk.Command()
        _quiet(E.command_created, fake_adsk.CommandCreatedEventArgs(cmd))
        _quiet(cmd.fire_activate)
        return cmd

    def text(self, cmd, input_id):
        return cmd.commandInputs.itemById(input_id).value

    def selected(self, cmd, input_id):
        item = cmd.commandInputs.itemById(input_id).selectedItem
        return item.name if item else None

    def box(self, cmd, input_id):
        return cmd.commandInputs.itemById(input_id).formattedText

    def choose(self, cmd, input_id, name):
        """What the user does: pick a list item, which makes Fusion fire
        inputChanged for that input and then validateInputs."""
        dd = cmd.commandInputs.itemById(input_id)
        for i in range(dd.listItems.count):
            if dd.listItems.item(i).name == name:
                dd.listItems.item(i).isSelected = True
                break
        else:
            self.fail(f'{name!r} is not an option of {input_id}')
        _quiet(cmd.fire_input_changed, input_id)
        _quiet(cmd.fire_validate)

    def loads(self):
        return sum(1 for _, m in app.log_lines if 'body changed to' in m)

    def process_fields(self, cmd):
        return (self.selected(cmd, E.ID_TYPE), self.selected(cmd, E.ID_CLASS), self.text(cmd, E.ID_COLOR),
                self.text(cmd, E.ID_THICK_MIN), self.text(cmd, E.ID_THICK_MAX))


class PreFill(DialogHarness):
    def test_preselected_body_fills_part_fields_and_info(self):
        cmd = self.open_dialog(preselect=self.body)
        self.assertEqual(self.text(cmd, E.ID_PART_NAME), 'RING')          # file name without ' v4'
        self.assertEqual(self.text(cmd, E.ID_PART_NUMBER), 'BW-100')
        self.assertEqual(self.selected(cmd, E.ID_MATERIAL), '6061 Aluminum')
        self.assertFalse(cmd.commandInputs.itemById(E.ID_MATERIAL_OTHER).isVisible)
        info = self.box(cmd, E.ID_INFO)
        self.assertIn('Surface area 10.00 in²', info)
        self.assertIn('Envelope', info)
        self.assertIn('10', info)
        self.assertIn('brite dip', self.box(cmd, E.ID_STATUS))      # the one blocking error
        self.choose(cmd, E.ID_BRITE, E.BRITE_NO)
        self.assertIn('Ready', self.box(cmd, E.ID_STATUS))
        self.assertIn('v' + config.ADDIN_VERSION, self.box(cmd, E.ID_STATUS))
        self.assertEqual(self.loads(), 1)

    def test_events_after_prefill_do_not_refill(self):
        cmd = self.open_dialog(preselect=self.body)
        cmd.commandInputs.itemById(E.ID_PART_NAME).value = 'EDITED'
        for _ in range(3):
            _quiet(cmd.fire_validate)
            _quiet(cmd.fire_select, self.body)
        self.assertEqual(self.text(cmd, E.ID_PART_NAME), 'EDITED')
        self.assertEqual(self.loads(), 1)

    def test_prefill_survives_broken_equality_and_varying_tokens(self):
        fake_adsk._Entity.eq_works = False
        fake_adsk._Entity.token_varies = True
        cmd = self.open_dialog(preselect=self.body)
        self.assertEqual(self.text(cmd, E.ID_PART_NAME), 'RING')
        for _ in range(5):
            _quiet(cmd.fire_validate)
        self.assertEqual(self.loads(), 1)

    def test_body_selected_inside_the_dialog(self):
        cmd = self.open_dialog()
        self.assertIn('appear here', self.box(cmd, E.ID_INFO))
        self.assertEqual(self.text(cmd, E.ID_PART_NAME), '')
        self.assertIn('Select one solid body', self.box(cmd, E.ID_STATUS))
        cmd.commandInputs.itemById(E.ID_BODY).addSelection(self.body)
        _quiet(cmd.fire_input_changed, E.ID_BODY)
        self.assertEqual(self.text(cmd, E.ID_PART_NAME), 'RING')
        self.assertIn('Surface area 10.00 in²', self.box(cmd, E.ID_INFO))

    def test_body_filled_by_fusion_without_input_changed(self):
        """A body picked before the command starts is put into the selection
        input by Fusion with no inputChanged; validateInputs must catch it."""
        cmd = self.open_dialog()
        cmd.commandInputs.itemById(E.ID_BODY).addSelection(self.body)
        _quiet(cmd.fire_validate)
        self.assertEqual(self.text(cmd, E.ID_PART_NUMBER), 'BW-100')

    def test_selected_face_edge_or_vertex_gives_its_body(self):
        """A canvas click (or right-click) selects the face under the cursor,
        not the body; the dialog must still pre-fill from that body, and the
        clicked face must not become a masked face."""
        for ent in (self.body.faces[1], self.body.edges[0], self.body.vertices[0]):
            cmd = self.open_dialog(preselect=ent)
            self.assertEqual(cmd.commandInputs.itemById(E.ID_BODY).selectionCount, 1)
            self.assertEqual(self.text(cmd, E.ID_PART_NAME), 'RING')
            self.assertEqual(cmd.commandInputs.itemById(E.ID_MASK_FACES).selectionCount, 0)
        self.assertFalse(cmd.commandInputs.itemById(E.ID_MASK_FACES).isUseCurrentSelections)

    def test_marking_menu_offers_the_command_for_a_face(self):
        class _Controls:
            def __init__(self):
                self.added = []

            def itemById(self, cid):
                return None

            def addCommand(self, cmd_def):
                self.added.append(cmd_def.id)

        class _Menu:
            def __init__(self):
                self.controls = _Controls()

        class _Args:
            def __init__(self, entities):
                self.selectedEntities = entities
                self.linearMarkingMenu = _Menu()

        app.userInterface.commandDefinitions.addButtonDefinition(E.CMD_ID, 'x', 'y')
        args = _Args([self.body.faces[0]])
        E.on_marking_menu(args)
        self.assertEqual(args.linearMarkingMenu.controls.added, [E.CMD_ID])
        other = _Args([object()])
        E.on_marking_menu(other)
        self.assertEqual(other.linearMarkingMenu.controls.added, [])
        self.assertNotIn('context_body', E._state)

    def test_document_version_text_flags_unsaved_changes(self):
        app.activeDocument = fake_adsk.FakeDocument('RING v4', saved=True, file_name='RING v4', modified=True)
        self.assertTrue(E._document_is_modified())
        self.assertEqual(E._document_version_text(True), 'v3 + unsaved changes')
        self.assertEqual(E._document_version_text(False), 'v3')
        app.activeDocument = fake_adsk.FakeDocument('Untitled', saved=False, modified=True)
        self.assertEqual(E._document_version_text(True), 'unsaved')

    def test_unsaved_document_falls_back_to_body_or_component_name(self):
        app.activeDocument = fake_adsk.FakeDocument('Untitled', saved=False)
        cmd = self.open_dialog(preselect=self.body)
        self.assertEqual(self.text(cmd, E.ID_PART_NAME), 'Ring')     # component name, body is 'Body1'
        self.body.name = 'Flange'
        other = fake_adsk.FakeBody(self.design, component_name='Comp2', body_name='Flange')
        cmd.commandInputs.itemById(E.ID_BODY).clearSelection()
        cmd.commandInputs.itemById(E.ID_BODY).addSelection(other)
        _quiet(cmd.fire_input_changed, E.ID_BODY)
        self.assertEqual(self.text(cmd, E.ID_PART_NAME), 'Flange')

    def test_switching_body_refills_and_deselecting_resets(self):
        cmd = self.open_dialog(preselect=self.body)
        other = fake_adsk.FakeBody(self.design, component_name='Plate', body_name='Body1', part_number='BW-200',
                                   material='Titanium', area_cm2=2 * IN_CM * IN_CM)
        sel = cmd.commandInputs.itemById(E.ID_BODY)
        sel.clearSelection()
        sel.addSelection(other)
        _quiet(cmd.fire_input_changed, E.ID_BODY)
        self.assertEqual(self.text(cmd, E.ID_PART_NUMBER), 'BW-200')
        self.assertEqual(self.selected(cmd, E.ID_MATERIAL), 'Titanium')
        self.assertIn('Surface area 2.00 in²', self.box(cmd, E.ID_INFO))
        self.assertEqual(self.loads(), 2)
        sel.clearSelection()
        _quiet(cmd.fire_input_changed, E.ID_BODY)
        self.assertIn('appear here', self.box(cmd, E.ID_INFO))
        self.assertIn('Select one solid body', self.box(cmd, E.ID_STATUS))

    def test_unknown_material_goes_to_other(self):
        self.body.material = fake_adsk._Material('Unobtainium')
        cmd = self.open_dialog(preselect=self.body)
        self.assertEqual(self.selected(cmd, E.ID_MATERIAL), E.MATERIAL_OTHER)
        self.assertTrue(cmd.commandInputs.itemById(E.ID_MATERIAL_OTHER).isVisible)
        self.assertEqual(self.text(cmd, E.ID_MATERIAL_OTHER), 'Unobtainium')
        self.choose(cmd, E.ID_BRITE, E.BRITE_NO)
        self.assertIn('not aluminum or titanium', self.box(cmd, E.ID_STATUS))

    def test_load_failure_is_visible_and_retried(self):
        class Broken(fake_adsk.FakeBody):
            fail = True

            @property
            def orientedMinimumBoundingBox(self):
                if type(self).fail:
                    raise RuntimeError('kernel says no')
                return fake_adsk._OBB(1, 1, 1)

            @orientedMinimumBoundingBox.setter
            def orientedMinimumBoundingBox(self, value):
                pass

        body = Broken(self.design)
        cmd = self.open_dialog(preselect=body)
        self.assertIn('Could not read the body: kernel says no', self.box(cmd, E.ID_INFO))
        Broken.fail = False
        _quiet(cmd.fire_validate)
        self.assertIn('Surface area', self.box(cmd, E.ID_INFO))


class Presets(DialogHarness):
    def test_preset_changes_fields_and_sticks(self):
        cmd = self.open_dialog(preselect=self.body)
        self.assertEqual(self.process_fields(cmd), ('Type II', 'Class 2', 'BLACK', '', ''))
        self.choose(cmd, E.ID_PRESET, PRESET_BLACK)
        self.assertEqual(self.process_fields(cmd), ('Type III', 'Class 2', 'BLACK', '0.0010', '0.0015'))
        # Fusion keeps firing validateInputs / select after the change.
        for _ in range(3):
            _quiet(cmd.fire_validate)
            _quiet(cmd.fire_select, self.body)
        self.assertEqual(self.process_fields(cmd), ('Type III', 'Class 2', 'BLACK', '0.0010', '0.0015'))
        self.assertEqual(self.selected(cmd, E.ID_PRESET), PRESET_BLACK)
        self.assertEqual(self.loads(), 1)

    def test_preset_with_null_thickness_keeps_current_thickness(self):
        cmd = self.open_dialog(preselect=self.body)
        self.choose(cmd, E.ID_PRESET, PRESET_BLACK)
        self.choose(cmd, E.ID_PRESET, PRESET_CLEAR)
        self.assertEqual(self.process_fields(cmd), ('Type II', 'Class 1', 'CLEAR', '0.0010', '0.0015'))

    def test_preset_survives_broken_equality(self):
        fake_adsk._Entity.eq_works = False
        fake_adsk._Entity.token_varies = True
        cmd = self.open_dialog(preselect=self.body)
        self.choose(cmd, E.ID_PRESET, PRESET_BLACK)
        for _ in range(3):
            _quiet(cmd.fire_validate)
        self.assertEqual(self.process_fields(cmd)[:2], ('Type III', 'Class 2'))

    def test_preset_without_body(self):
        cmd = self.open_dialog()
        self.choose(cmd, E.ID_PRESET, PRESET_BLACK)
        self.assertEqual(self.process_fields(cmd), ('Type III', 'Class 2', 'BLACK', '0.0010', '0.0015'))

    def test_custom_entry_leaves_fields_alone(self):
        cmd = self.open_dialog(preselect=self.body)
        self.choose(cmd, E.ID_PRESET, PRESET_BLACK)
        self.choose(cmd, E.ID_PRESET, '— Custom —')
        self.assertEqual(self.process_fields(cmd), ('Type III', 'Class 2', 'BLACK', '0.0010', '0.0015'))

    def test_input_changed_sees_only_the_tab_but_fields_still_apply(self):
        """Seen in Fusion: inputChanged's args.inputs is the Anodize tab's
        children, so Quantity on the Part tab is not in it."""
        cmd = self.open_dialog(preselect=self.body)
        preset = cmd.commandInputs.itemById(E.ID_PRESET)
        args = fake_adsk.InputChangedEventArgs(cmd, preset)
        self.assertIsNone(args.inputs.itemById(E.ID_QTY))
        self.assertIsNotNone(args.inputs.itemById(E.ID_TYPE))
        self.choose(cmd, E.ID_PRESET, PRESET_BLACK)
        self.assertEqual(self.process_fields(cmd), ('Type III', 'Class 2', 'BLACK', '0.0010', '0.0015'))
        self.assertFalse([m for _, m in app.log_lines if 'Error' in m], 'handler raised')

    def test_status_writes_do_not_recurse_or_log(self):
        cmd = self.open_dialog(preselect=self.body)
        app.log_lines.clear()
        _quiet(cmd.fire_validate)
        self.assertFalse([m for _, m in app.log_lines if 'input changed' in m])

    def test_dropdown_labels_match_presets_file(self):
        cmd = self.open_dialog()
        dd = cmd.commandInputs.itemById(E.ID_PRESET)
        names = [dd.listItems.item(i).name for i in range(dd.listItems.count)]
        labels = [p['label'] for p in json.loads((ADDIN / 'presets.json').read_text(encoding='utf-8'))]
        self.assertEqual(names, labels)


class MainModule(unittest.TestCase):
    def test_run_reloads_the_package_modules(self):
        import importlib
        main = importlib.import_module('BivyAnodizeSpec.BivyAnodizeSpec')
        before = sys.modules['BivyAnodizeSpec.commands.anodizeSpec.entry']
        main._load_fresh()
        self.assertIsNotNone(main._commands)
        self.assertIsNotNone(main._futil)
        after = sys.modules['BivyAnodizeSpec.commands.anodizeSpec.entry']
        self.assertIsNot(before, after)                 # imported again from disk
        self.assertIs(main._commands.anodizeSpec, after)
        self.assertIn('BivyAnodizeSpec.BivyAnodizeSpec', sys.modules)   # the main module itself is kept
        self.assertIn(__name__, sys.modules)                             # only commands/, lib/, config.py are dropped
        self.assertIn('fake_adsk', sys.modules)

    def test_fusion_style_loader_without_package(self):
        """Fusion executes the main file in a module of its own with __path__
        set and __package__ left unset (seen in Fusion: import_module
        with __package__ raised TypeError). The relative imports must still
        resolve and the reload must find the modules by location."""
        import warnings
        saved = {n: m for n, m in sys.modules.items() if n == 'BivyAnodizeSpec' or n.startswith('BivyAnodizeSpec.')}
        try:
            for n in saved:
                del sys.modules[n]
            mod = types.ModuleType('BivyAnodizeSpec')
            mod.__file__ = str(ADDIN / 'BivyAnodizeSpec.py')
            mod.__path__ = [str(ADDIN)]
            mod.__package__ = None
            mod.__spec__ = None
            sys.modules['BivyAnodizeSpec'] = mod
            code = compile((ADDIN / 'BivyAnodizeSpec.py').read_text(encoding='utf-8'), mod.__file__, 'exec')
            exec(code, mod.__dict__)
            with warnings.catch_warnings():
                warnings.simplefilter('ignore', ImportWarning)
                self.assertEqual(mod._package_name(), 'BivyAnodizeSpec')
                mod._load_fresh()
                first = mod._commands
                mod._load_fresh()
            self.assertIsNotNone(first)
            self.assertIsNot(first, mod._commands)      # second run got fresh modules
            self.assertTrue(hasattr(mod._commands, 'start'))
            self.assertTrue(hasattr(mod._futil, 'handle_error'))
            self.assertEqual(mod._commands.anodizeSpec.config.ADDIN_VERSION, config.ADDIN_VERSION)
        finally:
            for n in [n for n in sys.modules if n == 'BivyAnodizeSpec' or n.startswith('BivyAnodizeSpec.')]:
                del sys.modules[n]
            sys.modules.update(saved)


class ValidationAndExecute(DialogHarness):
    def test_brite_dip_required_and_thickness_checked(self):
        cmd = self.open_dialog(preselect=self.body)
        self.assertFalse(_quiet(cmd.fire_validate))
        self.assertIn('brite dip', self.box(cmd, E.ID_STATUS))
        self.choose(cmd, E.ID_BRITE, E.BRITE_NO)
        self.assertTrue(_quiet(cmd.fire_validate))
        self.assertIn('Ready', self.box(cmd, E.ID_STATUS))
        cmd.commandInputs.itemById(E.ID_THICK_MIN).value = 'abc'
        _quiet(cmd.fire_input_changed, E.ID_THICK_MIN)
        self.assertFalse(_quiet(cmd.fire_validate))
        self.assertTrue(cmd.commandInputs.itemById(E.ID_THICK_MIN).isValueError)
        cmd.commandInputs.itemById(E.ID_THICK_MIN).value = '0.002'
        cmd.commandInputs.itemById(E.ID_THICK_MAX).value = '0.001'
        self.assertFalse(_quiet(cmd.fire_validate))
        self.assertIn('greater than max', self.box(cmd, E.ID_STATUS))

    def test_execute_persists_and_fires_the_custom_event(self):
        cmd = self.open_dialog(preselect=self.body)
        self.choose(cmd, E.ID_PRESET, PRESET_BLACK)
        self.choose(cmd, E.ID_BRITE, '45 s')
        cmd.commandInputs.itemById(E.ID_REVISION).value = 'B'
        faces = cmd.commandInputs.itemById(E.ID_MASK_FACES)
        faces.addSelection(self.body.faces[1])
        _quiet(cmd.fire_input_changed, E.ID_MASK_FACES)
        self.assertIn('1 face masked', self.box(cmd, E.ID_MASK_SUMMARY))
        args = _quiet(cmd.fire_execute)
        self.assertFalse(args.executeFailed)
        self.assertEqual(app.custom_events, [(config.CUSTOM_EVENT_ID, 'generate')])
        saved = json.loads(self.body.attributes.itemByName(config.ATTR_GROUP, config.ATTR_SPEC_NAME).value)
        self.assertEqual((saved['part_name'], saved['revision'], saved['anodize_type'], saved['brite_dip']),
                         ('RING', 'B', 'Type III', 45))
        marks = [f.attributes.itemByName(config.ATTR_GROUP, config.ATTR_MASK_NAME) is not None for f in self.body.faces]
        self.assertEqual(marks, [False, True, False])
        self.assertEqual(E._state['job']['spec'].thickness_min_in, 0.001)
        self.assertFalse(E._state['job']['doc_modified'])
        self.assertEqual(P.load_settings()['last_company'], config.DEFAULT_COMPANY)

    def test_saved_spec_and_masks_are_restored(self):
        cmd = self.open_dialog(preselect=self.body)
        self.choose(cmd, E.ID_PRESET, PRESET_BLACK)
        self.choose(cmd, E.ID_BRITE, E.BRITE_NO)
        cmd.commandInputs.itemById(E.ID_PART_NAME).value = 'RING-SAVED'
        cmd.commandInputs.itemById(E.ID_MASK_FACES).addSelection(self.body.faces[2])
        _quiet(cmd.fire_execute)
        E._state.pop('job')
        # Reopen: the saved spec beats the model, masked faces come back.
        cmd2 = self.open_dialog(preselect=self.body)
        self.assertEqual(self.text(cmd2, E.ID_PART_NAME), 'RING-SAVED')
        self.assertEqual(self.process_fields(cmd2), ('Type III', 'Class 2', 'BLACK', '0.0010', '0.0015'))
        self.assertEqual(self.selected(cmd2, E.ID_BRITE), E.BRITE_NO)
        self.assertEqual(cmd2.commandInputs.itemById(E.ID_MASK_FACES).selectionCount, 1)
        self.assertIn('1 face masked', self.box(cmd2, E.ID_MASK_SUMMARY))
        self.assertTrue(_quiet(cmd2.fire_validate))

    def test_execute_without_body_fails(self):
        cmd = self.open_dialog()
        args = _quiet(cmd.fire_execute)
        self.assertTrue(args.executeFailed)
        self.assertEqual(app.custom_events, [])

    def test_pre_select_only_accepts_faces_of_the_body(self):
        cmd = self.open_dialog(preselect=self.body)
        # A twin body: same face tempIds and areas, so only identity may decide.
        other = fake_adsk.FakeBody(self.design, component_name='Other')
        faces_input = cmd.commandInputs.itemById(E.ID_MASK_FACES)
        self.assertTrue(_quiet(cmd.fire_pre_select, self.body.faces[0], faces_input))
        self.assertFalse(_quiet(cmd.fire_pre_select, other.faces[0], faces_input))
        # Faces are only filtered while the mask input is active.
        self.assertTrue(_quiet(cmd.fire_pre_select, other.faces[0], cmd.commandInputs.itemById(E.ID_BODY)))


if __name__ == '__main__':
    unittest.main()
