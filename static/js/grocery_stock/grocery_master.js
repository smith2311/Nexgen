/* ═══════════════════════════════════════════════════════════════════════
   grocery_master.js — one script for every grocery master page.

   Reads GS_CONFIG (built by templates/grocery_stock/master_page.html from
   grocery_stock/pages.py) and drives list / create / update / delete against
   the DRF endpoints in grocery_stock/views.py. Adding a master needs no
   change here — only a new entry in MASTER_SPECS.

   Auth is the browser session, so every unsafe request carries the CSRF
   token from the cookie. Reuses the shared Modal component.
═══════════════════════════════════════════════════════════════════════ */
(function () {
  'use strict';

  if (typeof GS_CONFIG === 'undefined') return;

  const cfg = GS_CONFIG;

  /* ── state ── */
  const state = {
    page: 1,
    pageSize: 50,
    search: '',
    status: '',        // '' | 'true' | 'false'
    ordering: '',
    count: 0,
    rows: [],
    editingId: null,   // null = creating
    deletingRow: null,
    fkOptions: {},     // endpoint -> [{id, label}]
  };

  /* ── tiny helpers ── */
  const $ = (id) => document.getElementById(id);

  function esc(v) {
    if (v === null || v === undefined) return '';
    return String(v).replace(/[&<>"']/g, (c) => (
      { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]
    ));
  }

  function csrfToken() {
    const m = document.cookie.match(/(?:^|;\s*)csrftoken=([^;]+)/);
    return m ? decodeURIComponent(m[1]) : '';
  }

  let toastTimer = null;
  function toast(message, isError) {
    const el = $('gsToast');
    if (!el) return;
    el.querySelector('.toast-msg').textContent = message;
    const icon = el.querySelector('i');
    icon.className = isError ? 'fas fa-circle-exclamation' : 'fas fa-check-circle';
    el.style.background = isError ? '#ef4444' : '';
    el.classList.add('show');
    clearTimeout(toastTimer);
    toastTimer = setTimeout(() => el.classList.remove('show'), 3200);
  }

  /* ── API ── */
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

  /* ═══ LIST ═══════════════════════════════════════════════════════════ */

  function listUrl() {
    const params = new URLSearchParams();
    params.set('page', state.page);
    if (state.search) params.set('search', state.search);
    if (state.status) params.set('is_active', state.status);
    if (state.ordering) params.set('ordering', state.ordering);
    return cfg.apiBase + '?' + params.toString();
  }

  async function loadList() {
    renderLoading();
    try {
      const data = await api(listUrl());
      // DRF pagination wraps results; a non-paginated endpoint returns a list.
      state.rows = Array.isArray(data) ? data : (data.results || []);
      state.count = Array.isArray(data) ? data.length : (data.count || 0);
      renderRows();
      renderPagination();
      renderCount();
      loadStats();
    } catch (err) {
      renderError(err);
    }
  }

  /**
   * Header counters. DRF gives the total in `count`, so two cheap HEAD-ish
   * requests for active/inactive are enough — no extra endpoint needed.
   */
  async function loadStats() {
    $('statTotal').textContent = state.count;
    try {
      const base = new URLSearchParams();
      if (state.search) base.set('search', state.search);

      const activeParams = new URLSearchParams(base); activeParams.set('is_active', 'true');
      const inactiveParams = new URLSearchParams(base); inactiveParams.set('is_active', 'false');

      const [active, inactive] = await Promise.all([
        api(cfg.apiBase + '?' + activeParams.toString()),
        api(cfg.apiBase + '?' + inactiveParams.toString()),
      ]);
      $('statActive').textContent = active.count !== undefined ? active.count : '—';
      $('statInactive').textContent = inactive.count !== undefined ? inactive.count : '—';
    } catch (e) {
      $('statActive').textContent = '—';
      $('statInactive').textContent = '—';
    }
  }

  /* ═══ RENDER ═════════════════════════════════════════════════════════ */

  function colSpan() { return cfg.columns.length + 1; }

  function renderHead() {
    const head = $('gsTblHead');
    const cells = cfg.columns.map((col) => {
      const sorted = state.ordering === col.key || state.ordering === '-' + col.key;
      const dir = state.ordering === '-' + col.key ? 'down' : 'up';
      const width = col.width ? ` style="width:${col.width}"` : '';
      return `<th class="gs-sortable${sorted ? ' gs-sorted' : ''}" data-key="${esc(col.key)}"${width}>`
           + esc(col.label)
           + `<i class="fas fa-sort${sorted ? '-' + dir : ''} gs-sort-ind"></i></th>`;
    }).join('');
    head.innerHTML = `<tr>${cells}<th style="width:96px;text-align:right">Actions</th></tr>`;

    head.querySelectorAll('.gs-sortable').forEach((th) => {
      th.addEventListener('click', () => {
        const key = th.dataset.key;
        state.ordering = state.ordering === key ? '-' + key : key;
        state.page = 1;
        renderHead();
        loadList();
      });
    });
  }

  function cellHtml(row, col) {
    const raw = row[col.key];
    if (col.type === 'bool') {
      return raw
        ? '<span class="gs-badge gs-badge-on">Active</span>'
        : '<span class="gs-badge gs-badge-off">Inactive</span>';
    }
    if (raw === null || raw === undefined || raw === '') {
      return '<span class="gs-cell-muted">—</span>';
    }
    if (col.type === 'num') return esc(raw);
    if (col.type === 'muted') return `<span class="gs-cell-muted">${esc(raw)}</span>`;
    return esc(raw);
  }

  function renderRows() {
    const body = $('gsTblBody');

    if (!state.rows.length) {
      body.innerHTML = `<tr><td class="gs-empty" colspan="${colSpan()}">
        <i class="fas fa-inbox"></i>
        ${state.search || state.status
          ? 'No records match your filters.'
          : 'Nothing here yet.' + (cfg.canWrite ? ' Use “Add” to create the first record.' : '')}
      </td></tr>`;
      return;
    }

    body.innerHTML = state.rows.map((row) => {
      const cells = cfg.columns.map((col, i) => {
        const cls = col.type === 'num' ? 'gs-cell-num' : (i === 0 ? 'gs-cell-code' : '');
        return `<td class="${cls}">${cellHtml(row, col)}</td>`;
      }).join('');

      const actions = cfg.canWrite
        ? `<button class="gs-icon-btn" data-act="edit" data-id="${row.id}" title="Edit">
             <i class="fas fa-pen"></i></button>
           <button class="gs-icon-btn gs-danger" data-act="del" data-id="${row.id}" title="Remove">
             <i class="fas fa-trash"></i></button>`
        : '<span class="gs-cell-muted">—</span>';

      return `<tr>${cells}<td><div class="gs-row-actions">${actions}</div></td></tr>`;
    }).join('');

    body.querySelectorAll('[data-act]').forEach((btn) => {
      btn.addEventListener('click', () => {
        const row = state.rows.find((r) => String(r.id) === btn.dataset.id);
        if (btn.dataset.act === 'edit') openEdit(row);
        else openDelete(row);
      });
    });
  }

  function renderLoading() {
    $('gsTblBody').innerHTML =
      `<tr><td class="gs-loading" colspan="${colSpan()}">
         <span class="gs-spinner"></span> Loading…</td></tr>`;
  }

  function renderError(err) {
    const message = err.status === 403
      ? 'You do not have permission to view this list.'
      : 'Could not load records. Check your connection and try again.';
    $('gsTblBody').innerHTML =
      `<tr><td class="gs-empty" colspan="${colSpan()}">
         <i class="fas fa-triangle-exclamation"></i>${esc(message)}</td></tr>`;
    $('gsRecordCount').textContent = 'Error';
  }

  function renderCount() {
    const from = state.count === 0 ? 0 : (state.page - 1) * state.pageSize + 1;
    const to = Math.min(state.page * state.pageSize, state.count);
    $('gsRecordCount').textContent = `${state.count} record${state.count === 1 ? '' : 's'}`;
    $('gsPagiInfo').textContent = state.count
      ? `Showing ${from}–${to} of ${state.count}`
      : 'No records';
  }

  function renderPagination() {
    const total = Math.max(1, Math.ceil(state.count / state.pageSize));
    const btns = $('gsPagiBtns');

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
        if (p >= 1 && p <= total && p !== state.page) { state.page = p; loadList(); }
      });
    });
  }

  /* ═══ FOREIGN-KEY DROPDOWNS ══════════════════════════════════════════ */

  /**
   * Pull every option for an fk field. Active only — an inactive master should
   * not be selectable on a new record, and the API rejects an inactive bin
   * anyway.
   */
  async function loadFkOptions(field) {
    const key = field.source;
    if (state.fkOptions[key]) return state.fkOptions[key];

    const options = [];
    let url = cfg.lookupBase + key + '/?is_active=true';
    // Follow DRF pagination so a long master is not silently truncated.
    while (url) {
      const data = await api(url);
      const rows = Array.isArray(data) ? data : (data.results || []);
      rows.forEach((row) => {
        let label;
        if (field.source_label === '__label__') {
          label = (row.warehouse_code ? row.warehouse_code + ' / ' : '') + (row.code || row.name);
        } else {
          label = row[field.source_label] || row.name || row.code || ('#' + row.id);
          if (row.code && field.source_label !== 'code') label += ' (' + row.code + ')';
        }
        options.push({ id: row.id, label: label });
      });
      url = Array.isArray(data) ? null : data.next;
    }

    state.fkOptions[key] = options;
    return options;
  }

  /* ═══ FORM ═══════════════════════════════════════════════════════════ */

  function buildForm() {
    const grid = $('gsFormGrid');
    grid.innerHTML = cfg.fields.map((field) => {
      const parts = [];

      if (field.section) {
        parts.push(`<div class="gs-form-section span-2">${esc(field.section)}</div>`);
      }

      const span = field.span === 2 || field.type === 'textarea' ? ' span-2' : '';
      const req = field.required ? '<span class="req">*</span>' : '';
      const help = field.help ? `<div class="form-hint">${esc(field.help)}</div>` : '';
      const id = 'gsf_' + field.name;

      let control;
      switch (field.type) {
        case 'textarea':
          control = `<textarea class="form-textarea" id="${id}" name="${esc(field.name)}"
                       placeholder="${esc(field.placeholder || '')}"></textarea>`;
          break;
        case 'checkbox':
          control = `<label class="gs-check" for="${id}">
                       <input type="checkbox" id="${id}" name="${esc(field.name)}">
                       <span>${esc(field.label)}</span></label>`;
          break;
        case 'select':
          control = `<select class="form-select" id="${id}" name="${esc(field.name)}">`
                  + (field.required ? '' : '<option value="">— none —</option>')
                  + (field.choices || []).map((c) =>
                      `<option value="${esc(c.value)}">${esc(c.label)}</option>`).join('')
                  + '</select>';
          break;
        case 'fk':
          control = `<select class="form-select gs-fk" id="${id}" name="${esc(field.name)}"
                       data-source="${esc(field.source)}">
                       <option value="">Loading…</option></select>`;
          break;
        case 'number':
        case 'decimal':
          control = `<input type="number" class="form-input" id="${id}" name="${esc(field.name)}"
                       ${field.step ? `step="${esc(field.step)}"` : 'step="1"'}
                       placeholder="${esc(field.placeholder || '')}">`;
          break;
        case 'email':
          control = `<input type="email" class="form-input" id="${id}" name="${esc(field.name)}"
                       placeholder="${esc(field.placeholder || '')}">`;
          break;
        default:
          control = `<input type="text" class="form-input" id="${id}" name="${esc(field.name)}"
                       ${field.maxlength ? `maxlength="${field.maxlength}"` : ''}
                       ${field.uppercase ? 'data-uppercase="1"' : ''}
                       autocomplete="off"
                       placeholder="${esc(field.placeholder || '')}">`;
      }

      // A checkbox carries its own label, so it does not get a second one.
      const label = field.type === 'checkbox'
        ? ''
        : `<label class="form-label" for="${id}">${esc(field.label)}${req}</label>`;

      parts.push(`<div class="form-field${span}" data-field="${esc(field.name)}">
                    ${label}${control}${help}
                    <div class="form-error" data-err="${esc(field.name)}"></div>
                  </div>`);

      return parts.join('');
    }).join('');

    // Uppercase-as-you-type for code fields, matching what the API stores.
    grid.querySelectorAll('[data-uppercase]').forEach((input) => {
      input.addEventListener('input', function () {
        const pos = this.selectionStart;
        this.value = this.value.toUpperCase();
        this.setSelectionRange(pos, pos);
      });
    });

    // Populate every fk dropdown.
    cfg.fields.filter((f) => f.type === 'fk').forEach(async (field) => {
      const select = $('gsf_' + field.name);
      try {
        const options = await loadFkOptions(field);
        select.innerHTML = (field.required ? '<option value="">— select —</option>'
                                           : '<option value="">— none —</option>')
          + options.map((o) => `<option value="${o.id}">${esc(o.label)}</option>`).join('');
        if (select.dataset.pending) {
          select.value = select.dataset.pending;
          delete select.dataset.pending;
        }
      } catch (e) {
        select.innerHTML = '<option value="">Could not load options</option>';
      }
    });
  }

  function clearFieldErrors() {
    $('gsFormError').hidden = true;
    $('gsFormErrorText').textContent = '';
    document.querySelectorAll('[data-err]').forEach((el) => { el.textContent = ''; });
    // .error is the shared invalid state on .form-input / .form-select.
    document.querySelectorAll('.error').forEach((el) => el.classList.remove('error'));
  }

  function showFormError(message) {
    $('gsFormErrorText').textContent = message;
    $('gsFormError').hidden = false;
  }

  function fillForm(record) {
    cfg.fields.forEach((field) => {
      const input = $('gsf_' + field.name);
      if (!input) return;
      const value = record ? record[field.name] : field.default;

      if (field.type === 'checkbox') {
        input.checked = value === undefined || value === null
          ? Boolean(field.default) : Boolean(value);
      } else if (field.type === 'fk') {
        const v = value === null || value === undefined ? '' : String(value);
        // Options may still be loading; stash the value for the loader.
        if (input.options.length <= 1) input.dataset.pending = v;
        else input.value = v;
      } else {
        input.value = value === null || value === undefined ? '' : value;
      }
    });
  }

  function collectForm() {
    const payload = {};
    cfg.fields.forEach((field) => {
      const input = $('gsf_' + field.name);
      if (!input) return;

      if (field.type === 'checkbox') {
        payload[field.name] = input.checked;
        return;
      }

      const value = input.value.trim();

      if (value === '') {
        // Blank fk / optional number must be null, not '' — DRF rejects ''
        // for a nullable integer or decimal.
        if (field.type === 'fk' || field.type === 'number' || field.type === 'decimal') {
          payload[field.name] = null;
        } else {
          payload[field.name] = '';
        }
        return;
      }
      payload[field.name] = value;
    });
    return payload;
  }

  function showFieldErrors(body) {
    if (!body || typeof body !== 'object') {
      showFormError('Could not save. Please try again.');
      return;
    }

    const general = [];
    Object.keys(body).forEach((key) => {
      const messages = Array.isArray(body[key]) ? body[key].join(' ') : String(body[key]);
      const slot = document.querySelector(`[data-err="${CSS.escape(key)}"]`);
      if (slot) {
        slot.textContent = messages;
        const input = $('gsf_' + key);
        if (input) input.classList.add('error');
      } else {
        general.push(key === 'detail' || key === 'non_field_errors'
          ? messages : `${key}: ${messages}`);
      }
    });

    if (general.length) showFormError(general.join(' '));
  }

  /* ═══ MODAL ACTIONS ══════════════════════════════════════════════════ */

  function openAdd() {
    state.editingId = null;
    clearFieldErrors();
    fillForm(null);
    $('gsModalTitle').textContent = 'Add ' + cfg.title.replace(' Master', '');
    $('gsModalSubtitle').textContent = 'Create a new record';
    Modal.open('gsEditOverlay');
  }

  async function openEdit(row) {
    if (!row) return;
    state.editingId = row.id;
    clearFieldErrors();
    $('gsModalTitle').textContent = 'Edit ' + cfg.title.replace(' Master', '');
    $('gsModalSubtitle').textContent = 'Update this record';
    Modal.open('gsEditOverlay');

    // The list payload is trimmed on some masters (items in particular), so
    // always read the detail route before populating the form.
    try {
      const full = await api(cfg.apiBase + row.id + '/');
      fillForm(full);
    } catch (e) {
      fillForm(row);
    }
  }

  async function save() {
    clearFieldErrors();
    const btn = $('gsSaveBtn');
    const original = btn.innerHTML;
    btn.disabled = true;
    btn.innerHTML = '<span class="gs-spinner"></span> Saving…';

    try {
      const payload = collectForm();
      const editing = state.editingId !== null;
      await api(editing ? cfg.apiBase + state.editingId + '/' : cfg.apiBase, {
        method: editing ? 'PATCH' : 'POST',
        body: JSON.stringify(payload),
      });

      Modal.close('gsEditOverlay');
      toast(editing ? 'Record updated' : 'Record created');
      // A new code may sort anywhere, so reload rather than patching in place.
      loadList();
    } catch (err) {
      if (err.status === 400) showFieldErrors(err.body);
      else if (err.status === 403) {
        showFormError((err.body && err.body.detail)
          || 'Your role does not allow this action.');
      } else {
        showFormError('Could not save. Please try again.');
      }
    } finally {
      btn.disabled = false;
      btn.innerHTML = original;
    }
  }

  function openDelete(row) {
    if (!row) return;
    state.deletingRow = row;

    const summary = cfg.columns.slice(0, 3).map((col) => {
      const value = row[col.key];
      const shown = col.type === 'bool'
        ? (value ? 'Active' : 'Inactive')
        : (value === null || value === undefined || value === '' ? '—' : value);
      return `<div class="lmodal-record-row">
                <span class="lmodal-record-lbl">${esc(col.label)}</span>
                <span class="lmodal-record-val">${esc(shown)}</span></div>`;
    }).join('');

    $('gsDeleteRecord').innerHTML = summary;
    Modal.open('gsDeleteOverlay');
  }

  async function confirmDelete() {
    if (!state.deletingRow) return;
    const btn = $('gsDeleteConfirm');
    const original = btn.innerHTML;
    btn.disabled = true;
    btn.innerHTML = '<span class="gs-spinner"></span> Removing…';

    try {
      await api(cfg.apiBase + state.deletingRow.id + '/', { method: 'DELETE' });
      Modal.close('gsDeleteOverlay');
      toast('Record removed');
      // Stepping back avoids landing on a page that no longer exists.
      if (state.rows.length === 1 && state.page > 1) state.page -= 1;
      loadList();
    } catch (err) {
      Modal.close('gsDeleteOverlay');
      toast(err.status === 403
        ? 'Your role does not allow removing this record.'
        : 'Could not remove the record.', true);
    } finally {
      btn.disabled = false;
      btn.innerHTML = original;
      state.deletingRow = null;
    }
  }

  /* ═══ BULK UPLOAD — mirrors the logistics master pages ═══════════════
     Tab 2 uploads a CSV/XLSX to <master>/upload/, which parses it into a
     session-held preview. Tab 3 shows that preview (via the shared
     LogiTable data table) and Confirm Import posts the token back. The
     drop zone, file chip, schema table and preview stats reuse the shared
     data_table component classes, so the look is identical to logistics. */

  const up = { preview: null, table: null };

  // Example values for fk columns in the sample CSV — match the seed data.
  const FK_SAMPLE = {
    categories: 'CEREALS', brands: 'TATA', units: 'KG',
    warehouses: 'WH-MAIN', locations: 'WH-MAIN/A-01',
  };

  function fkDesc(field) {
    if (field.source === 'locations') return 'Bin, written as WAREHOUSE/BIN code';
    return field.label + ' — the code from the ' + field.source + ' master';
  }

  function schemaDesc(field) {
    if (field.type === 'fk') return fkDesc(field);
    if (field.type === 'checkbox') return field.label + ' — true / false';
    if (field.type === 'select') {
      return field.label + ' — one of: '
        + (field.choices || []).map((c) => c.value).join(', ');
    }
    if (field.type === 'number' || field.type === 'decimal') {
      return field.label + ' — number' + (field.placeholder ? ' (e.g. ' + field.placeholder + ')' : '');
    }
    return field.label + (field.placeholder ? ' (e.g. ' + field.placeholder + ')' : '');
  }

  function sampleValue(field) {
    if (field.type === 'fk') return FK_SAMPLE[field.source] || '';
    if (field.type === 'checkbox') return String(Boolean(field.default));
    if (field.type === 'select') {
      return field.choices && field.choices.length ? field.choices[0].value : '';
    }
    if (field.type === 'number' || field.type === 'decimal') {
      return field.default !== undefined && field.default !== null
        ? String(field.default) : '';
    }
    return field.placeholder || '';
  }

  function csvCell(value) {
    return /[",\n]/.test(value) ? '"' + value.replace(/"/g, '""') + '"' : value;
  }

  function buildSchemaTable() {
    const el = $('gsSchemaTable');
    if (!el) return;
    const head = '<div class="schema-row" style="background:var(--bg);font-size:11px;'
      + 'font-weight:600;text-transform:uppercase;letter-spacing:0.05em;color:var(--text-muted)">'
      + '<div>Column</div><div>Type</div><div>Description</div></div>';
    const rows = cfg.fields.map((field) => {
      const badge = field.required
        ? '<span class="badge badge-red" style="font-size:10px">Required</span>'
        : '<span class="badge badge-blue" style="font-size:10px">Optional</span>';
      return '<div class="schema-row">'
        + '<div class="schema-col">' + esc(field.name) + '</div>'
        + '<div>' + badge + '</div>'
        + '<div class="schema-desc">' + esc(schemaDesc(field)) + '</div></div>';
    }).join('');
    el.insertAdjacentHTML('beforeend', head + rows);
  }

  function buildSamplePreview() {
    const el = $('gsSamplePreview');
    if (!el) return;
    const header = cfg.fields.map((f) => f.name).join(',');
    const sample = cfg.fields.map((f) => csvCell(sampleValue(f))).join(',');
    el.innerHTML = '<span class="csv-hdr">' + esc(header) + '</span>\n' + esc(sample);
  }

  window.downloadSample = function () {
    const header = cfg.fields.map((f) => f.name).join(',');
    const sample = cfg.fields.map((f) => csvCell(sampleValue(f))).join(',');
    const a = document.createElement('a');
    a.href = 'data:text/csv,' + encodeURIComponent(header + '\n' + sample + '\n');
    a.download = cfg.slug + '_sample.csv';
    a.click();
    toast('Sample CSV downloaded');
  };

  /* ── tabs ── */

  function showTab(tabId) {
    document.querySelectorAll('.lm-tab-btn').forEach((b) => {
      b.classList.toggle('active', b.dataset.tab === tabId);
    });
    document.querySelectorAll('.lm-tab-content').forEach((c) => {
      c.classList.toggle('active', c.id === tabId);
    });
  }

  window.switchLmTab = function (tabId) {
    showTab(tabId);
    if (tabId === 'tab-list') loadList();
  };

  /* ── file selection ── */

  function initUpload() {
    if (!$('dropZone') || typeof LogiTable === 'undefined') return;
    LogiTable.initDropZone({
      dropZoneId: 'dropZone',
      fileInputId: 'fileInput',
      onFile: handleFile,
    });
    buildSchemaTable();
    buildSamplePreview();
  }

  function handleFile(file) {
    LogiTable.handleFile(file, {
      nameId: 'fileSelectedName',
      sizeId: 'fileSelectedSize',
      rowId: 'fileSelectedRow',
      zoneId: 'dropZone',
      refId: 'uploadFileRef',
      onError: (msg) => toast(msg, true),
    });
  }

  window.onFileInput = function (input) {
    if (input.files[0]) handleFile(input.files[0]);
  };

  window.clearFile = function () {
    LogiTable.clearFile({
      rowId: 'fileSelectedRow',
      zoneId: 'dropZone',
      inputId: 'fileInput',
      refId: 'uploadFileRef',
    });
  };

  /* ── upload → preview → confirm ── */

  window.submitUpload = function (btn) {
    const input = $('fileInput');
    const ref = $('uploadFileRef');
    const file = (input && input.files[0]) || (ref && ref._file) || null;
    if (!file) { toast('Please select a file first', true); return; }

    const fd = new FormData();
    fd.append('file', file);
    const original = btn.innerHTML;
    btn.innerHTML = '<span class="gs-spinner"></span> Uploading…';
    btn.disabled = true;

    fetch(cfg.apiBase + 'upload/', {
      method: 'POST', credentials: 'same-origin',
      headers: { 'X-CSRFToken': csrfToken() },  // browser sets multipart Content-Type
      body: fd,
    })
      .then((r) => r.json().then((d) => ({ ok: r.ok, data: d })))
      .then((res) => {
        if (!res.ok) throw new Error(res.data.message || res.data.detail || 'Upload failed');
        up.preview = res.data;
        renderUploadPreview(res.data);
        switchLmTab('tab-preview');
        toast('File uploaded — review before importing');
      })
      .catch((e) => toast('Upload error: ' + e.message, true))
      .finally(() => { btn.innerHTML = original; btn.disabled = false; });
  };

  const PREV_STATUS = {
    ok: ['ok', 'fa-plus', 'New'],
    duplicate: ['dup', 'fa-clone', 'Duplicate'],
    error: ['err', 'fa-times', 'Error'],
  };

  function previewStatusBadge(row) {
    const info = PREV_STATUS[row.status] || ['err', 'fa-times', 'Error'];
    return '<span class="prev-status ' + info[0] + '"><i class="fas ' + info[1] + '"></i> '
      + info[2] + '</span>';
  }

  /** The preview table shows the master's key columns (required fields
      first, capped so wide masters like items stay readable). */
  function previewFields() {
    const data = cfg.fields.filter((f) => f.type !== 'checkbox');
    const required = data.filter((f) => f.required);
    const optional = data.filter((f) => !f.required);
    return required.concat(optional).slice(0, 4);
  }

  function renderUploadPreview(data) {
    const rows = data.rows || [];
    const ok = rows.filter((r) => r.status === 'ok').length;
    const dup = rows.filter((r) => r.status === 'duplicate').length;
    const err = rows.filter((r) => r.status === 'error').length;
    LogiTable.animateVal($('prevTotal'), rows.length);
    LogiTable.animateVal($('prevOk'), ok);
    LogiTable.animateVal($('prevWarn'), dup + err);

    if (up.table) {
      up.table.setData(rows);
    } else {
      const dataColumns = previewFields().map((field) => ({
        key: field.name, label: field.label, width: '1fr',
        sortable: true, searchable: true,
        value: (r) => (r.raw || {})[field.name] || '',
        render: (r) => {
          const value = (r.raw || {})[field.name];
          return value ? esc(String(value)) : '<span class="tbl-sub">—</span>';
        },
      }));

      up.table = LogiTable.createDataTable({
        mount: 'previewBody',
        data: rows,
        pageSize: 15,
        perPage: [15, 25, 50, 100],
        search: true,
        rowClass: (r) => (PREV_STATUS[r.status] || ['err'])[0],
        filters: [
          { key: 'all', label: 'All', test: () => true },
          { key: 'ok', label: 'New', test: (r) => r.status === 'ok' },
          { key: 'dup', label: 'Duplicate', test: (r) => r.status === 'duplicate' },
          { key: 'err', label: 'Error', test: (r) => r.status === 'error' },
        ],
        columns: [
          { key: 'row', label: '#', width: '56px', sortable: true,
            value: (r) => r.row, className: 'prev-row-num' },
        ].concat(dataColumns, [
          { key: 'status', label: 'Status', width: '110px', sortable: true,
            value: (r) => r.status, render: previewStatusBadge },
          { key: 'reason', label: 'Reason', width: '1.6fr', sortable: true, searchable: true,
            value: (r) => r.message || '',
            render: (r) => {
              if (!r.message) return '<span class="tbl-sub">—</span>';
              const color = r.status === 'error' ? 'var(--red)' : 'var(--text-muted)';
              return '<span style="font-size:12px;color:' + color + '">'
                + esc(r.message) + '</span>';
            } },
        ]),
      });
    }

    const info = $('confirmInfo');
    if (info) {
      info.innerHTML = '<strong>' + ok + '</strong> new · <strong>' + (dup + err)
        + '</strong> skipped';
    }
  }

  window.confirmImport = function (btn) {
    if (!up.preview) { toast('No preview data — upload a file first', true); return; }
    const original = btn.innerHTML;
    btn.innerHTML = '<span class="gs-spinner"></span> Importing…';
    btn.disabled = true;

    api(cfg.apiBase + 'upload/confirm/', {
      method: 'POST',
      body: JSON.stringify({ token: up.preview.token }),
    })
      .then((data) => {
        toast((data.created || 0) + ' record' + (data.created === 1 ? '' : 's') + ' imported'
          + (data.skipped ? ', ' + data.skipped + ' skipped' : ''));
        up.preview = null;
        clearFile();
        showTab('tab-list');
        loadList();
      })
      .catch((e) => {
        toast('Import error: ' + ((e.body && e.body.message) || e.message || 'failed'), true);
      })
      .finally(() => { btn.innerHTML = original; btn.disabled = false; });
  };

  window.cancelImport = function () {
    up.preview = null;
    clearFile();
    // Fire-and-forget: drop the server-side preview too.
    api(cfg.apiBase + 'upload/cancel/', { method: 'POST', body: '{}' }).catch(() => {});
    switchLmTab('tab-upload');
    toast('Import cancelled');
  };

  /* ═══ WIRING ═════════════════════════════════════════════════════════ */

  function debounce(fn, ms) {
    let t;
    return function () {
      clearTimeout(t);
      const args = arguments;
      t = setTimeout(() => fn.apply(null, args), ms);
    };
  }

  document.addEventListener('DOMContentLoaded', function () {
    Modal.wire();
    renderHead();
    buildForm();
    loadList();
    initUpload();

    const search = $('gsSearch');
    const clear = $('gsSearchClear');

    search.addEventListener('input', debounce(function () {
      state.search = this.value.trim();
      state.page = 1;
      clear.hidden = !this.value;
      loadList();
    }, 300));

    clear.addEventListener('click', function () {
      search.value = '';
      clear.hidden = true;
      state.search = '';
      state.page = 1;
      loadList();
    });

    $('gsStatusPills').addEventListener('click', function (e) {
      const pill = e.target.closest('.gs-pill');
      if (!pill) return;
      this.querySelectorAll('.gs-pill').forEach((p) => p.classList.remove('active'));
      pill.classList.add('active');
      state.status = pill.dataset.status;
      state.page = 1;
      loadList();
    });

    $('gsPerPage').addEventListener('change', function () {
      // DRF's PAGE_SIZE is fixed server-side, so this only affects the
      // client-side page maths; kept for parity with the other modules.
      state.pageSize = parseInt(this.value, 10) || 50;
      state.page = 1;
      loadList();
    });

    $('gsRefreshBtn').addEventListener('click', function () {
      state.fkOptions = {};   // pick up masters added in another tab
      loadList();
    });

    const addBtn = $('gsAddBtn');
    if (addBtn) addBtn.addEventListener('click', openAdd);

    $('gsSaveBtn').addEventListener('click', save);
    $('gsCancelBtn').addEventListener('click', () => Modal.close('gsEditOverlay'));
    $('gsModalClose').addEventListener('click', () => Modal.close('gsEditOverlay'));
    $('gsDeleteCancel').addEventListener('click', () => Modal.close('gsDeleteOverlay'));
    $('gsDeleteConfirm').addEventListener('click', confirmDelete);
  });

})();
