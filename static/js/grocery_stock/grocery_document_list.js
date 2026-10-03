/* ═══════════════════════════════════════════════════════════════════════
   grocery_document_list.js — list screen for GRN and Stock Issue.
   Driven by GS_LIST, built from DOCUMENT_SPECS in grocery_stock/pages_txn.py.
═══════════════════════════════════════════════════════════════════════ */
(function () {
  'use strict';

  if (typeof GS_LIST === 'undefined') return;

  const U = GSUtil;
  const cfg = GS_LIST;

  const state = {
    page: 1, pageSize: 50, count: 0,
    search: '', status: '', from: '', to: '',
    ordering: '', rows: [],
  };

  function detailUrl(id) {
    // Django reverses the route with a placeholder 0; swap in the real id.
    return cfg.detailUrlTemplate.replace(/0\/$/, id + '/');
  }

  function listUrl() {
    const p = new URLSearchParams({ page: state.page });
    if (state.search) p.set('search', state.search);
    if (state.status) p.set('status', state.status);
    if (state.from) p.set('from', state.from);
    if (state.to) p.set('to', state.to);
    if (state.ordering) p.set('ordering', state.ordering);
    return cfg.apiBase + '?' + p.toString();
  }

  function renderHead() {
    U.$('gsTblHead').innerHTML = '<tr>' + cfg.columns.map((col) => {
      const width = col.width ? ` style="width:${col.width}"` : '';
      return `<th${width}>${U.esc(col.label)}</th>`;
    }).join('') + '<th style="width:60px"></th></tr>';
  }

  function renderRows() {
    const body = U.$('gsTblBody');
    const span = cfg.columns.length + 1;

    if (!state.rows.length) {
      body.innerHTML = `<tr><td class="gs-empty" colspan="${span}">
        <i class="fas fa-inbox"></i>
        ${state.search || state.status || state.from || state.to
          ? 'No documents match your filters.'
          : 'Nothing here yet.'}</td></tr>`;
      return;
    }

    body.innerHTML = state.rows.map((row) => {
      const cells = cfg.columns.map((col, i) => {
        // A draft has no number yet — show what it is rather than an em dash.
        if (col.key === cfg.numberField && !row[col.key]) {
          return `<td class="gs-cell-code gs-cell-muted">Draft #${row.id}</td>`;
        }
        const cls = [U.alignClass(col), i === 0 ? 'gs-cell-code' : '']
          .filter(Boolean).join(' ');
        return `<td class="${cls}">${U.cell(row, col)}</td>`;
      }).join('');

      return `<tr class="gs-row-link" data-id="${row.id}">${cells}
        <td><a class="gs-icon-btn" href="${detailUrl(row.id)}" title="Open">
          <i class="fas fa-arrow-right"></i></a></td></tr>`;
    }).join('');

    body.querySelectorAll('.gs-row-link').forEach((tr) => {
      tr.addEventListener('click', (e) => {
        if (e.target.closest('a')) return;   // let the explicit link work
        window.location.href = detailUrl(tr.dataset.id);
      });
    });
  }

  async function loadStats() {
    try {
      const base = new URLSearchParams();
      if (state.search) base.set('search', state.search);
      const mk = (s) => {
        const p = new URLSearchParams(base);
        p.set('status', s);
        return cfg.apiBase + '?' + p.toString();
      };
      const [draft, posted] = await Promise.all([
        U.api(mk('DRAFT')), U.api(mk('POSTED')),
      ]);
      U.$('statDraft').textContent = draft.count;
      U.$('statPosted').textContent = posted.count;
    } catch (e) {
      U.$('statDraft').textContent = '—';
      U.$('statPosted').textContent = '—';
    }
  }

  async function load() {
    const span = cfg.columns.length + 1;
    U.$('gsTblBody').innerHTML =
      `<tr><td class="gs-loading" colspan="${span}">
         <span class="gs-spinner"></span> Loading…</td></tr>`;
    try {
      const data = await U.api(listUrl());
      state.rows = data.results || [];
      state.count = data.count || 0;
      renderRows();
      U.$('statTotal').textContent = state.count;
      U.$('gsRecordCount').textContent =
        `${state.count} document${state.count === 1 ? '' : 's'}`;
      U.renderPagination(state, (p) => { state.page = p; load(); });
      loadStats();
    } catch (err) {
      U.$('gsTblBody').innerHTML =
        `<tr><td class="gs-empty" colspan="${span}">
           <i class="fas fa-triangle-exclamation"></i>
           ${U.esc(U.errorMessage(err, 'Could not load documents.'))}</td></tr>`;
      U.$('gsRecordCount').textContent = 'Error';
    }
  }

  document.addEventListener('DOMContentLoaded', function () {
    renderHead();
    load();

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

    U.$('gsStatusPills').addEventListener('click', function (e) {
      const pill = e.target.closest('.gs-pill');
      if (!pill) return;
      this.querySelectorAll('.gs-pill').forEach((p) => p.classList.remove('active'));
      pill.classList.add('active');
      state.status = pill.dataset.status;
      state.page = 1;
      load();
    });

    ['gsFrom', 'gsTo'].forEach((id) => {
      U.$(id).addEventListener('change', function () {
        state[id === 'gsFrom' ? 'from' : 'to'] = this.value;
        state.page = 1;
        load();
      });
    });

    U.$('gsRefreshBtn').addEventListener('click', load);
  });
})();
