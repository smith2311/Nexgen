/* ============================================================================
 * VendorLink — commercial report charts
 *
 * Chart.js wiring for report_request.html and report_monthly.html. Kept in a
 * file rather than inline in the templates so the two reports cannot drift
 * into drawing the same quantity two different ways, and so the colour meaning
 * is declared once: green is always "cheapest", blue is always "the one we
 * chose". A reader who learns that on the request report should not have to
 * relearn it on the monthly one.
 *
 * Every chart sets maintainAspectRatio:false and relies on its parent well
 * having a fixed height (see .vlx-rep-chart__well). Without the fixed parent,
 * a Chart.js canvas sized to its container and a container sized to its canvas
 * feed each other and the chart grows on every resize event.
 * ========================================================================= */
(function () {
    'use strict';

    var C = {
        cheapest: '#059669',
        cheapestSoft: 'rgba(5,150,105,.14)',
        awarded: '#0076C8',
        awardedSoft: 'rgba(0,118,200,.14)',
        other: '#94A3B8',
        grid: 'rgba(11,28,48,.08)',
        ink: '#44474D',
        /* Cost-line palette. Seven fixed hues so a given cost line is the same
         * colour in every stack and across both reports. */
        lines: ['#0076C8', '#00B5E2', '#059669', '#F59E0B',
                '#8B5CF6', '#EF4444', '#64748B']
    };

    function money(currency, value) {
        var n = Number(value || 0).toLocaleString(undefined, {
            minimumFractionDigits: 2, maximumFractionDigits: 2
        });
        return currency ? currency + ' ' + n : n;
    }

    /* Long vendor names are the norm ("Nordwind Kontraktlogistik GmbH"), and a
     * full set of them on a category axis turns into a wall of rotated text.
     * Truncated for the tick, full name kept for the tooltip. */
    function shortLabel(name) {
        var s = String(name || '');
        return s.length > 22 ? s.slice(0, 21) + '…' : s;
    }

    function baseOptions(extra) {
        var options = {
            responsive: true,
            maintainAspectRatio: false,
            interaction: { mode: 'index', intersect: false },
            plugins: {
                legend: { display: false },
                tooltip: {
                    backgroundColor: '#0B1C30',
                    padding: 10,
                    titleFont: { size: 12, weight: '600' },
                    bodyFont: { size: 12 }
                }
            },
            scales: {
                x: { grid: { display: false }, ticks: { color: C.ink, font: { size: 11 } } },
                y: {
                    beginAtZero: true,
                    grid: { color: C.grid, drawBorder: false },
                    ticks: { color: C.ink, font: { size: 11 } }
                }
            }
        };
        return Object.assign(options, extra || {});
    }

    function canvas(id) {
        var el = document.getElementById(id);
        return el ? el.getContext('2d') : null;
    }

    // ── Request report ──────────────────────────────────────────────────────
    function totalsChart(data) {
        var ctx = canvas('repTotals');
        if (!ctx || !data.labels.length) { return; }

        var fills = data.roles.map(function (role) {
            return role === 'awarded' ? C.awarded : role === 'cheapest' ? C.cheapest : C.other;
        });

        new Chart(ctx, {
            type: 'bar',
            data: {
                labels: data.labels.map(shortLabel),
                datasets: [{
                    data: data.values,
                    backgroundColor: fills,
                    borderRadius: 6,
                    maxBarThickness: 46
                }]
            },
            options: baseOptions({
                /* Horizontal once there are more than five vendors: vertical
                 * bars force the names to rotate, and a rotated 30-character
                 * company name is unreadable at this size. */
                indexAxis: data.labels.length > 5 ? 'y' : 'x',
                plugins: {
                    legend: { display: false },
                    tooltip: {
                        backgroundColor: '#0B1C30',
                        callbacks: {
                            title: function (items) { return data.labels[items[0].dataIndex]; },
                            label: function (item) {
                                var role = data.roles[item.dataIndex];
                                var suffix = role === 'awarded' ? '  (awarded)'
                                           : role === 'cheapest' ? '  (cheapest)' : '';
                                return money(data.currency, item.raw) + suffix;
                            }
                        }
                    }
                }
            })
        });
    }

    function compositionChart(data) {
        var ctx = canvas('repComposition');
        if (!ctx || !data.labels.length) { return; }

        /* A cost line nobody quoted is dropped rather than drawn as a
         * zero-height band: seven legend entries where three carry data reads
         * as missing information. */
        var series = data.series.filter(function (s) {
            return s.values.some(function (v) { return Number(v) > 0; });
        });

        new Chart(ctx, {
            type: 'bar',
            data: {
                labels: data.labels.map(shortLabel),
                datasets: series.map(function (s, i) {
                    return {
                        label: s.label,
                        data: s.values,
                        backgroundColor: C.lines[i % C.lines.length],
                        borderRadius: 3,
                        maxBarThickness: 46
                    };
                })
            },
            options: baseOptions({
                plugins: {
                    legend: {
                        display: true, position: 'bottom',
                        labels: { boxWidth: 10, boxHeight: 10, font: { size: 11 }, color: C.ink }
                    },
                    tooltip: {
                        backgroundColor: '#0B1C30',
                        callbacks: {
                            title: function (items) { return data.labels[items[0].dataIndex]; },
                            label: function (item) {
                                return item.dataset.label + ': ' + money(data.currency, item.raw);
                            }
                        }
                    }
                },
                scales: {
                    x: { stacked: true, grid: { display: false }, ticks: { color: C.ink, font: { size: 11 } } },
                    y: {
                        stacked: true, beginAtZero: true,
                        grid: { color: C.grid, drawBorder: false },
                        ticks: { color: C.ink, font: { size: 11 } }
                    }
                }
            })
        });
    }

    // ── Monthly report ──────────────────────────────────────────────────────
    function volumeChart(data) {
        var ctx = canvas('repVolume');
        if (!ctx || !data.labels.length) { return; }
        new Chart(ctx, {
            data: {
                labels: data.labels,
                datasets: [
                    {
                        type: 'bar', label: 'RFIs raised', data: data.requests,
                        backgroundColor: C.awardedSoft, borderColor: C.awarded, borderWidth: 1,
                        borderRadius: 5, maxBarThickness: 34, order: 2
                    },
                    {
                        /* Bids on a line, not a second bar: the two quantities
                         * differ by an order of magnitude, and side-by-side
                         * bars would flatten the RFI series to nothing. */
                        type: 'line', label: 'Bids received', data: data.bids,
                        borderColor: C.cheapest, backgroundColor: C.cheapest,
                        borderWidth: 2, tension: .3, pointRadius: 3, yAxisID: 'y1', order: 1
                    }
                ]
            },
            options: baseOptions({
                plugins: {
                    legend: {
                        display: true, position: 'bottom',
                        labels: { boxWidth: 10, boxHeight: 10, font: { size: 11 }, color: C.ink }
                    },
                    tooltip: { backgroundColor: '#0B1C30' }
                },
                scales: {
                    x: { grid: { display: false }, ticks: { color: C.ink, font: { size: 11 } } },
                    y: {
                        beginAtZero: true, position: 'left',
                        grid: { color: C.grid, drawBorder: false },
                        ticks: { color: C.ink, font: { size: 11 }, precision: 0 },
                        title: { display: true, text: 'RFIs', color: C.ink, font: { size: 10 } }
                    },
                    y1: {
                        beginAtZero: true, position: 'right',
                        grid: { display: false },
                        ticks: { color: C.ink, font: { size: 11 }, precision: 0 },
                        title: { display: true, text: 'Bids', color: C.ink, font: { size: 10 } }
                    }
                }
            })
        });
    }

    function spendChart(data) {
        var ctx = canvas('repSpend');
        if (!ctx || !data.labels.length) { return; }
        new Chart(ctx, {
            type: 'bar',
            data: {
                labels: data.labels,
                datasets: [
                    {
                        label: 'Cheapest available', data: data.cheapest,
                        backgroundColor: C.cheapest, borderRadius: 5, maxBarThickness: 26
                    },
                    {
                        label: 'Awarded', data: data.awarded,
                        backgroundColor: C.awarded, borderRadius: 5, maxBarThickness: 26
                    }
                ]
            },
            options: baseOptions({
                plugins: {
                    legend: {
                        display: true, position: 'bottom',
                        labels: { boxWidth: 10, boxHeight: 10, font: { size: 11 }, color: C.ink }
                    },
                    tooltip: {
                        backgroundColor: '#0B1C30',
                        callbacks: {
                            label: function (item) {
                                return item.dataset.label + ': ' + money(data.currency, item.raw);
                            }
                        }
                    }
                }
            })
        });
    }

    function outcomesChart(data) {
        var ctx = canvas('repOutcomes');
        if (!ctx || !data.labels.length) { return; }
        // The outcome is binary now: an award either exists (the requester
        // decided) or it does not -- there is no vendor reply to distinguish
        // any more. See vendorlink/reports.py.
        var byLabel = {
            'Awarded': C.cheapest,
            'Not awarded': C.other
        };
        new Chart(ctx, {
            type: 'doughnut',
            data: {
                labels: data.labels,
                datasets: [{
                    data: data.values,
                    backgroundColor: data.labels.map(function (l) { return byLabel[l] || C.other; }),
                    borderWidth: 2, borderColor: '#fff'
                }]
            },
            options: {
                responsive: true, maintainAspectRatio: false, cutout: '58%',
                plugins: {
                    legend: {
                        position: 'bottom',
                        labels: { boxWidth: 10, boxHeight: 10, font: { size: 11 }, color: C.ink }
                    },
                    tooltip: { backgroundColor: '#0B1C30' }
                }
            }
        });
    }

    function winsChart(data) {
        var ctx = canvas('repWins');
        if (!ctx || !data.labels.length) { return; }
        new Chart(ctx, {
            type: 'bar',
            data: {
                labels: data.labels.map(shortLabel),
                datasets: [{
                    data: data.values, backgroundColor: C.awarded,
                    borderRadius: 5, maxBarThickness: 24
                }]
            },
            options: baseOptions({
                indexAxis: 'y',
                plugins: {
                    legend: { display: false },
                    tooltip: {
                        backgroundColor: '#0B1C30',
                        callbacks: {
                            title: function (items) { return data.labels[items[0].dataIndex]; },
                            label: function (item) {
                                return item.raw + (item.raw === 1 ? ' award won' : ' awards won');
                            }
                        }
                    }
                },
                scales: {
                    x: {
                        beginAtZero: true, grid: { color: C.grid, drawBorder: false },
                        ticks: { color: C.ink, font: { size: 11 }, precision: 0 }
                    },
                    y: { grid: { display: false }, ticks: { color: C.ink, font: { size: 11 } } }
                }
            })
        });
    }

    window.vlxReportCharts = {
        requestReport: function (payload) {
            if (payload.totals) { totalsChart(payload.totals); }
            if (payload.composition) { compositionChart(payload.composition); }
        },
        monthlyReport: function (payload) {
            if (payload.volume) { volumeChart(payload.volume); }
            if (payload.spend) { spendChart(payload.spend); }
            if (payload.outcomes) { outcomesChart(payload.outcomes); }
            if (payload.wins) { winsChart(payload.wins); }
        }
    };
}());
