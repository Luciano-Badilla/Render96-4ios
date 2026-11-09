#!/usr/bin/env python3
"""Regenera data/texture_map.json (solo para desarrolladores).

Fuentes:
  * Los YAML de assets de Ghostship (assets/ymls/{us,jp}): lista autoritativa de
    cada recurso TEXTURE que Ghostship carga, con su ruta dentro de sm64.o2r.
    La ruta del recurso es  <ruta del yml sin .yml>/<clave>.
  * Opcional: los scripts ghostship.sh / sm64ex.sh de SM64 Reloaded
    (https://github.com/GhostlyDark/SM64-Reloaded). Se leen como TEXTO (nunca se
    ejecutan) para obtener: (a) grupos de texturas idénticas en la ROM, y
    (b) nombres de sm64ex que no siguen la regla general (sufijos _us, etc.).

Uso:
  python3 tools/build_texture_map.py --ghostship /ruta/Ghostship \
      [--reloaded "/ruta/SM64-Reloaded/SUPER MARIO 64"] [-o data/texture_map.json]
"""
import argparse
import json
import os
import re
import sys

FORMATS = ("rgba16", "rgba32", "ia4", "ia8", "ia16", "i4", "i8", "ci4", "ci8")
FMT_RE = re.compile(r"\.(%s)$" % "|".join(FORMATS))
# Texturas exclusivas de otros personajes de sm64ex/Render96: nunca deben
# reemplazar a las de Mario aunque compartan la textura original.
CHARACTER_SPECIFIC = re.compile(r"luigi|wario", re.I)


def parse_ymls(root):
    textures = {}
    for dirpath, _, files in os.walk(root):
        for name in sorted(files):
            if not name.endswith(".yml"):
                continue
            path = os.path.join(dirpath, name)
            base = os.path.relpath(path, root)[: -len(".yml")].replace(os.sep, "/")
            key = None
            entry = {}

            def flush():
                if key and entry.get("type") == "TEXTURE":
                    textures.setdefault(base + "/" + key, {
                        "fmt": entry.get("format", "").lower(),
                        "w": int(entry.get("width", 0)),
                        "h": int(entry.get("height", 0)),
                    })

            with open(path, encoding="utf-8") as fh:
                for line in fh:
                    line = line.rstrip("\n")
                    m = re.match(r"^([^\s:#][^:]*):\s*$", line)
                    if m:
                        flush()
                        key = m.group(1).strip().strip("'\"")
                        entry = {}
                        if key.startswith(":"):
                            key = None
                        continue
                    m = re.match(r"^\s+(type|format|width|height):\s*(\S+)", line)
                    if m and key:
                        entry[m.group(1)] = m.group(2)
                flush()
    return textures


def parse_reloaded(script):
    """hash -> set(rutas destino) de un script de SM64 Reloaded (leído como texto)."""
    out = {}
    rx = re.compile(r'^cp "_temp/SUPER MARIO 64#(\S+?)\$EXT" "\$PORT/(.+)\$EXT"')
    with open(script, encoding="utf-8") as fh:
        for line in fh:
            m = rx.match(line)
            if m:
                out.setdefault(m.group(1), set()).add(m.group(2))
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--ghostship", required=True)
    ap.add_argument("--reloaded")
    ap.add_argument("-o", "--output", default=os.path.join(os.path.dirname(__file__), "..", "data", "texture_map.json"))
    a = ap.parse_args()

    textures = {}
    for region in ("us", "jp"):
        root = os.path.join(a.ghostship, "assets", "ymls", region)
        if os.path.isdir(root):
            for k, v in parse_ymls(root).items():
                textures.setdefault(k, v)
    if not textures:
        sys.exit("No se encontraron YAML de assets en %s" % a.ghostship)

    groups = []      # grupos de rutas Ghostship con la misma textura original
    fallback = {}    # nombre sm64ex (sin gfx/ ni .png) -> [rutas Ghostship]
    if a.reloaded:
        gs = parse_reloaded(os.path.join(a.reloaded, "ghostship.sh"))
        ex = parse_reloaded(os.path.join(a.reloaded, "sm64ex.sh"))
        for h, paths in gs.items():
            p = sorted(x for x in paths if x in textures)
            if len(p) > 1:
                groups.append(p)
        for h, paths in ex.items():
            targets = sorted(x for x in gs.get(h, ()) if x in textures)
            if not targets:
                continue
            for sp in paths:
                if not sp.startswith("gfx/") or CHARACTER_SPECIFIC.search(sp):
                    continue
                name = sp[4:]
                rule = FMT_RE.sub("", name)
                if rule in textures:
                    continue  # ya lo resuelve la regla general
                fallback.setdefault(name, set()).update(targets)
        fallback = {k: sorted(v) for k, v in sorted(fallback.items())}
        groups.sort()

    data = {
        "_comment": "Generado por tools/build_texture_map.py. No editar a mano.",
        "textures": dict(sorted(textures.items())),
        "groups": groups,
        "fallback": fallback,
    }
    with open(a.output, "w", encoding="utf-8") as fh:
        json.dump(data, fh, indent=1, sort_keys=False)
        fh.write("\n")
    print("texturas: %d  grupos: %d  fallback: %d -> %s" % (len(textures), len(groups), len(fallback), a.output))


if __name__ == "__main__":
    main()
