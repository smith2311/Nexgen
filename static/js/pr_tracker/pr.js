/* ── PR Tracker — shared JS ─────────────────────────────────────────────── */

(function () {
  'use strict';

  /* ── Tab switching (.lm-tabs / .lm-tab-content) ── */
  window.switchPrTab = function (tabId) {
    document.querySelectorAll('.lm-tab-btn').forEach(function (btn) {
      btn.classList.toggle('active', btn.dataset.tab === tabId);
    });
    document.querySelectorAll('.lm-tab-content').forEach(function (panel) {
      panel.classList.toggle('active', panel.id === tabId);
    });
  };

  /* Auto-activate first tab (fallback if no active set) */
  var firstActive = document.querySelector('.lm-tab-btn.active');
  if (firstActive) {
    var initialTab = firstActive.dataset.tab;
    if (initialTab) {
      document.querySelectorAll('.lm-tab-content').forEach(function (p) {
        p.classList.toggle('active', p.id === initialTab);
      });
    }
  }

  /* ── Drag-and-drop upload zones ── */
  document.querySelectorAll('.prt-upload-zone').forEach(function (zone) {
    zone.addEventListener('dragover', function (e) {
      e.preventDefault();
      zone.classList.add('drag-over');
    });
    zone.addEventListener('dragleave', function () {
      zone.classList.remove('drag-over');
    });
    zone.addEventListener('drop', function (e) {
      e.preventDefault();
      zone.classList.remove('drag-over');
      var files = e.dataTransfer.files;
      if (!files.length) return;
      var form = zone.closest('form');
      if (!form) return;
      var fileInput = form.querySelector('input[type="file"]');
      if (!fileInput) return;
      try { fileInput.files = files; } catch (_) {}
      var label = zone.querySelector('p');
      if (label) label.textContent = files[0].name;
    });
  });

  /* ── Progress bars — animate on load ── */
  document.querySelectorAll('.prt-progress-fill').forEach(function (bar) {
    var w = bar.style.width;
    bar.style.width = '0';
    setTimeout(function () { bar.style.width = w; }, 100);
  });

  /* ── Dashboard: select-all / bulk-delete row selection ── */
  var selectAll  = document.getElementById('prtSelectAll');
  var rowChecks  = document.querySelectorAll('.prt-row-check');
  var delBtn     = document.getElementById('prtBulkDeleteBtn');
  var selCountEl = document.getElementById('prtSelCount');

  function updateBulkDeleteState() {
    var checked = document.querySelectorAll('.prt-row-check:checked').length;
    if (delBtn) delBtn.disabled = checked === 0;
    if (selCountEl) selCountEl.textContent = checked + ' selected';
    if (selectAll) selectAll.checked = checked > 0 && checked === rowChecks.length;
  }

  if (selectAll) {
    selectAll.addEventListener('change', function () {
      rowChecks.forEach(function (cb) { cb.checked = selectAll.checked; });
      updateBulkDeleteState();
    });
  }
  rowChecks.forEach(function (cb) {
    cb.addEventListener('change', updateBulkDeleteState);
  });
  updateBulkDeleteState();

  /* ── Auto-dismiss success alerts after 6 s ── */
  document.querySelectorAll('.prt-alert.success').forEach(function (a) {
    setTimeout(function () {
      a.style.transition = 'opacity .5s';
      a.style.opacity = '0';
      setTimeout(function () { a.remove(); }, 500);
    }, 6000);
  });

})();
