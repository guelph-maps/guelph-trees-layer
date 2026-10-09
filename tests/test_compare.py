"""Unit tests for the OSM comparison: the Overpass fetch, and the matching.

The Overpass half is carried over from the parks sibling, where it pins the
failure that actually happened: on 2026-08-10 and again on 2026-08-17
overpass-api.de answered 504, the single attempt gave up, the run silently
compared against a fortnight-old cache and reported success. The matching half
is this layer's own: by distance alone, nearest-first and one-to-one, rolled up
into 250 m cells.
"""

import json
import os
import sys

import pytest
import requests
from datetime import datetime, timedelta, timezone

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from addressvault import net  # noqa: E402
from src import compare, config, site  # noqa: E402

# Minutes old, as a healthy instance's reply is; a fixed date would age past
# OSM_MAX_AGE_DAYS and be refused.
OSM_BASE = (datetime.now(timezone.utc) - timedelta(minutes=2)).strftime("%Y-%m-%dT%H:%M:%SZ")
GOOD = {"osm3s": {"timestamp_osm_base": OSM_BASE},
        "elements": [{"type": "node", "id": i, "lat": 43.5, "lon": -80.2,
                      "tags": {"natural": "tree"}}
                     for i in range(config.OSM_MIN_ELEMENTS + 1)]}


class FakeResponse:
    def __init__(self, payload=None, status=200):
        self.payload = payload
        self.status_code = status

    def raise_for_status(self):
        if self.status_code >= 400:
            raise requests.HTTPError(f"{self.status_code} Server Error")

    def json(self):
        return self.payload


def _post(by_host):
    """A requests.post that answers per hostname from ``by_host``."""
    calls = []

    def post(url, data=None, headers=None, timeout=None):
        calls.append(url)
        for host, reply in by_host.items():
            if host in url:
                if isinstance(reply, Exception):
                    raise reply
                return reply
        raise requests.ConnectionError("no route")

    post.calls = calls
    return post


@pytest.fixture
def paths(tmp_path, monkeypatch):
    """Point the cache + sidecar at a temp dir, and never really sleep."""
    monkeypatch.setattr(config, "OSM_CACHE_PATH", str(tmp_path / "osm-trees.json"))
    monkeypatch.setattr(config, "OSM_FETCH_PATH", str(tmp_path / ".osm-fetch.json"))
    monkeypatch.setattr(compare.time, "sleep", lambda _s: None)
    return tmp_path


def test_a_dead_primary_falls_through_to_the_next_mirror(paths, monkeypatch):
    # The whole point: one sick instance is not an outage.
    monkeypatch.setattr(config, "OVERPASS_URLS",
                        ("https://a.example/api", "https://b.example/api"))
    post = _post({"a.example": FakeResponse(status=504),
                  "b.example": FakeResponse(GOOD)})
    monkeypatch.setattr(compare.requests, "post", post)

    data, _date, from_cache = compare._load_osm()
    assert from_cache is False
    assert len(data["elements"]) == len(GOOD["elements"])
    assert [u.split("/")[2] for u in post.calls] == ["a.example", "b.example"]


def test_the_mirror_list_is_retried_before_giving_up(paths, monkeypatch):
    # A 504 is load shedding, and load sheds. Trying the list once was what
    # turned a transient failure into a fortnight of stale data.
    monkeypatch.setattr(config, "OVERPASS_URLS", ("https://a.example/api",))
    monkeypatch.setattr(config, "OVERPASS_ROUNDS", 3)
    replies = [FakeResponse(status=504), FakeResponse(status=504),
               FakeResponse(GOOD)]

    def post(url, data=None, headers=None, timeout=None):
        return replies.pop(0)

    monkeypatch.setattr(compare.requests, "post", post)
    _data, _date, from_cache = compare._load_osm()
    assert from_cache is False
    assert replies == [], "every round should have been used"


def test_an_implausibly_small_reply_is_not_accepted(paths, monkeypatch):
    # overpass.osm.ch serves a Switzerland extract and answers a Guelph bbox
    # with HTTP 200 and zero elements. Believing it would report every City
    # stop as missing from OSM -- 648 fictional gaps.
    monkeypatch.setattr(config, "OVERPASS_URLS",
                        ("https://regional.example/api", "https://good.example/api"))
    post = _post({"regional.example": FakeResponse({"elements": []}),
                  "good.example": FakeResponse(GOOD)})
    monkeypatch.setattr(compare.requests, "post", post)

    data, _date, from_cache = compare._load_osm()
    assert from_cache is False
    assert len(data["elements"]) == len(GOOD["elements"])


def test_a_reply_from_months_old_data_is_not_accepted(paths, monkeypatch):
    # overpass.private.coffee answered the Toronto sibling on 2026-09-29 in
    # full, with OSM as of 2026-05-06; its page showed May as this week.
    monkeypatch.setattr(config, "OVERPASS_URLS",
                        ("https://lagging.example/api", "https://good.example/api"))
    stale = {**GOOD, "osm3s": {"timestamp_osm_base": "2026-05-06T03:25:00Z"}}
    post = _post({"lagging.example": FakeResponse(stale),
                  "good.example": FakeResponse(GOOD)})
    monkeypatch.setattr(compare.requests, "post", post)

    _data, osm_date, from_cache = compare._load_osm()
    assert from_cache is False
    assert osm_date.startswith(OSM_BASE[:10])
    assert [u.split("/")[2] for u in post.calls] == ["lagging.example", "good.example"]


def test_a_good_fetch_records_when_and_where_it_came_from(paths, monkeypatch):
    monkeypatch.setattr(config, "OVERPASS_URLS", ("https://good.example/api",))
    monkeypatch.setattr(compare.requests, "post",
                        _post({"good.example": FakeResponse(GOOD)}))

    _data, osm_date, _from_cache = compare._load_osm()
    with open(config.OSM_FETCH_PATH, encoding="utf-8") as f:
        sidecar = json.load(f)
    # The date the page prints is the OSM database timestamp the mirror states
    # about its own reply, NOT the day the fetch happened: a mirror can answer
    # in two seconds with data from last week, and "today" would hide that.
    assert osm_date == f"{OSM_BASE[:10]} {OSM_BASE[11:16]}Z"
    assert sidecar["mirror"] == "good.example"
    assert sidecar["timestamp_osm_base"] == OSM_BASE
    assert sidecar["osm_base"] == osm_date


def test_the_cache_is_used_only_after_every_mirror_fails(paths, monkeypatch):
    with open(config.OSM_CACHE_PATH, "w", encoding="utf-8") as f:
        json.dump(GOOD, f)
    with open(config.OSM_FETCH_PATH, "w", encoding="utf-8") as f:
        json.dump({"fetched": "2026-08-03", "mirror": "old.example"}, f)

    monkeypatch.setattr(config, "OVERPASS_URLS",
                        ("https://a.example/api", "https://b.example/api"))
    monkeypatch.setattr(config, "OVERPASS_ROUNDS", 2)
    post = _post({"a.example": FakeResponse(status=504),
                  "b.example": FakeResponse(status=504)})
    monkeypatch.setattr(compare.requests, "post", post)
    monkeypatch.setattr(net, "wait_for_link", lambda wait=True: None)

    _data, fetched, from_cache = compare._load_osm()
    assert len(post.calls) == 4, "both mirrors, both rounds"
    # The date carried out is the cache's own, not today's -- that value is what
    # the gap page prints, and printing today's would be the original lie.
    assert (fetched, from_cache) == ("2026-08-03", True)


def test_with_no_cache_and_no_mirror_the_run_is_told_to_fail(paths, monkeypatch):
    monkeypatch.setattr(config, "OVERPASS_URLS", ("https://a.example/api",))
    monkeypatch.setattr(config, "OVERPASS_ROUNDS", 1)
    monkeypatch.setattr(compare.requests, "post",
                        _post({"a.example": FakeResponse(status=504)}))
    monkeypatch.setattr(net, "wait_for_link", lambda wait=True: None)

    with pytest.raises(compare.OsmUnavailable):
        compare._load_osm()


def test_being_offline_is_not_reported_as_an_overpass_outage(paths, monkeypatch):
    # No link means the fetch never happened; that is a 75 elsewhere in the
    # pipeline, not a broken Overpass, and it must not be confused with one.
    with open(config.OSM_CACHE_PATH, "w", encoding="utf-8") as f:
        json.dump(GOOD, f)
    monkeypatch.setattr(config, "OVERPASS_URLS", ("https://a.example/api",))
    monkeypatch.setattr(config, "OVERPASS_ROUNDS", 1)
    monkeypatch.setattr(compare.requests, "post",
                        _post({"a.example": requests.ConnectionError("offline")}))

    def no_link(wait=True):
        raise net.LinkUnavailable("no usable link")

    monkeypatch.setattr(net, "wait_for_link", no_link)
    _data, _fetched, from_cache = compare._load_osm()
    assert from_cache is True


def test_a_mirror_without_an_osm3s_block_still_dates_the_page(paths, monkeypatch):
    monkeypatch.setattr(config, "OVERPASS_URLS", ("https://terse.example/api",))
    terse = {"elements": GOOD["elements"]}
    monkeypatch.setattr(compare.requests, "post",
                        _post({"terse.example": FakeResponse(terse)}))
    _data, osm_date, _from_cache = compare._load_osm()
    assert osm_date.startswith("20")


def test_the_cache_date_falls_back_to_the_file_mtime(paths, monkeypatch):
    # A cache written before the sidecar existed still has to date itself.
    with open(config.OSM_CACHE_PATH, "w", encoding="utf-8") as f:
        json.dump(GOOD, f)
    os.utime(config.OSM_CACHE_PATH, (1754247600, 1754247600))  # 2026-08-03 local
    assert compare._cache_date().startswith("20")


# --- what the reader sees ---------------------------------------------------

def test_a_stale_comparison_says_so_on_the_page():
    note = site._osm_note({"osm_stale": True, "osm_date": "2026-08-03"})
    assert "2026-08-03" in note and "class=\"stale\"" in note


def test_a_fresh_comparison_adds_nothing():
    assert site._osm_note({"osm_stale": False, "osm_date": "2026-08-17"}) == ""


# --- matching ---------------------------------------------------------------
#
# Positions are built as metre offsets from one point, so each test reads as
# the street it describes.

BASE_LAT, BASE_LON = 43.5448, -80.2482


def _at(north_m=0.0, east_m=0.0):
    lat = BASE_LAT + north_m / 111_195
    lon = BASE_LON + east_m / (111_195 * 0.7248)  # cos(43.54 deg)
    return lat, lon


def _city(asset="FT1", north_m=0, east_m=0, genus="Acer",
          species="Acer platanoides", dbh=30):
    lat, lon = _at(north_m, east_m)
    t = {"city_asset_id": asset, "lat": lat, "lon": lon,
         "address": "10 Wyndham St N"}
    if genus:
        t["genus"] = genus
    if species:
        t["species"] = species
    if dbh:
        t["dbh_cm"] = dbh
        t["circumference"] = round(dbh * 3.14159 / 100, 2)
    return t


def _node(oid, north_m=0, east_m=0, **tags):
    lat, lon = _at(north_m, east_m)
    return {"type": "node", "id": oid, "lat": lat, "lon": lon,
            "tags": {"natural": "tree", **tags}}


def _osm(*nodes):
    return compare._osm_trees({"elements": list(nodes)})


def test_a_tree_within_match_m_is_matched():
    m = compare.match([_city()], _osm(_node(1, east_m=4)))
    assert list(m) == [0] and abs(m[0][1] - 4) < 0.1


def test_a_tree_beyond_match_m_is_not():
    assert compare.match([_city()], _osm(_node(1, east_m=6))) == {}


def test_two_city_trees_never_share_one_osm_tree():
    # Street trees 6 m apart, one OSM node between them nearer the second: it
    # goes to the second, and the first is missing -- not both matched.
    city = [_city("A"), _city("B", east_m=6)]
    m = compare.match(city, _osm(_node(1, east_m=4)))
    assert m == {1: (0, pytest.approx(2, abs=0.1))}


def test_nearest_first_beats_first_come():
    # A is 3 m from node 1; B is 1 m from it and 4 m from node 2. Taking City
    # trees in list order would hand node 1 to A. Nearest-first gives it to B,
    # whose 1 m pair is the one that is surely real; A's only other candidate
    # is 8 m off, so A is missing -- the audit's rule, kept so the counts agree.
    city = [_city("A", east_m=-2), _city("B", east_m=2)]
    m = compare.match(city, _osm(_node(1, east_m=1), _node(2, east_m=6)))
    assert m == {1: (0, pytest.approx(1, abs=0.1))}


def test_matches_reach_across_grid_cells():
    # The hash grid is MATCH_M wide; a pair straddling a grid line must still
    # be found.
    city = [_city(east_m=0.01 + i * 100) for i in range(5)]
    osm = _osm(*[_node(i, east_m=0.01 + i * 100 + 4.9) for i in range(5)])
    assert len(compare.match(city, osm)) == 5


def _analyse(city, *nodes):
    cells, trees, counts = compare.analyse(city, _osm(*nodes))
    return cells, trees, counts


def test_a_matched_bare_tree_needs_species_and_circumference():
    cells, trees, counts = _analyse([_city()], _node(7, east_m=1))
    (rows,) = trees.values()
    row = dict(zip(compare.TREE_FIELDS, rows[0]))
    assert row["status"] == "matched" and row["osm"] == 7
    assert row["need"] == ["species", "circumference"]
    assert counts["nospecies"] == 1 and counts["nogirth"] == 1


def test_a_tree_osm_already_names_needs_nothing_from_its_name():
    for tag in ("genus", "species", "taxon"):
        _c, trees, counts = _analyse([_city()], _node(7, **{tag: "Acer"}))
        assert counts["nospecies"] == 0, tag
        row = dict(zip(compare.TREE_FIELDS, trees[next(iter(trees))][0]))
        assert row["need"] == ["circumference"]


def test_a_fully_tagged_match_is_no_row_at_all():
    _c, trees, counts = _analyse(
        [_city()], _node(7, genus="Acer", circumference="0.94"))
    assert trees == {} and counts["matched"] == 1


def test_the_city_offers_only_what_it_has():
    # No genus in the City row: nothing to offer for species. No DBH: nothing
    # for circumference.
    _c, trees, counts = _analyse([_city(genus=None, species=None, dbh=None)],
                                 _node(7))
    assert trees == {} and counts["nospecies"] == counts["nogirth"] == 0


def test_cell_status_thresholds():
    n = config.CELL_MIN_CITY
    assert compare.cell_status(n - 1, 0, 0) == "sparse"
    assert compare.cell_status(n, 0, 0) == "empty_cell"
    # Matched share decides thin, not the OSM count: a woodlot's worth of OSM
    # trees in the cell does not make its street trees mapped.
    assert compare.cell_status(n, 10 * n, 0) == "thin_cell"
    half = config.THIN_SHARE * 20
    assert compare.cell_status(20, 20, half - 1) == "thin_cell"
    assert compare.cell_status(20, 20, half) == "covered"


def test_an_empty_street_is_an_empty_cell_and_every_tree_is_listed():
    n = config.CELL_MIN_CITY
    city = [_city(f"T{i}", east_m=i * 8) for i in range(n)]
    cells, trees, counts = _analyse(city)
    # Eight-metre spacing over 80 m may straddle a cell edge; count across all.
    assert counts["missing"] == n
    assert sum(len(r) for r in trees.values()) == n
    assert all(c["osm"] == 0 for c in cells.values())


def test_a_woodlot_with_no_city_tree_is_not_a_cell():
    cells, _t, _c = _analyse([_city()], *[_node(i, north_m=1000 + i) for i in range(30)])
    assert len(cells) == 1 and next(iter(cells.values()))["osm"] == 0


def test_cells_are_labelled_by_their_commonest_street():
    city = [_city("A"), _city("B", east_m=8), _city("C", east_m=16)]
    city[2]["address"] = "3 Quebec St"
    cells, _t, _c = _analyse(city)
    labels = {c["label"] for c in cells.values()}
    assert "Wyndham St N" in labels


def test_cell_ids_round_trip_to_bounds_that_hold_the_point():
    lat, lon = _at(123, -456)
    key = compare.cell_of({"lat": lat, "lon": lon})
    w, s, e, n = compare.cell_bounds(key)
    assert w <= lon < e and s <= lat < n


def test_ways_are_not_trees():
    trees = compare._osm_trees({"elements": [
        {"type": "way", "id": 1, "center": {"lat": 43.5, "lon": -80.2},
         "tags": {"natural": "tree"}}]})
    assert trees == []


def test_the_query_asks_for_tree_nodes_in_the_bbox():
    q = compare._overpass_query()
    assert 'node["natural"="tree"]' in q and "out;" in q
    s, w, n, e = config.GUELPH_BBOX
    assert f"({s},{w},{n},{e})" in q
