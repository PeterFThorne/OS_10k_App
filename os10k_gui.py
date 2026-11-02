#!/usr/bin/env python3
"""
OS 10k Tool - desktop front end for vm10k_core.py

Pick a site (centre grid reference, south-west corner + width/height, or
shapefile(s) + buffer),
preview it on the actual 1:10,000 map, and export the OS VectorMap Local
raster tiles for MapInfo - whole tiles (default) or an exact crop.

Run:  pythonw os10k_gui.py      (or double-click Run_OS_10k_Tool.bat)
"""

import json
import math
import os
import subprocess
import sys
import threading
import tkinter as tk
from tkinter import filedialog, messagebox, ttk

from PIL import Image, ImageDraw, ImageFont, ImageTk

import vm10k_core as core

APP_NAME = "OS 10k Tool"
APP_VERSION = core.__version__
HERE = os.path.dirname(os.path.abspath(__file__))
ICON_ICO = os.path.join(HERE, "os10k_icon.ico")
ICON_PNG = os.path.join(HERE, "os10k_icon.png")

NAVY = "#1F2A44"
NAVY_LIGHT = "#2E3D5E"
ORANGE = "#F28C28"
PALE = "#F4F6FA"

PREVIEW_PX = 560
MAX_PREVIEW_TILES = 36

SETTINGS_DIR = os.path.join(os.environ.get("APPDATA", os.path.expanduser("~")), "OS10kTool")
SETTINGS_FILE = os.path.join(SETTINGS_DIR, "settings.json")
DEFAULTS = {
    "data_root": core.DEFAULT_DATA_ROOT,
    "out_dir": "",
    "write_wor": True,
    "write_boundary": True,
    "open_when_done": True,
    "last_mode": "centre",
    "last_gr": "ST 22500 27500",
    "last_width": "1000",
    "last_height": "1000",
    "delete_boundary_mif": True,
    "shp_buffer": "300",
    "shp_same_folder": False,
    "shp_convert": True,
    "last_shp_dir": "",
}


def load_settings():
    s = dict(DEFAULTS)
    try:
        with open(SETTINGS_FILE) as f:
            s.update(json.load(f))
    except Exception:
        pass
    return s


def save_settings(s):
    try:
        os.makedirs(SETTINGS_DIR, exist_ok=True)
        with open(SETTINGS_FILE, "w") as f:
            json.dump(s, f, indent=2)
    except Exception:
        pass


def open_folder(path):
    if os.name == "nt":
        os.startfile(path)
    elif sys.platform == "darwin":
        subprocess.Popen(["open", path])
    else:
        subprocess.Popen(["xdg-open", path])


def make_tick_images(size=16, pad_right=6):
    """Checkbox images with a tick (ttk 'clam' draws a cross)."""
    k = 4
    S = size * k

    def box(fill, border, tick=None):
        im = Image.new("RGBA", ((size + pad_right) * k, S), (0, 0, 0, 0))
        d = ImageDraw.Draw(im)
        d.rounded_rectangle([k, k, S - k, S - k], radius=3 * k, fill=fill, outline=border, width=k + 2)
        if tick:
            d.line([(S * 0.26, S * 0.52), (S * 0.43, S * 0.69), (S * 0.75, S * 0.32)],
                   fill=tick, width=int(2.4 * k), joint="curve")
        return ImageTk.PhotoImage(im.resize((size + pad_right, size), Image.Resampling.LANCZOS))

    return {
        "off": box("white", "#8A93A6"),
        "off_hover": box("#FFF4EA", ORANGE),
        "off_dis": box("#EEF0F4", "#C9CDD6"),
        "on": box(ORANGE, ORANGE, "white"),
        "on_hover": box("#E07B16", "#E07B16", "white"),
        "on_dis": box("#E9C9A8", "#E9C9A8", "white"),
    }


def make_tab_images(r=7, k=4):
    """Rounded-top tab backgrounds (9-slice): normal, hover, selected."""
    W, H = 32, 28
    border = (205, 211, 221, 255)

    def tab(fill, bottom_line):
        im = Image.new("RGBA", (W * k, H * k), (0, 0, 0, 0))
        d = ImageDraw.Draw(im)
        d.rounded_rectangle([0, 0, W * k - 1, (H + r) * k], radius=r * k, fill=border)
        d.rounded_rectangle([k, k, W * k - 1 - k, (H + r) * k], radius=(r - 1) * k, fill=fill)
        if bottom_line:
            d.rectangle([0, (H - 1) * k, W * k, H * k], fill=border)
        return ImageTk.PhotoImage(im.resize((W, H), Image.Resampling.LANCZOS))

    return {
        "normal": tab((226, 230, 237, 255), True),
        "hover": tab((236, 239, 244, 255), True),
        "selected": tab((255, 255, 255, 255), False),
    }


def make_cog_image(size=20, k=8, colour=(74, 84, 104, 255)):
    """Outline-only settings cog: gear edge plus centre ring, no fill."""
    S = size * k
    c = S / 2
    im = Image.new("RGBA", (S, S), (0, 0, 0, 0))
    d = ImageDraw.Draw(im)
    w = int(1.7 * k)                       # ~1.7 px line at 20 px
    R, r, n = S * 0.47 - w / 2, S * 0.35, 8
    pts = []
    for i in range(n):
        a = 2 * math.pi * i / n
        for da, rad in ((-0.30, r), (-0.20, R), (0.20, R), (0.30, r)):
            t = a + da * (2 * math.pi / n)
            pts.append((c + rad * math.cos(t), c + rad * math.sin(t)))
    d.line(pts + pts[:2], fill=colour, width=w, joint="curve")
    rc = S * 0.15
    d.ellipse([c - rc, c - rc, c + rc, c + rc], outline=colour, width=w)
    return im.resize((size, size), Image.Resampling.LANCZOS)


# --------------------------------------------------------------------------
# Preview renderer (runs off the UI thread)
# --------------------------------------------------------------------------

class PreviewRenderer:
    def __init__(self):
        self._thumbs = {}            # tile path -> RGB thumbnail (~493 px)
        self._geoms = {}             # (shp path, mtime) -> (kind, parts)
        self._lock = threading.Lock()

    def geometry(self, path):
        key = (path, os.path.getmtime(path))
        with self._lock:
            if key in self._geoms:
                return self._geoms[key]
        g = core.read_shp_geometry(path)
        with self._lock:
            self._geoms[key] = g
        return g

    def thumb(self, path):
        with self._lock:
            if path in self._thumbs:
                return self._thumbs[path]
        with Image.open(path) as im:
            th = im.convert("RGB").reduce(16)
        with self._lock:
            if len(self._thumbs) > 120:
                self._thumbs.pop(next(iter(self._thumbs)))
            self._thumbs[path] = th
        return th

    def render(self, box, archive, mode, size=PREVIEW_PX, shapes=()):
        """Returns (PIL image, info dict)."""
        export_tiles = [archive.resolve(t) for t in core.tiles_for_box(box)]
        tx0 = min(t.x0 for t in export_tiles)
        ty0 = min(t.y0 for t in export_tiles)
        tx1 = max(t.x0 for t in export_tiles) + core.TILE_SIZE_M
        ty1 = max(t.y0 for t in export_tiles) + core.TILE_SIZE_M
        # view = tile block + margin, square
        span = max(tx1 - tx0, ty1 - ty0) * 1.15
        cx, cy = (tx0 + tx1) / 2, (ty0 + ty1) / 2
        vx0, vy1 = cx - span / 2, cy + span / 2
        scale = size / span                                    # px per metre

        def to_px(x, y):
            return (x - vx0) * scale, (vy1 - y) * scale

        img = Image.new("RGB", (size, size), "#E6E9EF")
        draw = ImageDraw.Draw(img, "RGBA")

        view_box = core.SiteBox(vx0, vy1 - span, vx0 + span, vy1)
        view_tiles = [archive.resolve(t) for t in core.tiles_for_box(view_box)]
        show_map = len(view_tiles) <= MAX_PREVIEW_TILES
        export_names = {t.name for t in export_tiles}

        for t in view_tiles:
            px0, py0 = to_px(t.x0, t.y0 + core.TILE_SIZE_M)
            px1, py1 = to_px(t.x0 + core.TILE_SIZE_M, t.y0)
            if t.exists and show_map:
                w, h = max(1, round(px1) - round(px0)), max(1, round(py1) - round(py0))
                img.paste(self.thumb(t.tif).resize((w, h), Image.Resampling.BILINEAR),
                          (round(px0), round(py0)))
            elif not t.exists:
                w, h = max(1, round(px1) - round(px0)), max(1, round(py1) - round(py0))
                hatch = Image.new("RGB", (w, h), (205, 212, 224))
                hd = ImageDraw.Draw(hatch)
                for k in range(-h, w, 14):
                    hd.line([k, h, k + h, 0], fill=(185, 193, 208))
                img.paste(hatch, (round(px0), round(py0)))

        # shade everything outside the export set, outline tiles
        if mode == "tiles":
            for t in view_tiles:
                if t.name not in export_names:
                    px0, py0 = to_px(t.x0, t.y0 + core.TILE_SIZE_M)
                    px1, py1 = to_px(t.x0 + core.TILE_SIZE_M, t.y0)
                    draw.rectangle([px0, py0, px1, py1], fill=(31, 42, 68, 95))
        else:
            sx0, sy0 = to_px(box.minx, box.maxy)
            sx1, sy1 = to_px(box.maxx, box.miny)
            shade = (31, 42, 68, 95)
            draw.rectangle([0, 0, size, sy0], fill=shade)
            draw.rectangle([0, sy1, size, size], fill=shade)
            draw.rectangle([0, sy0, sx0, sy1], fill=shade)
            draw.rectangle([sx1, sy0, size, sy1], fill=shade)

        try:
            font = ImageFont.truetype("segoeuib.ttf", 13)
        except Exception:
            try:
                font = ImageFont.truetype("DejaVuSans-Bold.ttf", 13)
            except Exception:
                font = ImageFont.load_default()

        for t in view_tiles:
            px0, py0 = to_px(t.x0, t.y0 + core.TILE_SIZE_M)
            px1, py1 = to_px(t.x0 + core.TILE_SIZE_M, t.y0)
            sel = t.name in export_names
            draw.rectangle([px0, py0, px1, py1],
                           outline=(31, 42, 68, 255 if sel else 120), width=2 if sel else 1)
            label = t.name + ("" if t.exists else " (none)")
            tb = draw.textbbox((0, 0), label, font=font)
            lx, ly = px0 + 5, py0 + 4
            draw.rectangle([lx - 3, ly - 2, lx + tb[2] + 3, ly + tb[3] + 2],
                           fill=(31, 42, 68, 210 if sel else 120))
            draw.text((lx, ly), label, fill="white", font=font)

        # input shapefiles
        for sh in shapes:
            kind, parts = self.geometry(sh.bng_path)
            for part in parts:
                pts = [to_px(x, y) for x, y in part]
                if kind == "point" or len(pts) == 1:
                    for px, py in pts:
                        draw.ellipse([px - 3, py - 3, px + 3, py + 3], fill=(200, 30, 90, 255))
                elif kind == "poly":
                    draw.polygon(pts, fill=(200, 30, 90, 40))
                    draw.line(pts + [pts[0]], fill=(200, 30, 90, 255), width=2)
                else:
                    draw.line(pts, fill=(200, 30, 90, 255), width=2)

        # site box
        sx0, sy0 = to_px(box.minx, box.maxy)
        sx1, sy1 = to_px(box.maxx, box.miny)
        if sx1 - sx0 < 4:
            mx = (sx0 + sx1) / 2; sx0, sx1 = mx - 2, mx + 2
        if sy1 - sy0 < 4:
            my = (sy0 + sy1) / 2; sy0, sy1 = my - 2, my + 2
        draw.rectangle([sx0, sy0, sx1, sy1], outline=ORANGE, width=3)
        ccx, ccy = to_px(*box.centre)
        draw.line([ccx - 7, ccy, ccx + 7, ccy], fill=ORANGE, width=2)
        draw.line([ccx, ccy - 7, ccx, ccy + 7], fill=ORANGE, width=2)

        info = {
            "tiles": export_tiles,
            "present": [t for t in export_tiles if t.exists],
            "missing": [t.name for t in export_tiles if not t.exists],
            "map_shown": show_map,
        }
        return img, info


# --------------------------------------------------------------------------
# GUI
# --------------------------------------------------------------------------

class App(tk.Tk):
    def __init__(self):
        super().__init__()
        self.settings = load_settings()
        self.title(f"{APP_NAME}  v{APP_VERSION}")
        self.configure(bg=PALE)
        self.minsize(1040, 760)
        self._set_icon()
        self._style()

        self.renderer = PreviewRenderer()
        self._preview_job = None
        self._preview_gen = 0
        self._running = False
        self._last_out = None
        self._name_auto = True
        self.shapes = []             # core.ShapeInput, in list order

        self._build()
        self._load_fields()
        self.protocol("WM_DELETE_WINDOW", self._on_close)
        self.after(300, self.schedule_preview)

    # ---- look & feel ----------------------------------------------------
    def _set_icon(self):
        try:
            if os.name == "nt":
                import ctypes
                ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("OS.10kTool")
            if os.path.exists(ICON_ICO) and os.name == "nt":
                self.iconbitmap(ICON_ICO)
            elif os.path.exists(ICON_PNG):
                self._icon_img = tk.PhotoImage(file=ICON_PNG)
                self.iconphoto(True, self._icon_img)
        except Exception:
            pass

    def _style(self):
        st = ttk.Style(self)
        try:
            st.theme_use("clam")
        except Exception:
            pass
        base = ("Segoe UI", 10)
        st.configure(".", font=base, background=PALE)
        st.configure("TFrame", background=PALE)
        st.configure("Card.TFrame", background="white", relief="flat")
        st.configure("TLabel", background=PALE)
        st.configure("Card.TLabel", background="white")
        st.configure("Head.TLabel", background="white", foreground=NAVY, font=("Segoe UI Semibold", 11))
        st.configure("Hint.TLabel", background="white", foreground="#6B7488", font=("Segoe UI", 9))
        st.configure("Info.TLabel", background=PALE, foreground=NAVY, font=("Segoe UI", 9))
        self._ticks = make_tick_images()
        t = self._ticks
        st.element_create("Tick.indicator", "image", t["off"],
                          ("disabled", "selected", t["on_dis"]), ("disabled", t["off_dis"]),
                          ("active", "selected", t["on_hover"]), ("selected", t["on"]),
                          ("active", t["off_hover"]), border=0, sticky="w")
        st.layout("TCheckbutton", [("Checkbutton.padding", {"sticky": "nswe", "children": [
            ("Tick.indicator", {"side": "left", "sticky": ""}),
            ("Checkbutton.focus", {"side": "left", "sticky": "w", "children": [
                ("Checkbutton.label", {"sticky": "nswe"})]})]})])
        st.configure("TRadiobutton", background="white")
        st.configure("TCheckbutton", background="white")
        st.configure("Run.TButton", font=("Segoe UI Semibold", 11), foreground="white",
                     background=ORANGE, padding=(18, 8), borderwidth=0)
        st.map("Run.TButton", background=[("disabled", "#C9CDD6"), ("active", "#E07B16")])
        st.configure("TButton", padding=(10, 5))
        st.configure("TNotebook", background=PALE, borderwidth=0)
        self._tabimgs = ti = make_tab_images()
        st.element_create("Rounded.tab", "image", ti["normal"],
                          ("selected", ti["selected"]), ("active", ti["hover"]),
                          border=(8, 8, 8, 2), sticky="nsew")
        st.layout("TNotebook.Tab", [("Rounded.tab", {"sticky": "nswe", "children": [
            ("Notebook.padding", {"side": "top", "sticky": "nswe", "children": [
                ("Notebook.label", {"side": "top", "sticky": ""})]})]})])
        st.configure("TNotebook", tabmargins=(2, 6, 2, 0), bordercolor="#CDD3DD",
                     lightcolor="white", darkcolor="#CDD3DD", background=PALE)
        st.configure("TNotebook.Tab", padding=(18, 7), font=("Segoe UI Semibold", 10),
                     foreground="#7A8296", background=PALE)
        st.map("TNotebook.Tab",
               foreground=[("selected", NAVY), ("active", "#4A5368")],
               padding=[("selected", (18, 7))],
               expand=[("selected", (0, 0, 0, 0))])
        st.configure("Horizontal.TProgressbar", background=ORANGE, troughcolor="#E1E5EC")

    # ---- layout ---------------------------------------------------------
    def _build(self):
        header = tk.Frame(self, bg=NAVY, height=54)
        header.pack(fill="x")
        try:
            if os.path.exists(ICON_PNG):
                img = Image.open(ICON_PNG).resize((34, 34), Image.Resampling.LANCZOS)
                self._hdr_icon = ImageTk.PhotoImage(img)
                tk.Label(header, image=self._hdr_icon, bg=NAVY).pack(side="left", padx=(14, 8), pady=10)
        except Exception:
            pass
        tk.Label(header, text=APP_NAME, bg=NAVY, fg="white",
                 font=("Segoe UI Semibold", 15)).pack(side="left", pady=10)
        tk.Label(header, text="OS VectorMap Local 1:10,000 raster  →  MapInfo", bg=NAVY, fg="#AFC0DD",
                 font=("Segoe UI", 10)).pack(side="left", padx=14)
        tk.Label(header, text=f"v{APP_VERSION}", bg=NAVY, fg="#AFC0DD",
                 font=("Segoe UI", 9)).pack(side="right", padx=14)

        self.nb = ttk.Notebook(self)
        self.nb.pack(fill="both", expand=True, padx=12, pady=(10, 12))
        self.main = ttk.Frame(self.nb)
        self.settings_tab = ttk.Frame(self.nb)
        self._cog_img = ImageTk.PhotoImage(make_cog_image(20))
        self._tab_spacer = ImageTk.PhotoImage(Image.new("RGBA", (1, 20), (0, 0, 0, 0)))  # match cog height
        self.nb.add(self.main, text="Extract", image=self._tab_spacer, compound="left")
        self.nb.add(self.settings_tab, text=" Settings", image=self._cog_img, compound="left")
        self._build_main()
        self._build_settings()

    def _card(self, parent, title):
        f = ttk.Frame(parent, style="Card.TFrame", padding=12)
        f.pack(fill="x", pady=(0, 10))
        ttk.Label(f, text=title, style="Head.TLabel").grid(row=0, column=0, columnspan=4, sticky="w", pady=(0, 6))
        return f

    def _build_main(self):
        left = ttk.Frame(self.main, padding=(4, 8, 10, 4))
        left.pack(side="left", fill="y")
        right = ttk.Frame(self.main, padding=(4, 8, 4, 4))
        right.pack(side="left", fill="both", expand=True)

        # --- site -----------------------------------------------------
        c = self._card(left, "1  Site location")
        self.loc_mode = tk.StringVar(value="centre")
        radios = ttk.Frame(c, style="Card.TFrame")
        radios.grid(row=1, column=0, columnspan=4, sticky="w")
        for txt, val in (("Centre grid ref", "centre"), ("South-west corner", "sw"), ("Shapefile(s)", "shp")):
            ttk.Radiobutton(radios, text=txt, value=val, variable=self.loc_mode,
                            command=self._mode_changed).pack(side="left", padx=(0, 16))

        self.gr_frame = ttk.Frame(c, style="Card.TFrame")
        self.gr_frame.grid(row=2, column=0, columnspan=4, sticky="we", pady=(8, 0))
        ttk.Label(self.gr_frame, text="Grid ref", style="Card.TLabel", width=10).pack(side="left")
        self.gr = tk.StringVar()
        ttk.Entry(self.gr_frame, textvariable=self.gr, width=26).pack(side="left")
        ttk.Label(self.gr_frame, text="  e.g. ST 22500 27500", style="Hint.TLabel").pack(side="left")

        self.sw_frame = ttk.Frame(c, style="Card.TFrame")
        ttk.Label(self.sw_frame, text="Easting", style="Card.TLabel", width=10).pack(side="left")
        self.sw_e = tk.StringVar()
        ttk.Entry(self.sw_frame, textvariable=self.sw_e, width=10).pack(side="left")
        ttk.Label(self.sw_frame, text="  Northing", style="Card.TLabel").pack(side="left")
        self.sw_n = tk.StringVar()
        ttk.Entry(self.sw_frame, textvariable=self.sw_n, width=10).pack(side="left", padx=(6, 0))

        self.shp_frame = ttk.Frame(c, style="Card.TFrame")
        lf = ttk.Frame(self.shp_frame, style="Card.TFrame")
        lf.pack(fill="x")
        self.shp_list = tk.Listbox(lf, height=3, width=44, relief="solid", bd=1, highlightthickness=0,
                                   font=("Segoe UI", 9), selectbackground=ORANGE, activestyle="none")
        self.shp_list.pack(side="left", fill="x", expand=True)
        bf = ttk.Frame(lf, style="Card.TFrame")
        bf.pack(side="left", padx=(6, 0), anchor="n")
        ttk.Button(bf, text="Add…", command=self._add_shp, width=8).pack(pady=(0, 3))
        ttk.Button(bf, text="Remove", command=self._remove_shp, width=8).pack()
        brow = ttk.Frame(self.shp_frame, style="Card.TFrame")
        brow.pack(fill="x", pady=(6, 0))
        ttk.Label(brow, text="Buffer (m)", style="Card.TLabel", width=10).pack(side="left")
        self.shp_buffer = tk.StringVar()
        ttk.Entry(brow, textvariable=self.shp_buffer, width=8).pack(side="left")
        ttk.Label(brow, text="  added round the most N / S / E / W points", style="Hint.TLabel").pack(side="left")
        self.shp_convert = tk.BooleanVar()
        ttk.Checkbutton(self.shp_frame, text="Convert the shapefile(s) to MapInfo TAB in the output",
                        variable=self.shp_convert).pack(anchor="w", pady=(6, 0))

        sz = ttk.Frame(c, style="Card.TFrame")
        self.size_frame = sz
        sz.grid(row=3, column=0, columnspan=4, sticky="w", pady=(8, 0))
        ttk.Label(sz, text="Width (m)", style="Card.TLabel", width=10).pack(side="left")
        self.width = tk.StringVar()
        ttk.Entry(sz, textvariable=self.width, width=8).pack(side="left")
        ttk.Label(sz, text="   Height (m)", style="Card.TLabel").pack(side="left")
        self.height = tk.StringVar()
        ttk.Entry(sz, textvariable=self.height, width=8).pack(side="left", padx=(6, 0))
        self.sw_hint = ttk.Label(c, text="", style="Hint.TLabel")
        self.sw_hint.grid(row=4, column=0, columnspan=4, sticky="w", pady=(4, 0))

        # --- output type ----------------------------------------------
        c = self._card(left, "2  Output")
        self.out_mode = tk.StringVar(value="tiles")
        ttk.Radiobutton(c, text="Whole tiles (5 km × 5 km, as supplied)", value="tiles",
                        variable=self.out_mode, command=self.schedule_preview).grid(row=1, column=0, sticky="w")
        ttk.Radiobutton(c, text="Exact crop to site box (one image)", value="crop",
                        variable=self.out_mode, command=self.schedule_preview).grid(row=2, column=0, sticky="w")

        # --- destination ------------------------------------------------
        c = self._card(left, "3  Save to")
        ttk.Label(c, text="Site name", style="Card.TLabel").grid(row=1, column=0, sticky="w")
        self.site_name = tk.StringVar()
        e = ttk.Entry(c, textvariable=self.site_name, width=34)
        e.grid(row=1, column=1, columnspan=2, sticky="w", pady=2)
        e.bind("<Key>", lambda _e: setattr(self, "_name_auto", False))
        ttk.Label(c, text="Folder", style="Card.TLabel").grid(row=2, column=0, sticky="w")
        self.out_dir = tk.StringVar()
        self.out_entry = ttk.Entry(c, textvariable=self.out_dir, width=34)
        self.out_entry.grid(row=2, column=1, sticky="w", pady=2)
        self.out_browse = ttk.Button(c, text="Browse…", command=self._browse_out)
        self.out_browse.grid(row=2, column=2, padx=(6, 0))
        self.shp_same_folder = tk.BooleanVar()
        self.same_cb = ttk.Checkbutton(c, text="Save in the same folder as the shapefile",
                                       variable=self.shp_same_folder, command=self._same_folder_changed)
        self.same_cb.grid(row=3, column=1, columnspan=2, sticky="w", pady=(4, 0))
        self.subfolder = tk.BooleanVar(value=False)
        ttk.Checkbutton(c, text="Create a subfolder named after the site", variable=self.subfolder)\
            .grid(row=4, column=1, columnspan=2, sticky="w", pady=(2, 0))

        # --- run ---------------------------------------------------------
        runrow = ttk.Frame(left)
        runrow.pack(fill="x", pady=(4, 0))
        self.run_btn = ttk.Button(runrow, text="Extract tiles", style="Run.TButton", command=self.run)
        self.run_btn.pack(side="left")
        self.open_btn = ttk.Button(runrow, text="Open output folder", command=self._open_out, state="disabled")
        self.open_btn.pack(side="left", padx=10)
        self.progress = ttk.Progressbar(left, mode="determinate", maximum=1.0)
        self.progress.pack(fill="x", pady=(10, 4))

        self.log = tk.Text(left, height=6, width=56, bg="white", fg="#26304A", relief="flat",
                           font=("Consolas", 9), wrap="word", state="disabled")
        self.log.pack(fill="both", expand=True)

        # --- preview -----------------------------------------------------
        pv = ttk.Frame(right, style="Card.TFrame", padding=10)
        pv.pack(fill="both", expand=True)
        ttk.Label(pv, text="Preview", style="Head.TLabel").pack(anchor="w")
        self.canvas = tk.Canvas(pv, width=PREVIEW_PX, height=PREVIEW_PX, bg="#E6E9EF",
                                highlightthickness=0)
        self.canvas.pack(pady=(6, 6))
        self.preview_info = ttk.Label(pv, text="", style="Hint.TLabel", justify="left")
        self.preview_info.pack(anchor="w")

        for v in (self.gr, self.sw_e, self.sw_n, self.width, self.height, self.shp_buffer):
            v.trace_add("write", lambda *_: self.schedule_preview())

    def _build_settings(self):
        c = ttk.Frame(self.settings_tab, style="Card.TFrame", padding=16)
        c.pack(fill="x", padx=4, pady=8)
        ttk.Label(c, text="Data source", style="Head.TLabel").grid(row=0, column=0, columnspan=3, sticky="w")
        ttk.Label(c, text="VectorMap tile archive", style="Card.TLabel").grid(row=1, column=0, sticky="w", pady=4)
        self.data_root = tk.StringVar()
        ttk.Entry(c, textvariable=self.data_root, width=60).grid(row=1, column=1, sticky="w")
        ttk.Button(c, text="Browse…", command=self._browse_data).grid(row=1, column=2, padx=6)
        ttk.Label(c, text="Folder holding the hp, nn, st, tq … subfolders of TIF + TAB tiles",
                  style="Hint.TLabel").grid(row=2, column=1, sticky="w")
        self.data_root.trace_add("write", lambda *_: self.schedule_preview())

        ttk.Label(c, text="Extras", style="Head.TLabel").grid(row=3, column=0, columnspan=3, sticky="w", pady=(16, 4))
        self.write_wor = tk.BooleanVar()
        self.write_boundary = tk.BooleanVar()
        self.open_when_done = tk.BooleanVar()
        ttk.Checkbutton(c, text="Write a MapInfo workspace (.WOR) that opens everything",
                        variable=self.write_wor).grid(row=4, column=1, sticky="w")
        ttk.Checkbutton(c, text="Write the site boundary (TAB if GDAL is installed, otherwise MIF/MID)",
                        variable=self.write_boundary).grid(row=5, column=1, sticky="w")
        self.delete_mif = tk.BooleanVar()
        ttk.Checkbutton(c, text="Delete the boundary MIF/MID once the TAB has been made",
                        variable=self.delete_mif).grid(row=6, column=1, sticky="w")
        ttk.Checkbutton(c, text="Open the output folder when finished",
                        variable=self.open_when_done).grid(row=7, column=1, sticky="w")

        gdal = core.find_ogr2ogr()
        ttk.Label(c, text=f"GDAL: {'found - ' + gdal if gdal else 'not found (boundary will be MIF/MID)'}",
                  style="Hint.TLabel").grid(row=8, column=1, sticky="w", pady=(10, 0))
        ttk.Label(c, text=f"Settings file: {SETTINGS_FILE}", style="Hint.TLabel")\
            .grid(row=9, column=1, sticky="w")
        ttk.Label(c, text=f"{APP_NAME} v{APP_VERSION}  ·  OS data © Crown copyright and database right",
                  style="Hint.TLabel").grid(row=10, column=1, sticky="w", pady=(10, 0))

    # ---- field helpers --------------------------------------------------
    def _load_fields(self):
        s = self.settings
        self.data_root.set(s["data_root"])
        self.out_dir.set(s["out_dir"])
        self.write_wor.set(s["write_wor"])
        self.write_boundary.set(s["write_boundary"])
        self.open_when_done.set(s["open_when_done"])
        self.delete_mif.set(s.get("delete_boundary_mif", True))
        self.shp_buffer.set(s.get("shp_buffer", "300"))
        self.shp_same_folder.set(s.get("shp_same_folder", False))
        self.shp_convert.set(s.get("shp_convert", True))
        for p in s.get("last_shapes", []):          # reload last session's shapefiles
            try:
                sh = core.load_shapefile(p)
                self.shapes.append(sh)
                self.shp_list.insert("end", os.path.basename(p) + (f"   ({sh.note})" if sh.note else ""))
            except Exception:
                pass
        self.loc_mode.set(s.get("last_mode", "centre"))
        self.gr.set(s.get("last_gr", ""))
        self.width.set(s.get("last_width", "1000"))
        self.height.set(s.get("last_height", "1000"))
        self._mode_changed()

    def _collect_settings(self):
        self.settings.update({
            "data_root": self.data_root.get().strip(),
            "out_dir": self.settings.get("out_dir", "") if self._out_is_auto() else self.out_dir.get().strip(),
            "write_wor": self.write_wor.get(),
            "write_boundary": self.write_boundary.get(),
            "open_when_done": self.open_when_done.get(),
            "last_mode": self.loc_mode.get(),
            "last_gr": self.gr.get(),
            "last_width": self.width.get(),
            "last_height": self.height.get(),
            "delete_boundary_mif": self.delete_mif.get(),
            "shp_buffer": self.shp_buffer.get(),
            "shp_same_folder": self.shp_same_folder.get(),
            "shp_convert": self.shp_convert.get(),
            "last_shp_dir": self.settings.get("last_shp_dir", ""),
            "last_shapes": [sh.path for sh in self.shapes],
        })
        save_settings(self.settings)

    def _mode_changed(self):
        # carry the current site across when switching between centre and SW corner
        prev = getattr(self, "_prev_mode", None)
        mode = self.loc_mode.get()
        box = None
        if prev and prev != mode:
            try:
                box = self.get_box(prev)
            except Exception:
                box = None
        self._prev_mode = mode
        for fr in (self.gr_frame, self.sw_frame, self.shp_frame):
            fr.grid_remove()
        if mode == "shp":
            self.shp_frame.grid(row=2, column=0, columnspan=4, sticky="we", pady=(8, 0))
            self.size_frame.grid_remove()
            self.sw_hint.config(text="Site = extent of all shapefiles plus the buffer.")
            self.same_cb.grid()
        else:
            self.size_frame.grid()
            self.same_cb.grid_remove()
            if mode == "centre":
                self.gr_frame.grid(row=2, column=0, columnspan=4, sticky="we", pady=(8, 0))
                if box:
                    self.gr.set(core.format_grid_ref(*box.centre))
                    self.width.set(f"{box.width:.0f}"); self.height.set(f"{box.height:.0f}")
                self.sw_hint.config(text="Box is centred on the grid reference.")
            else:
                self.sw_frame.grid(row=2, column=0, columnspan=4, sticky="we", pady=(8, 0))
                if box:
                    self.sw_e.set(f"{box.minx:.0f}"); self.sw_n.set(f"{box.miny:.0f}")
                    self.width.set(f"{box.width:.0f}"); self.height.set(f"{box.height:.0f}")
                self.sw_hint.config(text="Box extends north and east from the corner.")
        self._name_auto = True
        self._same_folder_changed()
        self.schedule_preview()

    # ---- shapefiles ----------------------------------------------------
    def _add_shp(self):
        paths = filedialog.askopenfilenames(
            title="Add shapefile(s)", filetypes=[("Shapefiles", "*.shp"), ("All files", "*.*")],
            initialdir=self.settings.get("last_shp_dir") or None)
        added = False
        for p in paths:
            p = os.path.normpath(p)
            if any(os.path.normcase(sh.path) == os.path.normcase(p) for sh in self.shapes):
                continue
            try:
                sh = core.load_shapefile(p)
            except Exception as exc:
                messagebox.showerror(APP_NAME, str(exc))
                continue
            self.shapes.append(sh)
            self.shp_list.insert("end", os.path.basename(p) + (f"   ({sh.note})" if sh.note else ""))
            self.settings["last_shp_dir"] = os.path.dirname(p)
            added = True
        if added:
            self._name_auto = True
            self._same_folder_changed()
            self.schedule_preview()

    def _remove_shp(self):
        for i in reversed(self.shp_list.curselection()):
            self.shp_list.delete(i)
            del self.shapes[i]
        self._same_folder_changed()
        self.schedule_preview()

    def _out_is_auto(self):
        return self.loc_mode.get() == "shp" and self.shp_same_folder.get()

    def _same_folder_changed(self):
        if self._out_is_auto():
            if not getattr(self, "_saved_out", None):
                self._saved_out = self.out_dir.get()
            self.out_dir.set(os.path.dirname(self.shapes[0].path) if self.shapes else "")
            self.out_entry.state(["disabled"]); self.out_browse.state(["disabled"])
        else:
            if getattr(self, "_saved_out", None) is not None:
                self.out_dir.set(self._saved_out)
                self._saved_out = None
            self.out_entry.state(["!disabled"]); self.out_browse.state(["!disabled"])

    def _num(self, var, label):
        try:
            return float(var.get().replace(",", "").strip())
        except ValueError:
            raise ValueError(f"{label} must be a number")

    def get_box(self, mode=None):
        mode = mode or self.loc_mode.get()
        if mode == "shp":
            box = core.box_from_shapefiles(self.shapes, self._num(self.shp_buffer, "Buffer"))
            box.validate()
            return box
        w, h = self._num(self.width, "Width"), self._num(self.height, "Height")
        if mode == "centre":
            e, n = core.parse_grid_ref(self.gr.get())
            box = core.SiteBox.from_centre(e, n, w, h)
        else:
            box = core.SiteBox.from_sw(self._num(self.sw_e, "Easting"),
                                       self._num(self.sw_n, "Northing"), w, h)
        box.validate()
        return box

    def _browse_out(self):
        d = filedialog.askdirectory(initialdir=self.out_dir.get() or None, title="Output folder")
        if d:
            self.out_dir.set(os.path.normpath(d))

    def _browse_data(self):
        d = filedialog.askdirectory(initialdir=self.data_root.get() or None, title="VectorMap tile archive")
        if d:
            self.data_root.set(os.path.normpath(d))

    def _open_out(self):
        if self._last_out and os.path.isdir(self._last_out):
            open_folder(self._last_out)

    def write_log(self, msg):
        self.log.config(state="normal")
        self.log.insert("end", msg + "\n")
        self.log.see("end")
        self.log.config(state="disabled")

    # ---- preview --------------------------------------------------------
    def schedule_preview(self):
        if self._preview_job:
            self.after_cancel(self._preview_job)
        self._preview_job = self.after(500, self._start_preview)

    def _start_preview(self):
        self._preview_job = None
        try:
            box = self.get_box()
        except Exception as exc:
            self.preview_info.config(text=f"⚠ {exc}")
            return
        cx, cy = box.centre
        shp_mode = self.loc_mode.get() == "shp"
        shapes = list(self.shapes) if shp_mode else []
        if self._name_auto:
            if shp_mode:
                self.site_name.set(shapes[0].name if shapes else "Site")
            else:
                self.site_name.set(f"{core.format_grid_ref(cx, cy).replace(' ', '')}_"
                                   f"{box.width:.0f}x{box.height:.0f}")
        n_tiles = len(core.tiles_for_box(box))
        if n_tiles > 400:
            self.canvas.delete("all")
            self.preview_info.config(text=f"⚠ Site touches {n_tiles} tiles - too large to preview.")
            return
        self._preview_gen += 1
        gen = self._preview_gen
        mode = self.out_mode.get()
        root = self.data_root.get().strip()
        self.preview_info.config(text="Drawing preview…")

        def work():
            try:
                archive = core.TileArchive(root)
                archive.check_root()
                img, info = self.renderer.render(box, archive, mode, shapes=shapes)
                self.after(0, lambda: self._show_preview(gen, box, img, info, mode))
            except Exception as exc:
                self.after(0, lambda: gen == self._preview_gen and
                           self.preview_info.config(text=f"⚠ {exc}"))
        threading.Thread(target=work, daemon=True).start()

    def _show_preview(self, gen, box, img, info, mode):
        if gen != self._preview_gen:
            return
        self._pv_img = ImageTk.PhotoImage(img)
        self.canvas.delete("all")
        self.canvas.create_image(0, 0, anchor="nw", image=self._pv_img)
        cx, cy = box.centre
        present = info["present"]
        lines = [f"Centre {core.format_grid_ref(cx, cy)}   ·   SW corner {box.minx:.0f}, {box.miny:.0f}   ·   "
                 f"{box.width:.0f} m × {box.height:.0f} m"]
        if mode == "tiles":
            mb = sum(os.path.getsize(t.tif) for t in present) / 1e6
            lines.append(f"Whole tiles: {len(present)} tile(s), about {mb:.0f} MB  -  "
                         + ", ".join(t.name for t in present))
        else:
            w = round(box.width / core.PIXEL_M); h = round(box.height / core.PIXEL_M)
            lines.append(f"Exact crop: one image about {w:,} × {h:,} px from {len(present)} tile(s)")
            if w * h > core.CROP_PIXEL_LIMIT:
                lines.append("⚠ Too large for a single crop - use Whole tiles")
        if info["missing"]:
            lines.append(f"No tile in archive (sea / no coverage): {', '.join(info['missing'])}")
        if not info["map_shown"]:
            lines.append("Large area - map imagery hidden in preview")
        self.preview_info.config(text="\n".join(lines))
        self.run_btn.config(text="Extract tiles" if mode == "tiles" else "Extract crop")

    # ---- run ------------------------------------------------------------
    def run(self):
        if self._running:
            return
        try:
            box = self.get_box()
        except Exception as exc:
            messagebox.showerror(APP_NAME, str(exc))
            return
        out = self.out_dir.get().strip()
        if not out:
            messagebox.showerror(APP_NAME, "Choose an output folder first.")
            return
        opts = core.RunOptions(
            data_root=self.data_root.get().strip(),
            out_dir=out,
            site_name=self.site_name.get().strip() or "Site",
            mode=self.out_mode.get(),
            make_subfolder=self.subfolder.get(),
            write_wor=self.write_wor.get(),
            write_boundary=self.write_boundary.get(),
            delete_boundary_mif=self.delete_mif.get(),
            shapefiles=list(self.shapes) if self.loc_mode.get() == "shp" else [],
            convert_shapefiles=self.shp_convert.get(),
        )
        self._collect_settings()
        self._running = True
        self.run_btn.config(state="disabled")
        self.open_btn.config(state="disabled")
        self.progress["value"] = 0
        self.log.config(state="normal"); self.log.delete("1.0", "end"); self.log.config(state="disabled")

        def log(msg):
            self.after(0, lambda m=msg: self.write_log(m))

        def prog(f):
            self.after(0, lambda v=f: self.progress.configure(value=v))

        def work():
            try:
                res = core.run_extraction(box, opts, log, prog)
                self.after(0, lambda: self._done(res, None))
            except Exception as exc:
                self.after(0, lambda e=exc: self._done(None, e))
        threading.Thread(target=work, daemon=True).start()

    def _done(self, res, err):
        self._running = False
        self.run_btn.config(state="normal")
        if err:
            self.write_log(f"ERROR: {err}")
            messagebox.showerror(APP_NAME, str(err))
            return
        self.progress["value"] = 1.0
        self._last_out = res.out_dir
        self.open_btn.config(state="normal")
        if self.open_when_done.get():
            try:
                open_folder(res.out_dir)
            except Exception:
                pass

    def _on_close(self):
        self._collect_settings()
        self.destroy()


if __name__ == "__main__":
    App().mainloop()
