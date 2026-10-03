/* Meril-V charts — thin, themed wrappers over the vendored Highcharts build.
 *
 * Highcharts, not Chart.js from a CDN. The grocery dashboard this look is
 * modelled on pulls chart.js@4 off jsdelivr at runtime; that is the one thing
 * from it deliberately NOT copied. Meril-V runs on warehouse floors and inside
 * a container with no guarantee of outbound internet, and static/lib/highcharts
 * is already vendored for exactly this reason. A dashboard whose charts vanish
 * when the CDN is unreachable is worse than one with no charts.
 *
 * Every colour is read from merilv/css/tokens.css at runtime rather than
 * duplicated here, so a token change moves the charts with the rest of the UI.
 */
window.mvCharts = (function () {
    'use strict';

    function token(name, fallback) {
        var probe = document.querySelector('.mv') || document.documentElement;
        var value = getComputedStyle(probe).getPropertyValue(name).trim();
        return value || fallback;
    }

    var INK, FAINT, GRID, HAIRLINE, BLUE, TEAL, AMBER, VIOLET, CYAN, ROSE, SERIES;

    function readTokens() {
        INK      = token('--mv-fg', '#0B1F3A');
        FAINT    = token('--mv-fg-2', '#4A5568');
        // --mv-border is an rgba() hairline; Highcharts accepts rgba strings
        // for axis and grid colours, so it can be used as-is.
        HAIRLINE = token('--mv-border', 'rgba(11,31,58,.10)');
        GRID     = HAIRLINE;
        BLUE     = token('--mv-accent-blue', '#0076C8');
        TEAL     = token('--mv-accent-teal', '#00A891');
        AMBER    = token('--mv-accent-amber', '#F59E0B');
        VIOLET   = token('--mv-accent-violet', '#8B5CF6');
        CYAN     = token('--mv-accent-cyan', '#00B5E2');
        ROSE     = token('--mv-accent-rose', '#EF4444');
        SERIES   = [BLUE, TEAL, AMBER, VIOLET, CYAN, ROSE];
    }

    function applyTheme() {
        if (typeof Highcharts === 'undefined') { return false; }
        readTokens();
        Highcharts.setOptions({
            credits: { enabled: false },
            lang: { thousandsSep: ',' },
            colors: SERIES,
            chart: {
                style: { fontFamily: 'inherit' },
                backgroundColor: 'transparent',
                spacing: [8, 4, 6, 4]
            },
            title: { text: null },
            tooltip: {
                backgroundColor: 'rgba(255,255,255,.97)',
                borderColor: HAIRLINE, borderRadius: 10, borderWidth: 1,
                shadow: { color: 'rgba(11,31,58,.18)', offsetX: 0, offsetY: 3, opacity: 0.1, width: 6 },
                style: { color: INK, fontSize: '12px' }
            },
            xAxis: {
                lineColor: HAIRLINE, tickColor: HAIRLINE,
                labels: { style: { color: FAINT, fontSize: '11px' } }
            },
            yAxis: {
                gridLineColor: GRID, gridLineDashStyle: 'Dash',
                labels: { style: { color: FAINT, fontSize: '11px' } },
                title: { text: null }
            },
            legend: { itemStyle: { color: FAINT, fontSize: '11px', fontWeight: '500' } },
            plotOptions: {
                series: { animation: { duration: 320 } }
            }
        });
        return true;
    }

    /* Reads a {% ... json_script %} payload. Returns null rather than throwing
       so one malformed payload cannot take down the whole page. */
    function readPayload(id) {
        var node = document.getElementById(id);
        if (!node) { return null; }
        try { return JSON.parse(node.textContent); } catch (e) { return null; }
    }

    function empty(el, message) {
        el.innerHTML = '<div class="mv-chart-empty">' + message + '</div>';
    }

    /* Throughput over time. Column rather than line: these are daily counts,
       and a line between two sparse days implies a continuity that isn't there. */
    function trend(elId, data, options) {
        var el = document.getElementById(elId);
        if (!el || !data) { return; }
        options = options || {};
        var total = (data.values || []).reduce(function (a, b) { return a + b; }, 0);
        if (!total) { return empty(el, options.emptyText || 'Nothing recorded in this window.'); }

        Highcharts.chart(el, {
            chart: { type: 'column', height: options.height || 260 },
            xAxis: {
                categories: data.labels,
                // A 90-day window would otherwise print 90 overlapping labels.
                tickInterval: Math.max(1, Math.ceil(data.labels.length / 12)),
                crosshair: { color: 'rgba(0,118,200,.08)' }
            },
            yAxis: { allowDecimals: false },
            tooltip: { headerFormat: '<b>{point.key}</b><br>', pointFormat: '{series.name}: <b>{point.y}</b>' },
            legend: { enabled: false },
            plotOptions: {
                column: { borderRadius: 3, borderWidth: 0, maxPointWidth: 26, color: BLUE }
            },
            series: [{ name: options.seriesName || 'Deliveries', data: data.values }]
        });
    }

    /* Ranked horizontal bars — the right shape for "top N by count", because
       the category labels are long text that never fits a vertical axis. */
    function rankedBar(elId, labels, values, options) {
        var el = document.getElementById(elId);
        if (!el) { return; }
        options = options || {};
        if (!labels || !labels.length) { return empty(el, options.emptyText || 'No data yet.'); }

        Highcharts.chart(el, {
            chart: { type: 'bar', height: options.height || Math.max(180, labels.length * 30 + 40) },
            xAxis: { categories: labels, lineWidth: 0, tickLength: 0 },
            yAxis: { allowDecimals: false, gridLineDashStyle: 'Dash' },
            legend: { enabled: false },
            tooltip: { pointFormat: '<b>{point.y}</b>' },
            plotOptions: {
                bar: {
                    borderRadius: 3, borderWidth: 0,
                    color: options.color || BLUE,
                    dataLabels: {
                        enabled: true, style: { fontSize: '11px', fontWeight: '600', textOutline: 'none', color: FAINT }
                    }
                }
            },
            series: [{ name: options.seriesName || 'Count', data: values }]
        });
    }

    /* Share-of-total. Donut over pie so the middle can carry the total, which
       is the number people actually want when they look at a breakdown. */
    function donut(elId, points, options) {
        var el = document.getElementById(elId);
        if (!el) { return; }
        options = options || {};
        if (!points || !points.length) { return empty(el, options.emptyText || 'No data yet.'); }
        var total = points.reduce(function (sum, p) { return sum + (p.y || 0); }, 0);

        Highcharts.chart(el, {
            chart: { type: 'pie', height: options.height || 240 },
            tooltip: { pointFormat: '<b>{point.y}</b> ({point.percentage:.0f}%)' },
            title: {
                text: '<div class="mv-donut-centre"><b>' + total + '</b><span>'
                    + (options.centreLabel || 'total') + '</span></div>',
                align: 'center', verticalAlign: 'middle', useHTML: true, y: 6
            },
            legend: { enabled: true, align: 'center', verticalAlign: 'bottom', itemMarginTop: 2 },
            plotOptions: {
                pie: {
                    innerSize: '68%', borderWidth: 2, borderColor: '#fff',
                    dataLabels: { enabled: false }, showInLegend: true
                }
            },
            series: [{ name: options.seriesName || 'Share', data: points }]
        });
    }

    /* Resolved hex values for callers that need one. Highcharts cannot resolve
       a CSS var(), so options.color must be a real colour — this is how a
       caller picks a token without hardcoding its hex. */
    function palette() {
        return { blue: BLUE, teal: TEAL, amber: AMBER, violet: VIOLET, cyan: CYAN, rose: ROSE };
    }

    return {
        applyTheme: applyTheme,
        readPayload: readPayload,
        palette: palette,
        trend: trend,
        rankedBar: rankedBar,
        donut: donut
    };
}());
