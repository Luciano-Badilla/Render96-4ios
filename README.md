# Render96 para Ghostship (iOS)

Herramientas para usar **Render96** en **Ghostship**, el port de Super Mario 64
de Harbour Masters que también funciona en iPhone y iPad.

Ghostship no puede leer directamente los archivos de Render96: Render96 se hizo para
`sm64ex`/`render96ex` y Ghostship usa archivos `.o2r` con otros nombres de recurso.
Este repositorio convierte el pack **Render96 HD Textures** y el **ModelPack** de
Render96 a archivos `.o2r` que Ghostship carga como mods.

| Parte de Render96 | Estado |
| --- | --- |
| Texturas HD (niveles, enemigos, HUD, cielos, menús) | ✅ `Render96-HD.o2r`: unas 1700 de las 2217 texturas del juego |
| Modelos 3D (Mario, enemigos, objetos) | ✅ `Render96-Models.o2r`: 70 actores, probado en iOS |
| Monedas 3D | ❌ Necesitan `geo_rotate_coin`, una función de render96ex que Ghostship no tiene; quedan las originales |
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

## Modelos 3D

Se generan aparte, en `Render96-Models.o2r`:

* **Sin PC:** *Actions → Generar Render96-Models.o2r → Run workflow*. Para probar solo
  algunos actores, escribe en `only` algo como `mario` o `mario,goomba,bobomb`. Después
  descarga el artefacto **Render96-Models-o2r**.
* **Con PC:**

  ```sh
  git clone --depth 1 -b models_vanilla https://github.com/Render96/ModelPack.git
  python3 r96_models.py ModelPack/Render96/actors            # todos los actores
  python3 r96_models.py ModelPack/Render96/actors --only mario
  python3 tools/verify_o2r.py Render96-Models.o2r            # comprobación opcional
  ```

Copia `Render96-Models.o2r` a `mods` junto con `Render96-HD.o2r`. Los dos archivos no
se pisan entre sí.

Cómo funciona el conversor de modelos:

* Lee el C que exporta fast64 en el ModelPack (`model.inc.c` y `geo.inc.c` de cada actor).
* Genera los recursos que carga Ghostship:
  * `DisplayList` en microcódigo F3D. Los triángulos usan `G_TRI2`, que en el modo F3D de
    Ghostship acepta los 64 vértices que cargan estos modelos.
  * `Vertex`, `Light` y `Texture` (RGBA, con `TEX_FLAG_LOAD_AS_IMG`).
  * `GeoLayout` en el formato que lee `src/port/game/GeoLayoutParser.cpp`. Las funciones
    geo se guardan con su dirección en la ROM US.
* Si una geo de Render96 tiene el mismo nombre que una de Ghostship (por ejemplo
  `mario_geo`), se escribe en la misma ruta, por ejemplo `actors/mario/geo/mario_geo`, y
  reemplaza al modelo original. El resto se guarda bajo `r96/actors/...`.
* Las animaciones no cambian: los modelos de Render96 usan el mismo esqueleto que los
  originales.
* `tools/verify_o2r.py` lee el `.o2r` igual que Ghostship. Comprueba que cada recurso
  se lea completo y que todas las referencias existan.

## Instalar en iOS

1. Abre Ghostship una vez (con tu ROM ya convertida) para que cree sus carpetas.
2. En la app **Archivos**: *En mi iPhone → Ghostship → `mods`*.
   Si no aparece `mods`, créala.
3. Copia `Render96-HD.o2r` (y, si quieres, `Render96-Models.o2r`) dentro de `mods`.
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

## Problemas comunes

* **No veo cambios**: comprueba que el archivo esté en `mods` y termine en `.o2r`, no en
  `.o2r.zip`. Después cierra la app por completo.
* **El juego se cierra al entrar a un nivel**: probablemente se quedó sin memoria.
  Genera el `.o2r` con `--max-size 1024`, o con `512`.
* **Algunas texturas siguen igual**: el pack de Render96 no las incluye, o son
  exclusivas de render96ex. `--report` lista las que se ignoraron.

## Créditos

* Texturas: equipo de Render96 / pokeheadroom: <https://github.com/pokeheadroom/RENDER96-HD-TEXTURE-PACK>
* Modelos: equipo de Render96: <https://github.com/Render96/ModelPack>
* Ghostship: Harbour Masters: <https://github.com/HarbourMasters/Ghostship>
* Correspondencias de texturas idénticas: SM64 Reloaded, de GhostlyDark: <https://github.com/GhostlyDark/SM64-Reloaded>

Este repositorio no contiene texturas, ROMs ni otros assets de Nintendo. Necesitas tu
propia copia de Super Mario 64 para usar Ghostship.
