/* ═══════════════════════════════════════════════════════
   vat_dashboard.js — VAT Compliance & Reconciliation Center
   Real data: /api/dashboard/stats/ + live /api/dashboard/fx/
═══════════════════════════════════════════════════════ */
/* Charts inherit the platform typeface rather than Chart.js's Helvetica default. */
Chart.defaults.font.family = "'Helvetica Neue',Helvetica,Arial,sans-serif";

window.VATC = (function () {
  'use strict';

  const API = VATC_CONFIG.urls;
  const esc = LogiTable.escHtml;
  let countries = [];
  let recent = [];
  let varianceChart = null;
  let fxChart = null;
  let fxRates = [];

  function g(id) { return document.getElementById(id); }
  function fmt(v) { return (v === null || v === undefined || v === '') ? '—' : v; }
  function num(v) { return (v === null || v === undefined) ? '—' : Number(v).toLocaleString('en-IN'); }

  /* ── RAIL ── */
  function renderRail(rail) {
    const max = Math.max.apply(null, rail.map(function (r) { return r.count; }).concat([1]));
    g('vatcRail').innerHTML = rail.map(function (s) {
      const done = s.count > 0;
      return '<div class="rail-step ' + (done ? 'done' : '') + '">' +
        '<div class="dot"><i class="fas ' + s.icon + '"></i></div>' +
        '<div class="rlabel">' + esc(s.label) + '</div>' +
        '<div class="rcount">' + s.count + '</div>' +
      '</div>';
    }).join('');
    void max;
  }

  /* ── GAUGE ── */
  function renderGauge(month) {
    const pct = month.match_pct || 0;
    const el = g('kpiGauge');
    el.style.setProperty('--pct', pct);
    g('kpiGaugePct').textContent = pct + '%';
    g('kpiProcessed').textContent = num(month.processed);
    g('kpiMatchedM').textContent = num(month.matched);
    // colour gauge by health
    const col = pct >= 90 ? '#10b981' : pct >= 70 ? '#f59e0b' : '#ef4444';
    g('kpiGaugePct').style.color = col;
    el.style.background =
      'radial-gradient(closest-side, #ffffff 70%, transparent 71%),' +
      'conic-gradient(' + col + ' ' + pct + '%, #e6ebf2 0)';
  }

  /* ── VARIANCE CHART ── */
  function renderVariance(trend) {
    const ctx = g('varianceChart').getContext('2d');
    const labels = trend.map(function (t) { return t.month; });
    const data = trend.map(function (t) { return t.value; });
    if (varianceChart) { varianceChart.data.labels = labels; varianceChart.data.datasets[0].data = data; varianceChart.update(); return; }
    varianceChart = new Chart(ctx, {
      type: 'line',
      data: { labels: labels, datasets: [{
        data: data, borderColor: '#2f9bff', backgroundColor: 'rgba(47,155,255,0.15)',
        fill: true, tension: 0.35, pointRadius: 2, pointBackgroundColor: '#2f9bff', borderWidth: 2,
      }] },
      options: baseLineOpts(),
    });
  }

  function baseLineOpts() {
    return {
      responsive: true, maintainAspectRatio: false,
      plugins: {
        legend: { display: false },
        tooltip: {
          mode: 'index', intersect: false,
          backgroundColor: '#0b1f3a', titleColor: '#fff', bodyColor: '#e8eef7',
          padding: 10, cornerRadius: 8, displayColors: false,
        },
      },
      interaction: { mode: 'index', intersect: false },
      scales: {
        x: { grid: { color: 'rgba(11,31,58,0.06)' }, ticks: { color: '#9ca3af', font: { size: 10 } } },
        y: { grid: { color: 'rgba(11,31,58,0.06)' }, ticks: { color: '#9ca3af', font: { size: 10 } }, beginAtZero: true },
      },
    };
  }

  /* ── LANGUAGES ── */
  function renderLangs(langs) {
    g('kpiLangs').innerHTML = (langs || []).map(function (l) {
      return '<span class="kpi-lang-chip">' + esc(l) + '</span>';
    }).join('') || '<span class="kpi-lang-chip">No languages configured</span>';
  }

  /* ── MASTER DATA (A) ── */
  function renderCountrySelect() {
    const sel = g('mdCountry');
    sel.innerHTML = countries.map(function (c) {
      return '<option value="' + c.id + '">' + esc(c.country) + '</option>';
    }).join('') || '<option>No countries</option>';
    onCountry();
  }
  function onCountry() {
    const id = parseInt(g('mdCountry').value);
    const c = countries.find(function (x) { return x.id === id; });
    if (!c) return;
    g('mdLanguage').textContent = fmt(c.language);
    g('mdLocalCcy').textContent = fmt(c.local_currency);
    g('mdStdCcy').textContent = fmt(c.standard_invoice_currency);
    g('mdTolerance').textContent = '+/- ' + fmt(c.tolerance_limit);
    g('mdCha').textContent = fmt(c.cha_partner_name);
    g('mdEntity').textContent = fmt(c.local_entity);
  }

  /* ── PROCESSING LOG (C) ── */
  function logPill(s) {
    const cls = s === 'MATCHED' ? 'matched' : s === 'DISCREPANCY' ? 'discrepancy' : 'pending';
    return '<span class="log-pill ' + cls + '">' + (s || 'PENDING') + '</span>';
  }
  function renderLog(rows) {
    const body = g('logBody');
    if (!rows.length) { body.innerHTML = '<div class="vatc-empty">No invoices processed yet.</div>'; return; }
    body.innerHTML = rows.map(function (r) {
      return '<div class="log-row" onclick="VATC.openInvoice(' + r.id + ')">' +
        '<div>' + esc((r.invoice_date || r.created_at || '').slice(0, 10) || '—') + '</div>' +
        '<div>' + esc(fmt(r.invoice_number)) + '</div>' +
        '<div>' + esc(fmt(r.invoice_currency)) + '</div>' +
        '<div>' + esc(fmt(r.variance)) + '</div>' +
        '<div>' + logPill(r.recon_status) + '</div>' +
      '</div>';
    }).join('');
  }

  /* ── FINAL REVIEW (D) ── */
  function renderReview(counts) {
    g('frMatchedCount').textContent = num(counts.matched) + ' invoices';
    g('frFlagCount').textContent = num(counts.discrepancy) + ' flagged';
    const first = recent.find(function (r) { return r.stage === 'RECONCILED'; }) || recent[0];
    if (first) buildSummary(first);
  }
  function buildSummary(inv) {
    const matched = inv.recon_status === 'MATCHED';
    g('frSummary').textContent =
      'Invoice ' + (inv.invoice_number || '—') + ' — ' + (inv.country || '—') + '.\n' +
      'Taxable: ' + fmt(inv.taxable_amount) + ' · Expected VAT: ' + fmt(inv.expected_vat) +
      ' · Invoice VAT: ' + fmt(inv.charged_vat) + '.\n' +
      'Variance: ' + fmt(inv.variance) + ' — Tolerance check ' + (matched ? 'PASSED.' : 'EXCEEDED.') + '\n' +
      'Status: ' + (inv.recon_status || 'PENDING') + '. Output language: English.';
  }

  /* ── FX (B) ── */
  function loadFx() {
    fetch(API.fx, { credentials: 'same-origin' })
      .then(function (r) { return r.json(); })
      .then(function (res) {
        fxRates = (res.rates || []);
        const valid = fxRates.filter(function (r) { return r.rate !== null; });
        if (valid.length) {
          featureFx(fxRates.indexOf(valid[0]));
          renderFxChart(valid);
        } else {
          g('fxFeaturePair').textContent = 'No active pairs';
          g('fxFeatureRate').textContent = '—';
        }
        g('fxList').innerHTML = fxRates.map(function (r, i) {
          return '<div class="fx-item ' + (r.rate === null ? 'err' : '') + '" data-i="' + i + '" onclick="VATC.featureFx(' + i + ')">' +
            '<span>' + esc(r.pair) + '</span>' +
            '<b>' + (r.rate === null ? 'unavailable' : Number(r.rate).toLocaleString('en-IN', { maximumFractionDigits: 4 })) + '</b>' +
          '</div>';
        }).join('') || '<div class="vatc-empty">No currency pairs configured.</div>';
      })
      .catch(function () { g('fxList').innerHTML = '<div class="vatc-empty">Could not load exchange rates.</div>'; });
  }

  function featureFx(i) {
    const r = fxRates[i];
    if (!r || r.rate === null) return;
    g('fxFeaturePair').textContent = '1 ' + r.from + ' to ' + r.to;
    g('fxFeatureRate').textContent = Number(r.rate).toLocaleString('en-IN', { maximumFractionDigits: 4 });
    document.querySelectorAll('.fx-item').forEach(function (el) {
      el.classList.toggle('active', parseInt(el.dataset.i) === i);
    });
  }

  function renderFxChart(valid) {
    const ctx = g('fxChart').getContext('2d');
    const labels = valid.map(function (r) { return r.pair; });
    const data = valid.map(function (r) { return r.rate; });
    if (fxChart) { fxChart.data.labels = labels; fxChart.data.datasets[0].data = data; fxChart.update(); return; }
    fxChart = new Chart(ctx, {
      type: 'line',
      data: { labels: labels, datasets: [{
        data: data, borderColor: '#00c2a8', backgroundColor: 'rgba(0,194,168,0.15)',
        fill: true, tension: 0.35, pointRadius: 3, pointBackgroundColor: '#00c2a8', borderWidth: 2,
      }] },
      options: baseLineOpts(),
    });
  }

  /* ── LOAD ── */
  function load() {
    fetch(API.stats, { credentials: 'same-origin' })
      .then(function (r) { return r.json(); })
      .then(function (res) {
        if (!res.success) return;
        countries = res.countries || [];
        recent = res.recent || [];
        g('kpiCountries').textContent = num(res.counts.countries);
        g('kpiRates').textContent = num(res.counts.rates);
        renderRail(res.rail || []);
        renderGauge(res.month || {});
        renderVariance(res.variance_trend || []);
        renderLangs(res.languages || []);
        renderCountrySelect();
        renderLog(recent);
        renderReview(res.counts || {});
      })
      .catch(function () {});
    loadFx();
  }

  return {
    init: load,
    refresh: function (btn) { if (btn) { btn.classList.add('spin'); setTimeout(function () { btn.classList.remove('spin'); }, 600); } load(); },
    onCountry: onCountry,
    loadFx: loadFx,
    featureFx: featureFx,
    openInvoice: function (id) { window.location.href = API.detailPage + id + '/'; },
    draftEmail: function () {
      const first = recent.find(function (r) { return r.stage === 'RECONCILED'; });
      if (first) window.location.href = API.detailPage + first.id + '/';
      else window.location.href = API.invoices;
    },
    go: function (where) { window.location.href = API[where] || API.invoices; },
  };
})();

document.addEventListener('DOMContentLoaded', function () { VATC.init(); });
