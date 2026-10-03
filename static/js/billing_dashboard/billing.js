/* ============================================================================
   Export Billing Dashboard — shared page behaviour.

   Every block below is defensive: it looks for its markup and silently does
   nothing when the current page doesn't have it, so all four billing screens
   can load this one file.

     1. Filter popover        — dropdown on desktop, bottom sheet on mobile
     2. Upload dropzone       — click + drag & drop, file preview, busy state
     3. KPI count-up          — numbers animate in on first paint
     4. Table labels          — mirrors <th> text onto <td data-label> so the
                                mobile card layout has column names even for
                                tables whose cells weren't hand-annotated
     5. Print / Save-as-PDF   — paper-sized, rasterised charts
     6. Column filter + sort  — Excel-style AutoFilter on any table
                                marked data-bd-filter
   ============================================================================ */
(function () {
  "use strict";

  // Matches the CSS breakpoint at which the filter panel becomes a bottom
  // sheet — below it, opening the panel locks the page behind it.
  var SHEET = "(max-width: 768px)";
  var isSheet = function () { return window.matchMedia(SHEET).matches; };
  var reduceMotion = window.matchMedia("(prefers-reduced-motion: reduce)").matches;

  /* ── 1. Filter popover / bottom sheet ─────────────────────────────────── */
  function initFilterPanel() {
    var toggle = document.getElementById("bdFilterToggle");
    var panel = document.getElementById("bdFilterPanel");
    if (!toggle || !panel) return;

    var closeBtn = document.getElementById("bdFilterClose");
    var backdrop = document.getElementById("bdFilterBackdrop");

    function isOpen() { return !panel.hidden; }

    function open() {
      panel.hidden = false;
      toggle.setAttribute("aria-expanded", "true");
      if (backdrop) backdrop.hidden = false;
      // Lock the page behind the sheet so only the sheet scrolls.
      if (isSheet()) document.body.classList.add("bd-sheet-open");
    }

    function close() {
      panel.hidden = true;
      toggle.setAttribute("aria-expanded", "false");
      if (backdrop) backdrop.hidden = true;
      document.body.classList.remove("bd-sheet-open");
    }

    toggle.addEventListener("click", function (e) {
      e.stopPropagation();
      if (isOpen()) { close(); } else { open(); }
    });
    if (closeBtn) closeBtn.addEventListener("click", close);
    if (backdrop) backdrop.addEventListener("click", close);

    // Clicks inside the panel must not bubble to the document closer.
    panel.addEventListener("click", function (e) { e.stopPropagation(); });
    document.addEventListener("click", function () { if (isOpen()) close(); });
    document.addEventListener("keydown", function (e) {
      if (e.key === "Escape" && isOpen()) { close(); toggle.focus(); }
    });
    // Crossing the breakpoint with the sheet open must release the lock.
    window.matchMedia(SHEET).addEventListener("change", function (ev) {
      if (!ev.matches) document.body.classList.remove("bd-sheet-open");
      else if (isOpen()) document.body.classList.add("bd-sheet-open");
    });
  }

  /* ── 2. Upload dropzone ───────────────────────────────────────────────── */
  function initUpload() {
    var drop = document.getElementById("bdDrop");
    var fileInput = document.getElementById("bdFile");
    var main = document.getElementById("bdDropMain");
    var hint = document.getElementById("bdDropHint");
    var form = document.getElementById("bdUploadForm");
    var submit = document.getElementById("bdSubmit");
    var bar = document.getElementById("bdUploadBar");
    if (!drop || !fileInput) return;

    var IDLE = main ? main.textContent : "";
    var IDLE_HINT = hint ? hint.textContent : "";

    function showFile() {
      if (!fileInput.files.length) {
        drop.classList.remove("has-file");
        if (main) main.textContent = IDLE;
        if (hint) hint.textContent = IDLE_HINT;
        return;
      }
      var f = fileInput.files[0];
      drop.classList.add("has-file");
      if (main) main.textContent = f.name;
      if (hint) hint.textContent = (f.size / 1048576).toFixed(2) + " MB · ready to upload";
    }

    fileInput.addEventListener("change", showFile);

    // Drag & drop. dragover must be cancelled for drop to fire at all.
    ["dragenter", "dragover"].forEach(function (evt) {
      drop.addEventListener(evt, function (e) {
        e.preventDefault(); e.stopPropagation();
        drop.classList.add("is-dragover");
      });
    });
    ["dragleave", "dragend"].forEach(function (evt) {
      drop.addEventListener(evt, function (e) {
        e.preventDefault(); e.stopPropagation();
        drop.classList.remove("is-dragover");
      });
    });
    drop.addEventListener("drop", function (e) {
      e.preventDefault(); e.stopPropagation();
      drop.classList.remove("is-dragover");
      if (e.dataTransfer && e.dataTransfer.files.length) {
        fileInput.files = e.dataTransfer.files;
        showFile();
      }
    });

    if (form && submit) {
      form.addEventListener("submit", function () {
        submit.disabled = true;
        submit.innerHTML = '<i class="fas fa-spinner fa-spin"></i> Processing…';
        if (bar) bar.classList.add("is-active");
      });
    }
  }

  /* ── 3. KPI count-up ──────────────────────────────────────────────────── */
  function initCountUp() {
    var targets = document.querySelectorAll("[data-countup]");
    if (!targets.length) return;

    if (reduceMotion || !("IntersectionObserver" in window)) return;

    function run(el) {
      var raw = el.getAttribute("data-countup");
      var end = parseFloat(raw);
      if (!isFinite(end)) return;
      var decimals = (raw.split(".")[1] || "").length;
      var prefix = el.getAttribute("data-prefix") || "";
      var suffix = el.getAttribute("data-suffix") || "";
      var started = null;
      var DURATION = 650;

      function frame(ts) {
        if (started === null) started = ts;
        var p = Math.min(1, (ts - started) / DURATION);
        // easeOutCubic
        var v = end * (1 - Math.pow(1 - p, 3));
        el.textContent = prefix + v.toLocaleString("en-IN", {
          minimumFractionDigits: decimals, maximumFractionDigits: decimals,
        }) + suffix;
        if (p < 1) requestAnimationFrame(frame);
      }
      requestAnimationFrame(frame);
    }

    var io = new IntersectionObserver(function (entries) {
      entries.forEach(function (entry) {
        if (!entry.isIntersecting) return;
        io.unobserve(entry.target);
        run(entry.target);
      });
    }, { threshold: 0.25 });

    targets.forEach(function (el) { io.observe(el); });
  }

  /* ── 4. Auto column labels for the mobile card layout ─────────────────── */
  function initTableLabels() {
    document.querySelectorAll("table.bd-table-cards").forEach(function (table) {
      var headers = [].map.call(
        table.querySelectorAll("thead th"),
        function (th) { return (th.textContent || "").trim(); }
      );
      if (!headers.length) return;
      table.querySelectorAll("tbody tr").forEach(function (tr) {
        [].forEach.call(tr.children, function (td, i) {
          if (!td.hasAttribute("data-label") && headers[i]) {
            td.setAttribute("data-label", headers[i]);
          }
        });
      });
    });
  }

  /* ── 5. Print / Save-as-PDF ───────────────────────────────────────────────
     Highcharts burns a pixel size into each SVG when it renders, and an SVG
     without a viewBox does not scale with CSS — so on paper a chart keeps its
     on-screen width and is cropped at the page edge. chart.reflow() is not a
     fix here: browsers fire beforeprint before the print layout exists, so it
     re-measures the *screen* container and some series fail to redraw.

     Instead the charts are given an explicit print size derived from the
     @page rule in billing.css, then restored afterwards. Keep the two in sync.
        A4 landscape 297mm − 9mm margins ×2  = 279mm ≈ 1054 CSS px
        − .bd-card padding (12px ×2) and border (1px ×2) ≈ 1028 px          */
  var PRINT_CHART_W = 1020;   // full-width charts (world map, ranked countries)
  var PRINT_HALF_W  = 500;    // paired charts: two sit side-by-side per row
  var PRINT_CHART_H = 290;    // standard chart height — two ROWS fit one page
  var PRINT_TALL_H  = 460;    // .bd-chart-tall (world map, ranked countries)

  function eachChart(fn) {
    if (!window.Highcharts || !Highcharts.charts) return;
    Highcharts.charts.forEach(function (c) {
      if (!c || !c.renderTo) return;
      try { fn(c); } catch (e) { /* chart mid-destroy — skip it */ }
    });
  }

  function chartsToPrint() {
    eachChart(function (c) {
      if (c.bdScreenSize) return;                 // already switched
      c.bdScreenSize = { w: c.chartWidth, h: c.chartHeight };
      var tall = c.renderTo.classList.contains("bd-chart-tall");
      // Charts paired in a .bd-grid-2 row are drawn at half width so two print
      // side-by-side; tall charts (map / ranked list) keep the full page width.
      var half = !tall && !!(c.renderTo.closest && c.renderTo.closest(".bd-grid-2"));
      c.setSize(half ? PRINT_HALF_W : PRINT_CHART_W, tall ? PRINT_TALL_H : PRINT_CHART_H, false);
    });
  }

  function chartsToScreen() {
    eachChart(function (c) {
      if (!c.bdScreenSize) return;
      c.setSize(c.bdScreenSize.w, c.bdScreenSize.h, false);
      c.bdScreenSize = null;
      c.reflow();
    });
  }

  /* Charts are printed as raster images, not as their live SVG.
     Browsers render inline SVG unreliably in paginated (print) layout —
     Firefox drops the bar/column series entirely, keeping only the axes — and
     the failure differs per browser. Snapshotting each chart to a PNG at the
     paper width makes the PDF identical everywhere. Drawn at 2× so the image
     is still crisp at print resolution. */
  function rasterizeChart(chart) {
    return new Promise(function (resolve) {
      var svgEl = chart.container && chart.container.querySelector("svg");
      if (!svgEl || !window.XMLSerializer) return resolve(null);

      var w = chart.chartWidth, h = chart.chartHeight;
      var clone = svgEl.cloneNode(true);
      clone.setAttribute("xmlns", "http://www.w3.org/2000/svg");
      clone.setAttribute("width", w);
      clone.setAttribute("height", h);

      var markup;
      try {
        // 'inherit' has nothing to inherit from once the SVG stands alone.
        markup = new XMLSerializer().serializeToString(clone)
          .replace(/font-family:\s*inherit/g, "font-family:Poppins,Arial,sans-serif");
      } catch (e) { return resolve(null); }

      var image = new Image();
      image.onload = function () {
        try {
          var scale = 2;
          var canvas = document.createElement("canvas");
          canvas.width = w * scale;
          canvas.height = h * scale;
          var ctx = canvas.getContext("2d");
          ctx.fillStyle = "#ffffff";
          ctx.fillRect(0, 0, canvas.width, canvas.height);
          ctx.drawImage(image, 0, 0, canvas.width, canvas.height);
          resolve(canvas.toDataURL("image/png"));
        } catch (e) { resolve(null); }
      };
      image.onerror = function () { resolve(null); };
      image.src = "data:image/svg+xml;charset=utf-8," + encodeURIComponent(markup);
    });
  }

  function clearPrintImages() {
    document.querySelectorAll(".bd-chart-print").forEach(function (el) { el.remove(); });
    document.querySelectorAll(".bd-chart--printed").forEach(function (el) {
      el.classList.remove("bd-chart--printed");
    });
  }

  // Resize to paper geometry, snapshot every chart, swap the images in.
  // Resolves once each chart is either imaged or has failed (and will simply
  // print as its live SVG).
  function preparePrintImages() {
    clearPrintImages();
    chartsToPrint();
    var jobs = [];
    eachChart(function (c) {
      jobs.push(rasterizeChart(c).then(function (png) {
        if (!png) return;
        var img = document.createElement("img");
        img.className = "bd-chart-print";
        img.alt = "";
        img.src = png;
        c.renderTo.parentNode.insertBefore(img, c.renderTo);
        c.renderTo.classList.add("bd-chart--printed");
      }));
    });
    return Promise.all(jobs);
  }

  function initPrint() {
    // Ctrl+P can't await the snapshotting, so it falls back to correctly
    // sized live SVG charts.
    window.addEventListener("beforeprint", function () {
      if (!document.querySelector(".bd-chart-print")) chartsToPrint();
    });
    window.addEventListener("afterprint", function () {
      clearPrintImages();
      chartsToScreen();
    });

    var btn = document.getElementById("bdPrint");
    if (!btn) return;

    btn.addEventListener("click", function () {
      // An open filter sheet would otherwise be captured over the report.
      document.body.classList.remove("bd-sheet-open");
      var panel = document.getElementById("bdFilterPanel");
      if (panel && !panel.hidden) {
        panel.hidden = true;
        var toggle = document.getElementById("bdFilterToggle");
        if (toggle) toggle.setAttribute("aria-expanded", "false");
        var backdrop = document.getElementById("bdFilterBackdrop");
        if (backdrop) backdrop.hidden = true;
      }

      var label = btn.innerHTML;
      btn.disabled = true;
      btn.innerHTML = '<i class="fas fa-spinner fa-spin"></i> Preparing…';

      var done = function () {
        btn.disabled = false;
        btn.innerHTML = label;
      };

      if (!window.Promise || !window.Highcharts) { done(); window.print(); return; }

      preparePrintImages().then(function () {
        done();
        window.print();
      }, function () {
        done();
        window.print();
      });
    });
  }

  /* ── 6. Excel-style column filter + sort ──────────────────────────────────
     Opt a table in with `data-bd-filter`; every <th> then becomes an AutoFilter
     control that opens a popover offering sort ascending / descending plus a
     searchable checkbox list of that column's distinct values, exactly like
     Excel's filter dropdown.

     Markup contract
       <table data-bd-filter data-bd-sort="9:desc">  default sort — colIndex:dir
       <th data-no-filter>            column is skipped (e.g. an actions column)
       <tr data-no-filter>            row is not data (the "no uploads" filler)
       <td data-sort="2026-09-14T…">  typed compare key, else the cell's text

     The popover is appended to <body> and positioned fixed, because
     .bd-table-scroll clips its own overflow — nested in the header it would be
     cut off at the first scroll edge.                                        */
  var openFilter = null;          // { el, col, anchor } — at most one at a time

  function cellDisplay(tr, i) {
    var td = tr.cells[i];
    return td ? (td.textContent || "").replace(/\s+/g, " ").trim() : "";
  }

  function cellKey(tr, i) {
    var td = tr.cells[i];
    if (!td) return "";
    var explicit = td.getAttribute("data-sort");
    return explicit !== null ? explicit.trim() : cellDisplay(tr, i);
  }

  function asNumber(v) {
    if (!v) return null;
    var n = Number(v.replace(/[,\s]/g, ""));
    return isFinite(n) ? n : null;
  }

  // Blanks last; numerically when both sides are numbers; otherwise a natural
  // string compare, which also orders the ISO timestamps carried in data-sort.
  function compareValues(a, b) {
    if (a === b) return 0;
    if (a === "") return 1;
    if (b === "") return -1;
    var na = asNumber(a), nb = asNumber(b);
    if (na !== null && nb !== null) return na - nb;
    if (na !== null) return -1;
    if (nb !== null) return 1;
    return a.localeCompare(b, undefined, { numeric: true, sensitivity: "base" });
  }

  function closeFilterPanel() {
    if (!openFilter) return;
    var panel = openFilter;
    openFilter = null;
    panel.anchor.setAttribute("aria-expanded", "false");
    panel.el.remove();
  }

  function positionFilterPanel() {
    if (!openFilter) return;
    var el = openFilter.el;
    var r = openFilter.anchor.getBoundingClientRect();
    // Anchor scrolled out of sight — the popover has nothing left to point at.
    if (r.bottom < 0 || r.top > window.innerHeight) { closeFilterPanel(); return; }
    var w = el.offsetWidth, h = el.offsetHeight, pad = 8;
    var left = Math.min(Math.max(pad, r.left), Math.max(pad, window.innerWidth - w - pad));
    var top = r.bottom + 4;
    if (top + h > window.innerHeight - pad) top = Math.max(pad, r.top - h - 4);
    el.style.left = left + "px";
    el.style.top = top + "px";
  }

  function initTableFilters() {
    var tables = document.querySelectorAll("table[data-bd-filter]");
    if (!tables.length) return;

    document.addEventListener("click", closeFilterPanel);
    document.addEventListener("keydown", function (e) {
      if (e.key === "Escape" && openFilter) {
        var th = openFilter.anchor;
        closeFilterPanel();
        th.focus();
      }
    });
    window.addEventListener("resize", positionFilterPanel);
    window.addEventListener("scroll", positionFilterPanel, true);

    tables.forEach(function (table) {
      var tbody = table.tBodies[0];
      var headRow = table.tHead && table.tHead.rows[0];
      if (!tbody || !headRow) return;

      var rows = [].filter.call(tbody.rows, function (tr) {
        return !tr.hasAttribute("data-no-filter") && !tr.querySelector("td.bd-empty");
      });
      if (!rows.length) return;

      var cols = [];

      /* Toolbar — row count + "clear all", shown only while a filter is on. */
      var tools = document.createElement("div");
      tools.className = "bd-table-tools";
      tools.hidden = true;
      tools.innerHTML =
        '<span class="bd-table-count"><i class="fas fa-filter"></i> Showing ' +
        '<b class="bd-table-shown">0</b> of <b>' + rows.length + '</b></span>' +
        '<button type="button" class="bd-btn bd-btn-sm bd-table-clear">' +
        '<i class="fas fa-filter-circle-xmark"></i> Clear all filters</button>';
      var scroller = table.closest(".bd-table-scroll") || table;
      scroller.parentNode.insertBefore(tools, scroller);
      var shownEl = tools.querySelector(".bd-table-shown");
      tools.querySelector(".bd-table-clear").addEventListener("click", function () {
        closeFilterPanel();
        cols.forEach(function (c) { c.selected = null; });
        applyFilters();
      });

      /* "No rows match" placeholder, kept last in the tbody. */
      var noMatch = document.createElement("tr");
      noMatch.setAttribute("data-no-filter", "");
      noMatch.className = "bd-filter-empty";
      noMatch.hidden = true;
      noMatch.innerHTML = '<td class="bd-empty" colspan="' + headRow.cells.length + '">' +
        '<i class="fas fa-filter"></i> No rows match the current filters.</td>';
      tbody.appendChild(noMatch);

      /* Header controls. */
      [].forEach.call(headRow.cells, function (th, idx) {
        if (th.hasAttribute("data-no-filter")) return;
        var label = (th.textContent || "").trim();
        var inner = document.createElement("span");
        inner.className = "bd-th-inner";
        inner.innerHTML = '<span class="bd-th-text"></span><span class="bd-th-marks">' +
          '<i class="fas fa-caret-down bd-th-caret" aria-hidden="true"></i>' +
          '<i class="fas fa-filter bd-th-funnel" aria-hidden="true"></i></span>';
        inner.querySelector(".bd-th-text").textContent = label;
        th.textContent = "";
        th.appendChild(inner);
        th.classList.add("bd-th-filterable");
        th.setAttribute("role", "button");
        th.setAttribute("tabindex", "0");
        th.setAttribute("aria-haspopup", "dialog");
        th.setAttribute("aria-expanded", "false");
        th.title = "Sort or filter by " + label;

        var col = { idx: idx, th: th, label: label, selected: null };
        cols.push(col);

        th.addEventListener("click", function (e) {
          e.stopPropagation();
          var wasOpen = openFilter && openFilter.col === col;
          closeFilterPanel();
          if (!wasOpen) openPanel(col);
        });
        th.addEventListener("keydown", function (e) {
          if (e.key === "Enter" || e.key === " ") { e.preventDefault(); th.click(); }
        });
      });
      if (!cols.length) return;

      /* ── filtering ── */
      function passes(tr, exceptIdx) {
        return cols.every(function (c) {
          if (!c.selected || c.idx === exceptIdx) return true;
          return c.selected.has(cellDisplay(tr, c.idx));
        });
      }

      function applyFilters() {
        var shown = 0;
        rows.forEach(function (tr) {
          var ok = passes(tr, null);
          tr.classList.toggle("bd-row-out", !ok);
          if (ok) shown++;
        });
        var active = 0;
        cols.forEach(function (c) {
          if (c.selected) active++;
          c.th.classList.toggle("is-filtered", !!c.selected);
        });
        noMatch.hidden = shown > 0;
        shownEl.textContent = shown;
        tools.hidden = !active;
      }

      /* ── sorting ── */
      function applySort(idx, dir) {
        var order = rows.map(function (tr, i) { return { tr: tr, i: i }; });
        order.sort(function (a, b) {
          var r = compareValues(cellKey(a.tr, idx), cellKey(b.tr, idx));
          return r === 0 ? a.i - b.i : (dir === "desc" ? -r : r);   // stable
        });
        rows = order.map(function (o) { return o.tr; });
        rows.forEach(function (tr) { tbody.appendChild(tr); });
        tbody.appendChild(noMatch);                      // keep the filler last
        cols.forEach(function (c) {
          c.th.classList.toggle("is-sorted-asc",  c.idx === idx && dir === "asc");
          c.th.classList.toggle("is-sorted-desc", c.idx === idx && dir === "desc");
        });
      }

      /* ── the popover ── */
      function openPanel(col) {
        var el = document.createElement("div");
        el.className = "bd-colfilter";
        el.setAttribute("role", "dialog");
        el.setAttribute("aria-label", "Sort and filter: " + col.label);
        el.innerHTML =
          '<div class="bd-colfilter-head"><span class="bd-colfilter-title"></span>' +
          '<button type="button" class="bd-colfilter-x" aria-label="Close">&times;</button></div>' +
          '<div class="bd-colfilter-sorts">' +
          '<button type="button" class="bd-colfilter-sort" data-dir="asc"><i class="fas fa-arrow-up"></i> Sort ascending</button>' +
          '<button type="button" class="bd-colfilter-sort" data-dir="desc"><i class="fas fa-arrow-down"></i> Sort descending</button>' +
          '</div>' +
          '<label class="bd-colfilter-search"><i class="fas fa-search"></i>' +
          '<input type="search" placeholder="Search values…" aria-label="Search values"></label>' +
          '<div class="bd-colfilter-list"></div>' +
          '<div class="bd-colfilter-foot">' +
          '<button type="button" class="bd-btn bd-btn-sm" data-act="clear">Clear filter</button>' +
          '<button type="button" class="bd-btn bd-btn-sm bd-btn-primary" data-act="apply"><i class="fas fa-check"></i> Apply</button>' +
          '</div>';
        el.querySelector(".bd-colfilter-title").textContent = col.label;
        el.addEventListener("click", function (e) { e.stopPropagation(); });
        document.body.appendChild(el);
        openFilter = { el: el, col: col, anchor: col.th };
        col.th.setAttribute("aria-expanded", "true");

        // Excel semantics: the value list reflects what the OTHER columns'
        // filters already allow, so a choice never points at unreachable rows.
        var values = [], seen = Object.create(null);
        rows.forEach(function (tr) {
          if (!passes(tr, col.idx)) return;
          var v = cellDisplay(tr, col.idx);
          if (!(v in seen)) { seen[v] = true; values.push(v); }
        });
        values.sort(compareValues);

        var list = el.querySelector(".bd-colfilter-list");
        list.innerHTML = '<label class="bd-colfilter-opt bd-colfilter-all">' +
          '<input type="checkbox" checked><span>(Select all)</span></label>';
        values.forEach(function (v) {
          var opt = document.createElement("label");
          opt.className = "bd-colfilter-opt";
          opt.innerHTML = '<input type="checkbox"><span></span>';
          var box = opt.querySelector("input");
          box.value = v;
          box.checked = !col.selected || col.selected.has(v);
          var text = opt.querySelector("span");
          if (v === "") { text.textContent = "(Blanks)"; text.className = "bd-colfilter-blank"; }
          else { text.textContent = v; opt.title = v; }
          list.appendChild(opt);
        });

        var allBox = list.querySelector(".bd-colfilter-all input");
        var boxes = [].slice.call(list.querySelectorAll(".bd-colfilter-opt:not(.bd-colfilter-all) input"));
        var applyBtn = el.querySelector('[data-act="apply"]');

        function syncAll() {
          var on = boxes.filter(function (b) { return b.checked; }).length;
          allBox.checked = on === boxes.length;
          allBox.indeterminate = on > 0 && on < boxes.length;
          applyBtn.disabled = on === 0;   // Excel likewise refuses an empty filter
        }
        syncAll();

        allBox.addEventListener("change", function () {
          // Only the entries the search box currently shows, as Excel does.
          boxes.forEach(function (b) {
            if (!b.parentNode.hidden) b.checked = allBox.checked;
          });
          syncAll();
        });
        boxes.forEach(function (b) { b.addEventListener("change", syncAll); });

        var search = el.querySelector(".bd-colfilter-search input");
        search.addEventListener("input", function () {
          var q = search.value.toLowerCase();
          boxes.forEach(function (b) {
            b.parentNode.hidden = !!q && b.value.toLowerCase().indexOf(q) === -1;
          });
        });

        el.querySelectorAll(".bd-colfilter-sort").forEach(function (btn) {
          btn.addEventListener("click", function () {
            applySort(col.idx, btn.getAttribute("data-dir"));
            closeFilterPanel();
          });
        });
        el.querySelector(".bd-colfilter-x").addEventListener("click", closeFilterPanel);
        el.querySelector('[data-act="clear"]').addEventListener("click", function () {
          col.selected = null;
          applyFilters();
          closeFilterPanel();
        });
        applyBtn.addEventListener("click", function () {
          var picked = boxes.filter(function (b) { return b.checked; });
          col.selected = picked.length === boxes.length
            ? null                                    // everything ticked = no filter
            : new Set(picked.map(function (b) { return b.value; }));
          applyFilters();
          closeFilterPanel();
        });

        positionFilterPanel();
        search.focus();
      }

      var preset = (table.getAttribute("data-bd-sort") || "").split(":");
      if (preset.length === 2 && /^\d+$/.test(preset[0])) applySort(+preset[0], preset[1]);
      applyFilters();
    });
  }

  function boot() {
    initFilterPanel();
    initUpload();
    initCountUp();
    initTableLabels();
    initTableFilters();
    initPrint();
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", boot);
  } else {
    boot();
  }
})();
