// Gap-review page for trees. The City lists tens of thousands of trees, far
// too many rows for one table, so the page leads with square cells
// (cells.geojson) and drills into one cell at a time; a cell's trees come from
// trees.json, fetched once on the first drill-down. At most one cell's trees
// are ever on the map.

(function () {
  "use strict";

  // Cell fill by status: the worse the coverage, the stronger the fill.
  var CELL_STYLE = {
    empty_cell: { color: "#c92a2a", fillOpacity: 0.45 },
    thin_cell: { color: "#e8590c", fillOpacity: 0.3 },
    covered: { color: "#2b8a3e", fillOpacity: 0.1 },
    sparse: { color: "#868e96", fillOpacity: 0.06 },
  };
  var CELL_LABEL = {
    empty_cell: "empty", thin_cell: "thin", covered: "covered", sparse: "sparse",
  };
  // Tree markers: missing is the same purple as on the other gap pages.
  var TREE_COLORS = { missing: "#7048e8", species: "#1c7ed6", girth: "#0c8599" };
  var KIND_ORDER = { missing: 0, species: 1, girth: 2 };
  var NEED_LABEL = { species: "species", circumference: "girth" };
  var SELECTED = "#0a7";
  var JOSM = "http://127.0.0.1:8111/load_and_zoom";
  var PAD_M = 30;  // JOSM box: a cell's square, or a tree's point, padded by this

  var map;
  var matchM = "";           // MATCH_M, for wording only
  var cells = [];            // cells.geojson features, most missing first
  var cellById = {};         // cell id -> feature (with _bounds, _rect)
  var currentCell = null;    // the cell drilled into, or null in list mode
  var drill = 0;             // bumped per selection; a late trees.json bails
  var treesUrl = null;
  var treesPromise = null;   // trees.json, fetched once (retried on failure)
  var F = null;              // field name -> index into a trees.json row
  var trees = [];            // the current cell's trees, as objects
  var treeLayer = null;      // featureGroup of the current cell's markers
  var treeMarkers = [];      // CircleMarker per tree, indexed like trees
  var highlight = null;      // ring around the selected tree
  var selectedTree = null;   // index into trees
  var selectedCellRow = null;
  var selectedTreeRow = null;
  var applyFilter = function () {};

  function init() {
    var el = document.getElementById("gap-map");
    if (!el || typeof L === "undefined" || !el.dataset.cells) {
      return;
    }
    treesUrl = el.dataset.trees;
    matchM = el.dataset.matchM || "";
    // Canvas, not SVG: a thousand cell rectangles stay cheap to pan.
    map = L.map(el, { preferCanvas: true, maxZoom: 20 })
      .setView([43.5448, -80.2482], 13);
    L.tileLayer("https://tile.openstreetmap.org/{z}/{x}/{y}.png", {
      maxNativeZoom: 19,
      maxZoom: 20,
      attribution:
        '&copy; <a href="https://www.openstreetmap.org/copyright">' +
        "OpenStreetMap</a> contributors",
    }).addTo(map);
    if (el.dataset.raster) {
      L.tileLayer(el.dataset.raster, {
        minZoom: 13, maxNativeZoom: 18, maxZoom: 20, opacity: 0.85,
      }).addTo(map);
    }

    fetch(el.dataset.cells)
      .then(function (r) { return r.json(); })
      .then(render)
      .catch(function (e) {
        el.innerHTML = '<p style="padding:1rem">Could not load cells.geojson.</p>';
        console.error(e);
      });
  }

  function render(data) {
    cells = (data.features || []).slice().sort(function (a, b) {
      return (b.properties.missing - a.properties.missing) ||
        (b.properties.city - a.properties.city);
    });

    var all = null;
    cells.forEach(function (f) {
      var p = f.properties;
      f._bounds = ringBounds(f.geometry.coordinates[0]);
      f._rect = L.rectangle(f._bounds, cellStyle(p.status));
      f._rect.bindTooltip(
        esc(p.label || p.cell) + " &middot; " + fmt(p.missing) + " of " +
        fmt(p.city) + " missing", { sticky: true });
      f._rect.on("click", function () { goCell(p.cell); });
      cellById[p.cell] = f;
      all = all ? all.extend(f._bounds) : L.latLngBounds(f._bounds.getSouthWest(),
        f._bounds.getNorthEast());
    });

    buildCellTable();
    wireControls();
    window.addEventListener("hashchange", onHash);
    if (!onHash() && all) {
      map.fitBounds(all, { padding: [10, 10] });
    }
  }

  // A cell is a lat/lon rectangle; its bounds are the ring's extremes.
  function ringBounds(ring) {
    var s = Infinity, w = Infinity, n = -Infinity, e = -Infinity;
    ring.forEach(function (c) {
      if (c[0] < w) { w = c[0]; }
      if (c[0] > e) { e = c[0]; }
      if (c[1] < s) { s = c[1]; }
      if (c[1] > n) { n = c[1]; }
    });
    return L.latLngBounds([s, w], [n, e]);
  }

  function cellStyle(status) {
    var s = CELL_STYLE[status] || CELL_STYLE.sparse;
    return {
      color: s.color, weight: 1, opacity: 0.7,
      fillColor: s.color, fillOpacity: s.fillOpacity,
    };
  }

  // The drilled-into cell: a dark outline and no fill, so its trees and the
  // City raster underneath stay readable.
  function styleCell(f, selected) {
    f._rect.setStyle(selected
      ? { color: "#1a1a1a", weight: 3, opacity: 1, fillOpacity: 0 }
      : cellStyle(f.properties.status));
  }

  // --- cell list ---

  function buildCellTable() {
    var tbody = document.querySelector("#cell-table tbody");
    tbody.innerHTML = cells.map(function (f) {
      var p = f.properties;
      var search = ((p.label || "") + " " + p.cell).toLowerCase();
      return '<tr data-cell="' + esc(p.cell) + '" data-status="' + esc(p.status) +
        '" data-search="' + esc(search) + '">' +
        '<td class="cell-name">' +
        (p.label ? '<span class="cell-label">' + esc(p.label) + "</span> " : "") +
        '<span class="cell-id">' + esc(p.cell) + "</span></td>" +
        '<td class="num">' + fmt(p.city) + "</td>" +
        '<td class="num">' + fmt(p.osm) + "</td>" +
        '<td class="num strong">' + fmt(p.missing) + "</td>" +
        '<td class="num">' + fmt(p.nospecies) + "</td>" +
        '<td class="status-' + esc(p.status) + '">' +
        esc(CELL_LABEL[p.status] || p.status) + "</td>" +
        '<td class="osm-links">' + josmLinkHtml(cellJosm(f)) + "</td></tr>";
    }).join("");

    tbody.addEventListener("click", function (ev) {
      if (ev.target.closest("a")) { return; }  // the JOSM link opens itself
      var tr = ev.target.closest("tr");
      if (tr) { goCell(tr.dataset.cell); }
    });
  }

  // --- selection, routed through location.hash so a link to a cell works ---

  function goCell(id) {
    if (currentCell && currentCell.properties.cell === id) { return; }
    if (location.hash !== "#" + id) {
      location.hash = id;  // hashchange -> onHash -> selectCell
    } else {
      selectCell(id);
    }
  }

  function goList() {
    if (!currentCell) { return; }
    if (window.history && history.pushState) {
      history.pushState(null, "", location.pathname + location.search);
    }
    showList();
  }

  // Follow the hash: a known cell id drills in, anything else is the list.
  // Returns true when it selected a cell.
  function onHash() {
    var id = decodeURIComponent(location.hash.replace(/^#/, ""));
    if (id && cellById[id]) {
      if (!currentCell || currentCell.properties.cell !== id) { selectCell(id); }
      return true;
    }
    if (currentCell) { showList(); }
    return false;
  }

  function selectCell(id) {
    var f = cellById[id];
    if (!f) { return; }
    var token = ++drill;
    clearTrees();
    if (currentCell) { styleCell(currentCell, false); }
    currentCell = f;
    applyFilter();  // puts back a cell the filter had hidden, styles it
    f._rect.bringToFront();  // outline over its neighbours; trees go on top
    map.fitBounds(f._bounds, { maxZoom: 18, padding: [20, 20] });

    if (selectedCellRow) { selectedCellRow.classList.remove("selected"); }
    selectedCellRow = document.querySelector(
      '#cell-table tr[data-cell="' + cssEsc(id) + '"]');
    if (selectedCellRow) { selectedCellRow.classList.add("selected"); }

    cellHead(f);
    setMode(true);
    setNote("Loading trees\u2026");

    loadTrees().then(function (data) {
      if (token !== drill) { return; }  // the user has moved on
      renderTrees((data.cells && data.cells[id]) || []);
    }, function (e) {
      if (token !== drill) { return; }
      setNote("Could not load trees.json. Pick the cell again to retry.");
      console.error(e);
    });
  }

  function showList() {
    drill++;  // a trees.json still in flight must not draw into the list
    clearTrees();
    var prev = currentCell;
    currentCell = null;
    if (prev) { styleCell(prev, false); }
    setMode(false);
    applyFilter();
    if (selectedCellRow && selectedCellRow.style.display !== "none") {
      selectedCellRow.scrollIntoView({ block: "nearest" });
    }
  }

  function setMode(cellMode) {
    document.getElementById("cells-mode").hidden = cellMode;
    document.getElementById("cell-mode").hidden = !cellMode;
  }

  function cellHead(f) {
    var p = f.properties;
    document.getElementById("cell-title").innerHTML =
      esc(p.label || "Cell " + p.cell) +
      ' <span class="cell-id">' + esc(p.cell) + "</span>";
    document.getElementById("cell-counts").innerHTML =
      "City <strong>" + fmt(p.city) + "</strong> &middot; OSM <strong>" +
      fmt(p.osm) + "</strong> &middot; matched <strong>" + fmt(p.matched) +
      "</strong> &middot; missing <strong>" + fmt(p.missing) +
      "</strong> &middot; no species <strong>" + fmt(p.nospecies) +
      "</strong> &middot; no girth <strong>" + fmt(p.nogirth) + "</strong>" +
      ' &middot; <span class="status-' + esc(p.status) + '">' +
      esc(CELL_LABEL[p.status] || p.status) + "</span>";
  }

  function setNote(text) {
    var note = document.getElementById("tree-note");
    note.hidden = !text;
    note.textContent = text || "";
  }

  // --- one cell's trees ---

  function loadTrees() {
    if (!treesPromise) {
      treesPromise = fetch(treesUrl)
        .then(function (r) {
          if (!r.ok) { throw new Error("trees.json: HTTP " + r.status); }
          return r.json();
        })
        .then(function (data) {
          F = {};
          (data.fields || []).forEach(function (k, i) { F[k] = i; });
          return data;
        });
      treesPromise.catch(function () { treesPromise = null; });
    }
    return treesPromise;
  }

  // A trees.json row as an object, plus what the map needs: position and kind
  // (missing / matched lacking species / matched lacking girth only).
  function treeFromRow(row) {
    var t = {};
    Object.keys(F).forEach(function (k) { t[k] = row[F[k]]; });
    t.need = t.need || [];
    t._ll = [t.lat, t.lon];
    t._kind = t.status === "missing" ? "missing"
      : t.need.indexOf("species") > -1 ? "species" : "girth";
    return t;
  }

  function renderTrees(rows) {
    trees = rows.map(treeFromRow).sort(function (a, b) {
      return (KIND_ORDER[a._kind] - KIND_ORDER[b._kind]) ||
        String(a.address || "").localeCompare(String(b.address || ""));
    });

    treeLayer = L.featureGroup().addTo(map);
    trees.forEach(function (t, j) {
      var m = L.circleMarker(t._ll, {
        radius: 6,
        color: "#fff",
        weight: 2,
        fillColor: TREE_COLORS[t._kind],
        fillOpacity: 0.95,
      });
      m.bindPopup(popupHtml(t));
      m.on("click", function () { selectTree(j, false); });
      treeLayer.addLayer(m);
      treeMarkers[j] = m;
    });

    var tbody = document.querySelector("#tree-table tbody");
    tbody.innerHTML = trees.map(function (t, j) {
      var sci = t.species || t.genus;
      return '<tr data-j="' + j + '">' +
        '<td class="tree-name">' + esc(t.common || sci || "(no species)") +
        (sci && sci !== t.common ? ' <span class="sci">' + esc(sci) + "</span>" : "") +
        (t.address ? '<span class="addr">' + esc(t.address) + "</span>" : "") +
        "</td>" +
        '<td class="num">' + (t.dbh != null ? t.dbh + "&nbsp;cm" : "&ndash;") + "</td>" +
        '<td class="status-' + t._kind + '">' + statusText(t) + "</td>" +
        '<td class="osm-links">' + osmPairHtml(osmTargets(t)) + "</td></tr>";
    }).join("");

    var p = currentCell.properties;
    setNote(trees.length ? null
      : "Nothing to list: every City tree in this cell has an OSM tree" +
        (matchM ? " within " + matchM + " m" : " nearby") +
        (p.missing ? "" : " that already carries species and girth") + ".");
  }

  function clearTrees() {
    if (treeLayer) { map.removeLayer(treeLayer); }
    if (highlight) { map.removeLayer(highlight); }
    treeLayer = null;
    highlight = null;
    trees = [];
    treeMarkers = [];
    selectedTree = null;
    selectedTreeRow = null;
    document.querySelector("#tree-table tbody").innerHTML = "";
    setNote(null);
  }

  function statusText(t) {
    if (t._kind === "missing") { return "missing"; }
    return "add " + t.need.map(function (n) { return NEED_LABEL[n] || n; })
      .join(", ") +
      (t.dist != null ? ' <span class="dist">' + t.dist + "&nbsp;m</span>" : "");
  }

  // OSM circumference is metres: DBH (cm) x pi / 100. Prefer the pipeline's
  // value; derive it here only if a row lacks one.
  function circOf(t) {
    if (t.circ != null) { return Number(t.circ).toFixed(2); }
    if (t.dbh) { return (t.dbh * Math.PI / 100).toFixed(2); }
    return null;
  }

  // Tags the City record could offer: for a matched tree only what its OSM
  // node lacks; for a missing tree, the lot.
  function offerTags(t) {
    var tags = [];
    var wantSpecies = t._kind === "missing" || t.need.indexOf("species") > -1;
    var wantCirc = t._kind === "missing" || t.need.indexOf("circumference") > -1;
    if (t._kind === "missing") { tags.push("natural=tree"); }
    if (wantSpecies && t.genus) { tags.push("genus=" + t.genus); }
    if (wantSpecies && t.species) { tags.push("species=" + t.species); }
    var c = circOf(t);
    if (wantCirc && c) { tags.push("circumference=" + c); }
    return tags;
  }

  function popupHtml(t) {
    var sci = t.species || t.genus;
    var h = "<strong>" + esc(t.common || sci || "(no species)") + "</strong>";
    if (sci && sci !== t.common) { h += " <em>" + esc(sci) + "</em>"; }
    if (t.address) { h += "<br>" + esc(t.address); }
    if (t.dbh != null) {
      var c = circOf(t);
      h += "<br>DBH " + t.dbh + " cm" + (c ? " &rarr; circumference " + c + " m" : "");
    }
    h += "<br>City asset " + esc(t.asset);
    if (t._kind === "missing") {
      h += "<br><strong>missing</strong>: no OSM tree within " +
        esc(matchM || "?") + " m. Map it if imagery shows a crown here.";
    } else {
      h += "<br>OSM node " + esc(t.osm) +
        (t.dist != null ? ", " + t.dist + " m away," : "") + " lacks " +
        t.need.map(function (n) { return NEED_LABEL[n] || n; }).join(" and ") + ".";
    }
    var tags = offerTags(t);
    if (tags.length) {
      h += '<br><span class="offer">City says (verify first):</span> ' +
        tags.map(function (s) { return "<code>" + esc(s) + "</code>"; }).join(" ");
    }
    h += "<br>" + osmPairHtml(osmTargets(t));
    return h;
  }

  // Select a tree: ring it, mark its row; from the table, also zoom and open
  // its popup (a map click opens the popup itself).
  function selectTree(j, fromTable) {
    var t = trees[j];
    if (!t) { return; }
    selectedTree = j;
    if (highlight) { map.removeLayer(highlight); }
    highlight = L.circleMarker(t._ll, {
      radius: 11, color: SELECTED, weight: 3, fill: false, interactive: false,
    }).addTo(map);
    if (fromTable) {
      map.setView(t._ll, Math.max(map.getZoom(), 19));
      treeMarkers[j].openPopup();
    }

    if (selectedTreeRow) { selectedTreeRow.classList.remove("selected"); }
    var tr = document.querySelector('#tree-table tr[data-j="' + j + '"]');
    if (tr) {
      tr.classList.add("selected");
      selectedTreeRow = tr;
      if (!fromTable) { tr.scrollIntoView({ block: "center" }); }
    }
  }

  // --- links ---

  // Where to send the user for a tree: its OSM node when matched, else the
  // City's position, on openstreetmap.org and in the iD editor; and JOSM
  // remote control for the box around the tree, its OSM node selected.
  function osmTargets(t) {
    var josm = josmUrl(t.lat, t.lon, t.lat, t.lon,
      t.osm != null ? "node" + t.osm : null);
    if (t.osm != null) {
      return {
        osm: "https://www.openstreetmap.org/node/" + t.osm,
        id: "https://www.openstreetmap.org/edit?node=" + t.osm,
        josm: josm,
      };
    }
    var m = mapTargets(t.lat, t.lon, 20);
    m.josm = josm;
    return m;
  }

  // JOSM for a whole cell: its square, nothing selected.
  function cellJosm(f) {
    var b = f._bounds;
    return josmUrl(b.getSouth(), b.getWest(), b.getNorth(), b.getEast(), null);
  }

  // JOSM load_and_zoom for [south, west, north, east] padded by PAD_M. Opened
  // as a plain link in one reused tab named "josm", not fetched: browsers
  // gate background requests from a public page to localhost, but not a link
  // the mapper clicks.
  function josmUrl(s, w, n, e, select) {
    var dLat = PAD_M / 111320;
    var dLon = PAD_M / (111320 * Math.cos((s + n) / 2 * Math.PI / 180));
    return JOSM + "?left=" + (w - dLon).toFixed(6) + "&right=" + (e + dLon).toFixed(6) +
      "&top=" + (n + dLat).toFixed(6) + "&bottom=" + (s - dLat).toFixed(6) +
      (select ? "&select=" + select : "") +
      "&changeset_source=" + encodeURIComponent("City of Guelph Trees");
  }

  function josmLinkHtml(url) {
    return '<a href="' + esc(url) + '" class="josm" target="josm" ' +
      'title="Load this area into JOSM via remote control (hotkey: J)">JOSM</a>';
  }

  function mapTargets(lat, lon, z) {
    var ll = z + "/" + Number(lat).toFixed(6) + "/" + Number(lon).toFixed(6);
    return {
      osm: "https://www.openstreetmap.org/#map=" + ll,
      id: "https://www.openstreetmap.org/edit#map=" + ll,
    };
  }

  // "OSM · iD · JOSM" links shared by the table and the popups.
  function osmPairHtml(t) {
    return '<a href="' + t.osm + '" target="_blank" rel="noopener" ' +
      'title="View on openstreetmap.org (hotkey: O)">OSM</a> &middot; ' +
      '<a href="' + t.id + '" target="_blank" rel="noopener" ' +
      'title="Open in the iD editor (hotkey: I)">iD</a> &middot; ' +
      josmLinkHtml(t.josm);
  }

  // --- controls ---

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

    // Rows and rectangles together. The drilled-into cell always stays on
    // the map, whatever the filter says.
    applyFilter = function () {
      var q = filter.value.trim().toLowerCase();
      var rows = document.querySelectorAll("#cell-table tbody tr");
      Array.prototype.forEach.call(rows, function (tr) {
        var f = cellById[tr.dataset.cell];
        var hit = statusOn(tr.dataset.status) &&
          (!q || tr.dataset.search.indexOf(q) > -1);
        tr.style.display = hit ? "" : "none";
        var show = hit || f === currentCell;
        if (show && !map.hasLayer(f._rect)) {
          map.addLayer(f._rect);
          f._rect.bringToBack();  // never above the current cell's trees
        } else if (!show && map.hasLayer(f._rect)) {
          map.removeLayer(f._rect);
        }
      });
      if (currentCell) { styleCell(currentCell, true); }
    };

    Object.keys(boxes).forEach(function (k) {
      boxes[k].addEventListener("change", applyFilter);
    });
    filter.addEventListener("input", applyFilter);
    applyFilter();

    document.getElementById("back-cells").addEventListener("click", function (ev) {
      ev.preventDefault();
      goList();
    });

    document.querySelector("#tree-table tbody").addEventListener("click", function (ev) {
      if (ev.target.closest("a")) { return; }  // let OSM links work
      var tr = ev.target.closest("tr");
      if (tr) { selectTree(parseInt(tr.dataset.j, 10), true); }
    });

    // O / I / J open the selected tree (or, with none picked, the cell) on
    // openstreetmap.org / in the iD editor / in JOSM (the same reused "josm"
    // tab as the links). Esc goes back to the cell list.
    document.addEventListener("keydown", function (ev) {
      if (ev.ctrlKey || ev.metaKey || ev.altKey) { return; }
      if (/^(input|textarea|select)$/i.test(ev.target.tagName)) { return; }
      if (!currentCell) { return; }
      if (ev.key === "Escape") { goList(); return; }
      var key = ev.key.toLowerCase();
      if (key !== "o" && key !== "i" && key !== "j") { return; }
      var t;
      if (selectedTree != null && trees[selectedTree]) {
        t = osmTargets(trees[selectedTree]);
      } else {
        var c = currentCell._bounds.getCenter();
        t = mapTargets(c.lat, c.lng, 18);
        t.josm = cellJosm(currentCell);
      }
      ev.preventDefault();
      if (key === "j") {
        window.open(t.josm, "josm");
      } else {
        window.open(key === "o" ? t.osm : t.id, "_blank", "noopener");
      }
    });
  }

  function fmt(n) {
    return String(n == null ? 0 : n).replace(/\B(?=(\d{3})+(?!\d))/g, ",");
  }

  function esc(s) {
    return String(s == null ? "" : s)
      .replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;");
  }

  // Cell ids are [a-z0-9_]; quote anything else for a selector anyway.
  function cssEsc(s) {
    return String(s).replace(/["\\]/g, "\\$&");
  }

  document.addEventListener("DOMContentLoaded", init);
})();
