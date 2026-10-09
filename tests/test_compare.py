"""Unit tests for the OSM comparison: the Overpass fetch, and the matching.

The Overpass half is carried over from the parks sibling, where it pins the
failure that actually happened: on 2026-08-10 and again on 2026-08-17
overpass-api.de answered 504, the single attempt gave up, the run silently
compared against a fortnight-old cache and reported success. The matching half
is this layer's own: by ref, then by distance, and never across agencies.
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
                      "tags": {"highway": "bus_stop"}}
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
    monkeypatch.setattr(config, "OSM_CACHE_PATH", str(tmp_path / "osm-bus-stops.json"))
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
# Positions are built as metre offsets from one City stop, so each test reads
# as the street layout it describes.

BASE_LAT, BASE_LON = 43.5448, -80.2482


def _at(north_m=0.0, east_m=0.0):
    lat = BASE_LAT + north_m / 111_195
    lon = BASE_LON + east_m / (111_195 * 0.7248)  # cos(43.54 deg)
    return lat, lon


def _city(ref, name="Wyndham at Fountain southbound", north_m=0, east_m=0,
          fixed_route=True):
    lat, lon = _at(north_m, east_m)
    return {"ref": ref, "name": name, "fixed_route": fixed_route,
            "on_demand": None if fixed_route else "Monday-Saturday",
            "lat": lat, "lon": lon}


def _node(oid, north_m=0, east_m=0, **tags):
    lat, lon = _at(north_m, east_m)
    return {"type": "node", "id": oid, "lat": lat, "lon": lon,
            "tags": {"highway": "bus_stop", **tags}}


def _run(city, elements):
    gaps, counts = compare._match(city, compare._osm_stops({"elements": elements}))
    return {(g["properties"]["ref"], g["properties"]["status"]) for g in gaps}, counts


def test_a_stop_with_its_ref_and_name_is_not_a_gap():
    gaps, counts = _run([_city(801)],
                        [_node(1, 2, name="Wyndham at Fountain southbound",
                               ref="801", network="Guelph Transit")])
    assert gaps == set() and sum(counts.values()) == 0


def test_names_match_through_case_and_punctuation_only():
    assert compare._norm("Gordon & Nottingham") == compare._norm("gordon and nottingham")
    # Dropping the direction is a real difference, not noise.
    assert compare._norm("Gordon at Nottingham") != \
        compare._norm("Gordon at Nottingham southbound")


def test_ref_match_reports_mismatch_and_unnamed():
    gaps, _ = _run([_city(801), _city(297, "Wyndham at Fountain northbound", east_m=20)],
                   [_node(1, ref="801", name="Wyndham at Fountain"),
                    _node(2, east_m=20, ref="297")])
    assert gaps == {(801, "mismatch"), (297, "unnamed")}


def test_a_ref_matched_stop_far_away_is_far():
    gaps, _ = _run([_city(501)], [_node(1, north_m=120, ref="501",
                                        name="Wyndham at Fountain southbound")])
    assert gaps == {(501, "far")}


def test_two_osm_stops_far_apart_with_one_ref_are_a_duplicate():
    # Seen downtown on 2026-10-08: ref 801 on two nodes 59 m apart.
    gaps, _ = _run([_city(801)],
                   [_node(1, 1, ref="801", name="Wyndham at Fountain southbound"),
                    _node(2, 60, ref="801", name="Wyndham at Fountain southbound")])
    assert gaps == {(801, "duplicate")}


def test_a_platform_way_beside_its_node_is_not_a_duplicate():
    way = {"type": "way", "id": 9, "center": dict(zip(("lat", "lon"), _at(4))),
           "tags": {"public_transport": "platform", "bus": "yes", "ref": "801",
                    "name": "Wyndham at Fountain southbound"}}
    gaps, _ = _run([_city(801)],
                   [_node(1, 1, ref="801", name="Wyndham at Fountain southbound"), way])
    assert gaps == set()


def test_an_unreferenced_osm_stop_goes_to_the_nearest_city_stop_only():
    # Two City stops 20 m apart across the road; one OSM stop with no ref, 5 m
    # from the southbound one. Within NEAR_M of both, it is only the nearer's.
    gaps, counts = _run(
        [_city(801), _city(297, "Wyndham at Fountain northbound", east_m=20)],
        [_node(1, east_m=5)],
    )
    assert gaps == {(801, "noref"), (297, "missing")}
    assert counts["noref"] == 1 and counts["missing"] == 1


def test_an_unreferenced_stop_beyond_near_m_is_not_claimed():
    gaps, _ = _run([_city(801)], [_node(1, north_m=config.NEAR_M + 5)])
    assert gaps == {(801, "missing")}


def test_another_agencys_stop_is_never_matched():
    # GO Transit's platforms share Guelph Central Station with Guelph Transit's.
    gaps, counts = _run([_city(5833, "Guelph Central Station Platform 1")],
                        [_node(1, 2, network="GO Transit", operator="Metrolinx",
                               name="Guelph Central GO")])
    assert gaps == {(5833, "missing")}
    assert counts["osm_only"] == 0


def test_an_on_demand_only_stop_is_not_called_missing():
    gaps, counts = _run([_city(9001, fixed_route=False)], [])
    assert gaps == {(9001, "ondemand")}
    assert counts["missing"] == 0


def test_an_osm_ref_the_city_does_not_list_is_osm_only():
    gaps, counts = _run([_city(801)],
                        [_node(1, ref="801", name="Wyndham at Fountain southbound",
                               network="Guelph Transit"),
                         _node(2, north_m=300, ref="4242", network="Guelph Transit")])
    assert gaps == {("4242", "osm_only")}
    assert counts["osm_only"] == 1


def test_an_untagged_stops_ref_is_matched_but_never_called_osm_only():
    # Most Guelph stops with a ref carry a network too, but a bare ref is still
    # read as a Guelph Transit stop ID for matching. It is not read as a stale
    # one: an untagged stop's numbering may be another agency's.
    gaps, _ = _run([_city(801)],
                   [_node(1, ref="801", name="Wyndham at Fountain southbound"),
                    _node(2, north_m=300, ref="4242")])
    assert gaps == set()


def test_a_multi_valued_ref_matches_each_stop():
    gaps, _ = _run([_city(801), _city(297, "Wyndham at Fountain northbound")],
                   [_node(1, ref="801;297")])
    assert gaps == {(801, "unnamed"), (297, "unnamed")}


def test_ways_are_placed_at_their_center():
    stops = compare._osm_stops({"elements": [
        {"type": "way", "id": 7, "center": {"lat": 43.5, "lon": -80.2},
         "tags": {"public_transport": "platform", "bus": "yes"}},
        {"type": "relation", "id": 8, "tags": {}},   # no position: skipped
    ]})
    assert [(s["otype"], s["lat"], s["lon"]) for s in stops] == [("way", 43.5, -80.2)]


def test_the_query_asks_for_both_tagging_schemes_with_a_position():
    q = compare._overpass_query()
    assert 'nwr["highway"="bus_stop"]' in q
    assert 'nwr["public_transport"="platform"]["bus"="yes"]' in q
    assert q.rstrip().endswith("out center tags;")
