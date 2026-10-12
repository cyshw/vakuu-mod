"""Entry point the game mod starts (python vakuu_engine.py --bridge-server PORT).

The code lives in packages beside this file: core/, combat/, deck/, route/, models/, takeover/, bridge/.
Installed copies point their settings at this file, so it keeps its name.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from bridge.server import main  # noqa: E402

if __name__ == '__main__': main()
