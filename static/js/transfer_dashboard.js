const _cfg = JSON.parse(document.getElementById('page-config').textContent);
const canUseDashboard = _cfg.canUseDashboard;
    const canUseScanner = _cfg.canUseScanner;
    let allTransfers = [];

    function getCookie(name) {
        let cookieValue = null;
        if (document.cookie && document.cookie !== '') {
            const cookies = document.cookie.split(';');
            for (let i = 0; i < cookies.length; i++) {
                const cookie = cookies[i].trim();
                if (cookie.substring(0, name.length + 1) === (name + '=')) {
                    cookieValue = decodeURIComponent(cookie.substring(name.length + 1));
                    break;
                }
            }
        }
        return cookieValue;
    }

    function showAlert(msg, type='info') {
        const div = document.createElement('div');
        div.className = `alert alert-${type}`;
        div.innerHTML = `${msg} <span style="float:right;cursor:pointer;" onclick="this.parentElement.remove()">×</span>`;
        document.getElementById('alertContainer').prepend(div);
        setTimeout(() => div.remove(), 5000);
    }

    function switchTab(e, tabId) {
        document.querySelectorAll('.tab-content').forEach(el => el.classList.remove('active'));
        document.querySelectorAll('.tab-btn').forEach(el => el.classList.remove('active'));
        document.getElementById(tabId).classList.add('active');
        e.target.classList.add('active');
    }

    async function loadTransfers() {
        try {
            const resp = await fetch('/product_scanning/api/transfers/', {
                headers: { 'Content-Type': 'application/json', 'X-CSRFToken': getCookie('csrftoken') },
                credentials: 'same-origin'
            });
            const data = await resp.json();
            allTransfers = data.results || data;
            updateStats();
            renderSessions('active', 'activeList');
            renderSessions('pending', 'pendingList');
            renderSessions('closed', 'closedList');
        } catch (err) {
            showAlert('Error loading transfers', 'error');
            console.error(err);
        }
    }

    function updateStats() {
        const active = allTransfers.filter(t => t.status === 'active').length;
        const pending = allTransfers.filter(t => t.status === 'completed').length;
        const closed = allTransfers.filter(t => t.status === 'closed').length;
        const products = allTransfers.reduce((sum, t) => sum + (t.total_products || t.transferred_products?.length || 0), 0);
        
        document.getElementById('activeCount').textContent = active;
        document.getElementById('pendingCount').textContent = pending;
        document.getElementById('closedCount').textContent = closed;
        document.getElementById('totalProducts').textContent = products;
    }

    function renderSessions(statusFilter, containerId) {
        const container = document.getElementById(containerId);
        const statusMap = { active: 'active', pending: 'completed', closed: 'closed' };
        const sessions = allTransfers.filter(t => t.status === statusMap[statusFilter]);

        if (sessions.length === 0) {
            const icons = { active: '🟢', pending: '⚠️', closed: '🔒' };
            const titles = { active: 'No Ongoing Transfers', pending: 'Nothing Awaiting Confirmation', closed: 'No Completed Transfers' };
            container.innerHTML = `
                <div class="empty-state" style="grid-column:1/-1;">
                    <div class="empty-icon">${icons[statusFilter]}</div>
                    <div class="empty-title">${titles[statusFilter]}</div>
                </div>
            `;
            return;
        }

        const colors = { active: '#f59e0b', completed: '#ef4444', closed: '#7c3aed' };
        const labels = { active: 'ONGOING', completed: 'PENDING', closed: 'COMPLETED' };

        container.innerHTML = sessions.map(t => {
            const from = t.from_hospital_details?.name || 'Unknown';
            const to = t.to_hospital_details?.name || 'Unknown';
            const user = t.initiated_by_details?.first_name 
                ? `${t.initiated_by_details.first_name} ${t.initiated_by_details.last_name}`
                : t.initiated_by_details?.username || 'Unknown';
            const count = t.total_products || t.transferred_products?.length || 0;
            const color = colors[t.status] || '#6b7280';
            const label = labels[t.status] || t.status.toUpperCase();

            const terminateBtn = (canUseDashboard && t.status !== 'closed') ? `
                <button class="btn-export" style="color:#dc2626;" onclick="terminateTransfer('${t.session_key}')">🚫 Terminate</button>
            ` : '';

            return `
            <div class="session-card" style="border-top-color:${color};">
                <div class="session-header">
                    <div class="session-batch">🔄 ${t.transfer_id}</div>
                    <span class="status-badge" style="background:rgba(249,115,22,0.12);color:#ea580c;">${label}</span>
                </div>
                <div class="session-info">
                    <div class="info-item" style="grid-column:1/-1;">
                        <div class="info-label">🏥 Route</div>
                        <div class="info-value route-display">${from} → ${to}</div>
                    </div>
                    <div class="info-item">
                        <div class="info-label">📦 Products</div>
                        <div class="info-value">${count}</div>
                    </div>
                    <div class="info-item">
                        <div class="info-label">👤 Initiated By</div>
                        <div class="info-value">${user}</div>
                    </div>
                    <div class="info-item">
                        <div class="info-label">📅 Started</div>
                        <div class="info-value">${new Date(t.started_at).toLocaleDateString()}</div>
                    </div>
                </div>
                <div class="session-actions">
                    <a href="/product_scanning/transfers/${t.session_key}/" class="btn-primary" style="text-decoration:none;display:flex;align-items:center;justify-content:center;">👁️ View</a>
                    ${terminateBtn}
                </div>
            </div>`;
        }).join('');
    }

    async function terminateTransfer(sessionKey) {
        if (!confirm('Terminate this transfer?')) return;
        try {
            await fetch('/product_scanning/api/transfers/terminate_transfer/', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json', 'X-CSRFToken': getCookie('csrftoken') },
                credentials: 'same-origin',
                body: JSON.stringify({ session_key: sessionKey })
            });
            showAlert('Transfer terminated', 'success');
            loadTransfers();
        } catch (err) {
            showAlert('Failed to terminate', 'error');
        }
    }

    document.addEventListener('DOMContentLoaded', loadTransfers);