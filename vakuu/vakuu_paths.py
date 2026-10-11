"""Where vakuu keeps settings, caches and call folders.

Next to the code (button-runtime/) when that folder can be written, as in a repository checkout.
A copy installed under the game's folder (Program Files, a Workshop download) usually cannot be
written by the player, so it falls back to %LOCALAPPDATA%\\vakuu.
"""
import os
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent


def _writable(folder):
    try:
        folder.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile(dir=folder): pass
        return True
    except OSError:
        return False


def _runtime():
    beside = ROOT / 'button-runtime'
    if _writable(beside): return beside
    local = Path(os.environ.get('LOCALAPPDATA') or Path.home()) / 'vakuu'
    local.mkdir(parents=True, exist_ok=True)
    return local


RUNTIME = _runtime()
