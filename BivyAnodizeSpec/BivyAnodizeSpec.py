# Bivy Works - Anodize Spec Sheet add-in entry point.
#
# Follows the structure of Fusion's "New Add-In" Python template: this file only
# delegates to commands/__init__.py, which starts and stops each command module.
#
# One addition: the add-in's own modules (commands/, lib/, config.py) are
# dropped from sys.modules and imported again on every run(). Fusion can keep
# the modules an add-in imported earlier, so a Stop / Run from the Scripts and
# Add-Ins dialog after updating the files could otherwise keep running the old
# code until Fusion is restarted.
#
# Fusion loads this file with its own loader, which leaves __package__ unset,
# so the package name is resolved the way the import system resolves the
# template's "from . import commands" (importlib._bootstrap._calc___package__),
# and the modules are imported by absolute name: a "from . import commands"
# issued after a reload would hand back the old module still attached to the
# package object instead of importing it again.

import importlib
import sys
import traceback
from pathlib import Path

_ROOT = Path(__file__).resolve().parent
_OWN = ('commands', 'lib', 'config.py')

_commands = None
_futil = None


def _package_name() -> str:
    g = globals()
    if g.get('__package__'):
        return g['__package__']
    spec = g.get('__spec__')
    if spec is not None and getattr(spec, 'parent', None):
        return spec.parent
    name = g.get('__name__') or ''
    return name if '__path__' in g else name.rpartition('.')[0]


def _is_own(module) -> bool:
    """Is this module one of the add-in's own (under commands/, lib/, or config.py)?"""
    paths = []
    try:
        f = getattr(module, '__file__', None)
        if f:
            paths.append(f)
        paths.extend(str(p) for p in (getattr(module, '__path__', None) or []))
    except Exception:
        return False
    for p in paths:
        try:
            rel = Path(p).resolve().relative_to(_ROOT)
        except (ValueError, OSError):
            continue
        if rel.parts and rel.parts[0] in _OWN:
            return True
    return False


def _drop_stale_modules() -> None:
    stale = [name for name, module in list(sys.modules.items())
             if module is not None and name != __name__ and _is_own(module)]
    for name in stale:
        sys.modules.pop(name, None)


def _load_fresh() -> None:
    global _commands, _futil
    pkg = _package_name()
    if not pkg:
        raise ImportError('cannot resolve the add-in package name (no __package__, __spec__ or __path__)')
    _drop_stale_modules()
    _commands = importlib.import_module(pkg + '.commands')
    _futil = importlib.import_module(pkg + '.lib.fusionAddInUtils')


def _report(name: str) -> None:
    if _futil is not None:
        _futil.handle_error(name)
        return
    try:                                   # the import itself failed: no futil yet
        import adsk.core
        adsk.core.Application.get().userInterface.messageBox(f'{name}\n{traceback.format_exc()}')
    except Exception:
        pass


def run(context):
    try:
        _load_fresh()
        # Runs the start() function of every command listed in commands/__init__.py.
        _commands.start()
    except:  # noqa: E722 - the template deliberately catches everything here
        _report('run')


def stop(context):
    try:
        if _commands is None:
            return
        # Release every event handler the add-in registered.
        _futil.clear_handlers()

        # Runs the stop() function of every command listed in commands/__init__.py.
        _commands.stop()
    except:  # noqa: E722
        _report('stop')
