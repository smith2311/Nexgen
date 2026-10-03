/* ============================================================================
 * Meril-V — Reports → User Activity charts
 *
 * Highcharts builders shared by the all-users dashboard and the per-user
 * drill-down. Both screens draw the same *kinds* of chart, so the config lives
 * here once rather than being copied into two inline <script> blocks that then
 * drift apart on colours, tooltips and axis formatting.
 *
 * Highcharts is vendored at static/lib/highcharts/ (no CDN — the platform must
 * not fetch scripts at runtime) and is already used by the billing and
 * material-rework dashboards. The theme below deliberately mirrors those, so
 * these screens read as part of the same product.
 *
 * Every series is prepared server-side in merilv/useractivity.py and arrives as
 * one JSON blob. Nothing here computes a metric — it only draws one, so a
 * number can never differ between the chart, the table and the Excel export.
 * ========================================================================= */
(function () {
    'use strict';

    /* Meril-V design tokens, restated as literals. Highcharts takes colours as
     * data and cannot resolve a CSS custom property, so these have to be spelled
     * out — they match --mv-fg-2 / --mv-fg-3 / --mv-border in tokens.css. */
    var INK = '#1f2937';
    var MUTED = '#6b7280';
    var FAINT = '#9ca3af';
    var HAIRLINE = '#e5e7eb';
    var GRID = '#eef2f7';
    var PRIMARY = '#0076C8';

    var isPhone = window.matchMedia('(max-width: 640px)').matches;
    var H = isPhone ? 240 : 300;
    var H_TALL = isPhone ? 320 : 400;

    function applyTheme() {
        if (!window.Highcharts) { return; }
        Highcharts.setOptions({
            credits: { enabled: false },
            lang: { thousandsSep: ',' },
            chart: {
                style: { fontFamily: 'inherit' },
                backgroundColor: 'transparent',
                spacing: [8, 4, 8, 4]
            },
            title: { text: null },
            tooltip: {
                backgroundColor: 'rgba(255,255,255,.97)',
                borderColor: HAIRLINE, borderRadius: 10, borderWidth: 1,
                shadow: { color: 'rgba(16,24,40,.18)', offsetX: 0, offsetY: 3, opacity: .1, width: 6 },
                style: { color: INK, fontSize: '12px' }
            },
            xAxis: {
                lineColor: HAIRLINE, tickColor: HAIRLINE,
                labels: { style: { color: MUTED, fontSize: '11px' } }
            },
            yAxis: {
                gridLineColor: GRID, gridLineDashStyle: 'Dash',
                labels: { style: { color: FAINT, fontSize: '11px' } },
                title: { style: { color: FAINT, fontSize: '11px' } }
            },
            plotOptions: { series: { animation: { duration: 500 }, borderRadius: 3 } }
        });
    }

    /* Read a `{{ x|json_script:"id" }}` block. Returns null rather than throwing
     * if the element is absent or unparseable, so one bad payload cannot take
     * the whole page's JS down with it. */
    function readPayload(id) {
        var el = document.getElementById(id);
        if (!el) { return null; }
        try {
            return JSON.parse(el.textContent);
        } catch (e) {
            return null;
        }
    }

    function el(id) { return document.getElementById(id); }

    /* Stacked column trend — actions per day, split by work type.
     *
     * Columns rather than an area: the data is a count of discrete events per
     * day, and an area spline interpolates between days, implying activity at
     * times when there was none. */
    function stackedTrend(id, trend, opts) {
        if (!el(id) || !trend || !trend.series.length) { return; }
        opts = opts || {};
        var series = trend.series.map(function (s) {
            return { name: s.name, color: s.color, data: s.data, type: 'column' };
        });

        // The individual view adds a cumulative line on a second axis, which is
        // what turns "busy days" into "is this person still working".
        if (opts.cumulative && trend.cumulative) {
            series.push({
                name: 'Cumulative', type: 'spline', yAxis: 1, color: '#8B5CF6',
                data: trend.cumulative, lineWidth: 2, marker: { enabled: false },
                dashStyle: 'ShortDot', enableMouseTracking: true
            });
        }

        var yAxes = [{
            title: { text: 'Actions' },
            stackLabels: {
                enabled: !isPhone,
                style: { fontWeight: '600', color: MUTED, textOutline: 'none', fontSize: '10px' }
            }
        }];
        if (opts.cumulative && trend.cumulative) {
            yAxes.push({ title: { text: 'Cumulative' }, opposite: true, gridLineWidth: 0 });
        }

        // The user's own daily average, so a column reads as above or below
        // their norm without the reader doing arithmetic.
        if (opts.average && trend.average) {
            yAxes[0].plotLines = [{
                value: trend.average, color: '#EF4444', dashStyle: 'Dash', width: 1.5, zIndex: 4,
                label: {
                    text: 'avg ' + trend.average + '/day',
                    style: { color: '#EF4444', fontSize: '10px', fontWeight: '600' }
                }
            }];
        }

        Highcharts.chart(id, {
            chart: { type: 'column', height: opts.height || H_TALL, zoomType: 'x' },
            xAxis: {
                categories: trend.dates,
                // A 365-day window would otherwise print every date and turn the
                // axis into a grey smear.
                tickInterval: Math.ceil(trend.dates.length / (isPhone ? 5 : 14)) || 1,
                crosshair: true
            },
            yAxis: yAxes,
            legend: { enabled: true, itemStyle: { fontSize: '11px', color: MUTED } },
            tooltip: { shared: true, headerFormat: '<b>{point.key}</b><br/>' },
            plotOptions: {
                column: { stacking: 'normal', borderWidth: 0, pointPadding: 0.05, groupPadding: 0.08 }
            },
            series: series
        });
    }

    /* Horizontal ranked bar. `urls` (optional, parallel to values) makes each
     * bar a link — the drill-down the all-users view needs. */
    function rankedBar(id, names, values, opts) {
        if (!el(id) || !values || !values.length) { return; }
        opts = opts || {};
        var urls = opts.urls || null;
        // Grows with the list but capped, so a long roster cannot leave a
        // 900px hole beside a short chart in the same grid row.
        var height = Math.max(200, Math.min(520, names.length * (isPhone ? 24 : 30) + 60));

        Highcharts.chart(id, {
            chart: {
                type: 'bar', height: height,
                events: urls ? {
                    // Cursor on the plot area, not just the bar, so the whole
                    // row reads as clickable.
                    load: function () { this.container.style.cursor = 'default'; }
                } : undefined
            },
            xAxis: { categories: names, labels: { style: { fontSize: isPhone ? '10px' : '11px' } } },
            yAxis: { title: { text: 'Actions' } },
            legend: { enabled: false },
            tooltip: { pointFormat: '<b>{point.y}</b> actions' },
            plotOptions: {
                bar: {
                    colorByPoint: true,
                    colors: ['#0076C8', '#1183CE', '#2290D4', '#339DDA', '#44AAE0',
                             '#55B7E6', '#66C4EC', '#77D1F2', '#88DEF8', '#99EBFE',
                             '#A5EFFE', '#B1F3FE'],
                    borderWidth: 0,
                    dataLabels: {
                        enabled: true,
                        style: { fontWeight: '600', fontSize: '10px', textOutline: 'none', color: INK }
                    },
                    cursor: urls ? 'pointer' : 'default',
                    point: urls ? {
                        events: {
                            click: function () {
                                var href = urls[this.index];
                                if (href) { window.location.href = href; }
                            }
                        }
                    } : undefined
                }
            },
            series: [{ name: 'Actions', data: values }]
        });
    }

    /* Donut. Points arrive with their own colour so the slice matches the
     * legend dot beside the table on the same page. */
    function donut(id, points, opts) {
        if (!el(id) || !points || !points.length) { return; }
        opts = opts || {};
        Highcharts.chart(id, {
            chart: { type: 'pie', height: opts.height || H },
            plotOptions: {
                pie: {
                    innerSize: '62%', borderWidth: 3, borderColor: '#fff',
                    dataLabels: {
                        enabled: !isPhone,
                        format: '{point.name}<br><b>{point.y}</b>',
                        style: { fontWeight: '600', textOutline: 'none', color: MUTED, fontSize: '11px' },
                        distance: 12
                    },
                    showInLegend: isPhone
                }
            },
            legend: { enabled: isPhone, itemStyle: { fontSize: '11px' } },
            tooltip: { pointFormat: '<b>{point.y}</b> ({point.percentage:.1f}%)' },
            series: [{ name: 'Actions', data: points }]
        });
    }

    /* Hour-of-day histogram. Rendered in the server's timezone
     * (Asia/Kolkata) — see useractivity._hourly. */
    function hourColumn(id, hours, opts) {
        if (!el(id) || !hours || !hours.values.length) { return; }
        opts = opts || {};
        Highcharts.chart(id, {
            chart: { type: 'column', height: opts.height || H },
            xAxis: {
                categories: hours.labels,
                tickInterval: isPhone ? 4 : 2,
                labels: { style: { fontSize: '10px' } },
                title: { text: 'Hour of day (IST)', style: { color: FAINT, fontSize: '10px' } }
            },
            yAxis: { title: { text: 'Actions' } },
            legend: { enabled: false },
            tooltip: { pointFormat: '<b>{point.y}</b> actions' },
            plotOptions: {
                column: {
                    color: PRIMARY, borderWidth: 0, pointPadding: 0.04, groupPadding: 0.06,
                    // A shift boundary is easier to spot when the busiest hour
                    // is a different colour from its neighbours.
                    zones: opts.peak ? [{ value: opts.peak, color: PRIMARY }, { color: '#00A891' }] : undefined
                }
            },
            series: [{ name: 'Actions', data: hours.values }]
        });
    }

    /* Weekday pattern. Plots the per-occurrence average, not the sum: a 30-day
     * window can hold five Mondays and four Sundays, and a raw total would read
     * that calendar accident as a real preference. */
    function weekdayColumn(id, weekdays, opts) {
        if (!el(id) || !weekdays || !weekdays.length) { return; }
        opts = opts || {};
        Highcharts.chart(id, {
            chart: { type: 'column', height: opts.height || H },
            xAxis: { categories: weekdays.map(function (w) { return w.weekday; }) },
            yAxis: { title: { text: 'Avg actions / day' } },
            legend: { enabled: false },
            tooltip: {
                formatter: function () {
                    var row = weekdays[this.point.index];
                    return '<b>' + row.weekday + '</b><br>' + row.avg + ' avg · '
                        + row.total + ' total over ' + row.days + ' day(s)';
                }
            },
            plotOptions: {
                column: {
                    color: '#00A891', borderWidth: 0,
                    dataLabels: {
                        enabled: !isPhone,
                        style: { fontWeight: '600', fontSize: '10px', textOutline: 'none', color: INK }
                    }
                }
            },
            series: [{ name: 'Avg', data: weekdays.map(function (w) { return w.avg; }) }]
        });
    }

    /* Minutes of engagement window per day — the "how much time" trend.
     * A spline is legitimate here: unlike an event count, a daily span is a
     * continuous measure and reading a slope off it is meaningful. */
    function minutesTrend(id, rows, opts) {
        if (!el(id) || !rows || !rows.length) { return; }
        opts = opts || {};
        Highcharts.chart(id, {
            chart: { type: 'areaspline', height: opts.height || H },
            xAxis: {
                categories: rows.map(function (r) { return r.date; }),
                tickInterval: Math.ceil(rows.length / (isPhone ? 4 : 10)) || 1
            },
            yAxis: { title: { text: 'Minutes' } },
            legend: { enabled: false },
            tooltip: {
                formatter: function () {
                    var m = this.y, h = Math.floor(m / 60);
                    var pretty = h ? h + 'h ' + (m % 60) + 'm' : m + 'm';
                    return '<b>' + this.x + '</b><br>' + pretty + ' active window';
                }
            },
            series: [{
                name: 'Active window',
                data: rows.map(function (r) { return r.minutes; }),
                color: '#8B5CF6', lineWidth: 2,
                fillColor: {
                    linearGradient: { x1: 0, y1: 0, x2: 0, y2: 1 },
                    stops: [[0, 'rgba(139,92,246,.30)'], [1, 'rgba(139,92,246,.02)']]
                },
                marker: { radius: 3, fillColor: '#fff', lineColor: '#8B5CF6', lineWidth: 2 }
            }]
        });
    }

    window.mvUserActivity = {
        applyTheme: applyTheme,
        readPayload: readPayload,
        stackedTrend: stackedTrend,
        rankedBar: rankedBar,
        donut: donut,
        hourColumn: hourColumn,
        weekdayColumn: weekdayColumn,
        minutesTrend: minutesTrend
    };
})();
