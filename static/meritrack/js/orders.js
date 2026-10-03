const API = '/meritrack/api';
let catalogProducts = [];
let selectedHospitalDepts = [];
let ordersData = [];  // cache full order objects for detail modal
let currentOrderIdx = -1;  // track which order is open in the detail modal

function showTab(tab) {
    document.querySelectorAll('.mt-pill-tab').forEach((b, i) => b.classList.toggle('active', i === (tab === 'list' ? 0 : 1)));
    document.getElementById('tabList').classList.toggle('hidden', tab !== 'list');
    document.getElementById('tabNew').classList.toggle('hidden', tab !== 'new');
    if (tab === 'list') loadOrders();
    if (tab === 'new') resetOrderForm();
}

async function loadCatalog() {
    try {
        const resp = await fetch(`${API}/catalog/?page_size=1000`, { credentials: 'same-origin' });
        const data = await resp.json();
        catalogProducts = data.results || data || [];
        populateProductSelects();
    } catch (e) { 
        console.error('Failed to load products:', e);
        showFormError('Failed to load product catalog. Please refresh.');
    }
}

function populateProductSelects() {
    if (catalogProducts.length === 0) {
        showFormError('No products available in catalog.');
        return;
    }

    // Build options string once to avoid O(n²) innerHTML += in loop
    const optionsHtml = '<option value="">Select product...</option>' +
        catalogProducts.map(p => {
            const label = [p.material_code, p.description || p.name, p.size].filter(Boolean).join(' — ');
            return `<option value="${p.id}">${escapeHtml(label)}</option>`;
        }).join('');

    document.querySelectorAll('.item-product').forEach(sel => {
        const currentValue = sel.value;
        sel.innerHTML = optionsHtml;
        if (currentValue) sel.value = currentValue;
    });
}

async function onHospitalChange() {
    const hospitalId = document.getElementById('orderHospital').value;
    const deptSel = document.getElementById('orderDepartment');
    selectedHospitalDepts = [];
    
    deptSel.innerHTML = '<option value="">Use Default Department</option>';
    deptSel.disabled = !hospitalId;
    
    clearFormError();
    updateOrderStatus();
    
    if (!hospitalId) return;
    
    try {
        const resp = await fetch(`${API}/departments/by-hospital/${hospitalId}/`, { credentials: 'same-origin' });
        if (!resp.ok) throw new Error('Failed to load departments');
        
        const depts = await resp.json();
        if (!Array.isArray(depts) || depts.length === 0) {
            showFormError('No departments available for this hospital.');
            return;
        }
        
        selectedHospitalDepts = depts;
        depts.forEach(d => { 
            deptSel.innerHTML += `<option value="${d.id}">${escapeHtml(d.name)}${d.floor ? ' (Floor ' + d.floor + ')' : ''}</option>`; 
        });
    } catch (e) {
        console.error('Error loading departments:', e);
        showFormError('Failed to load departments. Try selecting another hospital.');
    }
}

function addOrderItem() {
    const container = document.getElementById('orderItems');
    const div = document.createElement('div');
    div.className = 'order-item';
    div.innerHTML = `<select class="item-product" required><option value="">Select product...</option></select>
        <input type="number" class="item-qty" value="1" min="1" step="1" placeholder="Qty" required>
        <button type="button" class="remove-item" onclick="this.closest('.order-item').remove(); updateOrderStatus()"><i class="fas fa-times"></i></button>`;
    container.appendChild(div);
    populateProductSelects();
    updateOrderStatus();
}

function updateOrderStatus() {
    const items = document.querySelectorAll('.order-item');
    const statusDiv = document.getElementById('orderStatus');
    const submitBtn = document.getElementById('submitBtn');
    
    let validItems = 0;
    items.forEach(item => {
        const product = item.querySelector('.item-product').value;
        const qty = item.querySelector('.item-qty').value;
        if (product && qty && parseInt(qty) > 0) validItems++;
    });
    
    const hospitalSelected = !!document.getElementById('orderHospital').value;
    
    if (validItems > 0) {
        statusDiv.innerHTML = `<i class="fas fa-check-circle" style="color:var(--mt-success)"></i> ${validItems} item${validItems !== 1 ? 's' : ''} added`;
        statusDiv.style.color = 'var(--mt-success)';
    } else {
        statusDiv.innerHTML = '<i class="fas fa-info-circle"></i> Add at least one product';
        statusDiv.style.color = 'var(--mt-gray-400)';
    }
    
    submitBtn.disabled = !hospitalSelected || validItems === 0;
}

function showFormError(msg) {
    const errorDiv = document.getElementById('formError');
    if (msg) {
        errorDiv.innerHTML = `<i class="fas fa-exclamation-circle"></i> ${msg}`;
        errorDiv.style.display = 'block';
    }
}

function clearFormError() {
    document.getElementById('formError').style.display = 'none';
}

function resetOrderForm() {
    document.getElementById('orderHospital').value = '';
    document.getElementById('orderDepartment').value = '';
    document.getElementById('orderUrgency').value = 'normal';
    document.getElementById('orderNotes').value = '';
    
    const container = document.getElementById('orderItems');
    container.innerHTML = `<div class="order-item">
        <select class="item-product" required><option value="">Select product...</option></select>
        <input type="number" class="item-qty" value="1" min="1" step="1" placeholder="Qty" required>
        <button type="button" class="remove-item" onclick="this.closest('.order-item').remove(); updateOrderStatus()"><i class="fas fa-times"></i></button>
    </div>`;
    
    populateProductSelects();
    clearFormError();
    updateOrderStatus();
}

async function loadOrders() {
    const status = document.getElementById('orderStatusFilter').value;
    let url = `${API}/orders/?page_size=200`;
    if (status) url += `&status=${status}`;
    
    try {
        const resp = await fetch(url, { credentials: 'same-origin' });
        if (!resp.ok) throw new Error('Failed to load orders');
        
        const data = await resp.json();
        const orders = data.results || data || [];
        ordersData = orders;
        const body = document.getElementById('ordersBody');
        const cardsEl = document.getElementById('orderCards');
        
        if (!Array.isArray(orders) || orders.length === 0) { 
            body.innerHTML = '<tr><td colspan="7" style="text-align:center;color:var(--mt-gray-400);">No orders found</td></tr>';
            cardsEl.innerHTML = '<p style="text-align:center;color:var(--mt-gray-400);padding:20px 0;">No orders found</p>';
            return; 
        }

        const statusBadge = { 'draft':'badge-gray','submitted':'badge-yellow','confirmed':'badge-blue','shipped':'badge-orange','delivered':'badge-green','cancelled':'badge-red' };
        const urgencyBadge = { 'normal':'badge-blue','urgent':'badge-orange','critical':'badge-red' };
        const fmtDate = dt => dt ? new Date(dt).toLocaleDateString('en-GB',{day:'2-digit',month:'short',year:'numeric'}) : '—';

        // Desktop table rows
        body.innerHTML = orders.map((o, idx) => {
            const itemCount = o.items?.length || 0;
            const isSubmitted = o.status === 'submitted';
            const approveBtn = isSubmitted
                ? `<button class='btn-mt btn-success btn-sm approve-btn' type='button' data-order-idx='${idx}' title='Approve Order'><i class='fas fa-check'></i> Approve</button>`
                : '';
            return `<tr class='order-row${isSubmitted ? ' order-pending-approval' : ''}' data-order-idx='${idx}' title='Click to view full details'>
                <td>
                    <strong style='color:var(--mt-primary);'>${escapeHtml(o.order_id || '—')}</strong>
                    ${o.source_session_key ? `<br><span style='font-size:0.75em;color:var(--mt-gray-600);'><i class='fas fa-barcode'></i> ${escapeHtml(o.source_session_key)}</span>` : ''}
                </td>
                <td>${escapeHtml(o.hospital_name || '—')}</td>
                <td><span class='badge ${urgencyBadge[o.urgency] || 'badge-gray'}'>${o.urgency || 'normal'}</span></td>
                <td><span class='badge ${statusBadge[o.status] || 'badge-gray'}'>${o.status || '—'}</span></td>
                <td><strong>${itemCount}</strong> <span style='color:var(--mt-gray-600);font-size:0.85em;'>${itemCount === 1 ? 'item' : 'items'}</span></td>
                <td style='font-size:0.85em;color:var(--mt-gray-600);'>${fmtDate(o.created_at)}</td>
                <td><div class='action-btns'>
                    <button class='btn-mt btn-primary btn-sm view-btn' type='button' data-order-idx='${idx}'><i class='fas fa-eye'></i> View</button>
                    ${approveBtn}
                    <button class='btn-mt btn-warning btn-sm status-btn' type='button' data-order-idx='${idx}' title='Update Status'><i class='fas fa-edit'></i></button>
                    <button class='btn-mt btn-secondary btn-sm print-btn' type='button' data-order-idx='${idx}' title='Print'><i class='fas fa-print'></i></button>
                </div></td>
            </tr>`;
        }).join('');

        // Mobile card list
        cardsEl.innerHTML = orders.map((o, idx) => {
            const itemCount = o.items?.length || 0;
            const isSubmitted = o.status === 'submitted';
            return `<div class='order-card${isSubmitted ? ' order-pending-approval' : ''}' data-order-idx='${idx}'>
                <div class='order-card-header'>
                    <span class='order-card-id'>${escapeHtml(o.order_id || '—')}</span>
                    <div style='display:flex;gap:4px;'>
                        <span class='badge ${urgencyBadge[o.urgency] || 'badge-gray'}'>${o.urgency || 'normal'}</span>
                        <span class='badge ${statusBadge[o.status] || 'badge-gray'}'>${o.status || '—'}</span>
                    </div>
                </div>
                ${o.source_session_key ? `<div style='font-size:0.78em;color:var(--mt-gray-600);margin-bottom:4px;'><i class='fas fa-barcode'></i> Session: ${escapeHtml(o.source_session_key)}</div>` : ''}
                <div class='order-card-meta'>
                    <span><i class='fas fa-hospital' style='color:var(--mt-gray-400);'></i> ${escapeHtml(o.hospital_name || '—')}</span>
                    <span><i class='fas fa-box' style='color:var(--mt-gray-400);'></i> ${itemCount} ${itemCount === 1 ? 'item' : 'items'}</span>
                    <span><i class='fas fa-calendar' style='color:var(--mt-gray-400);'></i> ${fmtDate(o.created_at)}</span>
                </div>
                <div class='order-card-actions'>
                    <button class='btn-mt btn-primary btn-sm view-btn' type='button' data-order-idx='${idx}'><i class='fas fa-eye'></i> View</button>
                    ${isSubmitted ? `<button class='btn-mt btn-success btn-sm approve-btn' type='button' data-order-idx='${idx}'><i class='fas fa-check'></i> Approve</button>` : ''}
                    <button class='btn-mt btn-warning btn-sm status-btn' type='button' data-order-idx='${idx}'><i class='fas fa-edit'></i> Update</button>
                    <button class='btn-mt btn-secondary btn-sm print-btn' type='button' data-order-idx='${idx}'><i class='fas fa-print'></i> Print</button>
                </div>
            </div>`;
        }).join('');

        // Attach event listeners to desktop rows and all action buttons
        document.querySelectorAll('.order-row').forEach(row => {
            const idx = parseInt(row.dataset.orderIdx);
            row.addEventListener('click', () => openOrderDetail(idx));
        });
        document.querySelectorAll('.order-card').forEach(card => {
            card.addEventListener('click', () => openOrderDetail(parseInt(card.dataset.orderIdx)));
        });
        document.querySelectorAll('.view-btn').forEach(btn => {
            btn.addEventListener('click', (e) => { e.stopPropagation(); openOrderDetail(parseInt(btn.dataset.orderIdx)); });
        });
        document.querySelectorAll('.approve-btn').forEach(btn => {
            btn.addEventListener('click', (e) => { e.stopPropagation(); approveOrder(parseInt(btn.dataset.orderIdx)); });
        });
        document.querySelectorAll('.status-btn').forEach(btn => {
            btn.addEventListener('click', (e) => { e.stopPropagation(); openStatusModal(parseInt(btn.dataset.orderIdx)); });
        });
        document.querySelectorAll('.print-btn').forEach(btn => {
            btn.addEventListener('click', (e) => { e.stopPropagation(); printOrder(parseInt(btn.dataset.orderIdx)); });
        });
    } catch (e) { 
        console.error('Error loading orders:', e);
        document.getElementById('ordersBody').innerHTML = '<tr><td colspan="7" style="text-align:center;color:var(--mt-danger);">Failed to load orders</td></tr>';
    }
}

function openOrderDetail(idx) {
    const o = ordersData[idx];
    if (!o) return;
    currentOrderIdx = idx;

    const statusBadge = { 'draft':'badge-gray','submitted':'badge-yellow','confirmed':'badge-blue','shipped':'badge-orange','delivered':'badge-green','cancelled':'badge-red' };
    const urgencyBadge = { 'normal':'badge-blue','urgent':'badge-orange','critical':'badge-red' };
    const urgencyLabel = { 'normal':'Normal Delivery','urgent':'Urgent (2–3 days)','critical':'Critical — Next Day' };
    const statusLabel = { 'draft':'Draft','submitted':'Submitted','confirmed':'Confirmed','shipped':'Shipped','delivered':'Delivered','cancelled':'Cancelled' };

    document.getElementById('modalOrderId').textContent = o.order_id || 'Order Detail';
    document.getElementById('modalBadges').innerHTML =
        `<span class="badge ${statusBadge[o.status] || 'badge-gray'}">${statusLabel[o.status] || o.status}</span>
         <span class="badge ${urgencyBadge[o.urgency] || 'badge-gray'}">${urgencyLabel[o.urgency] || o.urgency}</span>`;

    const fmt = dt => dt ? new Date(dt).toLocaleString('en-GB',{day:'2-digit',month:'short',year:'numeric',hour:'2-digit',minute:'2-digit'}) : '—';
    const fmtDate = dt => dt ? new Date(dt).toLocaleDateString('en-GB',{day:'2-digit',month:'short',year:'numeric'}) : '—';

    document.getElementById('modalGrid').innerHTML = `
        <div class="detail-cell"><label>Hospital</label><span>${escapeHtml(o.hospital_name || '—')}</span></div>
        <div class="detail-cell"><label>Department</label><span>${escapeHtml(o.department_name || '—')}</span></div>
        <div class="detail-cell"><label>Placed By</label><span>${escapeHtml(o.ordered_by_name || '—')}</span></div>
        <div class="detail-cell"><label>Placed On</label><span>${fmt(o.created_at)}</span></div>
        <div class="detail-cell"><label>Confirmed</label><span>${fmtDate(o.confirmed_at)}</span></div>
        <div class="detail-cell"><label>Shipped</label><span>${fmtDate(o.shipped_at)}</span></div>
        <div class="detail-cell"><label>Delivered</label><span>${fmtDate(o.delivered_at)}</span></div>
        <div class="detail-cell"><label>Last Updated</label><span>${fmt(o.updated_at)}</span></div>
        ${o.source_session_key ? `<div class="detail-cell" style="grid-column:1/-1"><label><i class="fas fa-barcode"></i> Source Session</label><span><a href="/meritrack/session/${escapeHtml(o.source_session_key)}/" style="color:var(--mt-primary);font-family:monospace;">${escapeHtml(o.source_session_key)}</a> <span style="font-size:0.82em;color:var(--mt-gray-600);">(auto-generated from replenishment scan)</span></span></div>` : ''}
    `;

    const items = o.items || [];
    const tbody = document.getElementById('modalItemsBody');
    if (items.length === 0) {
        tbody.innerHTML = '<tr><td colspan="6" style="text-align:center;color:var(--mt-gray-400);padding:16px;">No items on this order</td></tr>';
    } else {
        tbody.innerHTML = items.map((item, i) => `
            <tr>
                <td style="color:var(--mt-gray-400);">${i + 1}</td>
                <td><strong>${escapeHtml(item.product_name || '—')}</strong></td>
                <td style="font-family:monospace;font-size:0.83em;color:var(--mt-gray-700);">${escapeHtml(item.catalog_product?.material_code || '—')}</td>
                <td style="font-size:0.85em;">${escapeHtml(item.product_size || '—')}</td>
                <td style="text-align:right;"><strong>${item.quantity || 1}</strong></td>
                <td style="color:var(--mt-gray-600);font-size:0.82em;">${escapeHtml(item.notes || '—')}</td>
            </tr>
        `).join('');

        if (items.length > 1) {
            const totalQty = items.reduce((s, i) => s + (i.quantity || 1), 0);
            tbody.innerHTML += `<tr style="background:var(--mt-primary-lighter);font-weight:700;border-top:2px solid var(--mt-info-border);">
                <td colspan="4" style="text-align:right;color:var(--mt-primary-dark);padding:10px 12px;">Total Quantity</td>
                <td style="text-align:right;color:var(--mt-primary-dark);">${totalQty}</td>
                <td></td>
            </tr>`;
        }
    }

    const notesEl = document.getElementById('modalNotes');
    if (o.notes && o.notes.trim()) {
        document.getElementById('modalNotesText').textContent = o.notes;
        notesEl.style.display = 'block';
    } else {
        notesEl.style.display = 'none';
    }

    document.getElementById('orderDetailModal').classList.add('active');
    document.body.style.overflow = 'hidden';

    // Show/hide approve button in modal footer based on status
    const approveModalBtn = document.getElementById('modalApproveBtn');
    if (approveModalBtn) {
        approveModalBtn.style.display = (o.status === 'submitted') ? 'inline-flex' : 'none';
    }
}

function closeModal() {
    document.getElementById('orderDetailModal').classList.remove('active');
    document.body.style.overflow = '';
}

function openStatusModal(idx) {
    const o = ordersData[idx];
    if (!o) return;
    currentOrderIdx = idx;
    document.getElementById('statusModalOrderId').textContent = o.order_id || '';
    document.getElementById('statusModalSelect').value = o.status || 'submitted';
    document.getElementById('statusModalError').style.display = 'none';
    document.getElementById('statusModal').classList.add('active');
    document.body.style.overflow = 'hidden';
}

function closeStatusModal() {
    document.getElementById('statusModal').classList.remove('active');
    document.body.style.overflow = '';
}

async function saveStatus() {
    const o = ordersData[currentOrderIdx];
    if (!o) return;
    const newStatus = document.getElementById('statusModalSelect').value;
    const errEl = document.getElementById('statusModalError');
    const btn = document.getElementById('statusSaveBtn');
    errEl.style.display = 'none';
    btn.disabled = true;
    btn.innerHTML = '<i class="fas fa-spinner fa-spin"></i> Saving...';
    try {
        const resp = await fetch(`${API}/orders/${o.id}/update_status/`, {
            method: 'POST',
            credentials: 'same-origin',
            headers: { 'Content-Type': 'application/json', 'X-CSRFToken': CSRF },
            body: JSON.stringify({ status: newStatus }),
        });
        const data = await resp.json();
        if (!resp.ok) {
            errEl.textContent = data.error || `Error ${resp.status}`;
            errEl.style.display = 'block';
            return;
        }
        // Update cache and refresh table
        ordersData[currentOrderIdx] = data;
        closeStatusModal();
        closeModal();
        loadOrders();
    } catch (e) {
        errEl.textContent = 'Network error. Please try again.';
        errEl.style.display = 'block';
    } finally {
        btn.disabled = false;
        btn.innerHTML = '<i class="fas fa-check"></i> Save';
    }
}

async function approveOrder(idx) {
    const o = ordersData[idx];
    if (!o) return;
    if (!confirm(`Confirm approval of order ${o.order_id}?\n\nThis will mark it as Confirmed and notify the rep.`)) return;

    try {
        const resp = await fetch(`${API}/orders/${o.id}/update_status/`, {
            method: 'POST',
            credentials: 'same-origin',
            headers: { 'Content-Type': 'application/json', 'X-CSRFToken': CSRF },
            body: JSON.stringify({ status: 'confirmed' }),
        });
        const data = await resp.json();
        if (!resp.ok) {
            alert(data.error || `Error ${resp.status}: Could not approve order`);
            return;
        }
        ordersData[idx] = data;
        closeModal();
        loadOrders();
    } catch (e) {
        alert('Network error. Please try again.');
    }
}

function printOrder(idx) {
    const o = ordersData[idx];
    if (!o) return;

    const fmt = dt => dt ? new Date(dt).toLocaleDateString('en-GB', {day:'2-digit',month:'short',year:'numeric'}) : '—';
    const items = o.items || [];
    const rows = items.map((item, i) => `
        <tr>
            <td class="print-num">${i+1}</td>
            <td>${escapeHtml(item.product_name || '—')}</td>
            <td class="print-code">${escapeHtml(item.catalog_product?.material_code || '—')}</td>
            <td>${escapeHtml(item.product_size || '—')}</td>
            <td class="print-num">${item.quantity || 1}</td>
        </tr>`).join('');
    const totalQty = items.reduce((s, i) => s + (i.quantity || 1), 0);

    const meta = (label, value) => `<div><dt>${label}</dt><dd>${escapeHtml(value || '—')}</dd></div>`;

    const area = document.getElementById('printArea');

    // #printArea normally sits deep inside <main>. Hoisting it to <body> lets
    // the print stylesheet drop every other top-level node with display:none,
    // which is what stops the app chrome reserving blank leading pages.
    if (area.parentNode !== document.body) document.body.appendChild(area);

    area.innerHTML = `
        <div class="print-head">
            <div class="print-logo">MeriTrack<small>Stock Order</small></div>
            <div class="print-orderid">${escapeHtml(o.order_id)}<small>Order ID</small></div>
        </div>
        <dl class="print-meta">
            ${meta('Hospital', o.hospital_name)}
            ${meta('Status', o.status)}
            ${meta('Department', o.department_name)}
            ${meta('Urgency', o.urgency)}
            ${meta('Ordered By', o.ordered_by_name)}
            ${meta('Order Date', fmt(o.created_at))}
        </dl>
        ${o.notes ? `<div class="print-notes"><strong>Notes</strong>${escapeHtml(o.notes)}</div>` : ''}
        <table class="print-table">
            <thead>
                <tr>
                    <th style="width:6%;">#</th>
                    <th>Product</th>
                    <th style="width:20%;">Material Code</th>
                    <th style="width:14%;">Size</th>
                    <th style="width:10%;">Qty</th>
                </tr>
            </thead>
            <tbody>${rows}</tbody>
            <tfoot>
                <tr>
                    <td colspan="4" class="print-num">Total (${items.length} line${items.length === 1 ? '' : 's'})</td>
                    <td class="print-num">${totalQty}</td>
                </tr>
            </tfoot>
        </table>
        <div class="print-sign">
            <div>Prepared by</div>
            <div>Checked by</div>
            <div>Authorised signatory</div>
        </div>
        <div class="print-foot">
            <span>MeriTrack — Stock Order ${escapeHtml(o.order_id)}</span>
            <span>Printed ${new Date().toLocaleString('en-GB')}</span>
        </div>
    `;

    // Chrome builds its preview asynchronously, so the old 500ms timer could
    // tear the document down mid-render. afterprint fires once the dialog is
    // actually done; matchMedia covers browsers that skip the event.
    document.body.classList.add('mt-print-doc');

    let done = false;
    const mql = window.matchMedia('print');
    const cleanup = () => {
        if (done) return;
        done = true;
        window.removeEventListener('afterprint', cleanup);
        if (mql.removeEventListener) mql.removeEventListener('change', onMedia);
        document.body.classList.remove('mt-print-doc');
        area.innerHTML = '';
    };
    const onMedia = e => { if (!e.matches) cleanup(); };

    window.addEventListener('afterprint', cleanup);
    if (mql.addEventListener) mql.addEventListener('change', onMedia);

    window.print();
}

async function placeOrder() {
    const hospitalId = document.getElementById('orderHospital').value;
    const departmentId = document.getElementById('orderDepartment').value;
    const urgency = document.getElementById('orderUrgency').value;
    const notes = document.getElementById('orderNotes').value;

    clearFormError();

    if (!hospitalId) { 
        showFormError('Please select a hospital.');
        return; 
    }

    const items = [];
    document.querySelectorAll('.order-item').forEach(row => {
        const pid = row.querySelector('.item-product').value;
        const qty = parseInt(row.querySelector('.item-qty').value);
        if (pid && qty && qty > 0) {
            items.push({ catalog_product_id: parseInt(pid), quantity: qty });
        }
    });
    
    if (items.length === 0) { 
        showFormError('Please add at least one item to the order.');
        return; 
    }

    // Disable submit button and show loading state
    const submitBtn = document.getElementById('submitBtn');
    const originalText = submitBtn.innerHTML;
    submitBtn.disabled = true;
    submitBtn.innerHTML = '<i class="fas fa-spinner fa-spin"></i> Submitting...';

    try {
        const resp = await fetch(`${API}/orders/place_order/`, {
            method: 'POST', 
            credentials: 'same-origin',
            headers: { 'Content-Type': 'application/json', 'X-CSRFToken': CSRF },
            body: JSON.stringify({ 
                hospital_id: parseInt(hospitalId), 
                department_id: departmentId ? parseInt(departmentId) : null, 
                urgency, 
                notes, 
                items 
            }),
        });
        
        // Safely parse response — server may return HTML on unhandled errors
        let data = null;
        const contentType = resp.headers.get('content-type') || '';
        if (contentType.includes('application/json')) {
            data = await resp.json();
        } else {
            const text = await resp.text();
            console.error('Non-JSON response from server:', resp.status, text.substring(0, 500));
        }

        if (resp.ok) { 
            showTab('list');
            const orderId = data?.order_id || 'your order';
            alert(`✓ Order ${orderId} placed successfully!\n\nStatus: ${data?.status}\nUrgency: ${data?.urgency}`);
            loadOrders();
        } else { 
            const msg = data?.error || `Server error (${resp.status}). Please try again or contact support.`;
            showFormError(msg);
        }
    } catch (e) { 
        console.error('Network error:', e);
        showFormError('Network error. Please check your connection and try again.');
    } finally {
        submitBtn.disabled = false;
        submitBtn.innerHTML = originalText;
    }
}

function escapeHtml(s) {
    if (!s) return '';
    const div = document.createElement('div');
    div.textContent = s;
    return div.innerHTML;
}

// Initialize
document.addEventListener('DOMContentLoaded', function() {
    loadOrders();
    loadCatalog();
    document.querySelectorAll('.item-qty').forEach(input => {
        input.addEventListener('change', updateOrderStatus);
    });
});

// Update status when product or quantity changes
document.addEventListener('change', function(e) {
    if (e.target.classList.contains('item-product') || e.target.classList.contains('item-qty')) {
        updateOrderStatus();
    }
});

// Close modal on Escape key
document.addEventListener('keydown', function(e) {
    if (e.key === 'Escape') closeModal();
});
