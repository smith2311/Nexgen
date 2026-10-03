/* ═══════════════════════════════════════════════════════════════════════
   grocery_dashboard.js — specification section 1.

   Reads everything from one call to /grocery/api/v1/dashboard/ and renders
   KPI tiles plus four Chart.js charts.

   Chart.js is loaded from the same CDN and version the VAT dashboard uses, so
   the two modules share one cached copy. If it fails to load the page still
   works: the KPIs and the top-items table are plain DOM, and each chart falls
   back to an empty-state panel rather than a blank box.
═══════════════════════════════════════════════════════════════════════ */
(function () {
  'use strict';

  if (typeof GS_DASH === 'undefined') return;

  const U = GSUtil;
  const charts = {};

  // Palette borrowed from the module's CSS tokens so the charts belong to the
  // same design system as the tables around them.
  const COLOR = {
    blue: '#0076c8', teal: '#00c2a8', green: '#10b981',
    amber: '#f59e0b', red: '#ef4444', purple: '#8b5cf6',
    slate: '#64748b', navy: '#0b1f3a',
  };
  const SERIES = [COLOR.blue, COLOR.teal, COLOR.amber, COLOR.purple,
                  COLOR.green, COLOR.red, COLOR.slate, COLOR.navy];

  const hasChart = () => typeof Chart !== 'undefined';

  /* ── KPI tiles ── */

  function money(v) { return '₹' + U.money(v); }

  function renderKpis(k) {
    const tiles = [
      { label: 'Inventory Value', value: money(k.inventory_value),
        icon: 'fas fa-indian-rupee-sign', tone: 'blue' },
      { label: 'Stock Quantity', value: U.qty(k.stock_quantity),
        icon: 'fas fa-boxes-stacked', tone: 'teal' },
      { label: 'Purchases This Month', value: money(k.month_purchase_cost),
        icon: 'fas fa-truck-ramp-box', tone: 'green' },
      { label: 'Consumed This Month', value: money(k.month_consumption_cost),
        icon: 'fas fa-utensils', tone: 'purple' },
      { label: 'Low Stock', value: k.low_stock_items,
        icon: 'fas fa-arrow-trend-down', tone: k.low_stock_items ? 'amber' : 'slate',
        link: '/grocery/report/stock/?stock_status=LOW' },
      { label: 'Out of Stock', value: k.out_of_stock_items,
        icon: 'fas fa-circle-xmark', tone: k.out_of_stock_items ? 'red' : 'slate',
        link: '/grocery/report/stock/?stock_status=OUT_OF_STOCK' },
      { label: 'Expiring ≤30 days', value: k.expiring_30_days,
        icon: 'fas fa-hourglass-half', tone: k.expiring_30_days ? 'amber' : 'slate',
        link: '/grocery/report/expiry/' },
      { label: 'Expired in Stock', value: k.expired_batches,
        icon: 'fas fa-triangle-exclamation', tone: k.expired_batches ? 'red' : 'slate',
        link: '/grocery/report/expiry/' },
      { label: 'Cost Variance', value: money(k.cost_variance),
        icon: 'fas fa-scale-unbalanced',
        tone: Number(k.cost_variance) > 0 ? 'red'
            : Number(k.cost_variance) < 0 ? 'green' : 'slate',
        link: '/grocery/report/variance/' },
      { label: 'Draft Receipts', value: k.draft_receipts,
        icon: 'fas fa-file-pen', tone: k.draft_receipts ? 'amber' : 'slate',
        link: '/grocery/doc/receipts/?status=DRAFT' },
    ];

    U.$('gsKpiGrid').innerHTML = tiles.map((t) => {
      const inner = `
        <span class="gs-kpi-icon gs-tone-${t.tone}"><i class="${t.icon}"></i></span>
        <span class="gs-kpi-body">
          <span class="gs-kpi-value">${U.esc(t.value)}</span>
          <span class="gs-kpi-label">${U.esc(t.label)}</span>
        </span>`;
      return t.link
        ? `<a class="gs-kpi gs-kpi-link" href="${t.link}">${inner}</a>`
        : `<div class="gs-kpi">${inner}</div>`;
    }).join('');
  }

  /* ── charts ── */

  function show(canvasId, emptyId, hasData) {
    const box = U.$(canvasId).parentElement;
    box.hidden = !hasData;
    U.$(emptyId).hidden = hasData;
    return hasData && hasChart();
  }

  function destroy(key) {
    if (charts[key]) { charts[key].destroy(); delete charts[key]; }
  }

  const MONEY_TICK = {
    callback: (v) => (Math.abs(v) >= 100000 ? (v / 100000).toFixed(1) + 'L'
                    : Math.abs(v) >= 1000 ? (v / 1000).toFixed(0) + 'k' : v),
  };

  const BASE_OPTS = {
    responsive: true,
    maintainAspectRatio: false,
    plugins: {
      legend: { labels: { boxWidth: 12, font: { size: 11 } } },
      tooltip: {
        callbacks: {
          label: (c) => `${c.dataset.label || c.label}: ₹${U.money(c.parsed.y ?? c.parsed)}`,
        },
      },
    },
  };

  function renderMonthly(rows) {
    const any = rows.some((r) => Number(r.purchases) || Number(r.consumption));
    if (!show('chartMonthly', 'emptyMonthly', any)) return;
    destroy('monthly');

    charts.monthly = new Chart(U.$('chartMonthly'), {
      type: 'line',
      data: {
        labels: rows.map((r) => r.label),
        datasets: [
          { label: 'Purchases', data: rows.map((r) => Number(r.purchases)),
            borderColor: COLOR.blue, backgroundColor: 'rgba(0,118,200,.10)',
            fill: true, tension: 0.3, pointRadius: 3 },
          { label: 'Consumption', data: rows.map((r) => Number(r.consumption)),
            borderColor: COLOR.purple, backgroundColor: 'rgba(139,92,246,.10)',
            fill: true, tension: 0.3, pointRadius: 3 },
        ],
      },
      options: Object.assign({}, BASE_OPTS, {
        scales: { y: { beginAtZero: true, ticks: MONEY_TICK } },
      }),
    });
  }

  function renderCategory(rows) {
    if (!show('chartCategory', 'emptyCategory', rows.length)) return;
    destroy('category');

    charts.category = new Chart(U.$('chartCategory'), {
      type: 'doughnut',
      data: {
        labels: rows.map((r) => r.name),
        datasets: [{ data: rows.map((r) => Number(r.value)),
                     backgroundColor: SERIES, borderWidth: 0 }],
      },
      options: Object.assign({}, BASE_OPTS, {
        cutout: '58%',
        plugins: Object.assign({}, BASE_OPTS.plugins, {
          legend: { position: 'right', labels: { boxWidth: 11, font: { size: 11 } } },
        }),
      }),
    });
  }

  function renderExpiry(rows) {
    const any = rows.some((r) => r.count);
    if (!show('chartExpiry', 'emptyExpiry', any)) return;
    destroy('expiry');

    charts.expiry = new Chart(U.$('chartExpiry'), {
      type: 'bar',
      data: {
        labels: rows.map((r) => r.label),
        datasets: [{
          label: 'Batches', data: rows.map((r) => r.count),
          backgroundColor: [COLOR.red, COLOR.amber, '#fbbf24', COLOR.teal],
          borderRadius: 6,
        }],
      },
      options: Object.assign({}, BASE_OPTS, {
        plugins: Object.assign({}, BASE_OPTS.plugins, {
          legend: { display: false },
          // Counts, not money — the shared tooltip would print ₹.
          tooltip: { callbacks: { label: (c) => `${c.parsed.y} batch(es)` } },
        }),
        scales: { y: { beginAtZero: true, ticks: { precision: 0 } } },
      }),
    });
  }

  function renderSupplier(rows) {
    if (!show('chartSupplier', 'emptySupplier', rows.length)) return;
    destroy('supplier');

    charts.supplier = new Chart(U.$('chartSupplier'), {
      type: 'bar',
      data: {
        labels: rows.map((r) => r.name),
        datasets: [{ label: 'Purchased', data: rows.map((r) => Number(r.value)),
                     backgroundColor: COLOR.teal, borderRadius: 6 }],
      },
      options: Object.assign({}, BASE_OPTS, {
        indexAxis: 'y',
        plugins: Object.assign({}, BASE_OPTS.plugins, { legend: { display: false } }),
        scales: { x: { beginAtZero: true, ticks: MONEY_TICK } },
      }),
    });
  }

  function renderTopItems(rows) {
    const body = U.$('gsTopItems');
    if (!rows.length) {
      body.innerHTML = `<tr><td class="gs-empty" colspan="3">
        <i class="fas fa-inbox"></i>No purchases recorded yet.</td></tr>`;
      return;
    }
    body.innerHTML = rows.map((r) => `<tr>
      <td>${U.esc(r.name)}</td>
      <td class="gs-cell-num">${U.qty(r.quantity)}</td>
      <td class="gs-cell-num">₹${U.money(r.value)}</td></tr>`).join('');
  }

  /* ── load ── */

  async function load() {
    const err = U.$('gsDashError');
    const errText = U.$('gsDashErrorText');
    err.hidden = true;
    try {
      const data = await U.api(GS_DASH.apiUrl);
      U.$('gsAsOf').textContent = 'as of ' + U.dateText(data.as_of);
      renderKpis(data.kpis);
      renderMonthly(data.monthly || []);
      renderCategory(data.category_value || []);
      renderExpiry(data.expiry_buckets || []);
      renderSupplier(data.supplier_purchase || []);
      renderTopItems(data.top_items || []);

      if (!hasChart()) {
        errText.textContent = 'Charts could not load (the chart library is '
          + 'unreachable). All figures below are still accurate.';
        err.hidden = false;
      }
    } catch (e) {
      U.$('gsKpiGrid').innerHTML = '';
      errText.textContent = U.errorMessage(e, 'Could not load the dashboard.');
      err.hidden = false;
    }
  }

  document.addEventListener('DOMContentLoaded', function () {
    load();
    U.$('gsRefreshBtn').addEventListener('click', () => {
      load();
      U.toast('Dashboard refreshed');
    });
  });
})();
