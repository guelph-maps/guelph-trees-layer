// Gap-review page: City raster layer + gap markers, driven by a side table.

(function () {
  "use strict";

  var COLORS = {
    missing: "#7048e8", noref: "#2b8a3e", far: "#c92a2a",
    duplicate: "#d6336c", mismatch: "#1c7ed6", unnamed: "#e8590c",
    osm_only: "#495057", ondemand: "#868e96",
  };
  // What a mapper does about each status; shown in the popup.
  var ACTIONS = {
    missing: "No OSM stop has this ref. Add the stop.",
    noref: "The OSM stop here has no ref. Add ref=",
    far: "The OSM stop with this ref is far from the City's point.",
    duplicate: "Two OSM stops far apart carry this ref.",
    mismatch: "The OSM name differs from the City's.",
    unnamed: "The OSM stop has no name.",
    osm_only: "OSM has this Guelph Transit ref; the City does not list it.",
    ondemand: "On-demand pick-up point only; not in OSM.",
  };
  var map;
  var features = [];
  var markers = [];      // CircleMarker per feature, indexed like features
  var highlight = null;  // ring + link line of the currently selected gap
  var selectedRow = null;
  var selectedIndex = null;  // feature index of the current selection

  function init() {
    var el = document.getElementById("gap-map");
    if (!el || typeof L === "undefined" || !el.dataset.geojson) {
      return;
    }
    map = L.map(el).setView([43.5448, -80.2482], 13);
    L.tileLayer("https://tile.openstreetmap.org/{z}/{x}/{y}.png", {
      maxZoom: 19,
      attribution:
        '&copy; <a href="https://www.openstreetmap.org/copyright">' +
        "OpenStreetMap</a> contributors",
    }).addTo(map);
    if (el.dataset.raster) {
      L.tileLayer(el.dataset.raster, {
        minZoom: 13, maxNativeZoom: 17, maxZoom: 19, opacity: 0.85,
      }).addTo(map);
    }

    fetch(el.dataset.geojson)
      .then(function (r) { return r.json(); })
      .then(render)
      .catch(function (e) {
        el.innerHTML = '<p style="padding:1rem">Could not load gaps.geojson.</p>';
        console.error(e);
      });
  }

  function render(data) {
    features = data.features || [];

    features.forEach(function (f, i) {
      f.properties._i = i;
      var c = f.geometry.coordinates;
      f._center = [c[1], c[0]];
      var m = L.circleMarker(f._center, {
        radius: 7,
        color: "#fff",
        weight: 2,
        fillColor: COLORS[f.properties.status] || "#888",
        fillOpacity: 0.95,
      });
      m.bindPopup(popupHtml(f));
      m.on("click", function () { select(i, false); });
      m.addTo(map);
      markers[i] = m;
    });

    buildTable();
    wireControls();
  }

  // Where to send the user for a feature: the openstreetmap.org view and the
  // iD editor. Points at the existing OSM stop when there is one, else at the
  // City's point.
  function osmTargets(f) {
    var p = f.properties;
    var m = p.osm_url && p.osm_url.match(/(node|way|relation)\/(\d+)/);
    if (m) {
      return {
        osm: p.osm_url,
        id: "https://www.openstreetmap.org/edit?" + m[1] + "=" + m[2],
      };
    }
    var c = f._center;
    var ll = c[0].toFixed(5) + "/" + c[1].toFixed(5);
    return {
      osm: "https://www.openstreetmap.org/#map=19/" + ll,
      id: "https://www.openstreetmap.org/edit#map=19/" + ll,
    };
  }

  // "OSM · iD" link pair shared by the table and the popups.
  function osmPairHtml(f) {
    var t = osmTargets(f);
    return '<a href="' + t.osm + '" target="_blank" rel="noopener" ' +
      'title="View on openstreetmap.org (hotkey: O)">OSM</a> &middot; ' +
      '<a href="' + t.id + '" target="_blank" rel="noopener" ' +
      'title="Open in the iD editor (hotkey: I)">iD</a>';
  }

  function popupHtml(f) {
    var p = f.properties;
    var h = "<strong>" + esc(p.ref) + "</strong> " +
      esc(p.name || p.osm_name || "(unnamed)") + "<br>" + p.status + ": " +
      esc(ACTIONS[p.status] || "");
    if (p.status === "noref") { h += esc(p.ref); }
    if (p.osm_name && p.status !== "osm_only") {
      h += "<br>OSM name: " + esc(p.osm_name);
    }
    if (p.distance_m != null) {
      h += "<br>City &harr; OSM: " + p.distance_m + " m";
    }
    if (p.on_demand) { h += "<br>On-demand service: " + esc(p.on_demand); }
    h += "<br>" + osmPairHtml(f);
    if (p.other_url) {
      h += ' &middot; <a href="' + p.other_url +
        '" target="_blank" rel="noopener">other stop with this ref</a>';
    }
    return h;
  }

  function refKey(p) {
    var n = parseInt(p.ref, 10);
    return isNaN(n) ? Infinity : n;
  }

  function buildTable() {
    var tbody = document.querySelector("#gap-table tbody");
    var rows = features.slice().sort(function (a, b) {
      return refKey(a.properties) - refKey(b.properties);
    });
    tbody.innerHTML = rows.map(function (f) {
      var p = f.properties;
      var name = p.name || (p.osm_name ? "(OSM) " + p.osm_name : "(unnamed)");
      var osm = (p.osm_name && p.status !== "osm_only"
        ? '<span class="osm-name">' + esc(p.osm_name) + "</span>" : "") +
        '<span class="osm-links">' + osmPairHtml(f) + "</span>";
      var search = (String(p.ref) + " " + (p.name || "") + " " +
        (p.osm_name || "")).toLowerCase();
      return '<tr data-i="' + p._i + '" data-status="' + p.status +
        '" data-search="' + esc(search) + '">' +
        '<td class="ref">' + esc(p.ref) + "</td>" +
        "<td>" + esc(name) + "</td>" +
        '<td class="status-' + p.status + '">' + p.status +
        (p.distance_m != null && p.status !== "mismatch" && p.status !== "unnamed"
          ? ' <span class="dist">' + p.distance_m + "&nbsp;m</span>" : "") +
        "</td>" +
        "<td>" + osm + "</td></tr>";
    }).join("");

    tbody.addEventListener("click", function (ev) {
      if (ev.target.closest("a")) { return; }  // let OSM links work
      var tr = ev.target.closest("tr");
      if (tr) { select(parseInt(tr.dataset.i, 10), true); }
    });
  }

  // Select a gap: ring it, join it to its OSM stop, fit the map, open its
  // popup, mark its row.
  function select(i, fromTable) {
    var f = features[i];
    if (!f) { return; }
    selectedIndex = i;
    var p = f.properties;

    if (highlight) { map.removeLayer(highlight); }
    var layers = [L.circleMarker(f._center, {
      radius: 13, color: "#0a7", weight: 3, fill: false,
    })];
    if (p.osm_lat != null && p.osm_lon != null) {
      var osm = [p.osm_lat, p.osm_lon];
      layers.push(L.polyline([f._center, osm], {
        color: "#0a7", weight: 2, dashArray: "4 4",
      }));
      layers.push(L.circleMarker(osm, {
        radius: 5, color: "#0a7", weight: 2, fillColor: "#fff", fillOpacity: 1,
      }));
    }
    highlight = L.featureGroup(layers).addTo(map);
    var bounds = highlight.getBounds();
    if (bounds.getNorthEast().equals(bounds.getSouthWest())) {
      map.setView(f._center, 18);
    } else {
      map.fitBounds(bounds, { maxZoom: 18, padding: [60, 60] });
    }
    if (markers[i]) { markers[i].openPopup(); }

    if (selectedRow) { selectedRow.classList.remove("selected"); }
    var tr = document.querySelector('#gap-table tr[data-i="' + i + '"]');
    if (tr) {
      tr.classList.add("selected");
      selectedRow = tr;
      if (!fromTable) { tr.scrollIntoView({ block: "center" }); }
    }
  }

  function wireControls() {
    var boxes = {};
    Array.prototype.forEach.call(
      document.querySelectorAll(".controls input[data-status]"),
      function (b) { boxes[b.dataset.status] = b; }
    );
    var filter = document.getElementById("filter");

    function statusOn(status) {
      var b = boxes[status];
      return b ? b.checked : true;
    }

    function apply() {
      var q = filter.value.trim().toLowerCase();

      var rows = document.querySelectorAll("#gap-table tbody tr");
      Array.prototype.forEach.call(rows, function (tr) {
        var hit = statusOn(tr.dataset.status) &&
          (!q || tr.dataset.search.indexOf(q) > -1);
        tr.style.display = hit ? "" : "none";
        var m = markers[parseInt(tr.dataset.i, 10)];
        if (hit && !map.hasLayer(m)) { map.addLayer(m); }
        else if (!hit && map.hasLayer(m)) { map.removeLayer(m); }
      });
    }

    Object.keys(boxes).forEach(function (k) {
      boxes[k].addEventListener("change", apply);
    });
    filter.addEventListener("input", apply);
    apply();

    // O / I open the selected gap on openstreetmap.org / in the iD editor.
    document.addEventListener("keydown", function (ev) {
      if (ev.ctrlKey || ev.metaKey || ev.altKey) { return; }
      if (/^(input|textarea|select)$/i.test(ev.target.tagName)) { return; }
      var key = ev.key.toLowerCase();
      if (key !== "o" && key !== "i") { return; }
      if (selectedIndex == null) { return; }
      var t = osmTargets(features[selectedIndex]);
      ev.preventDefault();
      window.open(key === "o" ? t.osm : t.id, "_blank", "noopener");
    });
  }

  function esc(s) {
    return String(s == null ? "" : s)
      .replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;");
  }

  document.addEventListener("DOMContentLoaded", init);
})();
