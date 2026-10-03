/* ═══════════════════════════════════════════════════════
   logi_datatable.js — Shared Table Component Utilities
   Logistics Management Design System
   Used by: location_master.js, domestic_sales_master.js
═══════════════════════════════════════════════════════ */

window.LogiTable = (function () {
  'use strict';

  function g(id) { return document.getElementById(id); }

  /* ── COOKIE ── */
  function getCookie(name) {
    const c = document.cookie.split(';').map(s => s.trim()).find(s => s.startsWith(name + '='));
    return c ? decodeURIComponent(c.split('=')[1]) : null;
  }

  /* ── HTML ESCAPE ── */
  function escHtml(str) {
    return String(str)
      .replace(/&/g, '&amp;').replace(/</g, '&lt;')
      .replace(/>/g, '&gt;').replace(/"/g, '&quot;');
  }

  /* ── ANIMATED COUNTER ── */
  function animateVal(el, target) {
    if (!el) return;
    target = parseInt(target) || 0;
    let start = null;
    const step = ts => {
      if (!start) start = ts;
      const p = Math.min((ts - start) / 700, 1);
      el.textContent = Math.round((1 - Math.pow(1 - p, 3)) * target).toLocaleString('en-IN');
      if (p < 1) requestAnimationFrame(step);
    };
    requestAnimationFrame(step);
  }

  /* ── DATE FORMAT ── */
  function formatDate(iso) {
    if (!iso) return '—';
    try {
      return new Date(iso).toLocaleDateString('en-IN', { day: '2-digit', month: 'short', year: 'numeric' });
    } catch (e) { return iso; }
  }

  /* ── DIVISION BADGE CLASS (from DB master, window.LP_DIVISIONS) ── */
  function divisionBadgeClass(div) {
    const list = (window.LP_DIVISIONS && window.LP_DIVISIONS.length) ? window.LP_DIVISIONS : [];
    const hit = list.find(d => d.code === div);
    return (hit && hit.badge_class) ? hit.badge_class : 'badge-muted';
  }

  /* ── PAGINATION RENDERER ──
     state: { page, totalCount, perPage }
     goPageFn: string name of global goPage function (default 'goPage')
  */
  function renderPagination(state, goPageFn) {
    goPageFn = goPageFn || 'goPage';
    const totalPages = Math.ceil(state.totalCount / state.perPage);
    const info = g('pagiInfo'), btns = g('pagiBtns');
    if (!info || !btns) return;
    const start = (state.page - 1) * state.perPage + 1;
    const end   = Math.min(state.page * state.perPage, state.totalCount);
    info.textContent = state.totalCount
      ? 'Showing ' + start + '–' + end + ' of ' + state.totalCount
      : 'No records';
    let html = '<button class="pagi-btn" onclick="' + goPageFn + '(' + (state.page - 1) + ')" ' + (state.page <= 1 ? 'disabled' : '') + '>← Prev</button>';
    for (let p = Math.max(1, state.page - 2); p <= Math.min(totalPages, state.page + 2); p++) {
      html += '<button class="pagi-btn ' + (p === state.page ? 'active' : '') + '" onclick="' + goPageFn + '(' + p + ')">' + p + '</button>';
    }
    html += '<button class="pagi-btn" onclick="' + goPageFn + '(' + (state.page + 1) + ')" ' + (state.page >= totalPages ? 'disabled' : '') + '>Next →</button>';
    btns.innerHTML = html;
  }

  /* ── DROP ZONE INIT ──
     opts: { dropZoneId, fileInputId, onFile(file) }
  */
  function initDropZone(opts) {
    const dz = g(opts.dropZoneId); if (!dz) return;
    dz.addEventListener('dragover',  function (e) { e.preventDefault(); dz.classList.add('dragover'); });
    dz.addEventListener('dragleave', function ()  { dz.classList.remove('dragover'); });
    dz.addEventListener('drop', function (e) {
      e.preventDefault();
      dz.classList.remove('dragover');
      const f = e.dataTransfer.files[0];
      if (f) opts.onFile(f);
    });
    dz.addEventListener('click', function () {
      const fi = g(opts.fileInputId);
      if (fi) fi.click();
    });
  }

  /* ── FILE HANDLER ──
     opts: { nameId, sizeId, rowId, zoneId, refId, onError(msg) }
     Returns true if file accepted, false otherwise.
  */
  function handleFile(file, opts) {
    const ext = '.' + file.name.split('.').pop().toLowerCase();
    if (!['.csv', '.xlsx', '.xls'].includes(ext)) {
      if (opts.onError) opts.onError('Invalid file type: ' + ext);
      return false;
    }
    const ne = g(opts.nameId), se = g(opts.sizeId);
    const re = g(opts.rowId),  de = g(opts.zoneId);
    if (ne) ne.textContent = file.name;
    if (se) se.textContent = (file.size / 1024).toFixed(1) + ' KB';
    if (re) re.style.display = 'flex';
    if (de) de.style.display = 'none';
    const ref = g(opts.refId);
    if (ref) ref._file = file;
    return true;
  }

  /* ── CLEAR FILE ──
     opts: { rowId, zoneId, inputId, refId }
  */
  function clearFile(opts) {
    const re = g(opts.rowId), de = g(opts.zoneId);
    const fi = g(opts.inputId), ref = g(opts.refId);
    if (re)  re.style.display = 'none';
    if (de)  de.style.display = 'block';
    if (fi)  fi.value = '';
    if (ref) ref._file = null;
  }

  /* ── SELF-RENDERING DATA TABLE ──
     Builds a full table (search + sortable header + rows + pagination) inside `mount`.

     opts:
       mount     : id of the container element (required)
       data      : array of row objects
       columns[] : { key, label, width, sortable, searchable, value(row), render(row), className }
       pageSize  : rows per page (default 15)
       perPage   : array of page-size options, e.g. [15,25,50,100] — shows a dropdown
       search    : show a search box (default false)
       filters[] : optional pill buttons — { key, label, test(row) }
       rowClass  : optional fn(row) -> extra class string for the row
       emptyHtml : optional HTML for the "no rows" state

     Returns an instance: { setData(rows), refresh(), getVisible(), setPageSize(n), destroy() }
  */
  function createDataTable(opts) {
    const root = g(opts.mount);
    if (!root) return null;

    const cols   = opts.columns || [];
    const tmpl   = cols.map(function (c) { return c.width || '1fr'; }).join(' ');
    const valOf  = function (c, r) { return c.value ? c.value(r) : (r[c.key] != null ? r[c.key] : ''); };
    const cellOf = function (c, r) { return c.render ? c.render(r) : escHtml(String(valOf(c, r))); };

    const st = {
      data:     opts.data || [],
      page:     1,
      pageSize: opts.pageSize || 15,
      sortKey:  null,   // index into cols
      sortDir:  1,      // 1 asc, -1 desc
      query:    '',
      filter:   opts.filters ? opts.filters[0].key : null,
    };

    /* optional per-page dropdown */
    const perPageHtml = (opts.perPage && opts.perPage.length)
      ? '<select class="dt-perpage">' + opts.perPage.map(function (n) {
          return '<option value="' + n + '"' + (n === st.pageSize ? ' selected' : '') + '>' + n + ' / page</option>';
        }).join('') + '</select>'
      : '';

    /* skeleton: [filters] [toolbar: perPage + search] [head] [body] [footer] */
    const toolbarHtml = (opts.search || perPageHtml)
      ? '<div class="dt-toolbar">' + perPageHtml
        + (opts.search ? '<div class="search-wrap"><i class="fas fa-search"></i>'
                       + '<input type="text" class="dt-search" placeholder="Search…"></div>' : '')
        + '</div>'
      : '';

    root.innerHTML =
      (opts.filters ? '<div class="dt-filters filter-bar"></div>' : '') +
      toolbarHtml +
      '<div class="tbl-wrap">'
      + '<div class="dt-head tbl-head" style="grid-template-columns:' + tmpl + '"></div>'
      + '<div class="dt-body"></div>'
      + '<div class="pagi"><div class="dt-info pagi-info"></div><div class="dt-btns pagi-btns"></div></div>'
      + '</div>';

    function pick(sel) { return root.querySelector(sel); }
    const elHead = pick('.dt-head'),    elBody    = pick('.dt-body');
    const elInfo = pick('.dt-info'),    elBtns    = pick('.dt-btns');
    const elSearch = pick('.dt-search'), elFilters = pick('.dt-filters');
    const elPerPage = pick('.dt-perpage');

    function drawHead() {
      elHead.innerHTML = cols.map(function (c, i) {
        const active = st.sortKey === i;
        const arrow  = c.sortable
          ? ' <i class="fas fa-sort' + (active ? (st.sortDir > 0 ? '-up' : '-down') : '') + ' dt-arrow' + (active ? ' on' : '') + '"></i>'
          : '';
        return '<div class="dt-th' + (c.sortable ? ' sortable' : '') + '" data-col="' + i + '">' + escHtml(c.label) + arrow + '</div>';
      }).join('');
      if (opts.filters && elFilters) {
        elFilters.innerHTML = opts.filters.map(function (f) {
          return '<button class="filter-pill' + (f.key === st.filter ? ' active' : '') + '" data-f="' + f.key + '">' + escHtml(f.label) + '</button>';
        }).join('');
      }
    }

    function getRows() {
      let rows = st.data.slice();
      if (opts.filters && st.filter) {
        const f = opts.filters.find(function (x) { return x.key === st.filter; });
        if (f) rows = rows.filter(f.test);
      }
      const q = st.query.toLowerCase().trim();
      if (q) {
        const sc = cols.filter(function (c) { return c.searchable; });
        rows = rows.filter(function (r) {
          return sc.some(function (c) { return String(valOf(c, r)).toLowerCase().includes(q); });
        });
      }
      if (st.sortKey != null) {
        const c = cols[st.sortKey];
        rows.sort(function (a, b) {
          let va = valOf(c, a), vb = valOf(c, b);
          const na = parseFloat(va), nb = parseFloat(vb);
          if (!isNaN(na) && !isNaN(nb)) { va = na; vb = nb; }
          else { va = String(va).toLowerCase(); vb = String(vb).toLowerCase(); }
          return va < vb ? -st.sortDir : va > vb ? st.sortDir : 0;
        });
      }
      return rows;
    }

    function draw() {
      const rows  = getRows();
      const total = rows.length;
      const pages = Math.max(1, Math.ceil(total / st.pageSize));
      if (st.page > pages) st.page = pages;
      const start = (st.page - 1) * st.pageSize;
      const slice = rows.slice(start, start + st.pageSize);

      if (!total) {
        elBody.innerHTML = opts.emptyHtml ||
          '<div class="empty-state"><i class="fas fa-inbox"></i><p>No matching rows.</p></div>';
      } else {
        elBody.innerHTML = slice.map(function (r) {
          const rc = opts.rowClass ? opts.rowClass(r) : '';
          return '<div class="tbl-row dt-row ' + rc + '" style="grid-template-columns:' + tmpl + '">'
            + cols.map(function (c) {
                return '<div' + (c.className ? ' class="' + c.className + '"' : '') + '>' + cellOf(c, r) + '</div>';
              }).join('')
            + '</div>';
        }).join('');
      }

      const from = total ? start + 1 : 0;
      const to   = Math.min(start + st.pageSize, total);
      elInfo.textContent = total ? 'Showing ' + from + '–' + to + ' of ' + total : 'No records';
      let h = '<button class="pagi-btn" data-p="' + (st.page - 1) + '" ' + (st.page <= 1 ? 'disabled' : '') + '>← Prev</button>';
      for (let p = Math.max(1, st.page - 2); p <= Math.min(pages, st.page + 2); p++) {
        h += '<button class="pagi-btn ' + (p === st.page ? 'active' : '') + '" data-p="' + p + '">' + p + '</button>';
      }
      h += '<button class="pagi-btn" data-p="' + (st.page + 1) + '" ' + (st.page >= pages ? 'disabled' : '') + '>Next →</button>';
      elBtns.innerHTML = h;
      drawHead();
    }

    /* delegated events */
    elHead.addEventListener('click', function (e) {
      const th = e.target.closest('.dt-th.sortable'); if (!th) return;
      const i = +th.dataset.col;
      if (st.sortKey === i) st.sortDir = -st.sortDir; else { st.sortKey = i; st.sortDir = 1; }
      draw();
    });
    elBtns.addEventListener('click', function (e) {
      const b = e.target.closest('.pagi-btn'); if (!b || b.disabled) return;
      st.page = +b.dataset.p; draw();
    });
    if (elFilters) elFilters.addEventListener('click', function (e) {
      const b = e.target.closest('.filter-pill'); if (!b) return;
      st.filter = b.dataset.f; st.page = 1; draw();
    });
    if (elSearch) elSearch.addEventListener('input', function () {
      st.query = this.value; st.page = 1; draw();
    });
    if (elPerPage) elPerPage.addEventListener('change', function () {
      st.pageSize = parseInt(this.value, 10) || st.pageSize; st.page = 1; draw();
    });

    draw();

    return {
      setData:     function (rows) { st.data = rows || []; st.page = 1; draw(); },
      refresh:     draw,
      getVisible:  getRows,
      setPageSize: function (n) { st.pageSize = n; st.page = 1; draw(); },
      destroy:     function () { root.innerHTML = ''; },
    };
  }

  /* ── PUBLIC API ── */
  return {
    getCookie:        getCookie,
    escHtml:          escHtml,
    animateVal:       animateVal,
    formatDate:       formatDate,
    divisionBadgeClass: divisionBadgeClass,
    renderPagination: renderPagination,
    initDropZone:     initDropZone,
    handleFile:       handleFile,
    clearFile:        clearFile,
    createDataTable:  createDataTable,
  };

})();
