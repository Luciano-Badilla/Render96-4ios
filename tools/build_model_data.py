#!/usr/bin/env python3
"""Regenera data/ghostship_models.json (solo para desarrolladores).

Lee de un clon de Ghostship:
  * include/assets/**.h  -> símbolo C de cada asset de actores -> ruta del recurso en sm64.o2r
  * src/port/game/GeoLayoutParser.cpp -> nombre de función geo -> dirección en la ROM US
    (Ghostship guarda las funciones de los GeoLayout como direcciones de la ROM US).

Uso:
  python3 tools/build_model_data.py --ghostship /ruta/Ghostship [-o data/ghostship_models.json]
"""
import argparse
import json
import os
import re


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--ghostship", required=True)
    ap.add_argument("-o", "--output",
                    default=os.path.join(os.path.dirname(__file__), "..", "data", "ghostship_models.json"))
    a = ap.parse_args()

    symbols = {}
    rx = re.compile(r'char ([A-Za-z0-9_]+)\[\] = "__OTR__((?:actors|levels/common|textures)/[^"]+)"')
    for dirpath, _, files in os.walk(os.path.join(a.ghostship, "include", "assets")):
        for f in sorted(files):
            if f.endswith(".h"):
                with open(os.path.join(dirpath, f), encoding="utf-8", errors="replace") as fh:
                    for m in rx.finditer(fh.read()):
                        symbols.setdefault(m.group(1), m.group(2))

    functions = {}
    src = open(os.path.join(a.ghostship, "src", "port", "game", "GeoLayoutParser.cpp"), encoding="utf-8").read()
    us = src[src.index("mUSFunctionTable"):src.index("mJPFunctionTable")]
    for addr, name in re.findall(r"\{\s*(0x[0-9a-fA-F]+),\s*FUNC\(([A-Za-z0-9_]+)\)\s*\}", us):
        functions[name] = int(addr, 16)

    data = {
        "_comment": "Generado por tools/build_model_data.py. No editar a mano.",
        "functions": dict(sorted(functions.items())),
        "symbols": dict(sorted(symbols.items())),
    }
    with open(a.output, "w", encoding="utf-8") as fh:
        json.dump(data, fh, indent=0)
        fh.write("\n")
    print("funciones: %d  símbolos: %d -> %s" % (len(functions), len(symbols), a.output))


if __name__ == "__main__":
    main()
