/* ================================================================
   VendorLink — Component behaviour (.vl scope)

   Vanilla, no framework. The codebase has neither Alpine nor HTMX
   anywhere, so pulling either in for this would add a dependency to
   every page for behaviour that is a few dozen lines here.

   Everything is exposed on window.VL so page scripts can call it,
   and every DOM write goes through textContent — chat replies,
   vendor names and server messages are all untrusted strings.
   ================================================================ */
(function () {
    'use strict';

    var VL = {};

    /* ── CSRF ────────────────────────────────────────────────────── */
    VL.csrf = function () {
        var m = document.cookie.match(/(?:^|;\s*)csrftoken=([^;]+)/);
        if (m) { return decodeURIComponent(m[1]); }
        var el = document.querySelector('input[name=csrfmiddlewaretoken]');
        return el ? el.value : '';
    };
    VL.csrfHeaders = function (extra) {
        var h = extra || {};
        h['X-CSRFToken'] = VL.csrf();
        return h;
    };

    /* ── Toast ───────────────────────────────────────────────────── */
    function toastHost() {
        var host = document.querySelector('.vl-toasts');
        if (!host) {
            host = document.createElement('div');
            host.className = 'vl-toasts';
            document.body.appendChild(host);
        }
        return host;
    }

    VL.toast = function (kind, title, detail, ms) {
        var el = document.createElement('div');
        el.className = 'vl-toast vl-toast--' + (kind || 'info');
        el.setAttribute('role', kind === 'error' ? 'alert' : 'status');

        var dot = document.createElement('span');
        dot.className = 'vl-toast__dot';
        el.appendChild(dot);

        var body = document.createElement('div');
        var strong = document.createElement('div');
        strong.style.fontWeight = '600';
        strong.textContent = title || '';
        body.appendChild(strong);
        if (detail) {
            var d = document.createElement('div');
            d.style.opacity = '.8';
            d.textContent = detail;
            body.appendChild(d);
        }
        el.appendChild(body);
        toastHost().appendChild(el);

        window.setTimeout(function () {
            el.classList.add('is-out');
            el.addEventListener('animationend', function () { el.remove(); }, { once: true });
        }, ms || 4200);
        return el;
    };

    /* ── Row flash after a successful action ─────────────────────── */
    VL.flashRow = function (row) {
        if (!row) { return; }
        row.classList.remove('vl-row-flash');
        void row.offsetWidth;              // restart the animation
        row.classList.add('vl-row-flash');
    };

    /* ── ConfirmModal, destructive variant ────────────────────────
       Typed confirmation: the button stays disabled until the user
       types the record's own name, so a destructive action cannot be
       carried by muscle memory alone. */
    VL.confirmDestructive = function (opts) {
        var scrim = document.createElement('div');
        scrim.className = 'vl-scrim';

        var modal = document.createElement('div');
        modal.className = 'vl-modal vl';
        modal.setAttribute('role', 'dialog');
        modal.setAttribute('aria-modal', 'true');

        var head = document.createElement('div');
        head.className = 'vl-card__head';
        var h = document.createElement('h3');
        h.className = 'vl-card__title';
        h.textContent = opts.title || 'Are you sure?';
        head.appendChild(h);

        var body = document.createElement('div');
        body.className = 'vl-card__body';
        var p = document.createElement('p');
        p.style.margin = '0 0 12px';
        p.textContent = opts.message || '';
        body.appendChild(p);

        var hint = document.createElement('label');
        hint.style.cssText = 'display:block;font-size:12px;margin-bottom:4px;';
        hint.textContent = 'Type ' + opts.confirmWord + ' to confirm';
        var input = document.createElement('input');
        input.className = 'vl-input';
        input.style.width = '100%';
        input.setAttribute('autocomplete', 'off');
        body.appendChild(hint);
        body.appendChild(input);

        var foot = document.createElement('div');
        foot.className = 'vl-card__head';
        foot.style.cssText = 'border-bottom:none;border-top:1px solid var(--vl-border);justify-content:flex-end;';
        var cancel = document.createElement('button');
        cancel.className = 'vl-btn';
        cancel.type = 'button';
        cancel.textContent = 'Cancel';
        var go = document.createElement('button');
        go.className = 'vl-btn vl-btn--danger';
        go.type = 'button';
        go.textContent = opts.confirmLabel || 'Delete';
        go.disabled = true;
        foot.appendChild(cancel);
        foot.appendChild(go);

        modal.appendChild(head);
        modal.appendChild(body);
        modal.appendChild(foot);
        document.body.appendChild(scrim);
        document.body.appendChild(modal);
        input.focus();

        function close() { scrim.remove(); modal.remove(); document.removeEventListener('keydown', onKey); }
        function onKey(e) { if (e.key === 'Escape') { close(); } }

        input.addEventListener('input', function () {
            go.disabled = input.value.trim() !== opts.confirmWord;
        });
        cancel.addEventListener('click', close);
        scrim.addEventListener('click', close);
        document.addEventListener('keydown', onKey);
        go.addEventListener('click', function () { close(); if (opts.onConfirm) { opts.onConfirm(); } });
    };

    /* ── Segmented control ───────────────────────────────────────── */
    VL.initSegments = function (root) {
        (root || document).querySelectorAll('.vl-seg').forEach(function (seg) {
            seg.addEventListener('click', function (e) {
                var btn = e.target.closest('button');
                if (!btn || !seg.contains(btn)) { return; }
                seg.querySelectorAll('button').forEach(function (b) {
                    b.setAttribute('aria-pressed', String(b === btn));
                });
                seg.dispatchEvent(new CustomEvent('vl:segment', {
                    bubbles: true, detail: { value: btn.dataset.value }
                }));
            });
        });
    };

    /* ── Report renderer ──────────────────────────────────────────
       Turns the `report` object from a MerilGPT answer into a card:
       summary, KPI tiles, one chart, a table and the caveats.

       Every section is optional and drawn only when it holds content, so
       a short report does not leave empty boxes behind. All text goes in
       through textContent — this is model output quoting vendor names and
       file contents, none of it safe as markup. */
    VL.renderReport = function (report) {
        if (!report) { return null; }

        var card = document.createElement('div');
        card.className = 'vl-report';

        var head = document.createElement('div');
        head.className = 'vl-report__head';
        var h = document.createElement('h4');
        h.className = 'vl-report__title';
        h.textContent = report.title || 'Report';
        var badge = document.createElement('span');
        badge.className = 'vl-report__badge';
        badge.textContent = 'Report';
        head.appendChild(h);
        head.appendChild(badge);
        card.appendChild(head);

        var body = document.createElement('div');
        body.className = 'vl-report__body';
        card.appendChild(body);

        if (report.summary) {
            var p = document.createElement('p');
            p.className = 'vl-report__summary';
            p.textContent = report.summary;
            body.appendChild(p);
        }

        if (report.kpis && report.kpis.length) {
            var kpis = document.createElement('div');
            kpis.className = 'vl-report__kpis';
            report.kpis.slice(0, 4).forEach(function (k) {
                var tile = document.createElement('div');
                tile.className = 'vl-report__kpi';
                var b = document.createElement('b');
                b.textContent = k.value == null ? '—' : String(k.value);
                var label = document.createElement('span');
                label.textContent = k.label || '';
                tile.appendChild(b);
                tile.appendChild(label);
                if (k.note) {
                    var note = document.createElement('span');
                    note.textContent = k.note;
                    note.style.opacity = '.75';
                    tile.appendChild(document.createElement('br'));
                    tile.appendChild(note);
                }
                kpis.appendChild(tile);
            });
            body.appendChild(kpis);
        }

        if (report.chart && window.Chart) {
            var wrap = document.createElement('div');
            wrap.className = 'vl-report__chart';
            var canvas = document.createElement('canvas');
            wrap.appendChild(canvas);
            body.appendChild(wrap);
            VL.drawChart(canvas, report.chart);
        } else if (report.chart) {
            // Chart.js absent — say so rather than leaving a silent gap the
            // reader could mistake for "there was no data".
            var miss = document.createElement('div');
            miss.className = 'vl-meta';
            miss.textContent = 'Chart unavailable — charting library not loaded.';
            body.appendChild(miss);
        }

        if (report.table && report.table.columns && report.table.columns.length) {
            var tw = document.createElement('div');
            tw.className = 'vl-report__tablewrap';
            var table = document.createElement('table');
            table.className = 'vl-report__table';

            var thead = document.createElement('thead');
            var htr = document.createElement('tr');
            report.table.columns.forEach(function (c) {
                var th = document.createElement('th');
                th.textContent = c;
                htr.appendChild(th);
            });
            thead.appendChild(htr);
            table.appendChild(thead);

            var tbody = document.createElement('tbody');
            (report.table.rows || []).forEach(function (row) {
                var tr = document.createElement('tr');
                (row || []).forEach(function (cell) {
                    var td = document.createElement('td');
                    td.textContent = cell == null ? '' : String(cell);
                    tr.appendChild(td);
                });
                tbody.appendChild(tr);
            });
            table.appendChild(tbody);
            tw.appendChild(table);
            body.appendChild(tw);
        }

        if (report.highlights && report.highlights.length) {
            var ul = document.createElement('ul');
            ul.className = 'vl-report__highlights';
            report.highlights.forEach(function (item) {
                var li = document.createElement('li');
                li.textContent = item;
                ul.appendChild(li);
            });
            body.appendChild(ul);
        }

        var actions = document.createElement('div');
        actions.className = 'vl-report__actions';
        var csv = document.createElement('button');
        csv.type = 'button';
        csv.className = 'vl-btn vl-btn--sm';
        csv.textContent = 'Download CSV';
        csv.addEventListener('click', function () { VL.reportToCsv(report); });
        actions.appendChild(csv);
        body.appendChild(actions);

        return card;
    };

    /* ── Charts ───────────────────────────────────────────────────

       One chart per report, and three ways to read it: the model picks
       a type, and the artifact toolbar lets the reader switch to
       another. Both paths go through VL.chartConfig, which is the
       point of splitting it out — the toolbar used to re-use the
       *drawn* datasets verbatim when it switched, which meant:

         - a doughnut built from a bar chart's dataset inherited that
           chart's single fill, so every vendor in a ten-vendor share
           came out the same indigo and the legend was ten identical
           swatches;
         - a line built from the same dataset inherited borderWidth:0
           — right for a bar, fatal for a line — so the points drew
           and nothing joined them up.

       Colour and stroke are presentation and belong to the *type*,
       not to the numbers. So the numbers are kept raw (rawChartData
       below) and the presentation is rebuilt on every draw. */

    /* Fallbacks match --vl-cat-* in vendorlink-tokens.css. They are
       repeated rather than only looked up because report cards also
       render on .vlx pages, where the .vl token scope is nowhere in
       the tree and getPropertyValue returns ''. */
    VL.CHART_SERIES = [
        '#2A78D6', '#EB6834', '#1BAF7A', '#EDA100',
        '#E87BA4', '#008300', '#4A3AA7', '#E34948'
    ];
    VL.CHART_OTHER = '#A1A1AA';

    /* The raw {labels, series} a canvas was drawn from, so a type
       switch re-styles the numbers instead of re-styling the styling.
       Kept off the DOM: Chart.js owns the canvas, and a drawn chart
       cannot be read back once it has been destroyed. */
    VL.rawChartData = new WeakMap();

    VL.chartPalette = function (el) {
        var scope = (el && el.closest && el.closest('.vl')) ||
                    document.querySelector('.vl');
        var css = scope ? getComputedStyle(scope) : null;
        var palette = VL.CHART_SERIES.map(function (fallback, i) {
            var value = css ? (css.getPropertyValue('--vl-cat-' + (i + 1)) || '').trim() : '';
            return value || fallback;
        });
        palette.other = (css && (css.getPropertyValue('--vl-cat-other') || '').trim()) ||
                        VL.CHART_OTHER;
        return palette;
    };

    function numeric(value) {
        var n = typeof value === 'number' ? value : parseFloat(value);
        return isNaN(n) ? 0 : n;
    }

    /* A doughnut is one series split by category, so its colour says
       *which vendor* — one hue per slice, in slot order.

       Past eight slices the tail folds into a single neutral "Other"
       rather than cycling back to slot 1, which would paint two
       vendors identically and quietly break the legend. Slices are
       ordered largest first, so the fold takes the slivers and not
       whichever rows happened to sort last in the table. */
    function foldForShare(labels, values, limit) {
        var rows = labels.map(function (label, i) {
            return { label: label, value: numeric(values[i]) };
        }).sort(function (a, b) { return b.value - a.value; });

        if (rows.length <= limit) {
            return {
                labels: rows.map(function (r) { return r.label; }),
                values: rows.map(function (r) { return r.value; }),
                folded: 0
            };
        }

        var kept = rows.slice(0, limit - 1);
        var tail = rows.slice(limit - 1);
        var rest = tail.reduce(function (sum, r) { return sum + r.value; }, 0);
        return {
            labels: kept.map(function (r) { return r.label; })
                        .concat(['Other (' + tail.length + ')']),
            values: kept.map(function (r) { return r.value; }).concat([rest]),
            folded: tail.length
        };
    }

    /* raw: { labels: [...], series: [{ name, data }], unit }
       Returns a complete Chart.js config for `type`. */
    VL.chartConfig = function (type, raw, el) {
        var palette = VL.chartPalette(el);
        var doughnut = type === 'doughnut';
        var labels = (raw.labels || []).slice();
        var series = raw.series || [];
        var datasets;

        if (doughnut) {
            var first = series[0] || { data: [] };
            var share = foldForShare(labels, first.data || [], palette.length);
            labels = share.labels;
            datasets = [{
                label: first.name || '',
                data: share.values,
                backgroundColor: share.labels.map(function (label, i) {
                    return share.folded && i === share.labels.length - 1
                        ? palette.other : palette[i];
                }),
                /* A hairline of surface between slices: adjacent hues
                   need the gap to read as two slices, not one shape. */
                borderColor: '#FFFFFF',
                borderWidth: 2
            }];
        } else if (type === 'line') {
            datasets = series.map(function (s, i) {
                var colour = palette[i % palette.length];
                return {
                    label: s.name || '',
                    data: s.data || [],
                    borderColor: colour,
                    backgroundColor: colour,
                    pointBackgroundColor: colour,
                    pointBorderColor: '#FFFFFF',
                    pointBorderWidth: 1,
                    /* The bug the "Line" button used to have: a stroke
                       width of 0 draws the points and no line. */
                    borderWidth: 2,
                    pointRadius: 3,
                    pointHoverRadius: 6,
                    tension: 0.3,
                    fill: false,
                    /* A missing value breaks the run in two rather than
                       drawing a straight line through the gap. */
                    spanGaps: false
                };
            });
        } else {
            datasets = series.map(function (s, i) {
                return {
                    label: s.name || '',
                    data: s.data || [],
                    backgroundColor: palette[i % palette.length],
                    borderWidth: 0,
                    borderRadius: 4,
                    maxBarThickness: 36
                };
            });
        }

        var unit = raw.unit || '';
        var options = {
            responsive: true,
            maintainAspectRatio: false,
            plugins: {
                legend: {
                    /* A doughnut's identity is carried entirely by its
                       legend, so it always has one; a single-series bar
                       or line is named by the report's own title. */
                    display: doughnut || datasets.length > 1,
                    position: 'bottom',
                    labels: { boxWidth: 10, font: { size: 10 } }
                },
                tooltip: {
                    callbacks: {
                        label: function (ctx) {
                            var v = doughnut ? ctx.parsed : ctx.parsed.y;
                            return ' ' + (unit ? unit + ' ' : '') + v;
                        }
                    }
                }
            }
        };

        /* Chart.js warns and draws nothing if a scales block is left on
           a doughnut, which has no axes. */
        if (type !== 'doughnut') {
            options.scales = {
                y: {
                    beginAtZero: true,
                    title: { display: !!unit, text: unit, font: { size: 10 } },
                    ticks: { font: { size: 10 } },
                    grid: { color: 'rgba(9,9,11,.06)' }
                },
                x: {
                    ticks: { font: { size: 10 }, maxRotation: 40, minRotation: 0 },
                    grid: { display: false }
                }
            };
        }

        return { type: type, data: { labels: labels, datasets: datasets }, options: options };
    };

    VL.drawChart = function (canvas, chart) {
        var raw = {
            labels: (chart.labels || []).slice(),
            series: (chart.series || []).map(function (s) {
                return { name: s.name || '', data: (s.data || []).slice() };
            }),
            unit: chart.unit || ''
        };
        VL.rawChartData.set(canvas, raw);
        return new window.Chart(canvas, VL.chartConfig(chart.type || 'bar', raw, canvas));
    };

    VL.reportToCsv = function (report) {
        function cell(value) {
            return '"' + String(value == null ? '' : value).replace(/"/g, '""') + '"';
        }
        var lines = [];
        if (report.title) { lines.push(cell(report.title)); }
        (report.kpis || []).forEach(function (k) {
            lines.push(cell(k.label) + ',' + cell(k.value));
        });
        if (report.table && report.table.columns) {
            lines.push('');
            lines.push(report.table.columns.map(cell).join(','));
            (report.table.rows || []).forEach(function (row) {
                lines.push((row || []).map(cell).join(','));
            });
        }
        var blob = new Blob([lines.join('\n')], { type: 'text/csv;charset=utf-8;' });
        var a = document.createElement('a');
        a.href = URL.createObjectURL(blob);
        a.download = 'merilgpt-report.csv';
        document.body.appendChild(a);
        a.click();
        URL.revokeObjectURL(a.href);
        a.remove();
        VL.toast('success', 'CSV downloaded');
    };

    /* ── ChatPanel ────────────────────────────────────────────────
       Owns the drawer, the message log and the composer. The caller
       supplies `send`, which returns a promise and may stream by
       calling the `onToken` callback it is handed — so this works
       whether the backend streams or answers in one shot. */
    VL.ChatPanel = function (root, options) {
        var opts = options || {};
        var log = root.querySelector('.vl-chat__log');
        var form = root.querySelector('.vl-chat__composer');
        var input = root.querySelector('textarea');
        var suggestWrap = root.querySelector('.vl-chat__suggest');
        var busy = false;

        function stamp() {
            return new Date().toLocaleTimeString([], { hour: '2-digit', minute: '2-digit', hour12: false });
        }

        function addMessage(role, text, report) {
            if (suggestWrap) { suggestWrap.hidden = true; }
            var wrap = document.createElement('div');
            wrap.className = 'vl-msg vl-msg--' + role;

            var bubble = document.createElement('div');
            bubble.className = 'vl-msg__bubble';
            bubble.textContent = text || '';
            wrap.appendChild(bubble);

            // The report sits between the prose and the timestamp: the answer
            // reads first, the visuals second, and the meta stays last.
            if (report) {
                var card = VL.renderReport(report);
                if (card) { wrap.appendChild(card); }
            }

            var time = document.createElement('div');
            time.className = 'vl-msg__time';
            time.textContent = stamp();
            wrap.appendChild(time);

            if (role === 'bot') {
                var acts = document.createElement('div');
                acts.className = 'vl-msg__acts';

                var copy = document.createElement('button');
                copy.type = 'button';
                copy.className = 'vl-btn vl-btn--ghost vl-btn--sm';
                copy.textContent = 'Copy';
                copy.addEventListener('click', function () {
                    navigator.clipboard.writeText(bubble.textContent).then(function () {
                        VL.toast('success', 'Copied');
                    }, function () {
                        VL.toast('error', 'Could not copy');
                    });
                });
                acts.appendChild(copy);

                if (opts.onRegenerate) {
                    var again = document.createElement('button');
                    again.type = 'button';
                    again.className = 'vl-btn vl-btn--ghost vl-btn--sm';
                    again.textContent = 'Regenerate';
                    again.addEventListener('click', function () { opts.onRegenerate(wrap); });
                    acts.appendChild(again);
                }
                ['Helpful', 'Not helpful'].forEach(function (label) {
                    var b = document.createElement('button');
                    b.type = 'button';
                    b.className = 'vl-btn vl-btn--ghost vl-btn--sm';
                    b.textContent = label;
                    b.addEventListener('click', function () {
                        b.setAttribute('aria-pressed', 'true');
                        VL.toast('info', 'Thanks — noted.');
                    });
                    acts.appendChild(b);
                });
                wrap.appendChild(acts);
            }

            log.appendChild(wrap);
            log.scrollTop = log.scrollHeight;
            return bubble;
        }

        function ask(text) {
            if (busy || !text.trim()) { return; }
            busy = true;
            addMessage('user', text.trim());
            input.value = '';

            var bubble = addMessage('bot', '');

            /* The robot loader, not just a blinking caret: this waits on an
               OpenAI call that routinely takes several seconds, and a lone
               caret reads as a stalled bubble rather than work in progress.
               Cloned from the same <template> the overlay uses, so there is
               one definition of the scene; if the page did not include the
               loader assets we fall back to the caret rather than showing
               nothing. */
            var waiting = null;
            var template = document.getElementById('vlxAiLoaderTemplate');
            if (template && template.content) {
                waiting = template.content.firstElementChild.cloneNode(true);
                waiting.classList.add('vlx-ailoader--bubble');
                var line = waiting.querySelector('.vlx-ailoader__msg');
                if (line) { line.textContent = 'Reading the bids…'; }
                bubble.appendChild(waiting);
            }

            var caret = document.createElement('span');
            caret.className = 'vl-caret';
            if (!waiting) { bubble.appendChild(caret); }

            function clearWaiting() {
                if (waiting && waiting.parentNode) { waiting.parentNode.removeChild(waiting); }
                waiting = null;
            }

            function onToken(chunk) {
                /* First token means the answer has started: the loader has
                   done its job and the text takes over. */
                clearWaiting();
                caret.remove();
                bubble.textContent += chunk;
                bubble.appendChild(caret);
                log.scrollTop = log.scrollHeight;
            }

            Promise.resolve(opts.send(text.trim(), onToken))
                .then(function (whole) {
                    clearWaiting();
                    caret.remove();
                    // send() may resolve with a plain string (streaming already
                    // filled the bubble) or with {answer, report} from a
                    // one-shot backend.
                    var answer = whole, report = null;
                    if (whole && typeof whole === 'object') {
                        answer = whole.answer;
                        report = whole.report;
                    }
                    if (typeof answer === 'string' && answer && !bubble.textContent) {
                        bubble.textContent = answer;
                    }
                    if (!bubble.textContent) { bubble.textContent = 'No answer came back.'; }
                    if (report) {
                        var card = VL.renderReport(report);
                        if (card) { bubble.parentNode.insertBefore(card, bubble.nextSibling); }
                    }
                })
                .catch(function (err) {
                    clearWaiting();
                    caret.remove();
                    bubble.textContent = (err && err.message) || 'Could not reach MerilGPT.';
                })
                .finally(function () {
                    busy = false;
                    log.scrollTop = log.scrollHeight;
                });
        }

        if (form) {
            form.addEventListener('submit', function (e) { e.preventDefault(); ask(input.value); });
        }
        if (input) {
            // Enter sends, Shift+Enter newlines — the convention every
            // chat UI uses, so nobody has to be told.
            input.addEventListener('keydown', function (e) {
                if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); ask(input.value); }
            });
        }
        if (suggestWrap) {
            suggestWrap.addEventListener('click', function (e) {
                var b = e.target.closest('button');
                if (b) { ask(b.dataset.prompt || b.textContent); }
            });
        }

        return {
            open: function (prefill) {
                root.classList.add('is-open');
                root.setAttribute('aria-hidden', 'false');
                if (prefill && input) { input.value = prefill; }
                if (input) { window.setTimeout(function () { input.focus(); }, 160); }
            },
            close: function () {
                root.classList.remove('is-open');
                root.setAttribute('aria-hidden', 'true');
            },
            ask: ask,
            addMessage: addMessage,
            clear: function () {
                log.innerHTML = '';
                if (suggestWrap) { suggestWrap.hidden = false; }
            }
        };
    };

    document.addEventListener('DOMContentLoaded', function () { VL.initSegments(document); });

    window.VL = VL;
}());
