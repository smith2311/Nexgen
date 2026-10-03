/* ═══════════════════════════════════════════════════════════════════════
   GST Reconciliation — records screen.

   Everything on this page is one of three things: a filter, a row action, or
   the upload. The filters live in one object, and every read of the server
   goes through load() with that object serialised — which is why the grid,
   the export and "Remove filtered" can never end up looking at different
   sets of records.

   The upload is a two-step job with a polled progress bar. The server does
   the work in a worker thread and writes real counts into a job row; this
   file asks for them every 600ms and stops when the job reaches a terminal
   state. No guessing, no animation pretending to be progress.
   ═══════════════════════════════════════════════════════════════════════ */
(function () {
    'use strict';

    var cfg = JSON.parse(document.getElementById('rcConfig').textContent);
    var options = JSON.parse(document.getElementById('rcFilterOptions').textContent);
    var pendingJob = JSON.parse(document.getElementById('rcPendingJobRaw').textContent);

    var POLL_MS = 600;
    var money = new Intl.NumberFormat('en-IN', { maximumFractionDigits: 2, minimumFractionDigits: 2 });
    var whole = new Intl.NumberFormat('en-IN');

    var state = {
        filters: { q: '', vendor: '', date_from: '', date_to: '',
                   upload_from: '', upload_to: '',
                   reason: '', presence: '', health: '', section: '', status: '',
                   cancelled: '' },
        sort: 'DocumentNumber',
        dir: 'asc',
        page: 1,
        pageSize: cfg.pageSize,
        pages: 1,
        rows: [],
        job: null,
        jobTimer: null,
        previewPage: 1,
        previewPages: 1,
        drawer: null
    };

    // ── tiny helpers ─────────────────────────────────────────────────────

    function $(id) { return document.getElementById(id); }
    function on(el, ev, fn) { if (el) el.addEventListener(ev, fn); }

    function esc(value) {
        if (value === null || value === undefined) { return ''; }
        return String(value).replace(/[&<>"']/g, function (ch) {
            return { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[ch];
        });
    }

    function csrf() {
        var match = document.cookie.match(/(?:^|;\s*)csrftoken=([^;]+)/);
        return match ? decodeURIComponent(match[1]) : '';
    }

    function api(url, opts) {
        opts = opts || {};
        var init = { method: opts.method || 'GET', headers: {}, credentials: 'same-origin' };
        if (opts.body !== undefined) {
            init.headers['Content-Type'] = 'application/json';
            init.body = JSON.stringify(opts.body);
        }
        if (init.method !== 'GET') { init.headers['X-CSRFToken'] = csrf(); }
        if (opts.form) { init.body = opts.form; init.headers['X-CSRFToken'] = csrf(); }

        return fetch(url, init).then(function (response) {
            return response.json().catch(function () {
                // A 500 renders Django's HTML error page, which is not JSON.
                // Say what actually happened rather than "unexpected token <".
                throw new Error('The server returned an unexpected response (' +
                                response.status + ').');
            }).then(function (data) {
                if (!response.ok || data.ok === false) {
                    throw new Error(data.error || ('Request failed (' + response.status + ').'));
                }
                return data;
            });
        });
    }

    function toast(message, kind) {
        kind = kind || 'ok';
        var icons = { ok: 'fa-circle-check', warn: 'fa-triangle-exclamation',
                      bad: 'fa-circle-exclamation' };
        var el = document.createElement('div');
        el.className = 'rc-toast ' + kind;
        el.innerHTML = '<i class="fas ' + icons[kind] + '"></i><div>' + esc(message) +
                       '</div><button type="button" aria-label="Dismiss">' +
                       '<i class="fas fa-xmark"></i></button>';
        el.querySelector('button').addEventListener('click', function () { el.remove(); });
        $('rcToasts').appendChild(el);
        setTimeout(function () { el.remove(); }, kind === 'bad' ? 12000 : 6000);
    }

    function fmtMoney(value) {
        if (value === null || value === undefined || value === 0) {
            return '<span class="rc-dash">&mdash;</span>';
        }
        var text = money.format(value);
        return value < 0 ? '<span class="rc-neg">' + text + '</span>' : text;
    }

    function fmtDate(iso) {
        if (!iso) { return '<span class="rc-dash">&mdash;</span>'; }
        var parts = iso.split('-');
        return parts[2] + '-' + parts[1] + '-' + parts[0];
    }

    function glyph(presence) {
        var p = presence || '---';
        return '<span class="rc-glyph">' +
               '<i class="' + (p.indexOf('G') >= 0 ? 'on-g' : '') + '">G</i>' +
               '<i class="' + (p.indexOf('E') >= 0 ? 'on-e' : '') + '">E</i>' +
               '<i class="' + (p.indexOf('W') >= 0 ? 'on-w' : '') + '">W</i></span>';
    }

    function chip(text, health) {
        if (!text) { return '<span class="rc-dash">&mdash;</span>'; }
        return '<span class="rc-chip ' + esc(health || 'idle') + '" title="' + esc(text) +
               '">' + esc(text) + '</span>';
    }

    // ── filter option lists ──────────────────────────────────────────────

    function fillSelect(select, rows, allLabel) {
        if (!select) { return; }
        var keep = select.value;
        select.innerHTML = '';
        select.appendChild(new Option(allLabel, ''));
        rows.forEach(function (row) {
            select.appendChild(new Option(
                row.value + '  (' + whole.format(row.count) + ')', row.value));
        });
        if (keep) { select.value = keep; }
    }

    function applyOptions(data) {
        options = data;
        fillSelect($('rcVendor'), data.vendors, 'All vendors');
        fillSelect($('rcReason'), data.reasons, 'All remarks');
        fillSelect($('rcPresence'), data.presence, 'All');
        fillSelect($('rcSection'), data.sections, 'All sections');
    }

    function refreshOptions() {
        api(cfg.urls.filters).then(function (data) { applyOptions(data.options); })
            .catch(function () { /* the lists are a convenience; stale is fine */ });
    }

    // ── the grid ─────────────────────────────────────────────────────────

    function query() {
        var params = new URLSearchParams();
        Object.keys(state.filters).forEach(function (key) {
            if (state.filters[key]) { params.set(key, state.filters[key]); }
        });
        params.set('sort', state.sort);
        params.set('dir', state.dir);
        params.set('page', state.page);
        params.set('page_size', state.pageSize);
        return params;
    }

    function readFilters() {
        state.filters.q = $('rcSearch').value.trim();
        state.filters.vendor = $('rcVendor').value;
        state.filters.date_from = $('rcFrom').value;
        state.filters.date_to = $('rcTo').value;
        state.filters.upload_from = $('rcUploadFrom').value;
        state.filters.upload_to = $('rcUploadTo').value;
        state.filters.reason = $('rcReason').value;
        state.filters.presence = $('rcPresence').value;
        state.filters.health = $('rcHealth').value;
        state.filters.section = $('rcSection').value;
        state.filters.status = $('rcStatus').value;
    }

    var loadToken = 0;

    function load(resetPage) {
        if (resetPage) { state.page = 1; }
        var token = ++loadToken;
        $('rcBusy').hidden = false;

        api(cfg.urls.records + '?' + query().toString()).then(function (data) {
            // A slow first request must not overwrite a fast second one.
            if (token !== loadToken) { return; }
            state.rows = data.rows;
            state.pages = data.pages;
            state.page = data.page;
            renderRows(data);
            renderCounts(data);
        }).catch(function (err) {
            if (token === loadToken) { toast(err.message, 'bad'); }
        }).finally(function () {
            if (token === loadToken) { $('rcBusy').hidden = true; }
        });
    }

    function renderCounts(data) {
        $('rcCount').textContent = data.filtered === data.total
            ? whole.format(data.total) + ' records'
            : whole.format(data.filtered) + ' of ' + whole.format(data.total);

        $('rcKpiTotal').textContent = whole.format(data.filtered);
        $('rcKpiOk').textContent = whole.format(data.health.ok);
        $('rcKpiWarn').textContent = whole.format(data.health.warn);
        $('rcKpiBad').textContent = whole.format(data.health.bad);
        if ($('rcKpiCancelled')) {
            $('rcKpiCancelled').textContent = whole.format(data.cancelled || 0);
        }
        // Truncated to one line by CSS, so the full text goes on the tooltip.
        var scope = data.describe.length
            ? data.describe.join(', ') : 'in the current view';
        $('rcKpiScope').textContent = scope;
        $('rcKpiScope').title = scope;

        $('rcFilterSummary').textContent = data.describe.length
            ? data.describe.join(' · ') : 'Active records';

        var from = data.filtered === 0 ? 0 : (data.page - 1) * data.page_size + 1;
        var to = Math.min(data.page * data.page_size, data.filtered);
        $('rcPagerInfo').textContent = data.filtered === 0
            ? 'Nothing to show'
            : 'Showing ' + whole.format(from) + '–' + whole.format(to) +
              ' of ' + whole.format(data.filtered);
        $('rcPage').textContent = data.page + ' / ' + data.pages;

        $('rcFirst').disabled = $('rcPrev').disabled = data.page <= 1;
        $('rcLast').disabled = $('rcNext').disabled = data.page >= data.pages;

        // Export is a plain navigation, so an empty result would show the
        // server's JSON refusal as a bare page. Easier to not offer it.
        $('rcExport').disabled = data.filtered === 0;
        $('rcPreviewExport').disabled = data.filtered === 0;
        if ($('rcRemoveFiltered')) { $('rcRemoveFiltered').disabled = !data.narrowed; }

        document.querySelectorAll('#rcKpis .rc-kpi').forEach(function (tile) {
            var pressed;
            if (tile.dataset.cancelled) {
                pressed = state.filters.cancelled === '1';
            } else {
                pressed = (tile.dataset.health || '') === (state.filters.health || '');
            }
            tile.setAttribute('aria-pressed', String(pressed));
        });

        document.querySelectorAll('#rcTable thead th.rc-sortable').forEach(function (th) {
            var active = th.dataset.sort === data.sort;
            th.classList.toggle('rc-sorted', active);
            th.querySelector('i').className = active
                ? 'fas fa-sort-' + (data.dir === 'desc' ? 'down' : 'up')
                : 'fas fa-sort';
        });
    }

    function rowHtml(row) {
        var actions = '<div class="rc-row-actions">' +
            '<button type="button" class="rc-icon-btn" data-open="' + row.id +
            '" title="Edit every field"><i class="fas fa-pen-to-square"></i></button>';
        if (cfg.perms.edit) {
            actions += row.IsDeleted
                ? '<button type="button" class="rc-icon-btn rc-good" data-restore="' + row.id +
                  '" title="Restore"><i class="fas fa-rotate-left"></i></button>'
                : '<button type="button" class="rc-icon-btn rc-danger" data-remove="' + row.id +
                  '" title="Remove"><i class="fas fa-trash-can"></i></button>';
        }
        actions += '</div>';

        var remark = chip(row.ReasonRemark, row.Health) +
            (row.ManualReason ? ' <span class="rc-chip pin">pinned</span>' : '') +
            (row.IsDeleted ? ' <span class="rc-chip bad">removed</span>' : '');

        var gap = (row.MaxGap && Math.abs(row.MaxGap) > 0.5)
            ? '<span class="rc-gap">' + money.format(row.MaxGap) + '</span>'
            : '<span class="rc-dash">&mdash;</span>';

        // Clicking an amount — or the document number itself — opens the
        // read-only detail modal, not the editor: the question behind "why is
        // this number what it is" is never "let me change it". The number is
        // included because that is the first cell a reader looks at; forcing
        // them to find an amount cell to drill in reads as a hidden affordance.
        function amount(value) {
            return '<button type="button" class="rc-amt-link" data-doc="' + row.id +
                   '">' + fmtMoney(value) + '</button>';
        }

        return '<tr class="' + (row.IsDeleted ? 'rc-row-removed' : '') + '">' +
            '<td>' + glyph(row.Presence) + '</td>' +
            '<td class="rc-doc"><button type="button" class="rc-doc-link" data-doc="' +
                row.id + '">' + esc(row.DocumentNumber) + '</button></td>' +
            '<td>' + esc(row.DocumentType) + '</td>' +
            '<td><span class="rc-party" title="' + esc(row.party) + '">' +
                esc(row.party) + '</span></td>' +
            '<td class="rc-num">' + fmtDate(row.GstDocumentDate) + '</td>' +
            '<td class="rc-num">' + amount(row.gst) + '</td>' +
            '<td class="rc-num">' + amount(row.einv) + '</td>' +
            '<td class="rc-num">' + amount(row.ewb) + '</td>' +
            '<td class="rc-num">' + gap + '</td>' +
            '<td>' + remark + '</td>' +
            '<td>' + actions + '</td></tr>';
    }

    function renderRows(data) {
        var body = $('rcBody');
        body.innerHTML = data.rows.map(rowHtml).join('');
        var empty = data.rows.length === 0;
        $('rcEmpty').hidden = !empty;
        $('rcTable').hidden = empty;
        $('rcEmptyHint').textContent = data.narrowed
            ? 'No document matches these filters. Try widening them, or click "Show all".'
            : 'Upload a reconciliation export above to begin.';
    }

    // ── row actions ──────────────────────────────────────────────────────

    function recordUrl(id, action) {
        return cfg.urls.recordBase + id + '/' + (action ? action + '/' : '');
    }

    on($('rcBody'), 'click', function (event) {
        var doc = event.target.closest('[data-doc]');
        if (doc) { openDocument(doc.dataset.doc); return; }

        var open = event.target.closest('[data-open]');
        if (open) { openDrawer(open.dataset.open); return; }

        var remove = event.target.closest('[data-remove]');
        if (remove) {
            if (!confirm('Remove this document? It is a soft delete — you can find and ' +
                         'restore it from the Record status filter.')) { return; }
            api(recordUrl(remove.dataset.remove, 'delete'), { method: 'POST', body: {} })
                .then(function (data) { toast(data.message); load(); refreshOptions(); })
                .catch(function (err) { toast(err.message, 'bad'); });
            return;
        }

        var restore = event.target.closest('[data-restore]');
        if (restore) {
            api(recordUrl(restore.dataset.restore, 'restore'), { method: 'POST', body: {} })
                .then(function (data) { toast(data.message); load(); refreshOptions(); })
                .catch(function (err) { toast(err.message, 'bad'); });
        }
    });

    // ── the document detail modal ────────────────────────────────────────
    //
    // Everything shown here comes from one call, and the verdict half of it is
    // generated by the classifier itself — the rule that fired and the sentence
    // behind it. Nothing in this file decides why a row says what it says.

    var docState = { id: null, panels: [] };

    function sourceCard(source) {
        var state, tone;
        if (!source.present) {
            state = 'Not present';
            tone = 'idle';
        } else if (!source.applicable) {
            state = 'Present, not compared';
            tone = 'info';
        } else {
            state = 'Compared';
            tone = 'ok';
        }
        var figure = source.taxable === null || source.taxable === undefined
            ? '<span class="rc-dash">NA</span>'
            : money.format(source.taxable);

        return '<div class="rc-src ' + (source.present ? '' : 'rc-src-absent') + '">' +
            '<div class="rc-src-head">' + esc(source.label) +
            '<span class="rc-chip ' + tone + '">' + state + '</span></div>' +
            '<div class="rc-src-value">' + figure + '</div>' +
            '<div class="rc-src-note">taxable value</div>' +
            '</div>';
    }

    function fieldRow(field) {
        return '<div class="rc-doc-field' + (field.empty ? ' rc-doc-empty' : '') + '">' +
            '<dt>' + esc(field.column) + '</dt>' +
            '<dd>' + esc(field.value) + '</dd></div>';
    }

    function renderPanels() {
        var showEmpty = $('rcDocShowEmpty').checked;
        var shown = 0, total = 0;

        $('rcDocPanels').innerHTML = docState.panels.map(function (panel) {
            var fields = panel.fields.filter(function (f) {
                total += 1;
                return showEmpty || !f.empty;
            });
            shown += fields.length;
            if (!fields.length) { return ''; }
            return '<section class="rc-doc-panel rc-panel-' + esc(panel.key) + '">' +
                '<h3>' + esc(panel.label) +
                '<span class="rc-doc-n">' + fields.length + '</span></h3>' +
                '<dl class="rc-doc-fields">' + fields.map(fieldRow).join('') + '</dl>' +
                '</section>';
        }).join('');

        $('rcDocFieldCount').textContent =
            whole.format(shown) + ' of ' + whole.format(total) + ' source columns shown';
    }

    function openDocument(id) {
        api(recordUrl(id, 'document')).then(function (data) {
            var v = data.verdict;
            docState = { id: id, panels: data.panels };

            $('rcDocNumber').textContent = data.record.DocumentNumber || '(no number)';
            $('rcDocType').textContent = data.record.DocumentType || 'NA';
            $('rcDocParty').textContent =
                [data.record.EntityGstin, data.record.party].filter(Boolean).join('  \u00b7  ');

            var remark = $('rcVerdictRemark');
            remark.className = 'rc-chip ' + (v.health || 'idle');
            remark.textContent = v.remark || 'NA';
            $('rcVerdictMap').innerHTML = glyph(v.presence);

            var tags = [];
            if (v.segment) { tags.push(v.segment); }
            if (v.section) { tags.push(v.section); }
            if (v.billing_code) { tags.push('Billing type ' + v.billing_code); }
            if (v.manual) { tags.push('pinned by hand'); }
            $('rcVerdictTags').innerHTML = tags.map(function (t) {
                return '<span class="rc-chip idle">' + esc(t) + '</span>';
            }).join('');

            $('rcVerdictWhy').textContent = v.why || '';

            $('rcVerdictSources').innerHTML = v.sources.map(sourceCard).join('');

            // The arithmetic, spelled out — this is the line that answers
            // "why is this a mismatch" or "why is this not one".
            var math;
            if (v.compared.length < 2) {
                math = v.compared.length === 1
                    ? 'Only ' + v.compared[0] + ' carries a comparable value, so there is ' +
                      'nothing to disagree with.'
                    : 'No source carries a comparable taxable value.';
            } else {
                math = 'Compared ' + v.compared.join(', ') + ' on taxable value: they differ ' +
                    'by ' + money.format(v.gap || 0) + ', and the tolerance is ' +
                    money.format(v.tolerance) + ' \u2014 ' +
                    (v.matched ? 'a match.' : 'a mismatch.');
            }
            $('rcVerdictMath').textContent = math;

            var bt = v.billing_type;
            $('rcVerdictBilling').textContent = bt
                ? 'Billing type ' + bt.code + ' (' + bt.description + ') expects ' +
                  bt.doc_type + ', and requires ' +
                  [bt.gst ? 'GST' : null, bt.einv ? 'E-Invoice' : null,
                   bt.ewb ? 'E-Way Bill' : null].filter(Boolean).join(' + ') + '.'
                : (v.billing_code
                    ? 'Billing type ' + v.billing_code + ' is not in the master, so the ' +
                      'generic rules applied.'
                    : 'This document carries no billing-type code, so the generic rules applied.');

            $('rcDocAudit').innerHTML =
                '<div><dt>Imported by</dt><dd>' + esc(data.audit.CreatedBy) + '</dd></div>' +
                '<div><dt>Source file</dt><dd>' + esc(data.audit.SourceFile) + '</dd></div>' +
                '<div><dt>Rule</dt><dd>' + esc(v.rule_id || 'NA') + '</dd></div>';

            renderPanels();
            $('rcDocBody').scrollTop = 0;
            $('rcDocModal').hidden = false;
            document.body.style.overflow = 'hidden';
        }).catch(function (err) { toast(err.message, 'bad'); });
    }

    function closeDocument() {
        $('rcDocModal').hidden = true;
        document.body.style.overflow = '';
    }

    document.querySelectorAll('[data-doc-close]').forEach(function (el) {
        on(el, 'click', closeDocument);
    });
    on($('rcDocModal'), 'click', function (event) {
        if (event.target === $('rcDocModal')) { closeDocument(); }
    });
    on($('rcDocShowEmpty'), 'change', renderPanels);
    on($('rcDocEdit'), 'click', function () {
        var id = docState.id;
        closeDocument();
        openDrawer(id);
    });

    // ── the edit drawer ──────────────────────────────────────────────────

    function openDrawer(id) {
        api(recordUrl(id)).then(function (data) {
            state.drawer = { id: id, groups: data.groups };
            $('rcDrawerTitle').textContent = data.record.DocumentNumber || '(no number)';
            $('rcDrawerSub').textContent =
                [data.record.DocumentType, data.record.EntityGstin, data.record.party]
                    .filter(Boolean).join(' · ');

            buildTabs(data.groups);
            buildFields(data.groups);

            $('rcManual').checked = data.classification.ManualReason;
            $('rcReasonRemark').value = data.classification.ReasonRemark;
            $('rcSegment').value = data.classification.Segment;
            $('rcGstr1Section').value = data.classification.Gstr1Section;
            $('rcHealthField').value = data.classification.Health || 'ok';
            syncPin();

            $('rcAudit').innerHTML =
                '<div><dt>Imported by</dt><dd>' + esc(data.audit.CreatedBy || '—') + '</dd></div>' +
                '<div><dt>Source file</dt><dd>' + esc(data.audit.SourceFile || '—') + '</dd></div>' +
                '<div><dt>Last updated</dt><dd>' +
                    esc((data.audit.UpdatedAt || '').replace('T', ' ').slice(0, 19) || '—') +
                '</dd></div>';

            $('rcDrawerHint').textContent =
                data.groups.reduce(function (n, g) { return n + g.fields.length; }, 0) +
                ' source columns, exactly as the export spells them.';

            $('rcDrawerHost').hidden = false;
            document.body.style.overflow = 'hidden';
            selectTab(data.groups[0].name);
        }).catch(function (err) { toast(err.message, 'bad'); });
    }

    function buildTabs(groups) {
        var tabs = groups.map(function (group) {
            return '<button type="button" class="rc-tab" role="tab" data-tab="' +
                   esc(group.name) + '" aria-selected="false">' + esc(group.label.split(' — ')[0]) +
                   '<span class="rc-tab-n">' + group.fields.length + '</span></button>';
        });
        tabs.push('<button type="button" class="rc-tab" role="tab" data-tab="__class" ' +
                  'aria-selected="false">Classification</button>');
        $('rcDrawerTabs').innerHTML = tabs.join('');
    }

    function buildFields(groups) {
        $('rcDrawerGroups').innerHTML = groups.map(function (group) {
            var inputs = group.fields.map(function (field) {
                return '<div class="rc-field"><label for="ef_' + esc(field.column) + '">' +
                       esc(field.column) + '</label>' +
                       '<input type="text" class="rc-input" id="ef_' + esc(field.column) +
                       '" data-column="' + esc(field.column) + '" value="' +
                       esc(field.value) + '"></div>';
            }).join('');
            return '<div data-group="' + esc(group.name) + '" hidden>' +
                   '<p class="rc-kpi-hint" style="margin:0 0 12px">' + esc(group.label) + '</p>' +
                   '<div class="rc-field-grid">' + inputs + '</div></div>';
        }).join('');
    }

    function selectTab(name) {
        document.querySelectorAll('#rcDrawerTabs .rc-tab').forEach(function (tab) {
            tab.setAttribute('aria-selected', String(tab.dataset.tab === name));
        });
        document.querySelectorAll('#rcDrawerGroups [data-group]').forEach(function (panel) {
            panel.hidden = panel.dataset.group !== name;
        });
        $('rcDrawerClassification').hidden = name !== '__class';
    }

    function syncPin() {
        var pinned = $('rcManual').checked;
        ['rcReasonRemark', 'rcSegment', 'rcGstr1Section', 'rcHealthField'].forEach(function (id) {
            $(id).disabled = !pinned;
        });
    }

    on($('rcDrawerTabs'), 'click', function (event) {
        var tab = event.target.closest('[data-tab]');
        if (tab) { selectTab(tab.dataset.tab); }
    });
    on($('rcManual'), 'change', syncPin);

    function closeDrawer() {
        $('rcDrawerHost').hidden = true;
        document.body.style.overflow = '';
        state.drawer = null;
    }

    document.querySelectorAll('[data-rc-close]').forEach(function (el) {
        on(el, 'click', closeDrawer);
    });

    on($('rcDrawerSave'), 'click', function () {
        if (!state.drawer) { return; }
        var fields = {};
        document.querySelectorAll('#rcDrawerGroups [data-column]').forEach(function (input) {
            fields[input.dataset.column] = input.value;
        });
        var payload = {
            fields: fields,
            classification: {
                ManualReason: $('rcManual').checked,
                ReasonRemark: $('rcReasonRemark').value,
                Segment: $('rcSegment').value,
                Gstr1Section: $('rcGstr1Section').value,
                Health: $('rcHealthField').value
            }
        };
        var button = $('rcDrawerSave');
        button.disabled = true;
        api(recordUrl(state.drawer.id, 'save'), { method: 'POST', body: payload })
            .then(function (data) {
                toast(data.message);
                closeDrawer();
                load();
                refreshOptions();
            })
            .catch(function (err) { toast(err.message, 'bad'); })
            .finally(function () { button.disabled = false; });
    });

    // ── add record ───────────────────────────────────────────────────────

    on($('rcAdd'), 'click', function () { $('rcAddModal').hidden = false; });
    document.querySelectorAll('[data-rc-close-add]').forEach(function (el) {
        on(el, 'click', function () { $('rcAddModal').hidden = true; });
    });

    on($('rcSaveNew'), 'click', function () {
        var payload = {
            DocumentNumber: $('nDoc').value.trim(),
            DocumentType: $('nType').value,
            EntityGstin: $('nEntity').value.trim(),
            GSTBillToTradeName: $('nParty').value.trim(),
            GstDocumentDate: $('nGstDate').value,
            GSTDocumentValue: $('nGstVal').value.trim(),
            EINVIrn: $('nIrn').value.trim(),
            EINVDocumentValue: $('nEinvVal').value.trim(),
            EwbNumber: $('nEwbNo').value.trim(),
            EWBDocumentValue: $('nEwbVal').value.trim(),
            GSTHsnOrSacCode: $('nHsn').value.trim(),
            GSTCustom1: $('nBillingType').value.trim()
        };
        api(cfg.urls.recordCreate, { method: 'POST', body: payload })
            .then(function (data) {
                toast(data.message);
                $('rcAddModal').hidden = true;
                ['nDoc', 'nEntity', 'nParty', 'nGstDate', 'nGstVal', 'nIrn',
                 'nEinvVal', 'nEwbNo', 'nEwbVal', 'nHsn', 'nBillingType']
                    .forEach(function (id) { $(id).value = ''; });
                load();
                refreshOptions();
            })
            .catch(function (err) { toast(err.message, 'bad'); });
    });

    // ── upload ───────────────────────────────────────────────────────────

    var chosenFile = null;

    function showStep(step) {
        ['rcUploadStep', 'rcProgressStep', 'rcPreviewStep'].forEach(function (id) {
            var el = $(id);
            if (el) { el.hidden = id !== step; }
        });
    }

    function setProgress(job) {
        var bar = $('rcBar');
        bar.style.width = job.percent + '%';
        bar.classList.toggle('rc-bar-error', job.state === 'error');
        $('rcProgressCount').textContent = job.total
            ? whole.format(job.done) + ' of ' + whole.format(job.total)
            : 'Working…';
        $('rcProgressPhase').textContent = job.phase || '';
    }

    function pollJob(id, onDone) {
        clearInterval(state.jobTimer);
        state.jobTimer = setInterval(function () {
            api(cfg.urls.jobBase + id + '/').then(function (data) {
                var job = data.job;
                setProgress(job);
                if (job.state === 'done' || job.state === 'error') {
                    clearInterval(state.jobTimer);
                    onDone(job);
                }
            }).catch(function (err) {
                clearInterval(state.jobTimer);
                toast(err.message, 'bad');
                showStep('rcUploadStep');
            });
        }, POLL_MS);
    }

    function pickFile(file) {
        // Picking a file is the user choosing; an older staged one stops being
        // relevant the moment they do.
        if (file && $('rcResume')) { $('rcResume').hidden = true; }
        chosenFile = file;
        $('rcFileName').hidden = !file;
        $('rcFileName').textContent = file ? file.name : '';
        $('rcRead').disabled = !file;
    }

    if (cfg.perms.import) {
        on($('rcPick'), 'click', function () { $('rcFile').click(); });
        on($('rcFile'), 'change', function (e) { pickFile(e.target.files[0] || null); });

        var drop = $('rcDrop');
        ['dragenter', 'dragover'].forEach(function (ev) {
            on(drop, ev, function (e) { e.preventDefault(); drop.classList.add('rc-drag'); });
        });
        ['dragleave', 'drop'].forEach(function (ev) {
            on(drop, ev, function (e) { e.preventDefault(); drop.classList.remove('rc-drag'); });
        });
        on(drop, 'drop', function (e) {
            if (e.dataTransfer.files.length) { pickFile(e.dataTransfer.files[0]); }
        });

        on($('rcRead'), 'click', function () {
            if (!chosenFile) { return; }
            var form = new FormData();
            form.append('file', chosenFile);
            form.append('mode', document.querySelector('input[name="rcMode"]:checked').value);

            showStep('rcProgressStep');
            setProgress({ percent: 2, done: 0, total: 0, phase: 'Uploading…', state: 'running' });

            api(cfg.urls.upload, { method: 'POST', form: form }).then(function (data) {
                state.job = data.job;
                pollJob(data.job.id, function (job) {
                    if (job.state === 'error') {
                        toast(job.message, 'bad');
                        showStep('rcUploadStep');
                        return;
                    }
                    state.job = job;
                    state.previewPage = 1;
                    loadPreview();
                });
            }).catch(function (err) {
                toast(err.message, 'bad');
                showStep('rcUploadStep');
            });
        });

        on($('rcConfirm'), 'click', function () {
            if (!state.job) { return; }
            var mode = document.querySelector('input[name="rcMode"]:checked').value;
            showStep('rcProgressStep');
            setProgress({ percent: 2, done: 0, total: state.job.row_count,
                          phase: 'Starting…', state: 'running' });

            api(cfg.urls.jobBase + state.job.id + '/confirm/',
                { method: 'POST', body: { mode: mode } }).then(function (data) {
                pollJob(data.job.id, function (job) {
                    if (job.state === 'error') {
                        toast(job.message, 'bad');
                        // The stage survives a failed import on purpose — the
                        // whole batch is one transaction, so nothing was
                        // written and Confirm can simply be pressed again.
                        state.job = job;
                        showStep('rcPreviewStep');
                        return;
                    }
                    toast(job.message);
                    state.job = null;
                    chosenFile = null;
                    pickFile(null);
                    showStep('rcUploadStep');
                    load(true);
                    refreshOptions();
                });
            }).catch(function (err) {
                toast(err.message, 'bad');
                showStep('rcPreviewStep');
            });
        });

        on($('rcDiscard'), 'click', function () {
            if (!state.job) { showStep('rcUploadStep'); return; }
            api(cfg.urls.jobBase + state.job.id + '/cancel/', { method: 'POST', body: {} })
                .then(function (data) {
                    toast(data.message);
                    state.job = null;
                    pickFile(null);
                    showStep('rcUploadStep');
                })
                .catch(function (err) { toast(err.message, 'bad'); });
        });

        on($('rcResumeOpen'), 'click', function () {
            if (!state.job) { return; }
            $('rcResume').hidden = true;
            state.previewPage = 1;
            loadPreview();
        });

        on($('rcResumeDiscard'), 'click', function () {
            if (!state.job) { $('rcResume').hidden = true; return; }
            api(cfg.urls.jobBase + state.job.id + '/cancel/', { method: 'POST', body: {} })
                .then(function (data) {
                    toast(data.message);
                    state.job = null;
                    $('rcResume').hidden = true;
                })
                .catch(function (err) { toast(err.message, 'bad'); });
        });

        on($('rcPreviewFull'), 'click', function () {
            if (!state.job) { return; }
            window.ReconPreview.open({
                url: cfg.urls.jobBase + state.job.id + '/preview/full/',
                title: 'Staged file — every column'
                // No download: nothing has been written yet, and the file the
                // user already has on disk is the one they uploaded.
            });
        });

        on($('rcPrevPreview'), 'click', function () {
            if (state.previewPage > 1) { state.previewPage -= 1; loadPreview(); }
        });
        on($('rcNextPreview'), 'click', function () {
            if (state.previewPage < state.previewPages) { state.previewPage += 1; loadPreview(); }
        });

        on($('rcRerun'), 'click', function () {
            if (!confirm('Re-classify every stored record against the current billing-type ' +
                         'master? Records pinned by hand keep their remark.')) { return; }
            showStep('rcProgressStep');
            setProgress({ percent: 2, done: 0, total: 0, phase: 'Starting…', state: 'running' });
            api(cfg.urls.rerun, { method: 'POST', body: {} }).then(function (data) {
                pollJob(data.job.id, function (job) {
                    toast(job.message, job.state === 'error' ? 'bad' : 'ok');
                    showStep('rcUploadStep');
                    load();
                    refreshOptions();
                });
            }).catch(function (err) {
                toast(err.message, 'bad');
                showStep('rcUploadStep');
            });
        });

        on($('rcRemoveFiltered'), 'click', function () {
            readFilters();
            var described = $('rcFilterSummary').textContent;
            if (!confirm('Soft-delete every record matching the current filters (' +
                         described + ')? This only affects what the filters above are ' +
                         'showing, and can be undone from the Record status filter.')) {
                return;
            }
            api(cfg.urls.removeFiltered, { method: 'POST', body: state.filters })
                .then(function (data) { toast(data.message); load(); refreshOptions(); })
                .catch(function (err) { toast(err.message, 'bad'); });
        });
    }

    function loadPreview() {
        api(cfg.urls.jobBase + state.job.id + '/preview/?page=' + state.previewPage)
            .then(function (data) {
                state.previewPages = data.pages;
                var job = data.job;
                $('rcPreviewTitle').textContent =
                    whole.format(job.row_count) + ' rows read from ' + job.filename;
                $('rcPreviewSub').textContent =
                    job.matched_headers + ' of ' + job.known_columns +
                    ' known columns matched. ' + job.message;
                $('rcPreviewBody').innerHTML = data.rows.map(function (row) {
                    return '<tr>' +
                        '<td>' + glyph(row.Presence) + '</td>' +
                        '<td class="rc-doc">' + esc(row.DocumentNumber) + '</td>' +
                        '<td>' + esc(row.DocumentType) + '</td>' +
                        '<td><span class="rc-party">' + esc(row.party) + '</span></td>' +
                        '<td class="rc-num">' + fmtMoney(row.gst) + '</td>' +
                        '<td class="rc-num">' + fmtMoney(row.einv) + '</td>' +
                        '<td class="rc-num">' + fmtMoney(row.ewb) + '</td>' +
                        '<td>' + chip(row.ReasonRemark, row.Health) + '</td></tr>';
                }).join('');
                $('rcPreviewPage').textContent = data.page + ' / ' + data.pages;
                $('rcPrevPreview').disabled = data.page <= 1;
                $('rcNextPreview').disabled = data.page >= data.pages;
                showStep('rcPreviewStep');
            })
            .catch(function (err) {
                toast(err.message, 'bad');
                showStep('rcUploadStep');
            });
    }

    // ── filter wiring ────────────────────────────────────────────────────

    var searchTimer = null;
    on($('rcSearch'), 'input', function () {
        clearTimeout(searchTimer);
        searchTimer = setTimeout(function () { readFilters(); load(true); }, 300);
    });

    ['rcVendor', 'rcFrom', 'rcTo', 'rcUploadFrom', 'rcUploadTo', 'rcReason',
     'rcPresence', 'rcHealth', 'rcSection', 'rcStatus'].forEach(function (id) {
        on($(id), 'change', function () { readFilters(); load(true); });
    });

    on($('rcReset'), 'click', function () {
        ['rcSearch', 'rcFrom', 'rcTo', 'rcUploadFrom', 'rcUploadTo']
            .forEach(function (id) { $(id).value = ''; });
        ['rcVendor', 'rcReason', 'rcPresence', 'rcHealth', 'rcSection', 'rcStatus']
            .forEach(function (id) { $(id).value = ''; });
        state.filters.cancelled = '';
        readFilters();
        load(true);
    });

    on($('rcKpis'), 'click', function (event) {
        var tile = event.target.closest('.rc-kpi');
        if (!tile) { return; }
        // Clicking the tile that is already applied clears it, so the tiles
        // work as a toggle rather than a one-way trap. Cancelled is its own
        // dimension (not a Health value), so it toggles independently.
        if (tile.dataset.cancelled) {
            state.filters.cancelled = state.filters.cancelled === '1' ? '' : '1';
        } else {
            var next = tile.dataset.health || '';
            $('rcHealth').value = (state.filters.health === next) ? '' : next;
            readFilters();
        }
        load(true);
    });

    document.querySelectorAll('#rcTable thead th.rc-sortable').forEach(function (th) {
        on(th, 'click', function () {
            var column = th.dataset.sort;
            if (state.sort === column) {
                state.dir = state.dir === 'asc' ? 'desc' : 'asc';
            } else {
                state.sort = column;
                state.dir = 'asc';
            }
            load(true);
        });
    });

    on($('rcPageSize'), 'change', function () {
        state.pageSize = parseInt($('rcPageSize').value, 10);
        load(true);
    });
    on($('rcFirst'), 'click', function () { state.page = 1; load(); });
    on($('rcPrev'), 'click', function () { state.page = Math.max(1, state.page - 1); load(); });
    on($('rcNext'), 'click', function () {
        state.page = Math.min(state.pages, state.page + 1); load();
    });
    on($('rcLast'), 'click', function () { state.page = state.pages; load(); });

    on($('rcExport'), 'click', function () {
        readFilters();
        // A download, not a fetch: the browser has to handle Content-Disposition.
        window.location.href = cfg.urls.exportRecords + '?' + query().toString();
    });

    on($('rcPreviewExport'), 'click', function () {
        readFilters();
        var params = query().toString();
        window.ReconPreview.open({
            url: cfg.urls.exportPreview + '?' + params,
            downloadUrl: cfg.urls.exportRecords + '?' + params,
            title: 'Records export'
        });
    });

    document.addEventListener('keydown', function (event) {
        if (event.key !== 'Escape') { return; }
        // Topmost first: the drawer can be opened from the detail modal.
        if (!$('rcDrawerHost').hidden) { closeDrawer(); }
        else if (!$('rcDocModal').hidden) { closeDocument(); }
        else if (!$('rcAddModal').hidden) { $('rcAddModal').hidden = true; }
    });

    // ── go ───────────────────────────────────────────────────────────────

    applyOptions(options);
    load();

    // A staged file from an earlier visit is OFFERED, never opened. Opening it
    // put a workbook the user had not chosen this session in front of them,
    // which is indistinguishable from the module importing something by itself.
    if (pendingJob && pendingJob.has_stage && $('rcResume')) {
        state.job = pendingJob;
        $('rcResumeName').textContent =
            pendingJob.filename + ' · ' + whole.format(pendingJob.row_count) + ' rows';
        $('rcResume').hidden = false;
    }
}());
