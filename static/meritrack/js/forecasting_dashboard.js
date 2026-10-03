const API = '/meritrack/api';
const headers = {'X-CSRFToken': csrfToken, 'Content-Type': 'application/json'};

function getHospitalId() { return document.getElementById('hospitalFilter').value; }

function confidenceBar(level) {
    const n = level === 'high' ? 5 : level === 'medium' ? 3 : 1;
    return `<div class="confidence-bar">${[1,2,3,4,5].map(i =>
        `<div class="seg ${i<=n?'filled':''}"></div>`).join('')}</div>
        <span style="font-size:0.75em;color:var(--mt-gray-600);margin-left:4px">${level}</span>`;
}

function trendBadge(t) {
    if (t === 'increasing') return '<span class="badge badge-green"><i class="fas fa-arrow-up"></i> Up</span>';
    if (t === 'decreasing') return '<span class="badge badge-red"><i class="fas fa-arrow-down"></i> Down</span>';
    return '<span class="badge badge-gray"><i class="fas fa-minus"></i> Stable</span>';
}

async function loadAlerts() {
    const hid = getHospitalId();
    const url = hid ? `${API}/forecasts/reorder-alerts/?hospital_id=${hid}` : `${API}/forecasts/reorder-alerts/`;
    try {
        const res = await fetch(url, {headers});
        const data = await res.json();
        const alerts = data.results || data;

        document.getElementById('alertCount').textContent = alerts.length;

        if (!alerts.length) {
            document.getElementById('reorderAlerts').innerHTML =
                '<div style="text-align:center;padding:30px;color:var(--mt-success)"><i class="fas fa-check-circle" style="font-size:2em;display:block;margin-bottom:8px"></i>No reorder alerts — stock levels are healthy</div>';
            return;
        }

        document.getElementById('reorderAlerts').innerHTML = alerts.slice(0, 20).map(a => {
            const urgency = a.days_of_stock_remaining < 7 ? 'critical' : a.days_of_stock_remaining < 30 ? 'warning' : 'info';
            const icon = urgency === 'critical' ? 'fa-exclamation-circle' : urgency === 'warning' ? 'fa-exclamation-triangle' : 'fa-info-circle';
            const iconColor = urgency === 'critical' ? 'var(--mt-danger)' : urgency === 'warning' ? 'var(--mt-warning)' : 'var(--mt-info)';
            return `<div class="alert-card ${urgency}">
                <div class="alert-icon" style="color:${iconColor}"><i class="fas ${icon}"></i></div>
                <div class="alert-content">
                    <div class="title">${a.product_name || 'Product'} @ ${a.hospital_name || 'Hospital'}</div>
                    <div class="detail">Stock: ${a.current_stock} | Reorder: ${a.recommended_reorder} | ~${a.days_of_stock_remaining || '?'} days remaining</div>
                </div>
                <span class="badge badge-${urgency === 'critical' ? 'red' : urgency === 'warning' ? 'yellow' : 'blue'}">${a.days_of_stock_remaining || '?'}d</span>
            </div>`;
        }).join('');
    } catch(e) {
        document.getElementById('reorderAlerts').innerHTML = '<div style="color:var(--mt-danger);text-align:center;padding:20px">Error loading alerts</div>';
    }
}

async function loadForecasts() {
    const hid = getHospitalId();
    const url = hid ? `${API}/forecasts/?hospital_id=${hid}` : `${API}/forecasts/`;
    try {
        const res = await fetch(url, {headers});
        const data = await res.json();
        const forecasts = data.results || data;
        const tbody = document.getElementById('forecastBody');

        if (!forecasts.length) {
            tbody.innerHTML = '<tr><td colspan="9" style="text-align:center;padding:30px;color:var(--mt-gray-400)">No forecasts available. Click "Generate Forecasts" to create them.</td></tr>';
            return;
        }

        tbody.innerHTML = forecasts.map(f => `<tr>
            <td>${f.hospital_name || '-'}</td>
            <td><strong>${f.product_name || '-'}</strong></td>
            <td>${f.period_start ? f.period_start.slice(0,7) : '-'}</td>
            <td><strong>${f.predicted_usage}</strong></td>
            <td>${f.current_stock ?? '-'}</td>
            <td style="color:${f.recommended_reorder > 0 ? 'var(--mt-danger)' : 'var(--mt-gray-600)'};font-weight:700">${f.recommended_reorder}</td>
            <td>${f.safety_stock}</td>
            <td>${trendBadge(f.trend_direction)}</td>
            <td>${confidenceBar(f.confidence_level)}</td>
        </tr>`).join('');
    } catch(e) {
        document.getElementById('forecastBody').innerHTML = '<tr><td colspan="9" style="color:var(--mt-danger);text-align:center">Error loading forecasts</td></tr>';
    }
}

async function loadLowStock() {
    const hid = getHospitalId();
    const url = hid ? `${API}/forecasts/low-stock/?hospital_id=${hid}` : `${API}/forecasts/low-stock/`;
    try {
        const res = await fetch(url, {headers});
        const data = await res.json();
        const items = data.results || data;
        const el = document.getElementById('lowStock');

        if (!items.length) {
            el.innerHTML = '<div style="text-align:center;padding:20px;color:var(--mt-success)">No low stock items</div>';
            return;
        }

        const max = Math.max(...items.map(i => i.current_stock || 0), 1);
        el.innerHTML = items.slice(0, 15).map(i => {
            const pct = (i.current_stock / max * 100);
            const color = i.current_stock < (i.safety_stock || 5) ? 'var(--mt-danger)' : i.current_stock < (i.safety_stock || 5) * 2 ? 'var(--mt-warning)' : 'var(--mt-success)';
            return `<div class="bar-row">
                <span class="bar-label">${i.product_name || 'Unknown'}</span>
                <div class="bar-track"><div class="bar-fill" style="width:${pct}%;background:${color}">${i.current_stock}</div></div>
                <span class="bar-count" style="color:${color}">${i.current_stock}</span>
            </div>`;
        }).join('');
    } catch(e) {
        document.getElementById('lowStock').innerHTML = '<div style="color:var(--mt-danger);text-align:center">Error loading data</div>';
    }
}

async function generateForecasts() {
    const hid = getHospitalId();
    try {
        const url = hid ? `${API}/analytics/demand-forecast/?hospital_id=${hid}` : `${API}/analytics/demand-forecast/`;
        const res = await fetch(url, {headers});
        const data = await res.json();
        alert(`Generated ${data.forecasts_generated || 0} forecasts`);
        loadAll();
    } catch(e) {
        alert('Error generating forecasts');
    }
}

function loadAll() {
    loadAlerts();
    loadForecasts();
    loadLowStock();
}

document.addEventListener('DOMContentLoaded', loadAll);
