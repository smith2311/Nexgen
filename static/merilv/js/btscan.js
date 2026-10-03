/* ============================================================================
 * Meril-V — BT Scanning console
 *
 * The carton-tracked floor console for a branch transfer. The plan screen
 * (branch_transfer_plan) shows the same picked quantities; this one adds the
 * cartons, colour zones and location priority the legacy bt-scanner.js had.
 *
 * State always comes from the server — every mutation returns the full payload
 * and the whole view repaints from it, rather than the client patching its own
 * copy. A second gun on the same session then cannot drift, which is the bug
 * the outbound console's counter race turned out to be.
 * ========================================================================= */
(function () {
    'use strict';

    var root = document.getElementById('mvBtScan');
    if (!root) { return; }

    var ui = window.mvUI || { toast: function () {}, csrfHeaders: function (h) { return h || {}; } };
    var canOperate = root.dataset.canOperate === '1';
    var urls = {
        state: root.dataset.stateUrl,
        scan: root.dataset.scanUrl,
        undo: root.dataset.undoUrl,
        boxNew: root.dataset.boxNewUrl,
        focus: root.dataset.focusUrl,
        priority: root.dataset.priorityUrl
    };

    function el(name) { return root.querySelector('[data-mv="' + name + '"]'); }
    function setText(name, value) {
        var node = el(name);
        if (node) { node.textContent = value; }
    }
    function setStatus(text) { setText('status', text); }
    function round(value) { return Math.round(Number(value) || 0); }

    function post(url, fields) {
        var body = new FormData();
        Object.keys(fields || {}).forEach(function (key) { body.append(key, fields[key]); });
        return fetch(url, {
            method: 'POST',
            headers: ui.csrfHeaders({ 'X-Requested-With': 'XMLHttpRequest' }),
            body: body
        }).then(function (res) {
            return res.json().then(function (json) { return { ok: res.ok, body: json }; });
        });
    }

    // ── Rendering ───────────────────────────────────────────────────────
    function renderBoxes(state) {
        var tbody = el('box-rows');
        if (!tbody) { return; }
        if (!state.boxes.length) {
            tbody.innerHTML = '<tr><td colspan="4" class="mv-t-caption">No cartons yet.</td></tr>';
            return;
        }
        tbody.innerHTML = state.boxes.map(function (box) {
            var isActive = box.id === state.active_box;
            return '<tr>'
                + '<td class="mv-t-strong mv-mono">' + escapeHtml(box.name) + '</td>'
                + '<td>' + (box.zone ? zoneChip(box.zone) : '<span class="mv-t-caption">—</span>') + '</td>'
                + '<td class="mv-num">' + box.count + '</td>'
                + '<td>' + (isActive
                    ? '<span class="mv-badge mv-badge--ok">Open</span>'
                    : '<span class="mv-badge mv-badge--neutral">Closed</span>') + '</td>'
                + '</tr>';
        }).join('');
    }

    // Zone names come from a fixed server-side choice list, but they are
    // rendered as text rather than interpolated into a style attribute — a
    // colour name is not something to trust into CSS.
    function zoneChip(zone) {
        return '<span class="mv-badge mv-badge--neutral">' + escapeHtml(zone) + '</span>';
    }

    function escapeHtml(value) {
        return String(value == null ? '' : value)
            .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
            .replace(/"/g, '&quot;').replace(/'/g, '&#39;');
    }

    function renderLines(state) {
        var tbody = el('line-rows');
        if (!tbody) { return; }
        setText('line-count', state.lines.length + ' line(s)'
            + (state.focus_key ? ' for ' + state.focus_key : ''));
        if (!state.lines.length) {
            tbody.innerHTML = '<tr><td colspan="7" class="mv-t-caption">'
                + (state.focus_key
                    ? 'No lines for ' + escapeHtml(state.focus_key) + ' on this plan.'
                    : 'This plan has no lines.')
                + '</td></tr>';
            return;
        }
        tbody.innerHTML = state.lines.map(function (line) {
            var done = line.pending <= 0;
            return '<tr' + (done ? ' class="is-complete"' : '') + '>'
                + '<td class="mv-mono mv-t-strong">' + escapeHtml(line.sku_code) + '</td>'
                + '<td class="mv-mono">' + escapeHtml(line.receiving_node_code || '—') + '</td>'
                + '<td>' + (line.zone ? zoneChip(line.zone) : '<span class="mv-t-caption">—</span>') + '</td>'
                + '<td class="mv-mono">' + escapeHtml(line.assigned_batch || '—') + '</td>'
                + '<td class="mv-num">' + round(line.required) + '</td>'
                + '<td class="mv-num">' + round(line.picked) + '</td>'
                + '<td class="mv-num">' + round(line.pending) + '</td>'
                + '</tr>';
        }).join('');
    }

    function renderFocusOptions(state) {
        var select = el('focus-key');
        if (!select) { return; }
        var options = ['<option value="">All plants</option>'].concat(
            state.plants.map(function (plant) {
                var on = plant === state.focus_key ? ' selected' : '';
                return '<option value="' + escapeHtml(plant) + '"' + on + '>' + escapeHtml(plant) + '</option>';
            })
        );
        select.innerHTML = options.join('');
    }

    function render(state) {
        if (!state) { return; }
        setText('kpi-picked', round(state.totals.picked));
        setText('kpi-required', round(state.totals.required));
        setText('kpi-pending', round(state.totals.pending));
        setText('kpi-boxes', state.totals.boxes);
        setText('kpi-percent', state.totals.percent);

        var active = state.boxes.filter(function (b) { return b.id === state.active_box; })[0];
        setText('active-box', active ? active.name : 'no open carton');

        renderBoxes(state);
        renderLines(state);
        renderFocusOptions(state);
    }

    function refresh() {
        return fetch(urls.state, { headers: { 'X-Requested-With': 'XMLHttpRequest' } })
            .then(function (res) { return res.json(); })
            .then(render);
    }

    // ── Scanning ────────────────────────────────────────────────────────
    var posting = false;
    var input = el('scan-input');

    function submitScan(raw) {
        if (posting || !raw) { return; }
        posting = true;
        setStatus('Submitting…');
        post(urls.scan, { raw_scan: raw }).then(function (res) {
            var body = res.body || {};
            render(body.state);
            if (body.accepted) {
                setStatus('Picked into ' + (body.box || 'carton') + '. ' + (body.message || ''));
            } else {
                setStatus('Rejected — ' + (body.message || 'see the feed below.'));
                ui.toast('warn', 'Scan rejected', body.message || '');
            }
        }).catch(function () {
            setStatus('Network error — the scan may not have been recorded. Try again.');
            ui.toast('danger', 'Scan failed to submit', 'Check your connection and rescan if needed.');
        }).finally(function () {
            posting = false;
            if (input) { input.value = ''; input.focus(); }
        });
    }

    if (input) {
        input.addEventListener('keydown', function (event) {
            if (event.key === 'Enter') {
                event.preventDefault();
                submitScan(input.value.trim());
            }
        });
        // Guns type fast and then stop; refocusing keeps the next scan landing
        // in the box rather than on the page.
        root.addEventListener('click', function (event) {
            if (!event.target.closest('button, select, input, a')) { input.focus(); }
        });
    }

    function wire(name, handler) {
        var node = el(name);
        if (node) { node.addEventListener('click', handler); }
    }

    wire('undo', function () {
        post(urls.undo, {}).then(function (res) {
            var body = res.body || {};
            if (body.ok) {
                render(body.state);
                setStatus('Last pick reversed.');
            } else {
                ui.toast('warn', 'Nothing undone', body.message || '');
            }
        });
    });

    wire('new-box', function () {
        var zoneSelect = el('new-box-zone');
        post(urls.boxNew, { zone: zoneSelect ? zoneSelect.value : '' }).then(function (res) {
            var body = res.body || {};
            if (body.ok) {
                render(body.state);
                setStatus('Now scanning into ' + body.box + '.');
                ui.toast('ok', 'Carton opened', body.box);
            } else {
                ui.toast('warn', 'Could not open a carton', body.message || '');
            }
        });
    });

    wire('focus-apply', function () {
        var select = el('focus-key');
        post(urls.focus, { focus_key: select ? select.value : '' }).then(function (res) {
            var body = res.body || {};
            if (body.ok) {
                render(body.state);
                setStatus(body.state.focus_key
                    ? 'Locked to ' + body.state.focus_key + '.'
                    : 'Focus cleared — every plant is in scope.');
            } else {
                ui.toast('warn', 'Focus not applied', body.message || '');
            }
        });
    });

    wire('priority-apply', function () {
        var field = el('priority');
        post(urls.priority, { location_priority: field ? field.value : '' }).then(function (res) {
            var body = res.body || {};
            if (body.ok) {
                render(body.state);
                setStatus(body.location_priority
                    ? 'Drawing from ' + body.location_priority + ' first.'
                    : 'Using the entity default draw order.');
            }
        });
    });

    refresh();
    if (canOperate && input) { input.focus(); }
})();
