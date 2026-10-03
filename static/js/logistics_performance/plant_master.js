/* ═══════════════════════════════════════════════════════
   plant_master.js
   Mirrors state_master.js (same UI/feature set) for the
   Plant Master — fields: plant_name (unique), plant_company,
   plant_email, plant_address.
   Uses shared components: master_page, data_table, modal, adv_filter.
═══════════════════════════════════════════════════════ */

(function () {
  'use strict';

  /* ─── CONFIG ─────────────────────────────────────── */
  const API = {
    list:    PL_CONFIG.urls.list,
    create:  PL_CONFIG.urls.create,
    update:  PL_CONFIG.urls.update,
    delete:  PL_CONFIG.urls.delete,
    upload:  PL_CONFIG.urls.upload,
    preview: PL_CONFIG.urls.preview,
    confirm: PL_CONFIG.urls.confirm,
  };
  const CSRF = LogiTable.getCookie('csrftoken');

  /* ─── ADVANCED FILTER ───────────────────────────── */
  let advFilter = null;

  /* ─── STATE ──────────────────────────────────────── */
  let state = {
    plants:        [],
    allPlants:     [],
    page:          1,
    perPage:       15,
    search:        '',
    editingId:     null,
    deleteId:      null,
    uploadPreview: null,
    totalCount:    0,
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
    const q = state.search.toLowerCase().trim();

    let filtered = state.allPlants;
    if (advFilter) filtered = advFilter.filterData(filtered);

    if (q) {
      filtered = filtered.filter(function (p) {
        return (p.plant_name || '').toLowerCase().includes(q) ||
               (p.plant_company || '').toLowerCase().includes(q) ||
               (p.plant_email || '').toLowerCase().includes(q);
      });
    }

    state.totalCount = filtered.length;

    const totalPages = Math.max(1, Math.ceil(state.totalCount / state.perPage));
    if (state.page > totalPages) state.page = totalPages;

    const start = (state.page - 1) * state.perPage;
    state.plants = filtered.slice(start, start + state.perPage);

    renderTable();
    LogiTable.renderPagination(state);

    const sel = g('lmPerPageSelect');
    if (sel && parseInt(sel.value) !== state.perPage) sel.value = state.perPage;

    updateHeaderStats({ total: state.totalCount });

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
        state.allPlants = Array.isArray(data) ? data : (data.data || data.results || []);
        state.page = 1;
        applyFilters();
      })
      .catch(function (e) { toast('Could not load plants: ' + e.message, 'error'); })
      .finally(function () { if (wrap) wrap.style.display = 'none'; });
  }

  function renderTable() {
    const body = g('tblBody');
    if (!body) return;

    if (!state.plants.length) {
      body.innerHTML = '<div class="empty-state">' +
        '<i class="fas fa-industry"></i>' +
        '<p>No plants found. <a href="javascript:void(0)" onclick="openAdd()" style="color:var(--blue)">Add one</a> or adjust filters.</p>' +
        '</div>';
      return;
    }

    body.innerHTML = state.plants.map(function (p, i) {
      return '<div class="tbl-row" style="animation-delay:' + (i * 0.03) + 's">' +
        '<div class="tbl-id">#' + p.id + '</div>' +
        '<div><div class="tbl-name">' + escHtml(p.plant_name || '—') + '</div></div>' +
        '<div><span class="tbl-sub">' + escHtml(p.plant_company || '—') + '</span></div>' +
        '<div><span class="tbl-sub">' + escHtml(p.plant_email || '—') + '</span></div>' +
        '<div><span class="tbl-sub">' + escHtml(p.plant_address || '—') + '</span></div>' +
        '<div class="tbl-actions">' +
          '<button class="tbl-btn edit" onclick="openEdit(' + p.id + ')"><i class="fas fa-pencil-alt"></i> Edit</button>' +
          '<button class="tbl-btn del"  onclick="openDelete(' + p.id + ')"><i class="fas fa-trash"></i></button>' +
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
    animateVal(g('statTotal'), stats.total || state.totalCount);
  }

  window.onListSearch = function (val) { state.search = val; state.page = 1; applyFilters(); };

  /* ═══ TAB 2 — ADD / EDIT FORM ═══════════════════ */
  const FORM_FIELDS = ['plant_name', 'plant_company', 'plant_email', 'plant_address'];

  /* ═══ EDIT MODAL ═══ */
  function _fillEditModal(p) {
    state.editingId = p.id;

    FORM_FIELDS.forEach(function (f) {
      const el = g('efield_' + f);
      if (!el) return;
      el.value = p[f] == null ? '' : p[f];
    });

    if (g('editModalTitle'))    g('editModalTitle').textContent    = 'Edit Plant';
    if (g('editModalSubtitle')) g('editModalSubtitle').textContent = 'Editing: ' + (p.plant_name || '');

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
    const p = state.plants.find(function (x) { return x.id === id; }) ||
              state.allPlants.find(function (x) { return x.id === id; });
    if (p) {
      _fillEditModal(p);
    } else {
      toast('Could not load plant', 'error');
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
    if (g('editModalTitle'))    g('editModalTitle').textContent    = 'Add New Plant';
    if (g('editModalSubtitle')) g('editModalSubtitle').textContent = 'Create a new plant';
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
      data[el.name] = el.value.trim();
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
          handleFormErrors(res.result.errors || res.result, 'efield_');
          toast(res.result.message || res.result.detail || 'Validation failed', 'error');
          return;
        }
        toast(res.result.message || (isEdit ? 'Plant updated!' : 'Plant created!'), 'success');
        closeEditModal();
        loadList();
      })
      .catch(function (e) { toast('Server error: ' + e.message, 'error'); })
      .finally(function () { btn.innerHTML = orig; btn.disabled = false; });
  };

  function resetFilters() {
    state.search = ''; state.page = 1;
    const se = g('listSearch'); if (se) se.value = '';
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

  function handleFormErrors(errors, prefix) {
    prefix = prefix || 'field_';
    if (!errors || typeof errors !== 'object') return;
    Object.entries(errors).forEach(function (entry) {
      const field = entry[0], msgs = entry[1];
      const el  = g(prefix + field);
      const err = qs('[data-err="' + field + '"]');
      if (el)  el.classList.add('error');
      if (err) err.textContent = Array.isArray(msgs) ? msgs[0] : msgs;
    });
  }

  /* ═══ DELETE MODAL ═══════════════════════════════ */
  window.openDelete = function (id) {
    const p = state.plants.find(function (x) { return x.id === id; }) ||
              state.allPlants.find(function (x) { return x.id === id; });
    if (!p) return;
    state.deleteId = id;
    const rowsEl = g('deleteModalRows');
    if (rowsEl) {
      rowsEl.innerHTML = [
        ['Plant Name', p.plant_name || '—'],
        ['Company',    p.plant_company || '—'],
        ['Email',      p.plant_email || '—'],
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
        toast('Plant deleted', 'success');
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
      'plant name,plant company,plant email,plant address',
      '3042,Meril Life Sciences,plant3042@meril.in,Vapi Gujarat',
      '3050,Meril Diagnostics,plant3050@meril.in,Surat Gujarat',
    ].join('\n');
    const a = document.createElement('a');
    a.href = 'data:text/csv,' + encodeURIComponent(csv);
    a.download = 'plant_master_sample.csv';
    a.click();
    toast('Sample CSV downloaded', 'success');
  };

  /* ═══ TAB 4 — IMPORT PREVIEW ════════════════════ */
  let previewTable = null;

  // status -> [rowClass, icon, label]
  const PREV_STATUS = {
    ok:        ['ok',  'fa-plus',  'New'],
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
          { key: 'ok',  label: 'New',       test: function (r) { return r.status === 'ok'; } },
          { key: 'dup', label: 'Duplicate', test: function (r) { return r.status === 'duplicate'; } },
          { key: 'err', label: 'Error',     test: function (r) { return r.status === 'error'; } },
        ],
        columns: [
          { key: 'row',  label: '#',      width: '56px',  sortable: true,
            value: function (r) { return r.row; }, className: 'prev-row-num' },
          { key: 'plant_name', label: 'Plant', width: '1fr', sortable: true, searchable: true,
            value:  function (r) { return (r.data || {}).plant_name || ''; },
            render: function (r) { return '<span class="badge badge-blue">' + escHtml((r.data || {}).plant_name || '—') + '</span>'; } },
          { key: 'plant_company', label: 'Company', width: '1fr', sortable: true, searchable: true,
            value:  function (r) { return (r.data || {}).plant_company || ''; },
            render: function (r) { return '<span class="tbl-sub">' + escHtml((r.data || {}).plant_company || '—') + '</span>'; } },
          { key: 'status', label: 'Status', width: '110px', sortable: true,
            value: function (r) { return r.status; }, render: previewStatusBadge },
          { key: 'reason', label: 'Reason', width: '1.6fr', sortable: true, searchable: true,
            value:  function (r) { return r.message || ''; },
            render: function (r) {
              if (!r.message) return '<span class="tbl-sub">—</span>';
              var color = r.status === 'error' ? 'var(--red)' : 'var(--text-muted)';
              return '<span style="font-size:12px;color:' + color + '">' + escHtml(r.message) + '</span>';
            } },
        ],
      });
    }

    const ci = g('confirmInfo');
    if (ci) ci.innerHTML = '<strong>' + ok + '</strong> new · <strong>' + (dup + err) + '</strong> skipped';
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
        toast((res.data.created || 0) + ' plants added', 'success');
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

    const ov = g('deleteModalOverlay');
    if (ov) ov.addEventListener('click', function (e) { if (e.target === ov) closeDeleteModal(); });
    const eov = g('editModalOverlay');
    if (eov) eov.addEventListener('click', function (e) { if (e.target === eov) closeEditModal(); });
    document.addEventListener('keydown', function (e) { if (e.key === 'Escape') { closeDeleteModal(); closeEditModal(); } });

    qsa('.form-input, .form-select, .form-textarea').forEach(function (el) {
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
