const API = '/meritrack/api';
const headers = {'X-CSRFToken': csrfToken, 'Content-Type': 'application/json'};

function statCard(icon, bg, value, label) {
    return `<div class="stat-card">
        <div class="stat-icon" style="background:${bg}"><i class="fas ${icon}"></i></div>
        <div class="stat-value">${value}</div>
        <div class="stat-label">${label}</div>
    </div>`;
}

function barChart(container, items, labelKey, countKey, color='var(--mt-primary)') {
    const el = document.getElementById(container);
    if (!items || !items.length) { el.innerHTML = '<div style="color:var(--mt-gray-400);text-align:center;padding:20px">No data</div>'; return; }
    const max = Math.max(...items.map(i => i[countKey] || 0), 1);
    el.innerHTML = items.slice(0, 12).map(i => `<div class="bar-row">
        <span class="bar-label">${i[labelKey] || 'N/A'}</span>
        <div class="bar-track"><div class="bar-fill" style="width:${(i[countKey]||0)/max*100}%;background:${color}">${i[countKey]||0}</div></div>
        <span class="bar-count">${i[countKey]||0}</span>
    </div>`).join('');
}

async function loadSyncStatus() {
    try {
        const res = await fetch(`${API}/sap-logs/`, {headers});
        const data = await res.json();
        const logs = data.results || data;

        const recent = logs.length ? logs[0] : null;
        const statusEl = document.getElementById('syncStatus');

        if (!recent) {
            statusEl.className = 'sync-status warning';
            statusEl.innerHTML = `<div class="sync-dot"></div><div>
                <div style="font-weight:700;font-size:0.9em">No SAP data imported yet</div>
                <div style="font-size:0.82em;color:var(--mt-gray-600)">Upload SAP export files or configure automatic sync</div>
            </div>`;
        } else if (recent.status === 'completed') {
            statusEl.className = 'sync-status synced';
            statusEl.innerHTML = `<div class="sync-dot"></div><div>
                <div style="font-weight:700;font-size:0.9em;color:var(--mt-success-dark)">Last sync: ${new Date(recent.completed_at || recent.started_at).toLocaleString()}</div>
                <div style="font-size:0.82em;color:var(--mt-gray-700)">${recent.records_processed} records processed — ${recent.operation_type.replace(/_/g,' ')}</div>
            </div>`;
        } else if (recent.status === 'failed') {
            statusEl.className = 'sync-status error';
            statusEl.innerHTML = `<div class="sync-dot"></div><div>
                <div style="font-weight:700;font-size:0.9em;color:var(--mt-danger-dark)">Last sync failed</div>
                <div style="font-size:0.82em;color:var(--mt-gray-700)">${recent.error_message || 'Unknown error'}</div>
            </div>`;
        }

        // Stats
        const total = logs.length;
        const successful = logs.filter(l => l.status === 'completed').length;
        const failed = logs.filter(l => l.status === 'failed').length;
        const totalRecords = logs.reduce((s, l) => s + (l.records_processed || 0), 0);

        document.getElementById('sapStats').innerHTML = [
            statCard('fa-database', 'var(--mt-primary)', total, 'Total Syncs'),
            statCard('fa-check-circle', 'var(--mt-success)', successful, 'Successful'),
            statCard('fa-times-circle', 'var(--mt-danger)', failed, 'Failed'),
            statCard('fa-file-alt', 'var(--mt-purple)', totalRecords.toLocaleString(), 'Records Processed'),
        ].join('');

        // Logs table
        const tbody = document.getElementById('logsBody');
        if (!logs.length) {
            tbody.innerHTML = '<tr><td colspan="6" style="text-align:center;padding:30px;color:var(--mt-gray-400)">No integration logs yet</td></tr>';
            return;
        }

        const statusBadge = {completed:'green', failed:'red', in_progress:'yellow', pending:'gray'};
        tbody.innerHTML = logs.slice(0, 50).map(l => `<tr>
            <td>${new Date(l.started_at).toLocaleString()}</td>
            <td>${(l.operation_type||'').replace(/_/g,' ')}</td>
            <td><span class="badge badge-${statusBadge[l.status]||'gray'}">${l.status}</span></td>
            <td>${l.records_processed || 0}</td>
            <td>${l.duration_seconds ? l.duration_seconds.toFixed(1) + 's' : '-'}</td>
            <td style="font-size:0.8em;color:var(--mt-gray-600)">${l.error_message || ''}</td>
        </tr>`).join('');
    } catch(e) {
        document.getElementById('syncStatus').innerHTML = '<div style="color:var(--mt-danger)">Error loading status</div>';
    }
}

async function loadSAPInsights() {
    try {
        const res = await fetch(`${API}/analytics/sap-insights/`, {headers});
        const data = await res.json();
        if (data.movement_types) barChart('movementDist', data.movement_types, 'description', 'count', 'var(--mt-primary)');
        if (data.top_customers) barChart('hospitalActivity', data.top_customers, 'customer_name', 'count', 'var(--mt-success)');
    } catch(e) {}
}

async function runReconciliation() {
    try {
        const res = await fetch(`${API}/analytics/sap-insights/`, {headers});
        const data = await res.json();

        const card = document.getElementById('reconCard');
        card.style.display = 'block';
        const resultsEl = document.getElementById('reconResults');

        if (data.reconciliation && data.reconciliation.length) {
            resultsEl.innerHTML = `
                <div class="recon-row header">
                    <div>Hospital</div><div>SAP Stock</div><div>App Stock</div><div>Difference</div><div>Status</div>
                </div>
                ${data.reconciliation.map(r => {
                    const diff = (r.app_stock || 0) - (r.sap_stock || 0);
                    const cls = diff === 0 ? 'zero' : diff > 0 ? 'positive' : 'negative';
                    return `<div class="recon-row">
                        <div>${r.hospital_name}</div>
                        <div>${r.sap_stock || 0}</div>
                        <div>${r.app_stock || 0}</div>
                        <div class="diff ${cls}">${diff > 0 ? '+' : ''}${diff}</div>
                        <div>${diff === 0 ? '<span class="badge badge-green">Match</span>' : '<span class="badge badge-yellow">Mismatch</span>'}</div>
                    </div>`;
                }).join('')}`;
        } else {
            resultsEl.innerHTML = '<div style="text-align:center;padding:20px;color:var(--mt-gray-400)">No reconciliation data available. Import SAP data first.</div>';
        }

        card.scrollIntoView({behavior: 'smooth'});
    } catch(e) {
        alert('Error running reconciliation');
    }
}

async function exportUsage() {
    try {
        const res = await fetch(`${API}/analytics/sap-insights/`, {headers});
        const data = await res.json();

        if (!data.usage_export || !data.usage_export.length) {
            alert('No usage data available for export');
            return;
        }

        // Create CSV
        const csvHeaders = ['Material', 'Plant', 'Storage Location', 'Batch', 'Movement Type', 'Quantity', 'Date', 'Customer'];
        const csvRows = data.usage_export.map(r => [
            r.material_code, r.plant, r.storage_location, r.batch_number,
            r.movement_type, r.quantity, r.posting_date, r.customer_name
        ]);

        let csv = csvHeaders.join(',') + '\n';
        csvRows.forEach(row => { csv += row.map(v => `"${v || ''}"`).join(',') + '\n'; });

        const blob = new Blob([csv], {type: 'text/csv'});
        const url = URL.createObjectURL(blob);
        const a = document.createElement('a');
        a.href = url;
        a.download = `sap_usage_export_${new Date().toISOString().slice(0,10)}.csv`;
        a.click();
        URL.revokeObjectURL(url);
    } catch(e) {
        alert('Error exporting usage data');
    }
}

document.addEventListener('DOMContentLoaded', () => {
    loadSyncStatus();
    loadSAPInsights();
});
