#!/usr/bin/env python3
"""Convierte el pack de texturas Render96 HD (formato sm64ex) en un .o2r para Ghostship.

Ghostship (el port de Super Mario 64 de Harbour Masters, también en iOS) carga
texturas HD desde archivos .o2r de la carpeta `mods`. Este script toma las PNG
de Render96 (que siguen los nombres de sm64ex, p. ej.
`gfx/actors/amp/amp_body.rgba16.png`) y las empaqueta con los nombres de recurso
que usa Ghostship (`actors/amp/amp_body`) como recursos `RawTexture` (RTEX),
el formato que Ghostship decodifica como PNG a resolución completa.

Uso básico:
    python3 r96_to_o2r.py RENDER96-HD-TEXTURE-PACK-master.zip
    python3 r96_to_o2r.py carpeta_del_pack/ -o Render96-HD.o2r

Opcional (requiere Pillow):  --max-size 512  reduce las texturas cuyo lado más
grande supere ese valor. Útil en iPhone/iPad con poca RAM.

Solo usa la biblioteca estándar de Python 3.8+ (Pillow únicamente para --max-size).
"""
import argparse
import io
import json
import os
import re
import struct
import sys
import zipfile

HERE = os.path.dirname(os.path.abspath(__file__))
DEFAULT_MAP = os.path.join(HERE, "data", "texture_map.json")

RES_TYPE_RAW_TEXTURE = 0x52544558  # 'RTEX'  (Ghostship: SM64::ResourceType::RawTexture)
RES_VERSION = 0                    # ResourceFactoryBinaryRawTextureV0
TEXTYPE_RGBA32 = 1                 # Fast::TextureType::RGBA32bpp
PNG_MAGIC = b"\x89PNG\r\n\x1a\n"

FORMATS = ("rgba16", "rgba32", "ia1", "ia4", "ia8", "ia16", "i4", "i8", "ci4", "ci8")
FMT_RE = re.compile(r"\.(%s)$" % "|".join(FORMATS))

# Prioridad de cada forma de resolver un nombre (menor = mejor).
P_EXACT, P_RULE, P_FALLBACK, P_ALIAS = 0, 1, 2, 3
P_NAMES = {P_EXACT: "exacto", P_RULE: "regla", P_FALLBACK: "tabla", P_ALIAS: "duplicado"}


def otr_header():
    """Cabecera OTR de 64 bytes, igual a la que escribe Torch (BaseExporter::WriteHeader)."""
    h = struct.pack(
        "<bbbbIIQIQI",
        0,                       # 0x00 endianness: 0 = little
        1,                       # 0x01 asset custom
        0, 0,                    # 0x02-0x03
        RES_TYPE_RAW_TEXTURE,    # 0x04 tipo de recurso
        RES_VERSION,             # 0x08 versión
        0xDEADBEEFDEADBEEF,      # 0x0C id
        0,                       # 0x14
        0,                       # 0x18 ROM CRC
        0,                       # 0x20 ROM enum
    )
    return h.ljust(64, b"\0")


def png_size(data):
    if data[:8] != PNG_MAGIC or data[12:16] != b"IHDR":
        raise ValueError("no es un PNG válido")
    return struct.unpack(">II", data[16:24])


def make_resource(png):
    w, h = png_size(png)
    body = struct.pack("<IIII", TEXTYPE_RGBA32, w, h, len(png))
    return otr_header() + body + png


def shrink(png, max_size):
    from PIL import Image  # import diferido: solo hace falta con --max-size

    im = Image.open(io.BytesIO(png))
    if max(im.size) <= max_size:
        return png
    im = im.convert("RGBA")
    scale = max_size / float(max(im.size))
    im = im.resize((max(1, round(im.width * scale)), max(1, round(im.height * scale))), Image.LANCZOS)
    out = io.BytesIO()
    im.save(out, "PNG", optimize=True)
    return out.getvalue()


# --------------------------------------------------------------------------- entrada

def iter_pngs(src):
    """Devuelve (nombre_sm64ex, lector) para cada PNG bajo una carpeta `gfx/`."""
    def key_of(path):
        path = path.replace("\\", "/")
        i = path.rfind("gfx/")
        if i < 0 or not path.lower().endswith(".png"):
            return None
        return path[i + 4: -4]

    if os.path.isdir(src):
        for dirpath, _, files in os.walk(src):
            for f in sorted(files):
                full = os.path.join(dirpath, f)
                k = key_of(os.path.relpath(full, src))
                if k:
                    yield k, (lambda p=full: open(p, "rb").read())
    elif zipfile.is_zipfile(src):
        zf = zipfile.ZipFile(src)
        for info in zf.infolist():
            if info.is_dir():
                continue
            k = key_of(info.filename)
            if k:
                yield k, (lambda i=info: zf.read(i))
    else:
        sys.exit("Entrada no válida: %s (debe ser una carpeta o un .zip)" % src)


# --------------------------------------------------------------------------- mapeo

def candidates(name, textures, fallback):
    """Rutas de Ghostship para un nombre sm64ex (sin `gfx/` ni `.png`)."""
    base = FMT_RE.sub("", name)
    if base in textures:
        yield P_EXACT, base
        return
    found = False
    # Teselas de cielo: textures/skybox_tiles/ccm.31 -> textures/skyboxes/ccm/ccm.31
    m = re.match(r"^textures/skybox_tiles/(\w+)\.(\d+)$", base)
    if m:
        for p in ("textures/skyboxes/%s/%s.%s" % (m[1], m[1], m[2]), "levels/ending/%s.%s" % (m[1], m[2])):
            if p in textures:
                found = True
                yield P_RULE, p
    # Variantes regionales de sm64ex: main_menu_seg7_us.0AC40 -> main_menu_seg7.0AC40, 23_us -> 23
    p = re.sub(r"_(us|eu|jp|sh)(?=\.|$)", "", base)
    if p != base and p in textures:
        found = True
        yield P_RULE, p
    if not found:
        for p in fallback.get(name, ()):
            yield P_FALLBACK, p


def build_plan(src, mapping, verbose=False):
    textures = mapping["textures"]
    fallback = mapping.get("fallback", {})
    chosen = {}   # ruta Ghostship -> (prioridad, nombre origen, lector)
    unmatched = []
    total = 0
    for name, reader in iter_pngs(src):
        total += 1
        hit = False
        for prio, target in candidates(name, textures, fallback):
            hit = True
            cur = chosen.get(target)
            if cur is None or (prio, name) < (cur[0], cur[1]):
                chosen[target] = (prio, name, reader)
        if not hit:
            unmatched.append(name)

    # Texturas idénticas en la ROM: si Render96 cubre una, rellenar las demás.
    for group in mapping.get("groups", []):
        best = min((chosen[p] for p in group if p in chosen and chosen[p][0] <= P_RULE),
                   key=lambda c: (c[0], c[1]), default=None)
        if best is None:
            continue
        for p in group:
            if p not in chosen:
                chosen[p] = (P_ALIAS, best[1], best[2])

    return total, chosen, unmatched


# --------------------------------------------------------------------------- main

def main(argv=None):
    ap = argparse.ArgumentParser(description="Render96 HD (sm64ex) -> .o2r para Ghostship")
    ap.add_argument("source", help="carpeta o .zip del pack Render96 HD (debe contener gfx/...)")
    ap.add_argument("-o", "--output", default="Render96-HD.o2r", help="archivo de salida (por defecto: %(default)s)")
    ap.add_argument("--map", default=DEFAULT_MAP, help="tabla de nombres (por defecto: data/texture_map.json)")
    ap.add_argument("--max-size", type=int, default=0, metavar="PX",
                    help="reducir texturas a este lado máximo (requiere Pillow); 0 = sin cambios")
    ap.add_argument("--name", default="Render96 HD Textures", help="nombre mostrado en el gestor de mods")
    ap.add_argument("--report", metavar="ARCHIVO", help="guardar un informe JSON del mapeo")
    ap.add_argument("-v", "--verbose", action="store_true")
    a = ap.parse_args(argv)

    with open(a.map, encoding="utf-8") as fh:
        mapping = json.load(fh)

    total, chosen, unmatched = build_plan(a.source, mapping, a.verbose)
    if not chosen:
        sys.exit("No se encontró ninguna textura reconocible en %s. ¿Contiene la carpeta gfx/?" % a.source)

    if a.max_size:
        try:
            import PIL  # noqa: F401
        except ImportError:
            sys.exit("--max-size requiere Pillow:  python3 -m pip install Pillow")

    manifest = {
        "name": a.name,
        "author": "Render96 team (texturas), conversión: Render96-4ios",
        "version": "1.0",
        "description": "Texturas HD de Render96 convertidas para Ghostship.",
        "website": "https://github.com/pokeheadroom/RENDER96-HD-TEXTURE-PACK",
    }

    tmp = a.output + ".tmp"
    counts = {}
    errors = []
    cache = {}
    with zipfile.ZipFile(tmp, "w", compression=zipfile.ZIP_STORED) as out:
        out.writestr("manifest.json", json.dumps(manifest, indent=2, ensure_ascii=False))
        for target in sorted(chosen):
            prio, name, reader = chosen[target]
            try:
                if name not in cache:
                    png = reader()
                    if a.max_size:
                        png = shrink(png, a.max_size)
                    cache.clear()  # solo se reutiliza la última (destinos consecutivos)
                    cache[name] = make_resource(png)
                out.writestr(target, cache[name])
                counts[prio] = counts.get(prio, 0) + 1
                if a.verbose:
                    print("  %-10s %s <- %s" % (P_NAMES[prio], target, name))
            except Exception as e:  # PNG corrupto, etc.
                errors.append((name, str(e)))
    os.replace(tmp, a.output)

    covered = len(chosen)
    print("PNG leídas:            %d" % total)
    print("Texturas en el .o2r:   %d de %d que tiene Ghostship" % (covered - len(errors), len(mapping["textures"])))
    for p in sorted(counts):
        print("   - %-10s %d" % (P_NAMES[p], counts[p]))
    if unmatched:
        print("Sin equivalente en Ghostship: %d (se ignoran; usa -v o --report para verlas)" % len(unmatched))
        if a.verbose:
            for n in unmatched:
                print("   ? %s" % n)
    for n, e in errors:
        print("ERROR %s: %s" % (n, e), file=sys.stderr)
    print("Listo: %s (%.1f MB)" % (a.output, os.path.getsize(a.output) / 1048576.0))

    if a.report:
        with open(a.report, "w", encoding="utf-8") as fh:
            json.dump({
                "mapped": {t: {"from": c[1], "how": P_NAMES[c[0]]} for t, c in sorted(chosen.items())},
                "unmatched": sorted(unmatched),
                "errors": errors,
            }, fh, indent=1, ensure_ascii=False)
    return 1 if errors else 0


if __name__ == "__main__":
    sys.exit(main())
