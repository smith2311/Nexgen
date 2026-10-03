/* Extra artifact controls for the MerilGPT report cards.
 *
 * VL.renderReport already draws the summary, KPIs, chart, table, highlights
 * and a CSV button. This adds the things people actually do with a result once
 * they have it, which the card had no answer for:
 *
 *   Copy table   -> tab-separated to the clipboard, which pastes straight into
 *                   Excel as cells. CSV-as-a-download is the wrong shape for
 *                   "put this in the deck I am writing now".
 *   Sort columns -> click a header. A vendor comparison is read by ranking it,
 *                   and re-asking the model to sort is a round trip for
 *                   something the browser can do instantly.
 *   Chart type   -> the same numbers as bars, a line or a doughnut. Which one
 *                   reads best depends on the question, and the model picks
 *                   once, in advance, without seeing the result.
 *   Expand       -> the card at full width. Even a widened drawer is narrow
 *                   for a ten-column table.
 *   Print        -> just the card, for pasting into minutes or a mail.
 *
 * Attached by delegation from the document, so it applies to cards the chat
 * appends later — a per-card wiring pass would miss every answer after the
 * first. Everything degrades: a card whose report has no table simply has no
 * table controls.
 */
(function () {
    'use strict';

    /* ── Helpers ─────────────────────────────────────────────────────────── */

    function tableToRows(table) {
        var rows = [];
        table.querySelectorAll('thead tr').forEach(function (tr) {
            rows.push(Array.prototype.map.call(tr.children, function (c) {
                return c.textContent.trim();
            }));
        });
        table.querySelectorAll('tbody tr').forEach(function (tr) {
            rows.push(Array.prototype.map.call(tr.children, function (c) {
                return c.textContent.trim();
            }));
        });
        return rows;
    }

    function copyText(text, button) {
        function done(ok) {
            var original = button.dataset.vlLabel || button.textContent;
            button.dataset.vlLabel = original;
            button.textContent = ok ? 'Copied' : 'Copy failed';
            setTimeout(function () { button.textContent = original; }, 1600);
        }
        if (navigator.clipboard && navigator.clipboard.writeText) {
            navigator.clipboard.writeText(text).then(function () { done(true); },
                                                     function () { fallback(); });
        } else {
            fallback();
        }
        function fallback() {
            /* execCommand is deprecated but is the only path on a page served
               over plain http, where navigator.clipboard is undefined — which
               is how this runs on the office network. */
            var area = document.createElement('textarea');
            area.value = text;
            area.setAttribute('readonly', '');
            area.style.cssText = 'position:fixed;top:-1000px;opacity:0;';
            document.body.appendChild(area);
            area.select();
            var ok = false;
            try { ok = document.execCommand('copy'); } catch (e) { ok = false; }
            document.body.removeChild(area);
            done(ok);
        }
    }

    /* ── Sortable table ──────────────────────────────────────────────────── */

    /* Numbers sort as numbers, not as strings.
     *
     * Every cell in a report table is a formatted string ("EUR 28,500",
     * "4.6", "12%"), so a plain string sort puts EUR 9,000 above EUR 28,500.
     * The currency, thousands separators and unit are stripped before the
     * comparison; anything that is not then a number falls back to a
     * locale-aware string compare. */
    function sortKey(text) {
        var cleaned = text.replace(/[^0-9.,\-]/g, '').replace(/,/g, '');
        if (cleaned === '' || cleaned === '-' || cleaned === '.') { return null; }
        var value = parseFloat(cleaned);
        return isNaN(value) ? null : value;
    }

    function sortTable(table, index, ascending) {
        var tbody = table.querySelector('tbody');
        if (!tbody) { return; }
        var rows = Array.prototype.slice.call(tbody.querySelectorAll('tr'));

        rows.sort(function (a, b) {
            var left = (a.children[index] || {}).textContent || '';
            var right = (b.children[index] || {}).textContent || '';
            var ln = sortKey(left), rn = sortKey(right);
            var result;
            if (ln !== null && rn !== null) {
                result = ln - rn;
            } else if (ln !== null) {
                result = -1;          // numbers before text
            } else if (rn !== null) {
                result = 1;
            } else {
                result = left.trim().localeCompare(right.trim(), undefined, { numeric: true });
            }
            return ascending ? result : -result;
        });

        rows.forEach(function (row) { tbody.appendChild(row); });
    }

    function makeSortable(table) {
        if (table.dataset.vlSortable === '1') { return; }
        table.dataset.vlSortable = '1';
        var headers = table.querySelectorAll('thead th');
        Array.prototype.forEach.call(headers, function (th, index) {
            th.classList.add('vl-art-sortable');
            th.tabIndex = 0;
            th.setAttribute('role', 'button');
            th.title = 'Sort by ' + th.textContent.trim();

            function toggle() {
                var ascending = th.dataset.vlDir !== 'asc';
                Array.prototype.forEach.call(headers, function (other) {
                    delete other.dataset.vlDir;
                    other.classList.remove('is-asc', 'is-desc');
                    other.removeAttribute('aria-sort');
                });
                th.dataset.vlDir = ascending ? 'asc' : 'desc';
                th.classList.add(ascending ? 'is-asc' : 'is-desc');
                th.setAttribute('aria-sort', ascending ? 'ascending' : 'descending');
                sortTable(table, index, ascending);
            }
            th.addEventListener('click', toggle);
            th.addEventListener('keydown', function (ev) {
                if (ev.key === 'Enter' || ev.key === ' ') { ev.preventDefault(); toggle(); }
            });
        });
    }

    /* ── Chart type switcher ─────────────────────────────────────────────── */

    /* The chart's own data, kept off the DOM.
     *
     * Chart.js owns the canvas, so re-reading the drawn chart is not possible
     * once it is rendered; the numbers are stashed against the canvas when the
     * switcher is first attached and reused on every switch.
     *
     * What is stashed is the *numbers*, never the drawn datasets. Replaying a
     * drawn dataset was the bug behind two complaints about this toolbar:
     * "Share" inherited the bar chart's single fill, so a ten-vendor doughnut
     * came out ten slices of one indigo, and "Line" inherited its borderWidth
     * of 0, so the line chart drew its points and no line between them.
     * Colour and stroke belong to the chart type, so VL.chartConfig derives
     * them fresh for whichever type is being switched to. */
    var chartStore = new WeakMap();

    function switchChartType(canvas, type) {
        if (!window.Chart) { return; }
        var existing = window.Chart.getChart ? window.Chart.getChart(canvas) : null;

        /* VL.drawChart records what it drew from, which is the accurate
           source. Reading the live chart is the fallback for a card drawn by
           something else, and for a doughnut it is already folded and sorted —
           so the recorded copy is preferred whenever it exists. */
        var saved = chartStore.get(canvas) ||
                    (window.VL && VL.rawChartData && VL.rawChartData.get(canvas));
        if (!saved && existing) {
            saved = {
                labels: (existing.data.labels || []).slice(),
                series: existing.data.datasets.map(function (d) {
                    return { name: d.label || '', data: (d.data || []).slice() };
                }),
                unit: ''
            };
        }
        if (!saved) { return; }
        chartStore.set(canvas, saved);
        if (existing) { existing.destroy(); }

        if (window.VL && typeof VL.chartConfig === 'function') {
            new window.Chart(canvas, VL.chartConfig(type, saved, canvas));
            return;
        }

        /* vendorlink-components.js absent: draw something rather than nothing.
           A doughnut has no axes, and leaving a scales block on one makes
           Chart.js warn and draw nothing. */
        var options = { maintainAspectRatio: false, plugins: { legend: { display: type === 'doughnut' } } };
        if (type !== 'doughnut') {
            options.scales = { y: { beginAtZero: true } };
        }
        new window.Chart(canvas, {
            type: type,
            data: {
                labels: saved.labels,
                datasets: saved.series.map(function (s) {
                    return {
                        label: s.name, data: s.data,
                        borderWidth: type === 'line' ? 2 : 0, fill: false
                    };
                })
            },
            options: options
        });
    }

    /* ── Expand / print ──────────────────────────────────────────────────── */

    /* Where an expanded card came from, so it can go back exactly there. */
    var homes = new WeakMap();

    function expandCard(card, button) {
        var open = !card.classList.contains('vl-art-expanded');

        /* The card has to leave the drawer to be centred on the page.
         *
         * position:fixed resolves against the nearest ancestor carrying a
         * transform, and the drawer has one — translateX is how it slides in
         * and out. So left:50% inside it means 50% of the DRAWER, and the
         * expanded card was anchored to the panel and ran off the right edge
         * of the screen. Moving it to <body> for the duration is the only way
         * to make "fixed" mean the viewport.
         *
         * The original parent and next sibling are remembered rather than
         * assumed: the chat appends turns continuously, so "put it back at the
         * end" would reorder the thread. */
        if (open) {
            homes.set(card, { parent: card.parentNode, before: card.nextSibling });
            document.body.appendChild(card);
        } else {
            var home = homes.get(card);
            if (home && home.parent) {
                home.parent.insertBefore(card, home.before || null);
                homes.delete(card);
            }
        }

        card.classList.toggle('vl-art-expanded', open);
        button.textContent = open ? 'Close' : 'Expand';
        button.setAttribute('aria-expanded', String(open));
        /* The scrim is created with the first expand and reused; clicking it
           closes, which is the gesture people try before finding the button. */
        var scrim = document.querySelector('.vl-art-scrim');
        if (!scrim) {
            scrim = document.createElement('div');
            scrim.className = 'vl-art-scrim';
            document.body.appendChild(scrim);
            scrim.addEventListener('click', function () {
                var current = document.querySelector('.vl-art-expanded');
                if (current) {
                    var btn = current.querySelector('[data-vl-art="expand"]');
                    if (btn) { expandCard(current, btn); }
                }
            });
        }
        scrim.hidden = !open;
        document.body.style.overflow = open ? 'hidden' : '';
    }

    function printCard(card) {
        /* The card is cloned into a print-only container rather than opening a
           new window: a popup is blocked by default on this deployment, and
           printing the whole page would carry the drawer, the nav and the rest
           of the chat with it. */
        var holder = document.querySelector('.vl-art-printarea');
        if (!holder) {
            holder = document.createElement('div');
            holder.className = 'vl-art-printarea';
            document.body.appendChild(holder);
        }
        holder.innerHTML = '';
        var clone = card.cloneNode(true);
        clone.classList.remove('vl-art-expanded');
        /* Controls are not part of what anyone wants on paper. */
        clone.querySelectorAll('.vl-art-toolbar, .vl-report__actions').forEach(function (el) {
            el.remove();
        });
        holder.appendChild(clone);

        /* Chart.js draws to a canvas, and a cloned canvas is blank — the
           bitmap does not come with the node. The original is copied across as
           an image so the printed page keeps its chart. */
        var sourceCanvases = card.querySelectorAll('canvas');
        var clonedCanvases = holder.querySelectorAll('canvas');
        Array.prototype.forEach.call(clonedCanvases, function (target, index) {
            var source = sourceCanvases[index];
            if (!source) { return; }
            try {
                var img = document.createElement('img');
                img.src = source.toDataURL('image/png');
                img.style.cssText = 'width:100%;height:auto;';
                target.parentNode.replaceChild(img, target);
            } catch (e) {
                target.remove();
            }
        });

        document.body.classList.add('vl-art-printing');
        window.print();
        /* after-print rather than a timeout: the dialog is modal and its
           duration is the user's, not ours. */
        function cleanup() {
            document.body.classList.remove('vl-art-printing');
            holder.innerHTML = '';
            window.removeEventListener('afterprint', cleanup);
        }
        window.addEventListener('afterprint', cleanup);
    }

    /* ── Toolbar ─────────────────────────────────────────────────────────── */

    function buildToolbar(card) {
        if (card.dataset.vlArt === '1') { return; }
        card.dataset.vlArt = '1';

        var table = card.querySelector('.vl-report__table');
        var canvas = card.querySelector('.vl-report__chart canvas');

        var bar = document.createElement('div');
        bar.className = 'vl-art-toolbar';

        function addButton(action, label, title) {
            var button = document.createElement('button');
            button.type = 'button';
            button.className = 'vl-art-btn';
            button.dataset.vlArt = action;
            button.textContent = label;
            button.title = title;
            bar.appendChild(button);
            return button;
        }

        if (table) {
            addButton('copy-table', 'Copy table', 'Copy as cells — pastes into Excel');
            makeSortable(table);
        }
        if (canvas && window.Chart) {
            var group = document.createElement('span');
            group.className = 'vl-art-chartswitch';
            [['bar', 'Bars'], ['line', 'Line'], ['doughnut', 'Share']].forEach(function (pair) {
                var button = document.createElement('button');
                button.type = 'button';
                button.className = 'vl-art-btn vl-art-btn--chip';
                button.dataset.vlArt = 'chart-' + pair[0];
                button.textContent = pair[1];
                button.title = 'Show this chart as ' + pair[1].toLowerCase();
                group.appendChild(button);
            });
            bar.appendChild(group);
        }
        addButton('copy-answer', 'Copy text', 'Copy the written answer');
        addButton('expand', 'Expand', 'Show this at full width');
        addButton('print', 'Print', 'Print just this result');

        /* Below the head, above the body: a toolbar under the whole card reads
           as belonging to the page rather than to this result. */
        var body = card.querySelector('.vl-report__body') || card;
        card.insertBefore(bar, body);
    }

    /* Cards arrive whenever the chat answers, so they are picked up on a
       mutation rather than once at load. */
    function scan(root) {
        (root || document).querySelectorAll('.vl-report').forEach(buildToolbar);
    }

    if (window.MutationObserver) {
        new MutationObserver(function (records) {
            records.forEach(function (record) {
                Array.prototype.forEach.call(record.addedNodes, function (node) {
                    if (node.nodeType !== 1) { return; }
                    if (node.classList && node.classList.contains('vl-report')) {
                        buildToolbar(node);
                    }
                    scan(node);
                });
            });
        }).observe(document.body, { childList: true, subtree: true });
    }
    document.addEventListener('DOMContentLoaded', function () { scan(document); });
    scan(document);

    /* ── One delegated click handler ─────────────────────────────────────── */

    document.addEventListener('click', function (ev) {
        var button = ev.target.closest ? ev.target.closest('[data-vl-art]') : null;
        if (!button || button.tagName !== 'BUTTON') { return; }
        var card = button.closest('.vl-report');
        if (!card) { return; }
        var action = button.dataset.vlArt;

        if (action === 'copy-table') {
            var table = card.querySelector('.vl-report__table');
            if (!table) { return; }
            /* Tab-separated, not comma: Excel splits tabs into cells on a
               plain paste, while a CSV string lands in one cell. */
            var text = tableToRows(table).map(function (row) {
                return row.join('\t');
            }).join('\n');
            copyText(text, button);
        } else if (action === 'copy-answer') {
            /* The bubble's text without the card, since the table and chart
               have their own copy path and their text would arrive mangled. */
            var bubble = card.closest('.vlx-gpt-bubble');
            var text2 = '';
            if (bubble) {
                var clone = bubble.cloneNode(true);
                clone.querySelectorAll('.vl-report, .vlx-gpt-sources').forEach(function (el) {
                    el.remove();
                });
                text2 = clone.innerText.trim();
            }
            copyText(text2 || card.innerText.trim(), button);
        } else if (action && action.indexOf('chart-') === 0) {
            var canvas = card.querySelector('.vl-report__chart canvas');
            if (!canvas) { return; }
            switchChartType(canvas, action.slice('chart-'.length));
            card.querySelectorAll('.vl-art-chartswitch .vl-art-btn').forEach(function (chip) {
                chip.classList.toggle('is-active', chip === button);
            });
        } else if (action === 'expand') {
            expandCard(card, button);
        } else if (action === 'print') {
            printCard(card);
        }
    });

    /* Escape closes an expanded card, matching every other overlay here. */
    document.addEventListener('keydown', function (ev) {
        if (ev.key !== 'Escape') { return; }
        var current = document.querySelector('.vl-art-expanded');
        if (!current) { return; }
        var button = current.querySelector('[data-vl-art="expand"]');
        if (button) { expandCard(current, button); }
    });
}());
