/* ============================================================================
 * Meril-V — Saved Layouts ("Layout Manager")
 *
 * One reusable "Columns" button + panel for any table that opts in via
 * <div data-mv-layout-manager data-screen-key="..." data-table="#selector">.
 * The table's <th>/<td> cells must each carry data-col="<key>" matching one
 * of the screen's registered column keys (merilv/layouts.py) — this module
 * only ever shows/hides/reorders/relabels cells that are already there; it
 * never fetches or renders row data itself, so it drops onto an existing
 * server-rendered table without touching how that table gets its rows.
 *
 * Named layouts are saved server-side per user (merilv/models.py:SavedLayout),
 * so a layout follows a user across devices — the legacy tool's own presets
 * lived in localStorage and never left the browser that made them.
 * ========================================================================= */
(function () {
    'use strict';

    function esc(s) {
        return String(s == null ? '' : s).replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;');
    }

    function init(root) {
        var screenKey = root.dataset.screenKey;
        var table = document.querySelector(root.dataset.table);
        if (!screenKey || !table) { return; }

        var ui = window.mvUI || { toast: function () {}, csrfHeaders: function (h) { return h || {}; } };
        var base = '/meril-v/layouts/' + encodeURIComponent(screenKey) + '/';

        var state = { availableColumns: [], layouts: [], activeId: null, working: [] };

        function post(url, fd) {
            return fetch(url, { method: 'POST', headers: ui.csrfHeaders(), body: fd || new FormData() })
                .then(function (r) { return r.json().then(function (j) { return { ok: r.ok, body: j }; }); });
        }

        /* ── Applying a layout to the live table ──────────────────────────── */
        function defaultWorkingSet() {
            var defaults = state.availableColumns.filter(function (c) { return c.default; }).map(function (c) { return c.key; });
            var rest = state.availableColumns.filter(function (c) { return !c.default; }).map(function (c) { return c.key; });
            return defaults.concat(rest).map(function (key) { return { key: key, header: '', enabled: defaults.indexOf(key) !== -1 }; });
        }

        function labelFor(key) {
            var def = state.availableColumns.filter(function (c) { return c.key === key; })[0];
            return def ? def.label : key;
        }

        function applyToTable(columns) {
            var order = columns.map(function (c) { return c.key; });
            var enabled = {};
            var headers = {};
            columns.forEach(function (c) { enabled[c.key] = !!c.enabled; if (c.header) { headers[c.key] = c.header; } });

            Array.prototype.forEach.call(table.querySelectorAll('tr'), function (row) {
                var byKey = {};
                Array.prototype.forEach.call(row.children, function (cell) {
                    if (cell.dataset.col) { byKey[cell.dataset.col] = cell; }
                });
                order.forEach(function (key) {
                    var cell = byKey[key];
                    if (!cell) { return; }
                    row.appendChild(cell);
                    cell.hidden = !enabled[key];
                });
            });

            var headRow = table.querySelector('thead tr');
            if (headRow) {
                Array.prototype.forEach.call(headRow.children, function (th) {
                    var key = th.dataset.col;
                    if (!key) { return; }
                    th.textContent = headers[key] || labelFor(key);
                });
            }
        }

        /* ── Panel ─────────────────────────────────────────────────────────── */
        var wrap = document.createElement('div');
        wrap.className = 'mv-layout-wrap';
        wrap.innerHTML =
            '<button type="button" class="mv-btn mv-btn--sm" data-mv-layout-toggle>' +
            '<svg viewBox="0 0 24 24" width="14" height="14" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round">' +
            '<path d="M12 3 3 8l9 5 9-5z"/><path d="M3 13l9 5 9-5"/></svg><span>Columns</span></button>' +
            '<div class="mv-layout-panel" hidden></div>';
        root.appendChild(wrap);
        var toggleBtn = wrap.querySelector('[data-mv-layout-toggle]');
        var panel = wrap.querySelector('.mv-layout-panel');

        function renderPanel() {
            var savedOptions = state.layouts.map(function (l) {
                return '<option value="' + l.id + '"' + (l.id === state.activeId ? ' selected' : '') + '>' + esc(l.name) + '</option>';
            }).join('');

            var rows = state.working.map(function (col, index) {
                return (
                    '<div class="mv-layout-row" data-key="' + esc(col.key) + '">' +
                    '<label class="mv-check"><input type="checkbox" data-mv-col-enabled' + (col.enabled ? ' checked' : '') + '>' +
                    '<span class="mv-t-caption">' + esc(labelFor(col.key)) + '</span></label>' +
                    '<input type="text" class="mv-input mv-input--sm" data-mv-col-header placeholder="' + esc(labelFor(col.key)) + '" value="' + esc(col.header || '') + '">' +
                    '<button type="button" class="mv-btn mv-btn--ghost mv-btn--icon mv-btn--sm" data-mv-col-up' + (index === 0 ? ' disabled' : '') + ' title="Move up">&uarr;</button>' +
                    '<button type="button" class="mv-btn mv-btn--ghost mv-btn--icon mv-btn--sm" data-mv-col-down' + (index === state.working.length - 1 ? ' disabled' : '') + ' title="Move down">&darr;</button>' +
                    '</div>'
                );
            }).join('');

            panel.innerHTML =
                '<div class="mv-layout-panel__head">' +
                '<select class="mv-select mv-select--sm" data-mv-layout-select style="flex:1;">' +
                '<option value="">Default view</option>' + savedOptions + '</select>' +
                '<button type="button" class="mv-btn mv-btn--ghost mv-btn--icon mv-btn--sm" data-mv-layout-delete title="Delete this saved layout"' +
                (state.activeId ? '' : ' disabled') + '>&times;</button>' +
                '</div>' +
                '<div class="mv-layout-panel__list">' + rows + '</div>' +
                '<div class="mv-layout-panel__foot">' +
                '<input type="text" class="mv-input mv-input--sm" data-mv-layout-name placeholder="Save as…" style="flex:1;">' +
                '<button type="button" class="mv-btn mv-btn--primary mv-btn--sm" data-mv-layout-save">Save &amp; apply</button>' +
                '</div>';

            wireRow();
        }

        function readWorkingFromPanel() {
            state.working = Array.prototype.map.call(panel.querySelectorAll('.mv-layout-row'), function (row) {
                return {
                    key: row.dataset.key,
                    enabled: row.querySelector('[data-mv-col-enabled]').checked,
                    header: row.querySelector('[data-mv-col-header]').value.trim(),
                };
            });
        }

        function wireRow() {
            panel.querySelectorAll('[data-mv-col-up]').forEach(function (btn, index) {
                btn.addEventListener('click', function () {
                    readWorkingFromPanel();
                    if (index === 0) { return; }
                    var tmp = state.working[index - 1];
                    state.working[index - 1] = state.working[index];
                    state.working[index] = tmp;
                    renderPanel();
                });
            });
            panel.querySelectorAll('[data-mv-col-down]').forEach(function (btn, index) {
                btn.addEventListener('click', function () {
                    readWorkingFromPanel();
                    if (index === state.working.length - 1) { return; }
                    var tmp = state.working[index + 1];
                    state.working[index + 1] = state.working[index];
                    state.working[index] = tmp;
                    renderPanel();
                });
            });
            panel.querySelectorAll('[data-mv-col-enabled], [data-mv-col-header]').forEach(function (el) {
                el.addEventListener('change', readWorkingFromPanel);
            });

            var select = panel.querySelector('[data-mv-layout-select]');
            select.addEventListener('change', function () {
                var id = select.value ? Number(select.value) : null;
                if (!id) {
                    state.activeId = null;
                    state.working = defaultWorkingSet();
                    applyToTable(state.working);
                    post(base + 'clear/');
                    renderPanel();
                    return;
                }
                post(base + id + '/activate/').then(function (res) {
                    if (!res.body.ok) { ui.toast('danger', 'Could not switch layout'); return; }
                    state.activeId = id;
                    state.working = res.body.layout.columns.slice();
                    applyToTable(state.working);
                    renderPanel();
                });
            });

            panel.querySelector('[data-mv-layout-delete]').addEventListener('click', function () {
                if (!state.activeId) { return; }
                post(base + state.activeId + '/delete/').then(function (res) {
                    if (!res.body.ok) { ui.toast('danger', 'Could not delete layout'); return; }
                    state.layouts = state.layouts.filter(function (l) { return l.id !== state.activeId; });
                    state.activeId = null;
                    state.working = defaultWorkingSet();
                    applyToTable(state.working);
                    renderPanel();
                    ui.toast('ok', 'Layout deleted');
                });
            });

            panel.querySelector('[data-mv-layout-save]').addEventListener('click', function () {
                readWorkingFromPanel();
                var nameInput = panel.querySelector('[data-mv-layout-name]');
                var name = nameInput.value.trim() || (state.layouts.filter(function (l) { return l.id === state.activeId; })[0] || {}).name;
                if (!name) {
                    ui.toast('warn', 'Name this layout', 'Type a name before saving.');
                    return;
                }
                var fd = new FormData();
                fd.append('name', name);
                fd.append('columns', JSON.stringify(state.working));
                fd.append('activate', '1');
                post(base + 'save/', fd).then(function (res) {
                    if (!res.body.ok) { ui.toast('danger', 'Could not save layout', res.body.message); return; }
                    var saved = res.body.layout;
                    var existing = state.layouts.filter(function (l) { return l.name === saved.name; })[0];
                    if (existing) { existing.columns = saved.columns; }
                    else { state.layouts.push({ id: saved.id, name: saved.name, columns: saved.columns, is_active: true }); }
                    state.activeId = saved.id;
                    state.working = saved.columns.slice();
                    applyToTable(state.working);
                    renderPanel();
                    ui.toast('ok', 'Layout saved', name);
                });
            });
        }

        toggleBtn.addEventListener('click', function () {
            panel.hidden = !panel.hidden;
        });
        document.addEventListener('click', function (e) {
            if (!wrap.contains(e.target)) { panel.hidden = true; }
        });

        /* ── First load ────────────────────────────────────────────────────── */
        fetch(base, { headers: { 'X-Requested-With': 'XMLHttpRequest' } })
            .then(function (r) { return r.json(); })
            .then(function (data) {
                if (!data.ok) { return; }
                state.availableColumns = data.available_columns;
                state.layouts = data.layouts;
                var active = state.layouts.filter(function (l) { return l.is_active; })[0];
                if (active) {
                    state.activeId = active.id;
                    state.working = active.columns.slice();
                } else {
                    state.working = defaultWorkingSet();
                }
                applyToTable(state.working);
                renderPanel();
            })
            .catch(function () {});
    }

    document.addEventListener('DOMContentLoaded', function () {
        Array.prototype.forEach.call(document.querySelectorAll('[data-mv-layout-manager]'), init);
    });
})();
