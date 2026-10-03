/* ═══════════════════════════════════════════════════════════════════════
   grocery_common.js — helpers shared by the grocery transaction screens.

   Loaded before grocery_document_list.js / grocery_document_form.js /
   grocery_report.js. Everything that all three need — fetch with CSRF, cell
   formatting, pagination, dropdown loading — lives here so the formatting of
   a quantity or a status badge cannot drift between screens.
═══════════════════════════════════════════════════════════════════════ */
window.GSUtil = (function () {
  'use strict';

  const $ = (id) => document.getElementById(id);

  function esc(value) {
    if (value === null || value === undefined) return '';
    return String(value).replace(/[&<>"']/g, (c) => (
      { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]
    ));
  }

  function csrfToken() {
    const m = document.cookie.match(/(?:^|;\s*)csrftoken=([^;]+)/);
    return m ? decodeURIComponent(m[1]) : '';
  }

  /** fetch + JSON + CSRF. Throws an Error carrying .status and .body. */
  async function api(url, options) {
    const opts = Object.assign({ headers: {} }, options || {});
    opts.credentials = 'same-origin';
    opts.headers['X-Requested-With'] = 'XMLHttpRequest';
    if (opts.method && opts.method !== 'GET') {
      opts.headers['X-CSRFToken'] = csrfToken();
      opts.headers['Content-Type'] = 'application/json';
    }

    const response = await fetch(url, opts);
    if (response.status === 204) return null;

    let body = null;
    try { body = await response.json(); } catch (e) { /* empty or non-JSON */ }

    if (!response.ok) {
      const err = new Error('request failed');
      err.status = response.status;
      err.body = body;
      throw err;
    }
    return body;
  }

  /**
   * Pull a readable message out of a DRF error body.
   * Handles the module's {error:{message}} envelope, DRF's {detail}, and
   * per-field {field: [msg]} — a screen should never show "[object Object]".
   */
  function errorMessage(err, fallback) {
    const body = err && err.body;
    if (!body) return fallback || 'Something went wrong. Please try again.';
    if (body.error && body.error.message) return body.error.message;
    if (body.detail) return body.detail;

    const parts = [];
    Object.keys(body).forEach((key) => {
      const value = body[key];
      if (Array.isArray(value)) parts.push(`${key}: ${value.join(' ')}`);
      else if (typeof value === 'string') parts.push(`${key}: ${value}`);
    });
    return parts.length ? parts.join(' · ')
                        : (fallback || 'Something went wrong.');
  }

  let toastTimer = null;
  function toast(message, isError) {
    const el = $('gsToast');
    if (!el) return;
    el.querySelector('.toast-msg').textContent = message;
    el.querySelector('i').className =
      isError ? 'fas fa-circle-exclamation' : 'fas fa-check-circle';
    el.style.background = isError ? '#ef4444' : '';
    el.classList.add('show');
    clearTimeout(toastTimer);
    toastTimer = setTimeout(() => el.classList.remove('show'), 3600);
  }

  /* ── formatting ── */

  // DRF serialises decimals as strings to protect precision, so parse only at
  // the point of display and never round-trip a float back to the server.
  const num = (v) => (v === null || v === undefined || v === '' ? null : Number(v));

  function money(value) {
    const n = num(value);
    if (n === null) return '—';
    return n.toLocaleString('en-IN', { minimumFractionDigits: 2,
                                       maximumFractionDigits: 2 });
  }

  function qty(value) {
    const n = num(value);
    if (n === null) return '—';
    return n.toLocaleString('en-IN', { minimumFractionDigits: 0,
                                       maximumFractionDigits: 3 });
  }

  function dateText(value) {
    if (!value) return '—';
    const d = new Date(value);
    if (isNaN(d)) return esc(value);
    return d.toLocaleDateString('en-IN', { day: '2-digit', month: 'short',
                                           year: 'numeric' });
  }

  function today() {
    const d = new Date();
    return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}-` +
           `${String(d.getDate()).padStart(2, '0')}`;
  }

  const STATUS_CLASS = {
    DRAFT: 'gs-badge-draft', POSTED: 'gs-badge-on', CANCELLED: 'gs-badge-off',
  };
  const STOCK_CLASS = {
    OK: 'gs-badge-on', LOW: 'gs-badge-warn', OUT_OF_STOCK: 'gs-badge-danger',
  };
  const STOCK_LABEL = {
    OK: 'Healthy', LOW: 'Low', OUT_OF_STOCK: 'Out of stock',
  };
  // Section 13 alert bands.
  const EXPIRY_CLASS = {
    EXPIRED: 'gs-badge-danger', CRITICAL: 'gs-badge-danger',
    WARNING: 'gs-badge-warn', NOTICE: 'gs-badge-warn',
    OK: 'gs-badge-on', NONE: 'gs-badge-off',
  };
  const EXPIRY_LABEL = {
    EXPIRED: 'Expired', CRITICAL: '≤ 30 days', WARNING: '≤ 60 days',
    NOTICE: '≤ 90 days', OK: 'Fine', NONE: 'No expiry',
  };

  /** Render one table cell for a column spec. Shared by every grocery table. */
  function cell(row, col) {
    const raw = row[col.key];

    switch (col.type) {
      case 'status': {
        const value = raw || 'DRAFT';
        const label = row.status_display || value;
        return `<span class="gs-badge ${STATUS_CLASS[value] || 'gs-badge-off'}">${esc(label)}</span>`;
      }
      case 'stock_status':
        return `<span class="gs-badge ${STOCK_CLASS[raw] || 'gs-badge-off'}">` +
               `${esc(STOCK_LABEL[raw] || raw || '—')}</span>`;
      case 'expiry_status':
        return `<span class="gs-badge ${EXPIRY_CLASS[raw] || 'gs-badge-off'}">` +
               `${esc(EXPIRY_LABEL[raw] || raw || '—')}</span>`;
      case 'movement': {
        const kind = row.movement_type || '';
        const tone = kind === 'RECEIPT' ? 'gs-badge-on'
                   : kind === 'ISSUE' ? 'gs-badge-warn' : 'gs-badge-off';
        return `<span class="gs-badge ${tone}">${esc(raw || kind || '—')}</span>`;
      }
      case 'signed': {
        const n = num(raw);
        if (n === null) return '<span class="gs-cell-muted">—</span>';
        const cls = n < 0 ? 'gs-neg' : 'gs-pos';
        return `<span class="${cls}">${n > 0 ? '+' : ''}${qty(raw)}</span>`;
      }
      case 'signed_money': {
        const n = num(raw);
        if (n === null) return '<span class="gs-cell-muted">—</span>';
        const cls = n < 0 ? 'gs-neg' : 'gs-pos';
        return `<span class="${cls}">${n > 0 ? '+' : ''}${money(raw)}</span>`;
      }
      case 'money':
        return raw === null || raw === undefined || raw === ''
          ? '<span class="gs-cell-muted">—</span>' : money(raw);
      case 'qty':
        return raw === null || raw === undefined || raw === ''
          ? '<span class="gs-cell-muted">—</span>' : qty(raw);
      case 'date':
        return dateText(raw);
      case 'num':
        return raw === null || raw === undefined ? '<span class="gs-cell-muted">—</span>'
                                                 : esc(raw);
      case 'muted':
        return raw ? `<span class="gs-cell-muted">${esc(raw)}</span>`
                   : '<span class="gs-cell-muted">—</span>';
      default:
        return raw === null || raw === undefined || raw === ''
          ? '<span class="gs-cell-muted">—</span>' : esc(raw);
    }
  }

  function alignClass(col) {
    return ['num', 'money', 'qty', 'signed', 'signed_money'].includes(col.type)
      ? 'gs-cell-num' : '';
  }

  /* ── pagination ── */

  function renderPagination(state, onGo) {
    const total = Math.max(1, Math.ceil(state.count / state.pageSize));
    const btns = $('gsPagiBtns');
    const info = $('gsPagiInfo');

    if (info) {
      const from = state.count === 0 ? 0 : (state.page - 1) * state.pageSize + 1;
      const to = Math.min(state.page * state.pageSize, state.count);
      info.textContent = state.count ? `Showing ${from}–${to} of ${state.count}`
                                     : 'No records';
    }
    if (!btns) return;
    if (total <= 1) { btns.innerHTML = ''; return; }

    let html = `<button class="gs-pagi-btn" data-page="${state.page - 1}"
                 ${state.page <= 1 ? 'disabled' : ''}>‹</button>`;
    const start = Math.max(1, state.page - 2);
    const end = Math.min(total, start + 4);
    for (let p = start; p <= end; p++) {
      html += `<button class="gs-pagi-btn${p === state.page ? ' active' : ''}"
                data-page="${p}">${p}</button>`;
    }
    html += `<button class="gs-pagi-btn" data-page="${state.page + 1}"
              ${state.page >= total ? 'disabled' : ''}>›</button>`;

    btns.innerHTML = html;
    btns.querySelectorAll('[data-page]').forEach((b) => {
      b.addEventListener('click', () => {
        const p = parseInt(b.dataset.page, 10);
        if (p >= 1 && p <= total && p !== state.page) onGo(p);
      });
    });
  }

  /* ── dropdowns ── */

  const optionCache = {};

  /**
   * Load every active row of a master for a <select>, following DRF
   * pagination so a long list is not silently cut at page one.
   */
  async function loadOptions(lookupBase, endpoint, labelField) {
    const key = endpoint + '|' + labelField;
    if (optionCache[key]) return optionCache[key];

    const out = [];
    let url = lookupBase + endpoint + '/?is_active=true';
    while (url) {
      const data = await api(url);
      const rows = Array.isArray(data) ? data : (data.results || []);
      rows.forEach((row) => {
        let label;
        if (labelField === '__label__') {
          label = (row.warehouse_code ? row.warehouse_code + ' / ' : '') +
                  (row.code || row.name);
        } else {
          label = row[labelField] || row.name || row.code || ('#' + row.id);
          if (row.code && labelField !== 'code') label += ' (' + row.code + ')';
        }
        out.push({ id: row.id, label: label, row: row });
      });
      url = Array.isArray(data) ? null : data.next;
    }

    optionCache[key] = out;
    return out;
  }

  function clearOptionCache() {
    Object.keys(optionCache).forEach((k) => delete optionCache[k]);
  }

  function debounce(fn, ms) {
    let t;
    return function () {
      const args = arguments;
      clearTimeout(t);
      t = setTimeout(() => fn.apply(null, args), ms);
    };
  }

  return {
    $: $, esc: esc, api: api, csrfToken: csrfToken, errorMessage: errorMessage,
    toast: toast, money: money, qty: qty, dateText: dateText, today: today,
    num: num, cell: cell, alignClass: alignClass,
    renderPagination: renderPagination, loadOptions: loadOptions,
    clearOptionCache: clearOptionCache, debounce: debounce,
  };
})();
