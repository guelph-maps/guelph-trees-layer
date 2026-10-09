"""Compare the City's trees against the trees already in OSM.

Pulls every ``natural=tree`` node in Guelph from OpenStreetMap (Overpass), then
matches each City tree to them by distance: nearest-first and one-to-one,
within MATCH_M. There is nothing else to match on -- the City's asset number is
not on the tree, and OSM's Guelph trees are almost all bare ``natural=tree``
traced from imagery.

Thirty-four thousand unmatched trees are not a work list, so the result leads
with CELL_M squares (data/cells.geojson), each with a status:

  * "empty_cell" -- CELL_MIN_CITY or more City trees and no OSM tree at all;
  * "thin_cell"  -- some OSM trees, but fewer than THIN_SHARE of the City's
                    have one within MATCH_M;
  * "covered"    -- the rest with CELL_MIN_CITY or more City trees;
  * "sparse"     -- fewer than CELL_MIN_CITY City trees.

and per-tree rows behind them (data/trees.json, keyed by cell), each City tree
either "missing" (no OSM tree within MATCH_M) or "matched" with the tags it
``need``s: "species" when the OSM tree has no genus, species or taxon and the
City knows the genus; "circumference" when it has no girth and the City has a
DBH.

There is no "osm_only". OSM's 40,000-odd trees with no City tree beside them
are woodlots, back yards and campus trees the City does not inventory, not
mistakes.

Distances are equirectangular metres -- exact enough at city scale, and no GIS
dependency. A flaky Overpass falls back to the cached response, but only after
every mirror has been tried several times -- see _load_osm.
"""

import json
import math
import os
import time
from collections import Counter, defaultdict
from datetime import date, datetime, timedelta, timezone
from urllib.parse import urlsplit

import requests
from addressvault import net

from src import config, history

CELL_STATUSES = ("empty_cell", "thin_cell", "covered", "sparse")
# The counts the page, the summary and the history carry. Cells for the first
# two, City trees for the rest.
STATUSES = ("empty_cell", "thin_cell", "missing", "nospecies", "nogirth")

# OSM keys that say what a tree is. Any one of them means a mapper has already
# recorded it, whatever the City calls it.
SPECIES_TAGS = ("genus", "species", "taxon")
TREE_FIELDS = ("lon", "lat", "status", "need", "asset", "genus", "species",
               "common", "dbh", "circ", "address", "osm", "dist")

_R = 6371008.8
_LAT0 = math.radians(config.GUELPH_CENTER[0])


def compare():
    """Build data/cells.geojson + trees.json + summary. Returns the summary."""
    data, osm_date, from_cache = _load_osm()
    osm = _osm_trees(data)
    print(f"OSM trees:           {len(osm):,}")
    city = list(_city_trees())
    print(f"City trees:          {len(city):,}")

    cells, trees, counts = analyse(city, osm)
    _write(cells, trees)

    summary = {"osm_trees": len(osm), "city": len(city),
               "osm_date": osm_date, "osm_stale": from_cache, **counts}
    with open(config.GAPS_COUNT_PATH, "w", encoding="utf-8") as f:
        json.dump(summary, f, indent=2)
    print(f"Matched within {config.MATCH_M} m: {counts['matched']:,} "
          f"({counts['matched'] / max(len(city), 1):.0%})")
    print("Gaps: " + ", ".join(f"{counts[s]:,} {s}" for s in STATUSES))
    if from_cache:
        print(f"NOTE: compared against cached OSM data from {osm_date}, "
              f"not a live Overpass fetch.")
    history.record(summary)
    return summary


# --- OSM source -------------------------------------------------------------

def _overpass_query(at=None):
    """The Overpass QL for trees; ``at`` (ISO instant) asks for OSM as it stood
    then -- an attic query, used by the history backfill.

    Nodes only: a tree mapped as an area is a wood, not a tree. Plain ``out``
    (tags, no metadata) keeps the 50,000-node reply to a few MB.
    """
    s, w, n, e = config.GUELPH_BBOX
    settings = "[out:json][timeout:240]"
    if at:
        settings += f'[date:"{at}"]'
    return settings + f';\nnode["natural"="tree"]({s},{w},{n},{e});\nout;'


class OsmUnavailable(Exception):
    """Every mirror refused, on a machine that has a working link."""


def _load_osm():
    """Return (data, fetch_date, from_cache) for OSM trees.

    Every mirror is tried, and the whole list is retried: the thing being worked
    around is a loaded instance shedding one request, which clears in seconds.
    In the parks sibling one POST was once the entire effort, and two 504s a
    week apart left its gap page comparing against a fortnight-old OSM without
    failing anything.

    Falling back to the cache still beats no gap page, but it is reported rather
    than shrugged off -- ``from_cache`` reaches the published page, and an online
    machine that could not reach any mirror raises so the run can exit non-zero
    and be retried by the scheduler.
    """
    query = _overpass_query()
    print("Querying Overpass for OSM trees ...")
    for attempt in range(config.OVERPASS_ROUNDS):
        if attempt:
            print(f"  no mirror answered; retrying the list in "
                  f"{config.OVERPASS_ROUND_WAIT}s "
                  f"({attempt + 1} of {config.OVERPASS_ROUNDS})")
            time.sleep(config.OVERPASS_ROUND_WAIT)
        for url in config.OVERPASS_URLS:
            data = _try_overpass(url, query)
            if data is None:
                continue
            with open(config.OSM_CACHE_PATH, "w", encoding="utf-8") as f:
                json.dump(data, f)
            osm_base = _osm_base(data)
            _save_fetch(date.today().isoformat(), url, osm_base, data)
            return data, osm_base, False

    # Nothing answered. Ask why before deciding what it means: no link is not a
    # failed build -- the run may as well not have happened -- while a healthy
    # link and no mirror is a real outage worth retrying.
    cached = _load_cache()
    try:
        net.wait_for_link(wait=False)
    except net.LinkUnavailable as e:
        print(f"Warning: no usable link ({e}); Overpass was never reachable.")
        if cached:
            return (*cached, True)
        raise

    if cached:
        print(f"Warning: no Overpass mirror answered in "
              f"{config.OVERPASS_ROUNDS} rounds; falling back to OSM data "
              f"from {cached[1]}.")
        return (*cached, True)
    raise OsmUnavailable(
        f"no Overpass mirror answered in {config.OVERPASS_ROUNDS} rounds "
        f"and there is no cached OSM data"
    )


def _try_overpass(url, query):
    """One attempt at one mirror; None if it failed or answered implausibly."""
    host = urlsplit(url).netloc
    started = time.time()
    try:
        resp = requests.post(
            url,
            data={"data": query},
            headers={"User-Agent": config.USER_AGENT},
            timeout=config.OVERPASS_TIMEOUT,
        )
        resp.raise_for_status()
        data = resp.json()
    except (requests.RequestException, ValueError) as e:
        print(f"  {host}: {type(e).__name__}: {str(e).splitlines()[0][:120]}")
        return None

    count = len(data.get("elements", []))
    if count < config.OSM_MIN_ELEMENTS:
        # HTTP 200 is not proof of an answer. A regional instance replies to a
        # Guelph bbox with a valid, empty element list, and a timed-out query
        # with a partial one; either would report half the city as missing.
        print(f"  {host}: {count:,} elements, under the "
              f"{config.OSM_MIN_ELEMENTS:,} floor -- ignoring this reply")
        return None
    base = _osm_base_time(data)
    if base and datetime.now(timezone.utc) - base > timedelta(days=config.OSM_MAX_AGE_DAYS):
        # Nor is a full reply proof of a current one. A lagging mirror answers
        # the query in full from data months old, and the page would present
        # that as this week's OSM.
        print(f"  {host}: OSM data from {_osm_base(data)}, older than "
              f"{config.OSM_MAX_AGE_DAYS} days -- ignoring this reply")
        return None
    print(f"  {host}: {count:,} elements in {time.time() - started:.0f}s, "
          f"OSM base {_osm_base(data)}")
    return data


def _load_cache():
    """(data, fetch_date) for the cached Overpass reply, or None."""
    if not os.path.isfile(config.OSM_CACHE_PATH):
        return None
    with open(config.OSM_CACHE_PATH, encoding="utf-8") as f:
        data = json.load(f)
    return data, _cache_date()


def _osm_base(data):
    """How current the OSM half of the diff actually is.

    Overpass states this about its own reply, as ``osm3s.timestamp_osm_base``:
    the moment of the last minutely diff the answering mirror had applied. That
    is the honest date for the page, and it is not the same as "today" -- a
    mirror can answer instantly with data from last week, which is precisely the
    failure this project has been bitten by. Trimmed to the minute for display;
    the raw value goes in the sidecar. Falls back to today's date if a mirror
    omits the block.
    """
    stamp = ((data.get("osm3s") or {}).get("timestamp_osm_base") or "").strip()
    if len(stamp) >= 16:
        return f"{stamp[:10]} {stamp[11:16]}Z"
    return date.today().isoformat()


def _osm_base_time(data):
    """The same timestamp as an aware datetime, or None if absent or garbled."""
    stamp = ((data.get("osm3s") or {}).get("timestamp_osm_base") or "").strip()
    try:
        return datetime.fromisoformat(stamp.replace("Z", "+00:00"))
    except ValueError:
        return None


def _cache_date():
    """How current the cached reply is: the sidecar, else the file's mtime."""
    if os.path.isfile(config.OSM_FETCH_PATH):
        with open(config.OSM_FETCH_PATH, encoding="utf-8") as f:
            sidecar = json.load(f)
        for key in ("osm_base", "fetched"):
            if sidecar.get(key):
                return sidecar[key]
    stamp = os.path.getmtime(config.OSM_CACHE_PATH)
    return date.fromtimestamp(stamp).isoformat()


def _save_fetch(fetched, url, osm_base, data):
    with open(config.OSM_FETCH_PATH, "w", encoding="utf-8") as f:
        json.dump({
            "fetched": fetched,
            "mirror": urlsplit(url).netloc,
            "osm_base": osm_base,
            "timestamp_osm_base":
                (data.get("osm3s") or {}).get("timestamp_osm_base"),
        }, f, indent=2)


def _osm_trees(data):
    """Flatten the reply into trees: position, id, and what it already says."""
    trees = []
    for el in data.get("elements", []):
        if "lat" not in el or "lon" not in el:
            continue
        tags = el.get("tags") or {}
        trees.append({
            "lat": el["lat"],
            "lon": el["lon"],
            "oid": el.get("id"),
            "has_species": any(tags.get(k) for k in SPECIES_TAGS),
            "has_girth": bool(tags.get("circumference")),
        })
    return trees


# --- City source ------------------------------------------------------------

def _city_trees():
    with open(config.SLIM_PATH, encoding="utf-8") as f:
        for line in f:
            feat = json.loads(line)
            p = feat.get("properties") or {}
            lon, lat = feat["geometry"]["coordinates"][:2]
            yield {"lat": lat, "lon": lon, **p}


# --- Matching ---------------------------------------------------------------

def analyse(city, osm):
    """Match, then roll up into cells. Returns (cells, trees_by_cell, counts).

    ``cells`` is {cell id: properties}, ``trees_by_cell`` {cell id: [row]} in
    TREE_FIELDS order, ``counts`` the summary's numbers.
    """
    matches = match(city, osm)

    cells = {}
    trees = defaultdict(list)
    counts = Counter()
    for o in osm:
        key = cell_of(o)
        cells.setdefault(key, _new_cell(key))["osm"] += 1
    streets = defaultdict(Counter)
    for i, c in enumerate(city):
        key = cell_of(c)
        cell = cells.setdefault(key, _new_cell(key))
        cell["city"] += 1
        streets[key][_street(c.get("address"))] += 1
        m = matches.get(i)
        if m is None:
            cell["missing"] += 1
            trees[key].append(_tree_row(c, "missing", [], None, None))
            continue
        cell["matched"] += 1
        o, d = osm[m[0]], m[1]
        need = []
        if not o["has_species"] and c.get("genus"):
            need.append("species")
            cell["nospecies"] += 1
        if not o["has_girth"] and c.get("circumference"):
            need.append("circumference")
            cell["nogirth"] += 1
        if need:
            trees[key].append(_tree_row(c, "matched", need, o["oid"], d))

    # A cell with OSM trees and no City tree is someone's woodlot: not a gap,
    # and not on the page.
    cells = {k: v for k, v in cells.items() if v["city"]}
    for key, cell in cells.items():
        cell["status"] = cell_status(cell["city"], cell["osm"], cell["matched"])
        common = [s for s, _ in streets[key].most_common() if s]
        cell["label"] = common[0] if common else ""
        counts[cell["status"]] += 1
        for k in ("missing", "nospecies", "nogirth", "matched"):
            counts[k] += cell[k]
    for key in trees:
        # Missing first, then the attribute work; within each, by address --
        # the order a mapper walks a street in.
        trees[key].sort(key=lambda r: (r[2] != "missing", r[10] or ""))
    counts = {k: counts[k] for k in (*CELL_STATUSES, "missing", "nospecies",
                                     "nogirth", "matched")}
    return cells, dict(trees), counts


def cell_status(city, osm, matched):
    if city < config.CELL_MIN_CITY:
        return "sparse"
    if osm == 0:
        return "empty_cell"
    if matched < config.THIN_SHARE * city:
        return "thin_cell"
    return "covered"


def match(city, osm, radius=None):
    """{City index: (OSM index, metres)}, nearest-first and one-to-one.

    Every City-OSM pair within ``radius`` is collected from a grid hash of the
    OSM trees, the pairs are sorted by distance, and each is taken unless one
    side is already spoken for. "Nearest OSM tree for each City tree" would not
    do: street trees stand 8-10 m apart, often closer, and two City trees
    would claim one OSM node.
    """
    radius = config.MATCH_M if radius is None else radius
    grid = defaultdict(list)
    for j, o in enumerate(osm):
        x, y = _xy(o)
        grid[(int(x // radius), int(y // radius))].append((j, x, y))

    pairs = []
    for i, c in enumerate(city):
        x, y = _xy(c)
        gx, gy = int(x // radius), int(y // radius)
        for dx in (-1, 0, 1):
            for dy in (-1, 0, 1):
                for j, ox, oy in grid.get((gx + dx, gy + dy), ()):
                    d = math.hypot(ox - x, oy - y)
                    if d <= radius:
                        pairs.append((d, i, j))
    pairs.sort()
    used = set()
    out = {}
    for d, i, j in pairs:
        if i in out or j in used:
            continue
        out[i] = (j, d)
        used.add(j)
    return out


def cell_of(p):
    """The CELL_M square a point falls in, as "c<col>_<row>" counted from the
    bounding box's south-west corner."""
    x, y = _xy(p)
    return f"c{int(x // config.CELL_M)}_{int(y // config.CELL_M)}"


def cell_bounds(key):
    """(west, south, east, north) of a cell in degrees."""
    col, row = (int(v) for v in key[1:].split("_"))
    s, w = config.GUELPH_BBOX[0], config.GUELPH_BBOX[1]
    m = config.CELL_M
    dlat = math.degrees(m / _R)
    dlon = math.degrees(m / (_R * math.cos(_LAT0)))
    return (w + col * dlon, s + row * dlat, w + (col + 1) * dlon, s + (row + 1) * dlat)


def _xy(p):
    """Metres east and north of the bounding box's south-west corner.

    One fixed projection for the whole city (equirectangular at Guelph's
    latitude), so the match grid and the cells agree with each other and from
    run to run. Off by well under a metre across 15 km.
    """
    s, w = config.GUELPH_BBOX[0], config.GUELPH_BBOX[1]
    return (math.radians(p["lon"] - w) * math.cos(_LAT0) * _R,
            math.radians(p["lat"] - s) * _R)


def _new_cell(key):
    return {"cell": key, "label": "", "status": "", "city": 0, "osm": 0,
            "matched": 0, "missing": 0, "nospecies": 0, "nogirth": 0}


def _street(address):
    """"133 Clairfields Dr W" -> "Clairfields Dr W"."""
    words = (address or "").split()
    while words and any(ch.isdigit() for ch in words[0]):
        words = words[1:]
    return " ".join(words)


def _tree_row(c, status, need, oid, dist):
    return [round(c["lon"], 6), round(c["lat"], 6), status, need,
            c.get("city_asset_id"), c.get("genus"), c.get("species"),
            c.get("common_name"), c.get("dbh_cm"), c.get("circumference"),
            c.get("address"), oid, None if dist is None else round(dist)]


def _write(cells, trees):
    features = []
    for key in sorted(cells):
        w, s, e, n = cell_bounds(key)
        ring = [[round(x, 6), round(y, 6)]
                for x, y in ((w, s), (e, s), (e, n), (w, n), (w, s))]
        features.append({"type": "Feature", "properties": cells[key],
                         "geometry": {"type": "Polygon", "coordinates": [ring]}})
    with open(config.CELLS_PATH, "w", encoding="utf-8") as f:
        json.dump({"type": "FeatureCollection", "features": features}, f)
    with open(config.TREES_PATH, "w", encoding="utf-8") as f:
        json.dump({"fields": list(TREE_FIELDS), "cells": trees}, f,
                  separators=(",", ":"))
