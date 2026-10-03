/* ============================================================================
 * VendorLink — Admin → Insights charts
 *
 * Highcharts builders shared by the all-users dashboard and the per-user
 * drill-down. Both draw the same kinds of chart, so the config lives here once
 * instead of being copied into two inline <script> blocks that then drift apart
 * on colours, tooltips and axis formatting.
 *
 * Highcharts is vendored at static/lib/highcharts/ — no CDN, because the
 * platform must not fetch scripts at runtime. Note what that bundle does NOT
 * include: heatmap. The heatmap on these pages is a CSS grid rendered by the
 * template (see vl-insights.css), not a series built here.
 *
 * Every series is prepared server-side in vendorlink/useractivity.py and
 * arrives as one JSON blob. Nothing here computes a metric — it only draws one,
 * so a number can never differ between the chart and the table beside it.
 * ========================================================================= */
(function () {
    'use strict';

    var INK = '#101828';
    var MUTED = '#5b6879';
    var FAINT = '#8a96a6';
    var HAIRLINE = '#e8edf3';
    var GRID = '#f1f4f8';
    var PRIMARY = '#0076c8';

    var isPhone = window.matchMedia('(max-width: 640px)').matches;
    var H = isPhone ? 240 : 290;
    var H_TALL = isPhone ? 300 : 360;

    function applyTheme() {
        if (!window.Highcharts) { return; }
        Highcharts.setOptions({
            credits: { enabled: false },
            lang: { thousandsSep: ',' },
            chart: {
                style: { fontFamily: 'inherit' },
                backgroundColor: 'transparent',
                spacing: [6, 4, 6, 4]
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
                labels: { style: { color: FAINT, fontSize: '11px' } }
            },
            yAxis: {
                gridLineColor: GRID, gridLineDashStyle: 'Dash',
                labels: { style: { color: FAINT, fontSize: '11px' } },
                title: { style: { color: FAINT, fontSize: '11px' } }
            },
            plotOptions: { series: { animation: { duration: 500 }, borderRadius: 3 } }
        });
    }

    /* Read a `{{ x|json_script:"id" }}` block. Returns null rather than
     * throwing if the element is missing or unparseable, so one bad payload
     * cannot take the rest of the page's JS down with it. */
    function readPayload(id) {
        var el = document.getElementById(id);
        if (!el) { return null; }
        try { return JSON.parse(el.textContent); } catch (e) { return null; }
    }

    function el(id) { return document.getElementById(id); }

    /* Stacked column trend — actions per day, split by work type.
     *
     * Columns, not an area: the data is a count of discrete events per day, and
     * an area spline interpolates between days, implying activity at times when
     * there was none. */
    function stackedTrend(id, trend, opts) {
        if (!el(id) || !trend || !trend.series || !trend.series.length) { return; }
        opts = opts || {};

        var series = trend.series.map(function (s) {
            return { name: s.name, color: s.color, data: s.data, type: 'column' };
        });
        if (opts.cumulative && trend.cumulative) {
            series.push({
                name: 'Cumulative', type: 'spline', yAxis: 1, color: '#8b5cf6',
                data: trend.cumulative, lineWidth: 2, marker: { enabled: false },
                dashStyle: 'ShortDot'
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
        // Their own daily average, so a column reads as above or below their
        // norm without the reader doing arithmetic.
        if (opts.average && trend.average) {
            yAxes[0].plotLines = [{
                value: trend.average, color: '#ef4444', dashStyle: 'Dash', width: 1.5, zIndex: 4,
                label: {
                    text: 'avg ' + trend.average + '/day',
                    style: { color: '#ef4444', fontSize: '10px', fontWeight: '600' }
                }
            }];
        }

        Highcharts.chart(id, {
            chart: { type: 'column', height: opts.height || H_TALL, zoomType: 'x' },
            xAxis: {
                categories: trend.dates,
                // A 365-day window would otherwise print every date and turn
                // the axis into a grey smear.
                tickInterval: Math.ceil(trend.dates.length / (isPhone ? 5 : 14)) || 1,
                crosshair: true
            },
            yAxis: yAxes,
            legend: { enabled: true, itemStyle: { fontSize: '11px', color: MUTED } },
            tooltip: {
                shared: true,
                // `shared: true` lists every series at the hovered x, including
                // the ones sitting at zero — on a sparse month that produced a
                // tooltip reading "Onboarding: 0 / Commercial: 0 / Assessment: 0",
                // which says nothing and looks broken. Only non-zero series are
                // listed, and a genuinely empty day says so.
                formatter: function () {
                    var lines = (this.points || []).filter(function (p) { return p.y; });
                    if (!lines.length) { return '<b>' + this.x + '</b><br>No activity'; }
                    var total = lines.reduce(function (sum, p) { return sum + p.y; }, 0);
                    return '<b>' + this.x + '</b> — ' + total + ' action' + (total === 1 ? '' : 's')
                        + '<br>' + lines.map(function (p) {
                            return '<span style="color:' + p.color + '">●</span> '
                                + p.series.name + ': <b>' + p.y + '</b>';
                        }).join('<br>');
                },
            },
            plotOptions: {
                column: { stacking: 'normal', borderWidth: 0, pointPadding: 0.05, groupPadding: 0.08 }
            },
            series: series
        });
    }

    /* Horizontal ranked bar. `urls` (parallel to values) makes each bar a link
     * — the drill-down the all-users view needs. */
    function rankedBar(id, names, values, opts) {
        if (!el(id) || !values || !values.length) { return; }
        opts = opts || {};
        var urls = opts.urls || null;
        // Grows with the list but capped, so a long roster cannot leave a 900px
        // hole beside a short chart in the same grid row.
        var height = Math.max(200, Math.min(520, names.length * (isPhone ? 24 : 30) + 60));

        Highcharts.chart(id, {
            chart: { type: 'bar', height: height },
            xAxis: { categories: names, labels: { style: { fontSize: isPhone ? '10px' : '11px' } } },
            yAxis: { title: { text: opts.axisTitle || 'Actions' } },
            legend: { enabled: false },
            tooltip: { pointFormat: '<b>{point.y}</b> ' + (opts.unit || 'actions') },
            plotOptions: {
                bar: {
                    colorByPoint: true,
                    colors: ['#0076c8', '#1183ce', '#2290d4', '#339dda', '#44aae0',
                             '#55b7e6', '#66c4ec', '#77d1f2', '#88def8', '#99ebfe',
                             '#a5effe', '#b1f3fe'],
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
            series: [{ name: opts.name || 'Actions', data: values }]
        });
    }

    /* Donut. Points arrive with their own colour so a slice matches the legend
     * dot beside the table on the same page. */
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
            series: [{ name: opts.name || 'Actions', data: points }]
        });
    }

    /* Weekday column. Plots the per-occurrence average, not the sum: a 30-day
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
                    return '<b>' + row.weekday + '</b><br>' + row.avg + ' avg &middot; '
                        + row.total + ' total over ' + row.days + ' day(s)';
                }
            },
            plotOptions: {
                column: {
                    color: '#00a891', borderWidth: 0,
                    dataLabels: {
                        enabled: !isPhone,
                        style: { fontWeight: '600', fontSize: '10px', textOutline: 'none', color: INK }
                    }
                }
            },
            series: [{ name: 'Avg', data: weekdays.map(function (w) { return w.avg; }) }]
        });
    }

    /* Simple column chart for a labelled series (countries, currencies). */
    function column(id, categories, values, opts) {
        if (!el(id) || !values || !values.length) { return; }
        opts = opts || {};
        Highcharts.chart(id, {
            chart: { type: 'column', height: opts.height || H },
            xAxis: { categories: categories, labels: { style: { fontSize: '10px' } } },
            yAxis: { title: { text: opts.axisTitle || null }, allowDecimals: false },
            legend: { enabled: false },
            tooltip: { pointFormat: '<b>{point.y}</b> ' + (opts.unit || '') },
            plotOptions: {
                column: {
                    color: opts.color || PRIMARY, borderWidth: 0,
                    dataLabels: {
                        enabled: !isPhone,
                        style: { fontWeight: '600', fontSize: '10px', textOutline: 'none', color: INK }
                    }
                }
            },
            series: [{ name: opts.name || 'Count', data: values }]
        });
    }

    window.vlInsights = {
        applyTheme: applyTheme,
        readPayload: readPayload,
        stackedTrend: stackedTrend,
        rankedBar: rankedBar,
        donut: donut,
        weekdayColumn: weekdayColumn,
        column: column
    };
})();
