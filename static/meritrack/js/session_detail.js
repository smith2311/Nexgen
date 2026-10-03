const API = '/meritrack/api';
let _sessionData = null;
let _productRemarks = {};
let _currentRemarkProductId = null;
let _currentRemarkSerial = null;
let _attachmentFile = null;
let _dtInstance = null;

// ─── Utilities ───────────────────────────────────────────────────────────────
function esc(s) {
    return String(s || '').replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;');
}

function escCSV(val) {
    const s = String(val || '').replace(/"/g, '""');
    return `"${s}"`;
}

function getExpiryClass(d) {
    if (!d) return 'badge-gray';
    const days = (new Date(d) - new Date()) / 86400000;
    if (days < 0) return 'badge-red';
    if (days <= 30) return 'badge-yellow';
    if (days <= 90) return 'badge-yellow';
    return 'badge-green';
}

function fmtDate(d) {
    if (!d) return '—';
    return d.slice(0, 10);
}

let _toastTimer;
function showToast(msg, type = 'success') {
    const t = document.getElementById('toast');
    t.className = `toast toast-${type}`;
    const icon = type === 'success' ? 'check-circle' : type === 'error' ? 'times-circle' : 'exclamation-triangle';
    t.innerHTML = `<i class="fas fa-${icon}"></i> ${esc(msg)}`;
    clearTimeout(_toastTimer);
    _toastTimer = setTimeout(() => t.classList.add('hidden'), 3500);
}

// ─── Load Session ─────────────────────────────────────────────────────────────
async function loadSessionData() {
    try {
        const [sessionResp, productRemarksResp, sessionRemarksResp] = await Promise.all([
            fetch(`${API}/sessions/by_session_key/?session_key=${encodeURIComponent(SESSION_KEY)}`, { credentials: 'same-origin' }),
            fetch(`${API}/product-remarks/?session=${encodeURIComponent(SESSION_KEY)}`, { credentials: 'same-origin' }),
            fetch(`${API}/session-remarks/?session=${encodeURIComponent(SESSION_KEY)}`, { credentials: 'same-origin' }),
        ]);

        const session = await sessionResp.json();
        const allProductRemarks = sessionResp.ok ? (await productRemarksResp.json().catch(() => [])) : [];
        const sessionRemarks = sessionRemarksResp.ok ? (await sessionRemarksResp.json().catch(() => [])) : [];

        _sessionData = session;

        // Index product remarks by scan_log id
        _productRemarks = {};
        (Array.isArray(allProductRemarks) ? allProductRemarks : []).forEach(r => {
            if (!_productRemarks[r.scan_log]) _productRemarks[r.scan_log] = [];
            _productRemarks[r.scan_log].push(r);
        });

        renderSessionInfo(session);
        renderProducts(session.scan_logs || []);
        renderSessionRemarks(Array.isArray(sessionRemarks) ? sessionRemarks : []);
        renderAttachmentUI(session);
    } catch (e) {
        console.error(e);
        document.getElementById('sessionInfo').innerHTML = '<div style="padding:20px;color:var(--mt-danger);">Failed to load session data.</div>';
    }
}

// ─── Session Info ─────────────────────────────────────────────────────────────
function renderSessionInfo(s) {
    const statusColors = { active: 'var(--mt-primary)', completed: 'var(--mt-success-dark)', on_hold: 'var(--mt-purple)', closed: 'var(--mt-success)', cancelled: 'var(--mt-danger)' };
    const statusLabels = { active: 'Active', completed: 'Awaiting', on_hold: 'On Hold', closed: 'Confirmed', cancelled: 'Cancelled' };
    const color = statusColors[s.status] || 'var(--mt-gray-600)';
    const rep = s.rep_details ? (s.rep_details.first_name + ' ' + s.rep_details.last_name).trim() || s.rep_details.username : '—';
    const hospital = s.hospital_details?.name || '—';

    document.getElementById('sessionInfo').innerHTML = `
        <h2 style="margin-bottom:14px;font-size:1.05em;font-weight:700;color:var(--mt-gray-800);"><i class="fas fa-info-circle" style="color:var(--mt-primary)"></i> Session Info</h2>
        <div class="info-grid">
            <div class="info-item"><label>Session Key</label><span style="font-family:monospace;font-size:0.88em;">${esc(s.session_key)}</span></div>
            <div class="info-item"><label>Type</label><span>${esc(s.session_type?.replace('_', ' ') || '—')}</span></div>
            <div class="info-item"><label>Status</label><span class="badge" style="background:${color}1a;color:${color};border:1px solid ${color}40;">${esc(statusLabels[s.status] || s.status)}</span></div>
            <div class="info-item"><label>Hospital</label><span>${esc(hospital)}</span></div>
            <div class="info-item"><label>Department</label><span>${esc(s.department_name || '—')}</span></div>
            <div class="info-item"><label>Rep</label><span>${esc(rep)}</span></div>
            <div class="info-item"><label>Started</label><span>${s.started_at ? new Date(s.started_at).toLocaleString() : '—'}</span></div>
            <div class="info-item"><label>Completed</label><span>${s.completed_at ? new Date(s.completed_at).toLocaleString() : '—'}</span></div>
            <div class="info-item"><label>Total Scans</label><span style="font-weight:700;color:var(--mt-primary);">${(s.scan_logs || []).length}</span></div>
            <div class="info-item"><label>Notes</label><span>${esc(s.notes || '—')}</span></div>
        </div>
    `;

    if (s.status === 'active' && IS_MANAGER) {
        document.getElementById('forceCompleteBanner').style.display = 'block';
    }
    if ((s.status === 'completed' || s.status === 'on_hold') && IS_MANAGER) {
        const banner = document.getElementById('managerActionsBanner');
        if (banner) {
            banner.style.display = 'block';
            const msg = document.getElementById('managerBannerMsg');
            if (msg) msg.textContent = s.status === 'on_hold'
                ? 'Session is on hold — you can resume or reject it.'
                : 'Session is awaiting confirmation — close, reject, or put on hold.';
            const btnHold   = document.getElementById('btnHold');
            const btnResume = document.getElementById('btnResume');
            const btnReject = document.getElementById('btnReject');
            if (s.status === 'on_hold') {
                if (btnHold)   btnHold.style.display = 'none';
                if (btnResume) btnResume.style.display = '';
            } else {
                if (btnHold)   btnHold.style.display = '';
                if (btnResume) btnResume.style.display = 'none';
            }
        }
    }
}

// ─── Products Table ───────────────────────────────────────────────────────────
function renderProducts(logs) {
    const tbody = document.getElementById('productsBody');
    if (!logs.length) {
        tbody.innerHTML = '<tr><td colspan="12" style="text-align:center;padding:40px;color:var(--mt-gray-400);">No products scanned.</td></tr>';
        return;
    }

    tbody.innerHTML = logs.map((l, i) => {
        const remarks = _productRemarks[l.id] || [];
        const remarkCount = remarks.length;
        const hasWarehouseRemark = remarks.some(r => r.role === 'warehouse');
        const remarkBtnBg    = hasWarehouseRemark ? 'var(--mt-warning-dark)' : remarkCount > 0 ? 'var(--mt-purple)' : 'var(--mt-gray-100)';
        const remarkBtnColor = hasWarehouseRemark ? '#fff'    : remarkCount > 0 ? '#fff'    : 'var(--mt-gray-600)';
        const remarkBtn = `<button onclick="openRemarkModal(${l.id},'${esc(l.serial_number)}')"
            style="background:${remarkBtnBg};color:${remarkBtnColor};border:none;border-radius:6px;padding:3px 9px;cursor:pointer;font-size:0.8em;font-weight:600;white-space:nowrap;" title="${hasWarehouseRemark ? 'Warehouse replied' : ''}">
            <i class="fas fa-comment${remarkCount > 0 ? '' : '-alt'}"></i> ${remarkCount > 0 ? remarkCount : '+'}${hasWarehouseRemark ? ' 🏭' : ''}
        </button>`;
        const expiryCell = l.expiry_date
            ? `<span class="badge ${getExpiryClass(l.expiry_date)}">${fmtDate(l.expiry_date)}</span>`
            : '—';
        return `<tr>
            <td>${i + 1}</td>
            <td style="font-weight:600;font-size:0.88em;">${esc(l.serial_number) || '—'}</td>
            <td style="font-size:0.85em;">${esc(l.gtin_code) || '—'}</td>
            <td style="font-size:0.85em;">${esc(l.batch_number) || '—'}</td>
            <td style="font-size:0.85em;">${esc(l.product_name) || '—'}</td>
            <td style="font-size:0.83em;">${esc(l.reference_number) || '—'}</td>
            <td>${expiryCell}</td>
            <td style="font-size:0.85em;">${fmtDate(l.production_date)}</td>
            <td style="font-size:0.83em;">${esc(l.material_code) || '—'}</td>
            <td style="font-weight:600;text-align:center;">${l.quantity || 1}</td>
            <td style="font-size:0.82em;color:var(--mt-gray-600);">${l.scanned_at ? new Date(l.scanned_at).toLocaleString() : '—'}</td>
            <td style="text-align:center;">${remarkBtn}</td>
        </tr>`;
    }).join('');

    if (_dtInstance) {
        _dtInstance.destroy();
        _dtInstance = null;
    }
    _dtInstance = $('#productsTable').DataTable({
        pageLength: 25,
        order: [],
        columnDefs: [{ orderable: false, targets: [11] }],
        language: { emptyTable: 'No products scanned.' },
    });
}

// ─── Session Remarks ──────────────────────────────────────────────────────────
function _remarkHtml(r) {
    const isWH = r.role === 'warehouse';
    return `<div style="display:flex;gap:10px;margin-bottom:12px;${isWH ? 'flex-direction:row-reverse;' : ''}">
        <div style="width:34px;height:34px;border-radius:50%;display:flex;align-items:center;justify-content:center;font-size:1em;flex-shrink:0;background:${isWH ? 'var(--mt-purple-light)' : 'var(--mt-info-light)'};color:${isWH ? 'var(--mt-purple)' : 'var(--mt-primary)'};">
            ${isWH ? '🏭' : '👤'}
        </div>
        <div style="max-width:80%;">
            <div style="background:${isWH ? 'var(--mt-purple-light)' : 'var(--mt-primary-lighter)'};border:1px solid ${isWH ? 'var(--mt-purple-border)' : 'var(--mt-info-border)'};border-radius:12px;padding:10px 14px;font-size:0.88em;color:var(--mt-gray-800);">
                ${esc(r.remark)}
            </div>
            <div style="font-size:0.75em;color:var(--mt-gray-400);margin-top:4px;${isWH ? 'text-align:right;' : ''}">
                ${esc(r.created_by_name)} · ${r.created_at ? new Date(r.created_at).toLocaleString() : ''}
            </div>
        </div>
    </div>`;
}

function renderSessionRemarks(remarks) {
    const el = document.getElementById('sessionRemarksList');
    const section = document.getElementById('sessionRemarksSection');
    const titleEl = section ? section.querySelector('h2') : null;

    const hasWarehouseReply = remarks.some(r => r.role === 'warehouse');
    const total = remarks.length;

    // Update section title with badge and highlight
    if (titleEl) {
        const badge = total > 0
            ? hasWarehouseReply
                ? `<span style="background:var(--mt-warning-light);color:var(--mt-warning-dark);border:1px solid var(--mt-warning-border);border-radius:20px;padding:1px 10px;font-size:.75em;font-weight:700;margin-left:8px;">${total} remark${total !== 1 ? 's' : ''} · 🏭 WH replied</span>`
                : `<span style="background:var(--mt-primary-lighter);color:var(--mt-primary);border:1px solid var(--mt-info-border);border-radius:20px;padding:1px 10px;font-size:.75em;font-weight:700;margin-left:8px;">${total} remark${total !== 1 ? 's' : ''}</span>`
            : '';
        titleEl.innerHTML = `<i class="fas fa-comments" style="color:var(--mt-purple)"></i> Session Remarks${badge}`;
    }
    if (section) {
        section.style.borderLeft = hasWarehouseReply ? '4px solid var(--mt-warning-dark)' : '';
        section.style.background = hasWarehouseReply ? 'var(--mt-warning-light)' : '';
    }

    if (!remarks.length) {
        el.innerHTML = '<p style="font-size:0.85em;color:var(--mt-gray-400);">No remarks yet.</p>';
        return;
    }
    el.innerHTML = remarks.map(_remarkHtml).join('');
}

async function addSessionRemark() {
    const text = document.getElementById('newSessionRemark').value.trim();
    if (!text) return;
    try {
        const resp = await fetch(`${API}/session-remarks/`, {
            method: 'POST', credentials: 'same-origin',
            headers: { 'Content-Type': 'application/json', 'X-CSRFToken': CSRF },
            body: JSON.stringify({ session: SESSION_KEY, remark: text }),
        });
        if (resp.ok) {
            document.getElementById('newSessionRemark').value = '';
            const all = await fetch(`${API}/session-remarks/?session=${encodeURIComponent(SESSION_KEY)}`, { credentials: 'same-origin' });
            renderSessionRemarks(await all.json().catch(() => []));
            showToast('Remark saved');
        } else {
            const err = await resp.json().catch(() => ({}));
            showToast(err.error || 'Failed to save remark', 'error');
        }
    } catch (e) { showToast('Network error', 'error'); }
}

// ─── Product Remarks Modal ────────────────────────────────────────────────────
function openRemarkModal(productId, serial) {
    _currentRemarkProductId = productId;
    _currentRemarkSerial = serial;
    document.getElementById('remarkModalSubtitle').textContent = `Serial: ${serial}`;
    document.getElementById('newProductRemark').value = '';
    document.getElementById('productRemarkModal').style.display = 'block';
    document.body.style.overflow = 'hidden';
    renderProductRemarksList(_productRemarks[productId] || []);
}

function closeRemarkModal() {
    document.getElementById('productRemarkModal').style.display = 'none';
    document.body.style.overflow = '';
}

function renderProductRemarksList(remarks) {
    const el = document.getElementById('productRemarksList');
    if (!remarks.length) {
        el.innerHTML = '<p style="font-size:0.85em;color:var(--mt-gray-400);">No remarks yet. Be the first to comment.</p>';
        return;
    }
    el.innerHTML = remarks.map(_remarkHtml).join('');
}

async function submitProductRemark() {
    const text = document.getElementById('newProductRemark').value.trim();
    if (!text || !_currentRemarkProductId) return;
    try {
        const resp = await fetch(`${API}/product-remarks/`, {
            method: 'POST', credentials: 'same-origin',
            headers: { 'Content-Type': 'application/json', 'X-CSRFToken': CSRF },
            body: JSON.stringify({ product: _currentRemarkProductId, remark: text }),
        });
        if (resp.ok) {
            const newRemark = await resp.json();
            if (!_productRemarks[_currentRemarkProductId]) _productRemarks[_currentRemarkProductId] = [];
            _productRemarks[_currentRemarkProductId].push(newRemark);
            document.getElementById('newProductRemark').value = '';
            renderProductRemarksList(_productRemarks[_currentRemarkProductId]);
            // Refresh the table button badge
            renderProducts(_sessionData?.scan_logs || []);
            showToast('Remark saved');
        } else {
            const err = await resp.json().catch(() => ({}));
            showToast(err.error || 'Failed to save remark', 'error');
        }
    } catch (e) { showToast('Network error', 'error'); }
}

// ─── Attachment & Upload ──────────────────────────────────────────────────────
function renderAttachmentUI(session) {
    const attachment = session.attachment_details;
    if (attachment) {
        // Show preview
        document.getElementById('uploadSection').style.display = 'none';
        document.getElementById('docPreviewSection').style.display = 'block';
        renderDocumentPreview(attachment);
    } else if (session.status === 'completed') {
        document.getElementById('uploadSection').style.display = 'block';
        document.getElementById('docPreviewSection').style.display = 'none';
    } else {
        document.getElementById('uploadSection').style.display = 'none';
        document.getElementById('docPreviewSection').style.display = 'none';
    }
}

function renderDocumentPreview(attachment) {
    const dlUrl = `/meritrack/api/sessions/download_attachment/?session_key=${encodeURIComponent(SESSION_KEY)}`;
    const isPDF = (attachment.content_type || '').includes('pdf');
    const isImg = /\/(png|jpg|jpeg|gif|bmp|tiff|webp)/.test(attachment.content_type || '');
    let previewHtml = '';
    if (isPDF) {
        previewHtml = `<iframe src="${dlUrl}" style="width:100%;height:600px;border:1px solid var(--mt-gray-200);border-radius:8px;" title="Document Preview"></iframe>`;
    } else if (isImg) {
        previewHtml = `<img src="${dlUrl}" style="max-width:100%;border-radius:8px;border:1px solid var(--mt-gray-200);" alt="Document Preview">`;
    } else {
        previewHtml = `<p style="color:var(--mt-gray-600);font-size:0.9em;">Preview not available for this file type.</p>`;
    }
    document.getElementById('docPreviewContent').innerHTML = `
        ${previewHtml}
        <div style="margin-top:12px;display:flex;justify-content:flex-end;">
            <a href="${dlUrl}" class="btn-mt btn-primary" download="${esc(attachment.original_filename)}"><i class="fas fa-download"></i> Download ${esc(attachment.original_filename)}</a>
        </div>
    `;
}

function handleAttachmentDragOver(e) {
    e.preventDefault();
    document.getElementById('uploadDropzone').classList.add('dragover');
}
function handleAttachmentDragLeave() {
    document.getElementById('uploadDropzone').classList.remove('dragover');
}
function handleAttachmentDrop(e) {
    e.preventDefault();
    document.getElementById('uploadDropzone').classList.remove('dragover');
    const file = e.dataTransfer.files[0];
    if (file) setAttachmentFile(file);
}
function onAttachmentSelected(e) {
    const file = e.target.files[0];
    if (file) setAttachmentFile(file);
}
function setAttachmentFile(file) {
    _attachmentFile = file;
    document.getElementById('uploadFilename').textContent = file.name;
    document.getElementById('uploadSubmitBtn').disabled = false;
}

async function submitAttachment() {
    if (!_attachmentFile) return;
    const btn = document.getElementById('uploadSubmitBtn');
    btn.disabled = true;
    btn.innerHTML = '<i class="fas fa-spinner fa-spin"></i> Uploading…';
    const fd = new FormData();
    fd.append('session_key', SESSION_KEY);
    fd.append('file', _attachmentFile);
    try {
        const resp = await fetch(`${API}/sessions/upload_attachment/`, {
            method: 'POST', credentials: 'same-origin',
            headers: { 'X-CSRFToken': CSRF },
            body: fd,
        });
        if (resp.ok) {
            showToast('Document uploaded and session closed', 'success');
            setTimeout(() => location.reload(), 1000);
        } else {
            const err = await resp.json().catch(() => ({}));
            showToast(err.error || 'Upload failed', 'error');
            btn.disabled = false;
            btn.innerHTML = '<i class="fas fa-upload"></i> Upload & Close Session';
        }
    } catch (e) {
        showToast('Network error during upload', 'error');
        btn.disabled = false;
        btn.innerHTML = '<i class="fas fa-upload"></i> Upload & Close Session';
    }
}

// ─── Force Complete ───────────────────────────────────────────────────────────
async function forceCompleteSession() {
    if (!confirm('Force-complete this session? The rep will be notified.')) return;
    try {
        const resp = await fetch(`${API}/sessions/force_complete_session/`, {
            method: 'POST', credentials: 'same-origin',
            headers: { 'Content-Type': 'application/json', 'X-CSRFToken': CSRF },
            body: JSON.stringify({ session_key: SESSION_KEY }),
        });
        if (resp.ok) {
            let data = {};
            try { data = await resp.json(); } catch(e) {}
            document.getElementById('forceCompleteBanner').style.display = 'none';
            if (data.replenishment_order_id) {
                showToast(`Session completed — replenishment order ${data.replenishment_order_id} created`);
                setTimeout(() => { window.location.href = '/meritrack/orders/'; }, 1500);
            } else {
                showToast('Session force-completed');
                setTimeout(() => location.reload(), 800);
            }
        } else {
            const err = await resp.json().catch(() => ({}));
            showToast(err.error || 'Failed to force-complete', 'error');
        }
    } catch (e) { showToast('Network error', 'error'); }
}

// ─── Reject / Hold / Resume ───────────────────────────────────────────────────
async function rejectSessionDetail() {
    const reason = prompt('Reason for rejecting this session (sent back to rep):');
    if (reason === null) return;
    try {
        const resp = await fetch(`${API}/sessions/reject_session/`, {
            method: 'POST', credentials: 'same-origin',
            headers: { 'Content-Type': 'application/json', 'X-CSRFToken': CSRF },
            body: JSON.stringify({ session_key: SESSION_KEY, reason: reason || 'Rejected by manager' }),
        });
        if (resp.ok) { showToast('Session rejected — sent back to rep'); setTimeout(() => location.reload(), 800); }
        else { const e = await resp.json().catch(() => ({})); showToast(e.error || 'Failed to reject', 'error'); }
    } catch (e) { showToast('Network error', 'error'); }
}

async function holdSessionDetail() {
    if (!confirm('Put this session on hold?')) return;
    try {
        const resp = await fetch(`${API}/sessions/hold_session/`, {
            method: 'POST', credentials: 'same-origin',
            headers: { 'Content-Type': 'application/json', 'X-CSRFToken': CSRF },
            body: JSON.stringify({ session_key: SESSION_KEY }),
        });
        if (resp.ok) { showToast('Session placed on hold'); setTimeout(() => location.reload(), 800); }
        else { const e = await resp.json().catch(() => ({})); showToast(e.error || 'Failed', 'error'); }
    } catch (e) { showToast('Network error', 'error'); }
}

async function resumeSessionDetail() {
    if (!confirm('Resume this session from on hold?')) return;
    try {
        const resp = await fetch(`${API}/sessions/resume_session/`, {
            method: 'POST', credentials: 'same-origin',
            headers: { 'Content-Type': 'application/json', 'X-CSRFToken': CSRF },
            body: JSON.stringify({ session_key: SESSION_KEY }),
        });
        if (resp.ok) { showToast('Session resumed'); setTimeout(() => location.reload(), 800); }
        else { const e = await resp.json().catch(() => ({})); showToast(e.error || 'Failed', 'error'); }
    } catch (e) { showToast('Network error', 'error'); }
}

// ─── Export CSV ───────────────────────────────────────────────────────────────
function exportCSV() {
    if (!_sessionData || !(_sessionData.scan_logs || []).length) {
        alert('No data to export');
        return;
    }
    const s = _sessionData;
    const logs = s.scan_logs;
    const hospital = s.hospital_details?.name || '';
    const rep = s.rep_details ? (s.rep_details.first_name + ' ' + s.rep_details.last_name).trim() || s.rep_details.username : '';

    let csv = '\uFEFF';
    csv += 'Session Key,Session Type,Hospital,Department,Rep,Serial Number,GTIN Code,Material Code,Product Name,Batch Number,Reference Number,Expiry Date,Production Date,Quantity,Action,Scanned At\n';
    logs.forEach(l => {
        const sn = (l.serial_number && !l.serial_number.startsWith('NSN-')) ? l.serial_number : '';
        csv += [
            escCSV(s.session_key), escCSV(s.session_type), escCSV(hospital),
            escCSV(s.department_name || ''), escCSV(rep), escCSV(sn),
            escCSV(l.gtin_code), escCSV(l.material_code), escCSV(l.product_name),
            escCSV(l.batch_number), escCSV(l.reference_number),
            escCSV(l.expiry_date), escCSV(l.production_date), escCSV(l.quantity || 1),
            escCSV(l.action), escCSV(l.scanned_at ? new Date(l.scanned_at).toLocaleString() : '')
        ].join(',') + '\n';
    });

    const blob = new Blob([csv], { type: 'text/csv;charset=utf-8;' });
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url;
    a.download = `${s.session_key}_${new Date().toISOString().split('T')[0]}.csv`;
    document.body.appendChild(a);
    a.click();
    URL.revokeObjectURL(url);
    a.remove();
}

// ─── Keyboard shortcut ────────────────────────────────────────────────────────
document.addEventListener('keydown', e => {
    if (e.key === 'Escape') closeRemarkModal();
});

loadSessionData();
