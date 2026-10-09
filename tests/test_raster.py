"""Tests for raster crown colour, size and rendering (run: python tests\\test_raster.py)."""

import json
import os
import shutil
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from PIL import Image

from src import config, raster
from src.raster import (
    CROWN_MAX_M, CROWN_MIN_M, DEFAULT_CROWN_M, GENUS_COLORS, MIN_CROWN_PX,
    OTHER_COLOR, OTHER_LABEL, SCALE_MIN_ZOOM, UNKNOWN_COLOR, UNKNOWN_LABEL,
    crown_radius_m, crown_radius_px, genus_color, legend_html,
)


def test_genus_colours():
    assert genus_color("Acer") == GENUS_COLORS[0][2]
    assert genus_color("Ginkgo") == OTHER_COLOR       # common, but not top ten
    assert genus_color(None) == UNKNOWN_COLOR
    assert genus_color("") == UNKNOWN_COLOR
    # Every palette colour is its own: two genera sharing one would make the
    # legend lie.
    colours = [c for _g, _label, c in GENUS_COLORS] + [OTHER_COLOR, UNKNOWN_COLOR]
    assert len(set(colours)) == len(colours)
    print("test_genus_colours: OK")


def test_crown_grows_with_dbh_and_is_clamped():
    radii = [crown_radius_m(d) for d in (1, 5, 20, 40, 80, 150, 300)]
    assert radii == sorted(radii)
    assert radii[0] == CROWN_MIN_M and radii[-1] == CROWN_MAX_M
    assert 1.4 <= crown_radius_m(5) <= 1.7
    assert 6.5 <= crown_radius_m(80) <= 7.5
    print("test_crown_grows_with_dbh_and_is_clamped: OK")


def test_implausible_dbh_is_unknown():
    # 879 cm is in the extract; over config.MAX_DBH_CM it is a typo, not the
    # biggest tree in the city.
    for bogus in (None, 0, -3, config.MAX_DBH_CM + 1, 879, "40", True):
        assert crown_radius_m(bogus) == DEFAULT_CROWN_M, bogus
    assert crown_radius_m(config.MAX_DBH_CM) == CROWN_MAX_M
    print("test_implausible_dbh_is_unknown: OK")


def test_dots_below_the_scale_zoom_crowns_from_it():
    # Below SCALE_MIN_ZOOM size says nothing; from it, it says DBH.
    z = SCALE_MIN_ZOOM - 1
    assert crown_radius_px(5, z) == crown_radius_px(80, z)
    z = max(config.RASTER_ZOOMS)
    assert crown_radius_px(80, z) > crown_radius_px(5, z) >= MIN_CROWN_PX
    # An 80 cm tree at z18 (0.43 m/px) is about a 7 m radius.
    assert 14 <= crown_radius_px(80, 18) <= 18
    print("test_dots_below_the_scale_zoom_crowns_from_it: OK")


def test_legend_lists_every_colour():
    legend = legend_html()
    assert legend.startswith('<ul class="legend">')
    for genus, label, (r, g, b, _a) in GENUS_COLORS:
        assert genus in legend and label in legend
        assert f"rgb({r},{g},{b})" in legend
    assert OTHER_LABEL in legend and UNKNOWN_LABEL in legend
    print("test_legend_lists_every_colour: OK")


def _feature(lon, lat, **props):
    return {"type": "Feature",
            "geometry": {"type": "Point", "coordinates": [lon, lat]},
            "properties": {"city_asset_id": "FT1", **props}}


def test_a_tiny_render_writes_non_empty_tiles():
    tmp = tempfile.mkdtemp()
    saved = config.RASTER_TILE_DIR, config.RASTER_ZOOMS
    try:
        slim = os.path.join(tmp, "trees-slim.geojsonl")
        with open(slim, "w", encoding="utf-8") as f:
            for feat in (
                _feature(-80.2482, 43.5448, genus="Acer", dbh_cm=60),
                _feature(-80.2481, 43.5448, genus="Ginkgo", dbh_cm=8),
                _feature(-80.2483, 43.5449, dbh_cm=879),  # no genus, bogus DBH
            ):
                f.write(json.dumps(feat) + "\n")
        config.RASTER_TILE_DIR = os.path.join(tmp, "raster")
        config.RASTER_ZOOMS = [13, 18]

        counts = raster.build_raster(slim)

        assert set(counts) == {13, 18} and all(counts.values())
        tiles = [os.path.join(d, n) for d, _s, files in os.walk(config.RASTER_TILE_DIR)
                 for n in files]
        assert len(tiles) == sum(counts.values())
        for path in tiles:
            with Image.open(path) as img:
                assert img.mode == "RGBA" and img.getbbox() is not None
    finally:
        config.RASTER_TILE_DIR, config.RASTER_ZOOMS = saved
        shutil.rmtree(tmp, ignore_errors=True)
    print("test_a_tiny_render_writes_non_empty_tiles: OK")


if __name__ == "__main__":
    test_genus_colours()
    test_crown_grows_with_dbh_and_is_clamped()
    test_implausible_dbh_is_unknown()
    test_dots_below_the_scale_zoom_crowns_from_it()
    test_legend_lists_every_colour()
    test_a_tiny_render_writes_non_empty_tiles()
