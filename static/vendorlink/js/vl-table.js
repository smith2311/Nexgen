/* ═══════════════════════════════════════════════════════════════════════
   vl-table.js — progressive enhancement for server-rendered VendorLink tables

   The shared LogiTable.createDataTable() builds a table from a JSON array,
   which suits the modules that fetch rows over AJAX. VendorLink renders its
   rows server-side, so this does the same job the other way round: it takes
   the `.tbl-row` elements Django already emitted and adds sorting, search,
   filter pills and pagination on top of them — same `dt-*` markup and the
   same look as the shared component, no extra requests.

   Markup contract:
     <div data-vl-table>                     ← wrapper (this element)
       <div class="tbl-wrap">
         <div class="tbl-head">
           <div data-sort="text|num">Name</div>   ← sortable column
           <div>Actions</div>                     ← plain column
         </div>
         <div class="tbl-row" data-search="acme logistics" data-tag="active">…</div>
       </div>
     </div>

   Options via attributes on the wrapper:
     data-vl-table            enable
     data-page-size="15"      rows per page (0 = no pagination)
     data-search-placeholder  placeholder text for the search box
     data-filters='[{"key":"all","label":"All"},{"key":"active","label":"Active"}]'
                              filter pills matched against each row's data-tag
   ══════════════════════════════════════════════════════════════════════ */
(function () {
  'use strict';

  function textOf(el) { return (el.textContent || '').trim(); }

  function parseNum(str) {
    var n = parseFloat(String(str).replace(/[^0-9.\-]/g, ''));
    return isNaN(n) ? null : n;
  }

  function build(wrap) {
    var table = wrap.querySelector('.tbl-wrap');
    var head = wrap.querySelector('.tbl-head');
    if (!table || !head) return;

    var rows = Array.prototype.slice.call(table.querySelectorAll('.tbl-row'));
    if (!rows.length) return;

    var pageSize = parseInt(wrap.getAttribute('data-page-size') || '15', 10);
    var state = { page: 1, sortCol: null, sortDir: 1, query: '', filter: null };

    var filters = [];
    try { filters = JSON.parse(wrap.getAttribute('data-filters') || '[]'); }
    catch (e) { filters = []; }
    if (filters.length) state.filter = filters[0].key;

    /* ── toolbar: filter pills + search, injected above the table ── */
    var toolbar = document.createElement('div');
    toolbar.className = 'vl-dt-controls';

    if (filters.length) {
      var pills = document.createElement('div');
      pills.className = 'dt-filters filter-bar';
      pills.innerHTML = filters.map(function (f) {
        return '<button type="button" class="filter-pill' +
          (f.key === state.filter ? ' active' : '') +
          '" data-f="' + f.key + '">' + f.label +
          (f.count != null ? ' <span class="vl-pill-n">' + f.count + '</span>' : '') +
          '</button>';
      }).join('');
      pills.addEventListener('click', function (e) {
        var b = e.target.closest('.filter-pill');
        if (!b) return;
        state.filter = b.getAttribute('data-f');
        state.page = 1;
        Array.prototype.forEach.call(pills.children, function (c) {
          c.classList.toggle('active', c === b);
        });
        draw();
      });
      toolbar.appendChild(pills);
    }

    var bar = document.createElement('div');
    bar.className = 'dt-toolbar';
    bar.innerHTML =
      '<select class="dt-perpage" aria-label="Rows per page">' +
        [10, 15, 25, 50, 100].map(function (n) {
          return '<option value="' + n + '"' + (n === pageSize ? ' selected' : '') + '>' + n + ' / page</option>';
        }).join('') +
      '</select>' +
      '<div class="search-wrap"><i class="fas fa-search"></i>' +
      '<input type="text" class="dt-search" aria-label="Filter rows" placeholder="' +
      (wrap.getAttribute('data-search-placeholder') || 'Search…') + '"></div>';
    toolbar.appendChild(bar);
    wrap.insertBefore(toolbar, table);

    bar.querySelector('.dt-search').addEventListener('input', function () {
      state.query = this.value.toLowerCase().trim();
      state.page = 1;
      draw();
    });
    bar.querySelector('.dt-perpage').addEventListener('change', function () {
      pageSize = parseInt(this.value, 10) || pageSize;
      state.page = 1;
      draw();
    });

    /* ── sortable headers ── */
    var heads = Array.prototype.slice.call(head.children);
    heads.forEach(function (th, i) {
      var mode = th.getAttribute('data-sort');
      if (!mode) return;
      th.classList.add('dt-th', 'sortable');
      th.insertAdjacentHTML('beforeend', ' <i class="fas fa-sort dt-arrow"></i>');
      th.addEventListener('click', function () {
        if (state.sortCol === i) state.sortDir = -state.sortDir;
        else { state.sortCol = i; state.sortDir = 1; }
        heads.forEach(function (o) {
          var ic = o.querySelector('.dt-arrow');
          if (ic) { ic.className = 'fas fa-sort dt-arrow'; }
        });
        var icon = th.querySelector('.dt-arrow');
        if (icon) icon.className = 'fas fa-sort-' + (state.sortDir > 0 ? 'up' : 'down') + ' dt-arrow on';
        state.page = 1;
        draw();
      });
    });

    /* ── footer: count + pager ── */
    var footer = document.createElement('div');
    footer.className = 'pagi';
    footer.innerHTML = '<div class="pagi-info"></div><div class="pagi-btns"></div>';
    table.appendChild(footer);
    var info = footer.querySelector('.pagi-info');
    var btns = footer.querySelector('.pagi-btns');
    btns.addEventListener('click', function (e) {
      var b = e.target.closest('.pagi-btn');
      if (!b || b.disabled) return;
      state.page = parseInt(b.getAttribute('data-p'), 10);
      draw();
      wrap.scrollIntoView({ behavior: 'smooth', block: 'start' });
    });

    var empty = document.createElement('div');
    empty.className = 'empty-state';
    empty.innerHTML = '<i class="fas fa-magnifying-glass"></i><p>No rows match your search.</p>';
    empty.hidden = true;
    table.insertBefore(empty, footer);

    function matches(row) {
      if (state.filter && state.filter !== 'all') {
        var tags = (row.getAttribute('data-tag') || '').split(/\s+/);
        if (tags.indexOf(state.filter) === -1) return false;
      }
      if (!state.query) return true;
      var hay = row.getAttribute('data-search');
      if (hay == null) hay = textOf(row);
      return hay.toLowerCase().indexOf(state.query) !== -1;
    }

    function draw() {
      var visible = rows.filter(matches);

      if (state.sortCol != null) {
        var mode = heads[state.sortCol].getAttribute('data-sort');
        var col = state.sortCol;
        visible.sort(function (a, b) {
          var av = textOf(a.children[col]), bv = textOf(b.children[col]);
          if (mode === 'num') {
            var an = parseNum(av), bn = parseNum(bv);
            // Rows with no number sort last regardless of direction.
            if (an === null && bn === null) return 0;
            if (an === null) return 1;
            if (bn === null) return -1;
            return (an - bn) * state.sortDir;
          }
          return av.localeCompare(bv, undefined, { numeric: true }) * state.sortDir;
        });
      }

      var total = visible.length;
      var pages = pageSize > 0 ? Math.max(1, Math.ceil(total / pageSize)) : 1;
      if (state.page > pages) state.page = pages;
      var start = pageSize > 0 ? (state.page - 1) * pageSize : 0;
      var slice = pageSize > 0 ? visible.slice(start, start + pageSize) : visible;

      rows.forEach(function (r) { r.style.display = 'none'; });
      // Re-append in sorted order so the DOM matches what the user sees.
      slice.forEach(function (r) { r.style.display = ''; table.insertBefore(r, empty); });

      empty.hidden = total !== 0;
      info.textContent = total
        ? 'Showing ' + (start + 1) + '–' + Math.min(start + slice.length, total) + ' of ' + total
        : 'No records';

      if (pageSize > 0 && pages > 1) {
        var h = '<button type="button" class="pagi-btn" data-p="' + (state.page - 1) + '"' +
                (state.page <= 1 ? ' disabled' : '') + '>← Prev</button>';
        for (var p = Math.max(1, state.page - 2); p <= Math.min(pages, state.page + 2); p++) {
          h += '<button type="button" class="pagi-btn' + (p === state.page ? ' active' : '') +
               '" data-p="' + p + '">' + p + '</button>';
        }
        h += '<button type="button" class="pagi-btn" data-p="' + (state.page + 1) + '"' +
             (state.page >= pages ? ' disabled' : '') + '>Next →</button>';
        btns.innerHTML = h;
      } else {
        btns.innerHTML = '';
      }
    }

    draw();
  }

  function init(root) {
    var scope = root || document;
    Array.prototype.forEach.call(
      scope.querySelectorAll('[data-vl-table]'),
      function (w) { if (!w.__vlTableReady) { w.__vlTableReady = true; build(w); } }
    );
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', function () { init(); });
  } else {
    init();
  }

  window.VLTable = { init: init };
})();
