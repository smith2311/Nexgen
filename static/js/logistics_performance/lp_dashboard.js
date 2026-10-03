/* lp_dashboard.js — Logistics Performance Center */
'use strict';

const C = window.LPD_CONFIG;

const LPD = {

  go(key) { window.location.href = C.urls[key]; },

  refresh(btn) {
    btn.classList.add('spin');
    setTimeout(() => { btn.classList.remove('spin'); location.reload(); }, 600);
  },

  init() {
    this._rail();
    this._kpiGauge();
    this._kpiCoverage();
    this._kpiTatChart();
    this._quadA();
    this._quadB();
    this._quadC();
    this._quadD();
    // Animate bars after paint
    requestAnimationFrame(() => requestAnimationFrame(() => this._animateBars()));
  },

  // ── Left Rail ──────────────────────────────────────────────────────────────
  _rail() {
    const steps = [
      { icon: 'fa-map-pin',   label: 'Network',    count: C.pincodes,    cls: C.pincodes > 0 ? 'healthy' : '',      url: C.urls.pincode_list },
      { icon: 'fa-id-card',   label: 'Sales Cards', count: C.sales_cards, cls: C.sales_pending > 0 ? 'warn' : (C.sales_cards > 0 ? 'healthy' : ''), url: C.urls.sales_list },
      { icon: 'fa-truck',     label: 'LP Records',  count: C.total,       cls: C.total > 0 ? 'active' : '',          url: C.urls.lp_list },
      { icon: 'fa-chart-bar', label: 'Report',      count: null,          cls: '',                                   url: C.urls.report },
    ];
    document.getElementById('lpdRail').innerHTML = steps.map(s => `
      <div class="lpd-step ${s.cls}" onclick="window.location.href='${s.url}'" title="${s.label}">
        <div class="dot"><i class="fas ${s.icon}"></i></div>
        <div class="rlabel">${s.label}</div>
        ${s.count !== null ? `<div class="rcount">${Number(s.count).toLocaleString('en-IN')}</div>` : ''}
      </div>`).join('');
  },

  // ── KPI 1: Delivery Gauge ─────────────────────────────────────────────────
  _kpiGauge() {
    const pct = C.total > 0 ? Math.round((C.delivered / C.total) * 100) : 0;
    const gauge = document.getElementById('lpdGauge');
    gauge.style.setProperty('--pct', pct);
    document.getElementById('lpdGaugePct').textContent   = pct + '%';
    document.getElementById('lpdTotal').textContent      = Number(C.total).toLocaleString('en-IN');
    document.getElementById('lpdDelivered').textContent  = Number(C.delivered).toLocaleString('en-IN');
    // Animate gauge fill
    let cur = 0;
    const target = pct;
    const step = () => {
      cur = Math.min(cur + 1.5, target);
      gauge.style.setProperty('--pct', cur);
      if (cur < target) requestAnimationFrame(step);
    };
    requestAnimationFrame(step);
  },

  // ── KPI 2: Coverage ───────────────────────────────────────────────────────
  _kpiCoverage() {
    document.getElementById('lpdPincodes').textContent = Number(C.pincodes).toLocaleString('en-IN');
    document.getElementById('lpdStates').textContent   = Number(C.states).toLocaleString('en-IN');
    const chips = [
      { label: `${C.locations} Locations`, warn: false },
      { label: `${C.couriers} Couriers`,   warn: C.couriers === 0 },
      { label: `${C.sales_cards} Sales Cards`, warn: false },
      { label: `${C.sales_divisions} Divisions`, warn: false },
    ];
    document.getElementById('lpdCovChips').innerHTML = chips
      .map(ch => `<span class="kpi-chip${ch.warn ? ' warn' : ''}">${ch.label}</span>`).join('');
  },

  // ── KPI 3: TAT Health (pure-CSS bars, no CDN dependency) ─────────────────
  _kpiTatChart() {
    const onTime = Math.max(0, C.delivered - C.high_tat);
    const bars = [
      { label: 'On-Time',  n: onTime,     color: '#10b981' },
      { label: 'High TAT', n: C.high_tat, color: '#ef4444' },
      { label: 'Pending',  n: C.pending,  color: '#f59e0b' },
      { label: 'With EDD', n: C.with_edd, color: '#0076c8' },
    ];
    const max = Math.max(...bars.map(b => b.n), 1);
    const canvas = document.getElementById('lpdTatChart');
    const div = document.createElement('div');
    div.className = 'tat-bars';
    div.innerHTML = bars.map(b => {
      const pct = Math.round((b.n / max) * 100);
      return `<div class="tat-bar-row">
        <div class="tat-bar-label">${b.label}</div>
        <div class="tat-bar-track">
          <div class="tat-bar-fill" data-pct="${pct}" style="background:${b.color}"></div>
        </div>
        <div class="tat-bar-val">${Number(b.n).toLocaleString('en-IN')}</div>
      </div>`;
    }).join('');
    canvas.replaceWith(div);
    // Animate bars after paint
    requestAnimationFrame(() => requestAnimationFrame(() => {
      div.querySelectorAll('.tat-bar-fill[data-pct]').forEach(el => {
        el.style.width = el.dataset.pct + '%';
      });
    }));
  },

  // ── Quad A: Shipment Status ───────────────────────────────────────────────
  _quadA() {
    const total = C.total || 1;
    const rows = [
      { icon: 'fa-check-circle', bg: 'var(--green-lt)', color: 'var(--green)', label: 'Delivered',         n: C.delivered, bar: '#10b981' },
      { icon: 'fa-clock',        bg: 'var(--amber-lt)', color: 'var(--amber)', label: 'In Transit/Pending', n: C.pending,   bar: '#f59e0b' },
      { icon: 'fa-calendar-alt', bg: 'var(--blue-lt)',  color: 'var(--blue)',  label: 'With EDD',           n: C.with_edd,  bar: '#0076c8' },
      { icon: 'fa-exclamation-triangle', bg: 'var(--red-lt)', color: 'var(--red)', label: 'High TAT (>5d)', n: C.high_tat,  bar: '#ef4444' },
    ];
    document.getElementById('quadA').innerHTML = rows.map(r => {
      const pct = Math.min(100, Math.round((r.n / total) * 100));
      return `
        <div class="status-row">
          <div class="status-icon" style="background:${r.bg};color:${r.color}">
            <i class="fas ${r.icon}"></i>
          </div>
          <div class="status-label">${r.label}</div>
          <div class="status-bar-wrap">
            <div class="status-bar" data-w="${pct}" style="background:${r.bar}"></div>
          </div>
          <div class="status-count">${Number(r.n).toLocaleString('en-IN')}</div>
        </div>`;
    }).join('');
  },

  _animateBars() {
    document.querySelectorAll('.status-bar[data-w]').forEach(el => {
      el.style.width = el.dataset.w + '%';
    });
  },

  // ── Quad B: Master Data Health ────────────────────────────────────────────
  _quadB() {
    const rows = [
      { icon: 'fa-map-pin',       label: 'Total Pincodes',  val: C.pincodes,      badge: C.pincodes > 0 ? 'ok' : 'err',  bdg: C.pincodes > 0 ? 'Active' : 'Empty' },
      { icon: 'fa-map-marker-alt',label: 'Locations',       val: C.locations,     badge: 'ok',                           bdg: 'Mapped' },
      { icon: 'fa-truck',         label: 'Courier Masters', val: C.courier_count, badge: C.courier_count > 0 ? 'ok' : 'warn', bdg: C.courier_count > 0 ? 'Ready' : 'Check' },
      { icon: 'fa-id-card',       label: 'Sales Cards',     val: C.sales_cards,   badge: 'ok',                           bdg: 'Active' },
      { icon: 'fa-exclamation',   label: 'Pending Cards',   val: C.sales_pending, badge: C.sales_pending > 0 ? 'warn' : 'ok', bdg: C.sales_pending > 0 ? 'Review' : 'Clear' },
      { icon: 'fa-building',      label: 'Companies',       val: C.division_count,badge: C.division_count > 0 ? 'ok' : 'warn', bdg: 'Configured' },
    ];
    document.getElementById('quadB').innerHTML =
      rows.map(r => `
        <div class="md-row">
          <label><i class="fas ${r.icon}"></i> ${r.label}</label>
          <div class="md-val">${Number(r.val).toLocaleString('en-IN')}</div>
          <span class="md-badge ${r.badge}">${r.bdg}</span>
        </div>`).join('') +
      `<div class="md-foot"><a href="${C.urls.pincode_list}">Manage Masters <i class="fas fa-arrow-right"></i></a></div>`;
  },

  // ── Quad C: Recent Shipments ──────────────────────────────────────────────
  _quadC() {
    const records = C.recent_records || [];
    if (!records.length) {
      document.getElementById('quadC').innerHTML = '<div class="lpd-empty"><i class="fas fa-inbox" style="font-size:24px;margin-bottom:8px;display:block;color:var(--border-md)"></i>No shipment records yet.</div>';
      return;
    }
    document.getElementById('quadC').innerHTML = `
      <div class="ship-tbl">
        <div class="ship-head"><div>Docket</div><div>Division</div><div>Courier</div><div>Dispatch</div><div>Status</div></div>
        ${records.map(r => {
          const status = r.delivery_date ? 'delivered' : (r.dispatch_date ? 'transit' : 'pending');
          const label  = r.delivery_date ? 'Delivered'  : (r.dispatch_date ? 'In Transit' : 'Pending');
          return `
            <div class="ship-row" onclick="window.location.href='${C.urls.lp_list}'" title="View all records">
              <div style="font-weight:600;font-family:var(--font-mono,monospace);font-size:11px">${r.docket_no}</div>
              <div>${r.division}</div>
              <div>${r.courier_name}</div>
              <div>${r.dispatch_date}</div>
              <div><span class="ship-pill ${status}">${label}</span></div>
            </div>`;
        }).join('')}
      </div>`;
  },

  // ── Quad D: Quick Actions ─────────────────────────────────────────────────
  _quadD() {
    const actions = [
      { icon: 'fa-upload',    label: 'Upload Records',  sub: 'CSV / Excel import',  url: C.urls.lp_list + '?tab=tab-upload', color: 'var(--blue)'   },
      { icon: 'fa-chart-line',label: 'Performance Report', sub: 'TAT, Cost & Courier', url: C.urls.report,                   color: 'var(--green)'  },
      { icon: 'fa-map-pin',   label: 'Pincode Master',  sub: 'Manage network',      url: C.urls.pincode_list,                color: 'var(--navy)'   },
      { icon: 'fa-id-card',   label: 'Sales Cards',     sub: 'Doc types & divisions', url: C.urls.sales_list,               color: 'var(--purple)' },
    ];
    document.getElementById('quadD').innerHTML = `
      <div class="qa-grid">
        ${actions.map(a => `
          <a href="${a.url}" class="qa-btn">
            <i class="fas ${a.icon}" style="color:${a.color}"></i>
            <b>${a.label}</b>
            <small>${a.sub}</small>
          </a>`).join('')}
      </div>`;
  },
};

document.addEventListener('DOMContentLoaded', () => LPD.init());
