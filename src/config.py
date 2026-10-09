"""Configuration constants for the Guelph bus stops tile layer build.

Single source of truth. No logic here.

Sibling of guelph-parks-layer: the same pipeline (download -> slim -> compare
-> vector + raster -> site -> publish), pointed at the City of Guelph Bus Stops
layer instead of Park Boundary. The structural differences all follow from
the geometry: these are points, so the slim step keeps points, the raster step
draws dots with stop-number labels, and the OSM comparison matches by the
stop's ID (OSM's ``ref``) and by distance instead of by polygon overlap.
"""

import os

PROJECT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA_DIR = os.path.join(PROJECT_DIR, "data")
BUILD_DIR = os.path.join(PROJECT_DIR, "build")
SITE_DIR = os.path.join(BUILD_DIR, "site")
ASSETS_DIR = os.path.join(PROJECT_DIR, "assets")
LOGS_DIR = os.path.join(PROJECT_DIR, "logs")

MBTILES_PATH = os.path.join(BUILD_DIR, "bus-stops.mbtiles")
SLIM_PATH = os.path.join(DATA_DIR, "bus-stops-slim.geojsonl")
COUNT_PATH = os.path.join(DATA_DIR, "bus-stops.count")
LAST_DOWNLOAD_PATH = os.path.join(DATA_DIR, ".last-download.json")

VECTOR_TILE_DIR = os.path.join(SITE_DIR, "tiles", "vector")
RASTER_TILE_DIR = os.path.join(SITE_DIR, "tiles", "raster")

# Data source: City of Guelph Bus Stops (OpenData1 layer 33), 648 points on
# 2026-10-08. Same service as the parks layer, so the same rules: the geometry
# is stored in EPSG:26917 and outSR=4326 is not optional, and f=geojson makes
# the service do the Esri-JSON -> GeoJSON conversion.
FEATURE_SERVER = (
    "https://gismaps.guelph.ca/hosting/rest/services/OpenData/OpenData1"
    "/FeatureServer/33"
)
DATASET_URL = (
    f"{FEATURE_SERVER}/query?where=1%3D1&outFields=*&outSR=4326&f=geojson"
)
# The service's own page. Guelph's open-data catalogue has no stable item page
# for this layer, so the FeatureServer URL is what a reader is pointed at.
DATASET_PAGE = FEATURE_SERVER
# This layer REDISTRIBUTES City data as published tiles -- which the beholder
# products never do -- and the licence attaches a required attribution to that.
# It is carried on the landing page and the gap page. See the README section
# "Licence"; it is a condition, not a courtesy.
LICENSE_URL = "https://gismaps.guelph.ca/Images/OpenDataLicenceVersion2.pdf"
LICENSE_NOTICE = (
    "Contains information licensed under the Open Government Licence "
    "– City of Guelph."
)
# Plain-ASCII attribution embedded in tile metadata (it is passed through the
# WSL shell, where the en dash in LICENSE_NOTICE is not safe).
ATTRIBUTION = (
    "Contains information licensed under the Open Government Licence "
    "- City of Guelph"
)

# GitHub Pages target. PAGES_URL carries NO trailing slash: every tile URL on
# the site is built as f"{PAGES_URL}/tiles/...", and a trailing slash would
# emit "...//tiles/...", which is the one thing on the page a reader copies
# verbatim into their editor.
GITHUB_REPO = "guelph-maps/guelph-bus-stops-layer"
PAGES_URL = "https://guelph-maps.github.io/guelph-bus-stops-layer"

# WSL distro that has tippecanoe installed (see wsl-setup.md).
WSL_DISTRO = "Ubuntu"

# Vector tiles. iD requests tiles at (map zoom - 1) and does NOT overzoom, so
# tiles must be generated natively through the zooms used for mapping. A stop
# is a point a mapper only looks for at street zooms, hence z12, not the parks
# layer's z10.
VECTOR_MINZOOM = 12
VECTOR_MAXZOOM = 19
VECTOR_LAYER_NAME = "bus_stops"

# Raster tiles. Editors overzoom z17 -> z18+. Stop numbers appear from
# LABEL_REF_MIN_ZOOM and names at the top zoom; see raster.py.
RASTER_ZOOMS = [13, 14, 15, 16, 17]

# Source property keys read from the Bus Stops GeoJSON.
#
# REF_KEY is what makes this layer conflatable at all: the City's StopId is
# what OSM mappers already put in ``ref``. In a downtown sample on 2026-10-08,
# all 26 Guelph Transit stops carrying a ref matched a StopId within 60 m, and
# every one that had a name had the City's (5833 is "Guelph Central Station
# Platform 1" on both sides). The service types it as a double, so
# slim casts it to int -- "109.0" in a tag would be a mapping error.
REF_KEY = "StopId"            # e.g. 109
NAME_KEY = "StopName"         # e.g. "Edinburgh at Ginger northbound"
OBJECT_ID_KEY = "OBJECTID"    # ArcGIS row id; see the note in slim.py
# "Fixed route service?" -- Yes on 627 of 648 stops. The other 21 are served
# only by on-demand transit: a pick-up point, not a stop any bus timetables.
# Whether those belong in OSM as highway=bus_stop is a mapper's call, so the
# gap page gives them their own category instead of listing them as missing.
FIXED_ROUTE_KEY = "Conv"
# Free text, e.g. "Sunday and Statutory Holiday". Carried through to the tiles
# so a mapper can read it; nothing is decided on it.
ON_DEMAND_KEY = "On_demand_service"

# --- OSM comparison (gap-review page) ---
# Bus stops are pulled from OSM via Overpass and matched against the City
# points: by ref first, by distance second; the gaps feed build/site/gaps/.
#
# The mirror policy is the parks layer's, unchanged and for the same reasons:
# overpass.kumi.systems and overpass.private.coffee have both been caught
# serving months-stale data, which is the worse failure because it looks
# exactly like success.
OVERPASS_URLS = (
    "https://overpass-api.de/api/interpreter",
)
OVERPASS_TIMEOUT = 300
OVERPASS_ROUNDS = 3
OVERPASS_ROUND_WAIT = 60
# Floor on a reply's element count. A downtown sample on 2026-10-08 carried 55
# platforms in about 1 km2, and the City lists 648 stops; a reply under 300 is
# a wrong-region or truncated answer, not a week's mapping. Taking an empty
# reply at face value would report every City stop as missing from OSM.
OSM_MIN_ELEMENTS = 300
# Oldest OSM data a reply may carry (its osm3s.timestamp_osm_base) and still be
# compared against. A healthy instance is minutes behind.
OSM_MAX_AGE_DAYS = 3
# Overpass rejects the default requests User-Agent (HTTP 406); identify the tool.
USER_AGENT = "guelph-bus-stops-layer/0.1 (toronto@comentality.com)"
# Guelph bounding box (S, W, N, E), verbatim from guelph-beholder's config.toml
# -- a touch larger than the city.
GUELPH_BBOX = (43.46, -80.34, 43.60, -80.14)
# Guelph city centre; where the preview and gap maps open.
GUELPH_CENTER = (43.5448, -80.2482)
OSM_CACHE_PATH = os.path.join(DATA_DIR, "osm-bus-stops.json")
# When the cached Overpass reply was fetched, from where, and -- more usefully
# -- the OSM database timestamp the reply declares about itself.
OSM_FETCH_PATH = os.path.join(DATA_DIR, ".osm-fetch.json")
GAPS_GEOJSON_PATH = os.path.join(DATA_DIR, "gaps.geojson")
GAPS_COUNT_PATH = os.path.join(DATA_DIR, "gaps.count.json")

# Whose stops these are. An OSM stop whose network or operator names another
# agency (GO Transit shares Guelph Central Station, platforms 12 and 13) is
# never matched, never offered a ref, and never reported as stale. A stop with
# no network or operator at all is still a candidate: "add ref" is exactly the
# work item for it.
NETWORK = "Guelph Transit"

# Distances, in metres. Measured against the downtown sample: ref-matched
# stops sit 1-9 m from the City point, a few up to ~25 m where a mapper put
# the node on the kerb and the City on the pole.
#   NEAR_M -- an OSM stop with no ref this close to a City stop is taken to
#             be it ("noref": add the ref). Distance alone cannot decide that:
#             188 of the 648 City stops have another City stop within 30 m
#             (the opposite side of the road; the station's platforms are 6-11
#             m apart), so the OSM stop must also be nearer this City stop
#             than any other. See compare._claim_unreferenced.
#   FAR_M  -- a ref-matched OSM stop further than this is reported "far": one
#             of the two is in the wrong place. A second OSM stop carrying the
#             same ref further than this from the first is a "duplicate".
NEAR_M = 30
FAR_M = 50

# --- Gap history (trend page) ---
HISTORY_PATH = os.path.join(DATA_DIR, "history.jsonl")
HISTORY_URL = f"{PAGES_URL}/gaps/history.jsonl"
# Backfill. overpass-api.de keeps OSM's full history and answers the same
# query for a past instant ([date:...]). Weekly points, one attempt per date,
# and a pause between dates -- attic queries are heavier than live ones.
ATTIC_URL = "https://overpass-api.de/api/interpreter"
BACKFILL_SINCE = "2026-06-01"   # a Monday; the parks sibling's start, so the charts line up
BACKFILL_STEP_DAYS = 7          # the weekly schedule's cadence
BACKFILL_PAUSE = 30
