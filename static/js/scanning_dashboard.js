const _cfg = JSON.parse(document.getElementById('page-config').textContent);
    const canUseDashboard = _cfg.canUseDashboard;
    const canUseScanner   = _cfg.canUseScanner;
    const canViewOnly     = _cfg.canViewOnly || false;

    // ── DataTables helper ──────────────────────────────────────────
    function initDataTable(tableId, extraOpts) {
        if (typeof $ === 'undefined' || !$.fn.DataTable) return;
        const $t = $('#' + tableId);
        if ($.fn.DataTable.isDataTable($t)) $t.DataTable().destroy();
        $t.DataTable(Object.assign({
            paging: true,
            pageLength: 25,
            lengthMenu: [10, 25, 50, 100],
            searching: true,
            ordering: true,
            info: true,
            autoWidth: false,
            responsive: true,
            language: {
                search: '🔍',
                searchPlaceholder: 'Search...',
                lengthMenu: 'Show _MENU_',
                info: '_START_–_END_ of _TOTAL_',
                infoEmpty: 'No records',
                emptyTable: 'No data available',
                paginate: { previous: '‹', next: '›' },
            },
            dom: '<"dt-top"lf>rt<"dt-bottom"ip>',
        }, extraOpts || {}));
    }

    const API = {
        base: '/product_scanning/api',
        sessions: () => `${API.base}/sessions/`,
        products: () => `${API.base}/products/`,
        hospitals: () => `${API.base}/hospitals/list_hospitals/`,
        forceComplete: () => `${API.base}/sessions/force_complete_session/`,
        terminate: () => `${API.base}/sessions/terminate_session/`,
    };

    let allSessions = [];
    let allProducts = [];
    let allHospitals = [];
    let currentTypeFilter = 'all'; // 'all', 'normal', 'transfer'
    let sessionFilters = { year: '', month: '', date: '', salesperson: '', hospital: '' };

    // Filter sessions by type + date/salesperson/hospital filters
    function getFilteredSessions() {
        let sessions = allSessions;

        if (currentTypeFilter === 'normal') sessions = sessions.filter(s => s.session_type !== 'transfer');
        else if (currentTypeFilter === 'transfer') sessions = sessions.filter(s => s.session_type === 'transfer');

        if (sessionFilters.year) {
            sessions = sessions.filter(s => new Date(s.started_at).getFullYear() === parseInt(sessionFilters.year));
        }
        if (sessionFilters.month) {
            sessions = sessions.filter(s => (new Date(s.started_at).getMonth() + 1) === parseInt(sessionFilters.month));
        }
        if (sessionFilters.date) {
            sessions = sessions.filter(s => new Date(s.started_at).toISOString().split('T')[0] === sessionFilters.date);
        }
        if (sessionFilters.salesperson) {
            sessions = sessions.filter(s =>
                (s.hospital?.salesperson_email || '').toLowerCase() === sessionFilters.salesperson.toLowerCase()
            );
        }
        if (sessionFilters.hospital) {
            sessions = sessions.filter(s => (s.hospital?.name || '') === sessionFilters.hospital);
        }

        return sessions;
    }

    function setTypeFilter(filterType) {
        currentTypeFilter = filterType;
        document.querySelectorAll('.filter-btn').forEach(btn => btn.classList.remove('active'));
        document.querySelector(`.filter-btn[onclick="setTypeFilter('${filterType}')"]`).classList.add('active');
        updateStats();
        renderActiveSessions();
        renderCompletedSessions();
        renderClosedSessions();
        if (document.getElementById('all-products')?.classList.contains('active')) loadAllProducts();
    }

    function _getUserName(s) {
        const u = s.scanned_by_details;
        if (!u) return 'Unknown';
        const full = ((u.first_name || '') + ' ' + (u.last_name || '')).trim();
        return full || u.username || 'Unknown';
    }

    function populateFilterDropdowns() {
        const years = [...new Set(allSessions.map(s => new Date(s.started_at).getFullYear()))].sort((a, b) => b - a);
        const yearSel = document.getElementById('filterYear');
        if (yearSel) {
            yearSel.innerHTML = '<option value="">Year</option>' +
                years.map(y => `<option value="${y}">${y}</option>`).join('');
            if (sessionFilters.year) yearSel.value = sessionFilters.year;
        }

        // Salespersons: sourced from hospital→salesperson mapping only
        const emailToName = {};
        allSessions.forEach(s => {
            const u = s.scanned_by_details;
            if (u && u.email) {
                const full = ((u.first_name || '') + ' ' + (u.last_name || '')).trim();
                emailToName[u.email.toLowerCase()] = full || u.username || u.email;
            }
        });
        const spEmails = [...new Set(
            allHospitals.map(h => h.salesperson_email).filter(e => e && e.trim())
        )].sort();
        const personSel = document.getElementById('filterSalesperson');
        if (personSel) {
            personSel.innerHTML = '<option value="">Any salesperson</option>' +
                spEmails.map(email => {
                    const raw  = email.split('@')[0];
                    const name = emailToName[email.toLowerCase()] ||
                        raw.split(/[._-]/).map(w => w.charAt(0).toUpperCase() + w.slice(1).toLowerCase()).join(' ');
                    return `<option value="${email}">${name}</option>`;
                }).join('');
            if (sessionFilters.salesperson) personSel.value = sessionFilters.salesperson;
        }

        const hospitals = [...new Set(allSessions.map(s => s.hospital?.name).filter(Boolean))].sort();
        const hospSel = document.getElementById('filterHospital');
        if (hospSel) {
            hospSel.innerHTML = '<option value="">Hospital</option>' +
                hospitals.map(h => `<option value="${h}">${h}</option>`).join('');
            if (sessionFilters.hospital) hospSel.value = sessionFilters.hospital;
        }
    }

    function applySessionFilter() {
        sessionFilters.year        = document.getElementById('filterYear')?.value        || '';
        sessionFilters.month       = document.getElementById('filterMonth')?.value       || '';
        sessionFilters.date        = document.getElementById('filterDate')?.value        || '';
        sessionFilters.salesperson = document.getElementById('filterSalesperson')?.value || '';
        sessionFilters.hospital    = document.getElementById('filterHospital')?.value    || '';

        const count = Object.values(sessionFilters).filter(v => v !== '').length;
        const badge  = document.getElementById('sfBadge');
        const trigger = document.getElementById('sfTrigger');
        if (badge)   { badge.textContent = count; badge.style.display = count ? 'inline-flex' : 'none'; }
        if (trigger) { trigger.classList.toggle('active', count > 0); }

        updateStats();
        renderActiveSessions();
        renderCompletedSessions();
        renderClosedSessions();
        if (document.getElementById('all-products')?.classList.contains('active')) loadAllProducts();
    }

    function clearSessionFilters() {
        sessionFilters = { year: '', month: '', date: '', salesperson: '', hospital: '' };
        ['filterYear','filterMonth','filterDate','filterSalesperson','filterHospital']
            .forEach(id => { const el = document.getElementById(id); if (el) el.value = ''; });
        const badge   = document.getElementById('sfBadge');
        const trigger = document.getElementById('sfTrigger');
        if (badge)   { badge.style.display = 'none'; }
        if (trigger) { trigger.classList.remove('active'); }
        updateStats();
        renderActiveSessions();
        renderCompletedSessions();
        renderClosedSessions();
        if (document.getElementById('all-products')?.classList.contains('active')) loadAllProducts();
    }

    function toggleFilterPanel(e) {
        e.stopPropagation();
        const panel = document.getElementById('sfPanel');
        if (!panel) return;
        const open = panel.style.display === 'none' || panel.style.display === '';
        panel.style.display = open ? 'block' : 'none';
    }

    function closeFilterPanel() {
        const panel = document.getElementById('sfPanel');
        if (panel) panel.style.display = 'none';
    }

    document.addEventListener('click', function(e) {
        const dropdown = document.getElementById('sfDropdown');
        if (dropdown && !dropdown.contains(e.target)) closeFilterPanel();
    });

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

    function showAlert(message, type = 'info') {
        const alertDiv = document.createElement('div');
        alertDiv.className = `alert alert-${type}`;
        alertDiv.innerHTML = message + ' <span style="float:right; cursor:pointer;" onclick="this.parentElement.remove()">×</span>';
        document.getElementById('alertContainer').prepend(alertDiv);
        setTimeout(() => {
            if (alertDiv.parentElement) alertDiv.remove();
        }, 5000);
    }

    function switchTab(event, tabId) {
        // Hide all tabs
        document.querySelectorAll('.tab-content').forEach(el => {
            el.classList.remove('active');
        });
        document.querySelectorAll('.tab-btn').forEach(el => {
            el.classList.remove('active');
        });

        // Show selected tab
        document.getElementById(tabId).classList.add('active');
        // Use closest() so clicking on inner spans still activates the button
        const btn = event.target.closest('.tab-btn') || event.target;
        btn.classList.add('active');

        // Refresh data for specific tabs
        if (tabId === 'all-products') {
            loadAllProducts();
        } else if (tabId === 'active-sessions') {
            renderActiveSessions();
        } else if (tabId === 'expired') {
            loadExpiredProducts();
        }
    }

    // Helper: fetch ALL pages from a DRF paginated endpoint
    async function fetchAllPages(url) {
        const items = [];
        let nextUrl = url;
        while (nextUrl) {
            const resp = await fetch(nextUrl, {
                method: 'GET',
                headers: { 'Content-Type': 'application/json', 'X-CSRFToken': getCookie('csrftoken') },
                credentials: 'same-origin'
            });
            if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
            const data = await resp.json();
            if (Array.isArray(data)) { items.push(...data); break; }
            items.push(...(data.results || []));
            nextUrl = data.next || null;
        }
        return items;
    }

    async function loadData() {
        try {

            // Fetch sessions, hospitals and expired summary in parallel.
            // allProducts is NOT loaded upfront — it's loaded lazily when
            // the "All Stock" tab is opened to avoid memory/timeout issues
            // at scale (each session carries its scanned_products inline).
            const [sessions, hospitals, expiredSummary] = await Promise.all([
                fetchAllPages(API.sessions()),
                fetchAllPages(API.hospitals()),
                fetch('/product_scanning/api/expired/summary/', {
                    method: 'GET',
                    headers: { 'Content-Type': 'application/json', 'X-CSRFToken': getCookie('csrftoken') },
                    credentials: 'same-origin'
                }).then(r => r.ok ? r.json() : { total_expired: 0 }).catch(() => ({ total_expired: 0 })),
            ]);

            allHospitals = hospitals;
            // allProducts populated lazily from session.scanned_products (already embedded in sessions response)

            // Normalize: map batch_details/hospital_details to batch/hospital
            // so every template reference like session.batch.batch_id works
            allSessions = sessions.map(s => {
                const hospital = s.hospital_details
                    || allHospitals.find(h => h.id === s.hospital)
                    || { id: s.hospital, name: 'Unknown', sap_code: 'N/A', province: 'N/A' };
                return {
                    ...s,
                    batch:    s.batch_details    || { batch_id: s.batch },
                    hospital: hospital,
                };
            });

            populateFilterDropdowns();
            updateStats();
            // Set expired count from summary (so it shows immediately, before user clicks the tab)
            document.getElementById('expiredCount').textContent = expiredSummary.total_expired || 0;
            renderActiveSessions();
            renderCompletedSessions();
            renderClosedSessions();

        } catch (error) {
            console.error('Load error:', error);
            showAlert(`Error loading data: ${error.message}`, 'error');
        }
    }

    function updateStats() {
        const sessions = getFilteredSessions();
        // Only count active sessions that have at least 1 scanned product
        const activeCount    = sessions.filter(s => s.status === 'active' && (s.scanned_products?.length || s.total_scans || 0) > 0).length;
        const pendingCount   = sessions.filter(s => s.status === 'completed' || s.status === 'on_hold').length;
        const closedCount    = sessions.filter(s => s.status === 'closed').length;
        // Only count hospitals from completed or closed sessions (not active)
        const uniqueHospitals = new Set(sessions.filter(s => s.status === 'completed' || s.status === 'on_hold' || s.status === 'closed').map(s => s.hospital?.id)).size;

        const totalProducts = sessions.reduce((sum, s) => sum + (s.scanned_products?.length || s.total_scans || 0), 0);

        document.getElementById('activeSessions').textContent    = activeCount;
        document.getElementById('pendingCloseCount').textContent = pendingCount;
        document.getElementById('closedCount').textContent       = closedCount;
        document.getElementById('totalProducts').textContent     = totalProducts;
        document.getElementById('totalHospitals').textContent    = uniqueHospitals;
    }

    // ── Remark badge helper ───────────────────────────────────────────────
    function _buildRemarkBadge(session) {
        const count = Number.isFinite(session.total_remarks)
            ? session.total_remarks
            : (session.has_warehouse_remark ? 1 : 0);
        if (!count) return '';
        const isWH = session.has_warehouse_remark;
        const bg     = isWH ? '#f5f3ff' : '#eff6ff';
        const color  = isWH ? '#7c3aed' : '#2563eb';
        const border = isWH ? '#ddd6fe' : '#bfdbfe';
        const title  = isWH
            ? `${count} remark${count !== 1 ? 's' : ''} (Warehouse replied)`
            : `${count} remark${count !== 1 ? 's' : ''}`;
        return `<button onclick="openBatchRemarks('${session.session_key}')" title="${title}" style="display:inline-flex;align-items:center;gap:4px;background:${bg};color:${color};border:1px solid ${border};border-radius:999px;padding:2px 8px;font-size:.72rem;font-weight:800;cursor:pointer;line-height:1;"><span>💬</span><span>${count}</span></button>`;
    }

    async function openBatchRemarks(sessionKey) {
        const modalHtml = `<div id="batchRemarkModal" style="position:fixed;inset:0;background:rgba(15,23,42,.55);z-index:9999;display:flex;align-items:flex-end;justify-content:center;" onclick="if(event.target===this)this.remove()">
          <div style="background:#fff;border-radius:20px 20px 0 0;width:min(620px,100%);max-height:80vh;overflow-y:auto;padding:0 0 env(safe-area-inset-bottom,0);">
            <div style="text-align:center;padding:10px 0 4px;"><div style="width:36px;height:4px;background:#e2e8f0;border-radius:2px;display:inline-block;"></div></div>
            <div style="display:flex;justify-content:space-between;align-items:center;padding:6px 18px 12px;border-bottom:1px solid #f1f5f9;">
              <div style="font-weight:800;font-size:1em;color:#0f172a;">💬 Batch Remarks</div>
              <button onclick="document.getElementById('batchRemarkModal').remove()" style="background:#f1f5f9;color:#64748b;border:none;width:32px;height:32px;border-radius:50%;font-size:1.1em;cursor:pointer;">✕</button>
            </div>
            <div id="batchRemarkBody" style="padding:16px 18px;">
              <div style="text-align:center;padding:20px;color:#94a3b8;">Loading…</div>
            </div>
          </div>
        </div>`;
        document.body.insertAdjacentHTML('beforeend', modalHtml);
        const body = document.getElementById('batchRemarkBody');
        try {
            const fetchJsonSafe = async (url) => {
                const resp = await fetch(url, {
                    method: 'GET',
                    headers: { 'Content-Type': 'application/json', 'X-CSRFToken': getCookie('csrftoken') },
                    credentials: 'same-origin'
                });
                if (!resp.ok) return { ok: false, status: resp.status, data: [] };
                const data = await resp.json();
                return { ok: true, status: resp.status, data };
            };

            const [sessionRes, productRes] = await Promise.all([
                fetchJsonSafe(`/product_scanning/api/session-remarks/?session=${encodeURIComponent(sessionKey)}`),
                fetchJsonSafe(`/product_scanning/api/product-remarks/?session=${encodeURIComponent(sessionKey)}`),
            ]);

            const sessionPayload = sessionRes.data;
            const productPayload = productRes.data;
            const sessionRemarks = Array.isArray(sessionPayload) ? sessionPayload : (sessionPayload.results || []);
            const productRemarks = Array.isArray(productPayload) ? productPayload : (productPayload.results || []);
            const allRemarks = [
                ...sessionRemarks.map(r => ({ ...r, _type: 'batch' })),
                ...productRemarks.map(r => ({ ...r, _type: 'product' })),
            ].sort((a, b) => new Date(a.created_at) - new Date(b.created_at));

            if (!allRemarks.length) {
                const warn = (!sessionRes.ok || !productRes.ok)
                    ? `<p style="color:#d97706;font-size:.82em;margin-bottom:8px;">Some remark sources could not be loaded (session: ${sessionRes.status}, product: ${productRes.status}).</p>`
                    : '';
                body.innerHTML = warn + '<p style="color:#94a3b8;font-style:italic;text-align:center;padding:16px;">No remarks yet.</p>';
                return;
            }
            body.innerHTML = allRemarks.map(r => {
                const isWH = r.role === 'warehouse';
                const typeTag = r._type === 'product'
                    ? `<span style="background:#fef3c7;color:#92400e;border-radius:10px;padding:1px 7px;font-size:.68rem;font-weight:700;margin-left:6px;">Product</span>`
                    : `<span style="background:#dcfce7;color:#166534;border-radius:10px;padding:1px 7px;font-size:.68rem;font-weight:700;margin-left:6px;">Batch</span>`;
                let productInfoLine = '';
                if (r._type === 'product' && r.product_info) {
                    const p = r.product_info;
                    const parts = [];
                    if (p.serial_number && !p.serial_number.startsWith('NSN-')) parts.push(`S/N: ${p.serial_number}`);
                    if (p.gtin_code) parts.push(`GTIN: ${p.gtin_code}`);
                    if (p.material_code) parts.push(`Material: ${p.material_code}`);
                    if (p.product_name) parts.push(p.product_name);
                    if (parts.length) productInfoLine = `<div style="font-size:.75em;color:#475569;background:#f8fafc;border-radius:4px;padding:3px 7px;margin-bottom:5px;font-family:monospace;letter-spacing:.01em;">${parts.join(' · ')}</div>`;
                }
                return `<div style="padding:10px 12px;border-radius:8px;background:${isWH?'#faf5ff':'#f8fafc'};border:1px solid ${isWH?'#ddd6fe':'#e2e8f0'};margin-bottom:8px;">
                    <div style="display:flex;align-items:center;flex-wrap:wrap;gap:4px;margin-bottom:4px;">
                        <span style="font-weight:700;font-size:.8em;color:${isWH?'#7c3aed':'#2563eb'};">${r.created_by_name || ''}</span>
                        <span style="font-size:.72em;color:#94a3b8;">(${isWH?'Warehouse':'Sales Rep'})</span>
                        ${typeTag}
                        <span style="font-size:.72em;color:#cbd5e1;margin-left:auto;">${r.created_at ? new Date(r.created_at).toLocaleString() : ''}</span>
                    </div>
                    ${productInfoLine}<div style="font-size:.88em;color:#1e293b;line-height:1.5;">${r.remark}</div>
                </div>`;
            }).join('');

            if (!sessionRes.ok || !productRes.ok) {
                body.insertAdjacentHTML('afterbegin', `<p style="color:#d97706;font-size:.82em;margin-bottom:8px;">Some remark sources could not be loaded completely (session: ${sessionRes.status}, product: ${productRes.status}).</p>`);
            }
        } catch(e) {
            body.innerHTML = `<p style="color:#dc2626;">Failed to load remarks: ${e.message || 'unknown error'}</p>`;
        }
    }

    function renderActiveSessions() {
        const container = document.getElementById('activeSessionsList');
        if (!container) return;
        const sessions = getFilteredSessions();
        // Only show active sessions that have at least 1 scanned product
        const active = sessions.filter(s => s.status === 'active' && (s.scanned_products?.length || s.total_scans || 0) > 0);
        const emptyCount = sessions.filter(s => s.status === 'active' && (s.scanned_products?.length || s.total_scans || 0) === 0).length;

        if (active.length === 0) {
            const emptyNote = emptyCount > 0
                ? `<p style="color:#94a3b8;font-size:0.82em;margin-top:6px;">${emptyCount} session(s) started but no products scanned yet</p>`
                : '<p>All sales rep audit sessions have been submitted.</p>';
            container.innerHTML = `
                <div class="empty-state" style="grid-column: 1/-1;">
                    <div class="empty-icon">&#10003;</div>
                    <div class="empty-title">No Ongoing Audits</div>
                    ${emptyNote}
                </div>
            `;
            return;
        }

        container.innerHTML = active.map(session => {
            const productCount = session.scanned_products?.length || session.total_scans || 0;
            const lastScan = session.scanned_products?.length
                ? new Date(Math.max(...session.scanned_products.map(p => new Date(p.scanned_at)))).toLocaleString()
                : 'No scans yet';
            const forceCompleteBtn = (canUseDashboard && !canViewOnly) ? `
                <button class="btn-export" onclick="forceCompleteSession('${session.session_key}')" title="Force submit this audit session on behalf of the sales rep">
                    ⚡ Force Submit
                </button>` : '';
            const terminateBtn = (canUseScanner && canUseDashboard && !canViewOnly) ? `
                <button class="btn-secondary" style="color:#dc2626;border-color:#fca5a5;" onclick="terminateSession('${session.session_key}')" title="Terminate this session (cancels it permanently)">
                    ✕ Terminate
                </button>` : '';
            
            // Check if this is an H2H transfer
            const isTransfer = session.session_type === 'transfer';
            const hospitalDisplay = isTransfer && session.to_hospital_details
                ? `<span style="color:#f97316;font-weight:700;">${session.hospital?.name || 'N/A'} &rarr; ${session.to_hospital_details.name}</span>`
                : session.hospital?.name || 'N/A';
            const sessionTypeLabel = isTransfer
                ? '<span class="type-badge">H2H</span>'
                : '';
            
            const remarkBadgeActive = _buildRemarkBadge(session);
            const remarkCountActive = Number.isFinite(session.total_remarks)
                ? session.total_remarks
                : (session.has_warehouse_remark ? 1 : 0);
            const remarkInfoActive = remarkCountActive > 0
                ? `<div class="info-item" style="background:${session.has_warehouse_remark ? '#faf5ff' : '#eff6ff'};border:1px solid ${session.has_warehouse_remark ? '#ddd6fe' : '#bfdbfe'};border-radius:8px;padding:8px;">
                        <div class="info-label" style="color:${session.has_warehouse_remark ? '#7c3aed' : '#2563eb'};">Remarks</div>
                        <div class="info-value" style="color:${session.has_warehouse_remark ? '#6d28d9' : '#1d4ed8'};font-weight:700;">💬 ${remarkCountActive} ${session.has_warehouse_remark ? 'WH reply' : 'remark' + (remarkCountActive !== 1 ? 's' : '')}</div>
                    </div>`
                : '';
            const cardHighlightActive = session.has_warehouse_remark
                ? 'border-top-color:#7c3aed;background:linear-gradient(180deg,#faf5ff 0%,#fff 60%);'
                : session.total_remarks > 0
                ? 'border-top-color:#2563eb;background:linear-gradient(180deg,#eff6ff 0%,#fff 60%);'
                : `border-top-color: ${isTransfer ? '#f97316' : '#f59e0b'};`;
            return `
            <div class="session-card" style="${cardHighlightActive}">
                <div class="session-header">
                    <div class="session-batch">${session.batch?.batch_id || 'Unknown'}${sessionTypeLabel}</div>
                    <div style="display:flex;align-items:center;gap:8px;">${remarkBadgeActive}<span class="status-badge" style="background:#fef3c7;color:#d97706;">ONGOING</span></div>
                </div>
                <div class="session-info">
                    <div class="info-item">
                        <div class="info-label">${isTransfer ? 'Route' : 'Hospital'}</div>
                        <div class="info-value">${hospitalDisplay}</div>
                    </div>
                    <div class="info-item">
                        <div class="info-label">Units Scanned</div>
                        <div class="info-value">${productCount}</div>
                    </div>
                    <div class="info-item">
                        <div class="info-label">Sales Rep</div>
                        <div class="info-value">${_userName(session)}</div>
                    </div>
                    ${remarkInfoActive}
                </div>
                <div class="session-actions">
                    <a href="/product_scanning/session/${session.session_key}/" class="btn-primary">
                        🔍 View Details
                    </a>
                    ${forceCompleteBtn}
                    ${terminateBtn}
                </div>
            </div>`;
        }).join('');
    }

    function renderCompletedSessions() {
        const container = document.getElementById('completedList');
        const sessions = getFilteredSessions();
        const completed = sessions.filter(s => s.status === 'completed' || s.status === 'on_hold');

        if (completed.length === 0) {
            container.innerHTML = `
                <div class="empty-state" style="grid-column: 1/-1;">
                    <div class="empty-icon">&#10003;</div>
                    <div class="empty-title">Nothing Awaiting Confirmation</div>
                    <p>Audit sessions appear here after a sales rep submits them.</p>
                </div>
            `;
            return;
        }

        container.innerHTML = completed.map(session => {
            const isTransfer = session.session_type === 'transfer';
            const hospitalDisplay = isTransfer && session.to_hospital_details
                ? `<span style="color:#f97316;font-weight:700;">${session.hospital?.name || 'N/A'} &rarr; ${session.to_hospital_details.name}</span>`
                : session.hospital?.name || 'N/A';
            const sessionTypeLabel = isTransfer
                ? '<span class="type-badge">H2H</span>'
                : '';
            
            const remarkBadgePending = _buildRemarkBadge(session);
            const remarkCountPending = Number.isFinite(session.total_remarks)
                ? session.total_remarks
                : (session.has_warehouse_remark ? 1 : 0);
            const remarkInfoPending = remarkCountPending > 0
                ? `<div class="info-item" style="background:${session.has_warehouse_remark ? '#faf5ff' : '#eff6ff'};border:1px solid ${session.has_warehouse_remark ? '#ddd6fe' : '#bfdbfe'};border-radius:8px;padding:8px;">
                        <div class="info-label" style="color:${session.has_warehouse_remark ? '#7c3aed' : '#2563eb'};">Remarks</div>
                        <div class="info-value" style="color:${session.has_warehouse_remark ? '#6d28d9' : '#1d4ed8'};font-weight:700;">💬 ${remarkCountPending} ${session.has_warehouse_remark ? 'WH reply' : 'remark' + (remarkCountPending !== 1 ? 's' : '')}</div>
                    </div>`
                : '';
            const cardHighlightPending = session.has_warehouse_remark
                ? 'border-top-color:#7c3aed;background:linear-gradient(180deg,#faf5ff 0%,#fff 60%);'
                : session.total_remarks > 0
                ? 'border-top-color:#2563eb;background:linear-gradient(180deg,#eff6ff 0%,#fff 60%);'
                : `border-top-color: ${isTransfer ? '#f97316' : '#ef4444'};`;
            return `
            <div class="session-card" style="${cardHighlightPending}">
                <div class="session-header">
                    <div class="session-batch">${session.batch?.batch_id || 'Unknown'}${sessionTypeLabel}</div>
                    <div style="display:flex;align-items:center;gap:8px;">${remarkBadgePending}${
                        session.status === 'on_hold'
                        ? '<span class="status-badge" style="background:#fef9c3;color:#854d0e;border:1px solid #fde047;">⏸ ON HOLD</span>'
                        : session.days_awaiting >= 6
                        ? `<span class="status-badge" style="background:#fef3c7;color:#92400e;">⚠️ ${session.days_awaiting}d OVERDUE</span>`
                        : '<span class="status-badge" style="background:#fee2e2;color:#dc2626;">AWAITING</span>'
                    }</div>
                </div>
                <div class="notice-box" style="${session.status === 'on_hold' ? 'background:#fef9c3;border-color:#fde047;color:#854d0e;' : session.days_awaiting >= 6 ? 'background:#fef3c7;border-color:#fbbf24;color:#92400e;' : ''}">
                    ${session.status === 'on_hold' ? '⏸ On hold — reminders paused. Open session to resume or close.' : session.days_awaiting >= 6 ? `⚠️ Overdue for ${session.days_awaiting} day${session.days_awaiting !== 1 ? 's' : ''} — daily reminder emails are being sent.` : isTransfer ? 'Transfer session submitted' : 'Sales rep submitted this audit'} ${session.status !== 'on_hold' && session.days_awaiting < 6 ? '&mdash; upload the confirmation document to complete.' : ''}
                </div>
                <div class="session-info">
                    <div class="info-item">
                        <div class="info-label">${isTransfer ? 'Route' : 'Hospital'}</div>
                        <div class="info-value">${hospitalDisplay}</div>
                    </div>
                    <div class="info-item">
                        <div class="info-label">Units Scanned</div>
                        <div class="info-value">${session.scanned_products?.length || session.total_scans || 0}</div>
                    </div>
                    <div class="info-item">
                        <div class="info-label">Sales Rep</div>
                        <div class="info-value">${_userName(session)}</div>
                    </div>
                    <div class="info-item">
                        <div class="info-label">Submitted</div>
                        <div class="info-value">${new Date(session.completed_at || session.started_at).toLocaleDateString()}</div>
                    </div>
                    ${remarkInfoPending}
                </div>
                <div class="session-actions">
                    <a href="/product_scanning/session/${session.session_key}/" class="btn-primary">
                        ${canViewOnly ? '🔍 View Details' : '📎 Upload &amp; Close'}
                    </a>
                    ${canViewOnly ? '' : `<button class="btn-export" onclick="exportSession('${session.session_key}')">⬇ Export</button>`}
                </div>
            </div>`;
        }).join('');
    }

    function renderClosedSessions() {
        const container = document.getElementById('closedList');
        const sessions = getFilteredSessions();
        const closed = sessions.filter(s => s.status === 'closed');

        if (closed.length === 0) {
            container.innerHTML = `
                <div class="empty-state" style="grid-column: 1/-1;">
                    <div class="empty-icon">&#128274;</div>
                    <div class="empty-title">No Completed Audits</div>
                    <p>Sessions move here after uploading a confirmation document.</p>
                </div>
            `;
            return;
        }

        container.innerHTML = closed.map(session => {
            const isTransfer = session.session_type === 'transfer';
            const hospitalDisplay = isTransfer && session.to_hospital_details
                ? `<span style="color:#f97316;font-weight:700;">${session.hospital?.name || 'N/A'} &rarr; ${session.to_hospital_details.name}</span>`
                : session.hospital?.name || 'N/A';
            const sessionTypeLabel = isTransfer
                ? '<span class="type-badge">H2H</span>'
                : '';
            
            const remarkBadgeClosed = _buildRemarkBadge(session);
            const remarkCountClosed = Number.isFinite(session.total_remarks)
                ? session.total_remarks
                : (session.has_warehouse_remark ? 1 : 0);
            const remarkInfoClosed = remarkCountClosed > 0
                ? `<div class="info-item" style="background:${session.has_warehouse_remark ? '#faf5ff' : '#eff6ff'};border:1px solid ${session.has_warehouse_remark ? '#ddd6fe' : '#bfdbfe'};border-radius:8px;padding:8px;">
                        <div class="info-label" style="color:${session.has_warehouse_remark ? '#7c3aed' : '#2563eb'};">Remarks</div>
                        <div class="info-value" style="color:${session.has_warehouse_remark ? '#6d28d9' : '#1d4ed8'};font-weight:700;">💬 ${remarkCountClosed} ${session.has_warehouse_remark ? 'WH reply' : 'remark' + (remarkCountClosed !== 1 ? 's' : '')}</div>
                    </div>`
                : '';
            const cardHighlightClosed = session.has_warehouse_remark
                ? 'border-top-color:#7c3aed;background:linear-gradient(180deg,#faf5ff 0%,#fff 60%);'
                : session.total_remarks > 0
                ? 'border-top-color:#2563eb;background:linear-gradient(180deg,#eff6ff 0%,#fff 60%);'
                : `border-top-color:${isTransfer ? '#f97316' : '#7c3aed'};`;

            return `
            <div class="session-card" style="${cardHighlightClosed}">
                <div class="session-header">
                    <div class="session-batch">${session.batch?.batch_id || 'Unknown'}${sessionTypeLabel}</div>
                    <div style="display:flex;align-items:center;gap:8px;">${remarkBadgeClosed}<span class="status-badge status-closed">COMPLETED</span></div>
                </div>
                <div class="session-info">
                    <div class="info-item">
                        <div class="info-label">${isTransfer ? 'Route' : 'Hospital'}</div>
                        <div class="info-value">${hospitalDisplay}</div>
                    </div>
                    <div class="info-item">
                        <div class="info-label">Products</div>
                        <div class="info-value">${session.scanned_products?.length || 0}</div>
                    </div>
                    <div class="info-item">
                        <div class="info-label">Sales Rep</div>
                        <div class="info-value">${_userName(session)}</div>
                    </div>
                    <div class="info-item">
                        <div class="info-label">Attachment</div>
                        <div class="info-value" style="font-size:0.85em;">${session.attachment_details?.original_filename || 'Uploaded'}</div>
                    </div>
                    ${remarkInfoClosed}
                </div>
                <div class="session-actions">
                    <a href="/product_scanning/session/${session.session_key}/" class="btn-primary">
                        🔍 View Details
                    </a>
                    ${canViewOnly ? '' : `<button class="btn-export" onclick="exportSession('${session.session_key}')">⬇ Export</button>`}
                </div>
            </div>`;
        }).join('');
    }

    // ── Expired Products ───────────────────────────────────────────────────
    let expiredProducts = [];
    let _expiredFilter  = 'all';

    const EP_CFG = {
        'expired': { bg:'#fee2e2', color:'#dc2626', label:'Expired'    },
        '1w':      { bg:'#fecdd3', color:'#be123c', label:'≤ 1 Week'   },
        '1m':      { bg:'#fef3c7', color:'#d97706', label:'≤ 1 Month'  },
        '3m':      { bg:'#fef9c3', color:'#a16207', label:'≤ 3 Months' },
        '6m':      { bg:'#dcfce7', color:'#16a34a', label:'≤ 6 Months' },
    };

    function epBadge(dateStr, level) {
        const c = EP_CFG[level] || { bg:'#f1f5f9', color:'#64748b', label:'—' };
        const d = dateStr || '—';
        return `<span style="display:inline-flex;flex-direction:column;align-items:center;background:${c.bg};color:${c.color};border-radius:6px;padding:2px 7px;font-size:.75rem;font-weight:700;line-height:1.3;white-space:nowrap;">`
             + `${d}<small style="font-size:.68rem;font-weight:500;">${c.label}</small></span>`;
    }

    function setExpiredFilter(btn, level) {
        _expiredFilter = level;
        document.querySelectorAll('.ep-filter').forEach(b => b.classList.remove('active'));
        btn.classList.add('active');
        renderExpiredProducts();
    }

    function renderExpiredProducts() {
        const tbody = document.getElementById('expiredProductsBody');
        let rows = expiredProducts;
        if (_expiredFilter !== 'all') {
            rows = rows.filter(p => p.expiry_level === _expiredFilter);
        }
        if (rows.length === 0) {
            tbody.innerHTML = '<tr><td colspan="11" style="text-align:center;padding:40px;color:#888;">No products in this category.</td></tr>';
            return;
        }
        tbody.innerHTML = rows.map(p => {
            const c = EP_CFG[p.expiry_level];
            const rowBg = c && p.expiry_level !== '6m' ? `background:${c.bg};` : '';
            const days = p.days_to_expiry !== undefined ? p.days_to_expiry : p.days_expired;
            const daysLabel = days !== null && days !== undefined
                ? (days < 0 ? `${Math.abs(days)}d ago` : `in ${days}d`)
                : '—';
            return `<tr class="${!p.acknowledged ? 'expired-unack' : ''}" style="${rowBg}">
                <td><span class="cell-mono">${p.serial_number || '—'}</span></td>
                <td>${p.product_name || '—'}</td>
                <td><span class="cell-mono">${p.gtin_code || '—'}</span></td>
                <td>${p.batch_number || '—'}</td>
                <td>${p.hospital_name || '—'}</td>
                <td>${epBadge(p.expiry_date, p.expiry_level)}</td>
                <td style="font-size:.85em;text-align:center;white-space:nowrap;">${daysLabel}</td>
                <td style="font-size:0.85em;">${p.session_batch_id || p.session_key || '—'}</td>
                <td>${p.scanned_by_name || '—'}</td>
                <td>${p.scanned_at ? new Date(p.scanned_at).toLocaleDateString() : '—'}</td>
                <td>${p.acknowledged
                    ? '<span class="ack-done" title="Acknowledged">✓</span>'
                    : (canViewOnly ? '<span style="color:#ccc;font-size:.8em;">—</span>' : `<button class="btn-ack" onclick="acknowledgeExpired(${p.id})" title="Acknowledge">Ack</button>`)
                }</td>
            </tr>`;
        }).join('');
    }

    async function loadExpiredProducts() {
        const tbody = document.getElementById('expiredProductsBody');
        tbody.innerHTML = '<tr><td colspan="11" style="text-align:center;padding:40px;color:#888;"><div class="spinner" style="margin-bottom:15px;"></div>Loading...</td></tr>';

        try {
            const resp = await fetch('/product_scanning/api/expired/', {
                method: 'GET',
                headers: { 'Content-Type': 'application/json', 'X-CSRFToken': getCookie('csrftoken') },
                credentials: 'same-origin'
            });
            const data = await resp.json();
            expiredProducts = data.results || data || [];

            document.getElementById('expiredCount').textContent = expiredProducts.length;

            if (expiredProducts.length === 0) {
                tbody.innerHTML = '<tr><td colspan="11" style="text-align:center;padding:40px;color:#888;">✅ No expired or near-expiry products found.</td></tr>';
                return;
            }

            renderExpiredProducts();

            initDataTable('expiredProductsTable', {
                order: [[6, 'asc']],
                scrollX: true,
                columnDefs: [
                    { targets: [2, 7], className: 'dt-hide-mobile' },
                    { targets: [10], orderable: false, className: 'dt-center' },
                ]
            });
        } catch (err) {
            console.error('Error loading expired products:', err);
            tbody.innerHTML = '<tr><td colspan="11" style="text-align:center;padding:40px;color:#dc2626;">Error loading products</td></tr>';
        }
    }

    async function acknowledgeExpired(id) {
        try {
            await fetch('/product_scanning/api/expired/acknowledge/', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json', 'X-CSRFToken': getCookie('csrftoken') },
                credentials: 'same-origin',
                body: JSON.stringify({ id })
            });
            showAlert('Expired product acknowledged', 'success');
            loadExpiredProducts();
        } catch (err) {
            showAlert('Failed to acknowledge', 'error');
        }
    }

    function exportExpiredProducts() {
        openExportModal('expired');
    }

    // ── Export date-range modal ────────────────────────────────────────────
    let _exportType = 'all'; // 'all' | 'expired'

    function openExportModal(type) {
        _exportType = type || 'all';
        // Default: last 30 days
        const today = new Date();
        const from  = new Date(today);
        from.setDate(today.getDate() - 29);
        document.getElementById('exportDateFrom').value = from.toISOString().split('T')[0];
        document.getElementById('exportDateTo').value   = today.toISOString().split('T')[0];
        document.getElementById('exportDateError').style.display = 'none';
        document.getElementById('exportDateModal').style.display = 'flex';
    }

    function closeExportModal() {
        document.getElementById('exportDateModal').style.display = 'none';
    }

    function setExportPreset(days) {
        const today = new Date();
        const from  = new Date(today);
        from.setDate(today.getDate() - (days - 1));
        document.getElementById('exportDateFrom').value = from.toISOString().split('T')[0];
        document.getElementById('exportDateTo').value   = today.toISOString().split('T')[0];
        document.getElementById('exportDateError').style.display = 'none';
    }

    function confirmExport() {
        const dateFrom = document.getElementById('exportDateFrom').value;
        const dateTo   = document.getElementById('exportDateTo').value;
        const errEl    = document.getElementById('exportDateError');
        errEl.style.display = 'none';

        if (!dateFrom || !dateTo) {
            errEl.textContent = 'Please select both From and To dates.';
            errEl.style.display = 'block';
            return;
        }
        const d1 = new Date(dateFrom), d2 = new Date(dateTo);
        if (d2 < d1) {
            errEl.textContent = '"To" date must be on or after "From" date.';
            errEl.style.display = 'block';
            return;
        }
        const diffDays = (d2 - d1) / (1000 * 60 * 60 * 24);
        if (diffDays > 62) {
            errEl.textContent = 'Date range exceeds 62 days. Please narrow your selection.';
            errEl.style.display = 'block';
            return;
        }

        closeExportModal();
        const endpoint = _exportType === 'expired'
            ? `/product_scanning/api/sessions/export-expired-csv/?date_from=${dateFrom}&date_to=${dateTo}`
            : `/product_scanning/api/sessions/export-csv/?date_from=${dateFrom}&date_to=${dateTo}`;

        // Trigger server-side file download
        const btn = document.getElementById('exportDateConfirmBtn');
        btn.disabled = true;
        btn.textContent = 'Downloading…';
        fetch(endpoint, { credentials: 'same-origin', headers: { 'X-CSRFToken': getCookie('csrftoken') } })
            .then(resp => {
                if (!resp.ok) return resp.json().then(e => { throw new Error(e.error || `HTTP ${resp.status}`); });
                return resp.blob();
            })
            .then(blob => {
                const type = _exportType === 'expired' ? 'ExpiredProducts' : 'StockAudit';
                const url  = URL.createObjectURL(blob);
                const a    = document.createElement('a');
                a.href     = url;
                a.download = `Meril_${type}_${dateFrom}_to_${dateTo}.csv`;
                document.body.appendChild(a);
                a.click();
                URL.revokeObjectURL(url);
                document.body.removeChild(a);
                showAlert(`✅ Export downloaded (${dateFrom} → ${dateTo})`, 'success');
            })
            .catch(err => {
                showAlert(`❌ Export failed: ${err.message}`, 'error');
            })
            .finally(() => {
                btn.disabled = false;
                btn.textContent = '⬇ Export CSV';
            });
    }

    async function loadAllProducts() {
        const tbody = document.getElementById('allProductsBody');

        // Build enriched product list from in-session scanned_products (lazy — no separate API call)
        const enrichedProducts = [];
        getFilteredSessions().forEach(session => {
            const batchId    = session.batch?.batch_id || '—';
            const hospName   = session.hospital?.name  || '—';
            const salesperson = _userName(session);
            (session.scanned_products || []).forEach(p => {
                enrichedProducts.push({ ...p, _batchId: batchId, _hospital: hospName, _salesperson: salesperson });
            });
        });

        // Scalability guard: show a prominent warning when the dataset is large
        const LARGE_THRESHOLD = 5000;
        const totalCount = enrichedProducts.length;
        const warnEl = document.getElementById('allStockScalabilityWarning');
        if (warnEl) {
            if (totalCount >= LARGE_THRESHOLD) {
                warnEl.style.display = 'block';
                warnEl.textContent   = `⚠️ ${totalCount.toLocaleString()} records loaded. For large exports use "Export All CSV" with a date range filter to avoid browser timeouts.`;
            } else {
                warnEl.style.display = 'none';
            }
        }

        document.getElementById('totalProducts').textContent = totalCount;

        if (enrichedProducts.length === 0) {
            tbody.innerHTML = `
                <tr>
                    <td colspan="9" style="text-align: center; padding: 40px; color: #888;">
                        No products scanned yet
                    </td>
                </tr>
            `;
            return;
        }

        tbody.innerHTML = enrichedProducts.map(product => {
            const sn = (product.serial_number && !product.serial_number.startsWith('NSN-'))
                ? `<span class="serial-code">${product.serial_number}</span>`
                : '<em style="color:#9ca3af;">No Serial</em>';
            const expiryDate = product.expiry_date ? new Date(product.expiry_date) : null;
            const today = new Date();
            let expiryText = product.expiry_date ? new Date(product.expiry_date).toLocaleDateString() : '—';
            let expiryStyle = '';
            if (expiryDate) {
                if (expiryDate < today) { expiryStyle = 'color:#dc2626;font-weight:600;'; expiryText += ' ⚠️'; }
                else if ((expiryDate - today) / (1000 * 60 * 60 * 24) < 30) { expiryStyle = 'color:#f97316;font-weight:600;'; }
            }
            return `<tr>
                <td>${sn}</td>
                <td><span class="gtin-code">${product.gtin_code || '—'}</span></td>
                <td>${product.batch_number || '—'}</td>
                <td>${product.product_name || '—'}</td>
                <td>${product.material_code || '—'}</td>
                <td style="font-size:0.85em;color:#0076c8;font-weight:600;">${product._batchId}</td>
                <td style="font-size:0.85em;">${product._hospital}</td>
                <td style="${expiryStyle}">${expiryText}</td>
                <td>${product.production_date ? new Date(product.production_date).toLocaleDateString() : '—'}</td>
                <td>${new Date(product.scanned_at).toLocaleDateString()}</td>
            </tr>`;
        }).join('');

        initDataTable('allProductsTable', {
            order: [[9, 'desc']],
            scrollX: true,
            columnDefs: [
                { targets: [4, 8], className: 'dt-hide-mobile' },  // Material Code, Prod. Date
            ]
        });
    }

    async function terminateSession(sessionKey) {
        const session = allSessions.find(s => s.session_key === sessionKey);
        const salesperson = session ? _userName(session) : 'the salesperson';
        const reason = prompt(`Terminate session for ${salesperson}?\n\nThis will permanently cancel the session. Enter a reason:`);
        if (!reason) return;

        try {
            const resp = await fetch(API.terminate(), {
                method: 'POST',
                headers: { 'Content-Type': 'application/json', 'X-CSRFToken': getCookie('csrftoken') },
                credentials: 'same-origin',
                body: JSON.stringify({ session_key: sessionKey, reason: reason }),
            });
            const result = await resp.json();
            if (!resp.ok) throw new Error(result.error || 'Terminate failed');
            showAlert('Session terminated successfully.', 'success');
            setTimeout(() => loadData(), 800);
        } catch (err) {
            showAlert(`${err.message}`, 'error');
        }
    }

    async function forceCompleteSession(sessionKey) {
        const session = allSessions.find(s => s.session_key === sessionKey);
        const salesperson = session ? _userName(session) : 'the salesperson';
        if (!confirm(`Force-complete audit session for ${salesperson}?\n\nThis will submit it on their behalf and move it to "Awaiting Confirmation" status.`)) return;

        try {
            const resp = await fetch(API.forceComplete(), {
                method: 'POST',
                headers: { 'Content-Type': 'application/json', 'X-CSRFToken': getCookie('csrftoken') },
                credentials: 'same-origin',
                body: JSON.stringify({ session_key: sessionKey }),
            });
            const result = await resp.json();
            if (!resp.ok) throw new Error(result.error || 'Force-complete failed');
            showAlert(`✅ Audit session force-submitted. It now appears under "Awaiting Confirmation" for document upload.`, 'success');
            setTimeout(() => loadData(), 800);
        } catch (err) {
            showAlert(`❌ ${err.message}`, 'error');
        }
    }

    function exportAllProducts() {
        openExportModal('all');
    }

    function _exportAllProductsLegacy() {
        // Build enriched product list from sessions
        const rows = [];
        allSessions.forEach(session => {
            const batchId  = session.batch?.batch_id || '';
            const hospName = session.hospital?.name  || '';
            const person   = _userName(session);
            (session.scanned_products || []).forEach(p => {
                const sn = (p.serial_number && !p.serial_number.startsWith('NSN-')) ? p.serial_number : '';
                rows.push(`"${sn}","${p.gtin_code||''}","${p.batch_number||''}","${p.product_name||''}","${p.material_code||''}","${batchId}","${hospName}","${p.expiry_date||''}","${p.production_date||''}","${new Date(p.scanned_at).toLocaleDateString()}","${person}"`);
            });
        });

        if (rows.length === 0) { showAlert('No data to export', 'error'); return; }

        const csv = 'Serial Number,GTIN Code,Lot/Batch No.,Product Name,Material Code,Session ID,Hospital,Expiry Date,Prod. Date,Scanned Date,Sales Rep\n' + rows.join('\n');
        const blob = new Blob([csv], { type: 'text/csv' });
        const url  = window.URL.createObjectURL(blob);
        const a    = document.createElement('a');
        a.href = url;
        a.download = `Meril_SA_StockAudit_${new Date().toISOString().split('T')[0]}.csv`;
        document.body.appendChild(a);
        a.click();
        window.URL.revokeObjectURL(url);
        document.body.removeChild(a);
        showAlert(`✅ Exported ${rows.length} products`, 'success');
    }

    function exportSession(sessionKey) {
        const session = allSessions.find(s => s.session_key === sessionKey);
        if (!session) {
            showAlert('Session not found', 'error');
            return;
        }

        const products = session.scanned_products || [];
        const scannedByName = _userName(session);
        const isTransfer = session.session_type === 'transfer';
        const hospitalName = session.hospital?.name || '';
        const toHospitalName = session.to_hospital_details?.name || '';
        
        // Include Session Type and To Hospital columns for transfers
        let csv = 'Batch ID,Session Type,Hospital,To Hospital,Sales Rep,Serial Number,GTIN Code,Lot/Batch No.,Product Name,Expiry Date,Prod. Date,Material Code,Quantity,Scanned Date\n';

        products.forEach(product => {
            csv += `"${session.batch?.batch_id}","${isTransfer ? 'H2H Transfer' : 'Stock Audit'}","${hospitalName}","${toHospitalName}","${scannedByName}","${product.serial_number}","${product.gtin_code || ''}","${product.batch_number || ''}","${product.product_name || ''}","${product.expiry_date || ''}","${product.production_date || ''}","${product.material_code || ''}","${product.quantity || 1}","${new Date(product.scanned_at).toLocaleDateString()}"\n`;
        });

        const blob = new Blob([csv], { type: 'text/csv' });
        const url = window.URL.createObjectURL(blob);
        const a = document.createElement('a');
        a.href = url;
        a.download = `${session.batch?.batch_id}_${new Date().toISOString().split('T')[0]}.csv`;
        document.body.appendChild(a);
        a.click();
        window.URL.revokeObjectURL(url);
        document.body.removeChild(a);

        showAlert(`✅ Exported ${products.length} products`, 'success');
    }

    function exportData() {
        const filter = document.getElementById('exportFilter').value;
        const now = new Date();
        let fromDate = null;

        if (filter === 'today') {
            fromDate = new Date();
            fromDate.setHours(0, 0, 0, 0);
        } else if (filter === 'week') {
            fromDate = new Date();
            fromDate.setDate(now.getDate() - 7);
        } else if (filter === 'month') {
            fromDate = new Date();
            fromDate.setMonth(now.getMonth() - 1);
        }
        // filter === 'all' → fromDate stays null → no filtering

        const filteredSessions = fromDate
            ? allSessions.filter(s => new Date(s.started_at) >= fromDate)
            : allSessions;
        let csv = 'Batch ID,Session Type,Hospital,To Hospital,Sales Rep,Serial Number,GTIN Code,Lot/Batch No.,Product Name,Material Code,Expiry Date,Prod. Date,Quantity,Scanned Date\n';

        filteredSessions.forEach(session => {
            const scannedByName = _userName(session);
            const isTransfer = session.session_type === 'transfer';
            const hospitalName = session.hospital?.name || '';
            const toHospitalName = session.to_hospital_details?.name || '';
            (session.scanned_products || []).forEach(product => {
                csv += `"${session.batch?.batch_id}","${isTransfer ? 'H2H Transfer' : 'Stock Audit'}","${hospitalName}","${toHospitalName}","${scannedByName}","${product.serial_number}","${product.gtin_code || ''}","${product.batch_number || ''}","${product.product_name || ''}","${product.material_code || ''}","${product.expiry_date || ''}","${product.production_date || ''}","${product.quantity || 1}","${new Date(product.scanned_at).toLocaleDateString()}"\n`;
            });
        });

        const blob = new Blob([csv], { type: 'text/csv' });
        const url = window.URL.createObjectURL(blob);
        const a = document.createElement('a');
        a.href = url;
        a.download = `SAP_Export_${new Date().toISOString().split('T')[0]}.csv`;
        document.body.appendChild(a);
        a.click();
        window.URL.revokeObjectURL(url);
        document.body.removeChild(a);

        showAlert(`✅ Downloaded data for ${filteredSessions.length} sessions`, 'success');
    }

    // Helper: get user's display name from a session object
    const _userName = s => {
        const u = s.scanned_by_details;
        if (!u) return 'Unknown';
        const full = `${u.first_name || ''} ${u.last_name || ''}`.trim();
        return full || u.username || 'Unknown';
    };

    // Helper: get batch ID string
    const _batchId = s => {
        return s.batch?.batch_id || s.batch_details?.batch_id || 'Unknown';
    };

    // Load on page load
    document.addEventListener('DOMContentLoaded', loadData);
    // Refresh every 30 seconds
    setInterval(loadData, 30000);
