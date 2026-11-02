# OS 10k Tool  (v1.1.4)

Desktop tool that pulls **OS VectorMap Local raster (1:10,000)** tiles for a site and
writes them out ready to open in **MapInfo Professional 8.5**.

Sister app to *OS Point Tool* (MasterMap) and *OS Shapefile Tool*. It's kept separate on purpose.

## What it does

1. You set the site. Either:
   - a **centre grid reference** (`ST 22500 27500`, `ST225275` or `322500,127500`), or
   - a **south-west corner** (easting/northing), where the box extends north and east.

   Then give the width and height in metres.
   - **shapefile(s)**: add one or more `.shp` files. The site is their combined extent plus a buffer
     (default 300 m) round the most northerly, southerly, easterly and westerly points. Shapefiles not in
     British National Grid are reprojected with GDAL. There's an option to save the output in the
     shapefile's own folder.
2. The preview shows the actual 10k map with the 5 km tiles the site touches, the site box in orange,
   and any input shapefiles in crimson.
3. You pick the output:
   - **Whole tiles** (default): every 5 km × 5 km tile the box touches, copied as supplied (TIF + TAB).
   - **Exact crop**: the tiles stitched and cropped to the box on the native 0.635 m pixel grid,
     giving one TIF + TAB. The original 8-bit OS colours are kept, so files stay small.
4. Extras (switch them on or off in **⚙ Settings**):
   - `<site>.WOR` is a MapInfo workspace that opens all the rasters (plus the boundary) in one map window.
   - `Site_Boundary` is the site box as an orange outline. It's written as a TAB if GDAL's `ogr2ogr` is found,
     otherwise as MIF/MID (open it via *Table → Import*). Once the TAB is made, the MIF/MID are deleted by
     default (a Settings tick controls this).
   - In shapefile mode, each input shapefile is also converted to MapInfo TAB (EPSG:27700 forced) and
     added to the workspace as the top layer.
   - `OS10k_run_summary.txt` records the site, the tiles used, and any missing tiles.

## Data source

Default archive: `E:\DATA\AVectormap`. It has one subfolder per 100 km square (`hp`, `nn`, `st`, `tq` …),
each holding `<NAME>.tif` + `<name>.TAB` (for example `st\ST22NE.tif`). You can change the path in Settings.

Archive quirks the tool already handles:
- **Sea / no coverage:** the tile simply isn't there. It's reported as missing; crops fill the gap with white.
- **TAB missing** (all 4 `hz` tiles, 8 in `so`): the tool rebuilds the TAB from the GeoTIFF position,
  identical to the OS ones.
- **Stray files:** `so` contains duplicate copies of some SP tiles, and there's an odd `.wor`, `test.*`, `.ghx` and `.pprc`.
  These are ignored. Tiles are always read from their own grid-square folder.

## Install / run (Windows)

- Needs Python 3.9+ with Pillow: `pip install pillow`. tkinter comes with the standard python.org installer.
- GDAL is optional. It's used for the boundary TAB, shapefile → TAB conversion, and reprojecting
  non-BNG shapefiles. Without GDAL, BNG shapefiles still work for setting the site.
- To start the app, double-click **`Run_OS_10k_Tool.bat`**, or run `pythonw os10k_gui.py`.
- Settings are saved to `%APPDATA%\OS10kTool\settings.json`.

Command line (no GUI):

```
python vm10k_core.py --gr "ST 2250 2750" --width 1000 --height 1000 -o D:\Out
python vm10k_core.py --sw 325000 130000 --width 3000 --height 2000 --mode crop -o D:\Out --subfolder
python vm10k_core.py --shp D:\Site\red_line.shp D:\Site\access.shp --buffer 300 -o D:\Site
```

Optional single .exe: `pyinstaller --onefile --windowed --icon os10k_icon.ico --add-data "os10k_icon.ico;." --add-data "os10k_icon.png;." os10k_gui.py`

## Files

| File | Purpose |
|---|---|
| `os10k_gui.py` | Desktop front end |
| `vm10k_core.py` | Back-end engine: grid refs, tile lookup, copy/crop, TAB/WOR/boundary writers, CLI |
| `make_icon.py` | Regenerates `os10k_icon.ico` / `.png` (navy grid + orange-framed tile, Point Tool theme) |
| `Run_OS_10k_Tool.bat` | Launcher |

## Limits

- An exact crop is capped at about 600 megapixels (roughly 15 km square). Use Whole tiles for anything bigger.
- The preview hides the map imagery when more than 36 tiles are in view, and doesn't draw at all past 400 tiles.

## Version history

- **1.1.4** (2026-11-02): Rounded tabs with a soft grey border. Orange highlight removed.
- **1.1.3** (2026-11-02): Tabs back to equal size. The open tab is shown by its white background, navy text and orange edge.
- **1.1.2** (2026-11-02): Settings cog is drawn as an outline only.
- **1.1.1** (2026-11-02): Proper cog icon on the Settings tab. The selected tab is drawn larger.
- **1.1.0** (2026-11-02): Tick-style checkboxes. Option to delete the boundary MIF/MID once its TAB is made
  (on by default). New shapefile site mode: extent + buffer, BNG check and reprojection, shapefiles drawn
  on the preview, convert-to-TAB option, save-in-shapefile-folder option.
- **1.0.0** (2026-11-02): First release. Centre GR / SW corner input, whole-tile and exact-crop output,
  live map preview, WOR + site boundary, TAB rebuild for tiles missing one.

Data: OS VectorMap Local. © Crown copyright and database right.
