/* ═══════════════════════════════════════════════════════
   vat_rate_master.js — VAT Rate Master (VAT Reconciliation)
   HSN-based + flat rates. Mirrors courier_master.js.
═══════════════════════════════════════════════════════ */
(function () {
  'use strict';

  const API = VR_CONFIG.urls;
  const CSRF = LogiTable.getCookie('csrftoken');
  const FORM_FIELDS = ['country', 'calc_type', 'hsn_sac_code', 'vat_percent', 'is_active'];

  let advFilter = null;
  let countryNames = [];
  let state = { rows: [], allRows: [], page: 1, perPage: 15, search: '',
    editingId: null, deleteId: null, uploadPreview: null, totalCount: 0 };

  function g(id) { return document.getElementById(id); }
  function qs(s, c) { return (c || document).querySelector(s); }
  function qsa(s, c) { return [...(c || document).querySelectorAll(s)]; }
  const escHtml = LogiTable.escHtml;
  const animateVal = LogiTable.animateVal;

  function toast(msg, type) {
    type = type || 'success';
    const el = g('lmToast'); if (!el) return;
    el.className = 'lm-toast ' + type;
    el.querySelector('.toast-msg').textContent = msg;
    el.querySelector('i').className =
      type === 'success' ? 'fas fa-check-circle' : type === 'error' ? 'fas fa-times-circle'
      : type === 'warning' ? 'fas fa-exclamation-triangle' : 'fas fa-info-circle';
    el.classList.add('show'); clearTimeout(el._t);
    el._t = setTimeout(function () { el.classList.remove('show'); }, 3200);
  }

  function showTab(tabId) {
    qsa('.lm-tab-btn').forEach(function (b) { b.classList.toggle('active', b.dataset.tab === tabId); });
    qsa('.lm-tab-content').forEach(function (c) {
      const on = c.id === tabId; c.classList.toggle('active', on); c.style.display = on ? 'block' : 'none';
    });
  }
  window.switchLmTab = function (tabId) { showTab(tabId); if (tabId === 'tab-list') loadList(); };

  function applyFilters() {
    const q = state.search.toLowerCase().trim();
    let filtered = state.allRows;
    if (advFilter) filtered = advFilter.filterData(filtered);
    if (q) {
      filtered = filtered.filter(function (r) {
        return ['country', 'hsn_sac_code', 'calc_type'].some(function (f) { return (r[f] || '').toString().toLowerCase().includes(q); });
      });
    }
    state.totalCount = filtered.length;
    const totalPages = Math.max(1, Math.ceil(state.totalCount / state.perPage));
    if (state.page > totalPages) state.page = totalPages;
    const start = (state.page - 1) * state.perPage;
    state.rows = filtered.slice(start, start + state.perPage);

    renderTable();
    LogiTable.renderPagination(state);
    const sel = g('lmPerPageSelect');
    if (sel && parseInt(sel.value) !== state.perPage) sel.value = state.perPage;

    const countries = new Set(filtered.map(function (r) { return r.country; }).filter(Boolean)).size;
    const hsn = filtered.filter(function (r) { return r.calc_type === 'HSN'; }).length;
    animateVal(g('statTotal'), state.totalCount);
    animateVal(g('statCountries'), countries);
    animateVal(g('statHsn'), hsn);

    const badge = g('recordCountBadge');
    if (badge) badge.textContent = state.totalCount.toLocaleString('en-IN') + ' record' + (state.totalCount !== 1 ? 's' : '');
  }

  function loadCountries() {
    return fetch(API.countryList + '?per_page=10000', { credentials: 'same-origin' })
      .then(function (r) { return r.json(); })
      .then(function (data) {
        const rows = Array.isArray(data) ? data : (data.data || []);
        countryNames = rows.map(function (c) { return c.country; }).filter(Boolean);
        populateCountrySelect();
      })
      .catch(function () { /* leave the select with just the placeholder */ });
  }

  function populateCountrySelect(extra) {
    const sel = g('efield_country'); if (!sel) return;
    const names = countryNames.slice();
    if (extra && names.indexOf(extra) === -1) names.push(extra);
    const current = sel.value;
    sel.innerHTML = '<option value="">Select a country…</option>' +
      names.map(function (n) { return '<option value="' + escHtml(n) + '">' + escHtml(n) + '</option>'; }).join('');
    sel.value = current;
  }

  function loadList() {
    const wrap = g('listLoading'); if (wrap) wrap.style.display = 'block';
    fetch(API.list + '?per_page=10000', { credentials: 'same-origin' })
      .then(function (r) { if (!r.ok) throw new Error('Server ' + r.status); return r.json(); })
      .then(function (data) { state.allRows = Array.isArray(data) ? data : (data.data || []); state.page = 1; applyFilters(); })
      .catch(function (e) { toast('Could not load rates: ' + e.message, 'error'); })
      .finally(function () { if (wrap) wrap.style.display = 'none'; });
  }

  function typeBadge(t) {
    return t === 'FLAT'
      ? '<span class="badge badge-purple">Flat</span>'
      : '<span class="badge badge-blue">HSN</span>';
  }
  function statusBadge(active) {
    return active ? '<span class="badge badge-green">Active</span>' : '<span class="badge badge-red">Inactive</span>';
  }

  function renderTable() {
    const body = g('tblBody'); if (!body) return;
    if (!state.rows.length) {
      body.innerHTML = '<div class="empty-state"><i class="fas fa-percent"></i>' +
        '<p>No VAT rates found. <a href="javascript:void(0)" onclick="openAdd()" style="color:var(--blue)">Add one</a> or adjust filters.</p></div>';
      return;
    }
    body.innerHTML = state.rows.map(function (r, i) {
      return '<div class="tbl-row" style="animation-delay:' + (i * 0.03) + 's">' +
        '<div class="tbl-id">#' + r.id + '</div>' +
        '<div><div class="tbl-name">' + escHtml(r.country || '—') + '</div></div>' +
        '<div>' + typeBadge(r.calc_type) + '</div>' +
        '<div>' + escHtml(r.hsn_sac_code || '—') + '</div>' +
        '<div>' + escHtml(r.vat_percent || '0') + '%</div>' +
        '<div>' + statusBadge(r.is_active) + '</div>' +
        '<div class="tbl-actions">' +
          '<button class="tbl-btn edit" onclick="openEdit(' + r.id + ')"><i class="fas fa-pencil-alt"></i> Edit</button>' +
          '<button class="tbl-btn del" onclick="openDelete(' + r.id + ')"><i class="fas fa-trash"></i></button>' +
        '</div></div>';
    }).join('');
  }

  window.goPage = function (p) {
    const totalPages = Math.ceil(state.totalCount / state.perPage);
    if (p < 1 || p > totalPages) return; state.page = p; applyFilters();
  };
  window.onLmPerPageChange = function (val) { state.perPage = parseInt(val); state.page = 1; applyFilters(); };
  window.onListSearch = function (val) { state.search = val; state.page = 1; applyFilters(); };

  window.onCalcTypeChange = function () {
    const t = g('efield_calc_type').value;
    const hsnField = g('hsnField');
    const hsnInput = g('efield_hsn_sac_code');
    if (t === 'FLAT') {
      hsnField.style.display = 'none';
      if (hsnInput) { hsnInput.value = ''; hsnInput.classList.remove('error'); }
    } else {
      hsnField.style.display = '';
    }
  };

  function fillForm(r) {
    populateCountrySelect(r.country);  // make sure the rate's country is selectable
    FORM_FIELDS.forEach(function (f) {
      const el = g('efield_' + f); if (!el) return;
      if (f === 'is_active') { el.value = (r[f] === false ? 'false' : 'true'); return; }
      el.value = r[f] == null ? '' : r[f];
    });
    onCalcTypeChange();
  }

  window.openEdit = function (id) {
    const r = state.rows.concat(state.allRows).find(function (x) { return x.id === id; });
    if (!r) { toast('Could not load rate', 'error'); return; }
    state.editingId = r.id; fillForm(r);
    if (g('editModalTitle')) g('editModalTitle').textContent = 'Edit VAT Rate';
    if (g('editModalSubtitle')) g('editModalSubtitle').textContent = 'Editing: ' + (r.country || '') + ' · ' + (r.hsn_sac_code || 'FLAT');
    clearFormErrors();
    g('editModalOverlay').classList.add('open'); document.body.style.overflow = 'hidden';
  };

  window.openAdd = function () {
    state.editingId = null;
    const form = g('locationEditForm'); if (form) form.reset();
    populateCountrySelect();
    if (g('efield_calc_type')) g('efield_calc_type').value = 'HSN';
    if (g('efield_is_active')) g('efield_is_active').value = 'true';
    onCalcTypeChange(); clearFormErrors();
    if (g('editModalTitle')) g('editModalTitle').textContent = 'Add New VAT Rate';
    if (g('editModalSubtitle')) g('editModalSubtitle').textContent = 'Create a new VAT rate';
    g('editModalOverlay').classList.add('open'); document.body.style.overflow = 'hidden';
  };

  window.closeEditModal = function () { g('editModalOverlay').classList.remove('open'); document.body.style.overflow = ''; state.editingId = null; };

  function clearFormErrors() {
    const form = g('locationEditForm'); if (!form) return;
    qsa('.form-input,.form-select', form).forEach(function (el) { el.classList.remove('error'); });
    qsa('.form-error', form).forEach(function (el) { el.textContent = ''; });
  }

  function validateForm(form) {
    let valid = true;
    qsa('[required]', form).forEach(function (el) {
      if (el.offsetParent === null) return; // skip hidden
      const err = form.querySelector('[data-err="' + el.name + '"]');
      const empty = !el.value.trim();
      el.classList.toggle('error', empty);
      if (err) err.textContent = empty ? 'This field is required' : '';
      if (empty) valid = false;
    });
    // HSN code required only when calc_type === HSN
    if (g('efield_calc_type').value === 'HSN') {
      const hsn = g('efield_hsn_sac_code');
      const err = qs('[data-err="hsn_sac_code"]');
      if (!hsn.value.trim()) { hsn.classList.add('error'); if (err) err.textContent = 'HSN/SAC required'; valid = false; }
    }
    return valid;
  }

  function handleFormErrors(errors) {
    if (!errors || typeof errors !== 'object') return;
    Object.entries(errors).forEach(function (entry) {
      const el = g('efield_' + entry[0]); const err = qs('[data-err="' + entry[0] + '"]');
      if (el) el.classList.add('error');
      if (err) err.textContent = Array.isArray(entry[1]) ? entry[1][0] : entry[1];
    });
  }

  window.submitEditModal = function (btn) {
    const form = g('locationEditForm');
    if (!validateForm(form)) return;
    const data = {};
    qsa('.form-input,.form-select', form).forEach(function (el) { if (el.name) data[el.name] = el.value.trim(); });

    const isEdit = !!state.editingId;
    const url = isEdit ? API.update + state.editingId + '/' : API.create;
    const method = isEdit ? 'PUT' : 'POST';
    const orig = btn.innerHTML;
    btn.innerHTML = '<span class="loading-spinner"></span> Saving…'; btn.disabled = true;
    fetch(url, { method: method, credentials: 'same-origin',
      headers: { 'Content-Type': 'application/json', 'X-CSRFToken': CSRF }, body: JSON.stringify(data) })
      .then(function (r) { return r.json().then(function (result) { return { ok: r.ok, result: result }; }); })
      .then(function (res) {
        const ok = res.result.success === true || (res.ok && res.result.success !== false);
        if (!ok) { handleFormErrors(res.result.errors); toast(res.result.message || 'Validation failed', 'error'); return; }
        toast(res.result.message || (isEdit ? 'Rate updated!' : 'Rate created!'), 'success');
        closeEditModal(); loadList();
      })
      .catch(function (e) { toast('Server error: ' + e.message, 'error'); })
      .finally(function () { btn.innerHTML = orig; btn.disabled = false; });
  };

  window.openDelete = function (id) {
    const r = state.rows.concat(state.allRows).find(function (x) { return x.id === id; });
    if (!r) return; state.deleteId = id;
    const rowsEl = g('deleteModalRows');
    if (rowsEl) {
      rowsEl.innerHTML = [
        ['Country', r.country || '—'], ['Type', r.calc_type || '—'],
        ['HSN / SAC', r.hsn_sac_code || 'FLAT'], ['VAT %', (r.vat_percent || '0') + '%'],
      ].map(function (p) {
        return '<div class="lmodal-record-row"><span class="lmodal-record-lbl">' + p[0] + '</span><span class="lmodal-record-val">' + escHtml(String(p[1])) + '</span></div>';
      }).join('');
    }
    g('deleteModalOverlay').classList.add('open');
  };
  window.closeDeleteModal = function () { g('deleteModalOverlay').classList.remove('open'); state.deleteId = null; };

  window.confirmDelete = function (btn) {
    if (!state.deleteId) return;
    const orig = btn.innerHTML;
    btn.innerHTML = '<span class="loading-spinner"></span> Deleting…'; btn.disabled = true;
    fetch(API.delete + state.deleteId + '/', { method: 'DELETE', credentials: 'same-origin', headers: { 'X-CSRFToken': CSRF } })
      .then(function (r) { if (!r.ok) throw new Error('Delete failed'); toast('Rate deleted', 'success'); closeDeleteModal(); loadList(); })
      .catch(function (e) { toast('Could not delete: ' + e.message, 'error'); })
      .finally(function () { btn.innerHTML = orig; btn.disabled = false; });
  };

  /* ── UPLOAD ── */
  function initDropZone() { LogiTable.initDropZone({ dropZoneId: 'dropZone', fileInputId: 'fileInput', onFile: handleFile }); }
  window.onFileInput = function (input) { if (input.files[0]) handleFile(input.files[0]); };
  function handleFile(file) {
    LogiTable.handleFile(file, { nameId: 'fileSelectedName', sizeId: 'fileSelectedSize', rowId: 'fileSelectedRow',
      zoneId: 'dropZone', refId: 'uploadFileRef', onError: function (m) { toast(m, 'error'); } });
  }
  window.clearFile = function () { LogiTable.clearFile({ rowId: 'fileSelectedRow', zoneId: 'dropZone', inputId: 'fileInput', refId: 'uploadFileRef' }); };

  window.submitUpload = function (btn) {
    const fi = g('fileInput'), ref = g('uploadFileRef');
    const file = (fi && fi.files[0]) || (ref && ref._file) || null;
    if (!file) { toast('Please select a file first', 'warning'); return; }
    const fd = new FormData(); fd.append('file', file);
    const orig = btn.innerHTML;
    btn.innerHTML = '<span class="loading-spinner"></span> Uploading…'; btn.disabled = true;
    fetch(API.upload, { method: 'POST', credentials: 'same-origin', headers: { 'X-CSRFToken': CSRF }, body: fd })
      .then(function (r) { return r.json().then(function (d) { return { ok: r.ok, data: d }; }); })
      .then(function (res) {
        if (!res.ok) throw new Error(res.data.message || 'Upload failed');
        state.uploadPreview = res.data; renderPreview(res.data); switchLmTab('tab-preview');
        toast('File uploaded — review before importing', 'info');
      })
      .catch(function (e) { toast('Upload error: ' + e.message, 'error'); })
      .finally(function () { btn.innerHTML = orig; btn.disabled = false; });
  };

  window.downloadSample = function () {
    const csv = ['country,calc_type,hsn_sac_code,vat_percent', 'Italy,HSN,9021909000,4',
      'Italy,HSN,9018908400,22', 'UAE,FLAT,,5', 'Saudi Arabia,FLAT,,15'].join('\n');
    const a = document.createElement('a');
    a.href = 'data:text/csv,' + encodeURIComponent(csv);
    a.download = 'vat_rate_master_sample.csv'; a.click();
    toast('Sample CSV downloaded', 'success');
  };

  let previewTable = null;
  const PREV_STATUS = { ok: ['ok', 'fa-plus', 'New'], duplicate: ['dup', 'fa-clone', 'Duplicate'], error: ['err', 'fa-times', 'Error'] };
  function previewStatusBadge(row) {
    const info = PREV_STATUS[row.status] || ['err', 'fa-times', 'Error'];
    return '<span class="prev-status ' + info[0] + '"><i class="fas ' + info[1] + '"></i> ' + info[2] + '</span>';
  }

  function renderPreview(data) {
    const rows = data.rows || [];
    const ok = rows.filter(function (r) { return r.status === 'ok'; }).length;
    const dup = rows.filter(function (r) { return r.status === 'duplicate'; }).length;
    const err = rows.filter(function (r) { return r.status === 'error'; }).length;
    animateVal(g('prevTotal'), rows.length); animateVal(g('prevOk'), ok); animateVal(g('prevWarn'), dup + err);

    const cols = [
      { key: 'row', label: '#', width: '56px', sortable: true, value: function (r) { return r.row; }, className: 'prev-row-num' },
      { key: 'country', label: 'Country', width: '1.2fr', sortable: true, searchable: true,
        value: function (r) { return (r.data || {}).country || ''; }, render: function (r) { return '<span class="tbl-id">' + escHtml((r.data || {}).country || '—') + '</span>'; } },
      { key: 'calc_type', label: 'Type', width: '0.7fr', value: function (r) { return (r.data || {}).calc_type || ''; }, render: function (r) { return escHtml((r.data || {}).calc_type || '—'); } },
      { key: 'hsn', label: 'HSN / SAC', width: '1fr', searchable: true, value: function (r) { return (r.data || {}).hsn_sac_code || ''; }, render: function (r) { return escHtml((r.data || {}).hsn_sac_code || 'FLAT'); } },
      { key: 'vat', label: 'VAT %', width: '0.6fr', value: function (r) { return (r.data || {}).vat_percent || ''; }, render: function (r) { return escHtml((r.data || {}).vat_percent || '—') + '%'; } },
      { key: 'status', label: 'Status', width: '110px', sortable: true, value: function (r) { return r.status; }, render: previewStatusBadge },
    ];

    if (previewTable) { previewTable.setData(rows); }
    else {
      previewTable = LogiTable.createDataTable({
        mount: 'previewBody', data: rows, pageSize: 15, perPage: [15, 25, 50, 100], search: true,
        rowClass: function (r) { return (PREV_STATUS[r.status] || ['err'])[0]; },
        filters: [
          { key: 'all', label: 'All', test: function () { return true; } },
          { key: 'ok', label: 'New', test: function (r) { return r.status === 'ok'; } },
          { key: 'dup', label: 'Duplicate', test: function (r) { return r.status === 'duplicate'; } },
          { key: 'err', label: 'Error', test: function (r) { return r.status === 'error'; } },
        ],
        columns: cols,
      });
    }
    const ci = g('confirmInfo');
    if (ci) ci.innerHTML = '<strong>' + ok + '</strong> new · <strong>' + (dup + err) + '</strong> skipped';
  }

  window.confirmImport = function (btn) {
    if (!state.uploadPreview) { toast('No preview data', 'warning'); return; }
    const orig = btn.innerHTML;
    btn.innerHTML = '<span class="loading-spinner"></span> Importing…'; btn.disabled = true;
    fetch(API.confirm, { method: 'POST', credentials: 'same-origin',
      headers: { 'Content-Type': 'application/json', 'X-CSRFToken': CSRF }, body: JSON.stringify({ token: state.uploadPreview.token }) })
      .then(function (r) { return r.json().then(function (d) { return { ok: r.ok, data: d }; }); })
      .then(function (res) {
        if (!res.ok) throw new Error(res.data.message || 'Import failed');
        toast((res.data.created || 0) + ' rates added', 'success');
        state.uploadPreview = null; clearFile(); showTab('tab-list'); loadList();
      })
      .catch(function (e) { toast('Import error: ' + e.message, 'error'); })
      .finally(function () { btn.innerHTML = orig; btn.disabled = false; });
  };
  window.cancelImport = function () { state.uploadPreview = null; clearFile(); switchLmTab('tab-upload'); toast('Import cancelled'); };

  document.addEventListener('DOMContentLoaded', function () {
    qsa('.lm-tab-content').forEach(function (c) {
      const on = c.id === 'tab-list'; c.classList.toggle('active', on); c.style.display = on ? 'block' : 'none';
    });
    qsa('.fi').forEach(function (el) { el.style.opacity = '1'; });
    loadCountries(); loadList(); initDropZone();

    if (typeof AdvFilter !== 'undefined') {
      advFilter = new AdvFilter({
        searchWrapId: 'lmSearchWrap', chipsId: 'lmAdvChips',
        onApply: function () { state.page = 1; applyFilters(); },
        fields: [
          { key: 'calc_type', label: 'Type', type: 'select', options: [{ v: 'HSN', l: 'HSN Based' }, { v: 'FLAT', l: 'Flat' }] },
          { key: 'is_active', label: 'Status', type: 'select', options: [{ v: true, l: 'Active' }, { v: false, l: 'Inactive' }] },
        ],
      });
    }

    const ov = g('deleteModalOverlay');
    if (ov) ov.addEventListener('click', function (e) { if (e.target === ov) closeDeleteModal(); });
    const eov = g('editModalOverlay');
    if (eov) eov.addEventListener('click', function (e) { if (e.target === eov) closeEditModal(); });
    document.addEventListener('keydown', function (e) { if (e.key === 'Escape') { closeDeleteModal(); closeEditModal(); } });

    let st; const se = g('listSearch');
    if (se) se.addEventListener('input', function () { clearTimeout(st); st = setTimeout(function () { onListSearch(se.value); }, 350); });
  });
})();
