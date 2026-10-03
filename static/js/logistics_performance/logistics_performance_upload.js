/* ═══════════════════════════════════════════════════════
   logistics_performance_upload.js
═══════════════════════════════════════════════════════ */

(function () {
  'use strict';

  /* ─── CONFIG ─────────────────────────────────────── */
  const API = {
    list:    LPU_CONFIG.urls.list,
    detail:  LPU_CONFIG.urls.detail,
    create:  LPU_CONFIG.urls.create,
    update:  LPU_CONFIG.urls.update,
    delete:  LPU_CONFIG.urls.delete,
    upload:  LPU_CONFIG.urls.upload,
    preview: LPU_CONFIG.urls.preview,
    confirm: LPU_CONFIG.urls.confirm,
  };
  const CSRF = getCookie('csrftoken');

  /* ─── ADVANCED FILTER ───────────────────────────── */
  let advFilter = null;

  /* ─── STATE ──────────────────────────────────────── */
  let state = {
    records:       [],
    allRecords:    [],
    page:          1,
    perPage:       15,
    search:        '',
    divisionFilter: '',
    selected:      new Set(),   // selected record ids (bulk delete)
    deleteId:      null,
    editingId:     null,
    uploadPreview: null,
    totalCount:    0,
    serverStats:   null,   // true full-table counts from the API (not the loaded slice)
  };

  /* ─── UTILS ──────────────────────────────────────── */
  function g(id)         { return document.getElementById(id); }
  function qs(sel, ctx)  { return (ctx || document).querySelector(sel); }
  function qsa(sel, ctx) { return [...(ctx || document).querySelectorAll(sel)]; }

  function getCookie(name) {
    const c = document.cookie.split(';').map(s => s.trim()).find(s => s.startsWith(name + '='));
    return c ? decodeURIComponent(c.split('=')[1]) : null;
  }

  function escHtml(str) {
    return String(str ?? '')
      .replace(/&/g, '&amp;').replace(/</g, '&lt;')
      .replace(/>/g, '&gt;').replace(/"/g, '&quot;');
  }

  function animateVal(el, target) {
    if (!el) return;
    target = parseInt(target) || 0;
    let start = null;
    const step = ts => {
      if (!start) start = ts;
      const p = Math.min((ts - start) / 700, 1);
      el.textContent = Math.round((1 - Math.pow(1 - p, 3)) * target).toLocaleString('en-IN');
      if (p < 1) requestAnimationFrame(step);
    };
    requestAnimationFrame(step);
  }

  function formatDate(val) {
    if (!val) return '—';
    try {
      const d = new Date(val);
      if (isNaN(d)) return String(val);
      return d.toLocaleDateString('en-IN', { day: '2-digit', month: 'short', year: 'numeric' });
    } catch { return String(val); }
  }

  // Division list comes from the DB (window.LP_DIVISIONS, injected in base.html).
  function lpDivisions() {
    return (window.LP_DIVISIONS && window.LP_DIVISIONS.length) ? window.LP_DIVISIONS : [];
  }

  function divisionBadgeClass(div) {
    const code = (div || '').toUpperCase();
    const hit = lpDivisions().find(d => (d.code || '').toUpperCase() === code);
    return (hit && hit.badge_class) ? hit.badge_class : 'badge-muted';
  }

  function modeBadge(mode) {
    if (!mode) return '<span class="badge badge-muted">—</span>';
    const lc = mode.toLowerCase();
    if (lc.includes('air'))     return `<span class="badge badge-blue">${escHtml(mode)}</span>`;
    if (lc.includes('surface')) return `<span class="badge badge-teal">${escHtml(mode)}</span>`;
    if (lc.includes('train'))   return `<span class="badge badge-amber">${escHtml(mode)}</span>`;
    return `<span class="badge badge-muted">${escHtml(mode)}</span>`;
  }

  function tatCell(tat) {
    if (tat === null || tat === undefined || tat === '') {
      return '<span class="calc-cell null">—</span>';
    }
    return `<span class="calc-cell">${escHtml(String(tat))}d</span>`;
  }

  function calcCell(val, suffix) {
   if (val === null || val === undefined || val === '' || val === 0) {
      return '<span class="calc-cell null">—</span>';
    }
    return `<span class="calc-cell">${escHtml(String(val))}${suffix || ''}</span>`;
  }

  /* ═══ TOAST ══════════════════════════════════════ */
  function toast(msg, type) {
    type = type || 'success';
    const el = g('lpuToast');
    if (!el) return;
    el.className = 'lpu-toast ' + type;
    el.querySelector('.toast-msg').textContent = msg;
    el.querySelector('i').className =
      type === 'success' ? 'fas fa-check-circle'
      : type === 'error'   ? 'fas fa-times-circle'
      : type === 'warning' ? 'fas fa-exclamation-triangle'
      : 'fas fa-info-circle';
    el.classList.add('show');
    clearTimeout(el._t);
    el._t = setTimeout(() => el.classList.remove('show'), 3200);
  }

  /* ═══ TABS ═══════════════════════════════════════ */
 function showTab(tabId) {
  qsa('.lpu-tab-btn').forEach(b => b.classList.toggle('active', b.dataset.tab === tabId));
  qsa('.lpu-tab-content').forEach(c => {
    const on = c.id === tabId;
    c.classList.toggle('active', on);
    c.style.display = on ? 'block' : 'none';
  });
  // scroll to top on tab switch
  window.scrollTo({ top: 0, behavior: 'smooth' });
}

  window.switchLpuTab = function (tabId) {
  showTab(tabId);
  if (tabId === 'tab-list')    loadList();
  if (tabId === 'tab-edit' && !state.editingId) resetLpuForm();
  if (tabId === 'tab-pending') loadPendingList();
};

  /* ═══ TAB 1 — LIST ═══════════════════════════════ */
  // Data fields that count as a "value" — auto-set fields (id, creator,
  // timestamps, calculated metrics) are excluded so a blank upload row is
  // still treated as empty.
  const ROW_VALUE_FIELDS = [
    'division','location','plant_name','billing_doc','billing_date','inv_value',
    'doc_type','billed_qty','shipto_party','shipto_name','shipto_city','shipto_region',
    'pincode','courier_no','courier_code','courier_name','docket_no','mode','boxes',
    'ch_weight','dispatch_date','delivery_date','basic_logistics_cost','other_charges',
    'remarks','cost_center','zone',
  ];
  function rowHasAnyValue(rec) {
    return ROW_VALUE_FIELDS.some(function (f) {
      var v = rec[f];
      return v !== null && v !== undefined && String(v).trim() !== '';
    });
  }

  function applyFilters() {
    const q   = state.search.toLowerCase().trim();
    const div = state.divisionFilter;

    // Hide rows that have no field values at all
    let filtered = state.allRecords.filter(rowHasAnyValue);
    // Apply advanced filters
    if (advFilter) filtered = advFilter.filterData(filtered);

    if (div) filtered = filtered.filter(r => (r.division || '') === div);
    if (q)   filtered = filtered.filter(r =>
      (r.division   || '').toLowerCase().includes(q) ||
      (r.location   || '').toLowerCase().includes(q) ||
      (r.shipto_name || '').toLowerCase().includes(q) ||
      (r.shipto_city || '').toLowerCase().includes(q) ||
      (r.docket_no  || '').toLowerCase().includes(q) ||
      String(r.pincode  || '').includes(q) ||
      String(r.billing_doc || '').includes(q)
    );

    state.totalCount = filtered.length;
    const totalPages = Math.max(1, Math.ceil(state.totalCount / state.perPage));
    if (state.page > totalPages) state.page = totalPages;

    const start = (state.page - 1) * state.perPage;
    state.records = filtered.slice(start, start + state.perPage);

    renderTable();
    renderPagination();

    // When no filter is active, show the LIVE full-table counts from the server.
    // Only fall back to counting the loaded rows when the user is filtering
    // client-side (the server stats wouldn't reflect that subset).
    const filtersActive = !!q || !!div || filtered.length !== state.allRecords.length;
    if (!filtersActive && state.serverStats) {
      updateHeaderStats({
        total:     state.serverStats.total,
        divisions: state.serverStats.divisions,
        with_edd:  state.serverStats.with_edd,
        delivered: state.serverStats.delivered,
      });
    } else {
      const divSet = new Set(filtered.map(r => r.division).filter(Boolean));
      updateHeaderStats({
        total:     state.totalCount,
        divisions: divSet.size,
        with_edd:  filtered.filter(r => r.expected_delivery_date).length,
        delivered: filtered.filter(r => r.delivery_date).length,
      });
    }

    const badge = g('recordCountBadge');
    if (badge) badge.textContent =
      state.totalCount.toLocaleString('en-IN') + ' record' + (state.totalCount !== 1 ? 's' : '');
  }

  function loadList() {
    const wrap = g('listLoading');
    if (wrap) wrap.style.display = 'block';

    fetch(API.list + '?per_page=10000', { credentials: 'same-origin' })
      .then(r => { if (!r.ok) throw new Error('Server ' + r.status); return r.json(); })
      .then(data => {
        state.allRecords = Array.isArray(data) ? data : (data.data || data.results || []);
        // True counts for the whole table (not just the rows we loaded).
        state.serverStats = (data && data.stats) ? data.stats : null;
        state.page = 1;
        applyFilters();
      })
      .catch(e => toast('Could not load records: ' + e.message, 'error'))
      .finally(() => { if (wrap) wrap.style.display = 'none'; });
  }

  function renderTable() {
    const body = g('tblBody');
    if (!body) return;

    if (!state.records.length) {
      body.innerHTML = `<div class="empty-state">
        <i class="fas fa-truck"></i>
        <p>No logistics records found. <a href="javascript:void(0)" onclick="switchLpuTab('tab-upload')" style="color:var(--blue)">Upload data</a> or adjust filters.</p>
      </div>`;
      updateSelectionUI();
      return;
    }

    const startIdx = (state.page - 1) * state.perPage;
    body.innerHTML = state.records.map((rec, i) => `
      <div class="tbl-row" style="animation-delay:${i * 0.025}s">
        <div class="tbl-sel"><input type="checkbox" class="lpu-rowchk"
             ${state.selected.has(rec.id) ? 'checked' : ''}
             onchange="toggleRowSelect(${rec.id}, this.checked)"></div>
        <div class="tbl-idx">${startIdx + i + 1}</div>
        <div class="tbl-id">#${rec.id}</div>
        <div><button class="tbl-btn view" onclick="viewLpRecord(${rec.id})" title="View full details"><i class="fas fa-eye"></i></button></div>
        <div><span class="badge ${divisionBadgeClass(rec.division)}">${escHtml(rec.division || '—')}</span></div>
        <div class="tbl-trunc">${escHtml(rec.location || '—')}</div>
        <div class="tbl-trunc">${escHtml(rec.plant_name || '—')}</div>
        <div class="tbl-id">${escHtml(String(rec.billing_doc || '—'))}</div>
        <div class="tbl-date">${formatDate(rec.billing_date)}</div>
        <div class="tbl-id">₹${Number(rec.inv_value || 0).toLocaleString('en-IN')}</div>
        <div><span class="badge badge-muted" style="font-size:10px">${escHtml(rec.doc_type || '—')}</span></div>
        <div class="tbl-trunc">${escHtml(rec.shipto_name || '—')}</div>
        <div class="tbl-trunc">${escHtml(rec.shipto_city || '—')} <span class="tbl-sub">${escHtml(rec.shipto_region || '')}</span></div>
        <div class="tbl-id">${escHtml(String(rec.pincode || '—'))}</div>
        <div>${modeBadge(rec.mode)}</div>
        <div class="tbl-id">${escHtml(rec.docket_no || '—')}</div>
        <div class="tbl-trunc">${escHtml(rec.courier_name || '—')}</div>
        <div class="tbl-date">${formatDate(rec.dispatch_date)}</div>
        <div class="tbl-date">${formatDate(rec.delivery_date)}</div>
        <div>${tatCell(rec.tat_from_master)}</div>
        <div class="col-calc">${calcCell(rec.expected_delivery_date ? formatDate(rec.expected_delivery_date) : null, '')}</div>
        <div class="col-calc">${calcCell(rec.dispatch_vs_edd_tat, 'd')}</div>
        <div class="col-calc">${calcCell(rec.dispatch_vs_delivery_tat, 'd')}</div>
        <div class="tbl-id">₹${Number(rec.basic_logistics_cost || 0).toLocaleString('en-IN')}</div>
        <div class="col-calc">${rec.actual_logistics_cost !== null && rec.actual_logistics_cost !== undefined && rec.actual_logistics_cost !== ''
          ? `<span class="calc-cell">₹${Number(rec.actual_logistics_cost).toLocaleString('en-IN')}</span>`
          : '<span class="calc-cell null">—</span>'}</div>
        <div class="tbl-trunc">${escHtml(rec.remarks || '—')}</div>
        <div class="tbl-trunc">${escHtml(rec.creator || '—')}</div>
        <div class="tbl-trunc">${escHtml(rec.cost_center || '—')}</div>
       <div class="tbl-actions">
 <button class="tbl-btn edit" onclick="openLpuEdit(${rec.id})"><i class="fas fa-pencil-alt"></i> Edit</button>
  <button class="tbl-btn del" onclick="openDelete(${rec.id})"><i class="fas fa-trash"></i></button>
</div>
      </div>`).join('');
    updateSelectionUI();
  }

  /* ═══ ROW SELECTION + BULK DELETE ════════════════ */
  function updateSelectionUI() {
    const n = state.selected.size;
    const btn = g('bulkDeleteBtn'), cnt = g('bulkDeleteCount');
    if (btn) btn.style.display = n ? 'inline-flex' : 'none';
    if (cnt) cnt.textContent = n;
    const all = g('selectAllChk');
    if (all) {
      const ids = state.records.map(r => r.id);
      const onPage = ids.filter(id => state.selected.has(id)).length;
      all.checked = ids.length > 0 && onPage === ids.length;
      all.indeterminate = onPage > 0 && onPage < ids.length;
    }
  }

  window.toggleRowSelect = function (id, checked) {
    if (checked) state.selected.add(id); else state.selected.delete(id);
    updateSelectionUI();
  };

  window.toggleSelectAll = function (checked) {
    state.records.forEach(r => {
      if (checked) state.selected.add(r.id); else state.selected.delete(r.id);
    });
    qsa('.lpu-rowchk').forEach(cb => { cb.checked = checked; });
    updateSelectionUI();
  };

  window.bulkDelete = function (btn) {
    const ids = [...state.selected];
    if (!ids.length) { toast('No rows selected', 'warning'); return; }
    if (!confirm(`Delete ${ids.length} selected record(s)? This cannot be undone.`)) return;
    const orig = btn.innerHTML;
    btn.innerHTML = '<span class="loading-spinner"></span> Deleting…'; btn.disabled = true;
    fetch(LPU_CONFIG.urls.bulkDelete, {
      method: 'POST', credentials: 'same-origin',
      headers: { 'Content-Type': 'application/json', 'X-CSRFToken': CSRF },
      body: JSON.stringify({ ids }),
    })
      .then(r => r.json().then(d => ({ ok: r.ok, data: d })))
      .then(({ ok, data }) => {
        if (!ok) throw new Error(data.message || 'Bulk delete failed');
        toast((data.deleted || 0) + ' record(s) deleted', 'success');
        state.selected.clear();
        loadList();
      })
      .catch(e => toast('Could not delete: ' + e.message, 'error'))
      .finally(() => { btn.innerHTML = orig; btn.disabled = false; });
  };

  window.triggerListSearch = function () {
    const se = g('listSearch');
    onListSearch(se ? se.value : '');
  };

 function renderPagination() {
  const totalPages = Math.ceil(state.totalCount / state.perPage);
  const info = g('pagiInfo'), btns = g('pagiBtns');
  if (!info || !btns) return;
  const start = (state.page - 1) * state.perPage + 1;
  const end   = Math.min(state.page * state.perPage, state.totalCount);
  info.textContent = state.totalCount ? `Showing ${start}–${end} of ${state.totalCount}` : 'No records';

  // Per-page dropdown
  const perPageSel = g('perPageSelect');
  if (perPageSel && parseInt(perPageSel.value) !== state.perPage) {
    perPageSel.value = state.perPage;
  }

  let html = `<button class="pagi-btn" onclick="goPage(${state.page - 1})" ${state.page <= 1 ? 'disabled' : ''}>← Prev</button>`;
  for (let p = Math.max(1, state.page - 2); p <= Math.min(totalPages, state.page + 2); p++) {
    html += `<button class="pagi-btn ${p === state.page ? 'active' : ''}" onclick="goPage(${p})">${p}</button>`;
  }
  html += `<button class="pagi-btn" onclick="goPage(${state.page + 1})" ${state.page >= totalPages ? 'disabled' : ''}>Next →</button>`;
  btns.innerHTML = html;
}
  window.goPage = function (p) {
    const totalPages = Math.ceil(state.totalCount / state.perPage);
    if (p < 1 || p > totalPages) return;
    state.page = p; applyFilters();
  };
  window.onPerPageChange = function(val) {
  state.perPage = parseInt(val);
  state.page = 1;
  applyFilters();
};

  function updateHeaderStats(stats) {
    animateVal(g('statTotal'),     stats.total     || 0);
    animateVal(g('statDivisions'), stats.divisions || 0);
    animateVal(g('statWithEdd'),   stats.with_edd  || 0);
    animateVal(g('statDelivered'), stats.delivered || 0);
  }

  window.onListSearch = function (val) { state.search = val; state.page = 1; applyFilters(); };

  window.setDivisionFilter = function (div, btn) {
    state.divisionFilter = div; state.page = 1;
    qsa('.filter-pill[data-division]').forEach(b => b.classList.remove('active'));
    if (btn) btn.classList.add('active');
    applyFilters();
  };

  /* Open the full-detail page for a single record */
  window.viewLpRecord = function (id) {
    const n = parseInt(id, 10);
    if (!n || isNaN(n)) { toast('Invalid record id', 'error'); return; }
    // Prefer the configured detail URL; fall back to an absolute path so a
    // stale/missing config can never build a bad relative URL.
    const base = API.detail || '/logistics/logistics-performance/';
    window.location.href = base + n + '/';
  };

  /* ═══ DELETE MODAL ═══════════════════════════════ */
  window.openDelete = function (id) {
    const rec = state.records.find(r => r.id === id);
    if (!rec) return;
    state.deleteId = id;
    const rowsEl = g('deleteModalRows');
    if (rowsEl) {
      rowsEl.innerHTML = [
        ['Division',    rec.division    || '—'],
        ['Location',    rec.location    || '—'],
        ['Billing Doc', rec.billing_doc || '—'],
        ['Shipto',      rec.shipto_name || '—'],
        ['Docket No',   rec.docket_no   || '—'],
      ].map(([l, v]) => `<div class="lmodal-record-row">
        <span class="lmodal-record-lbl">${l}</span>
        <span class="lmodal-record-val">${escHtml(String(v))}</span></div>`).join('');
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
    fetch(`${API.delete}${state.deleteId}/`, {
      method: 'DELETE', credentials: 'same-origin',
      headers: { 'X-CSRFToken': CSRF },
    })
      .then(r => { if (!r.ok) throw new Error('Delete failed'); toast('Record deleted', 'success'); closeDeleteModal(); loadList(); })
      .catch(e => toast('Could not delete: ' + e.message, 'error'))
      .finally(() => { btn.innerHTML = orig; btn.disabled = false; });
  };

  /* ═══ TAB 2 — BULK UPLOAD ════════════════════════ */
  function initDropZone() {
    const dz = g('dropZone'); if (!dz) return;
    dz.addEventListener('dragover',  e => { e.preventDefault(); dz.classList.add('dragover'); });
    dz.addEventListener('dragleave', () => dz.classList.remove('dragover'));
    dz.addEventListener('drop', e => {
      e.preventDefault(); dz.classList.remove('dragover');
      const f = e.dataTransfer.files[0]; if (f) handleFile(f);
    });
    dz.addEventListener('click', () => { const fi = g('fileInput'); if (fi) fi.click(); });
  }

  window.onFileInput = function (input) { if (input.files[0]) handleFile(input.files[0]); };

  function handleFile(file) {
    const ext = '.' + file.name.split('.').pop().toLowerCase();
    if (!['.csv', '.xlsx', '.xls'].includes(ext)) { toast('Invalid file type: ' + ext, 'error'); return; }
    const ne = g('fileSelectedName'), se = g('fileSelectedSize'), re = g('fileSelectedRow'), de = g('dropZone');
    if (ne) ne.textContent = file.name;
    if (se) se.textContent = (file.size / 1024).toFixed(1) + ' KB';
    if (re) re.style.display = 'flex';
    if (de) de.style.display = 'none';
    const ref = g('uploadFileRef'); if (ref) ref._file = file;
    // Auto-preview: trigger upload immediately after file selection
    const autoBtn = { innerHTML: '', disabled: false };
    setTimeout(function() { submitUpload(autoBtn); }, 300);
  }

  window.clearFile = function () {
    const re = g('fileSelectedRow'), de = g('dropZone'), fi = g('fileInput'), ref = g('uploadFileRef');
    if (re) re.style.display = 'none';
    if (de) de.style.display = 'block';
    if (fi) fi.value = '';
    if (ref) ref._file = null;
  };

  window.submitUpload = function (btn) {
    const fi  = g('fileInput'), ref = g('uploadFileRef');
    const file = (fi && fi.files[0]) || (ref && ref._file) || null;
    if (!file) { toast('Please select a file first', 'warning'); return; }

    const fd = new FormData(); fd.append('file', file);
    const orig = btn.innerHTML;
    btn.innerHTML = '<span class="loading-spinner"></span> Uploading…'; btn.disabled = true;

    fetch(API.upload, {
      method: 'POST', credentials: 'same-origin',
      headers: { 'X-CSRFToken': CSRF },
      body: fd,
    })
      .then(r => r.json().then(d => ({ ok: r.ok, data: d })))
      .then(({ ok, data }) => {
        if (!ok) throw new Error(data.message || data.detail || 'Upload failed');
        state.uploadPreview = data;
        renderPreview(data);
        switchLpuTab('tab-preview');
        toast('File uploaded — review before importing', 'info');
      })
      .catch(e => toast('Upload error: ' + e.message, 'error'))
      .finally(() => { btn.innerHTML = orig; btn.disabled = false; });
  };

  window.downloadSample = function () {
    const headers = [
      'Division', 'Location', 'Plant-Name', 'Billing Doc', 'Billing Date',
      'Inv Value', 'Doc. Type', 'Billed Qty', 'Shipto-Party', 'Shipto-Name',
      'Shipto-City', 'Pincode', 'Currier No', 'Currier Partner Code', 'Docket No',
      'Mode(Surface/Air/Train)', 'Boxes', 'CH Weight', 'Dispatch Date', 'Delivery Date',
      'Basic Logistics Cost', 'Other Charges', 'Remarks/Delay Reason', 'Cost Center',
      'Shipto-Region', 'Zone'
    ];
    const sample = [
      'MLSIPL,vapi,3051,3626800001,02-04-2026,2642852,ZKB,6,2001716,SVS PHARMA (A Unit Of SVS Facility,Hyderabad,502319,,30006,By Hand,Surface,,,02-04-2026,02-04-2026,,,Logistic Issue,30512010,36-Telengana,South',
      'MLSIPL,vapi,3051,3626800002,02-04-2026,2766000,ZKB,17,2000280,Matrix Biomedics Pvt. Ltd - C,Pune,411011,24MEC,30006,65245,Air,,,02-04-2026,03-04-2026,,,Logistic Issue,30512010,27-Maharashtra,South'
    ];
    const csv = [headers.join(','), ...sample].join('\n');
    const a = document.createElement('a');
    a.href = 'data:text/csv,' + encodeURIComponent(csv);
    a.download = 'logistics_performance_sample.csv';
    a.click();
    toast('Sample CSV downloaded', 'success');
  };

  /* ═══ TAB 3 — IMPORT PREVIEW ════════════════════ */
  let previewTable = null;

  const PREV_STATUS = {
    ok:        ['ok',  'fa-check', 'Ready'],
    duplicate: ['dup', 'fa-clone', 'Duplicate'],
    error:     ['err', 'fa-times', 'Error'],
  };
  function previewStatusBadge(row) {
    const info = PREV_STATUS[row.status] || ['err', 'fa-times', 'Error'];
    return `<span class="prev-status ${info[0]}"><i class="fas ${info[1]}"></i> ${info[2]}</span>`;
  }
  function calcDays(v) {
    return (v !== null && v !== undefined && v !== '')
      ? `<span class="calc-cell">${escHtml(String(v))}d</span>`
      : '<span class="calc-cell null">—</span>';
  }

  function renderPreview(data) {
    const rows = data.rows || [];
    const ok   = rows.filter(r => r.status === 'ok').length;
    const dup  = rows.filter(r => r.status === 'duplicate').length;
    const err  = rows.filter(r => r.status === 'error').length;
    const hasEdd = rows.filter(r => r.status === 'ok' && r.data && r.data.expected_delivery_date).length;

    animateVal(g('prevTotal'),  rows.length);
    animateVal(g('prevOk'),     ok);
    animateVal(g('prevWarn'),   dup + err);
    animateVal(g('prevCalc'),   hasEdd);

    if (previewTable) {
      previewTable.setData(rows);
    } else {
      previewTable = LogiTable.createDataTable({
        mount:    'previewBody',
        data:     rows,
        pageSize: 15,
        perPage:  [15, 25, 50, 100],
        search:   true,
        rowClass: r => (PREV_STATUS[r.status] || ['err'])[0],
        filters: [
          { key: 'all', label: 'All',       test: () => true },
          { key: 'ok',  label: 'Ready',     test: r => r.status === 'ok' },
          { key: 'dup', label: 'Duplicate', test: r => r.status === 'duplicate' },
          { key: 'err', label: 'Error',     test: r => r.status === 'error' },
        ],
        columns: [
          { key: 'row', label: '#', width: '48px', sortable: true,
            value: r => r.row, className: 'prev-row-num' },
          { key: 'division', label: 'Division', width: '90px', sortable: true, searchable: true,
            value:  r => (r.data || {}).division || '',
            render: r => `<span class="badge ${divisionBadgeClass((r.data || {}).division)}">${escHtml((r.data || {}).division || '—')}</span>` },
          { key: 'location', label: 'Location', width: '110px', sortable: true, searchable: true,
            value:  r => (r.data || {}).location || '',
            render: r => escHtml((r.data || {}).location || '—') },
          { key: 'dispatch_date', label: 'Dispatch', width: '100px', sortable: true,
            value:  r => (r.data || {}).dispatch_date || '',
            render: r => formatDate((r.data || {}).dispatch_date) },
          { key: 'docket_no', label: 'Docket No', width: '110px', sortable: true, searchable: true,
            value:  r => (r.data || {}).docket_no || '',
            render: r => escHtml((r.data || {}).docket_no || '—') },
          { key: 'courier_name', label: 'Courier', width: '110px', sortable: true, searchable: true,
            value:  r => (r.data || {}).courier_name || '',
            render: r => escHtml((r.data || {}).courier_name || '—') },
          { key: 'mode', label: 'Mode', width: '90px', sortable: true,
            value:  r => (r.data || {}).mode || '',
            render: r => modeBadge((r.data || {}).mode) },
          { key: 'shipto_city', label: 'Shipto City', width: '110px', sortable: true, searchable: true,
            value:  r => (r.data || {}).shipto_city || '',
            render: r => escHtml((r.data || {}).shipto_city || '—') },
          { key: 'pincode', label: 'Pincode', width: '90px', sortable: true, searchable: true,
            value:  r => (r.data || {}).pincode || '',
            render: r => `<span class="tbl-id">${escHtml(String((r.data || {}).pincode || '—'))}</span>` },
          { key: 'tat', label: 'TAT', width: '70px', sortable: true,
            value:  r => (r.data || {}).tat_from_master,
            render: r => { const v = (r.data || {}).tat_from_master; return (v !== null && v !== undefined && v !== '') ? `<span class="calc-cell">${escHtml(String(v))}d</span>` : '<span class="calc-cell null">No TAT</span>'; } },
          { key: 'edd', label: 'EDD', width: '100px', sortable: true,
            value:  r => (r.data || {}).expected_delivery_date || '',
            render: r => (r.data || {}).expected_delivery_date ? `<span class="calc-cell">${formatDate((r.data || {}).expected_delivery_date)}</span>` : '<span class="calc-cell null">—</span>' },
          { key: 'dve', label: 'Disp vs EDD', width: '90px', sortable: true,
            value:  r => (r.data || {}).dispatch_vs_edd_tat,
            render: r => calcDays((r.data || {}).dispatch_vs_edd_tat) },
          { key: 'dvd', label: 'Disp vs Del', width: '90px', sortable: true,
            value:  r => (r.data || {}).dispatch_vs_delivery_tat,
            render: r => calcDays((r.data || {}).dispatch_vs_delivery_tat) },
          { key: 'actual', label: 'Actual Cost', width: '100px', sortable: true,
            value:  r => (r.data || {}).actual_logistics_cost,
            render: r => { const v = (r.data || {}).actual_logistics_cost; return (v !== null && v !== undefined && v !== '') ? `<span class="calc-cell">₹${Number(v).toLocaleString('en-IN')}</span>` : '<span class="calc-cell null">—</span>'; } },
          { key: 'creator', label: 'Creator', width: '100px', sortable: true, searchable: true,
            value:  r => (r.data || {}).creator || '',
            render: r => escHtml((r.data || {}).creator || '—') },
          { key: 'status', label: 'Status', width: '100px', sortable: true,
            value: r => r.status, render: previewStatusBadge },
          { key: 'reason', label: 'Reason', width: '1.6fr', sortable: true, searchable: true,
            value:  r => r.message || '',
            render: r => r.message
              ? `<span style="font-size:12px;color:${r.status === 'error' ? 'var(--red)' : 'var(--text-muted)'}">${escHtml(r.message)}</span>`
              : '<span class="tbl-sub">—</span>' },
        ],
      });
    }

    const ci = g('confirmInfo');
    if (ci) {
      ci.innerHTML = `<strong>${ok}</strong> row${ok !== 1 ? 's' : ''} will be imported · <strong>${dup + err}</strong> will be skipped · <strong>${hasEdd}</strong> with calculated EDD`;
    }
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
      .then(r => r.json().then(d => ({ ok: r.ok, data: d })))
      .then(({ ok, data }) => {
        if (!ok) throw new Error(data.message || data.detail || 'Import failed');
        toast((data.imported || 0) + ' records imported!', 'success');
        state.uploadPreview = null;
        clearFile();
        showTab('tab-list');
        loadList();
      })
      .catch(e => toast('Import error: ' + e.message, 'error'))
      .finally(() => { btn.innerHTML = orig; btn.disabled = false; });
  };

  window.cancelImport = function () {
    state.uploadPreview = null;
    clearFile();
    switchLpuTab('tab-upload');
    toast('Import cancelled');
  };

  /* ═══ EDIT MODAL ════════════════════════════════════ */
const EDIT_FIELDS = [
  'division','location','plant_name','billing_doc','billing_date',
  'inv_value','doc_type','billed_qty','shipto_party','shipto_name','shipto_city','shipto_region',
  'pincode','courier_no','courier_code','courier_name','docket_no','mode','boxes','ch_weight','dispatch_date',
  'delivery_date','basic_logistics_cost','other_charges','remarks','cost_center',
  'zone','creator',
];

window.openEdit = function(id) {
  var titleEl    = document.getElementById('editModalTitle');
  var subtitleEl = document.getElementById('editModalSubtitle');

  if (id === null || id === undefined) {
    // ── Create mode ──────────────────────────────────────
    state.editingId = null;
    EDIT_FIELDS.forEach(function(f) {
      var el = g('editField_' + f);
      if (el) el.value = '';
    });
    if (titleEl)    titleEl.textContent    = 'New Record';
    if (subtitleEl) subtitleEl.textContent = 'Add new logistics performance entry';

  } else {
    // ── Edit mode ─────────────────────────────────────────
    var rec = state.allRecords.find(function(r) { return r.id === id; });
    if (!rec) { toast('Record not found', 'error'); return; }
    state.editingId = id;

    EDIT_FIELDS.forEach(function(f) {
      var el = g('editField_' + f);
      if (!el) return;
      if ((f === 'billing_date' || f === 'dispatch_date' || f === 'delivery_date') && rec[f]) {
        el.value = String(rec[f]).substring(0, 10);
      } else {
        el.value = rec[f] !== null && rec[f] !== undefined ? rec[f] : '';
      }
    });

    if (titleEl)    titleEl.textContent    = 'Edit Record';
    if (subtitleEl) subtitleEl.textContent = 'Editing: ' + escHtml(rec.docket_no || ('#' + id));
  }

  var ov = g('editModalOverlay');
  if (ov) ov.classList.add('open');
};

window.closeEditModal = function() {
  const ov = g('editModalOverlay');
  if (ov) ov.classList.remove('open');
  state.editingId = null;
};

window.submitEdit = function(btn) {
  var data = {};
  EDIT_FIELDS.forEach(function(f) {
    var el = g('editField_' + f);
    if (el) data[f] = el.value.trim() || null;
  });

  var isCreate = (state.editingId === null);
  var url    = isCreate ? API.create : (API.update + state.editingId + '/');
  var method = isCreate ? 'POST' : 'PUT';
  var orig   = btn.innerHTML;
  btn.innerHTML = '<span class="loading-spinner"></span> Saving…';
  btn.disabled  = true;

  fetch(url, {
    method: method,
    credentials: 'same-origin',
    headers: { 'Content-Type': 'application/json', 'X-CSRFToken': CSRF },
    body: JSON.stringify(data),
  })
    .then(function(r) { return r.json().then(function(d) { return { ok: r.ok, data: d }; }); })
    .then(function(res) {
      if (!res.ok) throw new Error(res.data.message || res.data.detail || (isCreate ? 'Create failed' : 'Update failed'));
      toast(isCreate ? 'Record created!' : 'Record updated!', 'success');
      closeEditModal();
      loadList();
    })
    .catch(function(e) { toast((isCreate ? 'Create' : 'Update') + ' error: ' + e.message, 'error'); })
    .finally(function() { btn.innerHTML = orig; btn.disabled = false; });
};
/* ═══ TAB EDIT — ADD / EDIT ══════════════════════════ */
const LPU_EDIT_FIELDS = [
  'division','location','plant_name','billing_doc','billing_date',
  'inv_value','doc_type','shipto_name','shipto_city','shipto_region',
  'pincode','mode','docket_no','courier_name','dispatch_date',
  'delivery_date','basic_logistics_cost','remarks','cost_center',
  'zone','creator',
];

function resetLpuForm() {
  state.editingId = null;
  LPU_EDIT_FIELDS.forEach(f => {
    const el = g('lpuField_' + f);
    if (el) el.value = '';
  });
  if (g('lpuFormTitle'))     g('lpuFormTitle').textContent     = 'Edit Record';
  if (g('lpuFormSubtitle'))  g('lpuFormSubtitle').textContent  = 'Update logistics performance entry';
  if (g('lpuSubmitBtnText')) g('lpuSubmitBtnText').textContent = 'Save Changes';
}

// openLpuEdit now uses the modal instead of the tab-edit page
window.openLpuEdit = function(id) {
  openEdit(id);
};

window.cancelLpuEdit = function() {
  resetLpuForm();
  showTab('tab-list');
};

window.submitLpuEdit = function(btn) {
  if (!state.editingId) return;

  const data = {};
  LPU_EDIT_FIELDS.forEach(f => {
    const el = g('lpuField_' + f);
    if (el) data[f] = el.value.trim() || null;
  });

  const orig = btn.innerHTML;
  btn.innerHTML = '<span class="loading-spinner"></span> Saving…';
  btn.disabled = true;

  fetch(`${API.update}${state.editingId}/`, {
    method: 'PUT',
    credentials: 'same-origin',
    headers: { 'Content-Type': 'application/json', 'X-CSRFToken': CSRF },
    body: JSON.stringify(data),
  })
    .then(r => r.json().then(d => ({ ok: r.ok, data: d })))
    .then(({ ok, data }) => {
      if (!ok) throw new Error(data.message || data.detail || 'Update failed');
      toast('Record updated!', 'success');
      resetLpuForm();
      showTab('tab-list');
      loadList();
    })
    .catch(e => toast('Update error: ' + e.message, 'error'))
    .finally(() => { btn.innerHTML = orig; btn.disabled = false; });
};
  /* ═══ PENDING TAB ═══════════════════════════════ */
  const API_PENDING = {
    list:          LPU_CONFIG.urls.pending,
    export:        LPU_CONFIG.urls.pendingExport,
    updateUpload:  LPU_CONFIG.urls.pendingUpdateUpload,
    updateConfirm: LPU_CONFIG.urls.pendingUpdateConfirm,
  };

  let pendingState = {
    allRecords:   [],
    records:      [],
    page:         1,
    perPage:      25,
    totalCount:   0,
    updatePreview: null,
  };

  function loadPendingList() {
    const body = g('pendingTblBody');
    if (body) body.innerHTML = '<div class="loading-overlay"><span class="loading-spinner"></span> Loading…</div>';

    fetch(API_PENDING.list, { credentials: 'same-origin' })
      .then(r => { if (!r.ok) throw new Error('Server ' + r.status); return r.json(); })
      .then(data => {
        pendingState.allRecords = data.data || [];
        pendingState.page = 1;
        applyPendingFilters();

        // Update badge
        const badge = g('pendingBadgeCount');
        if (badge) {
          if (pendingState.allRecords.length > 0) {
            badge.textContent = pendingState.allRecords.length;
            badge.style.display = 'inline-flex';
          } else {
            badge.style.display = 'none';
          }
        }
      })
      .catch(e => toast('Could not load pending records: ' + e.message, 'error'));
  }

  window.loadPendingList = loadPendingList;

  function applyPendingFilters() {
    const total = pendingState.allRecords.length;
    pendingState.totalCount = total;
    const totalPages = Math.max(1, Math.ceil(total / pendingState.perPage));
    if (pendingState.page > totalPages) pendingState.page = totalPages;
    const start = (pendingState.page - 1) * pendingState.perPage;
    pendingState.records = pendingState.allRecords.slice(start, start + pendingState.perPage);

    renderPendingTable();
    renderPendingPagination();

    const all = pendingState.allRecords;
    const noDispatch = all.filter(r => !r.dispatch_date).length;
    const noDelivery = all.filter(r => !r.delivery_date).length;
    const noCost     = all.filter(r => !r.basic_logistics_cost).length;

    animateVal(g('pendingStatTotal'),     total);
    animateVal(g('pendingStatNoDispatch'), noDispatch);
    animateVal(g('pendingStatNoDelivery'), noDelivery);
    animateVal(g('pendingStatNoCost'),     noCost);
  }

  function renderPendingTable() {
    const body = g('pendingTblBody');
    if (!body) return;

    if (!pendingState.records.length) {
      body.innerHTML = `<div class="empty-state">
        <i class="fas fa-check-circle" style="color:var(--green)"></i>
        <p>No pending records — all records are complete!</p>
      </div>`;
      return;
    }

    body.innerHTML = pendingState.records.map((rec, i) => {
      const missingBadges = (rec.pending_fields || [])
        .map(f => `<span class="badge badge-amber" style="font-size:10px">${escHtml(f)}</span>`)
        .join(' ');

      return `<div class="tbl-row" style="grid-template-columns:60px 90px 120px 100px 110px 110px 110px 130px 120px 1fr;animation-delay:${i * 0.02}s">
        <div class="tbl-id">#${rec.id}</div>
        <div><span class="badge ${divisionBadgeClass(rec.division)}">${escHtml(rec.division || '—')}</span></div>
        <div class="tbl-trunc">${escHtml(rec.location || '—')}</div>
        <div class="tbl-id">${escHtml(rec.billing_doc || '—')}</div>
        <div class="tbl-id">${escHtml(rec.docket_no || '—')}</div>
        <div class="tbl-date ${!rec.dispatch_date ? 'pending-empty' : ''}">${rec.dispatch_date ? formatDate(rec.dispatch_date) : '<span style="color:var(--amber);font-weight:600">—</span>'}</div>
        <div class="tbl-date ${!rec.delivery_date ? 'pending-empty' : ''}">${rec.delivery_date ? formatDate(rec.delivery_date) : '<span style="color:var(--amber);font-weight:600">—</span>'}</div>
        <div class="${!rec.basic_logistics_cost ? 'pending-empty' : ''}">${rec.basic_logistics_cost ? '₹' + Number(rec.basic_logistics_cost).toLocaleString('en-IN') : '<span style="color:var(--amber);font-weight:600">—</span>'}</div>
        <div style="display:flex;flex-wrap:wrap;gap:3px">${missingBadges}</div>
        <div class="tbl-trunc">${escHtml(rec.shipto_city || '—')} ${modeBadge(rec.mode)}</div>
      </div>`;
    }).join('');
  }

  function renderPendingPagination() {
    const totalPages = Math.ceil(pendingState.totalCount / pendingState.perPage);
    const info = g('pendingPagiInfo'), btns = g('pendingPagiBtns');
    if (!info || !btns) return;
    const start = (pendingState.page - 1) * pendingState.perPage + 1;
    const end   = Math.min(pendingState.page * pendingState.perPage, pendingState.totalCount);
    info.textContent = pendingState.totalCount
      ? `Showing ${start}–${end} of ${pendingState.totalCount}`
      : 'No pending records';

    let html = `<button class="pagi-btn" onclick="goPagePending(${pendingState.page - 1})" ${pendingState.page <= 1 ? 'disabled' : ''}>← Prev</button>`;
    for (let p = Math.max(1, pendingState.page - 2); p <= Math.min(totalPages, pendingState.page + 2); p++) {
      html += `<button class="pagi-btn ${p === pendingState.page ? 'active' : ''}" onclick="goPagePending(${p})">${p}</button>`;
    }
    html += `<button class="pagi-btn" onclick="goPagePending(${pendingState.page + 1})" ${pendingState.page >= totalPages ? 'disabled' : ''}>Next →</button>`;
    btns.innerHTML = html;
  }

  window.goPagePending = function(p) {
    const totalPages = Math.ceil(pendingState.totalCount / pendingState.perPage);
    if (p < 1 || p > totalPages) return;
    pendingState.page = p; applyPendingFilters();
  };
  window.onPendingPerPageChange = function(val) {
    pendingState.perPage = parseInt(val);
    pendingState.page = 1;
    applyPendingFilters();
  };

  window.downloadPendingExcel = function() {
    window.location.href = API_PENDING.export;
    toast('Downloading pending records Excel…', 'info');
  };

  /* ── Pending update upload ── */
  function initPendingDropZone() {
    const dz = g('pendingDropZone'); if (!dz) return;
    dz.addEventListener('dragover',  e => { e.preventDefault(); dz.classList.add('dragover'); });
    dz.addEventListener('dragleave', () => dz.classList.remove('dragover'));
    dz.addEventListener('drop', e => {
      e.preventDefault(); dz.classList.remove('dragover');
      const f = e.dataTransfer.files[0]; if (f) handlePendingFile(f);
    });
    dz.addEventListener('click', () => { const fi = g('pendingFileInput'); if (fi) fi.click(); });
  }

  window.onPendingFileInput = function(input) { if (input.files[0]) handlePendingFile(input.files[0]); };

  function handlePendingFile(file) {
    const ext = '.' + file.name.split('.').pop().toLowerCase();
    if (!['.csv', '.xlsx', '.xls'].includes(ext)) { toast('Invalid file type: ' + ext, 'error'); return; }
    const ne = g('pendingFileSelectedName'), se = g('pendingFileSelectedSize');
    const re = g('pendingFileSelectedRow'), de = g('pendingDropZone');
    if (ne) ne.textContent = file.name;
    if (se) se.textContent = (file.size / 1024).toFixed(1) + ' KB';
    if (re) re.style.display = 'flex';
    if (de) de.style.display = 'none';
    const ref = g('pendingDropZone'); if (ref) ref._file = file;
  }

  window.clearPendingFile = function() {
    const re = g('pendingFileSelectedRow'), de = g('pendingDropZone'), fi = g('pendingFileInput');
    if (re) re.style.display = 'none';
    if (de) { de.style.display = 'block'; de._file = null; }
    if (fi) fi.value = '';
    const wrap = g('pendingUpdatePreviewWrap');
    if (wrap) wrap.style.display = 'none';
    pendingState.updatePreview = null;
  };

  window.submitPendingUpdate = function(btn) {
    const fi  = g('pendingFileInput');
    const dz  = g('pendingDropZone');
    const file = (fi && fi.files[0]) || (dz && dz._file) || null;
    if (!file) { toast('Please select a file first', 'warning'); return; }

    const fd = new FormData(); fd.append('file', file);
    const orig = btn.innerHTML;
    btn.innerHTML = '<span class="loading-spinner"></span> Parsing…'; btn.disabled = true;

    fetch(API_PENDING.updateUpload, {
      method: 'POST', credentials: 'same-origin',
      headers: { 'X-CSRFToken': CSRF },
      body: fd,
    })
      .then(r => r.json().then(d => ({ ok: r.ok, data: d })))
      .then(({ ok, data }) => {
        if (!ok) throw new Error(data.message || 'Upload failed');
        pendingState.updatePreview = data;
        renderPendingUpdatePreview(data);
        const wrap = g('pendingUpdatePreviewWrap');
        if (wrap) { wrap.style.display = 'block'; wrap.scrollIntoView({ behavior: 'smooth' }); }
        toast('Preview ready — review and confirm', 'info');
      })
      .catch(e => toast('Upload error: ' + e.message, 'error'))
      .finally(() => { btn.innerHTML = orig; btn.disabled = false; });
  };

  function renderPendingUpdatePreview(data) {
    const rows = data.rows || [];
    const okRows   = rows.filter(r => r.status === 'ok');
    const warnRows = rows.filter(r => r.status !== 'ok');

    animateVal(g('puPrevOk'),   okRows.length);
    animateVal(g('puPrevWarn'), warnRows.length);

    const body = g('pendingUpdatePreviewBody');
    if (!body) return;

    const statusMap = {
      ok:    ['ok',  'fa-check',     'Update'],
      skip:  ['dup', 'fa-minus',     'Skip'],
      error: ['err', 'fa-times',     'Error'],
    };

    body.innerHTML = rows.map((row, i) => {
      const d = row.data || {};
      const [cls, icon, label] = statusMap[row.status] || ['err', 'fa-times', 'Error'];
      const ch = d.changes || {};
      const cur = d.current || {};

      const fmtChange = (field, label) => {
        if (ch[field]) {
          const old = cur[field] || '—';
          const nw  = ch[field];
          return `<span title="${escHtml(old)} → ${escHtml(nw)}" style="color:var(--green);font-weight:600">${escHtml(nw)}</span>`;
        }
        return `<span style="color:var(--text-muted)">${escHtml(cur[field] || '—')}</span>`;
      };

      return `<div class="prev-tbl-row ${cls}" style="grid-template-columns:50px 80px 110px 110px 110px 1fr 1fr 1fr 120px">
        <div class="prev-row-num">${row.row || i + 2}</div>
        <div class="tbl-id">${d.id || '—'}</div>
        <div><span class="badge ${divisionBadgeClass(d.division)}">${escHtml(d.division || '—')}</span></div>
        <div class="tbl-trunc">${escHtml(d.location || '—')}</div>
        <div class="tbl-id">${escHtml(d.docket_no || '—')}</div>
        <div>${fmtChange('dispatch_date', 'Dispatch Date')}</div>
        <div>${fmtChange('delivery_date', 'Delivery Date')}</div>
        <div>${fmtChange('basic_logistics_cost', 'Cost')}</div>
        <div><span class="prev-status ${cls}"><i class="fas ${icon}"></i> ${label}
          ${row.message ? `<span style="font-weight:400;opacity:0.75"> — ${escHtml(row.message)}</span>` : ''}
        </span></div>
      </div>`;
    }).join('');

    const ci = g('pendingUpdateConfirmInfo');
    if (ci) ci.innerHTML = `<strong>${okRows.length}</strong> record${okRows.length !== 1 ? 's' : ''} will be updated · <strong>${warnRows.length}</strong> skipped`;
  }

  window.confirmPendingUpdate = function(btn) {
    if (!pendingState.updatePreview) { toast('No preview data', 'warning'); return; }
    const orig = btn.innerHTML;
    btn.innerHTML = '<span class="loading-spinner"></span> Updating…'; btn.disabled = true;

    fetch(API_PENDING.updateConfirm, {
      method: 'POST', credentials: 'same-origin',
      headers: { 'Content-Type': 'application/json', 'X-CSRFToken': CSRF },
      body: JSON.stringify({ token: pendingState.updatePreview.token }),
    })
      .then(r => r.json().then(d => ({ ok: r.ok, data: d })))
      .then(({ ok, data }) => {
        if (!ok) throw new Error(data.message || 'Update failed');
        toast((data.updated || 0) + ' records updated successfully!', 'success');
        pendingState.updatePreview = null;
        const wrap = g('pendingUpdatePreviewWrap');
        if (wrap) wrap.style.display = 'none';
        clearPendingFile();
        loadPendingList();
      })
      .catch(e => toast('Update error: ' + e.message, 'error'))
      .finally(() => { btn.innerHTML = orig; btn.disabled = false; });
  };

  window.cancelPendingUpdate = function() {
    pendingState.updatePreview = null;
    const wrap = g('pendingUpdatePreviewWrap');
    if (wrap) wrap.style.display = 'none';
    clearPendingFile();
    toast('Update cancelled', 'info');
  };

  /* ─── INIT ────────────────────────────────────── */
  document.addEventListener('DOMContentLoaded', function () {

    // Init tabs
    qsa('.lpu-tab-content').forEach(c => {
      const on = c.id === 'tab-list';
      c.classList.toggle('active', on);
      c.style.display = on ? 'block' : 'none';
    });
    qsa('.lpu-tab-btn').forEach(b => b.classList.toggle('active', b.dataset.tab === 'tab-list'));

    qsa('.fi').forEach(el => { el.style.opacity = '1'; });

    loadList();
    initDropZone();
    initPendingDropZone();

    /* ── Advanced Filter init ── */
    if (typeof AdvFilter !== 'undefined') {
      advFilter = new AdvFilter({
        searchWrapId: 'lpuSearchWrap',
        chipsId:      'lpuAdvChips',
        onApply: function () { state.page = 1; applyFilters(); },
        fields: [
          { key: 'division',    label: 'Division',      type: 'select',
            options: lpDivisions().map(d => ({ v: d.code, l: d.label })) },
          { key: 'location',     label: 'Location',     type: 'text'   },
          { key: 'plant_name',   label: 'Plant',        type: 'text'   },
          { key: 'billing_doc',  label: 'Billing Doc',  type: 'text'   },
          { key: 'billing_date', label: 'Bill Date',    type: 'date'   },
          { key: 'shipto_name',  label: 'Shipto Name',  type: 'text'   },
          { key: 'shipto_city',  label: 'Shipto City',  type: 'text'   },
          { key: 'shipto_region',label: 'Region',       type: 'text'   },
          { key: 'zone',         label: 'Zone',         type: 'select',
            options: ((window.LP_MASTERS && LP_MASTERS.zones) || []).map(function (z) { return { v: z, l: z }; }) },
          { key: 'pincode',      label: 'Pincode',      type: 'text'   },
          { key: 'mode',         label: 'Mode',         type: 'select',
            options: ((window.LP_MASTERS && LP_MASTERS.modes) || []).map(function (m) { return { v: m, l: m }; }) },
          { key: 'courier_name', label: 'Courier',      type: 'select',
            options: ((window.LP_MASTERS && LP_MASTERS.couriers) || []).map(function (c) { return { v: c.name, l: c.name }; }) },
          { key: 'docket_no',    label: 'Docket No',    type: 'text'   },
          { key: 'dispatch_date',label: 'Dispatch Date',type: 'date'   },
          { key: 'delivery_date',label: 'Delivery Date',type: 'date'   },
          { key: 'basic_logistics_cost', label: 'Logistics Cost', type: 'number' },
          { key: 'remarks',      label: 'Remarks',      type: 'text'   },
          { key: 'creator',      label: 'Creator',      type: 'text'   },
        ],
      });
    }

    const ov = g('deleteModalOverlay');
    if (ov) ov.addEventListener('click', e => { if (e.target === ov) closeDeleteModal(); });
   document.addEventListener('keydown', e => {
  if (e.key === 'Escape') { closeDeleteModal(); closeEditModal(); }
});

const editOv = g('editModalOverlay');
if (editOv) editOv.addEventListener('click', e => { if (e.target === editOv) closeEditModal(); });

    let st;
    const se = g('listSearch');
    if (se) {
      se.addEventListener('input', function() {
        clearTimeout(st);
        st = setTimeout(function() { onListSearch(se.value); }, 350);
      });
      se.addEventListener('keydown', function(e) {
        if (e.key === 'Enter') { clearTimeout(st); triggerListSearch(); }
      });
    }

    // Handle ?open=new URL param (e.g. from dashboard "Add New Record" shortcut)
    var urlParam = new URLSearchParams(window.location.search).get('open');
    if (urlParam === 'new') {
      setTimeout(function() { openEdit(null); }, 400);
    }
  });

})();