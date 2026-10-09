"""Tests for the slim step (run: python tests\\test_slim.py).

The parks sibling's slim filters on status and keeps a placeholder name out of
the tiles. Here there is nothing to filter; what matters is the stop ID, which
the gap page matches on: it has to come out as the integer OSM writes in
``ref``, and it has to be unique.
"""

import json
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src import config
import src.slim as slim_mod
from src.slim import slim, stop_ref


def test_stop_ref():
    cases = [
        (109, 109),
        (109.0, 109),
        ("5833", 5833),
        (109.5, None),
        (0, None),
        ("", None),
        (None, None),
        ("abc", None),
    ]
    for raw, expected in cases:
        got = stop_ref(raw)
        assert got == expected, f"{raw!r}: expected {expected}, got {got}"
    print(f"test_stop_ref: {len(cases)} cases OK")


def _feature(ref, name="Edinburgh at Ginger northbound", conv="Yes", oid=1):
    return {
        "type": "Feature",
        "geometry": {"type": "Point",
                     "coordinates": [-80.21356963499628, 43.51108452151642]},
        "properties": {
            config.REF_KEY: ref,
            config.NAME_KEY: name,
            config.FIXED_ROUTE_KEY: conv,
            config.ON_DEMAND_KEY: "Sunday and Statutory Holiday",
            config.OBJECT_ID_KEY: oid,
        },
    }


def _slim(features):
    """Run slim over ``features`` in a temp dir; return the kept rows.

    The fixture is far below slim's own MIN_EXPECTED, so that bound is relaxed
    for the duration -- it guards a real extract, not a five-row fixture.
    """
    tmp = tempfile.mkdtemp()
    src_path = os.path.join(tmp, "bus-stops-2026-10-08.geojson")
    with open(src_path, "w", encoding="utf-8") as f:
        json.dump({"type": "FeatureCollection", "features": features}, f)

    saved = (config.DATA_DIR, config.SLIM_PATH, config.COUNT_PATH,
             slim_mod.MIN_EXPECTED)
    config.DATA_DIR = tmp
    config.SLIM_PATH = os.path.join(tmp, "bus-stops-slim.geojsonl")
    config.COUNT_PATH = os.path.join(tmp, "bus-stops.count")
    slim_mod.MIN_EXPECTED = 1
    try:
        slim(src_path)
        with open(config.SLIM_PATH, encoding="utf-8") as f:
            kept = [json.loads(line) for line in f]
        with open(config.COUNT_PATH, encoding="utf-8") as f:
            count = int(f.read())
    finally:
        (config.DATA_DIR, config.SLIM_PATH, config.COUNT_PATH,
         slim_mod.MIN_EXPECTED) = saved
    return kept, count


def test_slim_keeps_points_with_a_stop_id():
    features = [_feature(109.0, oid=1), _feature(110, conv="No", oid=2)]
    features.append(_feature(None, oid=3))                       # no stop ID
    no_geom = _feature(111, oid=4)
    no_geom["geometry"] = None
    features.append(no_geom)

    kept, count = _slim(features)
    assert count == len(kept) == 2
    first = kept[0]["properties"]
    assert first == {"ref": 109, "name": "Edinburgh at Ginger northbound",
                     "fixed_route": "yes",
                     "on_demand": "Sunday and Statutory Holiday",
                     "objectid": 1}, first
    assert kept[1]["properties"]["fixed_route"] == "no"
    # Rounded to ~0.1 m; the service hands back 14 decimals.
    assert kept[0]["geometry"]["coordinates"] == [-80.21357, 43.511085]
    print("test_slim_keeps_points_with_a_stop_id: OK")


def test_slim_refuses_a_repeated_stop_id():
    try:
        _slim([_feature(109, oid=1), _feature(109.0, oid=2)])
    except RuntimeError as e:
        assert "109" in str(e)
    else:
        raise AssertionError("a repeated stop ID reached the slim file")
    print("test_slim_refuses_a_repeated_stop_id: OK")


if __name__ == "__main__":
    test_stop_ref()
    test_slim_keeps_points_with_a_stop_id()
    test_slim_refuses_a_repeated_stop_id()
