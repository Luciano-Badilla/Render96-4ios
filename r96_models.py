#!/usr/bin/env python3
"""Convierte los modelos 3D de Render96 (ModelPack, formato C de fast64) en un .o2r para Ghostship.

El ModelPack de Render96 (https://github.com/Render96/ModelPack, rama `models_vanilla`)
trae cada actor como código C del decomp de SM64:
  * model.inc.c : luces (Lights1), texturas (rutas a PNG), vértices (Vtx) y display lists (Gfx)
  * geo.inc.c   : GeoLayouts (la jerarquía del modelo: huesos, interruptores, sombras...)
  * *.png       : texturas

Este script lee ese C y genera los recursos binarios que carga Ghostship:
  * DisplayList (ODLT) en microcódigo F3D, el que usa Ghostship para SM64
  * Vertex (OVTX), Light (LGTS) y Texture (OTEX, RGBA32 con TEX_FLAG_LOAD_AS_IMG)
  * GeoLayout (Blob OBLB) en el formato que lee src/port/game/GeoLayoutParser.cpp

Los GeoLayout de Render96 cuyo nombre existe en Ghostship (p. ej. `mario_geo`) se escriben
en la misma ruta que el original (`actors/mario/geo/mario_geo`), así que reemplazan al
modelo del juego. Todo lo demás va bajo `r96/actors/<actor>/...` para no pisar nada.

Uso:
    python3 r96_models.py ModelPack/Render96/actors -o Render96-Models.o2r
    python3 r96_models.py ModelPack/Render96/actors --only mario,goomba -v

Requiere Python 3.8+ y Pillow.
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
DEFAULT_DATA = os.path.join(HERE, "data", "ghostship_models.json")
PREFIX = "r96/actors"

# --------------------------------------------------------------------------- CRC64 (libultraship StrHash64)

_CRC_TABLE = []
for _i in range(256):
    _c = _i << 56
    for _ in range(8):
        _c = ((_c << 1) ^ 0x42F0E1EBA9EA3693) if _c & (1 << 63) else (_c << 1)
    _CRC_TABLE.append(_c & 0xFFFFFFFFFFFFFFFF)


def crc64(s):
    crc = 0xFFFFFFFFFFFFFFFF
    for b in s.encode("utf-8"):
        crc = _CRC_TABLE[((crc >> 56) ^ b) & 0xFF] ^ ((crc << 8) & 0xFFFFFFFFFFFFFFFF)
    return crc


# --------------------------------------------------------------------------- recursos

T_DLIST, T_VTX, T_LIGHT, T_TEXTURE, T_BLOB = 0x4F444C54, 0x4F565458, 0x46669697, 0x4F544558, 0x4F424C42
UCODE_F3D = 1


def header(res_type, version=0):
    h = struct.pack("<bbbbIIQIQI", 0, 1, 0, 0, res_type, version, 0xDEADBEEFDEADBEEF, 0, 0, 0)
    return h.ljust(64, b"\0")


# --------------------------------------------------------------------------- constantes GBI (F3D)

CONST = {
    # formatos de imagen
    "G_IM_FMT_RGBA": 0, "G_IM_FMT_YUV": 1, "G_IM_FMT_CI": 2, "G_IM_FMT_IA": 3, "G_IM_FMT_I": 4,
    "G_IM_SIZ_4b": 0, "G_IM_SIZ_8b": 1, "G_IM_SIZ_16b": 2, "G_IM_SIZ_32b": 3,
    "G_IM_SIZ_4b_LOAD_BLOCK": 2, "G_IM_SIZ_8b_LOAD_BLOCK": 2, "G_IM_SIZ_16b_LOAD_BLOCK": 2,
    "G_IM_SIZ_32b_LOAD_BLOCK": 3,
    "G_TX_NOMIRROR": 0, "G_TX_WRAP": 0, "G_TX_MIRROR": 1, "G_TX_CLAMP": 2,
    "G_TX_NOMASK": 0, "G_TX_NOLOD": 0, "G_TX_RENDERTILE": 0, "G_TX_LOADTILE": 7,
    "G_TEXTURE_IMAGE_FRAC": 2, "G_ON": 1, "G_OFF": 0,
    # modo de geometría (valores de F3D, no F3DEX2)
    "G_ZBUFFER": 0x1, "G_SHADE": 0x4, "G_TEXTURE_ENABLE": 0x2, "G_SHADING_SMOOTH": 0x200,
    "G_CULL_FRONT": 0x1000, "G_CULL_BACK": 0x2000, "G_CULL_BOTH": 0x3000,
    "G_FOG": 0x10000, "G_LIGHTING": 0x20000, "G_TEXTURE_GEN": 0x40000,
    "G_TEXTURE_GEN_LINEAR": 0x80000, "G_LOD": 0x100000, "G_CLIPPING": 0,
    # alpha compare
    "G_AC_NONE": 0, "G_AC_THRESHOLD": 1, "G_AC_DITHER": 3,
}

# Render modes (gbi.h)
AA_EN, Z_CMP, Z_UPD, IM_RD, CLR_ON_CVG = 0x8, 0x10, 0x20, 0x40, 0x80
CVG_DST_CLAMP, CVG_DST_WRAP, CVG_DST_FULL, CVG_DST_SAVE = 0, 0x100, 0x200, 0x300
ZMODE_OPA, ZMODE_INTER, ZMODE_XLU, ZMODE_DEC = 0, 0x400, 0x800, 0xC00
CVG_X_ALPHA, ALPHA_CVG_SEL, FORCE_BL = 0x1000, 0x2000, 0x4000
BL_CLR_IN, BL_CLR_MEM, BL_CLR_BL, BL_CLR_FOG = 0, 1, 2, 3
BL_A_IN, BL_A_FOG, BL_A_SHADE, BL_0, BL_1MA, BL_A_MEM, BL_1 = 0, 1, 2, 3, 0, 1, 2


def _gbl(clk, m1a, m1b, m2a, m2b):
    if clk == 1:
        return (m1a << 30) | (m1b << 26) | (m2a << 22) | (m2b << 18)
    return (m1a << 28) | (m1b << 24) | (m2a << 20) | (m2b << 16)


def _rm(base, blend):
    return {c: base | _gbl(c, *blend) for c in (1, 2)}


_RM = {
    "AA_ZB_OPA_SURF": _rm(AA_EN | Z_CMP | Z_UPD | IM_RD | CVG_DST_CLAMP | ZMODE_OPA | ALPHA_CVG_SEL,
                          (BL_CLR_IN, BL_A_IN, BL_CLR_MEM, BL_A_MEM)),
    "AA_ZB_XLU_SURF": _rm(AA_EN | Z_CMP | IM_RD | CVG_DST_WRAP | CLR_ON_CVG | FORCE_BL | ZMODE_XLU,
                          (BL_CLR_IN, BL_A_IN, BL_CLR_MEM, BL_1MA)),
    "AA_ZB_OPA_DECAL": _rm(AA_EN | Z_CMP | IM_RD | CVG_DST_WRAP | ALPHA_CVG_SEL | ZMODE_DEC,
                           (BL_CLR_IN, BL_A_IN, BL_CLR_MEM, BL_A_MEM)),
    "AA_ZB_XLU_DECAL": _rm(AA_EN | Z_CMP | IM_RD | CVG_DST_WRAP | CLR_ON_CVG | FORCE_BL | ZMODE_DEC,
                           (BL_CLR_IN, BL_A_IN, BL_CLR_MEM, BL_1MA)),
    "AA_ZB_TEX_EDGE": _rm(AA_EN | Z_CMP | Z_UPD | IM_RD | CVG_DST_CLAMP | CVG_X_ALPHA | ALPHA_CVG_SEL | ZMODE_OPA,
                          (BL_CLR_IN, BL_A_IN, BL_CLR_MEM, BL_A_MEM)),
    "ZB_OPA_SURF": _rm(Z_CMP | Z_UPD | CVG_DST_FULL | ALPHA_CVG_SEL | ZMODE_OPA,
                       (BL_CLR_IN, BL_A_IN, BL_CLR_MEM, BL_A_MEM)),
    "ZB_XLU_SURF": _rm(Z_CMP | IM_RD | CVG_DST_FULL | FORCE_BL | ZMODE_XLU,
                       (BL_CLR_IN, BL_A_IN, BL_CLR_MEM, BL_1MA)),
    "NOOP": {1: 0, 2: 0},
}
for _name, _v in _RM.items():
    CONST["G_RM_" + _name] = _v[1]
    CONST["G_RM_" + _name + "2"] = _v[2]

CCMUX = {"COMBINED": 0, "TEXEL0": 1, "TEXEL1": 2, "PRIMITIVE": 3, "SHADE": 4, "ENVIRONMENT": 5,
         "CENTER": 6, "SCALE": 6, "COMBINED_ALPHA": 7, "TEXEL0_ALPHA": 8, "TEXEL1_ALPHA": 9,
         "PRIMITIVE_ALPHA": 10, "SHADE_ALPHA": 11, "ENV_ALPHA": 12, "LOD_FRACTION": 13,
         "PRIM_LOD_FRAC": 14, "NOISE": 7, "K4": 7, "K5": 15, "1": 6, "0": 31}
ACMUX = {"COMBINED": 0, "TEXEL0": 1, "TEXEL1": 2, "PRIMITIVE": 3, "SHADE": 4, "ENVIRONMENT": 5,
         "LOD_FRACTION": 0, "PRIM_LOD_FRAC": 6, "1": 6, "0": 7}
CC_MODES = {  # G_CC_* -> (a, b, c, d, Aa, Ab, Ac, Ad)
    "G_CC_SHADE": ("0", "0", "0", "SHADE", "0", "0", "0", "SHADE"),
    "G_CC_SHADEFADEA": ("0", "0", "0", "SHADE", "0", "0", "0", "ENVIRONMENT"),
    "G_CC_MODULATERGB": ("TEXEL0", "0", "SHADE", "0", "0", "0", "0", "SHADE"),
    "G_CC_MODULATERGBA": ("TEXEL0", "0", "SHADE", "0", "TEXEL0", "0", "SHADE", "0"),
    "G_CC_DECALRGB": ("0", "0", "0", "TEXEL0", "0", "0", "0", "SHADE"),
    "G_CC_DECALRGBA": ("0", "0", "0", "TEXEL0", "0", "0", "0", "TEXEL0"),
    "G_CC_PRIMITIVE": ("0", "0", "0", "PRIMITIVE", "0", "0", "0", "PRIMITIVE"),
}

LAYERS = {"LAYER_FORCE": 0, "LAYER_OPAQUE": 1, "LAYER_OPAQUE_DECAL": 2, "LAYER_OPAQUE_INTER": 3,
          "LAYER_ALPHA": 4, "LAYER_TRANSPARENT": 5, "LAYER_TRANSPARENT_DECAL": 6, "LAYER_TRANSPARENT_INTER": 7}
SHADOWS = {"SHADOW_CIRCLE_9_VERTS": 0, "SHADOW_CIRCLE_4_VERTS": 1, "SHADOW_CIRCLE_4_VERTS_FLAT_UNUSED": 2,
           "SHADOW_SQUARE_PERMANENT": 10, "SHADOW_SQUARE_SCALABLE": 11, "SHADOW_SQUARE_TOGGLABLE": 12,
           "SHADOW_RECTANGLE_HARDCODED_OFFSET": 50, "SHADOW_CIRCLE_PLAYER": 99}
CONST.update(LAYERS)
CONST.update(SHADOWS)
CONST.update({"TRUE": 1, "FALSE": 0, "NULL": 0})


class ConvError(Exception):
    pass


def ev(expr):
    """Evalúa una expresión C entera simple con las constantes conocidas."""
    e = expr.strip()
    e = re.sub(r"\b(0x[0-9A-Fa-f]+|\d+)[uUlL]+\b", r"\1", e)
    e = re.sub(r"\(\s*(?:s|u)(?:8|16|32)\s*\)", "", e)

    def repl(m):
        w = m.group(0)
        if w in CONST:
            return str(CONST[w])
        raise ConvError("constante desconocida: %s" % w)

    e = re.sub(r"\b[A-Za-z_][A-Za-z0-9_]*\b", repl, e)
    if not re.fullmatch(r"[0-9xXa-fA-F\s\(\)\+\-\*/<>\|&~%^]*", e):
        raise ConvError("expresión no soportada: %s" % expr)
    e = e.replace("/", "//")
    return int(eval(e, {"__builtins__": {}}, {}))  # solo números y operadores (validado arriba)


# --------------------------------------------------------------------------- parser de C

def strip_comments(src):
    src = re.sub(r"/\*.*?\*/", " ", src, flags=re.S)
    return re.sub(r"//[^\n]*", " ", src)


def split_args(s):
    out, depth, cur = [], 0, []
    for ch in s:
        if ch in "({[":
            depth += 1
        elif ch in ")}]":
            depth -= 1
        if ch == "," and depth == 0:
            out.append("".join(cur).strip())
            cur = []
        else:
            cur.append(ch)
    tail = "".join(cur).strip()
    if tail:
        out.append(tail)
    return out


def macro_calls(body):
    """Lista de (nombre, [args]) de un cuerpo tipo `{ gsFoo(a, b), gsBar(), }`."""
    calls = []
    i, n = 0, len(body)
    rx = re.compile(r"\s*,?\s*([A-Za-z_][A-Za-z0-9_]*)\s*\(")
    while i < n:
        m = rx.match(body, i)
        if not m:
            if body[i:].strip(" \t\r\n,") == "":
                break
            raise ConvError("no se pudo leer: %r" % body[i:i + 60])
        name = m.group(1)
        j = m.end()
        depth = 1
        k = j
        while depth:
            if k >= n:
                raise ConvError("paréntesis sin cerrar en %s" % name)
            if body[k] == "(":
                depth += 1
            elif body[k] == ")":
                depth -= 1
            k += 1
        calls.append((name, split_args(body[j:k - 1])))
        i = k
    return calls


DEF_RX = re.compile(
    r"(?:ALIGNED8\s+|static\s+|const\s+|UNUSED\s+)*"
    r"(Lights1|Vtx|Gfx|u8|Texture|GeoLayout)\s+([A-Za-z_][A-Za-z0-9_]*)\s*(\[[^\]]*\])?\s*=\s*", re.S)


def find_block_end(src, i):
    """Desde src[i] (que es '{' o '"' o letra), devuelve el índice del ';' que termina la definición."""
    depth = 0
    in_str = False
    while i < len(src):
        c = src[i]
        if in_str:
            if c == "\\":
                i += 1
            elif c == '"':
                in_str = False
        elif c == '"':
            in_str = True
        elif c in "({":
            depth += 1
        elif c in ")}":
            depth -= 1
        elif c == ";" and depth == 0:
            return i
        i += 1
    raise ConvError("definición sin ';'")


VTX_RX = re.compile(r"\{\s*\{\s*\{\s*([^}]*)\}\s*,\s*([^,]+),\s*\{\s*([^}]*)\}\s*,\s*\{\s*([^}]*)\}\s*\}\s*\}")


class Actor:
    def __init__(self, name, folder):
        self.name = name
        self.folder = folder
        self.lights = {}
        self.vtx = {}
        self.gfx = {}
        self.tex = {}      # símbolo -> ruta png
        self.geo = {}

    def parse_file(self, path):
        src = strip_comments(open(path, encoding="utf-8", errors="replace").read())
        src = re.sub(r"^\s*#.*$", " ", src, flags=re.M)
        pos = 0
        while True:
            m = DEF_RX.search(src, pos)
            if not m:
                break
            kind, name = m.group(1), m.group(2)
            start = m.end()
            end = find_block_end(src, start)
            body = src[start:end].strip()
            pos = end + 1
            if kind == "Lights1":
                mm = re.match(r"gdSPDefLights1\s*\((.*)\)\s*$", body, re.S)
                if not mm:
                    raise ConvError("Lights1 no soportado: %s" % name)
                self.lights[name] = [ev(x) for x in split_args(mm.group(1))]
            elif kind == "Vtx":
                verts = []
                for v in VTX_RX.finditer(body.strip()[1:-1]):
                    ob = [ev(x) for x in split_args(v.group(1))]
                    flag = ev(v.group(2))
                    tc = [ev(x) for x in split_args(v.group(3))]
                    cn = [ev(x) for x in split_args(v.group(4))]
                    verts.append((ob, flag, tc, cn))
                self.vtx[name] = verts
            elif kind == "Gfx":
                self.gfx[name] = macro_calls(body.strip()[1:-1])
            elif kind in ("u8", "Texture"):
                mm = re.match(r'"([^"]+)"', body)
                if mm:
                    self.tex[name] = self.resolve_png(mm.group(1))
            elif kind == "GeoLayout":
                self.geo[name] = macro_calls(body.strip()[1:-1])

    def resolve_png(self, ref):
        base = os.path.basename(ref)
        for cand in (base + ".png", base):
            p = os.path.join(self.folder, cand)
            if os.path.isfile(p):
                return p
        p = os.path.join(os.path.dirname(self.folder), ref.split("/", 1)[-1] + ".png")
        if os.path.isfile(p):
            return p
        raise ConvError("no encuentro la textura %s" % ref)


# --------------------------------------------------------------------------- codificación

def path_of(actor, sym):
    return "%s/%s/%s" % (PREFIX, actor.name, sym)


class Converter:
    def __init__(self, data, verbose=False):
        self.functions = data["functions"]
        self.symbols = data["symbols"]
        self.verbose = verbose
        self.out = {}          # ruta -> bytes
        self.warnings = []
        self.actors = []       # todos los actores leídos (para referencias entre actores)

    # ---- referencias
    def ref(self, actor, sym, kinds):
        """Ruta de recurso para un símbolo (propio del actor o del juego original)."""
        sym = sym.strip()
        if sym in ("NULL", "0"):
            return None
        for owner in [actor] + [x for x in self.actors if x is not actor]:
            for k in kinds:
                if sym in getattr(owner, k):
                    self.emit(owner, k, sym)
                    return path_of(owner, sym)
        if sym in self.symbols:
            return self.symbols[sym]
        raise ConvError("símbolo no encontrado: %s" % sym)

    def emit(self, actor, kind, sym):
        p = path_of(actor, sym)
        if p in self.out:
            return
        self.out[p] = None  # evita recursión infinita
        if kind == "gfx":
            self.out[p] = self.enc_gfx(actor, sym)
        elif kind == "vtx":
            self.out[p] = self.enc_vtx(actor.vtx[sym])
        elif kind == "lights":
            self.out[p] = self.enc_light(actor.lights[sym])
        elif kind == "tex":
            self.out[p] = self.enc_tex(actor.tex[sym])
        elif kind == "geo":
            self.out[p] = self.enc_geo(actor, sym)

    # ---- Vertex / Light / Texture
    @staticmethod
    def enc_vtx(verts):
        b = bytearray(header(T_VTX))
        b += struct.pack("<I", len(verts))
        for ob, flag, tc, cn in verts:
            b += struct.pack("<HHHHHHBBBB", ob[0] & 0xFFFF, ob[1] & 0xFFFF, ob[2] & 0xFFFF, flag & 0xFFFF,
                             tc[0] & 0xFFFF, tc[1] & 0xFFFF,
                             cn[0] & 0xFF, cn[1] & 0xFF, cn[2] & 0xFF, cn[3] & 0xFF)
        return bytes(b)

    @staticmethod
    def enc_light(v):
        ar, ag, ab, r, g, bb, x, y, z = v
        amb = struct.pack("<BBBbBBBb", ar, ag, ab, 0, ar, ag, ab, 0)
        lig = struct.pack("<BBBbBBBbbbbb", r, g, bb, 0, r, g, bb, 0, x, y, z, 0)
        return header(T_LIGHT) + amb + lig.ljust(16, b"\0")

    @staticmethod
    def enc_tex(png_path):
        from PIL import Image
        im = Image.open(png_path).convert("RGBA")
        px = im.tobytes()
        body = struct.pack("<IIIIffI", 1, im.width, im.height, 1 << 1, 1.0, 1.0, len(px))
        return header(T_TEXTURE, 1) + body + px

    # ---- DisplayList (F3D)
    def enc_gfx(self, actor, sym):
        words = []           # lista de (w0, w1)

        def w(w0, w1=0):
            words.append((w0 & 0xFFFFFFFF, w1 & 0xFFFFFFFF))

        def hashed(path):
            h = crc64(path)
            w(h >> 32, h & 0xFFFFFFFF)

        pending_tri = []

        def flush_tris():
            while pending_tri:
                a = pending_tri.pop(0)
                b = pending_tri.pop(0) if pending_tri else (a[0], a[0], a[0])
                w((0xB1 << 24) | (a[0] * 2 << 16) | (a[1] * 2 << 8) | (a[2] * 2),
                  (b[0] * 2 << 16) | (b[1] * 2 << 8) | (b[2] * 2))

        for name, args in actor.gfx[sym]:
            if name in ("gsSP1Triangle", "gsSP2Triangles"):
                for t in range(0, len(args) // 4):
                    idx = [ev(args[t * 4 + i]) for i in range(3)]
                    if max(idx) > 63:
                        raise ConvError("índice de vértice > 63 en %s" % sym)
                    pending_tri.append(idx)
                continue
            flush_tris()
            if name == "gsSPVertex":
                m = re.match(r"\s*([A-Za-z_][A-Za-z0-9_]*)\s*(?:\+\s*(.+))?$", args[0])
                vsym, off = m.group(1), ev(m.group(2)) if m.group(2) else 0
                n, v0 = ev(args[1]), ev(args[2])
                if n + v0 > 64:
                    raise ConvError("carga de vértices fuera de rango en %s" % sym)
                path = self.ref(actor, vsym, ["vtx"])
                w((0x32 << 24) | (n << 12) | ((v0 + n) << 1), off * 16)
                hashed(path)
            elif name == "gsSPDisplayList":
                path = self.ref(actor, args[0], ["gfx"])
                w(0x31 << 24, 0)
                hashed(path)
            elif name == "gsSPEndDisplayList":
                w(0xB8 << 24)
            elif name == "gsDPPipeSync":
                w(0xE7 << 24)
            elif name == "gsDPTileSync":
                w(0xE8 << 24)
            elif name == "gsDPLoadSync":
                w(0xE6 << 24)
            elif name == "gsDPSetTextureImage":
                fmt, siz, width = ev(args[0]), ev(args[1]), ev(args[2])
                path = self.ref(actor, args[3], ["tex"])
                w((0x20 << 24) | ((fmt & 7) << 21) | ((siz & 3) << 19) | ((width - 1) & 0xFFF), 0)
                hashed(path)
            elif name == "gsDPSetTile":
                fmt, siz, line, tmem, tile, pal, cmt, maskt, shiftt, cms, masks, shifts = [ev(a) for a in args]
                w((0xF5 << 24) | ((fmt & 7) << 21) | ((siz & 3) << 19) | ((line & 0x1FF) << 9) | (tmem & 0x1FF),
                  ((tile & 7) << 24) | ((pal & 0xF) << 20) | ((cmt & 3) << 18) | ((maskt & 0xF) << 14) |
                  ((shiftt & 0xF) << 10) | ((cms & 3) << 8) | ((masks & 0xF) << 4) | (shifts & 0xF))
            elif name in ("gsDPSetTileSize", "gsDPLoadTile"):
                tile, uls, ult, lrs, lrt = [ev(a) for a in args]
                op = 0xF2 if name == "gsDPSetTileSize" else 0xF4
                w((op << 24) | ((uls & 0xFFF) << 12) | (ult & 0xFFF),
                  ((tile & 7) << 24) | ((lrs & 0xFFF) << 12) | (lrt & 0xFFF))
            elif name == "gsDPLoadBlock":
                tile, uls, ult, lrs, dxt = [ev(a) for a in args]
                lrs = min(lrs, 2047)
                w((0xF3 << 24) | ((uls & 0xFFF) << 12) | (ult & 0xFFF),
                  ((tile & 7) << 24) | ((lrs & 0xFFF) << 12) | (dxt & 0xFFF))
            elif name == "gsSPTexture":
                s, t, level, tile, on = [ev(a) for a in args]
                w((0xBB << 24) | ((level & 7) << 11) | ((tile & 7) << 8) | (on & 0xFF),
                  ((s & 0xFFFF) << 16) | (t & 0xFFFF))
            elif name == "gsSPSetGeometryMode":
                w(0xB7 << 24, ev(args[0]))
            elif name == "gsSPClearGeometryMode":
                w(0xB6 << 24, ev(args[0]))
            elif name == "gsSPGeometryMode":
                w(0xB6 << 24, ev(args[0]))
                w(0xB7 << 24, ev(args[1]))
            elif name == "gsSPSetLights1":
                path = self.ref(actor, args[0], ["lights"])
                w((0xBC << 24) | (0x00 << 8) | 0x02, (2 * 32) + 0x80000000)   # gsSPNumLights(NUMLIGHTS_1)
                w((0x42 << 24) | (0x86 << 16) | 16, (0x86 << 24) | (1 << 8))  # gsSPLight(&l.l[0], 1)
                hashed(path)
                w((0x42 << 24) | (0x88 << 16) | 16, (0x88 << 24))             # gsSPLight(&l.a, 2)
                hashed(path)
            elif name in ("gsDPSetCombineLERP", "gsDPSetCombineMode"):
                if name == "gsDPSetCombineMode":
                    c = [CC_MODES[args[0].strip()], CC_MODES[args[1].strip()]]
                    a = list(c[0]) + list(c[1])
                else:
                    a = [x.strip() for x in args]
                cc = lambda x: CCMUX[x]  # noqa: E731
                ac = lambda x: ACMUX[x]  # noqa: E731
                w0 = ((cc(a[0]) & 0xF) << 20) | ((cc(a[2]) & 0x1F) << 15) | ((ac(a[4]) & 7) << 12) | \
                     ((ac(a[6]) & 7) << 9) | ((cc(a[8]) & 0xF) << 5) | (cc(a[10]) & 0x1F)
                w1 = ((cc(a[1]) & 0xF) << 28) | ((cc(a[3]) & 7) << 15) | ((ac(a[5]) & 7) << 12) | \
                     ((ac(a[7]) & 7) << 9) | ((cc(a[9]) & 0xF) << 24) | ((ac(a[12]) & 7) << 21) | \
                     ((ac(a[14]) & 7) << 18) | ((cc(a[11]) & 7) << 6) | ((ac(a[13]) & 7) << 3) | (ac(a[15]) & 7)
                w((0xFC << 24) | w0, w1)
            elif name in ("gsDPSetEnvColor", "gsDPSetPrimColor", "gsDPSetFogColor", "gsDPSetBlendColor"):
                if name == "gsDPSetPrimColor":
                    m, l, r, g, b, a = [ev(x) for x in args]
                    w((0xFA << 24) | ((m & 0xFF) << 8) | (l & 0xFF), (r << 24) | (g << 16) | (b << 8) | a)
                else:
                    r, g, b, a = [ev(x) for x in args]
                    op = {"gsDPSetEnvColor": 0xFB, "gsDPSetFogColor": 0xF8, "gsDPSetBlendColor": 0xF9}[name]
                    w(op << 24, (r << 24) | (g << 16) | (b << 8) | a)
            elif name == "gsDPSetAlphaCompare":
                w((0xB9 << 24) | (0 << 8) | 2, ev(args[0]))
            elif name == "gsDPSetRenderMode":
                w((0xB9 << 24) | (3 << 8) | 29, ev(args[0]) | ev(args[1]))
            else:
                raise ConvError("comando GBI no soportado: %s en %s" % (name, sym))
        flush_tris()
        if not words or words[-1][0] >> 24 != 0xB8:
            w(0xB8 << 24)

        b = bytearray(header(T_DLIST))
        b.append(UCODE_F3D)
        while len(b) % 8:
            b.append(0xFF)
        own = crc64(path_of(actor, sym))
        b += struct.pack("<IIII", 0x33 << 24, 0xBEEFBEEF, own >> 32, own & 0xFFFFFFFF)
        for w0, w1 in words:
            b += struct.pack("<II", w0, w1)
        return bytes(b)

    # ---- GeoLayout
    def func_addr(self, name, where):
        name = name.strip()
        if name in ("NULL", "0"):
            return 0
        if name not in self.functions:
            self.warnings.append("%s: función %s no existe en Ghostship; se omite" % (where, name))
            return None
        return self.functions[name]

    def enc_geo(self, actor, sym):
        out = bytearray()
        u8 = lambda v: out.extend(struct.pack("<B", v & 0xFF))  # noqa: E731
        s16 = lambda v: out.extend(struct.pack("<H", v & 0xFFFF))  # noqa: E731  (trunca como C)
        u32 = lambda v: out.extend(struct.pack("<I", v & 0xFFFFFFFF))  # noqa: E731

        def crc(s, kinds=("gfx",)):
            p = self.ref(actor, s, list(kinds))
            out.extend(struct.pack("<Q", crc64(p) if p else 0))

        def vec(args):
            for a in args:
                s16(ev(a))

        for name, args in actor.geo[sym]:
            if name == "GEO_NODE_START":
                u8(0x0B)
            elif name == "GEO_OPEN_NODE":
                u8(0x04)
            elif name == "GEO_CLOSE_NODE":
                u8(0x05)
            elif name == "GEO_END":
                u8(0x01)
            elif name == "GEO_RETURN":
                u8(0x03)
            elif name == "GEO_BRANCH":
                u8(0x02); u8(ev(args[0])); crc(args[1], ("geo",))
            elif name == "GEO_BRANCH_AND_LINK":
                u8(0x00); crc(args[0], ("geo",))
            elif name == "GEO_DISPLAY_LIST":
                u8(0x15); u8(ev(args[0])); crc(args[1])
            elif name == "GEO_ANIMATED_PART":
                u8(0x13); u8(ev(args[0])); vec(args[1:4]); crc(args[4])
            elif name == "GEO_SCALE":
                u8(0x1D); u8(ev(args[0])); u32(ev(args[1]))
            elif name == "GEO_SCALE_WITH_DL":
                u8(0x1D); u8(ev(args[0]) | 0x80); u32(ev(args[1])); crc(args[2])
            elif name == "GEO_SWITCH_CASE":
                addr = self.func_addr(args[1], sym)
                u8(0x0E); s16(ev(args[0])); u32(addr or 0)
            elif name == "GEO_ASM":
                addr = self.func_addr(args[1], sym)
                u8(0x18); s16(ev(args[0])); u32(addr or 0)
            elif name == "GEO_SHADOW":
                u8(0x16); s16(ev(args[0])); s16(ev(args[1])); s16(ev(args[2]))
            elif name == "GEO_ROTATION_NODE":
                u8(0x12); u8(ev(args[0])); vec(args[1:4])
            elif name == "GEO_ROTATION_NODE_WITH_DL":
                u8(0x12); u8(ev(args[0]) | 0x80); vec(args[1:4]); crc(args[4])
            elif name == "GEO_TRANSLATE_NODE":
                u8(0x11); u8(ev(args[0])); vec(args[1:4])
            elif name == "GEO_TRANSLATE_NODE_WITH_DL":
                u8(0x11); u8(ev(args[0]) | 0x80); vec(args[1:4]); crc(args[4])
            elif name == "GEO_TRANSLATE_ROTATE":
                u8(0x10); u8(ev(args[0])); vec(args[1:7])
            elif name == "GEO_TRANSLATE_ROTATE_WITH_DL":
                u8(0x10); u8(ev(args[0]) | 0x80); vec(args[1:7]); crc(args[7])
            elif name == "GEO_BILLBOARD":
                u8(0x14); u8(0); vec(["0", "0", "0"])
            elif name == "GEO_BILLBOARD_WITH_PARAMS":
                u8(0x14); u8(ev(args[0])); vec(args[1:4])
            elif name == "GEO_BILLBOARD_WITH_PARAMS_AND_DL":
                u8(0x14); u8(ev(args[0]) | 0x80); vec(args[1:4]); crc(args[4])
            elif name == "GEO_RENDER_RANGE":
                u8(0x0D); s16(ev(args[0])); s16(ev(args[1]))
            elif name == "GEO_CULLING_RADIUS":
                u8(0x20); s16(ev(args[0]))
            elif name == "GEO_HELD_OBJECT":
                addr = self.func_addr(args[4], sym)
                u8(0x1C); u32(addr or 0); u8(ev(args[0])); vec(args[1:4])
            else:
                raise ConvError("comando geo no soportado: %s en %s" % (name, sym))
        return header(T_BLOB) + struct.pack("<I", len(out)) + bytes(out)

    # ---- actor completo
    def convert_actor(self, actor):
        """Escribe las geos de Render96 que reemplazan a una geo de Ghostship."""
        roots = []
        for sym in actor.geo:
            target = self.symbols.get(sym)
            if target and "/geo/" in target:
                roots.append((sym, target))
        if not roots:
            return []
        before = dict(self.out)
        done = []
        try:
            for sym, target in roots:
                self.emit(actor, "geo", sym)
                done.append((sym, target))
        except ConvError:
            self.out = before
            raise
        return done


# --------------------------------------------------------------------------- main

def main(argv=None):
    ap = argparse.ArgumentParser(description="Modelos de Render96 (ModelPack) -> .o2r para Ghostship")
    ap.add_argument("source", help="carpeta Render96/actors del ModelPack (rama models_vanilla)")
    ap.add_argument("-o", "--output", default="Render96-Models.o2r")
    ap.add_argument("--data", default=DEFAULT_DATA)
    ap.add_argument("--only", help="lista de actores separada por comas (p. ej. mario,goomba)")
    ap.add_argument("--skip", default="coin",
                    help="actores a omitir, separados por comas (por defecto: coin, que necesita "
                         "geo_rotate_coin de render96ex, una función que Ghostship no tiene)")
    ap.add_argument("-v", "--verbose", action="store_true")
    a = ap.parse_args(argv)

    try:
        import PIL  # noqa: F401
    except ImportError:
        sys.exit("Falta Pillow. Instálalo con:  python3 -m pip install Pillow")

    data = json.load(open(a.data, encoding="utf-8"))
    conv = Converter(data, a.verbose)
    only = set(x.strip() for x in a.only.split(",")) if a.only else None
    skip = set(x.strip() for x in a.skip.split(",") if x.strip())

    replaced, failed = [], []
    for name in sorted(os.listdir(a.source)):
        folder = os.path.join(a.source, name)
        if not os.path.isdir(folder):
            continue
        actor = Actor(name, folder)
        try:
            for f in ("model.inc.c", "geo.inc.c"):
                p = os.path.join(folder, f)
                if os.path.isfile(p):
                    actor.parse_file(p)
        except (ConvError, ValueError, OSError) as e:
            failed.append((name, "%s: %s" % (type(e).__name__, e)))
            continue
        conv.actors.append(actor)

    for actor in conv.actors:
        name = actor.name
        if (only and name not in only) or name in skip:
            continue
        try:
            done = conv.convert_actor(actor)
        except (ConvError, struct.error, KeyError, ValueError, OSError) as e:
            failed.append((name, "%s: %s" % (type(e).__name__, e)))
            continue
        for sym, target in done:
            # La geo raíz se publica en la ruta original de Ghostship.
            conv.out[target] = conv.out[path_of(actor, sym)]
            replaced.append((name, target))

    for n, e in failed:
        print("OMITIDO %s: %s" % (n, e))
    if not replaced:
        sys.exit("No se convirtió ningún modelo.")

    manifest = {
        "name": "Render96 Models",
        "author": "Render96 team (modelos), conversión: Render96-4ios",
        "version": "1.0",
        "description": "Modelos 3D de Render96 convertidos para Ghostship.",
        "website": "https://github.com/Render96/ModelPack",
    }
    tmp = a.output + ".tmp"
    with zipfile.ZipFile(tmp, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=6) as z:
        z.writestr("manifest.json", json.dumps(manifest, indent=2, ensure_ascii=False))
        for p in sorted(conv.out):
            if conv.out[p] is not None:
                z.writestr(p, conv.out[p])
    os.replace(tmp, a.output)

    actors = sorted(set(n for n, _ in replaced))
    print("Modelos reemplazados: %d geos de %d actores" % (len(replaced), len(actors)))
    print("   " + ", ".join(actors))
    if a.verbose:
        for n, t in replaced:
            print("   %-20s -> %s" % (n, t))
    for msg in sorted(set(conv.warnings)):
        print("AVISO: " + msg)
    print("Listo: %s (%.1f MB)" % (a.output, os.path.getsize(a.output) / 1048576.0))


if __name__ == "__main__":
    main()
