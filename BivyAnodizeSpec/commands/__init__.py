# The commands this add-in provides. Each command lives in its own folder with
# an entry.py that implements start() and stop(); Fusion calls run()/stop() in
# BivyAnodizeSpec.py, which calls the functions below.

from .anodizeSpec import entry as anodizeSpec

commands = [
    anodizeSpec,
]


def start():
    for command in commands:
        command.start()


def stop():
    for command in commands:
        command.stop()
