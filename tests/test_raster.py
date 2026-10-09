"""Tests for raster label placement (run: python tests\\test_raster.py)."""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from PIL import ImageFont

from src.raster import (
    DOT_RADIUS, FONT_PATH, FONT_SIZE, NAME_ZOOM, _place_labels, label_texts,
)

FONT = ImageFont.truetype(FONT_PATH, FONT_SIZE)


def _stop(ref, x, y, name="Edinburgh at Ginger northbound"):
    return {"xy": (x, y), "ref": str(ref), "name": name, "fixed_route": True}


def _overlap(a, b):
    return a[0] < b[2] and a[2] > b[0] and a[1] < b[3] and a[3] > b[1]


def test_names_only_at_the_top_zoom():
    s = _stop(109, 0, 0)
    assert label_texts(s, NAME_ZOOM - 1) == ["109"]
    assert label_texts(s, NAME_ZOOM) == ["109 Edinburgh at Ginger northbound", "109"]
    assert label_texts(_stop(109, 0, 0, name=""), NAME_ZOOM) == ["109"]
    print("test_names_only_at_the_top_zoom: OK")


def test_a_lone_stop_gets_its_name_to_the_right():
    labels = _place_labels([_stop(109, 1000, 1000)], FONT, NAME_ZOOM,
                           DOT_RADIUS[NAME_ZOOM])
    assert len(labels) == 1
    x, _y, text, anchor, _box = labels[0]
    assert text.startswith("109 ") and anchor == "lm" and x > 1000
    print("test_a_lone_stop_gets_its_name_to_the_right: OK")


def test_close_stops_fall_back_rather_than_overprint():
    # Two stops side by side, 40 px apart: the first takes the right side with
    # its name; the second cannot print its name either side without crossing
    # the first's label or dot, and settles for something that does not
    # overlap.
    stops = [_stop(109, 1000, 1000), _stop(110, 1040, 1000)]
    labels = _place_labels(stops, FONT, NAME_ZOOM, DOT_RADIUS[NAME_ZOOM])
    assert len(labels) == 2
    assert not _overlap(labels[0][4], labels[1][4])
    print("test_close_stops_fall_back_rather_than_overprint: OK")


def test_at_the_top_zoom_every_stop_keeps_its_number():
    # The station: platforms a few pixels apart. Below NAME_ZOOM some numbers
    # are dropped; at NAME_ZOOM none are.
    stops = [_stop(5833 + i, 1000 + 3 * i, 1000) for i in range(8)]
    top = _place_labels(stops, FONT, NAME_ZOOM, DOT_RADIUS[NAME_ZOOM])
    below = _place_labels(stops, FONT, NAME_ZOOM - 1, DOT_RADIUS[NAME_ZOOM - 1])
    assert len(top) == 8
    assert len(below) < 8
    print("test_at_the_top_zoom_every_stop_keeps_its_number: OK")


if __name__ == "__main__":
    test_names_only_at_the_top_zoom()
    test_a_lone_stop_gets_its_name_to_the_right()
    test_close_stops_fall_back_rather_than_overprint()
    test_at_the_top_zoom_every_stop_keeps_its_number()
