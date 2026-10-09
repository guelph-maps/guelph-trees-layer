"""Render tree points into raster (PNG) map tiles using Pillow.

Pure Python -- no native dependencies, runs the same on Windows and Linux.

Each tree is a crown: a filled circle coloured by genus (GENUS_COLORS), with a
thin light edge from EDGE_MIN_ZOOM so it reads over both the standard map and
aerial imagery, and a fill that is slightly transparent so a crown overlapping
another still lets the one beneath show through.

Below SCALE_MIN_ZOOM every tree is the same small dot -- at z13-15 a street
tree is under a pixel across, and what the layer can usefully show there is
where the trees are and which genera dominate a street. From SCALE_MIN_ZOOM the
crown is drawn to an approximate real size estimated from the trunk diameter
(crown_radius_m), so a row of new plantings looks different from a row of old
maples. Larger crowns are drawn first, so a small tree under a big one stays
visible on top of it.

No labels: the vector layer carries the tags, and 42,000 genus names on the
tiles would bury the colours that already say it.

Crowns are drawn from small pre-rendered sprites, one per (colour, radius),
supersampled for smooth edges and alpha-composited into the tile. Drawing
straight onto an RGBA tile with ImageDraw would *replace* pixels rather than
blend them: the transparency would show the basemap through a crown but never
a neighbouring crown.
"""

import html
import json
import math
import os
from collections import defaultdict

from PIL import Image, ImageDraw

from src import config
from src.tilemath import TILE_SIZE, lonlat_to_pixel

# The ten most common genera in the City's inventory, most common first, as
# (genus, English label, RGBA). Together they are three trees in four; the rest
# share OTHER_COLOR. Broadleaf genera take bright, saturated hues -- none of
# them a mid green, which would vanish into park fill and into the canopy on
# imagery. The three conifers take dark blue-greens, so evergreens read as a
# family at a glance: spruce a blue-teal, pine a deep green, cedar a sea green.
GENUS_COLORS = [
    ("Acer", "Maple", (220, 38, 38, 255)),
    ("Picea", "Spruce", (0, 105, 125, 255)),
    ("Gleditsia", "Honey locust", (250, 204, 21, 255)),
    ("Tilia", "Linden", (163, 230, 53, 255)),
    ("Quercus", "Oak", (146, 84, 32, 255)),
    ("Pinus", "Pine", (20, 83, 45, 255)),
    ("Syringa", "Lilac", (168, 85, 247, 255)),
    ("Thuja", "Cedar", (13, 148, 136, 255)),
    ("Celtis", "Hackberry", (236, 72, 153, 255)),
    ("Platanus", "Plane tree", (249, 115, 22, 255)),
]
OTHER_COLOR = (59, 130, 246, 255)      # any other genus: a plain blue
UNKNOWN_COLOR = (148, 148, 148, 255)   # genus not recorded: neutral grey
OTHER_LABEL = "Other genus"
UNKNOWN_LABEL = "Genus not recorded"
_COLOR_BY_GENUS = {g: c for g, _label, c in GENUS_COLORS}

FILL_ALPHA = 205                       # of 255: the basemap shows faintly through
EDGE_COLOR = (255, 255, 255, 230)
EDGE_PX = 1                            # light edge width
# The edge starts here. At z13-14 a dot is 2-3 px across and an edge would be
# all there was of it -- the city turns to pale lines; from z15 a dot is big
# enough to carry one, and without it a pine or a spruce disappears into the
# canopy on imagery.
EDGE_MIN_ZOOM = 15

# Dot radius below SCALE_MIN_ZOOM, in pixels (edge included): a density map,
# not crowns.
DOT_RADIUS = {13: 1.1, 14: 1.5, 15: 2.75}

# From this zoom a crown is drawn to scale. At z16 a pixel is 1.7 m, so a
# mature tree is already a few pixels across and size starts to say something.
SCALE_MIN_ZOOM = 16
# Floor on a drawn crown's radius. Under 3 px the edge is most of the circle
# and the colour, which is the point, shrinks to a speck; it also keeps a z16
# crown no smaller than a z15 dot.
MIN_CROWN_PX = 3.0

# Crown radius from trunk diameter, in metres: CROWN_A_M + CROWN_B_M * dbh_cm,
# clamped to [CROWN_MIN_M, CROWN_MAX_M]. Urban crown diameter runs roughly
# 0.15-0.2 m per cm of DBH; this gives a 5 cm new planting 1.5 m, a 30 cm tree
# 3.3 m and an 80 cm one 7 m. It is an estimate for drawing, not a measurement:
# it says "big tree here", not how far the branches reach.
CROWN_A_M = 1.0
CROWN_B_M = 0.075
CROWN_MIN_M = 1.5
CROWN_MAX_M = 7.5
DEFAULT_CROWN_M = 2.5                  # DBH missing or implausible

_EARTH_M_PER_PX_Z0 = 156543.03392      # Web Mercator ground resolution at the equator
_COS_LAT = math.cos(math.radians(config.GUELPH_CENTER[0]))
_SUPERSAMPLE = 4                       # sprites are drawn 4x and scaled down
_RADIUS_STEP = 0.25                    # sprite radii are quantised to this, in px


def build_raster(slim_path=None):
    """Render PNG tiles for every zoom in config.RASTER_ZOOMS.

    Returns a dict {zoom: tile_count}.
    """
    slim_path = slim_path or config.SLIM_PATH
    trees = _read_trees(slim_path)
    sprites = {}
    return {z: _render_zoom(trees, z, sprites) for z in config.RASTER_ZOOMS}


def genus_color(genus):
    """The RGBA crown colour for a genus: its own, OTHER_COLOR, or UNKNOWN_COLOR."""
    if not genus:
        return UNKNOWN_COLOR
    return _COLOR_BY_GENUS.get(genus, OTHER_COLOR)


def valid_dbh(dbh_cm):
    """The DBH in cm if it is a plausible tree's, else None.

    Zero, negative and anything over config.MAX_DBH_CM are data-entry errors;
    drawing an 879 cm tree as the largest crown in the city would advertise the
    one value in the extract that is certainly wrong.
    """
    if not isinstance(dbh_cm, (int, float)) or isinstance(dbh_cm, bool):
        return None
    if dbh_cm <= 0 or dbh_cm > config.MAX_DBH_CM:
        return None
    return dbh_cm


def crown_radius_m(dbh_cm):
    """Approximate crown radius in metres for a trunk diameter in cm."""
    dbh = valid_dbh(dbh_cm)
    if dbh is None:
        return DEFAULT_CROWN_M
    return min(max(CROWN_A_M + CROWN_B_M * dbh, CROWN_MIN_M), CROWN_MAX_M)


def metres_per_pixel(zoom):
    """Ground resolution at Guelph's latitude. The city spans 0.14 degrees of
    latitude, under 0.3% of scale -- one value for all of it is plenty."""
    return _EARTH_M_PER_PX_Z0 * _COS_LAT / (2 ** zoom)


def crown_radius_px(dbh_cm, zoom):
    """The radius a tree is drawn at, in pixels, at ``zoom``."""
    if zoom < SCALE_MIN_ZOOM:
        return DOT_RADIUS.get(zoom, DOT_RADIUS[max(DOT_RADIUS)])
    return max(crown_radius_m(dbh_cm) / metres_per_pixel(zoom), MIN_CROWN_PX)


def legend_html():
    """The palette as an HTML list, for {{LEGEND}} on the landing page."""
    rows = [(c, f"{html.escape(label)} <i>{html.escape(g)}</i>")
            for g, label, c in GENUS_COLORS]
    rows.append((OTHER_COLOR, html.escape(OTHER_LABEL)))
    rows.append((UNKNOWN_COLOR, html.escape(UNKNOWN_LABEL)))
    items = "\n".join(
        f'  <li><span class="swatch" style="background:rgb({r},{g},{b})">'
        f"</span><span>{text}</span></li>"
        for (r, g, b, _a), text in rows
    )
    return f'<ul class="legend">\n{items}\n</ul>'


def _read_trees(slim_path):
    """[(lon, lat, rgba, dbh_cm or None), ...] from the slim GeoJSONL."""
    trees = []
    with open(slim_path, encoding="utf-8") as f:
        for line in f:
            if not line.strip():
                continue
            feat = json.loads(line)
            lon, lat = feat["geometry"]["coordinates"][:2]
            p = feat["properties"]
            trees.append((lon, lat, genus_color(p.get("genus")),
                          valid_dbh(p.get("dbh_cm"))))
    return trees


def _render_zoom(trees, zoom, sprites):
    edge = zoom >= EDGE_MIN_ZOOM
    placed = []
    for lon, lat, color, dbh in trees:
        r = round(crown_radius_px(dbh, zoom) / _RADIUS_STEP) * _RADIUS_STEP
        x, y = lonlat_to_pixel(lon, lat, zoom)
        placed.append((r, x, y, color))
    # Largest first, so small crowns land on top. The sort is stable and the
    # input is in file order, so a re-run draws identical tiles.
    placed.sort(key=lambda t: -t[0])

    tiles = defaultdict(list)
    for r, x, y, color in placed:
        key = (color, r, edge)
        sprite = sprites.get(key)
        if sprite is None:
            sprite = sprites[key] = _sprite(color, r, edge)
        size = sprite.size[0]
        left, top = math.floor(x) - size // 2, math.floor(y) - size // 2
        for tx in range(left // TILE_SIZE, (left + size - 1) // TILE_SIZE + 1):
            for ty in range(top // TILE_SIZE, (top + size - 1) // TILE_SIZE + 1):
                tiles[(tx, ty)].append((left, top, sprite))
    print(f"Raster z{zoom}: {len(trees):,} trees in {len(tiles):,} tiles")

    out_dir = os.path.join(config.RASTER_TILE_DIR, str(zoom))
    made_dirs = set()
    written = 0
    for (tx, ty), crowns in tiles.items():
        img = _render_tile(tx, ty, crowns)
        if img.getbbox() is None:
            continue
        tdir = os.path.join(out_dir, str(tx))
        if tdir not in made_dirs:
            os.makedirs(tdir, exist_ok=True)
            made_dirs.add(tdir)
        img.save(os.path.join(tdir, f"{ty}.png"), optimize=True)
        written += 1
    return written


def _sprite(color, radius, edge):
    """One crown, centred in the middle pixel of an odd-sized RGBA image.

    Drawn at _SUPERSAMPLE times the size and scaled down, which is what gives a
    3 px crown a round edge instead of a jagged diamond.
    """
    size = 2 * math.ceil(radius) + 3
    ss = _SUPERSAMPLE
    big = Image.new("RGBA", (size * ss, size * ss), (0, 0, 0, 0))
    c = size * ss / 2
    rr = radius * ss
    fill = color[:3] + (FILL_ALPHA,)
    ImageDraw.Draw(big).ellipse(
        (c - rr, c - rr, c + rr, c + rr), fill=fill,
        outline=EDGE_COLOR if edge else None, width=EDGE_PX * ss,
    )
    # Pillow scales RGBA with premultiplied alpha, so the transparent corners
    # do not bleed black into the edge.
    return big.resize((size, size), Image.BOX)


def _render_tile(tx, ty, crowns):
    img = Image.new("RGBA", (TILE_SIZE, TILE_SIZE), (0, 0, 0, 0))
    ox, oy = tx * TILE_SIZE, ty * TILE_SIZE
    for left, top, sprite in crowns:
        x0, y0 = left - ox, top - oy
        w, h = sprite.size
        # alpha_composite refuses a negative destination, so a crown hanging
        # over the tile's edge is cropped to the part inside it.
        sx0, sy0 = max(0, -x0), max(0, -y0)
        sx1, sy1 = min(w, TILE_SIZE - x0), min(h, TILE_SIZE - y0)
        if sx0 >= sx1 or sy0 >= sy1:
            continue
        img.alpha_composite(sprite, dest=(x0 + sx0, y0 + sy0),
                            source=(sx0, sy0, sx1, sy1))
    return img
