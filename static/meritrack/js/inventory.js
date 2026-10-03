const API = '/meritrack/api';
let hospitalList = [];          // summary list from inventory_tree
const hospitalCache = {};       // { hospitalId: { departments: [...] } }
let activeSearch = '';
let activeStatus = '';
let activeExpiry = '';

async function loadInventory() {
    try {
        document.getElementById('inventoryTree').innerHTML = '<div class="empty-state"><i class="fas fa-spinner fa-spin"></i><p>Loading inventory…</p></div>';
        const resp = await fetch(`${API}/hospitals/inventory_tree/`, { credentials: 'same-origin' });
        if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
        hospitalList = await resp.json();
        renderSummaryTree(hospitalList);
    } catch (e) {
        document.getElementById('inventoryTree').innerHTML = '<div class="empty-state"><i class="fas fa-exclamation-triangle"></i><p>Failed to load inventory: ' + e.message + '</p></div>';
    }
}

function getFilteredHospitalList() {
    return hospitalList.filter(h => {
        // Name/city text search
        if (activeSearch) {
            const nameMatch = (h.name || '').toLowerCase().includes(activeSearch) ||
                              (h.city || '').toLowerCase().includes(activeSearch);
            if (!nameMatch) return false;
        }
        // Status filter: hide hospitals with 0 matching products
        const statusCount = {
            'on_shelf':       h.total_on_shelf,
            'used':           h.total_used,
            'returned':       h.total_returned,
            'expired_removed':h.total_expired_removed,
            'recalled':       h.total_recalled,
            'transferred':    h.total_transferred,
        };
        if (activeStatus && (statusCount[activeStatus] || 0) === 0) return false;
        // Expiry filter: hide hospitals with 0 matching on-shelf products
        if (activeExpiry === 'expired'       && (h.total_expired      || 0) === 0) return false;
        if (activeExpiry === 'expiring_soon' && (h.total_expiring_soon || 0) === 0) return false;
        if (activeExpiry === 'expiring_30'   && (h.total_expiring_30   || 0) === 0) return false;
        return true;
    });
}

function applyFilters() {
    activeSearch = document.getElementById('invSearchInput').value.toLowerCase().trim();
    activeStatus = document.getElementById('statusFilter').value;
    activeExpiry = document.getElementById('expiryFilter').value;

    // Capture which hospitals are currently expanded BEFORE re-render destroys the DOM
    const openIds = new Set();
    document.querySelectorAll('.hospital-body.open[data-hospital-id]').forEach(el => {
        openIds.add(el.dataset.hospitalId);
    });

    renderSummaryTree(getFilteredHospitalList());

    // Re-expand previously open hospitals with new filters applied
    openIds.forEach(id => {
        const body = document.querySelector(`.hospital-body[data-hospital-id="${id}"]`);
        if (!body) return; // hospital may have been hidden by filter
        const header = body.previousElementSibling;
        body.classList.add('open');
        const icon = header?.querySelector('.fa-chevron-down');
        if (icon) { icon.classList.remove('fa-chevron-down'); icon.classList.add('fa-chevron-up'); }
        loadHospitalProducts(id, body, true); // force reload with new filter params
    });
}

function clearFilters() {
    document.getElementById('invSearchInput').value = '';
    document.getElementById('statusFilter').value = '';
    document.getElementById('expiryFilter').value = '';
    activeSearch = '';
    activeStatus = '';
    activeExpiry = '';
    document.getElementById('filterStats').style.display = 'none';
    renderSummaryTree(getFilteredHospitalList());
}

function renderSummaryTree(hospitals) {
    const container = document.getElementById('inventoryTree');

    // Update filter stats bar
    const hasFilters = activeSearch || activeStatus || activeExpiry;
    const statsDiv = document.getElementById('filterStats');
    if (hasFilters) {
        statsDiv.style.display = 'block';
        const statusKey = activeStatus || 'total_on_shelf';
        const countField = {
            'on_shelf': 'total_on_shelf', 'used': 'total_used', 'returned': 'total_returned',
            'expired_removed': 'total_expired_removed', 'recalled': 'total_recalled',
            'transferred': 'total_transferred',
        }[activeStatus] || 'total_on_shelf';
        const total = hospitals.reduce((s, h) => s + (h[countField] || 0), 0);
        let statsText = `<strong>Filtered:</strong> ${hospitals.length} hospital${hospitals.length !== 1 ? 's' : ''}`;
        if (total) statsText += `, ${total} matching products`;
        document.getElementById('statsText').innerHTML = statsText;
    } else {
        statsDiv.style.display = 'none';
    }

    if (!hospitals.length) {
        container.innerHTML = '<div class="empty-state"><i class="fas fa-search"></i><p>No hospitals match. Try adjusting your filters.</p></div>';
        return;
    }

    container.innerHTML = hospitals.map(h => {
        const wasOpen = hospitalCache[h.id]; // keep open if already loaded
        return `<div class="hospital-item" data-hospital-id="${h.id}">
            <div class="hospital-header" onclick="toggleHospital(this, ${h.id})">
                <div class="hospital-name"><i class="fas fa-hospital" style="color:var(--mt-primary);margin-right:8px;"></i>${escapeHtml(h.name)}${h.city ? ' — ' + escapeHtml(h.city) : ''}</div>
                <div class="hospital-stats">
                    ${h.total_on_shelf > 0 ? `<span class="stat-chip stat-on-shelf">${h.total_on_shelf} on shelf</span>` : ''}
                    ${h.total_expired > 0 ? `<span class="stat-chip stat-expired">${h.total_expired} expired</span>` : ''}
                    ${h.total_used > 0 ? `<span class="stat-chip stat-used">${h.total_used} used</span>` : ''}
                    ${h.total_returned > 0 ? `<span class="stat-chip" style="background:var(--mt-gray-100);color:var(--mt-gray-700);">${h.total_returned} returned</span>` : ''}
                    <i class="fas fa-chevron-down" style="color:var(--mt-gray-400);"></i>
                </div>
            </div>
            <div class="hospital-body" data-hospital-id="${h.id}">
                <div class="dept-loading" style="padding:16px;color:var(--mt-gray-400);font-size:0.88em;display:none;">
                    <i class="fas fa-spinner fa-spin"></i> Loading products…
                </div>
                <div class="dept-content"></div>
            </div>
        </div>`;
    }).join('');
}

async function toggleHospital(header, hospitalId) {
    const body = header.nextElementSibling;
    const isOpen = body.classList.contains('open');

    if (isOpen) {
        body.classList.remove('open');
        const icon = header.querySelector('.fa-chevron-up');
        if (icon) { icon.classList.remove('fa-chevron-up'); icon.classList.add('fa-chevron-down'); }
        return;
    }

    body.classList.add('open');
    const icon = header.querySelector('.fa-chevron-down');
    if (icon) { icon.classList.remove('fa-chevron-down'); icon.classList.add('fa-chevron-up'); }

    await loadHospitalProducts(hospitalId, body, false);
}

async function loadHospitalProducts(hospitalId, body, forceReload) {
    const cacheKey = `${hospitalId}|${activeStatus}|${activeExpiry}|${activeSearch}`;

    if (!forceReload && hospitalCache[cacheKey]) {
        renderDepts(body, hospitalCache[cacheKey]);
        return;
    }

    // Show spinner
    const loadingEl = body.querySelector('.dept-loading');
    const contentEl = body.querySelector('.dept-content');
    if (loadingEl) loadingEl.style.display = 'block';
    if (contentEl) contentEl.innerHTML = '';

    try {
        const params = new URLSearchParams();
        if (activeStatus) params.set('status', activeStatus);
        if (activeSearch) params.set('search', activeSearch);
        const url = `${API}/hospitals/${hospitalId}/products/?${params}`;
        const resp = await fetch(url, { credentials: 'same-origin' });
        if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
        const data = await resp.json();

        // Client-side expiry filter (not done server-side)
        let depts = data.departments || [];
        if (activeExpiry) {
            depts = depts.map(d => ({
                ...d,
                products: (d.products || []).filter(p => {
                    if (!p.expiry_date) return false;
                    const now = new Date(); now.setHours(0,0,0,0);
                    const expiry = new Date(p.expiry_date); expiry.setHours(0,0,0,0);
                    const days = (expiry - now) / 86400000;
                    if (activeExpiry === 'expired') return days < 0;
                    if (activeExpiry === 'expiring_soon') return days >= 0 && days <= 90;
                    if (activeExpiry === 'expiring_30') return days >= 0 && days <= 30;
                    return true;
                })
            })).filter(d => d.products.length > 0);
        }

        hospitalCache[cacheKey] = depts;
        renderDepts(body, depts);
    } catch (e) {
        if (contentEl) contentEl.innerHTML = `<p style="padding:12px;color:var(--mt-danger);">Failed to load: ${e.message}</p>`;
    } finally {
        if (loadingEl) loadingEl.style.display = 'none';
    }
}

function renderDepts(body, depts) {
    const contentEl = body.querySelector('.dept-content');
    if (!contentEl) return;

    if (!depts || !depts.length) {
        contentEl.innerHTML = '<p style="color:var(--mt-gray-400);padding:12px;font-size:0.85em;">No products match the current filters.</p>';
        return;
    }

    contentEl.innerHTML = depts.map(d => {
        const products = d.products || [];
        const dept = d.department || {};
        return `<div class="dept-section">
            <div class="dept-name"><i class="fas fa-door-open"></i> ${escapeHtml(dept.name || d.name)} ${dept.floor ? '(Floor ' + escapeHtml(dept.floor) + ')' : ''} <span class="badge badge-blue">${products.length}</span></div>
            ${products.length ? `<table data-stack>
                <thead><tr><th>Serial</th><th>Product</th><th>Status</th><th>Expiry</th><th>Shelf</th></tr></thead>
                <tbody>${products.map(p => `<tr>
                    <td style="font-weight:600;">${escapeHtml(p.serial_number)}</td>
                    <td>${escapeHtml(p.catalog_product_name || p.product_name || '—')}</td>
                    <td><span class="badge ${getStatusBadge(p.status)}">${getStatusLabel(p.status)}</span></td>
                    <td>${p.expiry_date ? `<span class="badge ${getExpiryBadge(p.expiry_date)}">${p.expiry_date}</span>` : '—'}</td>
                    <td>${escapeHtml(p.shelf_location || '—')}</td>
                </tr>`).join('')}</tbody>
            </table>` : '<p style="color:var(--mt-gray-400);font-size:0.85em;">No products</p>'}
        </div>`;
    }).join('');
}

function getStatusBadge(s) {
    const map = { 'on_shelf': 'badge-green', 'used': 'badge-blue', 'returned': 'badge-gray', 'expired_removed': 'badge-red', 'recalled': 'badge-orange', 'transferred': 'badge-yellow' };
    return map[s] || 'badge-gray';
}

function getStatusLabel(s) {
    const labels = { 'on_shelf': 'On Shelf', 'used': 'Used on Patient', 'returned': 'Returned', 'expired_removed': 'Expired & Removed', 'recalled': 'Recalled', 'transferred': 'Transferred' };
    return labels[s] || s.replace(/_/g, ' ');
}

function getExpiryBadge(d) {
    const now = new Date(); now.setHours(0,0,0,0);
    const expiry = new Date(d); expiry.setHours(0,0,0,0);
    const days = (expiry - now) / 86400000;
    if (days < 0) return 'badge-red';
    if (days <= 30) return 'badge-orange';
    if (days <= 90) return 'badge-yellow';
    return 'badge-green';
}

function escapeHtml(s) {
    if (!s) return '';
    const div = document.createElement('div');
    div.textContent = s;
    return div.innerHTML;
}

loadInventory();
