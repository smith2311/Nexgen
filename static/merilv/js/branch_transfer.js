/* ============================================================================
 * Meril-V — Branch Transfer Console
 *
 * Reconciles the plan summary and transfer-lines tables from the server's
 * `state` payload after every scan, auto-assign run, manual batch edit and
 * every 2s poll — replacing the old "accept, then reload the whole page"
 * behaviour, which was the client's literal "no real-time stats" complaint
 * (Row 4.4).
 * ========================================================================= */
(function () {
    'use strict';

    function init(root) {
        if (!root) { return; }
        var cfg = JSON.parse(root.dataset.mvBt || '{}');
        var ui = window.mvUI || { toast: function () {}, csrfHeaders: function (h) { return h || {}; } };

        var input = document.getElementById('mvBtScanInput');
        var status = document.getElementById('mvBtStatus');
        var log = document.getElementById('mvBtLog');
        var summaryHost = root.querySelector('[data-mv="bt-summary"]');
        var linesHost = root.querySelector('[data-mv="bt-lines"]');
        var autoAssignBtn = root.querySelector('[data-mv="auto-assign"]');
        var bulkForm = root.querySelector('[data-mv="bt-bulk-form"]');
        var bulkResult = root.querySelector('[data-mv="bt-bulk-result"]');

        var posting = false;
        var polling = false;

        function esc(s) {
            return String(s == null ? '' : s).replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;');
        }

        function post(url, body) {
            return fetch(url, { method: 'POST', headers: ui.csrfHeaders(), body: body || new FormData() })
                .then(function (r) { return r.json().then(function (j) { return { ok: r.ok, status: r.status, body: j }; }); });
        }

        /* ── Scan log ────────────────────────────────────────────────────── */
        function logRow(family, reasonLabel, code, message) {
            var row = document.createElement('div');
            row.className = 'mv-scanrow mv-scanrow--' + family;
            row.innerHTML =
                '<div class="mv-scanrow__time mv-mono"></div>' +
                '<div class="mv-scanrow__badge"><span class="mv-reason mv-reason--' + family + '"></span></div>' +
                '<div class="mv-scanrow__body"><div class="mv-scanrow__code mv-mono"></div><div class="mv-scanrow__msg"></div></div>';
            row.querySelector('.mv-scanrow__time').textContent = new Date().toLocaleTimeString([], { hour12: false });
            row.querySelector('.mv-reason').textContent = esc(reasonLabel || '');
            row.querySelector('.mv-scanrow__code').textContent = code || '—';
            row.querySelector('.mv-scanrow__msg').textContent = message || '';
            if (log) { log.insertBefore(row, log.firstChild); }
        }

        /* ── Plan summary (plant -> colour-zone) ────────────────────────── */
        function renderBtSummary(summary) {
            if (!summaryHost) { return; }
            summary = summary || { plants: [], grand_total: { required: 0, picked: 0, pending: 0, percent: 0 } };
            if (!summary.plants.length) {
                summaryHost.innerHTML = '<div class="mv-empty"><div class="mv-empty__title">No plan lines to summarise</div></div>';
                return;
            }
            var rows = '';
            summary.plants.forEach(function (p) {
                rows += '<tr class="mv-bt-summary__plant-row">' +
                    '<td class="mv-t-strong" rowspan="' + (p.zones.length + 1) + '">' + esc(p.plant) + '</td>' +
                    '<td colspan="5" class="mv-t-caption">Total: <strong>' + Math.round(p.required) + '</strong> plan &middot; ' +
                    '<strong>' + Math.round(p.picked) + '</strong> dispatched &middot; <strong>' + Math.round(p.pending) + '</strong> pending &middot; ' + p.percent + '%</td></tr>';
                p.zones.forEach(function (z) {
                    rows += '<tr><td><span class="mv-badge mv-badge--neutral">' + esc(z.zone) + '</span></td>' +
                        '<td class="mv-num">' + Math.round(z.required) + '</td>' +
                        '<td class="mv-num">' + Math.round(z.picked) + '</td>' +
                        '<td class="mv-num">' + (z.pending > 0 ? '<span class="mv-fg-warn">' + Math.round(z.pending) + '</span>' : '0') + '</td>' +
                        '<td><div class="mv-progress"><div class="mv-progress__bar' + (z.percent >= 100 ? ' mv-progress__bar--ok' : '') +
                        '" style="width:' + z.percent + '%"></div></div><span class="mv-t-caption">' + z.percent + '%</span></td></tr>';
                });
            });
            var gt = summary.grand_total;
            summaryHost.innerHTML =
                '<table class="mv-table mv-table--dense"><thead><tr><th>Plant</th><th>Colour zone</th>' +
                '<th class="mv-num">Plan</th><th class="mv-num">Dispatched</th><th class="mv-num">Pending</th><th>Progress</th></tr></thead>' +
                '<tbody>' + rows + '</tbody><tfoot><tr class="mv-t-strong"><td colspan="2">TOTAL</td>' +
                '<td class="mv-num">' + Math.round(gt.required) + '</td><td class="mv-num">' + Math.round(gt.picked) +
                '</td><td class="mv-num">' + Math.round(gt.pending) + '</td><td>' + gt.percent + '%</td></tr></tfoot></table>';
        }

        /* ── Transfer lines + batch assignment ──────────────────────────── */
        function renderBtLines(lines) {
            if (!linesHost) { return; }
            lines = lines || [];
            if (!lines.length) {
                linesHost.innerHTML = '<div class="mv-empty"><div class="mv-empty__title">This plan has no lines</div></div>';
                return;
            }
            var rows = lines.map(function (l) {
                var batchCell;
                if (l.locked) {
                    batchCell = '<span class="mv-mono">' + esc(l.assigned_batch || '—') + '</span> ' +
                        '<span class="mv-t-caption">(locked — already picked)</span>';
                } else {
                    batchCell = '<input type="text" class="mv-input mv-input--sm mv-mono" style="width:110px;" ' +
                        'data-mv-bt-batch-input="' + l.id + '" value="' + esc(l.assigned_batch || '') + '" placeholder="Auto">' +
                        (l.batch_overridden ? '<span class="mv-badge mv-badge--warn" style="margin-left:4px;">Manual</span>' : '');
                }
                return '<tr class="' + (l.pending <= 0 ? 'mv-fg-3' : '') + '" data-mv-bt-line="' + l.id + '">' +
                    '<td class="mv-mono mv-t-strong">' + esc(l.sku_code) + '</td>' +
                    '<td class="mv-mono">' + esc(l.supply_node_code || '—') + '</td>' +
                    '<td class="mv-mono">' + esc(l.receiving_node_code || '—') + '</td>' +
                    '<td class="mv-mono">' + esc(l.storage_location || '—') + '</td>' +
                    '<td class="mv-num">' + Math.round(l.required) + '</td>' +
                    '<td class="mv-num">' + Math.round(l.picked) + '</td>' +
                    '<td class="mv-num">' + (l.pending > 0 ? '<span class="mv-fg-warn">' + Math.round(l.pending) + '</span>' : '0') + '</td>' +
                    '<td>' + batchCell + '</td></tr>';
            }).join('');
            linesHost.innerHTML =
                '<table class="mv-table mv-table--dense mv-table--zebra"><thead><tr>' +
                '<th>SKU</th><th>Supply Node</th><th>Receiving Node</th><th>Storage</th>' +
                '<th class="mv-num">Required</th><th class="mv-num">Picked</th><th class="mv-num">Pending</th>' +
                '<th>Assigned batch</th></tr></thead><tbody>' + rows + '</tbody></table>';
        }

        function renderState(state) {
            if (!state) { return; }
            var t = state.totals || {};
            var byId = {
                'kpi-required': Math.round(t.required || 0), 'kpi-picked': Math.round(t.picked || 0),
                'kpi-pending': Math.round(t.pending || 0), 'kpi-percent': (t.percent || 0) + '%'
            };
            Object.keys(byId).forEach(function (key) {
                var el = root.querySelector('[data-mv="' + key + '"]');
                if (el) { el.textContent = byId[key]; }
            });
            renderBtSummary(state.summary);
            renderBtLines(state.lines);
        }

        function poll() {
            if (polling || posting) { return; }
            polling = true;
            fetch(cfg.stateUrl, { headers: { 'X-Requested-With': 'XMLHttpRequest' } })
                .then(function (r) { return r.json(); })
                .then(renderState)
                .catch(function () {})
                .finally(function () { polling = false; });
        }
        setInterval(poll, 2000);

        /* ── Scan input ──────────────────────────────────────────────────── */
        function refocus() { if (input && document.activeElement !== input && !document.querySelector('.mv-modal-scrim.is-open')) { input.focus(); } }
        if (input) {
            document.addEventListener('keydown', function (e) {
                if (e.target === input || document.querySelector('.mv-modal-scrim.is-open')) { return; }
                if (/^(INPUT|TEXTAREA|SELECT)$/.test(e.target.tagName)) { return; }
                if (e.key.length === 1 || e.key === 'Backspace') { refocus(); }
            });
            refocus();
            input.addEventListener('keydown', function (e) {
                if (e.key !== 'Enter' || posting) { return; }
                var raw = input.value.trim();
                if (!raw) { return; }
                posting = true;
                if (status) { status.textContent = 'Submitting…'; }
                var fd = new FormData();
                fd.append('raw_scan', raw);
                post(cfg.scanUrl, fd).then(function (res) {
                    var data = res.body || {};
                    var family = data.accepted ? 'ok' : 'danger';
                    logRow(family, data.reason_code || (data.accepted ? 'OK' : 'REJECTED'), raw, data.message);
                    if (status) { status.textContent = data.message || (data.accepted ? 'Accepted.' : 'Rejected.'); }
                    if (data.state) { renderState(data.state); }
                }).catch(function () {
                    if (status) { status.textContent = 'Network error — the scan may not have been recorded.'; }
                }).finally(function () { posting = false; input.value = ''; setTimeout(refocus, 50); });
            });
        }

        /* ── Bulk scan upload/paste ───────────────────────────────────── */
        if (bulkForm) {
            bulkForm.addEventListener('submit', function (e) {
                e.preventDefault();
                if (posting) { return; }
                var fd = new FormData(bulkForm);
                var file = bulkForm.querySelector('input[type="file"]');
                var text = bulkForm.querySelector('textarea');
                if ((!file || !file.files.length) && (!text || !text.value.trim())) {
                    ui.toast('danger', 'No scans found', 'Choose a scan workbook or paste barcode rows.');
                    return;
                }
                posting = true;
                if (window.mvLoadingOverlay && typeof window.mvLoadingOverlay.show === 'function') {
                    window.mvLoadingOverlay.show(bulkForm);
                }
                var button = bulkForm.querySelector('button[type="submit"]');
                if (button) { button.disabled = true; }
                if (bulkResult) { bulkResult.textContent = 'Processing scans...'; }
                post(cfg.bulkUrl, fd).then(function (res) {
                    var data = res.body || {};
                    if (!res.ok) {
                        ui.toast('danger', 'Bulk scan failed', data.message || 'Could not process scan data.');
                        if (bulkResult) { bulkResult.textContent = data.message || 'Bulk scan failed.'; }
                        return;
                    }
                    if (data.state) { renderState(data.state); }
                    var message = data.message || (data.accepted_count + ' accepted, ' + data.rejected_count + ' rejected.');
                    if (bulkResult) { bulkResult.textContent = message; }
                    logRow(data.accepted_count ? 'ok' : 'danger', 'BULK', 'scan file', message);
                    bulkForm.reset();
                }).catch(function () {
                    ui.toast('danger', 'Bulk scan failed', 'Check your connection and try again.');
                    if (bulkResult) { bulkResult.textContent = 'Bulk scan failed.'; }
                }).finally(function () {
                    posting = false;
                    if (button) { button.disabled = false; }
                    if (window.mvLoadingOverlay && typeof window.mvLoadingOverlay.hide === 'function') {
                        window.mvLoadingOverlay.hide();
                    }
                    setTimeout(refocus, 50);
                });
            });
        }

        /* ── Auto-assign + manual batch override ────────────────────────── */
        if (autoAssignBtn) {
            autoAssignBtn.addEventListener('click', function () {
                autoAssignBtn.disabled = true;
                post(cfg.autoAssignUrl).then(function (res) {
                    var data = res.body || {};
                    if (!res.ok || !data.ok) {
                        ui.toast('danger', 'Could not assign batches', data.message || 'Unknown error.');
                        return;
                    }
                    ui.toast('ok', 'Batches assigned', data.assigned + ' line(s) updated.');
                    if (data.state) { renderState(data.state); }
                }).catch(function () {
                    ui.toast('danger', 'Could not assign batches', 'Check your connection and try again.');
                }).finally(function () { autoAssignBtn.disabled = false; });
            });
        }

        if (linesHost) {
            linesHost.addEventListener('change', function (e) {
                var field = e.target.closest('[data-mv-bt-batch-input]');
                if (!field) { return; }
                var lineId = field.dataset.mvBtBatchInput;
                var batch = field.value.trim();
                if (!batch) { return; }
                var fd = new FormData();
                fd.append('line_id', lineId);
                fd.append('batch', batch);
                post(cfg.lineBatchUrl, fd).then(function (res) {
                    var data = res.body || {};
                    if (!res.ok || !data.ok) {
                        ui.toast('danger', 'Batch not saved', data.message || 'Unknown error.');
                        return;
                    }
                    if (data.state) { renderState(data.state); }
                }).catch(function () { ui.toast('danger', 'Batch not saved', 'Check your connection and try again.'); });
            });
        }

        /* First paint already server-rendered; poll keeps it fresh from here. */
    }

    document.addEventListener('DOMContentLoaded', function () {
        Array.prototype.forEach.call(document.querySelectorAll('[data-mv-bt]'), init);
    });
})();
