# Guelph Trees Layer

Turns the City of Guelph [Trees](https://gismaps.guelph.ca/hosting/rest/services/OpenData/OpenData1/FeatureServer/7)
inventory -- the street and park trees the City owns or shares on a property
line, 42,712 on 2026-10-09 -- into map-tile layers that OpenStreetMap mappers
can add to the **JOSM** and **iD** editors as a reference overlay, plus a gap
page showing where OSM has no trees at all and which mapped trees lack the
species and girth the City records.

**Live layer and how to add it: https://guelph-maps.github.io/guelph-trees-layer/**

Part of the [guelph-maps](https://github.com/guelph-maps) organisation. It is a
clone of [guelph-bus-stops-layer](https://github.com/guelph-maps/guelph-bus-stops-layer)
([live](https://guelph-maps.github.io/guelph-bus-stops-layer/)): the same
`download -> slim -> compare -> vector -> raster -> site -> publish` pipeline,
the same ArcGIS service, the same Overpass, link-gate and publish rules. What
differs follows from the scale and from what a tree carries:

- **slim** writes names the way OSM does and the girth in OSM's unit;
- **compare** matches by distance alone, and rolls the result up into 250 m
  cells, because 34,000 unmatched trees are not a work list;
- **raster** draws crowns, coloured by genus and sized from the trunk.

Why build it at all: the [import audit](https://github.com/guelph-maps/guelph-osm-import-audit)
found OSM's ~50,000 Guelph trees are almost all one mapper's hand-tracing of
bare `natural=tree` -- genus on 3, species on 12 -- and that only 19% of the
City's trees have an OSM tree within 5 m. Half the city has none.

## What it produces

- **Vector tiles** (MVT, z14-19) &mdash; interactive in iD; click a tree to
  read its tags (below).
- **Raster tiles** (PNG, z13-18) &mdash; a crown per tree, coloured by genus,
  drawn to an approximate crown size from the trunk diameter at z16-18. A
  readable backdrop for JOSM.
- A **landing page** with copy-paste "add this layer" instructions for both
  editors and the colour legend.
- A **gap-review page** (`/gaps/`): a map of 250 m cells and a table of them,
  drilling into one cell's trees, and its **history page**
  (`/gaps/history.html`) charting the counts, one point per weekly run. Each
  cell links to **JOSM** for its square, and each tree to openstreetmap.org,
  iD and JOSM (needs JOSM running with Preferences &rarr; Remote Control
  enabled).

## What a tree carries

| Key | Example | Is it an OSM tag? |
|---|---|---|
| `genus` | `Quercus` | yes |
| `species` | `Quercus rubra`, `Platanus x acerifolia` | yes |
| `circumference` | `0.94` | yes -- metres, `DBH × π / 100` |
| `common_name` | `Red Oak` | no -- often a cultivar's name ("Green Mountain Maple" is a sugar maple), so not `species:en` |
| `dbh_cm` | `30` | no -- the City's trunk diameter, cm |
| `planted` | `2014` | no |
| `owner` | `city`, `boundary` | no |
| `address` | `133 Clairfields Dr W` | no -- the City's nearest address |
| `city_asset_id` | `FT10001` | no -- the City's inventory number. It is not on the tree, so it is not a `ref` |
| `objectid` | `1` | no -- the ArcGIS row, for finding it again |

Names are converted once, in `slim.taxon`: capitals to OSM's case, hybrids
with a plain `x` (`Platanus x acerifolia` is on ~21,000 OSM trees, the `×`
form on none), varieties back to their species, and "MALUS SPECIES" or a
quoted cultivar to the genus alone. Three City names cannot be right and are
corrected: `RHAMUS` and `GYMNOCLADUS DIOCUS` are misspellings, and the 31
`CONIUM MACULATUM` rows -- poison hemlock, a herb -- are called "HEMLOCK",
the eastern hemlock's name, so they get `genus=Tsuga` and no species. A DBH
over 300 cm (one row says 879) gets no circumference.

**A word on copying.** Individual-tree imports are contested in OSM, and
copying species or girth off this overlay is, in effect, an attribute import.
The City licence is compatible (below), but compatible is not consensus. The
layer is a reference: add a tree you can see on imagery, a species you have
verified. The landing page and the gap page both say so.

## The gap page

Overpass is asked for every `natural=tree` node in the city (nodes only -- a
tree mapped as an area is a wood). Each City tree is matched to an OSM tree
**nearest-first and one-to-one within 5 m** (`MATCH_M`): every pair within
5 m is sorted by distance and taken unless one side is spoken for. Street
trees stand 8-10 m apart, often closer, so "nearest OSM tree for each City
tree" would hand one node to two trees. The recalibration measured the gap at
3, 5 and 10 m and it barely moves; the radius is not doing the work.

Then the city is cut into 250 m cells (`CELL_M`), counted from the bounding
box's south-west corner:

| Cell status | Meaning |
|---|---|
| `empty_cell` | ≥ 10 City trees (`CELL_MIN_CITY`) and no OSM tree at all |
| `thin_cell` | ≥ 10 City trees, some OSM trees, but under half (`THIN_SHARE`) of the City's have one within 5 m |
| `covered` | the rest with ≥ 10 City trees |
| `sparse` | fewer than 10 City trees (off by default) |

`thin_cell` is decided by the **matched share, not the counts**. OSM's trees
include woodlots and back yards the City does not inventory, and a cell's
counts can agree while its trees do not -- the trap the audit's first tree
review fell into.

Inside a cell, per tree:

| Tree status | Meaning |
|---|---|
| `missing` | no OSM tree within 5 m |
| `nospecies` | matched, but the OSM tree has no `genus`, `species` or `taxon`, and the City knows the genus |
| `nogirth` | matched, but the OSM tree has no `circumference`, and the City has a DBH |

A matched tree can need both. **There is no `osm_only`**: the 40,000-odd OSM
trees with no City tree beside them are woodlots, private land and campus, not
mistakes.

The page loads `cells.geojson` (~900 squares) on open and `trees.json` (the
per-tree rows, ~5 MB) only on the first drill-down, so it never draws 42,000
markers.

The Overpass handling is the parks layer's unchanged: every mirror in
`OVERPASS_URLS` is tried `OVERPASS_ROUNDS` times, a reply under
`OSM_MIN_ELEMENTS` (30,000) elements or with a `timestamp_osm_base` older than
`OSM_MAX_AGE_DAYS` is refused, the page prints the OSM timestamp the mirror
declares, and a run that fell back to cached OSM says so on the page and exits
70 after publishing.

### What the first run found

2026-10-09, OSM base 2026-10-09 21:59Z: 42,712 City trees and 53,997 OSM trees
in the bounding box. 8,189 City trees (19%) have an OSM tree within 5 m;
**34,523 do not**. **452 cells** with ten or more City trees have no OSM tree
at all, holding 23,036 trees, and **159 are thin**. Of the matched trees,
**8,032** have no species in OSM and **8,095** no circumference. (The audit's
"589 of 914 cells" counted any cell with a City tree and used a UTM grid;
counted that way here it is 582.)

## Setup

1. Install Python dependencies:
   ```
   pip install -r requirements.txt
   ```
2. Set up WSL2 + tippecanoe once &mdash; see [wsl-setup.md](wsl-setup.md).
3. Create the `gh-pages` source once: in the GitHub repo, set
   **Settings &rarr; Pages &rarr; Source** to the `gh-pages` branch (root).

## Usage

```
python run.py download   # fetch the latest Trees GeoJSON (smart-cached)
python run.py slim       # normalise to a slim GeoJSONL
python run.py compare    # match City trees against OSM -> cells + trees
python run.py backfill   # one-off: reconstruct past weeks' gap counts from OSM history
python run.py vector     # build vector (MVT) tiles via WSL tippecanoe
python run.py raster     # build crown raster (PNG) tiles
python run.py site       # render the landing page (+ the gap-review page)
python run.py publish    # force-push the site to the gh-pages branch

python run.py build      # download + slim + compare + vector + raster + site
python run.py update     # build + publish  (the scheduled entry point)
```

Build output lands in `build/site/`; that directory is what gets published to
an orphan `gh-pages` branch, recreated on every build so history never grows.

## Tile endpoints

```
https://guelph-maps.github.io/guelph-trees-layer/tiles/vector/{z}/{x}/{y}.pbf   (z14-19)
https://guelph-maps.github.io/guelph-trees-layer/tiles/raster/{z}/{x}/{y}.png   (z13-18)
```

The vector layer name is `trees`. tippecanoe runs with `-r1`, so no tree is
thinned out of the low zooms; that is why the floor is z14 and not lower. The
vector pyramid is 14,466 tiles and 23 MB; GitHub Pages' limit is 1 GB.

## Scheduling (Windows)

```powershell
.\schedule-add.ps1      # registers a weekly task "kk-GuelphTreesLayer", Sundays 16:30
.\schedule-remove.ps1   # unregisters it
```

Sunday because Monday to Saturday 16:30 belong to the other layers, so no two
of them drive tippecanoe in the same WSL instance at once. Exit codes and the
restart policy are the parks layer's: **75** when there is no usable link
(nothing was fetched or published), **70** after publishing when the gap page
had to fall back to cached OSM.

## Tests

```
python tests\test_tilemath.py
python tests\test_slim.py
python tests\test_raster.py
pytest                       # the above, plus the download / compare / history / publish tests
```

## Licence / attribution

Tree data is &copy; City of Guelph, published under the
[Open Government Licence &ndash; City of Guelph](https://gismaps.guelph.ca/Images/OpenDataLicenceVersion2.pdf).
The OSMF Licence Working Group approved that licence as `compatible` on
2024-09-09.

**This project redistributes City data** as published tiles on a public URL,
and the licence attaches a required attribution to that. The landing page, the
gap page and the history page all carry, visibly:

> Contains information licensed under the Open Government Licence – City of Guelph.

linked to the licence text. The same attribution is embedded in the vector tile
metadata. This is a condition of use, not a courtesy: if you fork this, the
notice travels with it.
