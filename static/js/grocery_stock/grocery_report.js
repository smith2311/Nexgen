/* ═══════════════════════════════════════════════════════════════════════
   grocery_report.js — read-only report screens (Current Stock, Stock Ledger).
   Driven by GS_REPORT, built from REPORT_SPECS in grocery_stock/pages_txn.py.
═══════════════════════════════════════════════════════════════════════ */
(function () {
  'use strict';

  if (typeof GS_REPORT === 'undefined') return;

  const U = GSUtil;
  const cfg = GS_REPORT;

  const state = {
    page: 1, pageSize: 50, count: 0, rows: [],
    search: '', ordering: '',
    filters: {},          // name -> value
    toggles: {},          // name -> bool
    from: '', to: '',
  };

  function url() {
    const p = new URLSearchParams({ page: state.page });
    // Constraints baked into the report itself (e.g. the purchase register
    // only lists POSTED receipts). Set first so a user filter cannot drop one.
    Object.keys(cfg.fixedParams || {}).forEach((k) => p.set(k, cfg.fixedParams[k]));
    if (state.search) p.set('search', state.search);
    if (state.ordering) p.set('ordering', state.ordering);
    Object.keys(state.filters).forEach((k) => {
      if (state.filters[k]) p.set(k, state.filters[k]);
    });
    Object.keys(state.toggles).forEach((k) => {
      if (state.toggles[k]) p.set(k, 'true');
    });
    if (state.from) p.set('from', state.from);
    if (state.to) p.set('to', state.to);
    return cfg.apiBase + '?' + p.toString();
  }

  /* ── filter controls ── */

  function buildControls() {
    const wrap = U.$('gsFilterControls');

    wrap.innerHTML = cfg.filters.map((f) => {
      const id = 'gsf_' + f.name;
      if (f.type === 'select') {
        return `<select class="gs-select-sm gs-report-filter" id="${id}"
                  data-name="${U.esc(f.name)}">
                  <option value="">${U.esc(f.label)}: all</option>
                  ${(f.choices || []).map((c) =>
                    `<option value="${U.esc(c.value)}">${U.esc(c.label)}</option>`).join('')}
                </select>`;
      }
      return `<select class="gs-select-sm gs-report-filter" id="${id}"
                data-name="${U.esc(f.name)}" data-source="${U.esc(f.source)}"
                data-label="${U.esc(f.source_label)}">
                <option value="">${U.esc(f.label)}: all</option>
              </select>`;
    }).join('');

    if (cfg.dateRange) {
      wrap.insertAdjacentHTML('beforeend', `
        <label class="gs-daterange"><span>From</span>
          <input type="date" id="gsFrom" class="gs-select-sm"></label>
        <label class="gs-daterange"><span>To</span>
          <input type="date" id="gsTo" class="gs-select-sm"></label>`);
    }

    U.$('gsToggleControls').innerHTML = cfg.toggles.map((t) =>
      `<label class="gs-toggle"><input type="checkbox" id="gst_${t.name}"
         data-name="${U.esc(t.name)}"><span>${U.esc(t.label)}</span></label>`).join('');

    // Populate the foreign-key filters.
    cfg.filters.filter((f) => f.type === 'fk').forEach(async (f) => {
      const select = U.$('gsf_' + f.name);
      try {
        const options = await U.loadOptions(cfg.lookupBase, f.source, f.source_label);
        select.insertAdjacentHTML('beforeend', options.map((o) =>
          `<option value="${o.id}">${U.esc(o.label)}</option>`).join(''));
      } catch (e) { /* filter stays "all" */ }
    });

    wrap.addEventListener('change', (e) => {
      const el = e.target.closest('.gs-report-filter');
      if (el) {
        state.filters[el.dataset.name] = el.value;
        state.page = 1;
        load();
        return;
      }
      if (e.target.id === 'gsFrom') { state.from = e.target.value; state.page = 1; load(); }
      if (e.target.id === 'gsTo') { state.to = e.target.value; state.page = 1; load(); }
    });

    U.$('gsToggleControls').addEventListener('change', (e) => {
      const el = e.target.closest('input[type=checkbox]');
      if (!el) return;
      state.toggles[el.dataset.name] = el.checked;
      state.page = 1;
      load();
    });
  }

  function resetFilters() {
    state.filters = {}; state.toggles = {};
    state.from = ''; state.to = ''; state.search = ''; state.page = 1;
    U.$('gsSearch').value = '';
    U.$('gsSearchClear').hidden = true;
    document.querySelectorAll('.gs-report-filter').forEach((s) => { s.value = ''; });
    document.querySelectorAll('#gsToggleControls input').forEach((c) => {
      c.checked = false;
    });
    if (U.$('gsFrom')) U.$('gsFrom').value = '';
    if (U.$('gsTo')) U.$('gsTo').value = '';
    load();
  }

  /* ── table ── */

  function renderHead() {
    // Aggregate reports have no model column to order by, so their headers
    // are plain labels rather than dead sort controls.
    const sortable = cfg.sortable !== false;

    U.$('gsTblHead').innerHTML = '<tr>' + cfg.columns.map((col) => {
      const width = col.width ? ` style="width:${col.width}"` : '';
      const align = U.alignClass(col);
      if (!sortable) {
        return `<th class="${align}"${width}>${U.esc(col.label)}</th>`;
      }
      const sorted = state.ordering === col.key || state.ordering === '-' + col.key;
      const dir = state.ordering === '-' + col.key ? 'down' : 'up';
      return `<th class="gs-sortable ${align}${sorted ? ' gs-sorted' : ''}"
                data-key="${U.esc(col.key)}"${width}>${U.esc(col.label)}
                <i class="fas fa-sort${sorted ? '-' + dir : ''} gs-sort-ind"></i></th>`;
    }).join('') + '</tr>';

    if (!sortable) return;

    U.$('gsTblHead').querySelectorAll('.gs-sortable').forEach((th) => {
      th.addEventListener('click', () => {
        const key = th.dataset.key;
        state.ordering = state.ordering === key ? '-' + key : key;
        state.page = 1;
        renderHead();
        load();
      });
    });
  }

  function renderRows() {
    const body = U.$('gsTblBody');
    const span = cfg.columns.length;

    if (!state.rows.length) {
      body.innerHTML = `<tr><td class="gs-empty" colspan="${span}">
        <i class="fas fa-inbox"></i>
        Nothing to show. Post a goods receipt to start building stock.</td></tr>`;
      return;
    }

    body.innerHTML = state.rows.map((row) => '<tr>' + cfg.columns.map((col, i) => {
      const cls = [U.alignClass(col), i === 0 ? 'gs-cell-code' : '']
        .filter(Boolean).join(' ');
      return `<td class="${cls}">${U.cell(row, col)}</td>`;
    }).join('') + '</tr>').join('');
  }

  /* ── KPIs (Current Stock only) ── */

  async function loadKpis() {
    if (cfg.slug !== 'stock') return;
    try {
      const s = await U.api(cfg.apiBase + 'summary/');
      U.$('gsKpis').innerHTML = `
        <div class="lm-hstat"><div class="lm-hstat-val">${U.money(s.total_value)}</div>
          <div class="lm-hstat-lbl">Stock Value</div></div>
        <div class="lm-hstat"><div class="lm-hstat-val">${s.low_stock_items}</div>
          <div class="lm-hstat-lbl">Low Stock</div></div>
        <div class="lm-hstat"><div class="lm-hstat-val">${s.out_of_stock_items}</div>
          <div class="lm-hstat-lbl">Out of Stock</div></div>
        <div class="lm-hstat"><div class="lm-hstat-val">${s.expiring.within_30_days}</div>
          <div class="lm-hstat-lbl">Expiring ≤30d</div></div>`;
    } catch (e) { /* KPIs are a bonus; the table is the page */ }
  }

  async function load() {
    const span = cfg.columns.length;
    U.$('gsTblBody').innerHTML =
      `<tr><td class="gs-loading" colspan="${span}">
         <span class="gs-spinner"></span> Loading…</td></tr>`;
    try {
      const data = await U.api(url());
      state.rows = data.results || [];
      state.count = data.count || 0;
      renderRows();
      const rowsEl = U.$('statRows');
      if (rowsEl) rowsEl.textContent = state.count;
      U.$('gsRecordCount').textContent =
        `${state.count} row${state.count === 1 ? '' : 's'}`;
      U.renderPagination(state, (p) => { state.page = p; load(); });
    } catch (err) {
      U.$('gsTblBody').innerHTML =
        `<tr><td class="gs-empty" colspan="${span}">
           <i class="fas fa-triangle-exclamation"></i>
           ${U.esc(U.errorMessage(err, 'Could not load this report.'))}</td></tr>`;
      U.$('gsRecordCount').textContent = 'Error';
    }
  }

  document.addEventListener('DOMContentLoaded', function () {
    buildControls();
    renderHead();
    load();
    loadKpis();

    const search = U.$('gsSearch');
    const clear = U.$('gsSearchClear');

    search.addEventListener('input', U.debounce(function () {
      state.search = this.value.trim();
      state.page = 1;
      clear.hidden = !this.value;
      load();
    }, 300));

    clear.addEventListener('click', function () {
      search.value = ''; clear.hidden = true;
      state.search = ''; state.page = 1; load();
    });

    U.$('gsRefreshBtn').addEventListener('click', () => { load(); loadKpis(); });
    U.$('gsResetBtn').addEventListener('click', resetFilters);
  });
})();
