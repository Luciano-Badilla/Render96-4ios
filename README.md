# Render96 para Ghostship (iOS)

Herramientas para usar **Render96** en **Ghostship**, el port de Super Mario 64
de Harbour Masters que también funciona en iPhone y iPad.

Ghostship no puede leer directamente los archivos de Render96: Render96 se hizo para
`sm64ex`/`render96ex` y Ghostship usa archivos `.o2r` con otros nombres de recurso.
Este repositorio convierte el pack **Render96 HD Textures** a un `.o2r` que Ghostship
carga como mod.

| Parte de Render96 | Estado |
| --- | --- |
| Texturas HD (niveles, enemigos, HUD, cielos, menús) | ✅ Funciona: unas 1700 de las 2217 texturas del juego |
| Modelos 3D HD (Mario, enemigos, etc.) | ❌ Todavía no. Ver [Modelos](#modelos-3d) |
| Personajes extra (Luigi, Wario…), controles táctiles propios de Render96 | ❌ Ghostship no tiene esos recursos, así que se ignoran |

---

## Opción A: sin PC (GitHub Actions)

1. En este repositorio en GitHub, abre **Actions → Generar Render96-HD.o2r → Run workflow**.
   * `max_size`: deja `0` para la calidad original, o pon `1024` (o `512`) si tu
     iPhone tiene poca RAM o el juego se cierra solo.
2. Cuando termine, abre la ejecución y descarga el artefacto **Render96-HD-o2r**
   (es un `.zip`; la app Archivos lo descomprime con un toque).
3. Sigue con [Instalar en iOS](#instalar-en-ios).

## Opción B: con PC (Windows, macOS o Linux)

Necesitas Python 3.8 o más reciente y Pillow (`python3 -m pip install Pillow`).

1. Descarga el pack Render96 HD:
   <https://github.com/pokeheadroom/RENDER96-HD-TEXTURE-PACK> → **Code → Download ZIP**.
2. Ejecuta:

   ```sh
   python3 r96_to_o2r.py RENDER96-HD-TEXTURE-PACK-master.zip
   ```

   El script acepta el `.zip` tal como se descarga, o la carpeta ya descomprimida.
   Genera `Render96-HD.o2r`.

Opciones útiles:

```sh
# Reducir texturas grandes
python3 r96_to_o2r.py pack.zip --max-size 1024

# Ver qué archivo de Render96 se usó para cada textura de Ghostship
python3 r96_to_o2r.py pack.zip -v --report informe.json
```

## Instalar en iOS

1. Abre Ghostship una vez (con tu ROM ya convertida) para que cree sus carpetas.
2. En la app **Archivos**: *En mi iPhone → Ghostship → `mods`*.
   Si no aparece `mods`, créala.
3. Copia `Render96-HD.o2r` dentro de `mods`.
4. Cierra Ghostship por completo y vuelve a abrirlo.
5. Comprueba que esté activo en **Menú → Mods**. Ghostship marcará el mod como
   *Unsigned*. Es normal en mods hechos por usuarios y no impide que funcione.

Para quitarlo, borra el archivo de `mods` o desactívalo en el gestor de mods.

> El mismo `.o2r` sirve para Ghostship en Windows, macOS, Linux, Android y Switch.

---

## Cómo funciona

* Ghostship resuelve cada textura con una ruta como `actors/amp/amp_body`. Render96 usa
  la de sm64ex: `gfx/actors/amp/amp_body.rgba16.png`.
* `data/texture_map.json` tiene la lista completa de texturas de Ghostship (sacada de
  sus YAML de assets) y una tabla para los nombres que no siguen la regla general:
  * las teselas del cielo: `skybox_tiles/ccm.31` → `skyboxes/ccm/ccm.31`;
  * los sufijos regionales: `main_menu_seg7_us.0AC40` → `main_menu_seg7.0AC40`;
  * las fuentes, que tienen otros offsets;
  * las texturas repetidas en la ROM.
* Cada PNG se guarda como recurso **RawTexture (`RTEX`)**: una cabecera OTR de 64 bytes,
  luego tipo, ancho, alto y tamaño, y después el PNG. Ghostship
  (`src/port/importer/RawTextureFactory.cpp`) decodifica ese PNG con su resolución
  completa y el renderizador lo escala solo. Por eso las texturas HD se ven en HD y no
  quedan reducidas al tamaño de la N64.
* Excepción: cielos, HUD, fuentes, menús, fondo del título, medidor de vida y burbujas.
  El juego no los dibuja por ruta, sino a través de tablas (`AssetArray`), y Ghostship
  solo acepta recursos de tipo `Texture` en esas tablas
  (`src/port/importer/AssetArrayFactory.cpp`). Con otro tipo, el juego recibe datos
  que no entiende y dibuja basura o un cielo negro. Por eso esas texturas se guardan
  como `Texture` v1 con los píxeles RGBA ya decodificados y la bandera
  `TEX_FLAG_LOAD_AS_IMG`, comprimidas con deflate dentro del `.o2r`.
* Los mods de `mods/` se montan después de `sm64.o2r`, así que sus texturas reemplazan
  a las originales.

### Regenerar la tabla (desarrolladores)

Solo hace falta si cambian los nombres de assets en una versión nueva de Ghostship:

```sh
git clone --depth 1 https://github.com/HarbourMasters/Ghostship
git clone --depth 1 https://github.com/GhostlyDark/SM64-Reloaded   # opcional
python3 tools/build_texture_map.py --ghostship Ghostship \
    --reloaded "SM64-Reloaded/SUPER MARIO 64"
```

Los scripts de SM64 Reloaded solo se leen como texto, para sacar los grupos de texturas
idénticas y los nombres especiales de sm64ex. No se ejecutan.

## Modelos 3D

Los modelos HD de Render96 (Render96 Models / render96ex) son código C
(`model.inc.c`, `geo.inc.c`) que se compila dentro de `render96ex`. Ghostship carga
los modelos desde `.o2r` como recursos `DisplayList`, `Vertex` y `GeoLayout`, así que
sí se pueden reemplazar con un mod. Para eso habría que traducir el C de cada modelo a
esos recursos, o reexportarlo con el *fork* de fast64 de Harbour Masters, que exporta
para Ghostship. Es el siguiente paso de este proyecto.

## Problemas comunes

* **No veo cambios**: comprueba que el archivo esté en `mods` y termine en `.o2r`, no en
  `.o2r.zip`. Después cierra la app por completo.
* **El juego se cierra al entrar a un nivel**: probablemente se quedó sin memoria.
  Genera el `.o2r` con `--max-size 1024`, o con `512`.
* **Algunas texturas siguen igual**: el pack de Render96 no las incluye, o son
  exclusivas de render96ex. `--report` lista las que se ignoraron.

## Créditos

* Texturas: equipo de Render96 / pokeheadroom: <https://github.com/pokeheadroom/RENDER96-HD-TEXTURE-PACK>
* Ghostship: Harbour Masters: <https://github.com/HarbourMasters/Ghostship>
* Correspondencias de texturas idénticas: SM64 Reloaded, de GhostlyDark: <https://github.com/GhostlyDark/SM64-Reloaded>

Este repositorio no contiene texturas, ROMs ni otros assets de Nintendo. Necesitas tu
propia copia de Super Mario 64 para usar Ghostship.
