const API = '/meritrack/api';
const headers = {'X-CSRFToken': csrfToken, 'Content-Type': 'application/json'};

const statusBadge = {
    draft: 'gray', in_progress: 'yellow', pending_review: 'blue',
    completed: 'green', cancelled: 'red'
};

async function loadRecons() {
    try {
        const res = await fetch(`${API}/reconciliations/`, {headers});
        const data = await res.json();
        const results = data.results || data;

        // Update stats
        const completed = results.filter(r => r.status === 'completed');
        const inProgress = results.filter(r => r.status === 'in_progress');
        document.getElementById('totalRecons').textContent = results.length;
        document.getElementById('completedRecons').textContent = completed.length;
        document.getElementById('inProgressRecons').textContent = inProgress.length;

        if (completed.length) {
            const avg = completed.reduce((sum, r) => sum + parseFloat(r.accuracy_rate || 0), 0) / completed.length;
            document.getElementById('avgAccuracy').textContent = avg.toFixed(1) + '%';

            // Accuracy trend
            const sorted = completed.sort((a, b) => new Date(a.completed_at) - new Date(b.completed_at)).slice(-12);
            const max = 100;
            document.getElementById('accuracyTrend').innerHTML = sorted.map(r => {
                const h = Math.max(10, parseFloat(r.accuracy_rate || 0));
                const color = h >= 90 ? 'var(--mt-success)' : h >= 70 ? 'var(--mt-warning)' : 'var(--mt-danger)';
                return `<div class="trend-bar" style="height:${h}%;background:${color}">
                    <div class="tip">${r.hospital_name}: ${r.accuracy_rate}%</div></div>`;
            }).join('');
        }

        // Table
        const tbody = document.getElementById('reconTableBody');
        if (!results.length) {
            tbody.innerHTML = `<tr><td colspan="8" class="recon-empty">
                <i class="fas fa-clipboard-check"></i>
                No reconciliations yet. Start your first one!
            </td></tr>`;
            return;
        }

        tbody.innerHTML = results.map(r => `<tr>
            <td><strong>#${r.id}</strong></td>
            <td>${r.hospital_name || '-'}</td>
            <td><span class="badge badge-blue">${r.recon_type}</span></td>
            <td><span class="badge badge-${statusBadge[r.status] || 'gray'}">${r.status.replace(/_/g,' ')}</span></td>
            <td>${r.total_expected || '-'} expected / ${r.total_scanned || '-'} scanned</td>
            <td>${r.accuracy_rate ? r.accuracy_rate + '%' : '-'}</td>
            <td>${new Date(r.created_at).toLocaleDateString()}</td>
            <td><a href="/meritrack/reconciliation/${r.id}/" class="btn-mt btn-mt-primary" style="padding:6px 12px;font-size:0.82em">
                <i class="fas fa-eye"></i> View
            </a></td>
        </tr>`).join('');
    } catch(e) {
        document.getElementById('reconTableBody').innerHTML = `<tr><td colspan="8" style="color:var(--mt-danger);text-align:center">Error loading data</td></tr>`;
    }
}

document.addEventListener('DOMContentLoaded', loadRecons);
