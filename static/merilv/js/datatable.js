/* ============================================================================
 * Meril-V — shared UI utilities: toasts, confirm modals, row expansion,
 * dropzones, and client-side narrowing of an already-paginated table.
 *
 * Vanilla JS, no jQuery, no framework. Exposed as window.mvUI so screen
 * scripts (scanner.js) can reuse the toast queue and CSRF helper.
 * ========================================================================= */
(function () {
    'use strict';

    /* ── CSRF ────────────────────────────────────────────────────────────── */
    function getCookie(name) {
        var m = document.cookie.match(new RegExp('(^|;\\s*)' + name + '=([^;]*)'));
        return m ? decodeURIComponent(m[2]) : null;
    }
    function csrfHeaders(extra) {
        var h = extra || {};
        h['X-CSRFToken'] = getCookie('csrftoken') || '';
        return h;
    }

    /* ── Toasts — one at a time, the rest queued ─────────────────────────── */
    var toastQueue = [];
    var toastShowing = false;

    var TOAST_ICON = {
        ok: '<circle cx="12" cy="12" r="8"/><path d="M8.5 12l2.5 2.5L16 9.5"/>',
        danger: '<circle cx="12" cy="12" r="8"/><path d="M9 9l6 6M15 9l-6 6"/>',
        warn: '<path d="M12 4 3 19h18z"/><path d="M12 10v4M12 16.5h.01"/>',
        // A padlock: a step not taken, which is neither a warning about the
        // thing scanned nor an error in it. Sharing the warning triangle put
        // "no delivery focused" in the same visual bucket as a duplicate.
        blocked: '<rect x="5" y="11" width="14" height="9" rx="2"/><path d="M8 11V8a4 4 0 0 1 8 0v3"/>'
    };

    function renderToast(t) {
        var host = document.getElementById('mvToasts');
        if (!host) { return; }
        toastShowing = true;

        var el = document.createElement('div');
        el.className = 'mv-toast mv-toast--' + t.family;
        el.setAttribute('role', t.family === 'danger' ? 'alert' : 'status');
        el.innerHTML =
            '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5" ' +
            'stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">' +
            (TOAST_ICON[t.family] || TOAST_ICON.warn) + '</svg>' +
            '<div><div class="mv-toast__title"></div>' +
            (t.detail ? '<div class="mv-toast__detail"></div>' : '') + '</div>';
        el.querySelector('.mv-toast__title').textContent = t.title;
        if (t.detail) { el.querySelector('.mv-toast__detail').textContent = t.detail; }
        host.appendChild(el);

        function dismiss() {
            if (!el.parentNode) { return; }
            el.parentNode.removeChild(el);
            toastShowing = false;
            if (toastQueue.length) { renderToast(toastQueue.shift()); }
        }
        el.addEventListener('click', dismiss);
        /* Success self-dismisses; an error stays until acknowledged. */
        if (t.family !== 'danger') { setTimeout(dismiss, 4000); }
    }

    function toast(family, title, detail) {
        var t = { family: family, title: title, detail: detail };
        if (toastShowing) { toastQueue.push(t); } else { renderToast(t); }
    }

    /* ── Confirm modal ───────────────────────────────────────────────────────
     * Any element with data-mv-confirm gets intercepted. If
     * data-mv-confirm-type is present the user must type that exact string —
     * used for bulk import with Replace, delete entity, remove role. */
    function buildConfirm(opts, onConfirm) {
        var scrim = document.createElement('div');
        scrim.className = 'mv-modal-scrim is-open';
        var needsTyping = !!opts.typed;

        scrim.innerHTML =
            '<div class="mv-modal" role="dialog" aria-modal="true" aria-labelledby="mvCfTitle">' +
            '  <div class="mv-modal__head">' +
            '    <h2 class="mv-t-h2" id="mvCfTitle"></h2>' +
            '    <button type="button" class="mv-btn mv-btn--ghost mv-btn--icon" data-cf="cancel" aria-label="Close">' +
            '      <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5" ' +
            '        stroke-linecap="round"><path d="M6 6l12 12M18 6 6 18"/></svg>' +
            '    </button>' +
            '  </div>' +
            '  <div class="mv-modal__body">' +
            '    <p class="mv-t-body" data-cf="msg" style="margin:0"></p>' +
            (needsTyping
                ? '    <div class="mv-field"><label class="mv-label" for="mvCfType">Type ' +
                  '<span class="mv-mono"></span> to confirm</label>' +
                  '<input class="mv-input mv-input--mono" id="mvCfType" autocomplete="off"></div>'
                : '') +
            '  </div>' +
            '  <div class="mv-modal__foot">' +
            '    <button type="button" class="mv-btn" data-cf="cancel">Cancel</button>' +
            '    <button type="button" class="mv-btn mv-btn--danger" data-cf="ok">' +
            (opts.okLabel || 'Confirm') + '</button>' +
            '  </div>' +
            '</div>';

        scrim.querySelector('#mvCfTitle').textContent = opts.title || 'Are you sure?';
        scrim.querySelector('[data-cf="msg"]').textContent = opts.message || '';
        if (needsTyping) {
            scrim.querySelector('.mv-label .mv-mono').textContent = opts.typed;
        }

        var okBtn = scrim.querySelector('[data-cf="ok"]');
        if (needsTyping) {
            okBtn.disabled = true;
            var input = scrim.querySelector('#mvCfType');
            input.addEventListener('input', function () {
                okBtn.disabled = input.value.trim() !== opts.typed;
            });
        }

        function close() { if (scrim.parentNode) { scrim.parentNode.removeChild(scrim); } }
        scrim.addEventListener('click', function (e) {
            if (e.target === scrim || e.target.closest('[data-cf="cancel"]')) { close(); }
        });
        document.addEventListener('keydown', function esc(e) {
            if (e.key === 'Escape') { close(); document.removeEventListener('keydown', esc); }
        });
        okBtn.addEventListener('click', function () { close(); onConfirm(); });

        /* Inside `.mv`, not <body>: tokens.css scopes every --mv-* property
         * to `.mv`, so a confirm dialog mounted on <body> paints as
         * transparent text over the scrim -- the same defect the delivery
         * serials modal in scanner.js hit. `.mv` sets no transform, filter or
         * contain, so the fixed scrim is still viewport-relative. */
        (document.querySelector('.mv') || document.body).appendChild(scrim);
        var first = scrim.querySelector('#mvCfType') || okBtn;
        first.focus();
    }

    function wireConfirms() {
        document.addEventListener('click', function (e) {
            var trigger = e.target.closest('[data-mv-confirm]');
            if (!trigger) { return; }
            if (trigger.dataset.mvConfirmed === '1') { return; }
            e.preventDefault();
            e.stopPropagation();
            buildConfirm({
                title: trigger.dataset.mvConfirmTitle || 'Are you sure?',
                message: trigger.dataset.mvConfirm,
                okLabel: trigger.dataset.mvConfirmOk,
                typed: trigger.dataset.mvConfirmType
            }, function () {
                trigger.dataset.mvConfirmed = '1';
                /* A form marked data-mv-async is posted where it stands and
                   answered with a toast, rather than navigating.
                   A destructive action on a long table used to submit, redirect
                   to some other screen, and print its outcome in the page-top
                   alert block — so the answer to "delete this row" arrived
                   after a page load, somewhere else, scrolled to the top. */
                if (trigger.form && 'mvAsync' in trigger.form.dataset) {
                    submitFormAsync(trigger.form);
                } else if (trigger.form) {
                    trigger.form.submit();
                } else if (trigger.tagName === 'A') {
                    window.location.href = trigger.getAttribute('href');
                } else {
                    trigger.click();
                }
            });
        }, true);
    }

    /* Post a form without leaving the page, and say what happened where the
     * operator is looking.
     *
     * The view answers an XHR with {ok, message} instead of a redirect (see
     * _wants_json in merilv/views.py). On success the row this form sits in is
     * removed — data-mv-async-remove names it by selector, defaulting to the
     * enclosing <tr> — so the table tells the truth without a reload.
     *
     * The form still works with JavaScript off: it is a real <form> with a
     * real action, and the view still redirects for that caller. */
    function submitFormAsync(form) {
        var trigger = form.querySelector('[data-mv-confirm]');
        if (trigger) { trigger.disabled = true; }

        fetch(form.action, {
            method: (form.method || 'post').toUpperCase(),
            headers: csrfHeaders({ 'X-Requested-With': 'XMLHttpRequest' }),
            body: new FormData(form)
        }).then(function (response) {
            return response.json().then(function (body) {
                return { ok: response.ok, body: body || {} };
            });
        }).then(function (res) {
            if (!res.ok || !res.body.ok) {
                /* A refusal is the operator's to read and act on, so it stays
                   until dismissed — toast() keeps 'danger' on screen. */
                toast('danger', 'Not deleted', res.body.message || 'That could not be done.');
                return;
            }
            toast('ok', 'Deleted', res.body.message || '');
            var target = form.closest(form.dataset.mvAsyncRemove || 'tr');
            if (target && target.parentNode) { target.parentNode.removeChild(target); }
        }).catch(function () {
            toast('danger', 'Not deleted', 'Check your connection and try again.');
        }).finally(function () {
            if (trigger) { trigger.disabled = false; delete trigger.dataset.mvConfirmed; }
        });
    }

    /* ── Plain modal open/close ───────────────────────────────────────────
     * A trigger with data-mv-open-modal="id" opens #id (a .mv-modal-scrim).
     * Anything with data-mv-close-modal, the scrim background, or Esc closes
     * the nearest open one. These are ordinary <form method="post"> modals —
     * the confirm-modal above is the separate JS-built one for destructive
     * actions without their own markup. */
    function wireModals() {
        document.addEventListener('click', function (e) {
            var opener = e.target.closest('[data-mv-open-modal]');
            if (opener) {
                var target = document.getElementById(opener.dataset.mvOpenModal);
                if (target) {
                    // An "Add" trigger usually reuses the very modal a row's
                    // "Edit" fills in, so data-mv-reset-title asks for the
                    // previous row's values to be cleared and the heading
                    // relabelled first. Without it the add form silently
                    // reopens on whatever was last edited and saves over it.
                    if ('mvResetTitle' in opener.dataset) {
                        var form = target.querySelector('form');
                        if (form) {
                            form.reset();
                            var pk = form.querySelector('[name="pk"]');
                            if (pk) { pk.value = ''; }
                            // An Edit handler may have locked the primary-key
                            // field (editing a code would fork a new row rather
                            // than rename), so Add has to unlock it again —
                            // form.reset() restores values, never readOnly.
                            form.querySelectorAll('.mv-input--locked').forEach(function (field) {
                                field.readOnly = false;
                                field.classList.remove('mv-input--locked');
                            });
                        }
                        var heading = target.querySelector('.mv-modal__head h2');
                        if (heading) { heading.textContent = opener.dataset.mvResetTitle; }
                    }
                    target.classList.add('is-open');
                    var first = target.querySelector('input, select, textarea, button');
                    if (first) { first.focus(); }
                }
                return;
            }
            var closer = e.target.closest('[data-mv-close-modal]');
            var scrim = e.target.closest('.mv-modal-scrim');
            if (closer || (scrim && e.target === scrim)) {
                closeOpenModal();
            }
        });
        document.addEventListener('keydown', function (e) {
            if (e.key !== 'Escape') { return; }
            closeOpenModal();
        });
    }

    /* Closing a modal is announced, because something has to take the
     * keyboard back afterwards. On the scanner console that is the scan
     * input: a modal steals focus on open (see the focus() above) and
     * returning it to <body> means the next scanned code goes nowhere. */
    function closeOpenModal() {
        var open = document.querySelector('.mv-modal-scrim.is-open');
        if (!open) { return; }
        open.classList.remove('is-open');
        document.dispatchEvent(new CustomEvent('mv:modal-closed', { detail: { id: open.id || '' } }));
    }

    /* ── Clickable table rows ────────────────────────────────────────────────
     * A row carrying data-mv-href navigates like a link.
     *
     * This replaced inline `onclick="window.location=..."`, which had a bug
     * users could feel: most of these rows also contain a real <a> to the SAME
     * destination (the run name, a "Resume" button…). Clicking that link
     * navigated once via the anchor and again via the row's onclick, pushing
     * TWO identical history entries — so the first Back press only moved
     * between the duplicates and appeared to do nothing, and a second press
     * was needed to actually leave the page.
     *
     * Two rules fix it: never act when the click originated on something
     * interactive (that element already handles it), and honour modifier
     * clicks the way a browser does for links. */
    var INTERACTIVE = 'a, button, input, select, textarea, label, summary, [data-mv-no-nav]';

    function rowTarget(e) {
        var row = e.target.closest('[data-mv-href]');
        if (!row) { return null; }
        /* closest() from the click target: only skips when the click actually
         * started inside the interactive element, not merely near one. */
        if (e.target.closest(INTERACTIVE)) { return null; }
        /* Let text selection win over navigation — dragging to select a cell
         * shouldn't yank the user to another page. */
        var sel = window.getSelection && window.getSelection();
        if (sel && String(sel).length > 0 && !sel.isCollapsed) { return null; }
        return row.dataset.mvHref || null;
    }

    function wireRowLinks() {
        document.addEventListener('click', function (e) {
            var url = rowTarget(e);
            if (!url) { return; }
            if (e.ctrlKey || e.metaKey) { window.open(url, '_blank', 'noopener'); return; }
            if (e.shiftKey) { window.open(url, '_blank', 'noopener,width=1200,height=900'); return; }
            window.location.href = url;
        });

        /* Middle-click — 'click' doesn't fire for button 1 in modern browsers. */
        document.addEventListener('auxclick', function (e) {
            if (e.button !== 1) { return; }
            var url = rowTarget(e);
            if (!url) { return; }
            e.preventDefault();
            window.open(url, '_blank', 'noopener');
        });

        /* Keyboard: a row is only reachable when it was given a tabindex, so
         * this is a no-op for plain rows and real support where it was added. */
        document.addEventListener('keydown', function (e) {
            if (e.key !== 'Enter') { return; }
            var row = e.target.closest && e.target.closest('[data-mv-href][tabindex]');
            if (!row || row !== e.target) { return; }
            window.location.href = row.dataset.mvHref;
        });
    }

    /* ── Row expansion — reveals raw_scan / full detail ──────────────────── */
    function wireRowExpansion() {
        document.addEventListener('click', function (e) {
            var row = e.target.closest('tr[data-mv-expand]');
            if (!row) { return; }
            /* Checkbox and overflow cells must not toggle the row. */
            if (e.target.closest('.mv-col-check, .mv-col-actions, a, button, input')) { return; }
            var panel = document.getElementById(row.dataset.mvExpand);
            if (!panel) { return; }
            var open = panel.hasAttribute('hidden');
            if (open) { panel.removeAttribute('hidden'); } else { panel.setAttribute('hidden', ''); }
            row.setAttribute('aria-expanded', open ? 'true' : 'false');
        });
    }

    /* ── Dropzone ─────────────────────────────────────────────────────────────
     * Single-file zones keep the original behaviour: the label becomes the
     * chosen filename.
     *
     * A zone whose input carries `multiple` gets a basket instead. A native
     * file input REPLACES its selection on every pick — browse three invoices,
     * browse a fourth from another folder, and the first three are gone with no
     * warning. Consolidating a month's invoices out of several folders was
     * therefore impossible even though the input accepted many at once. The
     * basket accumulates across every browse and drop, dedupes, and lets a file
     * be removed individually; `input.files` is rebuilt from it through a
     * DataTransfer, since FileList itself is read-only.
     *
     * Keyed off `multiple` rather than a new attribute, so it applies exactly
     * where multi-file was already intended and nowhere else. */
    function extensionsFrom(input) {
        return (input.getAttribute('accept') || '')
            .split(',')
            .map(function (part) { return part.trim().toLowerCase(); })
            .filter(function (part) { return part.charAt(0) === '.'; });
    }

    function prettySize(bytes) {
        if (bytes < 1024) { return bytes + ' B'; }
        if (bytes < 1024 * 1024) { return (bytes / 1024).toFixed(0) + ' KB'; }
        return (bytes / (1024 * 1024)).toFixed(1) + ' MB';
    }

    function wireMultiDropzone(zone, input) {
        var basket = [];
        var allowed = extensionsFrom(input);

        var list = document.createElement('div');
        list.className = 'mv-filelist';
        // After the field, not inside the zone: a click anywhere in the zone
        // opens the picker, which would make the remove buttons unusable.
        (zone.closest('.mv-field') || zone).appendChild(list);

        var label = zone.querySelector('.mv-dropzone__label');
        var placeholder = label ? label.textContent : '';

        function badReason(file) {
            if (!allowed.length) { return ''; }
            var dot = file.name.lastIndexOf('.');
            var ext = dot === -1 ? '' : file.name.slice(dot).toLowerCase();
            return allowed.indexOf(ext) === -1
                ? 'not a ' + allowed.join(' / ') + ' file'
                : '';
        }

        /* FileList cannot be constructed or mutated, so the input's selection is
           reassembled from the basket. Files the server would reject are kept in
           the basket for display but left out of the POST. */
        function sync() {
            var transfer = new DataTransfer();
            basket.forEach(function (entry) {
                if (!entry.why) { transfer.items.add(entry.file); }
            });
            input.files = transfer.files;

            var good = basket.filter(function (e) { return !e.why; }).length;
            if (label) {
                label.textContent = good
                    ? good + (good === 1 ? ' file ready' : ' files ready')
                    : placeholder;
            }
            render();
        }

        function render() {
            list.innerHTML = '';
            basket.forEach(function (entry, index) {
                var row = document.createElement('div');
                row.className = 'mv-filelist__row' + (entry.why ? ' is-bad' : '');

                var name = document.createElement('span');
                name.className = 'mv-filelist__name';
                // textContent, not innerHTML — a filename is user-supplied.
                name.textContent = entry.file.name;
                row.appendChild(name);

                if (entry.why) {
                    var why = document.createElement('span');
                    why.className = 'mv-filelist__why';
                    why.textContent = entry.why;
                    row.appendChild(why);
                }

                var size = document.createElement('span');
                size.className = 'mv-filelist__size';
                size.textContent = prettySize(entry.file.size);
                row.appendChild(size);

                var remove = document.createElement('button');
                remove.type = 'button';
                remove.className = 'mv-filelist__x';
                remove.setAttribute('aria-label', 'Remove ' + entry.file.name);
                remove.textContent = '×';
                remove.addEventListener('click', function (ev) {
                    ev.preventDefault();
                    ev.stopPropagation();
                    basket.splice(index, 1);
                    sync();
                });
                row.appendChild(remove);

                list.appendChild(row);
            });
        }

        function add(files) {
            Array.prototype.forEach.call(files, function (file) {
                var duplicate = basket.some(function (entry) {
                    return entry.file.name === file.name && entry.file.size === file.size;
                });
                if (duplicate) { return; }
                basket.push({ file: file, why: badReason(file) });
            });
            sync();
        }

        input.addEventListener('change', function () {
            // A programmatic `input.value = ''` (the consolidator's tab switch)
            // empties the basket; a real pick adds to it.
            if (!input.files.length && !basket.length) { return; }
            if (!input.files.length) { basket = []; sync(); return; }
            add(input.files);
        });

        zone.addEventListener('drop', function (e) {
            e.preventDefault();
            if (e.dataTransfer && e.dataTransfer.files.length) { add(e.dataTransfer.files); }
        });

        return { clear: function () { basket = []; sync(); } };
    }

    function wireDropzones() {
        Array.prototype.forEach.call(
            document.querySelectorAll('.mv-dropzone'), function (zone) {
                var input = zone.querySelector('input[type="file"]');
                if (!input) { return; }

                zone.addEventListener('click', function () { input.click(); });
                zone.addEventListener('keydown', function (e) {
                    if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); input.click(); }
                });
                ['dragenter', 'dragover'].forEach(function (t) {
                    zone.addEventListener(t, function (e) {
                        e.preventDefault(); zone.classList.add('is-dragover');
                    });
                });
                ['dragleave', 'drop'].forEach(function (t) {
                    zone.addEventListener(t, function () { zone.classList.remove('is-dragover'); });
                });

                if (input.multiple) {
                    input.mvBasket = wireMultiDropzone(zone, input);
                    return;
                }

                zone.addEventListener('drop', function (e) {
                    e.preventDefault();
                    if (e.dataTransfer && e.dataTransfer.files.length) {
                        input.files = e.dataTransfer.files;
                        input.dispatchEvent(new Event('change', { bubbles: true }));
                    }
                });
                input.addEventListener('change', function () {
                    var label = zone.querySelector('.mv-dropzone__label');
                    if (!label) { return; }
                    if (!label.dataset.placeholder) { label.dataset.placeholder = label.textContent; }
                    label.textContent = input.files.length
                        ? Array.prototype.map.call(input.files, function (f) { return f.name; }).join(', ')
                        : label.dataset.placeholder;
                });
            });
    }

    /* ── Client-side narrowing of the current page of rows ────────────────────
     * Server-side pagination stays authoritative; this only filters the rows
     * already on screen, and says so in the footer so nobody mistakes it for a
     * full-table search. */
    function wireQuickFilter() {
        Array.prototype.forEach.call(
            document.querySelectorAll('[data-mv-quickfilter]'), function (input) {
                var table = document.getElementById(input.dataset.mvQuickfilter);
                if (!table) { return; }
                input.addEventListener('input', function () {
                    var q = input.value.trim().toLowerCase();
                    var shown = 0;
                    Array.prototype.forEach.call(
                        table.querySelectorAll('tbody tr:not(.mv-row-detail)'), function (tr) {
                            var hit = !q || tr.textContent.toLowerCase().indexOf(q) !== -1;
                            tr.hidden = !hit;
                            if (hit) { shown++; }
                        });
                    var out = document.getElementById(input.dataset.mvQuickfilterCount || '');
                    if (out) { out.textContent = shown; }
                });
            });
    }

    /* ── Scan-feed status filter ──────────────────────────────────────────────
     * Shared by the Outbound and ZVA05 consoles. A filter bar carries
     * data-mv-scanfilter-bar="<id of the log container>"; every row in that
     * container carries data-status="accepted|rejected" and data-code="<raw
     * barcode>". Filtering happens over the rows already in the DOM so it stays
     * instant and so rows appended by live scanning are covered too — which is
     * why applyScanFilters() is exposed for the scan handlers to call. */
    var scanFilters = [];

    function wireScanFilter() {
        Array.prototype.forEach.call(
            document.querySelectorAll('[data-mv-scanfilter-bar]'), function (bar) {
                var log = document.getElementById(bar.dataset.mvScanfilterBar);
                if (!log) { return; }
                var search = bar.querySelector('[data-mv-scanfilter-search]');
                var noMatch = log.parentNode
                    ? log.parentNode.querySelector('[data-mv-scanfilter-nomatch]')
                    : null;
                var mode = 'all';

                /* Scans the server did not render, per status.
                 *
                 * The feed is capped, and this tally can only see the rows in
                 * the DOM — so on a session with 769 logged scans the badges
                 * read "All 200 / Scanned 200 / Rejected 0" and the Scanned
                 * view was missing 529 of its rows. The cap stays (the whole
                 * session in the DOM is not a filter, it is a stall); what the
                 * cap left out is carried here and added back to each count.
                 * Live rows are appended to the DOM, so they are counted by
                 * the loop and must not be added twice. */
                function overflow(key) {
                    return parseInt(bar.dataset['mvScanfilterOver' + key], 10) || 0;
                }

                /* ── Delivery scope ──────────────────────────────────────
                 * The delivery the console is focused on, written onto the log
                 * container by scanner.js (data-mv-scope-delivery). While it is
                 * set this log answers "what has happened on THIS delivery",
                 * which is the question an operator picking one delivery is
                 * asking — the whole session's attempts interleaved is the
                 * answer to a different one.
                 *
                 * `scopeOff` is the reader's way back out. It widens the log
                 * only; the focus itself is a picking lock and clearing it
                 * would stop the operator scanning (see _focus_gate in
                 * views.py), so it is deliberately not touched from here. It
                 * resets whenever the focused delivery changes, so the default
                 * for each delivery is always its own rows. */
                var scopeOff = false;
                var scopeSeen = '';

                function scopeDelivery() {
                    var want = log.dataset.mvScopeDelivery || '';
                    if (want !== scopeSeen) { scopeSeen = want; scopeOff = false; }
                    return scopeOff ? '' : want;
                }

                function inScope(row, scope) {
                    if (!scope) { return true; }
                    var own = row.dataset.delivery || '';
                    /* An unattributed row is shown rather than hidden: rows
                     * logged before ScanLog carried a delivery have no way to
                     * prove they belong elsewhere, and dropping audit history
                     * on a guess is worse than showing a little too much. */
                    return !own || own === scope;
                }

                function apply() {
                    var q = (search && search.value || '').trim().toLowerCase();
                    var rows = log.querySelectorAll('.mv-scanrow');
                    var scope = scopeDelivery();
                    var tally = { all: 0, accepted: 0, rejected: 0 };
                    var shown = 0;
                    Array.prototype.forEach.call(rows, function (row) {
                        var st = row.dataset.status || 'rejected';
                        var scoped = inScope(row, scope);
                        /* Counted over the scoped rows, not every row in the
                         * DOM: a badge that tallies the whole session while the
                         * list below shows one delivery is the "the filter does
                         * nothing" report all over again. */
                        if (scoped) {
                            tally.all++;
                            tally[st] = (tally[st] || 0) + 1;
                        }
                        // data-code holds the full barcode; the visible row may
                        // show only the material, so search both.
                        var hay = (row.textContent + ' ' + (row.dataset.code || '')).toLowerCase();
                        var hit = scoped && (mode === 'all' || st === mode) && (!q || hay.indexOf(q) !== -1);
                        row.hidden = !hit;
                        if (hit) { shown++; }
                    });
                    Object.keys(tally).forEach(function (k) {
                        var out = bar.querySelector('[data-mv-count="' + k + '"]');
                        if (out) {
                            /* The overflow is what the server's row cap left
                               out of the WHOLE session, so it cannot be split
                               per delivery — adding it to a scoped tally would
                               overstate the delivery. The scope chip says the
                               list is narrowed instead. */
                            out.textContent = scope
                                ? tally[k]
                                : tally[k] + overflow(k.charAt(0).toUpperCase() + k.slice(1));
                        }
                    });
                    var chip = bar.querySelector('[data-mv-scanfilter-scope-chip]');
                    if (chip) {
                        var want = log.dataset.mvScopeDelivery || '';
                        chip.hidden = !want;
                        var label = chip.querySelector('[data-mv-scanfilter-scope-label]');
                        if (label) {
                            label.textContent = scope
                                ? ('Delivery ' + scope + ' only')
                                : ('All deliveries — ' + want + ' is focused');
                        }
                        var toggle = chip.querySelector('[data-mv-scanfilter-scope-toggle]');
                        if (toggle) {
                            toggle.textContent = scope
                                ? 'Show all deliveries'
                                : ('Show ' + want + ' only');
                        }
                    }
                    /* Say so when the list is a window onto a longer history,
                     * rather than letting a count and a row count disagree
                     * with no explanation. */
                    var moreEl = bar.querySelector('[data-mv-scanfilter-more]');
                    if (moreEl) {
                        var hidden = overflow('All');
                        moreEl.hidden = hidden === 0;
                        moreEl.textContent = hidden
                            ? (hidden + ' older scan' + (hidden === 1 ? '' : 's') +
                               (scope ? ' from this session not listed' : ' not listed'))
                            : '';
                    }
                    // The seeded "No scans yet" block is a genuine empty state,
                    // not a filter miss — keep the two distinct.
                    var seed = log.querySelector('[data-mv-empty="seed"]');
                    if (seed) { seed.hidden = rows.length > 0; }
                    if (noMatch) { noMatch.hidden = !(rows.length > 0 && shown === 0); }
                }

                Array.prototype.forEach.call(
                    bar.querySelectorAll('[data-mv-scanfilter]'), function (btn) {
                        btn.addEventListener('click', function () {
                            mode = btn.dataset.mvScanfilter;
                            Array.prototype.forEach.call(
                                bar.querySelectorAll('[data-mv-scanfilter]'), function (other) {
                                    other.setAttribute('aria-pressed', other === btn ? 'true' : 'false');
                                });
                            apply();
                        });
                    });
                if (search) { search.addEventListener('input', apply); }
                var scopeToggle = bar.querySelector('[data-mv-scanfilter-scope-toggle]');
                if (scopeToggle) {
                    scopeToggle.addEventListener('click', function () {
                        scopeOff = !scopeOff;
                        apply();
                    });
                }

                scanFilters.push(apply);
                apply();
            });
    }

    function applyScanFilters() {
        scanFilters.forEach(function (fn) { fn(); });
    }

    function init() {
        wireConfirms();
        wireModals();
        wireRowLinks();
        wireRowExpansion();
        wireDropzones();
        wireQuickFilter();
        wireScanFilter();
    }

    window.mvUI = {
        toast: toast,
        confirm: buildConfirm,
        csrfHeaders: csrfHeaders,
        getCookie: getCookie,
        // Call after appending a row to a filtered scan log, so the new row is
        // hidden/shown per the active filter and the tab counts stay right.
        applyScanFilters: applyScanFilters
    };

    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', init);
    } else {
        init();
    }
})();
