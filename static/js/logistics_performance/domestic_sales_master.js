/* ═══════════════════════════════════════════════════════
   domestic_sales_master.js
   Uses shared utilities from logi_datatable.js (LogiTable)
═══════════════════════════════════════════════════════ */

(function () {
  'use strict';

  /* ─── CONFIG ─────────────────────────────────────── */
  const API = {
    list:    DSM_CONFIG.urls.list,
    create:  DSM_CONFIG.urls.create,
    update:  DSM_CONFIG.urls.update,
    delete:  DSM_CONFIG.urls.delete,
    upload:  DSM_CONFIG.urls.upload,
    preview: DSM_CONFIG.urls.preview,
    confirm: DSM_CONFIG.urls.confirm,
  };
  const CSRF = LogiTable.getCookie('csrftoken');

  /* ─── ADVANCED FILTER ───────────────────────────── */
  let advFilter = null;

  /* ─── STATE ──────────────────────────────────────── */
  let state = {
    records:        [],
    allRecords:     [],
    page:           1,
    perPage:        15,
    search:         '',
    divisionFilter: '',
    editingId:      null,
    deleteId:       null,
    uploadPreview:  null,
    totalCount:     0,
  };

  /* ─── UTILS ──────────────────────────────────────── */
  function g(id)         { return document.getElementById(id); }
  function qs(sel, ctx)  { return (ctx || document).querySelector(sel); }
  function qsa(sel, ctx) { return [...(ctx || document).querySelectorAll(sel)]; }

  const escHtml           = LogiTable.escHtml;
  const animateVal        = LogiTable.animateVal;
  const formatDate        = LogiTable.formatDate;
  const divisionBadgeClass = LogiTable.divisionBadgeClass;

  // Interpret a raw pending_show value from an uploaded CSV. Accepts the
  // literal status words ("pending"/"active") as well as true/false/yes/1.
  function isPendingValue(v) {
    return ['pending', 'true', 'yes', '1'].indexOf(String(v == null ? '' : v).trim().toLowerCase()) !== -1;
  }

  /* ═══ TOAST ══════════════════════════════════════ */
  function toast(msg, type) {
    type = type || 'success';
    const el = g('dsmToast');
    if (!el) return;
    el.className = 'dsm-toast ' + type;
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
    qsa('.dsm-tab-btn').forEach(function (b) { b.classList.toggle('active', b.dataset.tab === tabId); });
    qsa('.dsm-tab-content').forEach(function (c) {
      var on = c.id === tabId;
      c.classList.toggle('active', on);
      c.style.display = on ? 'block' : 'none';
    });
  }

  window.switchDsmTab = function (tabId) {
    showTab(tabId);
    if (tabId === 'tab-list') loadList();
  };

  /* ═══ TAB 1 — LIST ═══════════════════════════════ */
  function applyFilters() {
    const q   = state.search.toLowerCase().trim();
    const div = state.divisionFilter;

    let filtered = state.allRecords;
    // Apply advanced filters
    if (advFilter) filtered = advFilter.filterData(filtered);

    if (div) {
      filtered = filtered.filter(function (r) { return (r.division || '') === div; });
    }
    if (q) {
      filtered = filtered.filter(function (r) {
        return (r.sales_type             || '').toLowerCase().includes(q) ||
               (r.division               || '').toLowerCase().includes(q) ||
               (r.sales_type_description || '').toLowerCase().includes(q);
      });
    }

    state.totalCount = filtered.length;

    const totalPages = Math.max(1, Math.ceil(state.totalCount / state.perPage));
    if (state.page > totalPages) state.page = totalPages;

    const start = (state.page - 1) * state.perPage;
    state.records = filtered.slice(start, start + state.perPage);

    renderTable();
    LogiTable.renderPagination(state);

    const sel = g('dsmPerPageSelect');
    if (sel && parseInt(sel.value) !== state.perPage) sel.value = state.perPage;

    const activeCount  = filtered.filter(function (r) { return !r.pending_show; }).length;
    const pendingCount = filtered.filter(function (r) { return  r.pending_show; }).length;
    const divCount     = new Set(filtered.map(function (r) { return r.division; }).filter(Boolean)).size;
    updateHeaderStats({ total: state.totalCount, active: activeCount, divisions: divCount, pending: pendingCount });

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
        state.allRecords = Array.isArray(data) ? data : (data.data || data.results || []);
        state.page = 1;
        applyFilters();
      })
      .catch(function (e) { toast('Could not load records: ' + e.message, 'error'); })
      .finally(function () { if (wrap) wrap.style.display = 'none'; });
  }

  function renderTable() {
    const body = g('tblBody');
    if (!body) return;

    if (!state.records.length) {
      body.innerHTML = '<div class="empty-state">' +
        '<i class="fas fa-tags"></i>' +
        '<p>No sales types found. <a href="javascript:void(0)" onclick="openAdd()" style="color:var(--blue)">Add one</a> or adjust filters.</p>' +
        '</div>';
      return;
    }

    body.innerHTML = state.records.map(function (rec, i) {
      return '<div class="tbl-row" style="animation-delay:' + (i * 0.03) + 's">' +
        '<div class="tbl-id">#' + rec.id + '</div>' +
        '<div><span class="badge ' + divisionBadgeClass(rec.division) + '">' + escHtml(rec.division || '—') + '</span></div>' +
        '<div><div class="tbl-name">' + escHtml(rec.sales_type || '—') + '</div></div>' +
        '<div class="tbl-desc" title="' + escHtml(rec.sales_type_description || '') + '">' + escHtml(rec.sales_type_description || '—') + '</div>' +
        '<div><span class="badge badge-' + (rec.pending_show ? 'amber' : 'green') + '">' + (rec.pending_show ? 'Pending' : 'Active') + '</span></div>' +
        '<div class="tbl-id">' + formatDate(rec.created_date) + '</div>' +
        '<div class="tbl-actions">' +
          '<button class="tbl-btn edit" onclick="openEdit(' + rec.id + ')"><i class="fas fa-pencil-alt"></i> Edit</button>' +
          '<button class="tbl-btn del"  onclick="openDelete(' + rec.id + ')"><i class="fas fa-trash"></i></button>' +
        '</div>' +
        '</div>';
    }).join('');
  }

  window.goPage = function (p) {
    const totalPages = Math.ceil(state.totalCount / state.perPage);
    if (p < 1 || p > totalPages) return;
    state.page = p; applyFilters();
  };

  window.onDsmPerPageChange = function (val) {
    state.perPage = parseInt(val);
    state.page = 1;
    applyFilters();
  };

  function updateHeaderStats(stats) {
    animateVal(g('statTotal'),     stats.total     || 0);
    animateVal(g('statActive'),    stats.active    || 0);
    animateVal(g('statDivisions'), stats.divisions || 0);
    animateVal(g('statPending'),   stats.pending   || 0);
  }

  window.onListSearch = function (val) { state.search = val; state.page = 1; applyFilters(); };

  window.setDivisionFilter = function (div, btn) {
    state.divisionFilter = div; state.page = 1;
    qsa('.filter-pill[data-division]').forEach(function (b) { b.classList.remove('active'); });
    if (btn) btn.classList.add('active');
    applyFilters();
  };

  /* ═══ TAB 2 — ADD / EDIT FORM ═══════════════════ */
  const FORM_FIELDS = ['division', 'sales_type', 'sales_type_description', 'pending_show'];

  /* ═══ EDIT MODAL (centered popup — matches Performance Upload) ═══ */
  function _fillEditModal(rec) {
    state.editingId = rec.id;

    FORM_FIELDS.forEach(function (f) {
      const el = g('efield_' + f);
      if (!el) return;
      let val = rec[f];
      if (val == null) val = '';
      if (f === 'pending_show') el.value = val ? 'true' : 'false';
      else                      el.value = val;
    });

    if (g('editModalTitle'))    g('editModalTitle').textContent    = 'Edit Sales Type';
    if (g('editModalSubtitle')) g('editModalSubtitle').textContent = 'Editing: ' + (rec.sales_type || '');

    const form = g('salesEditForm');
    if (form) {
      qsa('.form-input,.form-select,.form-textarea', form).forEach(function (el) { el.classList.remove('error'); });
      qsa('.form-error', form).forEach(function (el) { el.textContent = ''; });
    }

    const ov = g('editModalOverlay');
    if (ov) ov.classList.add('open');
    document.body.style.overflow = 'hidden';
  }

  window.openEdit = function (id) {
    const rec = state.records.find(function (r) { return r.id === id; });
    if (rec) {
      _fillEditModal(rec);
    } else {
      fetch(API.update + id + '/', { credentials: 'same-origin' })
        .then(function (r) { if (!r.ok) throw new Error('Not found'); return r.json(); })
        .then(function (data) { _fillEditModal(data); })
        .catch(function (e) { toast('Could not load record: ' + e.message, 'error'); });
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
    const form = g('salesEditForm');
    if (form) {
      form.reset();
      qsa('.form-input,.form-select,.form-textarea', form).forEach(function (el) { el.classList.remove('error'); });
      qsa('.form-error', form).forEach(function (el) { el.textContent = ''; });
    }
    if (g('editModalTitle'))    g('editModalTitle').textContent    = 'Add New Sales Type';
    if (g('editModalSubtitle')) g('editModalSubtitle').textContent = 'Create a new sales type';
    const ov = g('editModalOverlay');
    if (ov) ov.classList.add('open');
    document.body.style.overflow = 'hidden';
  };

  window.submitEditModal = function (btn) {
    const form = g('salesEditForm');
    if (!validateForm(form)) return;

    const data = {};
    qsa('.form-input,.form-select,.form-textarea', form).forEach(function (el) {
      if (!el.name) return;
      const v = el.value.trim();
      data[el.name] = el.name === 'pending_show' ? v === 'true' : v;
    });

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
        toast(res.result.message || (isEdit ? 'Sales type updated!' : 'Sales type created!'), 'success');
        closeEditModal();
        loadList();
      })
      .catch(function (e) { toast('Server error: ' + e.message, 'error'); })
      .finally(function () { btn.innerHTML = orig; btn.disabled = false; });
  };

  function resetFilters() {
    state.search = ''; state.divisionFilter = ''; state.page = 1;
    const se = g('listSearch'); if (se) se.value = '';
    qsa('.filter-pill[data-division]').forEach(function (b) { b.classList.remove('active'); });
    const ap = qs('.filter-pill[data-division=""]'); if (ap) ap.classList.add('active');
  }

  function validateForm(form) {
    let valid = true;
    qsa('[required]', form).forEach(function (el) {
      const err = form.querySelector('[data-err="' + el.name + '"]');
      const empty = !el.value.trim();
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
    const rec = state.records.find(function (r) { return r.id === id; });
    if (!rec) return;
    state.deleteId = id;
    const rowsEl = g('deleteModalRows');
    if (rowsEl) {
      rowsEl.innerHTML = [
        ['Division',    rec.division    || '—'],
        ['Sales Type',  rec.sales_type  || '—'],
        ['Status',      rec.pending_show ? 'Pending' : 'Active'],
        ['Description', (rec.sales_type_description || '—').substring(0, 60)],
      ].map(function (pair) {
        return '<div class="dsm-modal-record-row">' +
          '<span class="dsm-modal-record-lbl">' + pair[0] + '</span>' +
          '<span class="dsm-modal-record-val">' + escHtml(String(pair[1])) + '</span></div>';
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
        toast('Record deleted', 'success');
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
      nameId:  'fileSelectedName',
      sizeId:  'fileSelectedSize',
      rowId:   'fileSelectedRow',
      zoneId:  'dropZone',
      refId:   'uploadFileRef',
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
        switchDsmTab('tab-preview');
        toast('File uploaded — review before importing', 'info');
      })
      .catch(function (e) { toast('Upload error: ' + e.message, 'error'); })
      .finally(function () { btn.innerHTML = orig; btn.disabled = false; });
  };

  window.downloadSample = function () {
    const csv = [
      'division,sales_type,sales_type_description,pending_show',
      'Cardio,Direct Sale,Direct sales to hospital,active',
      'Endo,Consignment,Consignment based sale,active',
      'Ortho,Tender,Government tender sale,pending',
      'Diag,Demo,Demonstration unit,active',
    ].join('\n');
    const a = document.createElement('a');
    a.href = 'data:text/csv,' + encodeURIComponent(csv);
    a.download = 'domestic_sales_type_sample.csv';
    a.click();
    toast('Sample CSV downloaded', 'success');
  };

  /* ═══ TAB 4 — IMPORT PREVIEW ════════════════════ */
  let previewTable = null;

  // status -> [rowClass, icon, label]
  const PREV_STATUS = {
    ok:        ['ok',  'fa-check', 'Ready'],
    duplicate: ['dup', 'fa-clone', 'Duplicate'],
    error:     ['err', 'fa-times', 'Error'],
  };
  function previewStatusBadge(row) {
    const info = PREV_STATUS[row.status] || ['err', 'fa-times', 'Error'];
    return '<span class="prev-status ' + info[0] + '"><i class="fas ' + info[1] + '"></i> ' + info[2] + '</span>';
  }

  function renderPreview(data) {
    const rows = data.rows || [];
    const ok  = rows.filter(function (r) { return r.status === 'ok'; }).length;
    const dup = rows.filter(function (r) { return r.status === 'duplicate'; }).length;
    const err = rows.filter(function (r) { return r.status === 'error'; }).length;
    animateVal(g('prevTotal'), rows.length);
    animateVal(g('prevOk'),    ok);
    animateVal(g('prevWarn'),  dup + err);

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
          { key: 'ok',  label: 'Ready',     test: function (r) { return r.status === 'ok'; } },
          { key: 'dup', label: 'Duplicate', test: function (r) { return r.status === 'duplicate'; } },
          { key: 'err', label: 'Error',     test: function (r) { return r.status === 'error'; } },
        ],
        columns: [
          { key: 'row', label: '#', width: '56px', sortable: true,
            value: function (r) { return r.row; }, className: 'prev-row-num' },
          { key: 'division', label: 'Division', width: '110px', sortable: true, searchable: true,
            value:  function (r) { return (r.data || {}).division || ''; },
            render: function (r) { return '<span class="badge ' + divisionBadgeClass((r.data || {}).division) + '">' + escHtml((r.data || {}).division || '—') + '</span>'; } },
          { key: 'sales_type', label: 'Sales Type', width: '1fr', sortable: true, searchable: true,
            value:  function (r) { return (r.data || {}).sales_type || ''; },
            render: function (r) { return escHtml((r.data || {}).sales_type || '—'); } },
          { key: 'desc', label: 'Description', width: '1.4fr', sortable: true, searchable: true,
            value:  function (r) { return (r.data || {}).sales_type_description || ''; },
            render: function (r) { return '<span style="font-size:12px;color:var(--text-secondary)">' + escHtml(((r.data || {}).sales_type_description || '—').substring(0, 60)) + '</span>'; } },
          { key: 'pending', label: 'Pending', width: '90px', sortable: true,
            value:  function (r) { return isPendingValue((r.data || {}).pending_show) ? 'Pending' : 'Active'; },
            render: function (r) { var p = isPendingValue((r.data || {}).pending_show); return '<span class="badge badge-' + (p ? 'amber' : 'green') + '">' + (p ? 'Pending' : 'Active') + '</span>'; } },
          { key: 'status', label: 'Status', width: '110px', sortable: true,
            value: function (r) { return r.status; }, render: previewStatusBadge },
        ],
      });
    }

    const ci = g('confirmInfo');
    if (ci) ci.innerHTML = '<strong>' + ok + '</strong> row' + (ok !== 1 ? 's' : '') + ' will be imported · <strong>' + (dup + err) + '</strong> will be skipped';
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
        toast((res.data.imported || 0) + ' records imported!', 'success');
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
    switchDsmTab('tab-upload');
    toast('Import cancelled');
  };

  /* ─── INIT ────────────────────────────────────── */
  document.addEventListener('DOMContentLoaded', function () {

    qsa('.dsm-tab-content').forEach(function (c) {
      const on = c.id === 'tab-list';
      c.classList.toggle('active', on);
      c.style.display = on ? 'block' : 'none';
    });
    qsa('.dsm-tab-btn').forEach(function (b) {
      b.classList.toggle('active', b.dataset.tab === 'tab-list');
    });

    qsa('.fi').forEach(function (el) { el.style.opacity = '1'; });

    loadList();
    initDropZone();

    /* ── Advanced Filter init ── */
    if (typeof AdvFilter !== 'undefined') {
      advFilter = new AdvFilter({
        searchWrapId: 'dsmSearchWrap',
        chipsId:      'dsmAdvChips',
        onApply: function () { state.page = 1; applyFilters(); },
        fields: [
          { key: 'division',               label: 'Division',    type: 'select',
            options: ((window.LP_DIVISIONS && window.LP_DIVISIONS.length) ? window.LP_DIVISIONS : [])
                       .map(d => ({ v: d.code, l: d.label })) },
          { key: 'sales_type',             label: 'Sales Type',   type: 'text' },
          { key: 'sales_type_description', label: 'Description',  type: 'text' },
          { key: 'pending_show',           label: 'Status',       type: 'select',
            options: [
              { v: 'false', l: 'Active'  },
              { v: 'true',  l: 'Pending' },
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
