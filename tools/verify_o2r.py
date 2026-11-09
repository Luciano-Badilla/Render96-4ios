#!/usr/bin/env python3
"""Verifica un .o2r de modelos leyéndolo como lo hace Ghostship (libultraship + GeoLayoutParser).

Comprueba que:
  * cada DisplayList se lee hasta su G_ENDDL (F3D 0xB8), con los comandos OTR de 128 bits;
  * todos los CRC64 (vértices, texturas, luces, sub-DL, geos) apuntan a un recurso del .o2r
    o a uno de sm64.o2r conocido (data/ghostship_models.json);
  * las cargas de vértices caben en el búfer (64) y en el recurso Vertex;
  * los triángulos usan vértices cargados;
  * cada GeoLayout se recorre comando a comando sin sobrar ni faltar bytes, y las funciones
    existen en la tabla de Ghostship.

Uso: python3 tools/verify_o2r.py Render96-Models.o2r
"""
import json
import os
import struct
import sys
import zipfile

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
from r96_models import crc64, T_DLIST, T_VTX, T_LIGHT, T_TEXTURE, T_BLOB  # noqa: E402

OTR_EXPANDED = {0x20, 0x31, 0x32, 0x33, 0x36, 0x42}


def main():
    z = zipfile.ZipFile(sys.argv[1])
    data = json.load(open(os.path.join(os.path.dirname(__file__), "..", "data", "ghostship_models.json")))
    known = {}
    for p in z.namelist():
        known[crc64(p)] = p
    external = {crc64(p): p for p in data["symbols"].values()}
    funcs = set(data["functions"].values())
    types = {}
    for p in z.namelist():
        if p == "manifest.json":
            continue
        types[p] = struct.unpack("<I", z.read(p)[4:8])[0]
    errors = []
    stats = {"dl": 0, "geo": 0, "tri": 0, "ext": set()}

    def resolve(h, want, where):
        if h in known:
            p = known[h]
            if want and types.get(p) not in want:
                errors.append("%s: %s tiene tipo %08X" % (where, p, types.get(p, 0)))
            return p
        if h in external:
            stats["ext"].add(external[h])
            return external[h]
        errors.append("%s: CRC %016X no existe" % (where, h))
        return None

    vtx_count = {}
    for p, t in types.items():
        if t == T_VTX:
            vtx_count[p] = struct.unpack("<I", z.read(p)[64:68])[0]

    for p, t in sorted(types.items()):
        d = z.read(p)
        if t == T_DLIST:
            stats["dl"] += 1
            if d[64] != 1:
                errors.append("%s: ucode %d" % (p, d[64]))
            i = 72
            loaded = 0
            while True:
                if i + 8 > len(d):
                    errors.append("%s: sin G_ENDDL" % p)
                    break
                w0, w1 = struct.unpack("<II", d[i:i + 8]); i += 8
                op = w0 >> 24
                if op in OTR_EXPANDED:
                    h0, h1 = struct.unpack("<II", d[i:i + 8]); i += 8
                    h = (h0 << 32) | h1
                    if op == 0x32:
                        n = (w0 >> 12) & 0xFF
                        v0 = ((w0 >> 1) & 0x7F) - n
                        vp = resolve(h, {T_VTX}, p)
                        if vp in vtx_count and w1 // 16 + n > vtx_count[vp]:
                            errors.append("%s: carga de vértices fuera del recurso %s" % (p, vp))
                        if v0 + n > 64:
                            errors.append("%s: v0+n > 64" % p)
                        loaded = max(loaded, v0 + n)
                    elif op == 0x31:
                        resolve(h, {T_DLIST}, p)
                    elif op == 0x20:
                        resolve(h, {T_TEXTURE}, p)
                    elif op == 0x42:
                        resolve(h, {T_LIGHT}, p)
                    elif op == 0x33 and known.get(h) != p:
                        errors.append("%s: marcador con CRC ajeno" % p)
                    continue
                if op == 0xB1:
                    idx = [(w0 >> 17) & 0x7F, (w0 >> 9) & 0x7F, (w0 >> 1) & 0x7F,
                           (w1 >> 17) & 0x7F, (w1 >> 9) & 0x7F, (w1 >> 1) & 0x7F]
                    if max(idx) >= max(loaded, 1) and loaded:
                        errors.append("%s: triángulo usa vértice %d con %d cargados" % (p, max(idx), loaded))
                    stats["tri"] += 2
                if op == 0xB8:
                    break
            if i != len(d):
                errors.append("%s: %d bytes tras G_ENDDL" % (p, len(d) - i))
        elif t == T_BLOB:
            stats["geo"] += 1
            size = struct.unpack("<I", d[64:68])[0]
            g = d[68:68 + size]
            r = [0]

            def rd(fmt):
                v = struct.unpack_from("<" + fmt, g, r[0]); r[0] += struct.calcsize("<" + fmt)
                return v

            def crc(want, allow_null=False):
                (h,) = rd("Q")
                if h == 0 and allow_null:
                    return
                resolve(h, want, p)

            def fn():
                (a,) = rd("I")
                if a and a not in funcs:
                    errors.append("%s: función 0x%X desconocida" % (p, a))
            depth = 0
            while r[0] < len(g):
                (op,) = rd("B")
                if op == 0x00: crc({T_BLOB})
                elif op == 0x01: break
                elif op == 0x02: rd("B"); crc({T_BLOB})
                elif op == 0x03: break
                elif op == 0x04: depth += 1
                elif op == 0x05: depth -= 1
                elif op == 0x0B: pass
                elif op == 0x0D: rd("hh")
                elif op == 0x0E: rd("h"); fn()
                elif op == 0x10:
                    (pr,) = rd("B"); rd("hhhhhh")
                    if pr & 0x80: crc({T_DLIST})
                elif op in (0x11, 0x12, 0x14):
                    (pr,) = rd("B"); rd("hhh")
                    if pr & 0x80: crc({T_DLIST})
                elif op == 0x13: rd("B"); rd("hhh"); crc({T_DLIST}, True)
                elif op == 0x15: rd("B"); crc({T_DLIST})
                elif op == 0x16: rd("hhh")
                elif op == 0x18: rd("h"); fn()
                elif op == 0x1C: fn(); rd("B"); rd("hhh")
                elif op == 0x1D:
                    (pr,) = rd("B"); rd("I")
                    if pr & 0x80: crc({T_DLIST})
                elif op == 0x20: rd("h")
                else:
                    errors.append("%s: opcode geo 0x%02X inesperado" % (p, op)); break
            if r[0] != len(g):
                errors.append("%s: geo con %d bytes sin leer" % (p, len(g) - r[0]))
            if depth > 0 or depth < -1:
                errors.append("%s: OPEN/CLOSE desbalanceados (%d)" % (p, depth))
            # depth == -1: un CLOSE de más, igual que en el juego original (puertas).
        elif t == T_TEXTURE:
            tt, w, h, fl, hs, vs, sz = struct.unpack("<IIIIffI", d[64:92])
            if sz != w * h * 4 or len(d) != 92 + sz:
                errors.append("%s: textura mal formada" % p)

    print("DisplayLists: %d  triángulos: %d  GeoLayouts: %d  recursos externos usados: %d"
          % (stats["dl"], stats["tri"], stats["geo"], len(stats["ext"])))
    for e in errors[:50]:
        print("ERROR " + e)
    print("errores: %d" % len(errors))
    return 1 if errors else 0


if __name__ == "__main__":
    sys.exit(main())
