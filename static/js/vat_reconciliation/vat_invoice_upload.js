/* ═══════════════════════════════════════════════════════
   vat_invoice_upload.js — Invoice upload + process list
═══════════════════════════════════════════════════════ */
(function () {
  'use strict';

  const API = INV_CONFIG.urls;
  const CSRF = LogiTable.getCookie('csrftoken');
  const escHtml = LogiTable.escHtml;
  const animateVal = LogiTable.animateVal;

  let state = { rows: [], allRows: [], page: 1, perPage: 15, search: '', totalCount: 0, deleteId: null, files: [] };

  function g(id) { return document.getElementById(id); }
  function qsa(s, c) { return [...(c || document).querySelectorAll(s)]; }

  function toast(msg, type) {
    type = type || 'success';
    const el = g('lmToast'); if (!el) return;
    el.className = 'lm-toast ' + type;
    el.querySelector('.toast-msg').textContent = msg;
    el.querySelector('i').className =
      type === 'success' ? 'fas fa-check-circle' : type === 'error' ? 'fas fa-times-circle'
      : type === 'warning' ? 'fas fa-exclamation-triangle' : 'fas fa-info-circle';
    el.classList.add('show'); clearTimeout(el._t);
    el._t = setTimeout(function () { el.classList.remove('show'); }, 3600);
  }

  function showTab(tabId) {
    qsa('.lm-tab-btn').forEach(function (b) { b.classList.toggle('active', b.dataset.tab === tabId); });
    qsa('.lm-tab-content').forEach(function (c) {
      const on = c.id === tabId; c.classList.toggle('active', on); c.style.display = on ? 'block' : 'none';
    });
  }
  window.switchLmTab = function (tabId) { showTab(tabId); if (tabId === 'tab-list') loadList(); };

  /* ── LIST ── */
  function applyFilters() {
    const q = state.search.toLowerCase().trim();
    let filtered = state.allRows;
    if (q) {
      filtered = filtered.filter(function (r) {
        return ['invoice_number', 'country', 'recon_status'].some(function (f) { return (r[f] || '').toString().toLowerCase().includes(q); });
      });
    }
    state.totalCount = filtered.length;
    const totalPages = Math.max(1, Math.ceil(state.totalCount / state.perPage));
    if (state.page > totalPages) state.page = totalPages;
    const start = (state.page - 1) * state.perPage;
    state.rows = filtered.slice(start, start + state.perPage);
    renderTable();
    LogiTable.renderPagination(state);

    animateVal(g('statTotal'), state.allRows.length);
    animateVal(g('statMatched'), state.allRows.filter(function (r) { return r.recon_status === 'MATCHED'; }).length);
    animateVal(g('statDiscrepancy'), state.allRows.filter(function (r) { return r.recon_status === 'DISCREPANCY'; }).length);

    const badge = g('recordCountBadge');
    if (badge) badge.textContent = state.totalCount.toLocaleString('en-IN') + ' invoice' + (state.totalCount !== 1 ? 's' : '');
  }

  function loadList() {
    const wrap = g('listLoading'); if (wrap) wrap.style.display = 'block';
    fetch(API.list + '?per_page=10000', { credentials: 'same-origin' })
      .then(function (r) { if (!r.ok) throw new Error('Server ' + r.status); return r.json(); })
      .then(function (data) { state.allRows = data.data || []; state.page = 1; applyFilters(); })
      .catch(function (e) { toast('Could not load invoices: ' + e.message, 'error'); })
      .finally(function () { if (wrap) wrap.style.display = 'none'; });
  }

  function statusBadge(s) {
    if (s === 'MATCHED') return '<span class="badge badge-green">Matched</span>';
    if (s === 'DISCREPANCY') return '<span class="badge badge-red">Discrepancy</span>';
    return '<span class="badge badge-amber">Pending</span>';
  }
  function fmt(v) { return (v === null || v === undefined || v === '') ? '—' : v; }

  function renderTable() {
    const body = g('tblBody'); if (!body) return;
    if (!state.rows.length) {
      body.innerHTML = '<div class="empty-state"><i class="fas fa-file-invoice"></i>' +
        '<p>No invoices yet. <a href="javascript:void(0)" onclick="switchLmTab(\'tab-upload\')" style="color:var(--blue)">Upload one</a>.</p></div>';
      return;
    }
    body.innerHTML = state.rows.map(function (r, i) {
      const processed = r.stage === 'RECONCILED';
      return '<div class="tbl-row" style="animation-delay:' + (i * 0.03) + 's">' +
        '<div class="tbl-id">#' + r.id + '</div>' +
        '<div><div class="tbl-name">' + escHtml(r.invoice_number || '—') + '</div></div>' +
        '<div>' + escHtml(fmt(r.country)) + '</div>' +
        '<div>' + escHtml(fmt(r.taxable_amount)) + '</div>' +
        '<div>' + escHtml(fmt(r.expected_vat)) + '</div>' +
        '<div>' + escHtml(fmt(r.charged_vat)) + '</div>' +
        '<div>' + escHtml(fmt(r.variance)) + '</div>' +
        '<div>' + statusBadge(r.recon_status) + '</div>' +
        '<div class="tbl-actions">' +
          (processed
            ? '<a class="tbl-btn edit" href="' + API.detailPage + r.id + '/"><i class="fas fa-eye"></i> View</a>'
            : '<button class="tbl-btn proc" onclick="processInvoice(' + r.id + ', this)"><i class="fas fa-cogs"></i> Process</button>') +
          '<button class="tbl-btn del" onclick="openDelete(' + r.id + ')"><i class="fas fa-trash"></i></button>' +
        '</div></div>';
    }).join('');
  }

  window.goPage = function (p) {
    const totalPages = Math.ceil(state.totalCount / state.perPage);
    if (p < 1 || p > totalPages) return; state.page = p; applyFilters();
  };
  window.onListSearch = function (val) { state.search = val; state.page = 1; applyFilters(); };

  /* ── PROCESS ── */
  window.processInvoice = function (id, btn) {
    const orig = btn ? btn.innerHTML : '';
    if (btn) { btn.innerHTML = '<span class="loading-spinner"></span> Processing…'; btn.disabled = true; }
    fetch(API.process + id + '/', { method: 'POST', credentials: 'same-origin', headers: { 'X-CSRFToken': CSRF } })
      .then(function (r) { return r.json().then(function (d) { return { ok: r.ok, data: d }; }); })
      .then(function (res) {
        if (!res.ok) throw new Error(res.data.message || 'Processing failed');
        const st = (res.data.data || {}).recon_status;
        toast('Invoice processed — ' + (st || 'done'), st === 'DISCREPANCY' ? 'warning' : 'success');
        loadList();
      })
      .catch(function (e) { toast(e.message, 'error'); if (btn) { btn.innerHTML = orig; btn.disabled = false; } });
  };

  /* ── DELETE ── */
  window.openDelete = function (id) {
    const r = state.allRows.find(function (x) { return x.id === id; });
    if (!r) return; state.deleteId = id;
    const rowsEl = g('deleteModalRows');
    if (rowsEl) {
      rowsEl.innerHTML = [['Invoice No', r.invoice_number || '—'], ['Country', r.country || '—'], ['Status', r.recon_status || '—']]
        .map(function (p) { return '<div class="lmodal-record-row"><span class="lmodal-record-lbl">' + p[0] + '</span><span class="lmodal-record-val">' + escHtml(String(p[1])) + '</span></div>'; }).join('');
    }
    g('deleteModalOverlay').classList.add('open');
  };
  window.closeDeleteModal = function () { g('deleteModalOverlay').classList.remove('open'); state.deleteId = null; };
  window.confirmDelete = function (btn) {
    if (!state.deleteId) return;
    const orig = btn.innerHTML; btn.innerHTML = '<span class="loading-spinner"></span> Deleting…'; btn.disabled = true;
    fetch(API.del + state.deleteId + '/', { method: 'DELETE', credentials: 'same-origin', headers: { 'X-CSRFToken': CSRF } })
      .then(function (r) { if (!r.ok) throw new Error('Delete failed'); toast('Invoice deleted', 'success'); closeDeleteModal(); loadList(); })
      .catch(function (e) { toast(e.message, 'error'); })
      .finally(function () { btn.innerHTML = orig; btn.disabled = false; });
  };

  /* ── FILE SELECTION ── */
  window.onFileInput = function (input) { addFiles(input.files); };

  function addFiles(fileList) {
    [...fileList].forEach(function (f) {
      if (!/\.(pdf|jpe?g|png)$/i.test(f.name)) { toast('Unsupported: ' + f.name, 'error'); return; }
      state.files.push(f);
    });
    renderFileList();
  }

  function renderFileList() {
    const wrap = g('fileListWrap');
    if (!state.files.length) { wrap.style.display = 'none'; wrap.innerHTML = ''; return; }
    wrap.style.display = 'block';
    wrap.innerHTML = state.files.map(function (f, i) {
      return '<div class="file-selected" style="margin-bottom:6px;">' +
        '<i class="fas fa-file-alt"></i>' +
        '<div><div class="file-selected-name">' + escHtml(f.name) + '</div>' +
        '<div class="file-selected-size">' + (f.size / 1024).toFixed(1) + ' KB</div></div>' +
        '<button class="file-selected-clear" onclick="removeFile(' + i + ')" title="Remove">×</button></div>';
    }).join('');
  }
  window.removeFile = function (i) { state.files.splice(i, 1); renderFileList(); };

  function buildFormData() {
    // No header fields — invoice number, date and language are auto-detected
    // by the processing pipeline. Just send the file(s).
    const fd = new FormData();
    state.files.forEach(function (f) { fd.append('files', f); });
    return fd;
  }

  function validate() {
    if (!state.files.length) { toast('Please select at least one file', 'warning'); return false; }
    return true;
  }

  function doUpload() {
    return fetch(API.upload, { method: 'POST', credentials: 'same-origin', headers: { 'X-CSRFToken': CSRF }, body: buildFormData() })
      .then(function (r) { return r.json().then(function (d) { return { ok: r.ok, data: d }; }); })
      .then(function (res) {
        if (!res.ok) {
          if (res.data.errors) {
            Object.entries(res.data.errors).forEach(function (e) {
              const el = g(e[0]); const err = document.querySelector('[data-err="' + e[0] + '"]');
              if (el) el.classList.add('error'); if (err) err.textContent = Array.isArray(e[1]) ? e[1][0] : e[1];
            });
          }
          throw new Error(res.data.message || 'Upload failed');
        }
        return res.data.id;
      });
  }

  function resetForm() {
    g('invForm').reset(); state.files = []; renderFileList();
  }

  window.submitUpload = function (btn) {
    if (!validate()) return;
    const orig = btn.innerHTML; btn.innerHTML = '<span class="loading-spinner"></span> Uploading…'; btn.disabled = true;
    doUpload()
      .then(function () { toast('Invoice uploaded — process it from the list', 'success'); resetForm(); switchLmTab('tab-list'); })
      .catch(function (e) { toast(e.message, 'error'); })
      .finally(function () { btn.innerHTML = orig; btn.disabled = false; });
  };

  window.uploadAndProcess = function (btn) {
    if (!validate()) return;
    const orig = btn.innerHTML; btn.innerHTML = '<span class="loading-spinner"></span> Uploading…'; btn.disabled = true;
    let newId = null;
    doUpload()
      .then(function (id) {
        newId = id;
        btn.innerHTML = '<span class="loading-spinner"></span> Processing…';
        return fetch(API.process + id + '/', { method: 'POST', credentials: 'same-origin', headers: { 'X-CSRFToken': CSRF } })
          .then(function (r) { return r.json().then(function (d) { return { ok: r.ok, data: d }; }); });
      })
      .then(function (res) {
        if (!res.ok) throw new Error(res.data.message || 'Processing failed');
        resetForm();
        toast('Processed — opening result…', 'success');
        window.location.href = API.detailPage + newId + '/';
      })
      .catch(function (e) {
        toast(e.message, 'error');
        resetForm(); switchLmTab('tab-list');
      })
      .finally(function () { btn.innerHTML = orig; btn.disabled = false; });
  };

  document.addEventListener('DOMContentLoaded', function () {
    qsa('.lm-tab-content').forEach(function (c) {
      const on = c.id === 'tab-list'; c.classList.toggle('active', on); c.style.display = on ? 'block' : 'none';
    });
    qsa('.fi').forEach(function (el) { el.style.opacity = '1'; });
    loadList();

    // drag & drop
    const dz = g('dropZone');
    if (dz) {
      dz.addEventListener('click', function () { g('fileInput').click(); });
      ['dragover', 'dragenter'].forEach(function (ev) { dz.addEventListener(ev, function (e) { e.preventDefault(); dz.classList.add('dragover'); }); });
      ['dragleave', 'drop'].forEach(function (ev) { dz.addEventListener(ev, function (e) { e.preventDefault(); dz.classList.remove('dragover'); }); });
      dz.addEventListener('drop', function (e) { if (e.dataTransfer.files.length) addFiles(e.dataTransfer.files); });
    }

    const ov = g('deleteModalOverlay');
    if (ov) ov.addEventListener('click', function (e) { if (e.target === ov) closeDeleteModal(); });
    document.addEventListener('keydown', function (e) { if (e.key === 'Escape') closeDeleteModal(); });

    let st; const se = g('listSearch');
    if (se) se.addEventListener('input', function () { clearTimeout(st); st = setTimeout(function () { onListSearch(se.value); }, 350); });
  });
})();
