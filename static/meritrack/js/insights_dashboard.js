const API = '/meritrack/api';
const headers = {'X-CSRFToken': csrfToken, 'Content-Type': 'application/json'};

function showTab(id) {
    document.querySelectorAll('.tab-content').forEach(t => t.classList.remove('active'));
    document.querySelectorAll('.mt-pill-tabs .mt-pill-tab').forEach(b => b.classList.remove('active'));
    document.getElementById('tab-' + id).classList.add('active');
    event.target.classList.add('active');
    refreshData();
}

function getFilters() {
    return {
        hospital_id: document.getElementById('hospitalFilter').value,
        months: document.getElementById('monthsFilter').value,
    };
}

async function fetchJSON(url) {
    const res = await fetch(url, {headers});
    return res.json();
}

function barChart(container, items, labelKey, countKey, color='var(--mt-primary)') {
    const el = document.getElementById(container);
    if (!items || !items.length) { el.innerHTML = '<div style="color:var(--mt-gray-400);text-align:center;padding:20px">No data available</div>'; return; }
    const max = Math.max(...items.map(i => i[countKey] || 0));
    el.innerHTML = items.slice(0, 15).map(i => {
        const pct = max > 0 ? ((i[countKey] || 0) / max * 100) : 0;
        return `<div class="bar-row">
            <span class="bar-label">${i[labelKey] || 'N/A'}</span>
            <div class="bar-track"><div class="bar-fill" style="width:${pct}%;background:${color}"></div></div>
            <span class="bar-count">${i[countKey] || 0}</span>
        </div>`;
    }).join('');
}

function statCard(icon, bg, value, label, cls='') {
    return `<div class="stat-card ${cls}">
        <div class="stat-icon" style="background:${bg}"><i class="fas ${icon}"></i></div>
        <div class="stat-value">${value}</div>
        <div class="stat-label">${label}</div>
    </div>`;
}

async function loadExecutive() {
    const data = await fetchJSON(`${API}/analytics/executive-dashboard/`);
    if (!data.metrics) return;
    const m = data.metrics;
    document.getElementById('execMetrics').innerHTML = [
        statCard('fa-hospital', 'var(--mt-primary)', m.total_hospitals, 'Active Hospitals'),
        statCard('fa-boxes', 'var(--mt-success)', m.total_on_shelf, 'Products on Shelf'),
        statCard('fa-times-circle', 'var(--mt-danger)', m.total_expired, 'Expired on Shelf', 'danger'),
        statCard('fa-clock', 'var(--mt-warning)', m.expiring_30d, 'Expiring in 30 Days', 'warning'),
        statCard('fa-check-circle', 'var(--mt-purple)', m.used_ytd, 'Used YTD'),
        statCard('fa-chart-line', 'var(--mt-secondary)', m.used_last_30d, 'Used Last 30 Days'),
        statCard('fa-bell', 'var(--mt-orange)', m.pending_alerts, 'Pending Alerts', m.pending_alerts > 0 ? 'warning' : ''),
        statCard('fa-shopping-cart', 'var(--mt-purple)', m.pending_orders, 'Pending Orders'),
    ].join('');

    // Usage trend
    const trendEl = document.getElementById('usageTrend');
    const labelsEl = document.getElementById('usageTrendLabels');
    if (data.usage_trend && data.usage_trend.length) {
        const max = Math.max(...data.usage_trend.map(t => t.count), 1);
        trendEl.innerHTML = data.usage_trend.map(t => {
            const h = Math.max(4, (t.count / max) * 100);
            return `<div class="trend-bar" style="height:${h}%;background:var(--mt-primary)"><div class="tip">${t.month}: ${t.count}</div></div>`;
        }).join('');
        labelsEl.innerHTML = data.usage_trend.map(t =>
            `<span style="flex:1;text-align:center;font-size:0.65em;color:var(--mt-gray-400)">${t.month.slice(5)}</span>`
        ).join('');
    }

    // Top hospitals
    barChart('topHospitals', data.top_hospitals, 'name', 'count', 'var(--mt-primary)');

    // Alert distribution
    if (data.alert_distribution) {
        const alertEl = document.getElementById('alertDist');
        const tierColors = {'6m':'var(--mt-success)','3m':'var(--mt-warning)','1m':'var(--mt-orange)','1w':'var(--mt-danger)','expired':'var(--mt-danger)'};
        const tierLabels = {'6m':'6 Months','3m':'3 Months','1m':'1 Month','1w':'1 Week','expired':'Expired'};
        alertEl.innerHTML = Object.entries(data.alert_distribution).map(([k,v]) =>
            `<div class="bar-row"><span class="bar-label">${tierLabels[k]||k}</span>
            <div class="bar-track"><div class="bar-fill" style="width:${Math.min(100,v*5)}%;background:${tierColors[k]||'var(--mt-gray-600)'}">${v}</div></div>
            <span class="bar-count">${v}</span></div>`
        ).join('');
    }

    // Value at risk
    document.getElementById('valueAtRisk').innerHTML = `
        <div style="font-size:2.2em;font-weight:800;color:var(--mt-danger)">€${(data.inventory_value_at_risk||0).toLocaleString()}</div>
        <div style="font-size:0.9em;color:var(--mt-gray-600);margin-top:8px">Products expiring within 90 days</div>`;
}

async function loadHealthScores() {
    const f = getFilters();
    const data = await fetchJSON(`${API}/analytics/inventory-health/?hospital_id=${f.hospital_id}`);
    if (!data) return;

    const cls = data.overall_health_score >= 70 ? 'good' : data.overall_health_score >= 40 ? 'warning' : 'critical';
    document.getElementById('healthOverview').innerHTML = `
        <div class="health-score ${cls}">${data.overall_health_score}</div>
        <div style="font-size:0.9em;color:var(--mt-gray-600)">Overall Inventory Health Score (0-100)</div>
        <div style="margin-top:8px">
            <span class="badge badge-green">${data.healthy_count} Healthy</span>
            <span class="badge badge-yellow">${data.warning_count} Warning</span>
            <span class="badge badge-red">${data.critical_count} Critical</span>
        </div>`;

    const container = document.getElementById('hospitalHealthCards');
    container.innerHTML = (data.hospitals || []).map(h => {
        const hcls = h.health_score >= 70 ? 'good' : h.health_score >= 40 ? 'warning' : 'critical';
        return `<div class="kpi-card">
            <h4>${h.hospital_name} <span class="badge badge-${hcls === 'good' ? 'green' : hcls === 'warning' ? 'yellow' : 'red'}">${h.health_score}</span></h4>
            <div style="display:grid;grid-template-columns:1fr 1fr;gap:8px;font-size:0.85em">
                <div><strong>${h.total_on_shelf}</strong> On Shelf</div>
                <div style="color:var(--mt-danger)"><strong>${h.expired_on_shelf}</strong> Expired</div>
                <div style="color:var(--mt-warning)"><strong>${h.expiring_30d}</strong> Exp 30d</div>
                <div><strong>${h.used_ytd}</strong> Used YTD</div>
                <div>Turnover: <strong>${h.turnover_rate}x</strong></div>
                <div>Value: <strong>€${(h.inventory_value||0).toLocaleString()}</strong></div>
            </div>
        </div>`;
    }).join('');
}

async function loadVelocity() {
    const f = getFilters();
    const data = await fetchJSON(`${API}/analytics/product-velocity/?hospital_id=${f.hospital_id}&months=${f.months}`);
    if (!data || !data.summary) return;
    const s = data.summary;

    document.getElementById('velocitySummary').innerHTML = [
        statCard('fa-bolt', 'var(--mt-success)', s.fast, 'Fast Moving'),
        statCard('fa-tachometer-alt', 'var(--mt-warning)', s.medium, 'Medium Moving'),
        statCard('fa-hourglass-half', 'var(--mt-orange)', s.slow, 'Slow Moving'),
        statCard('fa-ban', 'var(--mt-danger)', s.dead, 'Dead Stock', 'danger'),
    ].join('');

    // Velocity bar
    const total = s.total_products || 1;
    document.getElementById('velocityChart').innerHTML = `
        <div class="velocity-bar">
            <div class="seg" style="width:${s.fast/total*100}%;background:var(--mt-success)">${s.fast}</div>
            <div class="seg" style="width:${s.medium/total*100}%;background:var(--mt-warning)">${s.medium}</div>
            <div class="seg" style="width:${s.slow/total*100}%;background:var(--mt-orange)">${s.slow}</div>
            <div class="seg" style="width:${s.dead/total*100}%;background:var(--mt-danger)">${s.dead}</div>
        </div>
        <div style="display:flex;gap:16px;justify-content:center;margin-top:12px;font-size:0.82em">
            <span><span style="display:inline-block;width:12px;height:12px;border-radius:50%;background:var(--mt-success);margin-right:4px"></span>Fast</span>
            <span><span style="display:inline-block;width:12px;height:12px;border-radius:50%;background:var(--mt-warning);margin-right:4px"></span>Medium</span>
            <span><span style="display:inline-block;width:12px;height:12px;border-radius:50%;background:var(--mt-orange);margin-right:4px"></span>Slow</span>
            <span><span style="display:inline-block;width:12px;height:12px;border-radius:50%;background:var(--mt-danger);margin-right:4px"></span>Dead</span>
        </div>`;

    // Table
    const tbody = document.querySelector('#velocityTable tbody');
    const velColors = {fast:'green',medium:'yellow',slow:'orange',dead:'red'};
    tbody.innerHTML = (data.products || []).map(p =>
        `<tr><td>${p.product_name||'N/A'}</td><td>${p.material_code||''}</td>
        <td>${p.total_used}</td><td>${p.monthly_rate}/mo</td>
        <td><span class="badge badge-${velColors[p.velocity]||'gray'}">${p.velocity}</span></td></tr>`
    ).join('');
}

async function loadExpiryRisk() {
    const f = getFilters();
    const data = await fetchJSON(`${API}/analytics/expiry-risk/?hospital_id=${f.hospital_id}`);
    if (!data || !data.tiers) return;
    const t = data.tiers;

    const tierCards = [
        {k:'expired', icon:'fa-times-circle', bg:'var(--mt-danger)', label:'Expired'},
        {k:'1_week', icon:'fa-exclamation-circle', bg:'var(--mt-danger)', label:'1 Week'},
        {k:'1_month', icon:'fa-exclamation-triangle', bg:'var(--mt-orange)', label:'1 Month'},
        {k:'3_months', icon:'fa-clock', bg:'var(--mt-warning)', label:'3 Months'},
        {k:'6_months', icon:'fa-hourglass-half', bg:'var(--mt-success)', label:'6 Months'},
        {k:'safe', icon:'fa-check-circle', bg:'var(--mt-success-dark)', label:'Safe'},
    ];
    document.getElementById('expiryTiers').innerHTML = tierCards.map(tc => {
        const val = t[tc.k] || {count:0, value:0};
        return statCard(tc.icon, tc.bg, val.count, `${tc.label} (€${(val.value||0).toLocaleString()})`,
            ['expired','1_week'].includes(tc.k) ? 'danger' : '');
    }).join('');

    barChart('expiryByHospital', data.by_hospital, 'department__hospital__name', 'total_at_risk', 'var(--mt-danger)');
    barChart('expiryByCategory', data.by_category, 'catalog_product__category', 'count', 'var(--mt-warning)');
}

async function loadUsagePatterns() {
    const f = getFilters();
    const data = await fetchJSON(`${API}/analytics/usage-patterns/?hospital_id=${f.hospital_id}&months=${f.months}`);
    if (!data) return;

    document.getElementById('usageStats').innerHTML = [
        statCard('fa-chart-bar', 'var(--mt-primary)', data.total_usage, 'Total Usage'),
        statCard('fa-calendar', 'var(--mt-purple)', data.avg_monthly, 'Avg Monthly'),
    ].join('');

    barChart('topSurgeons', data.by_surgeon, 'surgeon_name', 'count', 'var(--mt-primary)');
    barChart('topProcedures', data.by_procedure, 'procedure_type', 'count', 'var(--mt-secondary)');
    barChart('topProducts', data.by_product, 'product__product_name', 'count', 'var(--mt-purple)');
}

async function loadSAPMovements() {
    const f = getFilters();
    const data = await fetchJSON(`${API}/analytics/sap-insights/?months=${f.months}`);
    if (!data) return;

    if (data.ratios) {
        const r = data.ratios;
        document.getElementById('sapRatios').innerHTML = [
            statCard('fa-arrow-down', 'var(--mt-success)', r.fillup_count, 'Fill-ups (631)'),
            statCard('fa-stethoscope', 'var(--mt-primary)', r.usage_count, 'Used (633)'),
            statCard('fa-undo', 'var(--mt-warning)', r.return_count, 'Returns (632)'),
            statCard('fa-percentage', 'var(--mt-purple)', r.usage_to_fillup_ratio + 'x', 'Usage/Fillup Ratio'),
        ].join('');
    }

    barChart('movementTypes', data.movement_types, 'description', 'count', 'var(--mt-primary)');
    barChart('plantActivity', data.plants, 'plant', 'count', 'var(--mt-success)');
    barChart('topCustomers', data.top_customers, 'customer_name', 'count', 'var(--mt-purple)');
}

async function refreshData() {
    const activeTab = document.querySelector('.tab-content.active');
    if (!activeTab) return;
    const id = activeTab.id.replace('tab-', '');

    switch(id) {
        case 'overview': await loadExecutive(); break;
        case 'hospital-health': await loadHealthScores(); break;
        case 'velocity': await loadVelocity(); break;
        case 'expiry-risk': await loadExpiryRisk(); break;
        case 'usage-patterns': await loadUsagePatterns(); break;
        case 'sap-movements': await loadSAPMovements(); break;
    }
}

document.addEventListener('DOMContentLoaded', loadExecutive);
