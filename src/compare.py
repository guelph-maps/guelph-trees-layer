"""Compare the City bus stops against the bus stops already in OSM.

Pulls bus stops from OpenStreetMap (Overpass) within Guelph, then matches each
City stop to them -- by stop ID first, since OSM mappers already carry it as
``ref``, and by distance only where OSM has a stop with no ref. The result is
written to data/gaps.geojson, one row per City stop that needs work, plus one
per OSM stop the City no longer lists:

  * "missing"   -- no OSM stop carries this ref and none sits unreferenced
                   beside it (add the stop);
  * "noref"     -- an OSM stop with no ref is this one (add ref=<StopId>);
  * "far"       -- the OSM stop with this ref is more than FAR_M away (one of
                   the two is in the wrong place);
  * "duplicate" -- two OSM stops more than FAR_M apart carry this ref;
  * "unnamed"   -- matched, but the OSM stop has no name;
  * "mismatch"  -- matched, but its OSM name differs;
  * "ondemand"  -- missing, but the City serves it by on-demand transit only:
                   a pick-up point, which may or may not belong in OSM as a
                   stop, so it is not offered as a plain "missing";
  * "osm_only"  -- an OSM Guelph Transit stop whose ref the City does not list
                   (retired, renumbered, or a typo).

Distances are equirectangular metres -- exact enough at city scale, and no GIS
dependency. A flaky Overpass falls back to the cached response, but only after
every mirror has been tried several times -- see _load_osm.
"""

import json
import math
import os
import re
import time
from datetime import date, datetime, timedelta, timezone
from urllib.parse import urlsplit

import requests
from addressvault import net

from src import config, history

STATUSES = ("missing", "noref", "far", "duplicate", "unnamed", "mismatch",
            "ondemand", "osm_only")


def compare():
    """Build data/gaps.geojson + summary from City vs OSM. Returns the summary."""
    data, osm_date, from_cache = _load_osm()
    osm = _osm_stops(data)
    print(f"OSM bus stops:       {len(osm):,}")
    city = list(_city_stops())
    print(f"City stops:          {len(city):,}")

    gaps, counts = _match(city, osm)
    _write_gaps(gaps)

    summary = {"osm_stops": len(osm), "city": len(city),
               "osm_date": osm_date, "osm_stale": from_cache, **counts}
    with open(config.GAPS_COUNT_PATH, "w", encoding="utf-8") as f:
        json.dump(summary, f, indent=2)
    print("Gaps: " + ", ".join(f"{counts[s]:,} {s}" for s in STATUSES))
    if from_cache:
        print(f"NOTE: compared against cached OSM data from {osm_date}, "
              f"not a live Overpass fetch.")
    history.record(summary)
    return summary


# --- OSM source -------------------------------------------------------------

def _overpass_query(at=None):
    """The Overpass QL for bus stops; ``at`` (ISO instant) asks for OSM as it
    stood then -- an attic query, used by the history backfill.

    Both tagging schemes are asked for: the legacy highway=bus_stop node, and
    a PTv2 platform tagged bus=yes without it (sometimes a way, which is why
    "out center" -- it gives every element a single lat/lon). Stop positions
    on the carriageway are not stops a rider waits at and are left out.
    """
    s, w, n, e = config.GUELPH_BBOX
    bbox = f"{s},{w},{n},{e}"
    settings = "[out:json][timeout:180]"
    if at:
        settings += f'[date:"{at}"]'
    return (
        settings + ";\n(\n"
        f'  nwr["highway"="bus_stop"]({bbox});\n'
        f'  nwr["public_transport"="platform"]["bus"="yes"]({bbox});\n'
        ");\nout center tags;"
    )


class OsmUnavailable(Exception):
    """Every mirror refused, on a machine that has a working link."""


def _load_osm():
    """Return (data, fetch_date, from_cache) for OSM bus stops.

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
    print("Querying Overpass for OSM bus stops ...")
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
        # Guelph bbox with a valid, empty element list, and taking that at face
        # value would report every City stop as missing from OSM.
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



def _osm_stops(data):
    """Flatten the reply into stops: position, ref(s), name, whose."""
    stops = []
    for el in data.get("elements", []):
        tags = el.get("tags") or {}
        if "lat" in el and "lon" in el:
            lat, lon = el["lat"], el["lon"]
        elif el.get("center"):
            lat, lon = el["center"]["lat"], el["center"]["lon"]
        else:
            continue
        stops.append({
            "lat": lat,
            "lon": lon,
            "refs": _refs(tags.get("ref")),
            "raw_ref": tags.get("ref"),
            "name": tags.get("name"),
            "otype": el.get("type"),
            "oid": el.get("id"),
            "agency": _agency(tags),
        })
    return stops


def _refs(value):
    """The integer stop IDs a ref tag carries ("109", "109;5810"), or ()."""
    out = []
    for part in (value or "").split(";"):
        part = part.strip()
        if part.isdigit():
            out.append(int(part))
    return tuple(out)


def _agency(tags):
    """"ours", "other" (another agency's stop), or "unknown" (untagged)."""
    named = " ".join(tags.get(k, "") for k in ("network", "operator")).lower()
    if not named.strip():
        return "unknown"
    return "ours" if config.NETWORK.lower() in named else "other"


# --- City source ------------------------------------------------------------

def _city_stops():
    with open(config.SLIM_PATH, encoding="utf-8") as f:
        for line in f:
            feat = json.loads(line)
            props = feat.get("properties") or {}
            lon, lat = feat["geometry"]["coordinates"][:2]
            yield {
                "ref": props["ref"],
                "name": props.get("name"),
                "fixed_route": props.get("fixed_route") != "no",
                "on_demand": props.get("on_demand"),
                "lat": lat,
                "lon": lon,
            }


# --- Matching ---------------------------------------------------------------

def _match(city, osm):
    """One gap row per City stop that needs work, plus OSM-only stops."""
    gaps = []
    counts = {s: 0 for s in STATUSES}
    # Another agency's stop is never a candidate for anything: GO Transit's
    # platforms at Guelph Central sit metres from Guelph Transit's.
    ours = [o for o in osm if o["agency"] != "other"]
    by_ref = {}
    for o in ours:
        for r in o["refs"]:
            by_ref.setdefault(r, []).append(o)
    claimed = _claim_unreferenced(city, [o for o in ours if not o["refs"]])

    for c in city:
        cands = by_ref.get(c["ref"], [])
        extra = {}
        if cands:
            match = min(cands, key=lambda o: _dist(c, o))
            far_twin = [o for o in cands if _dist(o, match) > config.FAR_M]
            if far_twin:
                status = "duplicate"
                extra["other_url"] = _osm_url(far_twin[0])
            elif _dist(c, match) > config.FAR_M:
                status = "far"
            elif not match["name"]:
                status = "unnamed"
            elif _norm(match["name"]) != _norm(c["name"]):
                status = "mismatch"
            else:
                continue  # an OSM stop with this ref and name is here
        elif c["ref"] in claimed:
            match = claimed[c["ref"]]
            status = "noref"
        else:
            match = None
            status = "missing" if c["fixed_route"] else "ondemand"
        gaps.append(_gap_feature(c, status, match, extra))
        counts[status] += 1

    # The other direction: a stop OSM says is Guelph Transit's, whose number
    # the City no longer lists. Only explicitly-tagged stops, so a GO or
    # untagged stop's own numbering is never read as one of these.
    city_refs = {c["ref"] for c in city}
    for o in ours:
        if o["agency"] == "ours" and o["refs"] and \
                not any(r in city_refs for r in o["refs"]):
            gaps.append(_osm_only_feature(o))
            counts["osm_only"] += 1
    return gaps, counts


def _claim_unreferenced(city, unreferenced):
    """{City ref: OSM stop} for OSM stops with no ref that are evidently it.

    An unreferenced OSM stop is given to the City stop nearest it, and only if
    that is within NEAR_M. Nearest-first matters: a quarter of the City's stops
    have another stop -- usually the opposite direction -- within 30 m, and a
    City-side "anything within NEAR_M" would give one OSM node to both. When
    two OSM stops pick the same City stop, the nearer one keeps it.
    """
    claimed = {}
    best = {}
    for o in unreferenced:
        c = min(city, key=lambda c: _dist(c, o), default=None)
        if c is None:
            continue
        d = _dist(c, o)
        if d <= config.NEAR_M and d < best.get(c["ref"], math.inf):
            claimed[c["ref"]] = o
            best[c["ref"]] = d
    return claimed


def _osm_url(o):
    return f"https://www.openstreetmap.org/{o['otype']}/{o['oid']}"


def _gap_feature(c, status, match, extra):
    props = {
        "ref": c["ref"],
        "name": c["name"],
        "status": status,
    }
    if c["on_demand"]:
        props["on_demand"] = c["on_demand"]
    if match:
        props["osm_name"] = match["name"]
        props["osm_ref"] = match["raw_ref"]
        props["osm_url"] = _osm_url(match)
        props["osm_lat"] = round(match["lat"], 6)
        props["osm_lon"] = round(match["lon"], 6)
        props["distance_m"] = round(_dist(c, match))
    props.update(extra)
    return {
        "type": "Feature",
        "properties": props,
        "geometry": {"type": "Point",
                     "coordinates": [round(c["lon"], 6), round(c["lat"], 6)]},
    }


def _osm_only_feature(o):
    return {
        "type": "Feature",
        "properties": {
            "ref": o["raw_ref"],
            "name": None,
            "status": "osm_only",
            "osm_name": o["name"],
            "osm_ref": o["raw_ref"],
            "osm_url": _osm_url(o),
        },
        "geometry": {"type": "Point",
                     "coordinates": [round(o["lon"], 6), round(o["lat"], 6)]},
    }


def _write_gaps(gaps):
    with open(config.GAPS_GEOJSON_PATH, "w", encoding="utf-8") as f:
        json.dump({"type": "FeatureCollection", "features": gaps}, f)


# --- Geometry and names -----------------------------------------------------

def _dist(a, b):
    """Metres between two {lat, lon}; equirectangular, fine at city scale."""
    lat0 = math.radians((a["lat"] + b["lat"]) / 2)
    dx = math.radians(b["lon"] - a["lon"]) * math.cos(lat0)
    dy = math.radians(b["lat"] - a["lat"])
    return 6371008.8 * math.hypot(dx, dy)


def _norm(name):
    """Loose match key: case, punctuation, "&"/"and" and spacing only.

    Deliberately no more. Guelph Transit's names are formulaic ("Edinburgh at
    Ginger northbound") and OSM's, where present, were mostly copied from
    them; an OSM name that drops the direction or spells out "Street" is a
    real difference the mapper should look at, not noise to normalise away.
    Affects matching only; the displayed name keeps the raw data.
    """
    if not name:
        return ""
    s = name.lower().replace("&", " and ").replace("'", "").replace("’", "")
    return re.sub(r"[^a-z0-9]+", " ", s).strip()
