#!/usr/bin/env python3
"""Route a KiCad board: trouting board.kicad_pcb  (see trouting --help)"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.realpath(__file__)))
from trouting.cli import main

if __name__ == "__main__":     # on Windows each worker imports this file again: it must not route
    main()
