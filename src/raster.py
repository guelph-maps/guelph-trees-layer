"""Render bus stop points into raster (PNG) map tiles using Pillow.

Pure Python -- no native dependencies, runs the same on Windows and Linux.

Each stop is a dot: filled blue for a fixed-route stop, a hollow blue ring for
an on-demand-only pick-up point (config.FIXED_ROUTE_KEY), both with a white
edge so they read over imagery. From LABEL_REF_MIN_ZOOM the dot carries the
stop number -- the value a mapper types into ``ref`` -- and at NAME_ZOOM the
number and the City's stop name, where there is room.

Labels are placed once per zoom, globally, against a spatial hash, so a label
straddling a tile seam is drawn identically on both sides. Every stop's number
is placed first, right of the dot or else left, wherever it collides with
nothing; then, at NAME_ZOOM, each number is upgraded to number + name where the
longer label fits. Below NAME_ZOOM a number that fits nowhere is dropped (the
dot is still drawn); at NAME_ZOOM, the zoom a mapper edits at, every stop
keeps its number even if it overlaps -- the station's platforms are 6-11 m
apart, and a missing number there is worse than a crowded one.
"""

import json
import os
from collections import defaultdict

from PIL import Image, ImageDraw, ImageFont

from src import config
from src.tilemath import TILE_SIZE, lonlat_to_pixel

STOP_COLOR = (25, 118, 210, 255)       # blue; passes WCAG AA against the halo
EDGE_COLOR = (255, 255, 255, 255)
HALO_COLOR = (255, 255, 255, 235)
LABEL_COLOR = (13, 71, 161, 255)       # darker blue for text
FONT_PATH = os.path.join(config.ASSETS_DIR, "font", "DejaVuSans-Bold.ttf")
FONT_SIZE = 11
STROKE_WIDTH = 1                       # white halo width, in pixels

# Dot radius per zoom, in pixels (white edge included).
DOT_RADIUS = {13: 2, 14: 3, 15: 4, 16: 5, 17: 6}
RING_WIDTH = 2                         # hollow on-demand dot's ring

LABEL_REF_MIN_ZOOM = 15                # stop numbers from here
NAME_ZOOM = 17                         # numbers + names; nothing dropped
LABEL_GAP_PX = 3                       # dot edge -> label text
_GRID_CELL = 128                       # spatial-hash cell for collision lookups


def build_raster(slim_path=None):
    """Render PNG tiles for every zoom in config.RASTER_ZOOMS.

    Returns a dict {zoom: tile_count}.
    """
    slim_path = slim_path or config.SLIM_PATH
    font = ImageFont.truetype(FONT_PATH, FONT_SIZE)
    return {z: _render_zoom(slim_path, z, font) for z in config.RASTER_ZOOMS}


def _render_zoom(slim_path, zoom, font):
    stops = _read_stops(slim_path, zoom)
    radius = DOT_RADIUS.get(zoom, DOT_RADIUS[max(DOT_RADIUS)])
    labels = (_place_labels(stops, font, zoom, radius)
              if zoom >= LABEL_REF_MIN_ZOOM else [])
    print(f"Raster z{zoom}: {len(stops):,} stops, {len(labels):,} labels placed")

    dot_tiles = defaultdict(list)
    for s in stops:
        x, y = s["xy"]
        box = (x - radius, y - radius, x + radius, y + radius)
        for key in _tiles_touching(box):
            dot_tiles[key].append(s)

    label_tiles = defaultdict(list)
    for x, y, text, anchor, box in labels:
        for key in _tiles_touching(box):
            label_tiles[key].append((x, y, text, anchor))

    out_dir = os.path.join(config.RASTER_TILE_DIR, str(zoom))
    keys = set(dot_tiles) | set(label_tiles)
    made_dirs = set()
    written = 0
    for tx, ty in keys:
        img = _render_tile(tx, ty, dot_tiles.get((tx, ty), ()),
                           label_tiles.get((tx, ty), ()), font, radius)
        if img.getbbox() is None:
            continue
        tdir = os.path.join(out_dir, str(tx))
        if tdir not in made_dirs:
            os.makedirs(tdir, exist_ok=True)
            made_dirs.add(tdir)
        img.save(os.path.join(tdir, f"{ty}.png"), optimize=True)
        written += 1
    return written


def _read_stops(slim_path, zoom):
    """[{xy, ref, name, fixed_route}, ...] in global pixels at ``zoom``."""
    stops = []
    with open(slim_path, encoding="utf-8") as f:
        for line in f:
            feat = json.loads(line)
            lon, lat = feat["geometry"]["coordinates"][:2]
            p = feat["properties"]
            stops.append({
                "xy": lonlat_to_pixel(lon, lat, zoom),
                "ref": str(p["ref"]),
                "name": p.get("name") or "",
                "fixed_route": p.get("fixed_route") != "no",
            })
    return stops


def label_texts(stop, zoom):
    """The label texts a stop tries at ``zoom``, longest first."""
    if zoom >= NAME_ZOOM and stop["name"]:
        return [f"{stop['ref']} {stop['name']}", stop["ref"]]
    return [stop["ref"]]


def _place_labels(stops, font, zoom, radius):
    """Greedy, collision-checked label placement for one zoom.

    Stops are taken in stop-number order so the result is the same on every
    run. A label goes right of its dot, else left, wherever it overlaps no
    placed label and no other stop's dot. Returns
    [(x, y, text, anchor, box), ...] in global pixels.
    """
    pad = STROKE_WIDTH
    grid = defaultdict(list)

    def cells(x0, y0, x1, y1):
        for cx in range(int(x0 // _GRID_CELL), int(x1 // _GRID_CELL) + 1):
            for cy in range(int(y0 // _GRID_CELL), int(y1 // _GRID_CELL) + 1):
                yield (cx, cy)

    def collides(box):
        x0, y0, x1, y1 = box
        for key in cells(*box):
            for ox0, oy0, ox1, oy1 in grid.get(key, ()):
                if x0 < ox1 and x1 > ox0 and y0 < oy1 and y1 > oy0:
                    return True
        return False

    def add(box):
        for key in cells(*box):
            grid[key].append(box)

    # Dots are obstacles too: a label printed across the next stop's dot hides
    # the thing the layer exists to show.
    for s in stops:
        x, y = s["xy"]
        add((x - radius, y - radius, x + radius, y + radius))

    def candidate(s, text, side):
        x, y = s["xy"]
        w = font.getlength(text)
        off = radius + LABEL_GAP_PX
        if side == "right":
            lx, anchor, box = x + off, "lm", (x + off - pad, y - FONT_SIZE / 2 - pad,
                                              x + off + w + pad, y + FONT_SIZE / 2 + pad)
        else:
            lx, anchor, box = x - off, "rm", (x - off - w - pad, y - FONT_SIZE / 2 - pad,
                                              x - off + pad, y + FONT_SIZE / 2 + pad)
        return lx, y, text, anchor, box

    def first_fit(s, text):
        for side in ("right", "left"):
            cand = candidate(s, text, side)
            if not collides(cand[4]):
                return cand
        return None

    # Numbers first, for every stop: a name placed early must not be what
    # pushes a later stop's number into overprinting it.
    order = sorted(stops, key=lambda s: (len(s["ref"]), s["ref"]))
    placed = {}
    for i, s in enumerate(order):
        cand = first_fit(s, s["ref"])
        if cand is None and zoom >= NAME_ZOOM:
            cand = candidate(s, s["ref"], "right")
        if cand:
            add(cand[4])
            placed[i] = cand

    # Then upgrade a number to number + name wherever the longer label fits.
    for i, s in enumerate(order):
        texts = label_texts(s, zoom)
        if i not in placed or len(texts) < 2:
            continue
        box = placed[i][4]
        for key in cells(*box):
            grid[key].remove(box)
        cand = first_fit(s, texts[0])
        if cand:
            placed[i] = cand
        add(placed[i][4])
    return [placed[i] for i in sorted(placed)]


def _tiles_touching(bbox, pad=STROKE_WIDTH + 1):
    x0, y0, x1, y1 = bbox
    for tx in range(int((x0 - pad) // TILE_SIZE), int((x1 + pad) // TILE_SIZE) + 1):
        for ty in range(int((y0 - pad) // TILE_SIZE), int((y1 + pad) // TILE_SIZE) + 1):
            yield (tx, ty)


def _render_tile(tx, ty, stops, labels, font, radius):
    img = Image.new("RGBA", (TILE_SIZE, TILE_SIZE), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)
    ox, oy = tx * TILE_SIZE, ty * TILE_SIZE

    for s in stops:
        x, y = s["xy"][0] - ox, s["xy"][1] - oy
        box = (x - radius, y - radius, x + radius, y + radius)
        if s["fixed_route"]:
            draw.ellipse(box, fill=STOP_COLOR, outline=EDGE_COLOR, width=1)
        else:
            draw.ellipse(box, fill=EDGE_COLOR)
            inner = (x - radius + 1, y - radius + 1, x + radius - 1, y + radius - 1)
            draw.ellipse(inner, outline=STOP_COLOR,
                         width=min(RING_WIDTH, max(1, radius - 1)))

    for x, y, text, anchor in labels:
        draw.text(
            (x - ox, y - oy), text, font=font, fill=LABEL_COLOR,
            stroke_width=STROKE_WIDTH, stroke_fill=HALO_COLOR, anchor=anchor,
        )
    return img
