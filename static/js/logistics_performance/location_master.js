/* ═══════════════════════════════════════════════════════
   location_master.js
   Uses shared utilities from logi_datatable.js (LogiTable)
═══════════════════════════════════════════════════════ */

(function () {
  'use strict';

  /* ─── CONFIG ─────────────────────────────────────── */
  const API = {
    list:    LM_CONFIG.urls.list,
    create:  LM_CONFIG.urls.create,
    update:  LM_CONFIG.urls.update,
    delete:  LM_CONFIG.urls.delete,
    upload:  LM_CONFIG.urls.upload,
    preview: LM_CONFIG.urls.preview,
    confirm: LM_CONFIG.urls.confirm,
  };
  const CSRF = LogiTable.getCookie('csrftoken');

  /* ─── ADVANCED FILTER ───────────────────────────── */
  let advFilter = null;

  /* ─── STATE ──────────────────────────────────────── */
  let state = {
    locations:     [],
    allLocations:  [],
    page:          1,
    perPage:       15,
    search:        '',
    zoneFilter:    '',
    editingId:     null,
    deleteId:      null,
    uploadPreview: null,
    totalCount:    0,
    serverStats:   null,   // true full-table counts from the API (not the loaded slice)
  };

  /* ─── UTILS ──────────────────────────────────────── */
  function g(id)         { return document.getElementById(id); }
  function qs(sel, ctx)  { return (ctx || document).querySelector(sel); }
  function qsa(sel, ctx) { return [...(ctx || document).querySelectorAll(sel)]; }

  const escHtml    = LogiTable.escHtml;
  const animateVal = LogiTable.animateVal;

  /* ═══ TOAST ══════════════════════════════════════ */
  function toast(msg, type) {
    type = type || 'success';
    const el = g('lmToast');
    if (!el) return;
    el.className = 'lm-toast ' + type;
    el.querySelector('.toast-msg').textContent = msg;
    el.querySelector('i').className =
      type === 'success' ? 'fas fa-check-circle'
      : type === 'error'   ? 'fas fa-times-circle'
      : type === 'warning' ? 'fas fa-exclamation-triangle'
      : 'fas fa-info-circle';
    el.classList.add('show');
    clearTimeout(el._t);
    el._t = setTimeout(function () { el.classList.remove('show'); }, 3200);
  }

  /* ═══ TABS ═══════════════════════════════════════ */
  function showTab(tabId) {
    qsa('.lm-tab-btn').forEach(function (b) { b.classList.toggle('active', b.dataset.tab === tabId); });
    qsa('.lm-tab-content').forEach(function (c) {
      var on = c.id === tabId;
      c.classList.toggle('active', on);
      c.style.display = on ? 'block' : 'none';
    });
  }

  window.switchLmTab = function (tabId) {
    showTab(tabId);
    if (tabId === 'tab-list') loadList();
  };

  /* ═══ TAB 1 — LIST ═══════════════════════════════ */
  function applyFilters() {
    const q    = state.search.toLowerCase().trim();
    const zone = state.zoneFilter;

    let filtered = state.allLocations;
    // Apply advanced filters
    if (advFilter) filtered = advFilter.filterData(filtered);

    if (zone) {
      filtered = filtered.filter(function (l) { return (l.zone || '') === zone; });
    }
    if (q) {
      filtered = filtered.filter(function (l) {
        return (l.name     || l.location || '').toLowerCase().includes(q) ||
               (l.city     || '').toLowerCase().includes(q) ||
               (l.state    || '').toLowerCase().includes(q) ||
               (l.pincode  || '').toString().includes(q) ||
               (l.courier_code || '').toLowerCase().includes(q) ||
               (l.courier_name || '').toLowerCase().includes(q);
      });
    }

    state.totalCount = filtered.length;

    const totalPages = Math.max(1, Math.ceil(state.totalCount / state.perPage));
    if (state.page > totalPages) state.page = totalPages;

    const start = (state.page - 1) * state.perPage;
    state.locations = filtered.slice(start, start + state.perPage);

    renderTable();
    LogiTable.renderPagination(state);

    const sel = g('lmPerPageSelect');
    if (sel && parseInt(sel.value) !== state.perPage) sel.value = state.perPage;

    // When no filter is active, show the LIVE full-table counts from the server.
    // Only fall back to counting the loaded rows when the user is filtering
    // client-side (the server stats wouldn't reflect that subset).
    const filtersActive = !!q || !!zone || filtered.length !== state.allLocations.length;
    if (!filtersActive && state.serverStats) {
      updateHeaderStats({
        total:  state.serverStats.total,
        active: state.serverStats.active,
        zones:  state.serverStats.zones,
        states: state.serverStats.states,
      });
    } else {
      const activeCount = filtered.filter(function (l) { return l.is_active !== false; }).length;
      const zones  = new Set(filtered.map(function (l) { return l.zone; }).filter(Boolean)).size;
      const states = new Set(filtered.map(function (l) { return l.state || l.state_name; }).filter(Boolean)).size;
      updateHeaderStats({ total: filtered.length, active: activeCount, zones: zones, states: states });
    }

    const badge = g('recordCountBadge');
    if (badge) badge.textContent =
      state.totalCount.toLocaleString('en-IN') + ' record' + (state.totalCount !== 1 ? 's' : '');
  }

  function loadList() {
    const wrap = g('listLoading');
    if (wrap) wrap.style.display = 'block';

    fetch(API.list + '?per_page=10000', { credentials: 'same-origin' })
      .then(function (r) { if (!r.ok) throw new Error('Server ' + r.status); return r.json(); })
      .then(function (data) {
        state.allLocations = Array.isArray(data) ? data : (data.data || data.results || []);
        // True counts for the whole table (not just the rows we loaded).
        state.serverStats = (data && data.stats) ? data.stats : null;
        state.page = 1;
        applyFilters();
      })
      .catch(function (e) { toast('Could not load locations: ' + e.message, 'error'); })
      .finally(function () { if (wrap) wrap.style.display = 'none'; });
  }

  function renderTable() {
    const body = g('tblBody');
    if (!body) return;

    if (!state.locations.length) {
      body.innerHTML = '<div class="empty-state">' +
        '<i class="fas fa-map-marker-alt"></i>' +
        '<p>No locations found. <a href="javascript:void(0)" onclick="openAdd()" style="color:var(--blue)">Add one</a> or adjust filters.</p>' +
        '</div>';
      return;
    }

    body.innerHTML = state.locations.map(function (loc, i) {
      return '<div class="tbl-row" style="animation-delay:' + (i * 0.03) + 's">' +
        '<div class="tbl-id">#' + loc.id + '</div>' +
        '<div><div class="tbl-name">' + escHtml(loc.name || loc.location || '—') + '</div></div>' +
        '<div>' +
          '<div>' + escHtml(loc.city || loc.location || '—') + '</div>' +
          '<div class="tbl-sub">' + escHtml(loc.state_name || loc.state || '') + '</div>' +
        '</div>' +
        '<div><span class="badge badge-blue">' + escHtml(loc.zone || '—') + '</span></div>' +
        '<div><span class="badge badge-' + (loc.is_active !== false ? 'green' : 'muted') + '">' + (loc.is_active !== false ? 'Active' : 'Inactive') + '</span></div>' +
        '<div class="tbl-id">' + escHtml(String(loc.pincode || '—')) + '</div>' +
        '<div class="tbl-actions">' +
          '<button class="tbl-btn edit" onclick="openEdit(' + loc.id + ')"><i class="fas fa-pencil-alt"></i> Edit</button>' +
          '<button class="tbl-btn del"  onclick="openDelete(' + loc.id + ')"><i class="fas fa-trash"></i></button>' +
        '</div>' +
        '</div>';
    }).join('');
  }

  window.goPage = function (p) {
    const totalPages = Math.ceil(state.totalCount / state.perPage);
    if (p < 1 || p > totalPages) return;
    state.page = p; applyFilters();
  };

  window.onLmPerPageChange = function (val) {
    state.perPage = parseInt(val);
    state.page = 1;
    applyFilters();
  };

  function updateHeaderStats(stats) {
    animateVal(g('statTotal'),  stats.total  || state.totalCount);
    animateVal(g('statActive'), stats.active || 0);
    animateVal(g('statZones'),  stats.zones  || 0);
    animateVal(g('statStates'), stats.states || 0);
  }

  window.onListSearch = function (val) { state.search = val; state.page = 1; applyFilters(); };

  window.setZoneFilter = function (zone, btn) {
    state.zoneFilter = zone; state.page = 1;
    qsa('.filter-pill[data-zone]').forEach(function (b) { b.classList.remove('active'); });
    if (btn) btn.classList.add('active');
    applyFilters();
  };

  /* ═══ TAB 2 — ADD / EDIT FORM ═══════════════════ */
  const FORM_FIELDS = [
    'name','city','district','state','zone','pincode',
    'courier_code','courier_name','mode','rate','tat',
    'oda_nonoda','is_active','service_type','min_weight','notes',
  ];

  /* ═══ EDIT MODAL (centered popup — matches Performance Upload) ═══ */
  function _fillEditModal(loc) {
    state.editingId = loc.id;

    FORM_FIELDS.forEach(function (f) {
      const el = g('efield_' + f);
      if (!el) return;
      let val = loc[f];
      if (val == null && f === 'name') val = loc.location;
      if (val == null) val = '';
      if (f === 'is_active') el.value = val ? 'true' : 'false';
      else                   el.value = val;
    });

    const displayName = loc.name || loc.location || '';
    if (g('editModalTitle'))    g('editModalTitle').textContent    = 'Edit Location';
    if (g('editModalSubtitle')) g('editModalSubtitle').textContent = 'Editing: ' + displayName;

    const form = g('locationEditForm');
    if (form) {
      qsa('.form-input,.form-select,.form-textarea', form).forEach(function (el) { el.classList.remove('error'); });
      qsa('.form-error', form).forEach(function (el) { el.textContent = ''; });
    }

    const ov = g('editModalOverlay');
    if (ov) ov.classList.add('open');
    document.body.style.overflow = 'hidden';
  }

  window.openEdit = function (id) {
    const loc = state.locations.find(function (l) { return l.id === id; });
    if (loc) {
      _fillEditModal(loc);
    } else {
      fetch(API.update + id + '/', { credentials: 'same-origin' })
        .then(function (r) { if (!r.ok) throw new Error('Not found'); return r.json(); })
        .then(function (data) { _fillEditModal(data); })
        .catch(function (e) { toast('Could not load location: ' + e.message, 'error'); });
    }
  };

  window.closeEditModal = function () {
    const ov = g('editModalOverlay');
    if (ov) ov.classList.remove('open');
    document.body.style.overflow = '';
    state.editingId = null;
  };

  // Open the shared modal in CREATE mode (Add uses the same modal as Edit).
  window.openAdd = function () {
    state.editingId = null;
    const form = g('locationEditForm');
    if (form) {
      form.reset();
      qsa('.form-input,.form-select,.form-textarea', form).forEach(function (el) { el.classList.remove('error'); });
      qsa('.form-error', form).forEach(function (el) { el.textContent = ''; });
    }
    if (g('editModalTitle'))    g('editModalTitle').textContent    = 'Add New Location';
    if (g('editModalSubtitle')) g('editModalSubtitle').textContent = 'Create a new location';
    const ov = g('editModalOverlay');
    if (ov) ov.classList.add('open');
    document.body.style.overflow = 'hidden';
  };

  window.submitEditModal = function (btn) {
    const form = g('locationEditForm');
    if (!validateForm(form)) return;

    const data = {};
    qsa('.form-input,.form-select,.form-textarea', form).forEach(function (el) {
      if (!el.name) return;
      const v = el.value.trim();
      data[el.name] = el.name === 'is_active' ? v === 'true' : v;
    });
    if (data.name !== undefined) data.location = data.name;

    const isEdit = !!state.editingId;
    const url    = isEdit ? API.update + state.editingId + '/' : API.create;
    const method = isEdit ? 'PUT' : 'POST';
    const orig = btn.innerHTML;
    btn.innerHTML = '<span class="loading-spinner"></span> Saving…'; btn.disabled = true;

    fetch(url, {
      method: method, credentials: 'same-origin',
      headers: { 'Content-Type': 'application/json', 'X-CSRFToken': CSRF },
      body: JSON.stringify(data),
    })
      .then(function (r) { return r.json().then(function (result) { return { ok: r.ok, result: result }; }); })
      .then(function (res) {
        const succeeded = res.result.success === true || (res.ok && res.result.success !== false);
        if (!succeeded) {
          toast(res.result.message || res.result.detail || 'Validation failed', 'error');
          return;
        }
        toast(res.result.message || (isEdit ? 'Location updated!' : 'Location created!'), 'success');
        closeEditModal();
        loadList();
      })
      .catch(function (e) { toast('Server error: ' + e.message, 'error'); })
      .finally(function () { btn.innerHTML = orig; btn.disabled = false; });
  };

  function resetFilters() {
    state.search = ''; state.zoneFilter = ''; state.page = 1;
    const se = g('listSearch'); if (se) se.value = '';
    qsa('.filter-pill[data-zone]').forEach(function (b) { b.classList.remove('active'); });
    const ap = qs('.filter-pill[data-zone=""]'); if (ap) ap.classList.add('active');
  }

  function validateForm(form) {
    let valid = true;
    qsa('[required]', form).forEach(function (el) {
      const err = form.querySelector('[data-err="' + el.name + '"]');
      const empty = el.type === 'checkbox' ? false : !el.value.trim();
      el.classList.toggle('error', empty);
      if (err) err.textContent = empty ? 'This field is required' : '';
      if (empty) valid = false;
    });
    return valid;
  }

  function handleFormErrors(errors) {
    if (!errors || typeof errors !== 'object') return;
    Object.entries(errors).forEach(function (entry) {
      const field = entry[0], msgs = entry[1];
      const el  = g('field_' + field);
      const err = qs('[data-err="' + field + '"]');
      if (el)  el.classList.add('error');
      if (err) err.textContent = Array.isArray(msgs) ? msgs[0] : msgs;
    });
  }

  /* ═══ DELETE MODAL ═══════════════════════════════ */
  window.openDelete = function (id) {
    const loc = state.locations.find(function (l) { return l.id === id; });
    if (!loc) return;
    state.deleteId = id;
    const rowsEl = g('deleteModalRows');
    if (rowsEl) {
      const displayName = loc.name || loc.location || '—';
      rowsEl.innerHTML = [
        ['Name', displayName], ['City', loc.city || '—'],
        ['State', loc.state || '—'], ['Zone', loc.zone || '—'], ['Pincode', loc.pincode || '—'],
      ].map(function (pair) {
        return '<div class="lmodal-record-row">' +
          '<span class="lmodal-record-lbl">' + pair[0] + '</span>' +
          '<span class="lmodal-record-val">' + escHtml(String(pair[1])) + '</span></div>';
      }).join('');
    }
    const ov = g('deleteModalOverlay'); if (ov) ov.classList.add('open');
  };

  window.closeDeleteModal = function () {
    const ov = g('deleteModalOverlay'); if (ov) ov.classList.remove('open');
    state.deleteId = null;
  };

  window.confirmDelete = function (btn) {
    if (!state.deleteId) return;
    const orig = btn.innerHTML;
    btn.innerHTML = '<span class="loading-spinner"></span> Deleting…'; btn.disabled = true;
    fetch(API.delete + state.deleteId + '/', {
      method: 'DELETE', credentials: 'same-origin',
      headers: { 'X-CSRFToken': CSRF },
    })
      .then(function (r) {
        if (!r.ok) throw new Error('Delete failed');
        toast('Location deleted', 'success');
        closeDeleteModal();
        loadList();
      })
      .catch(function (e) { toast('Could not delete: ' + e.message, 'error'); })
      .finally(function () { btn.innerHTML = orig; btn.disabled = false; });
  };

  /* ═══ TAB 3 — BULK UPLOAD ════════════════════════ */
  function initDropZone() {
    LogiTable.initDropZone({
      dropZoneId:  'dropZone',
      fileInputId: 'fileInput',
      onFile:      handleFile,
    });
  }

  window.onFileInput = function (input) { if (input.files[0]) handleFile(input.files[0]); };

  function handleFile(file) {
    LogiTable.handleFile(file, {
      nameId: 'fileSelectedName',
      sizeId: 'fileSelectedSize',
      rowId:  'fileSelectedRow',
      zoneId: 'dropZone',
      refId:  'uploadFileRef',
      onError: function (msg) { toast(msg, 'error'); },
    });
  }

  window.clearFile = function () {
    LogiTable.clearFile({
      rowId:   'fileSelectedRow',
      zoneId:  'dropZone',
      inputId: 'fileInput',
      refId:   'uploadFileRef',
    });
  };

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
        if (!res.ok) throw new Error(res.data.message || res.data.detail || 'Upload failed');
        state.uploadPreview = res.data;
        renderPreview(res.data);
        switchLmTab('tab-preview');
        toast('File uploaded — review before importing', 'info');
      })
      .catch(function (e) { toast('Upload error: ' + e.message, 'error'); })
      .finally(function () { btn.innerHTML = orig; btn.disabled = false; });
  };

  window.downloadSample = function () {
    const csv = [
      'name,city,district,state,zone,pincode,courier_code,courier_name,mode,rate,tat,oda_nonoda,min_weight,service_type,is_active',
      'New Delhi HQ,New Delhi,North Delhi,Delhi,North,110001,DTDC001,DTDC,Express,50.00,1,NONODA,0.5,Standard,true',
      'Ahmedabad Branch,Ahmedabad,Ahmedabad,Gujarat,West,380001,BLUD01,Bluedart,Express,45.00,2,NONODA,0.5,Standard,true',
    ].join('\n');
    const a = document.createElement('a');
    a.href = 'data:text/csv,' + encodeURIComponent(csv);
    a.download = 'location_master_sample.csv';
    a.click();
    toast('Sample CSV downloaded', 'success');
  };

  /* ═══ TAB 4 — IMPORT PREVIEW ════════════════════ */
  let previewTable = null;

  // status -> [rowClass, icon, label]
  const PREV_STATUS = {
    ok:        ['ok',  'fa-plus',  'New'],
    update:    ['upd', 'fa-sync',  'Update'],
    duplicate: ['dup', 'fa-clone', 'Duplicate'],
    error:     ['err', 'fa-times', 'Error'],
  };
  function previewStatusBadge(row) {
    const info = PREV_STATUS[row.status] || ['err','fa-times','Error'];
    return '<span class="prev-status ' + info[0] + '"><i class="fas ' + info[1] + '"></i> ' + info[2] + '</span>';
  }

  function renderPreview(data) {
    const rows = data.rows || [];
    const ok  = rows.filter(function (r) { return r.status === 'ok'; }).length;
    const upd = rows.filter(function (r) { return r.status === 'update'; }).length;
    const dup = rows.filter(function (r) { return r.status === 'duplicate'; }).length;
    const err = rows.filter(function (r) { return r.status === 'error'; }).length;
    animateVal(g('prevTotal'), rows.length);
    animateVal(g('prevOk'),    ok + upd);   // rows that will be written (new + updated)
    animateVal(g('prevWarn'),  dup + err);

    // build (or refresh) the reusable datatable
    if (previewTable) {
      previewTable.setData(rows);
    } else {
      previewTable = LogiTable.createDataTable({
        mount:    'previewBody',
        data:     rows,
        pageSize: 15,
        perPage:  [15, 25, 50, 100],
        search:   true,
        rowClass: function (r) { return (PREV_STATUS[r.status] || ['err'])[0]; },
        filters: [
          { key: 'all', label: 'All',       test: function ()  { return true; } },
          { key: 'ok',  label: 'New',       test: function (r) { return r.status === 'ok'; } },
          { key: 'upd', label: 'Update',    test: function (r) { return r.status === 'update'; } },
          { key: 'dup', label: 'Duplicate', test: function (r) { return r.status === 'duplicate'; } },
          { key: 'err', label: 'Error',     test: function (r) { return r.status === 'error'; } },
        ],
        columns: [
          { key: 'row',  label: '#',      width: '56px',  sortable: true,
            value: function (r) { return r.row; }, className: 'prev-row-num' },
          { key: 'name', label: 'Name',   width: '1.4fr', sortable: true, searchable: true,
            value:  function (r) { return (r.data || {}).name || (r.data || {}).location || ''; },
            render: function (r) { return '<span class="tbl-id">' + escHtml((r.data || {}).name || (r.data || {}).location || '—') + '</span>'; } },
          { key: 'city', label: 'City',   width: '1fr',   sortable: true, searchable: true,
            value:  function (r) { return (r.data || {}).city || ''; },
            render: function (r) { return escHtml((r.data || {}).city || '—'); } },
          { key: 'state', label: 'State', width: '1fr',   sortable: true, searchable: true,
            value:  function (r) { return (r.data || {}).state || ''; },
            render: function (r) { return escHtml((r.data || {}).state || '—'); } },
          { key: 'zone', label: 'Zone',   width: '90px',  sortable: true,
            value:  function (r) { return (r.data || {}).zone || ''; },
            render: function (r) { return '<span class="badge badge-blue">' + escHtml((r.data || {}).zone || '—') + '</span>'; } },
          { key: 'pin',  label: 'Pincode', width: '100px', sortable: true, searchable: true,
            value:  function (r) { return (r.data || {}).pincode || ''; },
            render: function (r) { return '<span class="tbl-id">' + escHtml(String((r.data || {}).pincode || '—')) + '</span>'; } },
          { key: 'status', label: 'Status', width: '110px', sortable: true,
            value: function (r) { return r.status; }, render: previewStatusBadge },
          { key: 'reason', label: 'Reason', width: '1.6fr', sortable: true, searchable: true,
            value:  function (r) { return r.message || ''; },
            render: function (r) {
              if (!r.message) return '<span class="tbl-sub">—</span>';
              var color = r.status === 'error' ? 'var(--red)'
                        : r.status === 'update' ? 'var(--blue)'
                        : 'var(--text-muted)';
              return '<span style="font-size:12px;color:' + color + '">' + escHtml(r.message) + '</span>';
            } },
        ],
      });
    }

    const ci = g('confirmInfo');
    if (ci) ci.innerHTML = '<strong>' + ok + '</strong> new · <strong>' + upd + '</strong> to update · <strong>' + (dup + err) + '</strong> skipped';
  }

  window.confirmImport = function (btn) {
    if (!state.uploadPreview) { toast('No preview data', 'warning'); return; }
    const orig = btn.innerHTML;
    btn.innerHTML = '<span class="loading-spinner"></span> Importing…'; btn.disabled = true;
    fetch(API.confirm, {
      method: 'POST', credentials: 'same-origin',
      headers: { 'Content-Type': 'application/json', 'X-CSRFToken': CSRF },
      body: JSON.stringify({ token: state.uploadPreview.token }),
    })
      .then(function (r) { return r.json().then(function (d) { return { ok: r.ok, data: d }; }); })
      .then(function (res) {
        if (!res.ok) throw new Error(res.data.message || res.data.detail || 'Import failed');
        toast((res.data.created || 0) + ' added · ' + (res.data.updated || 0) + ' updated', 'success');
        state.uploadPreview = null;
        resetFilters();
        clearFile();
        showTab('tab-list');
        loadList();
      })
      .catch(function (e) { toast('Import error: ' + e.message, 'error'); })
      .finally(function () { btn.innerHTML = orig; btn.disabled = false; });
  };

  window.cancelImport = function () {
    state.uploadPreview = null;
    clearFile();
    switchLmTab('tab-upload');
    toast('Import cancelled');
  };

  /* ─── INIT ────────────────────────────────────── */
  document.addEventListener('DOMContentLoaded', function () {

    qsa('.lm-tab-content').forEach(function (c) {
      const on = c.id === 'tab-list';
      c.classList.toggle('active', on);
      c.style.display = on ? 'block' : 'none';
    });
    qsa('.lm-tab-btn').forEach(function (b) {
      b.classList.toggle('active', b.dataset.tab === 'tab-list');
    });

    qsa('.fi').forEach(function (el) { el.style.opacity = '1'; });

    loadList();
    initDropZone();

    /* ── Advanced Filter init ── */
    if (typeof AdvFilter !== 'undefined') {
      advFilter = new AdvFilter({
        searchWrapId: 'lmSearchWrap',
        chipsId:      'lmAdvChips',
        onApply: function () { state.page = 1; applyFilters(); },
        fields: [
          { key: 'pincode',     label: 'Pincode',       type: 'text'   },
          { key: 'name',        label: 'Location Name', type: 'text'   },
          { key: 'city',        label: 'City',          type: 'text'   },
          { key: 'state_name',  label: 'State',         type: 'select',
            options: ((window.LP_MASTERS && LP_MASTERS.states) || []).map(function (s) { return { v: s, l: s }; }) },
          { key: 'district',    label: 'District',      type: 'text'   },
          { key: 'zone',        label: 'Zone',          type: 'select',
            options: ((window.LP_MASTERS && LP_MASTERS.zones) || []).map(function (z) { return { v: z, l: z }; }) },
          { key: 'courier_name', label: 'Courier Name', type: 'select',
            options: ((window.LP_MASTERS && LP_MASTERS.couriers) || []).map(function (c) { return { v: c.name, l: c.name }; }) },
          { key: 'mode',         label: 'Mode',         type: 'select',
            options: ((window.LP_MASTERS && LP_MASTERS.modes) || []).map(function (m) { return { v: m, l: m }; }) },
          { key: 'is_active', label: 'Status', type: 'select',
            options: [
              { v: 'true',  l: 'Active'   },
              { v: 'false', l: 'Inactive' },
            ]
          },
        ],
      });
    }

    const ov = g('deleteModalOverlay');
    if (ov) ov.addEventListener('click', function (e) { if (e.target === ov) closeDeleteModal(); });
    const eov = g('editModalOverlay');
    if (eov) eov.addEventListener('click', function (e) { if (e.target === eov) closeEditModal(); });
    document.addEventListener('keydown', function (e) { if (e.key === 'Escape') { closeDeleteModal(); closeEditModal(); } });

    qsa('.form-input, .form-select').forEach(function (el) {
      el.addEventListener('input', function () {
        if (el.classList.contains('error') && el.value.trim()) {
          el.classList.remove('error');
          const err = qs('[data-err="' + el.name + '"]');
          if (err) err.textContent = '';
        }
      });
    });

    let st;
    const se = g('listSearch');
    if (se) se.addEventListener('input', function () {
      clearTimeout(st);
      st = setTimeout(function () { onListSearch(se.value); }, 350);
    });
  });

})();
