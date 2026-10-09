"""Filter the Bus Stops GeoJSON into a slim newline-delimited GeoJSON.

The source file is a ~230 KB GeoJSON FeatureCollection; it is parsed as a
stream with ijson for consistency with the sibling projects. Every point with a
stop ID is kept, one compact Feature per line, in data/bus-stops-slim.geojsonl
-- the input to both tile builders and to the gap tool.

There is no status filter to apply: the City removes a retired stop from the
extract rather than flagging it. The one categorical field, fixed-route vs
on-demand-only service, is carried through rather than filtered on -- an
on-demand pick-up point is real, and the tiles draw it, hollow. See
config.FIXED_ROUTE_KEY.
"""

import json
import os

import ijson

from src import config

# Sanity bounds for the slimmed feature count (648 stops on 2026-10-08).
MIN_EXPECTED = 500
MAX_EXPECTED = 900


def slim(src_path):
    """Stream the GeoJSON into data/bus-stops-slim.geojsonl.

    Keeps Point features that carry a stop ID, with `ref`, `name`,
    `fixed_route`, `on_demand` and `objectid` properties. Returns the slim file
    path. Raises if the feature count is implausible, or if two points share a
    stop ID -- the gap page matches on it, so it has to be a key.
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
            ref = stop_ref(props_in.get(config.REF_KEY))
            if geom.get("type") != "Point" or not geom.get("coordinates") \
                    or ref is None:
                skipped += 1
                continue
            if ref in seen:
                raise RuntimeError(f"Stop ID {ref} appears twice in {src_path}.")
            seen.add(ref)

            props_out = {"ref": ref}
            name = props_in.get(config.NAME_KEY)
            if name:
                props_out["name"] = str(name).strip()
            props_out["fixed_route"] = (
                "yes" if str(props_in.get(config.FIXED_ROUTE_KEY, "")).strip()
                .lower() == "yes" else "no"
            )
            on_demand = props_in.get(config.ON_DEMAND_KEY)
            if on_demand:
                props_out["on_demand"] = str(on_demand).strip()
            # The ArcGIS row id. Shown so a reviewer can find the row again in
            # the same extract -- it can renumber when the layer is
            # republished, so nothing keys on it; the stop ID is the key.
            objectid = props_in.get(config.OBJECT_ID_KEY)
            if objectid is not None:
                props_out["objectid"] = int(objectid)

            lon, lat = geom["coordinates"][:2]
            out.write(json.dumps({
                "type": "Feature",
                # ~0.1 m. The service hands back 14 decimal places.
                "geometry": {"type": "Point",
                             "coordinates": [round(float(lon), 6),
                                             round(float(lat), 6)]},
                "properties": props_out,
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


def stop_ref(value):
    """The City's stop ID as an int, or None.

    The service types StopId as a double, so it arrives as 109 or 109.0 (or a
    Decimal, through ijson). OSM's ref is written "109" -- never
    "109.0" -- so it is normalised here, once, and everything downstream
    compares ints.
    """
    if value is None or value == "":
        return None
    try:
        f = float(value)
    except (TypeError, ValueError):
        return None
    if f != int(f) or f <= 0:
        return None
    return int(f)
