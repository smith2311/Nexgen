/* ═══════════════════════════════════════════════════════
   division_master.js
   Mirrors courier_master.js (same UI/feature set) for the
   Division Master — fields: companyname, division.
   Delete is a SOFT delete (row hidden, data retained).
   Uses shared utilities from data_table.js (LogiTable).
═══════════════════════════════════════════════════════ */

(function () {
  'use strict';

  /* ─── CONFIG ─────────────────────────────────────── */
  const API = {
    list:    DM_CONFIG.urls.list,
    create:  DM_CONFIG.urls.create,
    update:  DM_CONFIG.urls.update,
    delete:  DM_CONFIG.urls.delete,
    upload:  DM_CONFIG.urls.upload,
    preview: DM_CONFIG.urls.preview,
    confirm: DM_CONFIG.urls.confirm,
  };
  const CSRF = LogiTable.getCookie('csrftoken');

  /* ─── ADVANCED FILTER ───────────────────────────── */
  let advFilter = null;

  /* ─── STATE ──────────────────────────────────────── */
  let state = {
    divisions:     [],
    allDivisions:  [],
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
  const formatDate = LogiTable.formatDate;

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

    let filtered = state.allDivisions;
    if (advFilter) filtered = advFilter.filterData(filtered);

    if (q) {
      filtered = filtered.filter(function (d) {
        return (d.companyname || '').toLowerCase().includes(q) ||
               (d.division    || '').toLowerCase().includes(q);
      });
    }

    state.totalCount = filtered.length;

    const totalPages = Math.max(1, Math.ceil(state.totalCount / state.perPage));
    if (state.page > totalPages) state.page = totalPages;

    const start = (state.page - 1) * state.perPage;
    state.divisions = filtered.slice(start, start + state.perPage);

    renderTable();
    LogiTable.renderPagination(state);

    const sel = g('lmPerPageSelect');
    if (sel && parseInt(sel.value) !== state.perPage) sel.value = state.perPage;

    const companies = new Set(filtered.map(function (d) { return d.companyname; }).filter(Boolean)).size;
    const divisions = new Set(filtered.map(function (d) { return d.division; }).filter(Boolean)).size;
    const codes = new Set(filtered.map(function (d) { return d.code; })
                          .filter(function (c) { return c != null && c !== ''; })).size;
    updateHeaderStats({ total: state.totalCount, companies: companies, divisions: divisions, codes: codes });

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
        state.allDivisions = Array.isArray(data) ? data : (data.data || data.results || []);
        state.page = 1;
        applyFilters();
      })
      .catch(function (e) { toast('Could not load divisions: ' + e.message, 'error'); })
      .finally(function () { if (wrap) wrap.style.display = 'none'; });
  }

  function renderTable() {
    const body = g('tblBody');
    if (!body) return;

    if (!state.divisions.length) {
      body.innerHTML = '<div class="empty-state">' +
        '<i class="fas fa-sitemap"></i>' +
        '<p>No divisions found. <a href="javascript:void(0)" onclick="openAdd()" style="color:var(--blue)">Add one</a> or adjust filters.</p>' +
        '</div>';
      return;
    }

    body.innerHTML = state.divisions.map(function (d, i) {
      return '<div class="tbl-row" style="animation-delay:' + (i * 0.03) + 's">' +
        '<div class="tbl-id">#' + d.id + '</div>' +
        '<div><div class="tbl-name">' + escHtml(d.companyname || '—') + '</div></div>' +
        '<div><span class="badge badge-blue">' + escHtml(d.division || '—') + '</span></div>' +
        '<div>' + escHtml(d.code != null && d.code !== '' ? String(d.code) : '—') + '</div>' +
        '<div>' + escHtml(d.created_by || '—') + '</div>' +
        '<div class="tbl-sub">' + escHtml(formatDate(d.created_date)) + '</div>' +
        '<div class="tbl-actions">' +
          '<button class="tbl-btn edit" onclick="openEdit(' + d.id + ')"><i class="fas fa-pencil-alt"></i> Edit</button>' +
          '<button class="tbl-btn del"  onclick="openDelete(' + d.id + ')"><i class="fas fa-trash"></i></button>' +
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
    animateVal(g('statTotal'),     stats.total     || state.totalCount);
    animateVal(g('statCompanies'), stats.companies || 0);
    animateVal(g('statDivisions'), stats.divisions || 0);
    if (g('statCode')) animateVal(g('statCode'), stats.codes || 0);
  }

  window.onListSearch = function (val) { state.search = val; state.page = 1; applyFilters(); };

  /* ═══ TAB 2 — ADD / EDIT FORM ═══════════════════ */
  const FORM_FIELDS = ['companyname', 'division', 'code'];

  /* ═══ EDIT MODAL ═══ */
  function _fillEditModal(d) {
    state.editingId = d.id;

    FORM_FIELDS.forEach(function (f) {
      const el = g('efield_' + f);
      if (!el) return;
      el.value = d[f] == null ? '' : d[f];
    });

    if (g('editModalTitle'))    g('editModalTitle').textContent    = 'Edit Division';
    if (g('editModalSubtitle')) g('editModalSubtitle').textContent = 'Editing: ' + (d.companyname || '') + ' — ' + (d.division || '');

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
    const d = state.divisions.find(function (x) { return x.id === id; }) ||
              state.allDivisions.find(function (x) { return x.id === id; });
    if (d) {
      _fillEditModal(d);
    } else {
      toast('Could not load division', 'error');
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
    if (g('editModalTitle'))    g('editModalTitle').textContent    = 'Add New Division';
    if (g('editModalSubtitle')) g('editModalSubtitle').textContent = 'Create a new division';
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
        toast(res.result.message || (isEdit ? 'Division updated!' : 'Division created!'), 'success');
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

  /* ═══ DELETE MODAL (soft delete) ═════════════════ */
  window.openDelete = function (id) {
    const d = state.divisions.find(function (x) { return x.id === id; }) ||
              state.allDivisions.find(function (x) { return x.id === id; });
    if (!d) return;
    state.deleteId = id;
    const rowsEl = g('deleteModalRows');
    if (rowsEl) {
      rowsEl.innerHTML = [
        ['Company Name', d.companyname || '—'],
        ['Division', d.division || '—'],
        ['Code', (d.code != null && d.code !== '') ? String(d.code) : '—'],
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
    btn.innerHTML = '<span class="loading-spinner"></span> Removing…'; btn.disabled = true;
    fetch(API.delete + state.deleteId + '/', {
      method: 'DELETE', credentials: 'same-origin',
      headers: { 'X-CSRFToken': CSRF },
    })
      .then(function (r) {
        if (!r.ok) throw new Error('Remove failed');
        toast('Division removed', 'success');
        closeDeleteModal();
        loadList();
      })
      .catch(function (e) { toast('Could not remove: ' + e.message, 'error'); })
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
      'companyname,division,code',
      'Meril Life Sciences,MEPL,1',
      'Meril Diagnostics,MDPL,2',
    ].join('\n');
    const a = document.createElement('a');
    a.href = 'data:text/csv,' + encodeURIComponent(csv);
    a.download = 'division_master_sample.csv';
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
          { key: 'row',  label: '#',            width: '56px',  sortable: true,
            value: function (r) { return r.row; }, className: 'prev-row-num' },
          { key: 'company', label: 'Company Name', width: '1.4fr', sortable: true, searchable: true,
            value:  function (r) { return (r.data || {}).companyname || ''; },
            render: function (r) { return escHtml((r.data || {}).companyname || '—'); } },
          { key: 'division', label: 'Division', width: '1fr', sortable: true, searchable: true,
            value:  function (r) { return (r.data || {}).division || ''; },
            render: function (r) { return '<span class="badge badge-blue">' + escHtml((r.data || {}).division || '—') + '</span>'; } },
          { key: 'code', label: 'Code', width: '90px', sortable: true, searchable: true,
            value:  function (r) { return (r.data || {}).code || ''; },
            render: function (r) { var c = (r.data || {}).code; return escHtml(c != null && c !== '' ? String(c) : '—'); } },
          { key: 'status', label: 'Status',     width: '110px', sortable: true,
            value: function (r) { return r.status; }, render: previewStatusBadge },
          { key: 'reason', label: 'Reason',     width: '1.6fr', sortable: true, searchable: true,
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
        toast((res.data.created || 0) + ' divisions added', 'success');
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
          { key: 'companyname', label: 'Company Name', type: 'text' },
          { key: 'division',    label: 'Division',     type: 'select',
            options: ((window.LP_DIVISIONS && LP_DIVISIONS.length ? LP_DIVISIONS : []))
                       .map(function (d) { return { v: d.code, l: d.label }; }) },
          { key: 'code',        label: 'Code',         type: 'text' },
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
