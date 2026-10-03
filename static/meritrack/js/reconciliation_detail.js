const API = '/meritrack/api';
const headers = {'X-CSRFToken': csrfToken, 'Content-Type': 'application/json'};
let allItems = [];

const statusBadge = {matched:'green', missing:'red', extra:'yellow', expired_found:'red', damaged:'red', wrong_location:'yellow'};
const statusColors = {matched:'var(--mt-success)', missing:'var(--mt-danger)', extra:'var(--mt-warning)'};

async function loadDetail() {
    try {
        const res = await fetch(`${API}/reconciliations/${reconId}/detail-summary/`, {headers});
        const data = await res.json();

        // Meta
        document.getElementById('reconMeta').innerHTML = `
            <div class="info-item"><span class="label">Hospital:</span><span class="value">${data.hospital_name || '-'}</span></div>
            <div class="info-item"><span class="label">Type:</span><span class="value">${data.recon_type || '-'}</span></div>
            <div class="info-item"><span class="label">Status:</span><span class="value"><span class="badge badge-${data.status==='completed'?'green':'blue'}">${(data.status||'').replace(/_/g,' ')}</span></span></div>
            <div class="info-item"><span class="label">Created:</span><span class="value">${data.created_at ? new Date(data.created_at).toLocaleString() : '-'}</span></div>
            <div class="info-item"><span class="label">By:</span><span class="value">${data.performed_by_name || '-'}</span></div>
        `;

        // Stats
        const s = data.summary || {};
        document.getElementById('reconDetailStats').innerHTML = `
            <div class="stat-card"><div class="stat-value">${s.total_expected||0}</div><div class="stat-label">Expected</div></div>
            <div class="stat-card"><div class="stat-value">${s.total_scanned||0}</div><div class="stat-label">Scanned</div></div>
            <div class="stat-card"><div class="stat-value" style="color:var(--mt-success)">${s.matched||0}</div><div class="stat-label">Matched</div></div>
            <div class="stat-card"><div class="stat-value" style="color:var(--mt-danger)">${s.missing||0}</div><div class="stat-label">Missing</div></div>
            <div class="stat-card"><div class="stat-value" style="color:var(--mt-warning)">${s.extra||0}</div><div class="stat-label">Extra</div></div>
            <div class="stat-card"><div class="stat-value" style="color:var(--mt-danger)">${s.expired||0}</div><div class="stat-label">Expired Found</div></div>
        `;

        // Accuracy
        const acc = data.accuracy_rate || 0;
        const cls = acc >= 90 ? 'var(--mt-success)' : acc >= 70 ? 'var(--mt-warning)' : 'var(--mt-danger)';
        document.getElementById('accuracyDisplay').innerHTML = `
            <div class="accuracy-value" style="color:${cls}">${acc}%</div>
            <div style="color:var(--mt-gray-600);margin-top:4px">Accuracy Rate</div>
            <div style="margin-top:8px">Discrepancy: <strong style="color:var(--mt-danger)">${data.discrepancy_rate||0}%</strong></div>
        `;

        // Load items
        const itemsRes = await fetch(`${API}/reconciliations/${reconId}/`, {headers});
        const reconData = await itemsRes.json();
        allItems = reconData.items || [];
        renderItems(allItems);
    } catch(e) {
        document.getElementById('reconMeta').innerHTML = '<div style="color:var(--mt-danger)">Error loading data</div>';
    }
}

function renderItems(items) {
    const tbody = document.getElementById('itemsBody');
    if (!items.length) {
        tbody.innerHTML = '<tr><td colspan="8" style="text-align:center;padding:30px;color:var(--mt-gray-400)">No items</td></tr>';
        return;
    }
    tbody.innerHTML = items.map(i => `<tr>
        <td><strong>${i.product_name || '-'}</strong></td>
        <td>${i.batch_number || '-'}</td>
        <td>${i.serial_number || '-'}</td>
        <td style="font-family:monospace;font-size:0.8em">${i.barcode_data || '-'}</td>
        <td><span class="badge badge-${statusBadge[i.match_status]||'gray'}">${(i.match_status||'').replace(/_/g,' ')}</span></td>
        <td>${i.expected_quantity ?? '-'}</td>
        <td>${i.found_quantity ?? '-'}</td>
        <td style="font-size:0.82em;color:var(--mt-gray-600)">${i.notes || ''}</td>
    </tr>`).join('');
}

function filterItems(status) {
    document.querySelectorAll('.filter-tab').forEach(t => t.classList.remove('active'));
    event.target.classList.add('active');
    if (status === 'all') { renderItems(allItems); return; }
    renderItems(allItems.filter(i => i.match_status === status));
}

document.addEventListener('DOMContentLoaded', loadDetail);
