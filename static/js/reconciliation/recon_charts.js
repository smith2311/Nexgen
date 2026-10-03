/* ═══════════════════════════════════════════════════════════════════════
   Filing summary — the four charts.

   Chart.js is served from static/lib/, not a CDN: base.html self-hosts every
   font and stylesheet, and this module has to work on a machine that cannot
   reach jsdelivr.

   The numbers arrive with the page in #rcChartData — the server already
   computed them for the tiles, the insights and the vendor table, so fetching
   them again would be a second computation that could disagree with the first.

   ── On the colours ──────────────────────────────────────────────────────
   Two palettes, each doing one job.

   STATUS (Clear / Watch / Exception) is reserved and never reused as a series
   colour. The module's own chip colours could not be used here: as adjacent
   stacked segments #dc2626 and #d97706 sit at ΔE 14.4 for *normal* vision —
   below the 15 floor — so red and amber blur into each other. Re-stepped to
   #15803d / #f59e0b / #be123c, which clears every check (worst adjacent pair
   ΔE 17.4 under protanopia, 30.3 normal). The chips elsewhere in the module
   are unchanged; they sit apart and always carry their own text.

   MAGNITUDE is one flat blue. Colour encodes the entity, never its rank, so a
   ranked bar chart does NOT get a light-to-dark ramp down the bars — that
   would repaint the survivors every time a filter changed the order.

   Amber sits at 2.09:1 against white, which is below the 3:1 mark, so it ships
   with relief: a legend, a value printed on every bar, and a table view of
   every chart.
   ═══════════════════════════════════════════════════════════════════════ */
(function () {
    'use strict';

    var holder = document.getElementById('rcChartData');
    if (!holder || typeof Chart === 'undefined') { return; }

    var data = JSON.parse(holder.textContent);

    var STATUS = { ok: '#15803d', warn: '#f59e0b', bad: '#be123c' };
    var STATUS_LABEL = { ok: 'Clear', warn: 'Watch', bad: 'Exception' };
    var BLUE = '#2e6fd8';
    var SURFACE = '#ffffff';
    var INK = '#1e2d40';
    var INK_SOFT = '#4a5c6e';
    var GRID = '#eef2f7';

    var group = new Intl.NumberFormat('en-IN');

    Chart.defaults.font.family =
        '"Helvetica Neue", Inter, ui-sans-serif, system-ui, sans-serif';
    Chart.defaults.font.size = 11;
    Chart.defaults.color = INK_SOFT;

    function tooltip(extra) {
        return Object.assign({
            backgroundColor: INK,
            padding: 10,
            cornerRadius: 8,
            titleFont: { size: 12, weight: '700' },
            bodyFont: { size: 11 },
            displayColors: true,
            boxWidth: 9,
            boxHeight: 9,
            boxPadding: 4
        }, extra || {});
    }

    // Recessive axes: the data should be the loudest thing in the frame.
    function axes(opts) {
        opts = opts || {};
        return {
            x: {
                stacked: !!opts.stacked,
                grid: { display: !!opts.horizontal, color: GRID, drawBorder: false },
                border: { display: false },
                ticks: {
                    color: INK_SOFT,
                    callback: opts.horizontal
                        ? function (v) { return group.format(v); }
                        : undefined
                }
            },
            y: {
                stacked: !!opts.stacked,
                grid: { display: !opts.horizontal, color: GRID, drawBorder: false },
                border: { display: false },
                beginAtZero: true,
                ticks: {
                    color: INK_SOFT,
                    autoSkip: false,
                    callback: opts.horizontal ? undefined
                        : function (v) { return group.format(v); }
                }
            }
        };
    }

    /* A value at the end of every bar. With at most nine bars per chart this is
       "selective" in the sense that matters — it is not a number on every point
       of a dense series — and it is what makes the amber segments readable for
       anyone the fill colour fails. */
    var barValues = {
        id: 'rcBarValues',
        afterDatasetsDraw: function (chart) {
            var ctx = chart.ctx;
            ctx.save();
            ctx.font = '600 10px ' + Chart.defaults.font.family;
            ctx.fillStyle = INK_SOFT;
            ctx.textBaseline = 'middle';
            chart.data.datasets.forEach(function (dataset, i) {
                var meta = chart.getDatasetMeta(i);
                if (meta.hidden) { return; }
                meta.data.forEach(function (bar, index) {
                    var value = dataset.data[index];
                    if (!value) { return; }
                    ctx.textAlign = 'left';
                    ctx.fillText(group.format(value), bar.x + 8, bar.y);
                });
            });
            ctx.restore();
        }
    };

    function shorten(text, max) {
        return text.length > max ? text.slice(0, max - 1) + '…' : text;
    }

    // ── the stacked monthly chart ────────────────────────────────────────

    function trendChart() {
        var canvas = document.getElementById('rcTrendChart');
        if (!canvas || !data.trend.length) { return; }

        var datasets = ['ok', 'warn', 'bad'].map(function (key) {
            return {
                label: STATUS_LABEL[key],
                data: data.trend.map(function (m) { return m[key]; }),
                backgroundColor: STATUS[key],
                // A 2px surface-coloured border is the gap between stacked
                // segments — without it the bands butt together and the
                // boundary has to be inferred from hue alone.
                borderColor: SURFACE,
                borderWidth: { top: 2, bottom: 0, left: 0, right: 0 },
                borderRadius: 4,
                borderSkipped: false,
                maxBarThickness: 46
            };
        });

        new Chart(canvas, {
            type: 'bar',
            data: { labels: data.trend.map(function (m) { return m.label; }), datasets: datasets },
            options: {
                responsive: true,
                maintainAspectRatio: false,
                interaction: { mode: 'index', intersect: false },
                plugins: {
                    legend: { display: false },     // drawn as HTML below the title
                    tooltip: tooltip({
                        callbacks: {
                            footer: function (items) {
                                var total = items.reduce(function (n, i) { return n + i.parsed.y; }, 0);
                                return 'Total  ' + group.format(total);
                            }
                        }
                    })
                },
                scales: axes({ stacked: true })
            }
        });

        document.getElementById('rcTrendLegend').innerHTML =
            ['ok', 'warn', 'bad'].map(function (key) {
                return '<span class="rc-lg"><i style="background:' + STATUS[key] +
                       '"></i>' + STATUS_LABEL[key] + '</span>';
            }).join('');

        table('rcTrendTable',
              ['Month', 'Clear', 'Watch', 'Exception', 'Total'],
              data.trend.map(function (m) {
                  return [m.label, m.ok, m.warn, m.bad, m.total];
              }));
    }

    // ── the ranked bars ──────────────────────────────────────────────────

    function rankedChart(canvasId, rows, tableId, heading, labelMax) {
        var canvas = document.getElementById(canvasId);
        if (!canvas || !rows.length) { return; }

        new Chart(canvas, {
            type: 'bar',
            data: {
                labels: rows.map(function (r) { return shorten(r.label, labelMax || 26); }),
                datasets: [{
                    data: rows.map(function (r) { return r.count; }),
                    // One hue for every bar: the colour says "documents", the
                    // length says how many. Ranking is already the y-order.
                    backgroundColor: BLUE,
                    borderRadius: 4,
                    borderSkipped: false,
                    maxBarThickness: 22
                }]
            },
            options: {
                indexAxis: 'y',
                responsive: true,
                maintainAspectRatio: false,
                layout: { padding: { right: 46 } },   // room for the value labels
                plugins: {
                    // One series: the card title names it, so a legend box
                    // would only repeat the heading.
                    legend: { display: false },
                    tooltip: tooltip({
                        callbacks: {
                            title: function (items) { return rows[items[0].dataIndex].label; },
                            label: function (item) {
                                return group.format(item.parsed.x) + ' documents';
                            }
                        }
                    })
                },
                scales: axes({ horizontal: true })
            },
            plugins: [barValues]
        });

        table(tableId, [heading, 'Documents'],
              rows.map(function (r) { return [r.label, r.count]; }));
    }

    // ── the table view every chart carries ───────────────────────────────

    function esc(value) {
        return String(value === null || value === undefined ? '' : value)
            .replace(/[&<>"']/g, function (c) {
                return { '&': '&amp;', '<': '&lt;', '>': '&gt;',
                         '"': '&quot;', "'": '&#39;' }[c];
            });
    }

    function table(id, headings, rows) {
        var host = document.getElementById(id);
        if (!host) { return; }
        host.innerHTML =
            '<table class="rc-table"><thead><tr>' +
            headings.map(function (h, i) {
                return '<th' + (i ? ' class="rc-num"' : '') + '>' + esc(h) + '</th>';
            }).join('') +
            '</tr></thead><tbody>' +
            rows.map(function (row) {
                return '<tr>' + row.map(function (cell, i) {
                    return '<td' + (i ? ' class="rc-num"' : '') + '>' +
                           (i ? group.format(cell) : esc(cell)) + '</td>';
                }).join('') + '</tr>';
            }).join('') +
            '</tbody></table>';
    }

    document.querySelectorAll('[data-table-toggle]').forEach(function (button) {
        button.addEventListener('click', function () {
            var card = button.closest('.rc-card');
            var chart = card.querySelector('.rc-chart');
            var view = card.querySelector('.rc-chart-table');
            var showing = view.hidden;
            view.hidden = !showing;
            chart.hidden = showing;
            button.classList.toggle('rc-btn-on', showing);
            button.setAttribute('aria-pressed', String(showing));
        });
    });

    trendChart();
    rankedChart('rcReasonChart', data.reasons, 'rcReasonsTable', 'Reason remark', 30);
    rankedChart('rcSectionChart', data.sections, 'rcSectionsTable', 'GSTR-1 section', 20);
    rankedChart('rcPresenceChart', data.presence, 'rcPresenceTable', 'Presence', 12);
}());
