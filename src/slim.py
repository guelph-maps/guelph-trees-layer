"""Filter the Trees GeoJSON into a slim newline-delimited GeoJSON.

The source file is an ~18 MB GeoJSON FeatureCollection, parsed as a stream
with ijson. Every point with an asset ID is kept, one compact Feature per line,
in data/trees-slim.geojsonl -- the input to both tile builders and to the gap
tool.

Properties come out under two kinds of name, and the difference matters to a
mapper reading them in iD:

  * OSM keys, in OSM's spelling and units -- ``genus``, ``species`` and
    ``circumference`` (metres). These are the values a mapper might add to a
    tree they have verified, so they are written the way OSM writes them;
  * City-only fields -- ``city_asset_id``, ``common_name``, ``dbh_cm``,
    ``owner``, ``planted``, ``address``, ``objectid``. None of these is an OSM
    tag. ``city_asset_id`` in particular is the City's inventory number, not
    painted on the tree, so it is not a ``ref``.

The City's common names are often a cultivar's ("Green Mountain Maple" is a
sugar maple), which is why they ship as ``common_name`` and not ``species:en``.
"""

import json
import math
import os
from datetime import datetime, timezone

import ijson

from src import config

# Sanity bounds for the slimmed feature count (42,712 on 2026-10-09; the City
# plants ~650-1,100 a year and removes some).
MIN_EXPECTED = 35000
MAX_EXPECTED = 60000

# Genus values that name no genus.
NO_GENUS = {"UNKNOWN", "OTHER"}

# Fixes for names in the City file that cannot be right, applied before
# parsing. Each is a misspelling of a real tree, or -- in Conium's case -- a
# herb: poison hemlock cannot be a street tree, and the 31 rows' common name,
# "HEMLOCK", is the eastern hemlock's, a Tsuga. Which Tsuga is not stated, so
# those keep the genus alone.
NAME_FIXES = {
    "RHAMUS CATHARTICA": "RHAMNUS CATHARTICA",
    "GYMNOCLADUS DIOCUS": "GYMNOCLADUS DIOICUS",
    "CONIUM MACULATUM": "TSUGA SPECIES",
}


def slim(src_path):
    """Stream the GeoJSON into data/trees-slim.geojsonl.

    Returns the slim file path. Raises if the feature count is implausible, or
    if two points share an asset ID -- the gap page's drill-down keys on it.
    """
    print(f"Slimming {src_path} ...")
    os.makedirs(config.DATA_DIR, exist_ok=True)

    count = 0
    skipped = 0
    seen = set()
    with open(src_path, "rb") as src, \
            open(config.SLIM_PATH, "w", encoding="utf-8") as out:
        for feature in ijson.items(src, "features.item"):
            props_in = feature.get("properties") or {}
            geom = feature.get("geometry") or {}
            asset = str(props_in.get(config.ASSET_ID_KEY) or "").strip()
            if geom.get("type") != "Point" or not geom.get("coordinates") \
                    or not asset:
                skipped += 1
                continue
            if asset in seen:
                raise RuntimeError(f"Asset ID {asset} appears twice in {src_path}.")
            seen.add(asset)

            lon, lat = geom["coordinates"][:2]
            out.write(json.dumps({
                "type": "Feature",
                # ~0.1 m. The service hands back 14 decimal places.
                "geometry": {"type": "Point",
                             "coordinates": [round(float(lon), 6),
                                             round(float(lat), 6)]},
                "properties": tree_props(props_in),
            }) + "\n")
            count += 1

    with open(config.COUNT_PATH, "w", encoding="utf-8") as f:
        f.write(str(count))

    print(f"Done: {config.SLIM_PATH} ({count:,} features, {skipped:,} skipped)")
    if not MIN_EXPECTED <= count <= MAX_EXPECTED:
        raise RuntimeError(
            f"Slim feature count {count:,} is outside the expected range "
            f"{MIN_EXPECTED:,}-{MAX_EXPECTED:,} -- aborting."
        )
    return config.SLIM_PATH


def tree_props(p):
    """The slim properties for one City row (absent where the City has none)."""
    out = {"city_asset_id": str(p[config.ASSET_ID_KEY]).strip()}
    genus, species = taxon(p.get(config.GENUS_KEY), p.get(config.SPECIES_KEY))
    if genus:
        out["genus"] = genus
    if species:
        out["species"] = species
    common = (p.get(config.COMMON_KEY) or "").strip()
    if common and common.upper() not in NO_GENUS:
        out["common_name"] = _title(common)
    dbh = dbh_cm(p.get(config.DBH_KEY))
    if dbh:
        out["dbh_cm"] = dbh
        out["circumference"] = circumference(dbh)
    owner = (p.get(config.OWNER_KEY) or "").strip().lower()
    if owner:
        out["owner"] = owner
    year = planted_year(p.get(config.PLANTED_KEY))
    if year:
        out["planted"] = year
    address = (p.get(config.ADDRESS_KEY) or "").strip()
    if address:
        out["address"] = _title(address)
    # The ArcGIS row id. Shown so a reviewer can find the row again in the same
    # extract -- it can renumber when the layer is republished, so nothing keys
    # on it; the asset ID is the key.
    objectid = p.get(config.OBJECT_ID_KEY)
    if objectid is not None:
        out["objectid"] = int(objectid)
    return out


def taxon(genus_raw, species_raw):
    """(genus, species) in OSM's spelling, either None where not known.

    The City writes names in capitals: "QUERCUS RUBRA", "ACER X FREEMANII",
    "POPULUS NIGRA V. ITALICA", "ACER X 'NORWEGIAN SUNSET'", "MALUS SPECIES".
    OSM writes ``genus=Quercus`` and ``species=Quercus rubra``, a hybrid with a
    plain "x" (``Platanus x acerifolia`` is on ~21,000 OSM trees, the "×" form
    on none). A variety is dropped back to its species; a cultivar with no
    species epithet, or "SPECIES", leaves the genus alone.
    """
    name = " ".join((species_raw or "").upper().split())
    name = NAME_FIXES.get(name, name)
    words = name.split()
    genus = words[0] if words else " ".join((genus_raw or "").upper().split())
    if not genus or genus in NO_GENUS or not genus.isalpha():
        return None, None
    genus = genus.capitalize()

    rest = words[1:]
    hybrid = bool(rest) and rest[0] == "X"
    if hybrid:
        rest = rest[1:]
    if not rest or not rest[0].isalpha() or rest[0] == "SPECIES":
        return genus, None
    return genus, f"{genus} {'x ' if hybrid else ''}{rest[0].lower()}"


def dbh_cm(value):
    """Trunk diameter in whole cm, or None if absent, zero or implausible."""
    try:
        d = int(value)
    except (TypeError, ValueError):
        return None
    if d <= 0 or d > config.MAX_DBH_CM:
        return None
    return d


def circumference(dbh):
    """OSM's ``circumference``: the trunk's girth in metres, from DBH in cm."""
    return round(dbh * math.pi / 100, 2)


def planted_year(value):
    """The planting year as a string, from the service's epoch milliseconds."""
    if value is None or isinstance(value, (bool, str)):
        return None
    try:
        return str(datetime.fromtimestamp(float(value) / 1000, timezone.utc).year)
    except (OverflowError, OSError, ValueError):
        return None


def _title(s):
    """"133 CLAIRFIELDS DR W" -> "133 Clairfields Dr W"; "MOUNTAIN-ASH" too."""
    return " ".join(
        "-".join(part.capitalize() for part in word.split("-"))
        for word in s.split()
    )
