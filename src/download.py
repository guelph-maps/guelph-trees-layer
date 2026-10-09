"""Download the Guelph Bus Stops GeoJSON from the City's ArcGIS service.

The parks sibling's download step with a different layer number. An ArcGIS
FeatureServer `/query` is generated per request, so it has no Last-Modified
or Content-Length worth caching on. The body is ~230 KB, cheap enough to
fetch every time; what is compared is a **sha256 of the response**.
Identical hash means the City has not touched the layer, and the existing dated
file is kept -- which also keeps DATA_DATE meaning "when the City data last
changed" rather than "when the build last ran".

The output is one dated GeoJSON FeatureCollection in data/, which is all `slim`
and the rest of the pipeline know about.

Every request is gated on a usable link first (``addressvault.net``, the same
gate the address vault pulls behind). The weekly trigger is what makes that
worth doing: a run lost to a dead link is not retried for a week.
"""

import hashlib
import json
import os
import time
from datetime import date

import requests
from addressvault import net

from src import config

# The Bus Stops layer is ~650 points, ~230 KB of GeoJSON -- a retry costs
# a couple of seconds when the link is healthy and buys back a week when it is
# not.
RETRIES = 3
RETRY_WAIT = 30


def download(force=False):
    """Download the latest Bus Stops GeoJSON.

    Returns (status, filepath) where status is "DOWNLOADED" or "SKIPPED".
    Raises ``net.LinkUnavailable`` when the host has no usable link: nothing was
    attempted, so the caller exits 75 rather than reporting a build failure.
    """
    os.makedirs(config.DATA_DIR, exist_ok=True)

    # Ask before doing anything. Offline or metered, nothing below can work, and
    # finding out here costs a second instead of a 600 s read timeout. Never
    # block waiting for the link: the task carries a hard ExecutionTimeLimit, so
    # a wait would be killed mid-sleep -- retrying is the scheduler's job.
    net.wait_for_link(wait=False)

    print("Querying the Bus Stops FeatureServer ...")
    body = _fetch(config.DATASET_URL)
    _check_payload(body)
    digest = hashlib.sha256(body).hexdigest()

    sidecar = _load_sidecar()
    if not force and sidecar and sidecar.get("sha256") == digest:
        existing = os.path.join(config.DATA_DIR, sidecar.get("filename", ""))
        if os.path.isfile(existing):
            print("Unchanged since the last download.")
            return "SKIPPED", existing

    # No server-side edit date exists on this service (the layer has no
    # editingInfo and no last_edited_date field), so the file is dated by the
    # day the change was first seen here.
    filename = f"bus-stops-{date.today().isoformat()}.geojson"
    filepath = os.path.join(config.DATA_DIR, filename)
    with open(filepath, "wb") as f:
        f.write(body)
    print(f"Done: {filepath} ({len(body) // 1024:,} KB)")

    _save_sidecar(digest, len(body), filename)
    return "DOWNLOADED", filepath


def _fetch(url):
    """GET ``url``, retrying a dropped connection. Returns the raw body."""
    for attempt in range(RETRIES + 1):
        try:
            resp = requests.get(
                url,
                headers={"User-Agent": config.USER_AGENT},
                timeout=600,
            )
            resp.raise_for_status()
            return resp.content
        except requests.RequestException as e:
            if attempt == RETRIES:
                # Out of retries. If the link itself is down then this was never
                # a City failure -- say so, and the run exits 75 instead of
                # logging a build error against a service never reached at all.
                net.wait_for_link(wait=False)
                raise
            print(f"  query failed ({e}); retrying in {RETRY_WAIT}s")
            time.sleep(RETRY_WAIT)


def _check_payload(body):
    """Reject an answer that is not a whole FeatureCollection.

    An ArcGIS service answers an error with HTTP 200 and a JSON body carrying an
    ``error`` object, so raise_for_status proves nothing. It also silently
    truncates a page at maxRecordCount and sets ``exceededTransferLimit``; at
    ~650 rows against a limit of 100,000 that cannot happen today, so this
    raises rather than paginating -- a partial extract would report the stops
    that fell off the end as missing from OSM, and that is worth stopping for,
    not working around.
    """
    try:
        doc = json.loads(body)
    except ValueError as e:
        raise RuntimeError(f"The service did not return JSON: {e}")
    if "error" in doc:
        raise RuntimeError(f"The service returned an error: {doc['error']}")
    if doc.get("type") != "FeatureCollection":
        raise RuntimeError(f"Expected a FeatureCollection, got {doc.get('type')!r}")
    if doc.get("exceededTransferLimit"):
        raise RuntimeError(
            "The service truncated the result (exceededTransferLimit). The "
            "download is a partial extract -- paginate with resultOffset / "
            "resultRecordCount before trusting it."
        )
    n = len(doc.get("features") or ())
    print(f"  {n:,} features")
    if not n:
        raise RuntimeError("The service returned an empty FeatureCollection.")


def _load_sidecar():
    if os.path.isfile(config.LAST_DOWNLOAD_PATH):
        with open(config.LAST_DOWNLOAD_PATH, encoding="utf-8") as f:
            return json.load(f)
    return None


def _save_sidecar(digest, size, filename):
    with open(config.LAST_DOWNLOAD_PATH, "w", encoding="utf-8") as f:
        json.dump(
            {"sha256": digest, "bytes": size, "filename": filename}, f, indent=2
        )
