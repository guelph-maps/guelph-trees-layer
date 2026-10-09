# Guelph Bus Stops Layer

Turns the City of Guelph [Bus Stops](https://gismaps.guelph.ca/hosting/rest/services/OpenData/OpenData1/FeatureServer/33)
layer (648 Guelph Transit stops on 2026-10-08) into map-tile layers that
OpenStreetMap mappers can add to the **JOSM** and **iD** editors as a reference
overlay, plus a gap page listing where OSM's bus stops disagree with the City's.

**Live layer and how to add it: https://guelph-maps.github.io/guelph-bus-stops-layer/**

Part of the [guelph-maps](https://github.com/guelph-maps) organisation. It is a
clone of [guelph-parks-layer](https://github.com/guelph-maps/guelph-parks-layer)
([live](https://guelph-maps.github.io/guelph-parks-layer/)): the same
`download -> slim -> compare -> vector -> raster -> site -> publish` pipeline,
the same ArcGIS service, the same Overpass, link-gate and publish rules. A
person who knows one knows the other. What differs follows from the geometry
-- these are points, not polygons:

- **slim** keeps points and normalises the stop ID (see below);
- **compare** matches by the stop's `ref`, then by distance, not by overlap;
- **raster** draws dots with stop-number labels instead of filled outlines.

## What it produces

- **Vector tiles** (MVT, z12-19) &mdash; interactive in iD; click a stop to
  read its `ref`, `name`, `fixed_route`, `on_demand` and `objectid` tags.
- **Raster tiles** (PNG, z13-17) &mdash; a blue dot per stop (a hollow ring for
  an on-demand-only pick-up point), the stop number from z15, and number plus
  name at z17 wherever the name fits. A readable backdrop for JOSM.
- A **landing page** with copy-paste "add this layer" instructions for both
  editors.
- A **gap-review page** (`/gaps/`) on an interactive map and a filterable
  table, and its **history page** (`/gaps/history.html`) charting the counts,
  one point per weekly run.

## The stop ID is the key

The City's `StopId` is what Guelph's OSM mappers already put in `ref`. In a
downtown sample on 2026-10-08, all 26 Guelph Transit stops carrying a `ref`
matched a `StopId` within 60 m, and every one with a name had the City's name
verbatim ("Guelph Central Station Platform 1" is `ref=5833` on both sides).
That makes this layer conflatable by ID rather than by guesswork, and it is
why the gap page can say *which* stop an OSM node is.

The service types `StopId` as a double. `slim` casts it to an int once
(`109.0` in a tag would be a mapping error) and refuses an extract in which two
points share one, since everything downstream keys on it.

## The gap page

Overpass is asked for every `highway=bus_stop` and every
`public_transport=platform` + `bus=yes` in the city (`out center`, so a platform
mapped as a way gets a position too). Then, per City stop:

| Status | Meaning | What to do |
|---|---|---|
| `missing` | no OSM stop has this `ref`, and none without a `ref` sits beside it | add the stop |
| `noref` | an OSM stop with no `ref` is this one | add `ref=<StopId>` |
| `far` | the OSM stop with this `ref` is more than 50 m off | check which is in the wrong place |
| `duplicate` | two OSM stops more than 50 m apart carry this `ref` | one of them is wrong |
| `unnamed` | matched, but the OSM stop has no `name` | add the name |
| `mismatch` | matched, but the OSM name differs | compare the two |
| `ondemand` | not in OSM, and the City serves it by on-demand transit only | a mapper's call (off by default) |
| `osm_only` | an OSM Guelph Transit stop whose `ref` the City does not list | retired, renumbered or a typo |

Three rules keep that honest:

- **Nearest-first for stops without a ref.** 188 of the 648 City stops have
  another City stop within 30 m -- usually the same intersection in the other
  direction; the station's platforms are 6-11 m apart. So an OSM stop with no
  `ref` is given to the City stop *nearest it*, and only within `NEAR_M` (30 m);
  "any City stop within 30 m" would hand one OSM node to both sides of the road.
- **Never across agencies.** An OSM stop whose `network` or `operator` names
  another agency (GO Transit and GOST share Guelph Central Station) is never
  matched, never offered a ref, and never called `osm_only`. A stop with no
  network at all is still a candidate -- "add the ref" is exactly the work
  item for it -- but only an explicitly Guelph Transit stop can be `osm_only`.
- **Names compared strictly.** Case, punctuation and `&`/`and` only. An OSM name
  that drops the direction ("Gordon at Nottingham" for "... southbound") is a
  real difference worth a look, not noise to normalise away.

A platform mapped as both a node and an outline with the same `ref` is not a
`duplicate`: that takes two stops *more than* `FAR_M` apart.

The Overpass handling is the parks layer's unchanged: every mirror in
`OVERPASS_URLS` is tried `OVERPASS_ROUNDS` times, a reply under
`OSM_MIN_ELEMENTS` elements or older than `OSM_MAX_AGE_DAYS` is refused, the
page prints the OSM timestamp the mirror declares, and a run that fell back to
cached OSM says so on the page and exits 70 after publishing. See the parks
README for the history behind each rule.

### What a first run looks like

The build environment this was written in could not reach any Overpass mirror,
so the comparison was smoke-tested against five samples of 1-2 km2 pulled from
the OSM API (downtown, the university, the west end, the south end and the
north end): 113 City stops, 118 OSM stops, and 10 missing, 4 noref, 1 duplicate, 12
unnamed, 1 mismatch and 3 on-demand. The duplicate is `ref=801` (Wyndham at
Fountain southbound) on two nodes 59 m apart. The city-wide numbers come from
the first real run.

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
python run.py download   # fetch the latest Bus Stops GeoJSON (smart-cached)
python run.py slim       # normalise to a slim GeoJSONL
python run.py compare    # diff City stops against OSM -> data/gaps.geojson
python run.py backfill   # one-off: reconstruct past weeks' gap counts from OSM history
python run.py vector     # build vector (MVT) tiles via WSL tippecanoe
python run.py raster     # build labelled raster (PNG) tiles
python run.py site       # render the landing page (+ the gap-review page)
python run.py publish    # force-push the site to the gh-pages branch

python run.py build      # download + slim + compare + vector + raster + site
python run.py update     # build + publish  (the scheduled entry point)
```

Build output lands in `build/site/`; that directory is what gets published to
an orphan `gh-pages` branch, recreated on every build so history never grows.

## Tile endpoints

```
https://guelph-maps.github.io/guelph-bus-stops-layer/tiles/vector/{z}/{x}/{y}.pbf   (z12-19)
https://guelph-maps.github.io/guelph-bus-stops-layer/tiles/raster/{z}/{x}/{y}.png   (z13-17)
```

The vector layer name is `bus_stops`. tippecanoe runs with `-r1`, so no stop is
thinned out of the low zooms.

At z17 the raster gives every stop at least its number, even where numbers
overlap: the station's platforms are 7-13 px apart at that zoom, and a missing
number there is worse than a crowded one. In iD, the vector layer separates
them.

## Scheduling (Windows)

Run as Administrator:

```powershell
.\schedule-add.ps1      # registers a weekly task "kk-GuelphBusStopsLayer", Thursdays 16:30
.\schedule-remove.ps1   # unregisters it
```

Thursday keeps it clear of the parks layers' Monday and Tuesday slots and the
buildings layer's Wednesday one, so no two of them drive tippecanoe in the same
WSL instance at once. Exit codes and
the restart policy are the parks layer's: **75** when there is no usable link
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

Bus stop data is &copy; City of Guelph, published under the
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
