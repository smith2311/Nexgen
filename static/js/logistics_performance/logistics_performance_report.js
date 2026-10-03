/* ═══════════════════════════════════════════════════════
   logistics_performance_report.js
   Handles all 5 report tabs: TAT, Cost, Courier, LC, Pincode
═══════════════════════════════════════════════════════ */

'use strict';

// ── State ─────────────────────────────────────────────────────────────────────
const _loaded         = { tat: false, cost: false, courier: false, lc: false };

// Raw data caches (populated on load, re-used by render)
let _tatRaw = [], _courierRaw = [], _lcRaw = [];

// ── Helpers ───────────────────────────────────────────────────────────────────

// Read a control's value (empty string if absent)
function _v(id) { const el = document.getElementById(id); return el ? el.value : ''; }

// Active group/view selectors (default to Division)
function tatGroup() { return _v('tatView') || 'Division'; }

// Inline filter dropdowns shared by the data tabs (prefix = tat|courier|lc)
function _tabFilters(prefix) {
  return {
    division: _v(prefix + 'Division'),
    mode:     _v(prefix + 'Mode'),
    courier:  _v(prefix + 'Courier'),
    plant:    _v(prefix + 'Plant'),
  };
}

// Cost doc-type pills → comma-separated sales_types param
// If all pills are active (or none), send empty string so backend returns everything
function costSalesTypes() {
  const all    = document.querySelectorAll('#costDocTypePills .filter-pill');
  const active = document.querySelectorAll('#costDocTypePills .filter-pill.active');
  if (!active.length || active.length === all.length) return '';   // no filter = all records
  return [...active].map(b => b.dataset.val).join(',');
}
function toggleCostPill(btn) {
  btn.classList.toggle('active');
  loadCost();
}

// Progressive reveal: show advanced filters only after both dates are set
function onCostDateChange() {
  const from = _v('costFromDate');
  const to   = _v('costToDate');
  const adv  = document.getElementById('costAdvancedFilters');
  const hint = document.getElementById('costDateHint');
  if (from && to) {
    adv.style.display  = '';
    hint.style.display = 'none';
    loadCost();
  } else {
    adv.style.display  = 'none';
    hint.style.display = '';
  }
}

function fmtINR(val) {
  if (val === null || val === undefined || val === '') return '—';
  const num = parseFloat(val);
  if (isNaN(num)) return '—';
  return new Intl.NumberFormat('en-IN', {
    style: 'currency', currency: 'INR',
    minimumFractionDigits: 0, maximumFractionDigits: 0,
  }).format(num);
}

function fmtNum(val, dp = 0) {
  if (val === null || val === undefined) return '—';
  const num = parseFloat(val);
  if (isNaN(num)) return '—';
  return new Intl.NumberFormat('en-IN', { maximumFractionDigits: dp }).format(num);
}

function pctClass(val) {
  const n = parseFloat(val);
  if (isNaN(n)) return '';
  if (n >= 90) return 'pct-good';
  if (n >= 70) return 'pct-warn';
  return 'pct-bad';
}

async function apiFetch(url) {
  const r = await fetch(url);
  if (!r.ok) throw new Error(`HTTP ${r.status}`);
  return r.json();
}

function buildQS(params) {
  const q = new URLSearchParams();
  Object.entries(params).forEach(([k, v]) => { if (v !== undefined && v !== null && v !== '') q.set(k, v); });
  return q.toString() ? '?' + q.toString() : '';
}

function showToast(msg, type = 'info') {
  const el = document.getElementById('rptToast');
  el.querySelector('.toast-msg').textContent = msg;
  el.classList.add('show');
  setTimeout(() => el.classList.remove('show'), 3200);
}

function setLoading(loadingId, bodyId, on) {
  document.getElementById(loadingId).style.display = on ? 'flex' : 'none';
  if (on) document.getElementById(bodyId).innerHTML = '';
}

function showEmpty(bodyId, msg = 'No records found for the selected filters.') {
  document.getElementById(bodyId).innerHTML =
    `<div class="empty-state"><div class="empty-state-icon"><i class="fas fa-search"></i></div><p>${msg}</p></div>`;
}

function showError(bodyId, msg) {
  document.getElementById(bodyId).innerHTML =
    `<div class="empty-state"><div class="empty-state-icon"><i class="fas fa-exclamation-triangle"></i></div><p>Error: ${msg}</p></div>`;
}

// ── Tab switching ─────────────────────────────────────────────────────────────

// Which loader to call when a tab is first activated
const _tabLoader = {
  'tab-tat':     () => loadTAT(),
  'tab-cost':    () => onCostDateChange(),   // reveal filters if dates already set
  'tab-courier': () => loadCourier(),
  'tab-lc':      () => loadLC(),
  'tab-pincode': null,   // pincode only loads on explicit search
};

function switchRptTab(id) {
  document.querySelectorAll('.rpt-tab-content').forEach(el => el.classList.remove('active'));
  document.querySelectorAll('.rpt-tab-btn').forEach(btn => btn.classList.remove('active'));
  document.getElementById(id).classList.add('active');
  document.querySelector(`[data-tab="${id}"]`).classList.add('active');

  // Auto-load on first switch to each tab
  const key = id.replace('tab-', '');
  if (key in _loaded && !_loaded[key] && _tabLoader[id]) {
    _loaded[key] = true;
    _tabLoader[id]();
  }
}

// ── Populate dropdowns from RPT_CONFIG ────────────────────────────────────────

function populateSelect(id, values) {
  const sel = document.getElementById(id);
  if (!sel) return;
  while (sel.options.length > 1) sel.remove(1);
 const seen = new Set();
  (values || []).forEach(v => {
    const key = String(v == null ? '' : v).trim().toLowerCase();
    if (!key || seen.has(key)) return;   // skip blanks + duplicates
    seen.add(key);
    const opt = document.createElement('option');
    opt.value = v; opt.textContent = v;
    sel.appendChild(opt);
  });
}

function initDropdowns() {
  // Inline filter dropdowns per data tab (mirror the reference report).
  ['tatDivision', 'costDivision', 'courierDivision', 'lcDivision']
    .forEach(id => populateSelect(id, RPT_CONFIG.divisions));
  ['tatMode', 'courierMode', 'lcMode']
    .forEach(id => populateSelect(id, RPT_CONFIG.modes));
  ['tatCourier', 'courierCourier', 'lcCourier']
    .forEach(id => populateSelect(id, RPT_CONFIG.couriers));
  ['tatPlant', 'costPlant', 'courierPlant', 'lcPlant']
    .forEach(id => populateSelect(id, RPT_CONFIG.plants));
  populateSelect('pincodeLocation', RPT_CONFIG.locations);
  populateSelect('pincodeMode', RPT_CONFIG.pincode_modes);

  // Filter each tab's Plant dropdown by the selected Division.
  // Capture phase so the plant list updates before the inline onchange reload fires.
  [['tatDivision', 'tatPlant'], ['costDivision', 'costPlant'],
   ['courierDivision', 'courierPlant'], ['lcDivision', 'lcPlant']]
    .forEach(([divId, plantId]) => {
      const divSel = document.getElementById(divId);
      if (divSel) divSel.addEventListener('change', () => filterPlantsByDivision(divId, plantId), true);
    });
}

// Repopulate a Plant dropdown to only the plants belonging to the chosen Division.
function filterPlantsByDivision(divId, plantId) {
  const division = _v(divId);
  const plants = division
    ? (RPT_CONFIG.plant_map[division] || [])
    : RPT_CONFIG.plants;
  const plantSel = document.getElementById(plantId);
  if (!plantSel) return;
  const prev = plantSel.value;
  populateSelect(plantId, plants);
  // Keep current selection if it's still valid for this division, else reset to "All".
  plantSel.value = plants.includes(prev) ? prev : '';
}

// ── Header stats ──────────────────────────────────────────────────────────────

async function loadStats() {
  try {
    const d = await apiFetch(RPT_CONFIG.urls.stats);
    if (d.success) {
      document.getElementById('statTotal').textContent   = fmtNum(d.stats.total);
      document.getElementById('statOnTime').textContent  = fmtNum(d.stats.on_time);
      document.getElementById('statDelayed').textContent = fmtNum(d.stats.delayed);
      document.getElementById('statPending').textContent = fmtNum(d.stats.pending);
    }
  } catch { /* silent */ }
}

// ── TAT Report ────────────────────────────────────────────────────────────────

async function loadTAT() {
  const qs = buildQS({
    group_by:  tatGroup(),
    date_from: _v('tatFromDate'),
    date_to:   _v('tatToDate'),
    ..._tabFilters('tat'),
  });

  const footer = document.getElementById('tatFooter');
  setLoading('tatLoading', 'tatTableBody', true);
  footer.style.display = 'none';

  try {
    const d = await apiFetch(RPT_CONFIG.urls.tat + qs);
    setLoading('tatLoading', 'tatTableBody', false);

    if (!d.success || !d.data.length) {
      _tatRaw = [];
      showEmpty('tatTableBody',
        'No TAT records found. Records need both a dispatch date and delivery date to appear here.');
      return;
    }
    _tatRaw = d.data;
    renderTAT();

  } catch (e) {
    setLoading('tatLoading', 'tatTableBody', false);
    showError('tatTableBody', e.message);
  }
}

function renderTAT() {
  const data   = _tatRaw;
  const footer = document.getElementById('tatFooter');
  footer.style.display = 'none';

  // Keep the table's group column header in sync with the View By selector
  const grpHdr = document.getElementById('tatGroupHeader');
  if (grpHdr) grpHdr.textContent = tatGroup();

  if (!data.length) {
    showEmpty('tatTableBody', 'No TAT records match the current filters.');
    return;
  }

  let tHit = 0, tMiss = 0, tGrand = 0;
  const html = data.map((r, i) => {
    tHit += r.hit; tMiss += r.miss; tGrand += r.grand_total;
    return `
      <div class="rpt-data-row tat-head">
        <div>${i + 1}</div>
        <div><strong>${r.label}</strong></div>
        <div class="pct-good cell-link" onclick="loadTATDetail('${r.label.replace(/'/g,"\\'")}','hit')">${r.hit}</div>
        <div class="pct-bad cell-link" onclick="loadTATDetail('${r.label.replace(/'/g,"\\'")}','miss')">${r.miss}</div>
        <div>${r.grand_total}</div>
        <div class="${pctClass(r.hit_pct)}">${r.hit_pct}%</div>
        <div class="pct-bad">${r.miss_pct}%</div>
        <div>${r.grand_pct}%</div>
      </div>`;
  }).join('');
  document.getElementById('tatTableBody').innerHTML = html;

  const hp = tGrand ? Math.round((tHit  / tGrand) * 100) : 0;
  const mp = tGrand ? Math.round((tMiss / tGrand) * 100) : 0;
  document.getElementById('ftTatHit').textContent      = tHit;
  document.getElementById('ftTatMiss').textContent     = tMiss;
  document.getElementById('ftTatGrand').textContent    = tGrand;
  document.getElementById('ftTatHitPct').textContent   = hp + '%';
  document.getElementById('ftTatMissPct').textContent  = mp + '%';
  document.getElementById('ftTatGrandPct').textContent = (hp + mp) + '%';
  footer.style.display = '';
}

// ── Cost Report — 3 simultaneous grids (Division · Mode · State) ──────────────

function _costQS() {
  return buildQS({
    date_from:   _v('costFromDate'),
    date_to:     _v('costToDate'),
    division:    _v('costDivision'),
    plant:       _v('costPlant'),
    sales_types: costSalesTypes(),
  });
}

async function _fetchCostView(viewBy) {
  return apiFetch(RPT_CONFIG.urls.cost + _costQS() + '&view_by=' + viewBy);
}

function _renderCostGrid(data, bodyId, footerIds, cols, rowFn) {
  const [fSales, fLC, fAbs] = footerIds;
  const footer = document.getElementById(fSales).closest('.rpt-tbl-footer');
  footer.style.display = 'none';

  if (!data.length) {
    showEmpty(bodyId, 'No records for the selected filters.');
    return;
  }

  let tSales = 0, tLC = 0, tAbs = 0;
  document.getElementById(bodyId).innerHTML = data.map((r, i) => {
    tSales += r.total_sales; tLC += r.actual_lc; tAbs += r.abs_amount;
    return `<div class="rpt-data-row" style="grid-template-columns:${cols}">${rowFn(r, i)}</div>`;
  }).join('');

  document.getElementById(fSales).textContent = fmtINR(tSales);
  document.getElementById(fLC).textContent    = fmtINR(tLC);
  const absEl = document.getElementById(fAbs);
  absEl.textContent = fmtINR(tAbs);
  absEl.className   = tAbs >= 0 ? 'abs-positive' : 'abs-negative';
  footer.style.display = '';
}

async function loadCost() {
  // Show all three loading states
  ['costDivLoading','costModeLoading','costStateLoading'].forEach(id => {
    document.getElementById(id).style.display = 'flex';
  });
  ['costDivBody','costModeBody','costStateBody'].forEach(id =>
    document.getElementById(id).innerHTML = '');
  ['costDivFooter','costModeFooter','costStateFooter'].forEach(id =>
    document.getElementById(id).style.display = 'none');

  const [dDiv, dMode, dState] = await Promise.allSettled([
    _fetchCostView('Division'),
    _fetchCostView('Mode'),
    _fetchCostView('State'),
  ]);

  // Division grid
  document.getElementById('costDivLoading').style.display = 'none';
  if (dDiv.status === 'fulfilled' && dDiv.value.success) {
    _renderCostGrid(
      dDiv.value.data, 'costDivBody',
      ['ftDivSales','ftDivLC','ftDivAbs'],
      '40px 1fr 130px 130px 100px 100px 110px 140px',
      (r, i) => `
        <div>${i+1}</div><div><strong>${r.division}</strong></div>
        <div>${fmtINR(r.total_sales)}</div><div>${fmtINR(r.actual_lc)}</div>
        <div class="${pctClass(r.actual_lc_pct)}">${r.actual_lc_pct}%</div>
        <div>${r.budget_lc_pct}%</div>
        <div class="${r.diff_pct >= 0 ? 'pct-good':'pct-bad'}">${r.diff_pct}%</div>
        <div class="${r.abs_amount >= 0 ? 'abs-positive':'abs-negative'}">${fmtINR(r.abs_amount)}</div>`
    );
  } else { showEmpty('costDivBody', 'No division records found.'); }

  // Mode grid
  document.getElementById('costModeLoading').style.display = 'none';
  if (dMode.status === 'fulfilled' && dMode.value.success) {
    _renderCostGrid(
      dMode.value.data, 'costModeBody',
      ['ftModeSales','ftModeLC','ftModeAbs'],
      '40px 110px 90px 130px 130px 100px 100px 140px',
      (r, i) => `
        <div>${i+1}</div><div>${r.division}</div><div>${r.mode || '—'}</div>
        <div>${fmtINR(r.total_sales)}</div><div>${fmtINR(r.actual_lc)}</div>
        <div class="${pctClass(r.actual_lc_pct)}">${r.actual_lc_pct}%</div>
        <div>${r.budget_lc_pct}%</div>
        <div class="${r.abs_amount >= 0 ? 'abs-positive':'abs-negative'}">${fmtINR(r.abs_amount)}</div>`
    );
  } else { showEmpty('costModeBody', 'No mode records found.'); }

  // State grid
  document.getElementById('costStateLoading').style.display = 'none';
  if (dState.status === 'fulfilled' && dState.value.success) {
    _renderCostGrid(
      dState.value.data, 'costStateBody',
      ['ftStateSales','ftStateLC','ftStateAbs'],
      '40px 1fr 110px 130px 130px 100px 100px 140px',
      (r, i) => `
        <div>${i+1}</div><div>${r.state || 'Other Region'}</div><div>${r.division}</div>
        <div>${fmtINR(r.total_sales)}</div><div>${fmtINR(r.actual_lc)}</div>
        <div class="${pctClass(r.actual_lc_pct)}">${r.actual_lc_pct}%</div>
        <div>${r.budget_lc_pct}%</div>
        <div class="${r.abs_amount >= 0 ? 'abs-positive':'abs-negative'}">${fmtINR(r.abs_amount)}</div>`
    );
  } else { showEmpty('costStateBody', 'No state records found.'); }
}

// ── Courier Performance ───────────────────────────────────────────────────────

async function loadCourier() {
  const qs = buildQS({
    date_from: _v('courierFromDate'),
    date_to:   _v('courierToDate'),
    ..._tabFilters('courier'),
  });

  const footer = document.getElementById('courierFooter');
  setLoading('courierLoading', 'courierTableBody', true);
  footer.style.display = 'none';

  try {
    const d = await apiFetch(RPT_CONFIG.urls.courier + qs);
    setLoading('courierLoading', 'courierTableBody', false);

    if (!d.success || !d.data.length) {
      _courierRaw = [];
      showEmpty('courierTableBody',
        'No courier TAT records found. Records need both dispatch date and delivery date.');
      return;
    }
    _courierRaw = d.data;
    renderCourier();

  } catch (e) {
    setLoading('courierLoading', 'courierTableBody', false);
    showError('courierTableBody', e.message);
  }
}

function renderCourier() {
  const data   = _courierRaw;
  const footer = document.getElementById('courierFooter');
  footer.style.display = 'none';

  if (!data.length) {
    showEmpty('courierTableBody', 'No courier records match the current filters.');
    return;
  }

  let fD0=0, fD1=0, fD2=0, fD3=0, fD4=0, fD5=0, fGrand=0;
  const html = data.map((r, i) => {
    fD0+=r.d0; fD1+=r.d1; fD2+=r.d2; fD3+=r.d3; fD4+=r.d4; fD5+=r.d5; fGrand+=r.grand;
    const c = r.courier.replace(/'/g, "\\'");
    const m = r.mode.replace(/'/g, "\\'");
    return `
      <div class="rpt-data-row courier-head">
        <div>${i + 1}</div>
        <div>${r.month_name}</div>
        <div>${r.year}</div>
        <div><strong>${r.courier}</strong></div>
        <div style="font-size:10px;color:var(--text-muted)">${r.service_type || '—'}</div>
        <div>${r.mode}</div>
        <div class="cr-stack pct-good">${r.d0}<span class="side-pct">${r.ontime_pct}%</span></div>
        <div class="cell-link"
             onclick="loadCourierDetail('${c}',${r.month_num},${r.year},'${m}',1)">${r.d1}</div>
        <div class="cell-link"
             onclick="loadCourierDetail('${c}',${r.month_num},${r.year},'${m}',2)">${r.d2}</div>
        <div class="cell-link"
             onclick="loadCourierDetail('${c}',${r.month_num},${r.year},'${m}',3)">${r.d3}</div>
        <div class="cell-link"
             onclick="loadCourierDetail('${c}',${r.month_num},${r.year},'${m}',4)">${r.d4}</div>
        <div class="cell-link ${r.d5 > 0 ? 'pct-bad' : ''}"
             onclick="loadCourierDetail('${c}',${r.month_num},${r.year},'${m}',5)">${r.d5}</div>
        <div><strong>${r.grand}</strong></div>
        <div class="${pctClass(r.ontime_pct)}">${r.ontime_pct}%</div>
        <div class="${r.delay_pct > 20 ? 'pct-bad' : 'pct-warn'}">${r.delay_pct}%</div>
      </div>`;
  }).join('');
  document.getElementById('courierTableBody').innerHTML = html;

  const fOntime  = fGrand ? Math.round((fD0 / fGrand) * 100) : 0;
  const fDelayed = fGrand ? Math.round(((fD1+fD2+fD3+fD4+fD5) / fGrand) * 100) : 0;
  document.getElementById('ftCrD0').textContent     = fD0;
  document.getElementById('ftCrD1').textContent     = fD1;
  document.getElementById('ftCrD2').textContent     = fD2;
  document.getElementById('ftCrD3').textContent     = fD3;
  document.getElementById('ftCrD4').textContent     = fD4;
  document.getElementById('ftCrD5').textContent     = fD5;
  document.getElementById('ftCrGrand').textContent  = fGrand;
  document.getElementById('ftCrOntime').textContent = fOntime + '%';
  document.getElementById('ftCrDelay').textContent  = fDelayed + '%';
  footer.style.display = '';
}

// ── LC Contribution ───────────────────────────────────────────────────────────

// Raw LC totals kept for summary cards (not filtered — they reflect API totals)
let _lcTotals = null;

async function loadLC() {
  const qs = buildQS({
    date_from: _v('lcFromDate'),
    date_to:   _v('lcToDate'),
    ..._tabFilters('lc'),
  });

  const footer  = document.getElementById('lcFooter');
  const summary = document.getElementById('lcSummary');
  setLoading('lcLoading', 'lcTableBody', true);
  footer.style.display  = 'none';
  summary.style.display = 'none';

  try {
    const d = await apiFetch(RPT_CONFIG.urls.lc + qs);
    setLoading('lcLoading', 'lcTableBody', false);

    if (!d.success || !d.data.length) {
      _lcRaw    = [];
      _lcTotals = null;
      showEmpty('lcTableBody', 'No LC data found. Records need courier name and logistics cost.');
      return;
    }

    _lcRaw    = d.data;
    _lcTotals = d.totals;

    // Summary cards always show unfiltered API totals
    document.getElementById('lcTotalLC').textContent    = fmtINR(d.totals.total_lc);
    document.getElementById('lcTotalSales').textContent = fmtINR(d.totals.total_sales);
    document.getElementById('lcTotalWt').textContent    = fmtNum(d.totals.total_wt, 1);
    summary.style.display = 'flex';

    renderLC();

  } catch (e) {
    setLoading('lcLoading', 'lcTableBody', false);
    showError('lcTableBody', e.message);
  }
}

function renderLC() {
  const data   = _lcRaw;
  const footer = document.getElementById('lcFooter');
  footer.style.display = 'none';

  if (!data.length) {
    showEmpty('lcTableBody', 'No LC records match the current filters.');
    return;
  }

  let fSales=0, fFV=0, fBiz=0, fWt=0, fWtPct=0;
  const html = data.map((r, i) => {
    fSales+=r.sale_value; fFV+=r.lc_fv; fBiz+=r.biz_pct; fWt+=r.weight; fWtPct+=r.weight_pct;
    return `
      <div class="rpt-data-row lc-head">
        <div>${i + 1}</div>
        <div><strong>${r.courier}</strong></div>
        <div>${fmtINR(r.sale_value)}</div>
        <div>${fmtINR(r.lc_fv)}</div>
        <div>${r.biz_pct.toFixed(2)}%</div>
        <div>${r.lc_pct.toFixed(4)}</div>
        <div>${fmtNum(r.weight, 1)}</div>
        <div>${r.weight_pct.toFixed(2)}%</div>
      </div>`;
  }).join('');
  document.getElementById('lcTableBody').innerHTML = html;

  document.getElementById('ftLcSales').textContent  = fmtINR(fSales);
  document.getElementById('ftLcFV').textContent     = fmtINR(fFV);
  document.getElementById('ftLcBiz').textContent    = fBiz.toFixed(2) + '%';
  document.getElementById('ftLcWt').textContent     = fmtNum(fWt, 1);
  document.getElementById('ftLcWtPct').textContent  = fWtPct.toFixed(2) + '%';
  footer.style.display = '';
}

// ── Pincode Search ────────────────────────────────────────────────────────────

async function loadPincode() {
  const pincode = document.getElementById('pincodeInput').value.trim();
  if (!pincode) {
    showToast('Please enter a pincode to search.');
    return;
  }

  const qs = buildQS({
    pincode,
    weight:    document.getElementById('pincodeWeight').value,
    mode:      document.getElementById('pincodeMode').value,
    location:  document.getElementById('pincodeLocation').value,
    date_from: document.getElementById('pincodeDateFrom').value,
    date_to:   document.getElementById('pincodeDateTo').value,
  });

  const distEl = document.getElementById('pincodeDistrict');
  const legend = document.getElementById('pincodeLegend');
  setLoading('pincodeLoading', 'pincodeTableBody', true);
  distEl.style.display = 'none';
  legend.style.display = 'none';

  try {
    const d = await apiFetch(RPT_CONFIG.urls.pincode + qs);
    setLoading('pincodeLoading', 'pincodeTableBody', false);

    if (d.district) {
      distEl.innerHTML     = `<i class="fas fa-map-marker-alt"></i> ${d.district}`;
      distEl.style.display = 'inline-flex';
    }

    if (!d.success || !d.data.length) {
      showEmpty('pincodeTableBody', 'No couriers found for this pincode.');
      return;
    }

    legend.style.display = 'flex';

    const html = d.data.map((r, i) => {
      const amtCls = r.amt_rank === 'best' ? 'amt-best' : r.amt_rank === 'second' ? 'amt-second' : '';
      const tatCls = r.tat_rank === 'best' ? 'tat-best' : r.tat_rank === 'second' ? 'tat-second' : '';
      return `
        <div class="rpt-data-row pincode-head">
          <div>${i + 1}</div>
          <div>
            <strong>${r.courier_name}</strong>
            ${r.service_type ? `<br><span style="font-size:10px;color:var(--text-muted)">${r.service_type}</span>` : ''}
          </div>
          <div>${fmtNum(r.rate, 2)}</div>
          <div>${fmtNum(r.min_weight, 2)} kg</div>
          <div class="${amtCls}">${fmtINR(r.amount)}</div>
          <div class="${tatCls}"><strong>${r.tat}</strong> day${r.tat !== 1 ? 's' : ''}</div>
          <div>${fmtINR(r.biz_alloc)}</div>
          <div>
            <span style="font-size:10px;padding:2px 8px;border-radius:4px;font-weight:600;
              background:${r.oda_nonoda === 'ODA' ? '#fef3c7' : '#f1f5f9'};
              color:${r.oda_nonoda === 'ODA' ? '#92400e' : '#475569'}">
              ${r.oda_nonoda || '—'}
            </span>
          </div>
          <div>${r.mode || '—'}</div>
          <div>${r.zone || '—'}</div>
        </div>`;
    }).join('');
    document.getElementById('pincodeTableBody').innerHTML = html;

  } catch (e) {
    setLoading('pincodeLoading', 'pincodeTableBody', false);
    showError('pincodeTableBody', e.message);
  }
}

// ── Export ────────────────────────────────────────────────────────────────────

function exportReport(tab) {
  const urlMap = {
    tat:     RPT_CONFIG.urls.tat_export,
    cost:    RPT_CONFIG.urls.cost_export,
    courier: RPT_CONFIG.urls.courier_export,
    lc:      RPT_CONFIG.urls.lc_export,
  };
  if (!urlMap[tab]) return;

  let qs = '';
  if (tab === 'tat') {
    qs = buildQS({
      group_by:  tatGroup(),
      date_from: _v('tatFromDate'),
      date_to:   _v('tatToDate'),
      ..._tabFilters('tat'),
    });
  } else if (tab === 'cost') {
    qs = _costQS();
  } else if (tab === 'courier') {
    qs = buildQS({
      date_from: _v('courierFromDate'),
      date_to:   _v('courierToDate'),
      ..._tabFilters('courier'),
    });
  } else if (tab === 'lc') {
    qs = buildQS({
      date_from: _v('lcFromDate'),
      date_to:   _v('lcToDate'),
      ..._tabFilters('lc'),
    });
  }
  window.location = urlMap[tab] + qs;
}

// ── Detail Panel ──────────────────────────────────────────────────────────────

function openDetailPanel(title) {
  document.getElementById('detailPanelTitle').innerHTML = `<i class="fas fa-list-alt"></i> ${title}`;
  document.getElementById('detailPanelBody').innerHTML =
    '<div class="loading-overlay" style="display:flex"><div class="loading-dots"><span></span><span></span><span></span></div><span>Loading detail records…</span></div>';
  document.getElementById('detailPanelOverlay').classList.add('open');
  document.body.style.overflow = 'hidden';
}

function closeDetailPanel() {
  document.getElementById('detailPanelOverlay').classList.remove('open');
  document.body.style.overflow = '';
}

function renderDetailTable(data) {
  if (!data || !data.length) {
    document.getElementById('detailPanelBody').innerHTML =
      '<div class="empty-state"><div class="empty-state-icon"><i class="fas fa-search"></i></div><p>No records found.</p></div>';
    return;
  }

  const cols = [
    { k: 'billing_date',             h: 'Bill Date'   },
    { k: 'billing_doc',              h: 'Bill Doc'    },
    { k: 'division',                 h: 'Division'    },
    { k: 'location',                 h: 'Location'    },
    { k: 'plant_name',               h: 'Plant'       },
    { k: 'shipto_name',              h: 'Ship To'     },
    { k: 'shipto_city',              h: 'City'        },
    { k: 'shipto_region',            h: 'Region'      },
    { k: 'zone',                     h: 'Zone'        },
    { k: 'courier_name',             h: 'Courier'     },
    { k: 'docket_no',                h: 'Docket No'   },
    { k: 'mode',                     h: 'Mode'        },
    { k: 'dispatch_date',            h: 'Dispatch'    },
    { k: 'expected_delivery_date',   h: 'EDD'         },
    { k: 'delivery_date',            h: 'Delivered'   },
    { k: 'dispatch_vs_edd_tat',      h: 'EDD TAT'     },
    { k: 'dispatch_vs_delivery_tat', h: 'Actual TAT'  },
    { k: 'remarks',                  h: 'Remarks'     },
  ];

  const thead = `<thead><tr><th class="detail-sno">#</th>${cols.map(c => `<th>${c.h}</th>`).join('')}</tr></thead>`;
  const tbody = data.map((r, i) => {
    const edd = r.dispatch_vs_edd_tat || 0;
    const del = r.dispatch_vs_delivery_tat;
    const delCls = (del !== null && del !== undefined)
      ? (del <= edd ? 'tat-met' : 'tat-miss') : '';
    return `<tr><td class="detail-sno">${i + 1}</td>${cols.map(c => {
      const v = (r[c.k] !== null && r[c.k] !== undefined) ? r[c.k] : '—';
      if (c.k === 'dispatch_vs_delivery_tat') return `<td class="${delCls}">${v}</td>`;
      return `<td>${v}</td>`;
    }).join('')}</tr>`;
  }).join('');

  document.getElementById('detailPanelBody').innerHTML =
    `<table class="detail-tbl">${thead}<tbody>${tbody}</tbody></table>`;
}

async function loadTATDetail(label, result) {
  const resultLabel = result === 'hit' ? 'Met' : 'Not Met';
  openDetailPanel(`${tatGroup()}: ${label} — ${resultLabel} Records`);
  const qs = buildQS({
    group_by:  tatGroup(),
    label,
    result,
    date_from: _v('tatFromDate'),
    date_to:   _v('tatToDate'),
    ..._tabFilters('tat'),
  });
  try {
    const d = await apiFetch(RPT_CONFIG.urls.tat_detail + qs);
    renderDetailTable(d.success ? d.data : []);
  } catch (e) {
    document.getElementById('detailPanelBody').innerHTML =
      `<div class="empty-state"><div class="empty-state-icon"><i class="fas fa-exclamation-triangle"></i></div><p>Error: ${e.message}</p></div>`;
  }
}

async function loadCourierDetail(courier, month, year, mode, bucket) {
  const bucketNames = { 0: 'On Time', 1: '+1 Day Delay', 2: '+2 Day Delay',
                        3: '+3 Day Delay', 4: '+4 Day Delay', 5: '+5 Day Delay' };
  openDetailPanel(`${courier} — ${bucketNames[bucket] || 'Delay'} Records (${month}/${year})`);
  const qs = buildQS({
    courier, month, year, mode, bucket,
    date_from: _v('courierFromDate'),
    date_to:   _v('courierToDate'),
    division:  _v('courierDivision'),
    plant:     _v('courierPlant'),
  });
  try {
    const d = await apiFetch(RPT_CONFIG.urls.courier_detail + qs);
    renderDetailTable(d.success ? d.data : []);
  } catch (e) {
    document.getElementById('detailPanelBody').innerHTML =
      `<div class="empty-state"><div class="empty-state-icon"><i class="fas fa-exclamation-triangle"></i></div><p>Error: ${e.message}</p></div>`;
  }
}

// ── Quick Date-Range Presets ──────────────────────────────────────────────────

function _ymd(d) {
  // Format Date object as YYYY-MM-DD (local, not UTC, to avoid timezone bugs)
  const y = d.getFullYear();
  const m = String(d.getMonth() + 1).padStart(2, '0');
  const dd = String(d.getDate()).padStart(2, '0');
  return `${y}-${m}-${dd}`;
}

window.setDateRange = function (preset) {
  let from = '', to = '';
  const today = new Date();

  if (preset !== 'clear') {
    to = _ymd(today);
    let fromDate;
    switch (preset) {
      case 'today':   fromDate = today; break;
      case '7d':      fromDate = new Date(today); fromDate.setDate(fromDate.getDate() - 6); break;
      case 'month':   fromDate = new Date(today.getFullYear(), today.getMonth(), 1); break;
      case '30d':     fromDate = new Date(today); fromDate.setDate(fromDate.getDate() - 29); break;
      case 'quarter': fromDate = new Date(today.getFullYear(), Math.floor(today.getMonth() / 3) * 3, 1); break;
      case 'ytd':     fromDate = new Date(today.getFullYear(), 0, 1); break;
      default: return;
    }
    from = _ymd(fromDate);
  }

  // Apply to ALL data tabs' date inputs
  ['tat', 'cost', 'courier', 'lc'].forEach(p => {
    const fEl = document.getElementById(p + 'FromDate');
    const tEl = document.getElementById(p + 'ToDate');
    if (fEl) fEl.value = from;
    if (tEl) tEl.value = to;
  });
  // Update cost tab's progressive reveal
  onCostDateChange();

  // Highlight the active preset
  document.querySelectorAll('.rpt-preset-pill').forEach(b => b.classList.remove('active'));
  if (preset !== 'clear') {
    const evtBtn = event && event.target && event.target.closest && event.target.closest('.rpt-preset-pill');
    if (evtBtn) evtBtn.classList.add('active');
  }

  // Mark non-active tabs as needing reload, then reload current tab
  const activeEl = document.querySelector('.rpt-tab-content.active');
  const activeKey = activeEl ? activeEl.id.replace('tab-', '') : '';
  Object.keys(_loaded).forEach(k => { if (k !== activeKey) _loaded[k] = false; });

  const reloadMap = { tat: loadTAT, cost: loadCost, courier: loadCourier, lc: loadLC };
  if (reloadMap[activeKey]) reloadMap[activeKey]();
};

// ── Filter Sync Across Tabs ───────────────────────────────────────────────────
// When the user changes a date on one tab, propagate to all tabs and mark
// inactive tabs as needing reload (so switching to them refetches fresh).

function _setupFilterSync() {
  const groups = {
    FromDate: ['tatFromDate', 'costFromDate', 'courierFromDate', 'lcFromDate'],
    ToDate:   ['tatToDate',   'costToDate',   'courierToDate',   'lcToDate'],
  };

  Object.values(groups).forEach(ids => {
    ids.forEach(srcId => {
      const srcEl = document.getElementById(srcId);
      if (!srcEl) return;
      srcEl.addEventListener('change', () => {
        let costUpdated = false;
        ids.forEach(dstId => {
          if (dstId === srcId) return;
          const dstEl = document.getElementById(dstId);
          if (dstEl && dstEl.value !== srcEl.value) {
            dstEl.value = srcEl.value;
            if (dstId === 'costFromDate' || dstId === 'costToDate') costUpdated = true;
          }
        });
        if (costUpdated) onCostDateChange();
        // Mark other tabs as dirty
        const activeEl = document.querySelector('.rpt-tab-content.active');
        const activeKey = activeEl ? activeEl.id.replace('tab-', '') : '';
        Object.keys(_loaded).forEach(k => { if (k !== activeKey) _loaded[k] = false; });
      });
    });
  });
}

// ── Pincode auto-search (debounced) ───────────────────────────────────────────
let _pincodeDebounce;
window.onPincodeInput = function (el) {
  clearTimeout(_pincodeDebounce);
  const v = (el.value || '').trim();
  if (v.length >= 6) {
    _pincodeDebounce = setTimeout(() => loadPincode(), 450);
  }
};

// ── Init ──────────────────────────────────────────────────────────────────────

document.addEventListener('DOMContentLoaded', () => {
  initDropdowns();
  // cost headers are now static in the template — no init needed
  loadStats();
  loadTAT();                         // auto-load first tab immediately
  _loaded.tat = true;

  _setupFilterSync();

  // Esc closes detail panel
  document.addEventListener('keydown', e => {
    if (e.key === 'Escape') closeDetailPanel();
  });
});
