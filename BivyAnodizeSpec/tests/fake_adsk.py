"""A minimal stand-in for the ``adsk`` package so the dialog module can be
exercised outside Fusion.

Only what ``commands/anodizeSpec/entry.py`` touches while a dialog is open is
modelled: command inputs (with the recursive ``itemById`` of the real
``CommandInputs``), the command events, selections, a body with a component,
a material and attributes, the application/UI singletons and the log.
Everything that needs the real kernel (rendering, temporary BReps) stays
unmodelled; tests must not reach it.

Fusion hands Python a *new wrapper object* on every API access, so
``sel.selection(0).entity`` here returns a fresh proxy each time. Equality
between proxies of the same body is configurable (``FakeBody.eq_works``) to
mimic a broken ``==``, and entity tokens can be made to differ per call
(``FakeBody.token_varies``), which are the two Fusion behaviours the dialog's
body identity logic must survive.

Use ``install()`` before importing the add-in package; it returns the fake
``app`` so a test can set the active document and selections.
"""

from __future__ import annotations

import sys
import types
from typing import Any, Dict, List, Optional


# ------------------------------------------------------------------ basics

class _Castable:
    @classmethod
    def cast(cls, obj):
        return obj if isinstance(obj, cls) else None


class _Enum:
    pass


class LogLevels(_Enum):
    InfoLogLevel = 0
    WarningLogLevel = 1
    ErrorLogLevel = 2


class LogTypes(_Enum):
    FileLogType = 0
    ConsoleLogType = 1


class DropDownStyles(_Enum):
    TextListDropDownStyle = 0


class DialogResults(_Enum):
    DialogOK = 0
    DialogCancel = 1


class MessageBoxButtonTypes(_Enum):
    OKButtonType = 0


class MessageBoxIconTypes(_Enum):
    WarningIconType = 0


# ------------------------------------------------------------------ events

class EventHandler:
    def notify(self, args):  # pragma: no cover - overridden
        raise NotImplementedError


class Event:
    """``futil.add_handler`` looks up ``sys.modules[event.__module__]`` and
    the class named by the ``handler`` annotation of ``add``."""
    __module__ = 'adsk.core'

    def __init__(self, sender):
        self.sender = sender
        self.handlers: List[EventHandler] = []

    def add(self, handler) -> bool:
        self.handlers.append(handler)
        return True

    # futil.add_handler reads add.__annotations__['handler'] as a class name.
    add.__annotations__ = {'handler': 'EventHandler', 'return': bool}

    def remove(self, handler) -> bool:
        self.handlers.remove(handler)
        return True

    def fire(self, args) -> None:
        for h in list(self.handlers):
            h.notify(args)


class CommandCreatedEventArgs:
    def __init__(self, command):
        self.command = command


class CommandEventArgs:
    def __init__(self, command):
        self.command = command
        self.executeFailed = False
        self.executeFailedMessage = ''


class InputChangedEventArgs:
    """``inputs`` is the collection the changed input belongs to (a tab's
    children for an input on a tab), as in Fusion; the command is reachable
    through ``firingEvent.sender``."""

    def __init__(self, command, changed):
        self.input = changed
        self.inputs = changed.commandInputs
        self.firingEvent = command.inputChanged


class ValidateInputsEventArgs:
    def __init__(self, command):
        self.inputs = command.commandInputs
        self.firingEvent = command.validateInputs
        self.areInputsValid = True


class _Selection:
    def __init__(self, entity):
        self.entity = entity


class SelectionEventArgs:
    def __init__(self, command, event, entity, active_input=None):
        self.firingEvent = event
        self.selection = _Selection(entity)
        self.activeInput = active_input
        self.isSelectable = True


class CustomEventArgs:
    def __init__(self, info=''):
        self.additionalInfo = info


class MarkingMenuEventArgs:
    pass


# ------------------------------------------------------------------ inputs

class CommandInput(_Castable):
    def __init__(self, inputs, input_id, name):
        self.parentCommandInputs = inputs
        self.id = input_id
        self.name = name
        self.isVisible = True
        self.isEnabled = True
        self.isFullWidth = False
        self.isValueError = False

    @property
    def commandInputs(self):
        return self.parentCommandInputs


class StringValueCommandInput(CommandInput):
    def __init__(self, inputs, input_id, name, value):
        super().__init__(inputs, input_id, name)
        self.value = value


class TextBoxCommandInput(CommandInput):
    """Setting the text from code fires inputChanged for the box, as Fusion
    does (the Fusion log shows a flood of 'input changed: status')."""

    def __init__(self, inputs, input_id, name, text, rows, read_only):
        super().__init__(inputs, input_id, name)
        self._formatted = text
        self._text = text
        self.numRows = rows
        self.isReadOnly = read_only

    def _fire(self):
        cmd = self.parentCommandInputs.command
        if cmd is not None and cmd.inputChanged.handlers:
            cmd.inputChanged.fire(InputChangedEventArgs(cmd, self))

    @property
    def formattedText(self):
        return self._formatted

    @formattedText.setter
    def formattedText(self, value):
        self._formatted = value
        self._fire()

    @property
    def text(self):
        return self._text

    @text.setter
    def text(self, value):
        self._text = value
        self._fire()


class BoolValueCommandInput(CommandInput):
    def __init__(self, inputs, input_id, name, is_checkbox, folder, value):
        super().__init__(inputs, input_id, name)
        self.value = value


class IntegerSpinnerCommandInput(CommandInput):
    def __init__(self, inputs, input_id, name, lo, hi, step, value):
        super().__init__(inputs, input_id, name)
        self.minimumValue, self.maximumValue, self.spinStep = lo, hi, step
        self.value = value


class ListItem:
    def __init__(self, items, name, selected, icon):
        self._items = items
        self.name = name
        self.icon = icon
        self._selected = False
        if selected:
            self.isSelected = True

    @property
    def isSelected(self):
        return self._selected

    @isSelected.setter
    def isSelected(self, value):
        if value:
            for other in self._items._list:
                other._selected = False
        self._selected = bool(value)


class ListItems:
    def __init__(self):
        self._list: List[ListItem] = []

    def add(self, name, is_selected, icon=''):
        item = ListItem(self, name, is_selected, icon)
        self._list.append(item)
        return item

    @property
    def count(self):
        return len(self._list)

    def item(self, i):
        return self._list[i]


class DropDownCommandInput(CommandInput):
    def __init__(self, inputs, input_id, name, style):
        super().__init__(inputs, input_id, name)
        self.dropDownStyle = style
        self.listItems = ListItems()

    @property
    def selectedItem(self):
        for it in self.listItems._list:
            if it.isSelected:
                return it
        return None


class SelectionCommandInput(CommandInput):
    def __init__(self, inputs, input_id, name, prompt):
        super().__init__(inputs, input_id, name)
        self.commandPrompt = prompt
        self.isUseCurrentSelections = True
        self.filters: List[str] = []
        self.limits = (0, 0)
        self._entities: List[Any] = []

    def addSelectionFilter(self, f):
        self.filters.append(f)

    def setSelectionLimits(self, lo, hi=0):
        self.limits = (lo, hi)

    @property
    def selectionCount(self):
        return len(self._entities)

    def selection(self, i):
        # A fresh proxy per access, as in Fusion.
        return _Selection(self._entities[i].proxy())

    def addSelection(self, entity) -> bool:
        lo, hi = self.limits
        if hi and len(self._entities) >= hi:
            return False
        self._entities.append(entity)
        return True

    def clearSelection(self):
        self._entities = []


class TabCommandInput(CommandInput):
    def __init__(self, inputs, input_id, name):
        super().__init__(inputs, input_id, name)
        self.children = CommandInputs(inputs.command)
        self.isActive = False

    def activate(self):
        self.isActive = True


class GroupCommandInput(TabCommandInput):
    pass


class CommandInputs:
    def __init__(self, command):
        self.command = command
        self._list: List[CommandInput] = []

    # -- collection
    @property
    def count(self):
        return len(self._list)

    def item(self, i):
        return self._list[i]

    def itemById(self, input_id):
        """Searches tabs and groups too, as the real one does (the official
        Command Inputs sample finds a table inside a tab this way)."""
        for inp in self._list:
            if inp.id == input_id:
                return inp
            if isinstance(inp, TabCommandInput):
                hit = inp.children.itemById(input_id)
                if hit is not None:
                    return hit
        return None

    def _add(self, inp):
        if self.itemById(inp.id) is not None:
            raise RuntimeError(f'duplicate input id {inp.id!r}')
        self._list.append(inp)
        return inp

    # -- factories used by entry.py
    def addTabCommandInput(self, input_id, name, resource=''):
        return self._add(TabCommandInput(self, input_id, name))

    def addGroupCommandInput(self, input_id, name):
        return self._add(GroupCommandInput(self, input_id, name))

    def addTextBoxCommandInput(self, input_id, name, text, rows, read_only):
        return self._add(TextBoxCommandInput(self, input_id, name, text, rows, read_only))

    def addStringValueInput(self, input_id, name, value=''):
        return self._add(StringValueCommandInput(self, input_id, name, value))

    def addBoolValueInput(self, input_id, name, is_checkbox, folder='', value=False):
        return self._add(BoolValueCommandInput(self, input_id, name, is_checkbox, folder, value))

    def addIntegerSpinnerCommandInput(self, input_id, name, lo, hi, step, value):
        return self._add(IntegerSpinnerCommandInput(self, input_id, name, lo, hi, step, value))

    def addDropDownCommandInput(self, input_id, name, style):
        return self._add(DropDownCommandInput(self, input_id, name, style))

    def addSelectionInput(self, input_id, name, prompt):
        return self._add(SelectionCommandInput(self, input_id, name, prompt))


# ------------------------------------------------------------------ command

class Command(_Castable):
    def __init__(self):
        self.commandInputs = CommandInputs(self)
        self.okButtonText = 'OK'
        self.isExecutedWhenPreEmpted = True
        self.isRepeatable = True
        self.activate = Event(self)
        self.inputChanged = Event(self)
        self.validateInputs = Event(self)
        self.preSelect = Event(self)
        self.select = Event(self)
        self.unselect = Event(self)
        self.execute = Event(self)
        self.destroy = Event(self)
        self.sizes: List[tuple] = []

    def setDialogInitialSize(self, w, h):
        self.sizes.append(('initial', w, h))

    def setDialogMinimumSize(self, w, h):
        self.sizes.append(('minimum', w, h))

    # -- helpers for tests (not API)
    def fire_activate(self):
        self.activate.fire(CommandEventArgs(self))

    def fire_input_changed(self, input_id):
        changed = self.commandInputs.itemById(input_id)
        self.inputChanged.fire(InputChangedEventArgs(self, changed))

    def fire_validate(self) -> bool:
        args = ValidateInputsEventArgs(self)
        self.validateInputs.fire(args)
        return args.areInputsValid

    def fire_select(self, entity):
        self.select.fire(SelectionEventArgs(self, self.select, entity))

    def fire_pre_select(self, entity, active_input) -> bool:
        args = SelectionEventArgs(self, self.preSelect, entity, active_input)
        self.preSelect.fire(args)
        return args.isSelectable

    def fire_execute(self):
        args = CommandEventArgs(self)
        self.execute.fire(args)
        return args


# ------------------------------------------------------------------ model objects

class _Attribute:
    def __init__(self, owner, group, name, value):
        self.parent = owner
        self.groupName, self.name, self.value = group, name, value

    def deleteMe(self):
        self.parent.attributes._items.pop((self.groupName, self.name), None)
        return True


class _Attributes:
    def __init__(self, owner):
        self._owner = owner
        self._items: Dict[tuple, _Attribute] = {}

    def itemByName(self, group, name):
        return self._items.get((group, name))

    def add(self, group, name, value):
        attr = _Attribute(self._owner, group, name, value)
        self._items[(group, name)] = attr
        return attr


class _Design:
    def __init__(self):
        self.components: List['_Component'] = []

    def findAttributes(self, group, name):
        out = []
        for comp in self.components:
            for body in comp.bodies:
                for face in body.faces:
                    a = face.attributes.itemByName(group, name)
                    if a is not None:
                        out.append(a)
        return out


class _Component:
    _next = 1

    def __init__(self, design, name, part_number=''):
        self.parentDesign = design
        self.id = f'comp-{_Component._next}'
        _Component._next += 1
        self.name = name
        self.partNumber = part_number
        self.bodies: List['FakeBody'] = []
        design.components.append(self)


class _Material:
    def __init__(self, name):
        self.name = name


class _OBB:
    def __init__(self, length, width, height):
        self.length, self.width, self.height = length, width, height


class _Entity(_Castable):
    """Shared identity semantics for bodies and faces."""
    eq_works = True
    token_varies = False

    def __init__(self):
        self._token_n = 0
        self.isValid = True
        self.nativeObject = None
        self.assemblyContext = None

    @property
    def entityToken(self):
        self._token_n += 1
        base = f'tok-{id(self._core())}'
        return f'{base}-{self._token_n}' if type(self).token_varies else base

    def _core(self):
        return self

    def __eq__(self, other):
        if not isinstance(other, _Entity):
            return NotImplemented
        if not type(self).eq_works:
            return self is other
        return self._core() is other._core()

    def __hash__(self):
        return id(self._core())


class BRepFace(_Entity):
    def __init__(self, body, area_cm2, temp_id):
        super().__init__()
        self.body = body
        self.area = area_cm2
        self.tempId = temp_id
        self.attributes = _Attributes(self)

    def createForAssemblyContext(self, occ):
        return self

    def proxy(self):
        return self


class BRepBody(_Entity):
    pass


class BRepEdge(_Entity):
    def __init__(self, body):
        super().__init__()
        self.body = body

    def proxy(self):
        return self


class BRepVertex(_Entity):
    def __init__(self, body):
        super().__init__()
        self.body = body

    def proxy(self):
        return self


class FakeBody(BRepBody):
    """A solid with a parent component, material, area, OBB and faces."""

    def __init__(self, design, component_name='Comp', body_name='Body1', part_number='',
                 material='Aluminum 6061', area_cm2=100.0, obb_cm=(10.0, 5.0, 2.0), faces=(5.0, 3.0, 2.0)):
        super().__init__()
        self.parentComponent = _Component(design, component_name, part_number)
        self.parentComponent.bodies.append(self)
        self.name = body_name
        self.material = _Material(material) if material else None
        self.area = area_cm2
        self.orientedMinimumBoundingBox = _OBB(*obb_cm)
        self.isSolid = True
        self.attributes = _Attributes(self)
        self.faces = [BRepFace(self, a, i + 1) for i, a in enumerate(faces)]
        self.edges = [BRepEdge(self)]
        self.vertices = [BRepVertex(self)]

    def findByTempId(self, temp_id):
        return [f for f in self.faces if f.tempId == temp_id]

    def proxy(self):
        return _BodyProxy(self)


class _BodyProxy(BRepBody):
    """What Fusion returns from ``selection.entity``: a new wrapper each time
    around the same body. Attribute access is forwarded."""

    def __init__(self, core):
        _Entity.__init__(self)
        object.__setattr__(self, '_core_body', core)

    def _core(self):
        return self._core_body

    def __getattr__(self, name):
        return getattr(self._core_body, name)

    @property
    def entityToken(self):
        return self._core_body.entityToken

    @property
    def nativeObject(self):
        return None

    @nativeObject.setter
    def nativeObject(self, value):
        pass


# ------------------------------------------------------------------ app / ui

class _DataFile:
    def __init__(self, name, version=3):
        self.name = name
        self.versionNumber = version


class FakeDocument:
    def __init__(self, name='Untitled', saved=False, file_name='', modified=False):
        self.name = name
        self.isSaved = saved
        self.isModified = modified
        self.dataFile = _DataFile(file_name) if saved else None


class _ActiveSelections:
    def __init__(self):
        self._items: List[Any] = []

    @property
    def count(self):
        return len(self._items)

    def item(self, i):
        return _Selection(self._items[i].proxy())

    def set(self, entities):
        self._items = list(entities)

    def clear(self):
        self._items = []
        return True


class _CommandDefinitions:
    def __init__(self):
        self._defs: Dict[str, Any] = {}

    def itemById(self, cid):
        return self._defs.get(cid)

    def addButtonDefinition(self, cid, name, desc, folder=''):
        d = types.SimpleNamespace(id=cid, name=name, commandCreated=Event(None),
                                  deleteMe=lambda: self._defs.pop(cid, None) is not None, isPromoted=False)
        self._defs[cid] = d
        return d


class FakeUI:
    def __init__(self):
        self.activeSelections = _ActiveSelections()
        self.activeCommand = 'SelectCommand'
        self.commandDefinitions = _CommandDefinitions()
        self.messages: List[tuple] = []

    def messageBox(self, text, title='', buttons=0, icon=0):
        self.messages.append((text, title))
        return 0


class FakeApp:
    _instance: Optional['FakeApp'] = None

    def __init__(self):
        self.userInterface = FakeUI()
        self.activeDocument: Optional[FakeDocument] = None
        self.activeProduct = None
        self.log_lines: List[tuple] = []
        self.custom_events: List[tuple] = []

    @classmethod
    def get(cls):
        if cls._instance is None:
            cls._instance = cls()
        return cls._instance

    def log(self, message, level=0, log_type=0):
        self.log_lines.append((level, message))

    def registerCustomEvent(self, event_id):
        ev = Event(self)
        ev.eventId = event_id
        return ev

    def unregisterCustomEvent(self, event_id):
        return True

    def fireCustomEvent(self, event_id, info=''):
        self.custom_events.append((event_id, info))
        return True


def doEvents():
    pass


# ------------------------------------------------------------------ install

def install() -> FakeApp:
    """Register ``adsk``, ``adsk.core`` and ``adsk.fusion`` in ``sys.modules``
    (idempotent) and return the application singleton."""
    if 'adsk' in sys.modules and getattr(sys.modules['adsk'], '_fake', False):
        return FakeApp.get()
    adsk = types.ModuleType('adsk')
    adsk._fake = True
    core = types.ModuleType('adsk.core')
    fusion = types.ModuleType('adsk.fusion')
    for name, obj in list(globals().items()):
        if isinstance(obj, type) or name == 'doEvents':
            setattr(core, name, obj)
    core.Application = FakeApp
    fusion.BRepBody = BRepBody
    fusion.BRepFace = BRepFace
    fusion.BRepEdge = BRepEdge
    fusion.BRepVertex = BRepVertex
    adsk.core, adsk.fusion, adsk.doEvents = core, fusion, doEvents
    sys.modules['adsk'] = adsk
    sys.modules['adsk.core'] = core
    sys.modules['adsk.fusion'] = fusion
    return FakeApp.get()
