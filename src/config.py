"""Configuration constants for the Guelph trees tile layer build.

Single source of truth. No logic here.

Sibling of guelph-bus-stops-layer: the same pipeline (download -> slim ->
compare -> vector + raster -> site -> publish), pointed at the City of Guelph
street and park tree inventory instead of Bus Stops. What differs follows from
the scale and from what a tree carries: 42,000 points with no ID a mapper can
read off the tree, so the comparison matches by distance alone, and the gap
page leads with 250 m cells instead of rows.
"""

import os

PROJECT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA_DIR = os.path.join(PROJECT_DIR, "data")
BUILD_DIR = os.path.join(PROJECT_DIR, "build")
SITE_DIR = os.path.join(BUILD_DIR, "site")
ASSETS_DIR = os.path.join(PROJECT_DIR, "assets")
LOGS_DIR = os.path.join(PROJECT_DIR, "logs")

MBTILES_PATH = os.path.join(BUILD_DIR, "trees.mbtiles")
SLIM_PATH = os.path.join(DATA_DIR, "trees-slim.geojsonl")
COUNT_PATH = os.path.join(DATA_DIR, "trees.count")
LAST_DOWNLOAD_PATH = os.path.join(DATA_DIR, ".last-download.json")

VECTOR_TILE_DIR = os.path.join(SITE_DIR, "tiles", "vector")
RASTER_TILE_DIR = os.path.join(SITE_DIR, "tiles", "raster")

# Data source: City of Guelph Trees (OpenData1 layer 7), 57,700 points on
# 2026-10-09, of which 42,712 are owned by the City or sit on a property line.
# The rest are private trees the City has inventoried, which are not this
# layer's business: the scope is OWNER IN ('CITY','BOUNDARY'), as the import
# audit's REPORT says. Every row is TREE_STATUS=ACTIVE; a removed tree leaves
# the extract rather than being flagged.
#
# Same service as the bus stops and parks layers, so the same rules: outSR=4326
# is not optional, and f=geojson makes the service do the conversion. Only the
# fields used are asked for -- 18.5 MB, one request, well under the layer's
# maxRecordCount of 100,000.
FEATURE_SERVER = (
    "https://gismaps.guelph.ca/hosting/rest/services/OpenData/OpenData1"
    "/FeatureServer/7"
)
OUT_FIELDS = (
    "OBJECTID,ASSET_ID,GENUS,SPECIES_SCIENTIFIC,SPECIES_COMMON,DBH,OWNER,"
    "DT_PLANTED,TREE_STATUS,LOCATION_ID_DESCRIPTION"
)
DATASET_URL = (
    f"{FEATURE_SERVER}/query?where=OWNER%20IN%20(%27CITY%27,%27BOUNDARY%27)"
    f"&outFields={OUT_FIELDS}&outSR=4326&f=geojson"
)
# The service's own page. Guelph's open-data catalogue has no stable item page
# for this layer, so the FeatureServer URL is what a reader is pointed at.
DATASET_PAGE = FEATURE_SERVER
# This layer REDISTRIBUTES City data as published tiles, and the licence
# attaches a required attribution to that. It is carried on the landing page
# and the gap page. See the README section "Licence"; it is a condition, not a
# courtesy.
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
GITHUB_REPO = "guelph-maps/guelph-trees-layer"
PAGES_URL = "https://guelph-maps.github.io/guelph-trees-layer"

# WSL distro that has tippecanoe installed (see wsl-setup.md).
WSL_DISTRO = "Ubuntu"

# Vector tiles. iD requests tiles at (map zoom - 1) and does NOT overzoom, so
# tiles must be generated natively through the zooms used for mapping. A tree
# is only worth clicking at street zooms, and 42,000 of them in a z12 tile is a
# slow editor for nothing; z14 is the buildings sibling's floor too.
VECTOR_MINZOOM = 14
VECTOR_MAXZOOM = 19
VECTOR_LAYER_NAME = "trees"

# Raster tiles. Editors overzoom the top zoom. Crowns are drawn to scale from
# z16, where a street tree is several pixels across; see raster.py.
RASTER_ZOOMS = [13, 14, 15, 16, 17, 18]

# Source property keys read from the Trees GeoJSON.
ASSET_ID_KEY = "ASSET_ID"           # e.g. "FT10001"; unique, on every row
GENUS_KEY = "GENUS"                 # e.g. "QUERCUS"; "UNKNOWN" or null on ~870
SPECIES_KEY = "SPECIES_SCIENTIFIC"  # e.g. "QUERCUS RUBRA", "MALUS SPECIES"
COMMON_KEY = "SPECIES_COMMON"       # e.g. "RED OAK"
DBH_KEY = "DBH"                     # trunk diameter at breast height, in cm
OWNER_KEY = "OWNER"                 # "CITY" or "BOUNDARY"
PLANTED_KEY = "DT_PLANTED"          # epoch ms, or null
ADDRESS_KEY = "LOCATION_ID_DESCRIPTION"  # closest address, "133 CLAIRFIELDS DR W"
OBJECT_ID_KEY = "OBJECTID"          # ArcGIS row id; see the note in slim.py

# A DBH above this is a data-entry error, not a tree: the extract carries one
# of 879 cm, and Ontario's largest trees are under 250. Such a tree keeps its
# genus and species but gets no circumference -- a mapper copying "27.6" into
# OSM would be worse than leaving it blank.
MAX_DBH_CM = 300

# --- OSM comparison (gap-review page) ---
# Trees are pulled from OSM via Overpass and matched to the City's by distance;
# the result feeds build/site/gaps/.
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
# Floor on a reply's element count. OSM had 50,688 trees inside the city on
# 2026-10-09 (more in the bounding box); a reply under 30,000 is a truncated or
# wrong-region answer, and taking it at face value would report half the city's
# trees as missing.
OSM_MIN_ELEMENTS = 30000
# Oldest OSM data a reply may carry (its osm3s.timestamp_osm_base) and still be
# compared against. A healthy instance is minutes behind.
OSM_MAX_AGE_DAYS = 3
# Overpass rejects the default requests User-Agent (HTTP 406); identify the tool.
USER_AGENT = "guelph-trees-layer/0.1 (toronto@comentality.com)"
# Guelph bounding box (S, W, N, E), verbatim from guelph-beholder's config.toml
# -- a touch larger than the city.
GUELPH_BBOX = (43.46, -80.34, 43.60, -80.14)
# Guelph city centre; where the preview and gap maps open.
GUELPH_CENTER = (43.5448, -80.2482)
OSM_CACHE_PATH = os.path.join(DATA_DIR, "osm-trees.json")
# When the cached Overpass reply was fetched, from where, and -- more usefully
# -- the OSM database timestamp the reply declares about itself.
OSM_FETCH_PATH = os.path.join(DATA_DIR, ".osm-fetch.json")
CELLS_PATH = os.path.join(DATA_DIR, "cells.geojson")
TREES_PATH = os.path.join(DATA_DIR, "trees.json")
GAPS_COUNT_PATH = os.path.join(DATA_DIR, "gaps.count.json")

# Matching, in metres. An OSM tree within MATCH_M of a City tree, nearest-first
# and one-to-one, is that tree. The recalibration measured the gap at 3, 5 and
# 10 m (35,486 / 34,291 / 33,452 City trees unmatched): it barely moves, so the
# radius is not doing the work. 5 m is about a street tree's crown radius --
# OSM's trees here were traced from crowns, the City's are trunk positions.
MATCH_M = 5

# Cells. Thirty-four thousand unmatched trees are not a work list; the page
# leads with CELL_M squares instead. A cell is
#   empty_cell -- at least CELL_MIN_CITY City trees and no OSM tree at all;
#   thin_cell  -- at least CELL_MIN_CITY City trees, some OSM trees, but under
#                 THIN_SHARE of the City trees have an OSM tree within MATCH_M.
#                 Matched share, not a count ratio: OSM's trees include
#                 woodlots and back yards the City does not inventory, and a
#                 cell's counts can agree while its trees do not (the trap the
#                 audit's first tree review fell into);
#   covered    -- the rest with at least CELL_MIN_CITY trees;
#   sparse     -- fewer than CELL_MIN_CITY City trees: listed, but off by
#                 default -- three trees in a cell is not a neighbourhood.
CELL_M = 250
CELL_MIN_CITY = 10
THIN_SHARE = 0.5

# --- Gap history (trend page) ---
HISTORY_PATH = os.path.join(DATA_DIR, "history.jsonl")
HISTORY_URL = f"{PAGES_URL}/gaps/history.jsonl"
# Backfill. overpass-api.de keeps OSM's full history and answers the same
# query for a past instant ([date:...]). Weekly points, one attempt per date,
# and a pause between dates -- attic queries are heavier than live ones, and
# a 50,000-tree one more so.
ATTIC_URL = "https://overpass-api.de/api/interpreter"
BACKFILL_SINCE = "2026-06-01"   # a Monday; the parks sibling's start, so the charts line up
BACKFILL_STEP_DAYS = 7          # the weekly schedule's cadence
BACKFILL_PAUSE = 60
