#!/usr/bin/env python3
"""
Compilador de Tailwind CSS para Skilled ERP.

Uso:
  python build_tailwind.py          → build de producción (minificado)
  python build_tailwind.py --watch  → modo desarrollo (vigila cambios)
"""

import sys
import subprocess
import os

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
INPUT  = os.path.join(BASE_DIR, "static", "css", "tailwind.input.css")
OUTPUT = os.path.join(BASE_DIR, "static", "css", "tailwind.css")

watch_mode = "--watch" in sys.argv

cmd = [
    sys.executable, "-m", "pytailwindcss",
    "-i", INPUT,
    "-o", OUTPUT,
]

if watch_mode:
    cmd.append("--watch")
    print("Modo desarrollo: vigilando cambios en templates y JS...")
else:
    cmd.append("--minify")
    print("Compilando Tailwind CSS (producción, minificado)...")

result = subprocess.run(cmd, cwd=BASE_DIR)

if result.returncode == 0 and not watch_mode:
    size_kb = os.path.getsize(OUTPUT) / 1024
    print(f"tailwind.css generado — {size_kb:.1f} KB")
