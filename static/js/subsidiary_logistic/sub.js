/* ── Subsidiary Logistics Cost — sub.js ─────────────────────────────── */
(function () {
  'use strict';

  /* ── Tab switching ── */
  window.switchSubTab = function (tabId) {
    document.querySelectorAll('.lm-tab-btn').forEach(function (btn) {
      btn.classList.toggle('active', btn.dataset.tab === tabId);
    });
    document.querySelectorAll('.lm-tab-content').forEach(function (panel) {
      panel.classList.toggle('active', panel.id === tabId);
    });
  };

  /* ── Drag-drop ── */
  var dropZone = document.getElementById('dropZone');
  if (dropZone) {
    dropZone.addEventListener('dragover', function (e) {
      e.preventDefault(); dropZone.classList.add('drag-over');
    });
    dropZone.addEventListener('dragleave', function () {
      dropZone.classList.remove('drag-over');
    });
    dropZone.addEventListener('drop', function (e) {
      e.preventDefault(); dropZone.classList.remove('drag-over');
      var files = e.dataTransfer.files;
      if (!files.length) return;
      var input = document.getElementById('excelFile');
      try { input.files = files; } catch (_) {}
      onFileSelected(input);
    });
  }

  /* ── File selected ── */
  window.onFileSelected = function (input) {
    var previewBtn   = document.getElementById('previewBtn');
    var fileInfo     = document.getElementById('fileInfo');
    var fileNameLbl  = document.getElementById('fileNameLabel');
    var uploadError  = document.getElementById('uploadError');

    uploadError.style.display = 'none';

    if (!input.files || !input.files[0]) return;
    var file = input.files[0];

    if (!file.name.toLowerCase().endsWith('.xlsx')) {
      showUploadError('Only .xlsx files are accepted.');
      return;
    }

    document.getElementById('dropLabel').textContent = file.name;
    fileNameLbl.textContent = file.name;
    fileInfo.style.display  = 'block';
    previewBtn.disabled     = false;
  };

  /* ── Preview (AJAX to /sub/upload/preview/) ── */
  window.doPreview = function () {
    var input = document.getElementById('excelFile');
    if (!input.files || !input.files[0]) { showUploadError('Please select a file first.'); return; }

    var loading = document.getElementById('loadingIndicator');
    var previewBtn = document.getElementById('previewBtn');
    loading.style.display  = 'flex';
    previewBtn.disabled    = true;

    var fd = new FormData();
    fd.append('excel_file', input.files[0]);
    fd.append('csrfmiddlewaretoken', CSRF_TOKEN);

    fetch(PREVIEW_URL, { method: 'POST', body: fd })
      .then(function (r) { return r.json(); })
      .then(function (data) {
        loading.style.display = 'none';
        previewBtn.disabled   = false;
        if (data.error) { showUploadError(data.error); return; }
        renderPreview(data);
      })
      .catch(function (err) {
        loading.style.display = 'none';
        previewBtn.disabled   = false;
        showUploadError('Network error — please try again.');
      });
  };

  /* ── Render preview table ── */
  function renderPreview(data) {
    var rows     = data.rows   || [];
    var valid    = data.valid  || 0;
    var total    = data.total  || 0;
    var errCount = data.error_count || 0;

    /* Update header stats */
    document.getElementById('statTotal').textContent  = total;
    document.getElementById('statValid').textContent  = valid;
    document.getElementById('statErrors').textContent = errCount;
    document.getElementById('previewStats').style.display = 'flex';

    /* Store rows for save */
    document.getElementById('rowsJsonInput').value = JSON.stringify(rows);

    /* Build HTML */
    var notice = errCount === 0
      ? '<div class="sub-preview-notice"><i class="fas fa-circle-check"></i> All ' + valid + ' rows are valid. Click <strong>Save All</strong> to commit to the database.</div>'
      : '<div class="sub-preview-error-notice"><i class="fas fa-triangle-exclamation"></i> ' + errCount + ' row(s) have errors and will be skipped. ' + valid + ' valid row(s) will be saved.</div>';

    var tRows = rows.map(function (r, i) {
      var statusBadge = r.valid
        ? '<span class="sub-badge-valid"><i class="fas fa-check"></i> Valid</span>'
        : '<span class="sub-badge-error"><i class="fas fa-xmark"></i> ' + (r.errors || []).join('; ') + '</span>';
      var rowClass = r.valid ? '' : 'style="background:var(--red-lt);"';
      return '<tr ' + rowClass + '>' +
        '<td style="color:var(--text-muted);font-family:var(--font-mono);font-size:12px;">' + (i + 1) + '</td>' +
        '<td style="font-weight:500;">' + esc(r.country) + '</td>' +
        '<td class="num" style="font-family:var(--font-mono);">' + (r.total_sales || '—') + '</td>' +
        '<td class="num" style="font-family:var(--font-mono);">' + (r.ob_logistics_cost || '—') + '</td>' +
        '<td class="num" style="font-family:var(--font-mono);">' + (r.operational_cost  || '—') + '</td>' +
        '<td style="font-size:12.5px;">' + (r.period_date ? formatDate(r.period_date) : '—') + '</td>' +
        '<td>' + statusBadge + '</td>' +
        '</tr>';
    }).join('');

    var saveBtn = valid > 0
      ? '<button type="button" class="sub-btn sub-btn-teal" onclick="doSave()"><i class="fas fa-floppy-disk"></i> Save All (' + valid + ' rows)</button>'
      : '<button type="button" class="sub-btn sub-btn-teal" disabled>No valid rows to save</button>';

    document.getElementById('previewContent').innerHTML =
      notice +
      '<div class="sub-tbl-wrap"><div style="overflow-x:auto;"><table class="sub-tbl">' +
      '<thead><tr>' +
      '<th>#</th><th>Country</th>' +
      '<th class="num">Total Sales (Cr)</th>' +
      '<th class="num">OB Cost (Cr)</th>' +
      '<th class="num">Ops Cost (Cr)</th>' +
      '<th>Period Date</th><th>Status</th>' +
      '</tr></thead><tbody>' + tRows + '</tbody></table></div></div>' +
      '<div style="display:flex;gap:10px;margin-top:14px;">' +
      saveBtn +
      '<button type="button" class="sub-btn sub-btn-ghost" onclick="switchSubTab(\'tab-upload\')"><i class="fas fa-arrow-left"></i> Back</button>' +
      '</div>';

    /* Enable preview tab and switch to it */
    var previewTabBtn = document.getElementById('previewTabBtn');
    if (previewTabBtn) previewTabBtn.disabled = false;
    switchSubTab('tab-preview');
  }

  /* ── Save confirmed rows ── */
  window.doSave = function () {
    document.getElementById('saveForm').submit();
  };

  /* ── Helpers ── */
  function showUploadError(msg) {
    var el = document.getElementById('uploadError');
    document.getElementById('uploadErrorText').innerHTML =
      '<i class="fas fa-circle-xmark"></i> ' + msg;
    el.style.display = 'block';
  }

  function esc(s) {
    return String(s || '')
      .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;');
  }

  function formatDate(iso) {
    if (!iso) return '—';
    var parts = iso.split('-');
    if (parts.length !== 3) return iso;
    var months = ['Jan','Feb','Mar','Apr','May','Jun','Jul','Aug','Sep','Oct','Nov','Dec'];
    return parts[2] + ' ' + months[parseInt(parts[1], 10) - 1] + ' ' + parts[0];
  }

  /* ── Auto-dismiss success alerts ── */
  document.querySelectorAll('.sub-alert.success').forEach(function (a) {
    setTimeout(function () {
      a.style.transition = 'opacity .5s';
      a.style.opacity = '0';
      setTimeout(function () { a.remove(); }, 500);
    }, 6000);
  });

})();
