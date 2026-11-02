#!/usr/bin/env python3
"""
vm10k_core.py  -  back-end engine for the OS 10k Tool

Takes a site box (British National Grid, EPSG:27700) and pulls the matching
OS VectorMap Local raster tiles (1:10,000, 5 km x 5 km GeoTIFF + MapInfo TAB)
out of the tile archive, e.g.  E:\\DATA\\AVectormap\\st\\ST22NE.tif

Two output modes:
  tiles  (default)  copy every tile the box touches, whole, with its TAB
  crop              stitch the tiles and crop to the exact box -> one TIF + TAB

Extras: MapInfo workspace (.WOR) that opens everything, site boundary
(TAB via GDAL's ogr2ogr if available, otherwise MIF/MID), and a run summary.

No GUI code here - the GUI (os10k_gui.py) and the command line both drive
run_extraction(). Only dependency: Pillow.

CLI examples:
    python vm10k_core.py --gr "ST 2250 2750" --width 1000 --height 1000 -o D:\\out
    python vm10k_core.py --sw 325000 130000 --width 3000 --height 2000 --mode crop -o D:\\out
"""

import argparse
import math
import os
import re
import shutil
import subprocess
import sys
from dataclasses import dataclass, field
from datetime import datetime

from PIL import Image, TiffImagePlugin

Image.MAX_IMAGE_PIXELS = None

__version__ = "1.0.0"

DEFAULT_DATA_ROOT = r"E:\DATA\AVectormap"
TILE_SIZE_M = 5000
TILE_PX = 7874
PIXEL_M = TILE_SIZE_M / TILE_PX          # 0.63500127 m
CROP_PIXEL_LIMIT = 600_000_000            # ~15.5 km square; beyond this use tiles mode

BNG_COORDSYS = ('CoordSys Earth Projection 8, 79, "m", -2, 49, '
                '0.9996012717, 400000, -100000')
# Bounds MapInfo needs for a BNG vector table (MIF)
BNG_COORDSYS_BOUNDS = BNG_COORDSYS + ' Bounds (-7845061.1011, -15524202.1641) (8645061.1011, 4470074.53373)'

# GeoTIFF keys for OSGB36 / British National Grid (used when writing crops)
BNG_GEOKEYS = (1, 1, 0, 3,
               1024, 0, 1, 1,        # GTModelType = Projected
               1025, 0, 1, 1,        # RasterType = PixelIsArea
               3072, 0, 1, 27700)    # ProjectedCSType = EPSG:27700


# --------------------------------------------------------------------------
# Grid references
# --------------------------------------------------------------------------

def _letter_index(ch):
    i = ord(ch.upper()) - ord("A")
    if i > 7:          # OS grid skips 'I'
        i -= 1
    return i


def letters_to_origin(letters):
    """'ST' -> (300000, 100000)  (SW corner of the 100 km square)."""
    if len(letters) != 2 or "I" in letters.upper():
        raise ValueError(f"'{letters}' is not a valid 100 km square")
    l1, l2 = _letter_index(letters[0]), _letter_index(letters[1])
    e = ((l1 - 2) % 5) * 5 + (l2 % 5)
    n = (19 - (l1 // 5) * 5) - (l2 // 5)
    if not (0 <= e <= 6 and 0 <= n <= 12):
        raise ValueError(f"'{letters}' is outside the British National Grid")
    return e * 100000, n * 100000


def origin_to_letters(e, n):
    """Easting/northing anywhere in a 100 km square -> 'ST'."""
    e100, n100 = int(e // 100000), int(n // 100000)
    if not (0 <= e100 <= 6 and 0 <= n100 <= 12):
        raise ValueError(f"({e:.0f}, {n:.0f}) is outside the British National Grid")
    l1 = (19 - n100) - (19 - n100) % 5 + (e100 + 10) // 5
    l2 = (19 - n100) * 5 % 25 + e100 % 5
    if l1 > 7:
        l1 += 1
    if l2 > 7:
        l2 += 1
    return chr(l1 + 65) + chr(l2 + 65)


def parse_grid_ref(text):
    """
    Accepts:  'ST 22500 27500', 'ST2250027500', 'ST 225 275', 'st225275'
              '325000, 130000', '325000 130000'
    Returns (easting, northing) in metres. Letter refs give the SW corner of
    the precision square (OS convention).
    """
    s = text.strip().upper()
    nums = re.findall(r"\d+(?:\.\d+)?", s)
    m = re.match(r"^([A-Z]{2})\s*([\d\s]*)$", s)
    if m:
        letters, digits = m.group(1), re.sub(r"\s", "", m.group(2))
        if len(digits) % 2 or len(digits) > 10:
            raise ValueError("Grid reference needs an even number of digits (up to 10)")
        e0, n0 = letters_to_origin(letters)
        if not digits:
            return float(e0), float(n0)
        half = len(digits) // 2
        scale = 10 ** (5 - half)
        return float(e0 + int(digits[:half]) * scale), float(n0 + int(digits[half:]) * scale)
    if len(nums) == 2 and re.fullmatch(r"[\d\s,.;]+", s):
        return float(nums[0]), float(nums[1])
    raise ValueError(f"Could not read grid reference '{text}'")


def format_grid_ref(e, n, digits=10):
    letters = origin_to_letters(e, n)
    half = digits // 2
    scale = 10 ** (5 - half)
    ee = int((e % 100000) // scale)
    nn = int((n % 100000) // scale)
    return f"{letters} {ee:0{half}d} {nn:0{half}d}"


# --------------------------------------------------------------------------
# Site box and tiles
# --------------------------------------------------------------------------

@dataclass
class SiteBox:
    minx: float
    miny: float
    maxx: float
    maxy: float

    @classmethod
    def from_centre(cls, e, n, width, height):
        return cls(e - width / 2, n - height / 2, e + width / 2, n + height / 2)

    @classmethod
    def from_sw(cls, e, n, width, height):
        return cls(e, n, e + width, n + height)

    @property
    def width(self):
        return self.maxx - self.minx

    @property
    def height(self):
        return self.maxy - self.miny

    @property
    def centre(self):
        return (self.minx + self.maxx) / 2, (self.miny + self.maxy) / 2

    def validate(self):
        if self.width <= 0 or self.height <= 0:
            raise ValueError("Width and height must be greater than zero")
        if self.minx < 0 or self.miny < 0 or self.maxx > 700000 or self.maxy > 1300000:
            raise ValueError("Site box falls outside the British National Grid")


def tile_name(e, n):
    """Name of the 5 km tile containing the point, e.g. 'ST22NE'."""
    letters = origin_to_letters(e, n)
    ed = int((e % 100000) // 10000)
    nd = int((n % 100000) // 10000)
    ns = "N" if (n % 10000) >= 5000 else "S"
    ew = "E" if (e % 10000) >= 5000 else "W"
    return f"{letters}{ed}{nd}{ns}{ew}"


@dataclass
class Tile:
    name: str             # 'ST22NE'
    x0: float             # SW corner easting
    y0: float             # SW corner northing
    tif: str = None       # full path, None if not in archive
    tab: str = None       # full path, None if missing

    @property
    def exists(self):
        return self.tif is not None


def tiles_for_box(box):
    """All 5 km tile footprints the box touches (edges exactly on a tile line
    don't pull in the neighbour), ordered north-west to south-east."""
    tiles = []
    y = math.floor(box.maxy / TILE_SIZE_M) * TILE_SIZE_M
    if y == box.maxy:
        y -= TILE_SIZE_M
    while y + TILE_SIZE_M > box.miny:
        x = math.floor(box.minx / TILE_SIZE_M) * TILE_SIZE_M
        while x < box.maxx:
            tiles.append(Tile(tile_name(x + 1, y + 1), x, y))
            x += TILE_SIZE_M
        y -= TILE_SIZE_M
    return tiles


class TileArchive:
    """Case-insensitive lookup into  <root>/<2 letters>/<NAME>.tif / .tab"""

    def __init__(self, root=DEFAULT_DATA_ROOT):
        self.root = root
        self._dirs = {}
        self._folders = None

    def _folder_map(self):
        if self._folders is None:
            self._folders = {}
            if os.path.isdir(self.root):
                for d in os.listdir(self.root):
                    if os.path.isdir(os.path.join(self.root, d)):
                        self._folders[d.lower()] = d
        return self._folders

    def _listing(self, letters):
        key = letters.lower()
        if key not in self._dirs:
            real = self._folder_map().get(key)
            files = {}
            if real:
                folder = os.path.join(self.root, real)
                for f in os.listdir(folder):
                    files[f.lower()] = os.path.join(folder, f)
            self._dirs[key] = files
        return self._dirs[key]

    def resolve(self, tile):
        files = self._listing(tile.name[:2])
        tile.tif = files.get(tile.name.lower() + ".tif")
        tile.tab = files.get(tile.name.lower() + ".tab")
        return tile

    def check_root(self):
        if not os.path.isdir(self.root):
            raise FileNotFoundError(f"Data folder not found: {self.root}")
        if not self._folder_map():
            raise FileNotFoundError(f"No grid-square subfolders in {self.root}")


# --------------------------------------------------------------------------
# Writers
# --------------------------------------------------------------------------

def _crlf(lines):
    return "\r\n".join(lines) + "\r\n"


def write_raster_tab(tab_path, tif_filename, x_left, y_top, w_px, h_px, pixel=PIXEL_M):
    """MapInfo raster registration (version 300 - opens in MapInfo 8.5).
    Same 3-point convention as the OS-supplied TABs."""
    x1 = x_left + (w_px - 1) * pixel
    y1 = y_top - (h_px - 1) * pixel
    lines = [
        "!table", "!version 300", "!charset WindowsLatin1", "",
        "Definition Table",
        f'  File "{tif_filename}"',
        '  Type "RASTER"',
        f'  ({x_left:.8f},{y_top:.8f}) (0,0) Label "Pt 1",',
        f'  ({x1:.8f},{y_top:.8f}) ({w_px - 1},0) Label "Pt 2",',
        f'  ({x_left:.8f},{y1:.8f}) (0,{h_px - 1}) Label "Pt 3"',
        f"  {BNG_COORDSYS}",
        '  Units "m"',
    ]
    with open(tab_path, "w", newline="") as f:
        f.write(_crlf(lines))


def tab_for_tile(tile, out_dir, tif_filename):
    """Copy the OS TAB if it exists, otherwise build one from the tile position."""
    dest = os.path.join(out_dir, os.path.splitext(tif_filename)[0] + ".TAB")
    if tile.tab:
        shutil.copy2(tile.tab, dest)
        generated = False
    else:
        with Image.open(tile.tif) as im:
            w, h = im.size
            tie = im.tag_v2.get(33922)
            scale = im.tag_v2.get(33550)
        if tie and scale:
            x_left, y_top, px = tie[3], tie[4], scale[0]
        else:
            x_left, y_top, px = tile.x0, tile.y0 + TILE_SIZE_M, PIXEL_M
        write_raster_tab(dest, tif_filename, x_left, y_top, w, h, px)
        generated = True
    return dest, generated


def write_site_mif(base_path, box, site_name):
    """Site boundary as MIF/MID (always readable by MapInfo via Table > Import)."""
    mif, mid = base_path + ".mif", base_path + ".mid"
    lines = [
        "Version 300", 'Charset "WindowsLatin1"', 'Delimiter ","',
        BNG_COORDSYS_BOUNDS,
        "Columns 3", "  Site Char(60)", "  Width_m Float", "  Height_m Float",
        "Data", "",
        "Region 1", "  5",
        f"{box.minx:.3f} {box.miny:.3f}", f"{box.minx:.3f} {box.maxy:.3f}",
        f"{box.maxx:.3f} {box.maxy:.3f}", f"{box.maxx:.3f} {box.miny:.3f}",
        f"{box.minx:.3f} {box.miny:.3f}",
        "    Pen (3,2,16744448)",          # 3 px solid orange
        "    Brush (1,0,16777215)",        # hollow
    ]
    with open(mif, "w", newline="") as f:
        f.write(_crlf(lines))
    with open(mid, "w", newline="") as f:
        f.write(_crlf([f'"{site_name[:60]}",{box.width:.1f},{box.height:.1f}']))
    return mif


def find_ogr2ogr():
    exe = shutil.which("ogr2ogr")
    if exe:
        return exe
    for p in (r"C:\OSGeo4W\bin\ogr2ogr.exe", r"C:\OSGeo4W64\bin\ogr2ogr.exe",
              r"C:\Program Files\QGIS 3.40\bin\ogr2ogr.exe"):
        if os.path.exists(p):
            return p
    return None


def write_site_boundary(out_dir, box, site_name, log):
    base = os.path.join(out_dir, "Site_Boundary")
    mif = write_site_mif(base, box, site_name)
    ogr = find_ogr2ogr()
    if not ogr:
        log("  Site boundary written as MIF/MID (GDAL not found - import it in MapInfo)")
        return None
    tab = base + ".TAB"
    cmd = [ogr, "-f", "MapInfo File", tab, mif, "-a_srs", "EPSG:27700",
           "-dsco", "FORMAT=TAB"]
    try:
        kw = {"creationflags": 0x08000000} if os.name == "nt" else {}  # no console flash
        subprocess.run(cmd, check=True, capture_output=True, timeout=60, **kw)
        log("  Site boundary written as TAB (and MIF/MID)")
        return tab
    except Exception as exc:  # keep going, the MIF is still there
        log(f"  Site boundary TAB conversion failed ({exc}); MIF/MID kept")
        return None


def write_workspace(wor_path, table_files, box, boundary_tab=None):
    """MapInfo workspace that opens every raster (plus the boundary) in one map."""
    aliases, lines = [], ["!Workspace", "!Version 600", "!Charset WindowsLatin1"]
    for tf in table_files:
        alias = re.sub(r"\W", "_", os.path.splitext(os.path.basename(tf))[0])
        if alias[0].isdigit():
            alias = "T_" + alias
        aliases.append(alias)
        lines.append(f'Open Table "{os.path.basename(tf)}" As {alias} Interactive')
    if boundary_tab:
        lines.append(f'Open Table "{os.path.basename(boundary_tab)}" As Site_Boundary Interactive')
        aliases.insert(0, "Site_Boundary")      # top layer
    cx, cy = box.centre
    zoom = max(box.width, box.height) * 1.3
    lines += [
        f"Map From {','.join(aliases)}",
        '  Position (0.05,0.05) Units "in"',
        '  Width 9 Units "in" Height 7 Units "in"',
        "Set Window FrontWindow() ScrollBars Off Autoscroll On",
        "Set Map",
        f"  {BNG_COORDSYS}",
        f"  Center ({cx:.1f},{cy:.1f})",
        f'  Zoom {zoom:.1f} Units "m"',
        "  Preserve Zoom Display Zoom",
        "  Distance Units \"m\" Area Units \"sq m\" XY Units \"m\"",
    ]
    with open(wor_path, "w", newline="") as f:
        f.write(_crlf(lines))


# --------------------------------------------------------------------------
# Exact crop
# --------------------------------------------------------------------------

def _background_index(palette):
    """Palette index closest to white - used to fill gaps where there is no tile."""
    best, best_d = 0, 10 ** 9
    for i in range(0, len(palette) // 3):
        r, g, b = palette[i * 3:i * 3 + 3]
        d = (255 - r) + (255 - g) + (255 - b)
        if d < best_d:
            best, best_d = i, d
    return best


def crop_mosaic(tiles, box, out_tif, log=print, progress=None):
    """Stitch the tiles and crop to the box on the native 0.635 m pixel grid."""
    present = [t for t in tiles if t.exists]
    if not present:
        raise RuntimeError("None of the tiles for this box are in the archive")

    X0 = min(t.x0 for t in tiles)
    Ytop = max(t.y0 for t in tiles) + TILE_SIZE_M
    col0 = math.floor((box.minx - X0) / PIXEL_M + 1e-6)
    col1 = math.ceil((box.maxx - X0) / PIXEL_M - 1e-6)
    row0 = math.floor((Ytop - box.maxy) / PIXEL_M + 1e-6)
    row1 = math.ceil((Ytop - box.miny) / PIXEL_M - 1e-6)
    w, h = col1 - col0, row1 - row0
    if w * h > CROP_PIXEL_LIMIT:
        raise RuntimeError(
            f"Crop would be {w} x {h} px ({w * h / 1e6:.0f} MP) - too large for one image. "
            "Use 'Whole tiles' mode for a site this size.")

    with Image.open(present[0].tif) as first:
        palette = first.getpalette()
        geo_src = dict(first.tag_v2)
    bg = _background_index(palette)
    mosaic = Image.new("P", (w, h), bg)
    mosaic.putpalette(palette)

    for i, t in enumerate(present, 1):
        log(f"  Stitching {t.name} ({i}/{len(present)})")
        tc = round((t.x0 - X0) / PIXEL_M)                       # tile offset, whole px
        tr = round((Ytop - (t.y0 + TILE_SIZE_M)) / PIXEL_M)
        # overlap of this tile with the crop window, in tile pixel coords
        a0, a1 = max(col0, tc) - tc, min(col1, tc + TILE_PX) - tc
        b0, b1 = max(row0, tr) - tr, min(row1, tr + TILE_PX) - tr
        if a1 <= a0 or b1 <= b0:
            continue
        with Image.open(t.tif) as im:
            if im.mode != "P" or im.getpalette() != palette:
                piece = im.convert("RGB").crop((a0, b0, a1, b1)).quantize(palette=mosaic)
            else:
                piece = im.crop((a0, b0, a1, b1))
            mosaic.paste(piece, (tc + a0 - col0, tr + b0 - row0))
        if progress:
            progress(i / len(present))

    x_left = X0 + col0 * PIXEL_M
    y_top = Ytop - row0 * PIXEL_M

    ifd = TiffImagePlugin.ImageFileDirectory_v2()
    for tag, val, typ in (
        (33550, (PIXEL_M, PIXEL_M, 0.0), 12),
        (33922, (0.0, 0.0, 0.0, x_left, y_top, 0.0), 12),
        (34735, tuple(geo_src.get(34735, BNG_GEOKEYS)), 3),
    ):
        ifd[tag] = val
        ifd.tagtype[tag] = typ
    if 34736 in geo_src:
        ifd[34736] = tuple(geo_src[34736]); ifd.tagtype[34736] = 12
    if 34737 in geo_src:
        ifd[34737] = geo_src[34737]; ifd.tagtype[34737] = 2
    ifd[270] = f"OS VectorMap Local 1:10000 crop - {os.path.basename(out_tif)}"
    ifd[33432] = geo_src.get(33432, "ORDNANCE SURVEY CROWN COPYRIGHT")
    mosaic.save(out_tif, compression="tiff_lzw", tiffinfo=ifd, dpi=(400, 400))

    tab = os.path.splitext(out_tif)[0] + ".TAB"
    write_raster_tab(tab, os.path.basename(out_tif), x_left, y_top, w, h)
    log(f"  Crop: {w} x {h} px, top-left ({x_left:.2f}, {y_top:.2f})")
    return tab


# --------------------------------------------------------------------------
# Main entry point
# --------------------------------------------------------------------------

@dataclass
class RunOptions:
    data_root: str = DEFAULT_DATA_ROOT
    out_dir: str = "."
    site_name: str = "Site"
    mode: str = "tiles"                 # 'tiles' | 'crop'
    make_subfolder: bool = False
    write_wor: bool = True
    write_boundary: bool = True


@dataclass
class RunResult:
    out_dir: str
    tiles: list = field(default_factory=list)
    missing: list = field(default_factory=list)
    generated_tabs: list = field(default_factory=list)
    outputs: list = field(default_factory=list)
    workspace: str = None


def safe_name(s):
    s = re.sub(r'[<>:"/\\|?*]+', "_", s.strip()) or "Site"
    return s[:80]


def plan(box, data_root=DEFAULT_DATA_ROOT, archive=None):
    """Tiles the box needs, resolved against the archive (no files written)."""
    box.validate()
    archive = archive or TileArchive(data_root)
    archive.check_root()
    return [archive.resolve(t) for t in tiles_for_box(box)]


def run_extraction(box, opts, log=print, progress=None):
    tiles = plan(box, opts.data_root)
    out_dir = opts.out_dir
    if opts.make_subfolder:
        out_dir = os.path.join(out_dir, safe_name(opts.site_name))
    os.makedirs(out_dir, exist_ok=True)

    res = RunResult(out_dir=out_dir, tiles=tiles,
                    missing=[t.name for t in tiles if not t.exists])
    cx, cy = box.centre
    log(f"OS 10k Tool v{__version__}")
    log(f"Site '{opts.site_name}': centre {format_grid_ref(cx, cy)} "
        f"({box.width:.0f} m x {box.height:.0f} m)")
    log(f"Tiles touched: {', '.join(t.name for t in tiles)}")
    if res.missing:
        log(f"Not in archive (sea / no coverage): {', '.join(res.missing)}")
    present = [t for t in tiles if t.exists]
    if not present:
        raise RuntimeError("None of the tiles for this site are in the archive")

    if opts.mode == "crop":
        log("Mode: exact crop to site box")
        out_tif = os.path.join(out_dir, safe_name(opts.site_name) + "_10k.tif")
        tab = crop_mosaic(tiles, box, out_tif, log, progress)
        res.outputs.append(tab)
    else:
        log("Mode: whole tiles")
        for i, t in enumerate(present, 1):
            fn = os.path.basename(t.tif)
            shutil.copy2(t.tif, os.path.join(out_dir, fn))
            tab, gen = tab_for_tile(t, out_dir, fn)
            if gen:
                res.generated_tabs.append(t.name)
            res.outputs.append(tab)
            log(f"  Copied {t.name}" + ("  (TAB rebuilt - missing in archive)" if gen else ""))
            if progress:
                progress(i / len(present))

    boundary = None
    if opts.write_boundary:
        boundary = write_site_boundary(out_dir, box, opts.site_name, log)
    if opts.write_wor:
        res.workspace = os.path.join(out_dir, safe_name(opts.site_name) + ".WOR")
        write_workspace(res.workspace, res.outputs, box, boundary)
        log(f"  Workspace: {os.path.basename(res.workspace)}")

    with open(os.path.join(out_dir, "OS10k_run_summary.txt"), "w", newline="") as f:
        f.write(_crlf([
            f"OS 10k Tool v{__version__}  -  {datetime.now():%Y-%m-%d %H:%M}",
            f"Site name: {opts.site_name}",
            f"Centre:    {format_grid_ref(cx, cy)}  ({cx:.1f}, {cy:.1f})",
            f"SW corner: ({box.minx:.1f}, {box.miny:.1f})",
            f"Size:      {box.width:.1f} m x {box.height:.1f} m",
            f"Mode:      {'exact crop' if opts.mode == 'crop' else 'whole tiles'}",
            f"Source:    {opts.data_root}",
            f"Tiles:     {', '.join(t.name for t in present)}",
            f"Missing:   {', '.join(res.missing) or 'none'}",
            f"TABs rebuilt: {', '.join(res.generated_tabs) or 'none'}",
            "Data: OS VectorMap Local raster. Crown copyright and database right.",
        ]))
    log(f"Done -> {out_dir}")
    return res


def main():
    ap = argparse.ArgumentParser(description="OS 10k Tool - extract VectorMap Local raster tiles for a site")
    loc = ap.add_mutually_exclusive_group(required=True)
    loc.add_argument("--gr", help="Centre grid reference, e.g. 'ST 2250 2750' or '322500,127500'")
    loc.add_argument("--sw", nargs=2, type=float, metavar=("E", "N"), help="South-west corner")
    ap.add_argument("--width", type=float, required=True, help="Box width (m)")
    ap.add_argument("--height", type=float, required=True, help="Box height (m)")
    ap.add_argument("--mode", choices=["tiles", "crop"], default="tiles")
    ap.add_argument("--data", default=DEFAULT_DATA_ROOT, help="Tile archive root")
    ap.add_argument("-o", "--out", required=True, help="Output folder")
    ap.add_argument("--name", help="Site name (default from grid ref)")
    ap.add_argument("--subfolder", action="store_true", help="Put output in a site-name subfolder")
    ap.add_argument("--no-wor", action="store_true")
    ap.add_argument("--no-boundary", action="store_true")
    a = ap.parse_args()

    if a.gr:
        e, n = parse_grid_ref(a.gr)
        box = SiteBox.from_centre(e, n, a.width, a.height)
    else:
        box = SiteBox.from_sw(a.sw[0], a.sw[1], a.width, a.height)
    cx, cy = box.centre
    name = a.name or format_grid_ref(cx, cy).replace(" ", "")
    opts = RunOptions(a.data, a.out, name, a.mode, a.subfolder, not a.no_wor, not a.no_boundary)
    try:
        run_extraction(box, opts)
    except Exception as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
