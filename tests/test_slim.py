"""Tests for the slim step (run: python tests\\test_slim.py).

What matters here is what a mapper reads off the vector layer: names in OSM's
spelling (``genus=Quercus``, ``species=Platanus x acerifolia``), girth in OSM's
unit (``circumference`` in metres), the City-only fields under names that are
plainly not OSM tags, and the asset ID unique -- the gap page keys on it.
"""

import json
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src import config
import src.slim as slim_mod
from src.slim import circumference, dbh_cm, planted_year, slim, taxon


def test_taxon():
    cases = [
        (("QUERCUS", "QUERCUS RUBRA"), ("Quercus", "Quercus rubra")),
        (("MALUS", "MALUS SPECIES"), ("Malus", None)),
        (("ACER", "ACER X FREEMANII"), ("Acer", "Acer x freemanii")),
        (("PLATANUS", "PLATANUS X ACERIFOLIA"), ("Platanus", "Platanus x acerifolia")),
        (("POPULUS", "POPULUS NIGRA V. ITALICA"), ("Populus", "Populus nigra")),
        (("ACER", "ACER X 'NORWEGIAN SUNSET'"), ("Acer", None)),
        (("UNKNOWN", "UNKNOWN"), (None, None)),
        (("OTHER", "OTHER SPECIES"), (None, None)),
        ((None, None), (None, None)),
        (("ACER", None), ("Acer", None)),
        # The City's misspellings, and a herb standing in for a hemlock.
        (("RHAMUS", "RHAMUS CATHARTICA"), ("Rhamnus", "Rhamnus cathartica")),
        (("GYMNOCLADUS", "GYMNOCLADUS DIOCUS"), ("Gymnocladus", "Gymnocladus dioicus")),
        (("CONIUM", "CONIUM MACULATUM"), ("Tsuga", None)),
    ]
    for raw, expected in cases:
        got = taxon(*raw)
        assert got == expected, f"{raw!r}: expected {expected}, got {got}"
    print(f"test_taxon: {len(cases)} cases OK")


def test_dbh_and_circumference():
    assert dbh_cm(30) == 30
    assert dbh_cm(0) is None and dbh_cm(None) is None
    assert dbh_cm(879) is None, "an 8.8 m trunk is a typo"
    # OSM's circumference is metres; DBH is a diameter in cm.
    assert circumference(30) == 0.94
    assert circumference(5) == 0.16
    print("test_dbh_and_circumference: OK")


def test_planted_year():
    assert planted_year(1411689600000) == "2014"
    assert planted_year(None) is None
    print("test_planted_year: OK")


def _feature(asset, genus="QUERCUS", species="QUERCUS RUBRA", dbh=5, oid=1):
    return {
        "type": "Feature",
        "geometry": {"type": "Point",
                     "coordinates": [-80.200350107709511, 43.495513268418819]},
        "properties": {
            config.OBJECT_ID_KEY: oid,
            config.ASSET_ID_KEY: asset,
            config.GENUS_KEY: genus,
            config.SPECIES_KEY: species,
            config.COMMON_KEY: "RED OAK",
            config.DBH_KEY: dbh,
            config.OWNER_KEY: "CITY",
            config.PLANTED_KEY: 1411689600000,
            "TREE_STATUS": "ACTIVE",
            config.ADDRESS_KEY: "133 CLAIRFIELDS DR W",
        },
    }


def _slim(features):
    """Run slim over ``features`` in a temp dir; return the kept rows.

    The fixture is far below slim's own MIN_EXPECTED, so that bound is relaxed
    for the duration -- it guards a real extract, not a five-row fixture.
    """
    tmp = tempfile.mkdtemp()
    src_path = os.path.join(tmp, "trees-2026-10-09.geojson")
    with open(src_path, "w", encoding="utf-8") as f:
        json.dump({"type": "FeatureCollection", "features": features}, f)

    saved = (config.DATA_DIR, config.SLIM_PATH, config.COUNT_PATH,
             slim_mod.MIN_EXPECTED)
    config.DATA_DIR = tmp
    config.SLIM_PATH = os.path.join(tmp, "trees-slim.geojsonl")
    config.COUNT_PATH = os.path.join(tmp, "trees.count")
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


def test_slim_keeps_points_with_an_asset_id():
    features = [_feature("FT10001", oid=1),
                _feature("FT10002", "UNKNOWN", "UNKNOWN", dbh=0, oid=2)]
    features.append(_feature(None, oid=3))                       # no asset ID
    no_geom = _feature("FT10004", oid=4)
    no_geom["geometry"] = None
    features.append(no_geom)

    kept, count = _slim(features)
    assert count == len(kept) == 2
    first = kept[0]["properties"]
    assert first == {"city_asset_id": "FT10001", "genus": "Quercus",
                     "species": "Quercus rubra", "common_name": "Red Oak",
                     "dbh_cm": 5, "circumference": 0.16, "owner": "city",
                     "planted": "2014", "address": "133 Clairfields Dr W",
                     "objectid": 1}, first
    second = kept[1]["properties"]
    assert not {"genus", "species", "dbh_cm", "circumference"} & set(second)
    # Rounded to ~0.1 m; the service hands back 14 decimals.
    assert kept[0]["geometry"]["coordinates"] == [-80.20035, 43.495513]
    print("test_slim_keeps_points_with_an_asset_id: OK")


def test_slim_refuses_a_repeated_asset_id():
    try:
        _slim([_feature("FT1", oid=1), _feature("FT1", oid=2)])
    except RuntimeError as e:
        assert "FT1" in str(e)
    else:
        raise AssertionError("a repeated asset ID reached the slim file")
    print("test_slim_refuses_a_repeated_asset_id: OK")


if __name__ == "__main__":
    test_taxon()
    test_dbh_and_circumference()
    test_planted_year()
    test_slim_keeps_points_with_an_asset_id()
    test_slim_refuses_a_repeated_asset_id()
