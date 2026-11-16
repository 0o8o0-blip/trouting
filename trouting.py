#!/usr/bin/env python3
"""Route a KiCad board: trouting board.kicad_pcb  (see trouting --help)"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.realpath(__file__)))
from trouting.cli import main

main()
