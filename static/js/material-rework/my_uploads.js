function getCookie(name) {
    var c = document.cookie.split(';').map(function(s){ return s.trim(); }).find(function(s){ return s.startsWith(name + '='); });
    return c ? decodeURIComponent(c.split('=')[1]) : null;
}

function escHtml(str) {
    return String(str||'').replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;');
}

function badge(status, label) {
    const s = STATUS_BADGE[status] || ['#f3f4f6','#374151', label];
    return `<span style="background:${s[0]};color:${s[1]};padding:2px 8px;border-radius:10px;font-size:.73rem;font-weight:700;">${s[2]}</span>`;
}

function srcBadge(source, display) {
    if (!source) return '—';
    const colors = {warehouse:['#e0f2fe','#0369a1'], order_management:['#ede9fe','#5b21b6'], production:['#d1fae5','#065f46']};
    const c = colors[source] || ['#f3f4f6','#374151'];
    return `<span style="background:${c[0]};color:${c[1]};padding:2px 9px;border-radius:10px;font-size:.73rem;font-weight:700;">${escHtml(display)}</span>`;
}

function openBatch(batchId) {
    const b = BATCHES[batchId];
    if (!b) return;
    document.getElementById('panelTitle').innerHTML = `<i class="fas fa-ticket-alt" style="color:#0076c8;"></i> <span style="font-family:monospace;font-size:.9rem;">${escHtml(b.reference_number)}</span>`;
    document.getElementById('slideOverlay').classList.add('open');

    const foot = document.getElementById('panelFoot');
    if (b.status === 'hod_rejected') {
        foot.innerHTML = `
            <div style="display:flex;gap:8px;flex-wrap:wrap;">
                <a href="${REPLACE_URL_TPL}${batchId}/"
                   style="flex:1;padding:10px;background:#fffbeb;color:#b45309;border:1px solid #fde68a;border-radius:6px;font-size:.85rem;font-weight:700;display:inline-flex;align-items:center;justify-content:center;gap:6px;text-decoration:none;">
                    <i class="fas fa-file-upload"></i> Replace File
                </a>
                <button type="button" onclick="openDeleteConfirm(${batchId},'${escHtml(b.reference_number)}')"
                    style="flex:1;padding:10px;background:#fef2f2;color:#dc2626;border:1px solid #fecaca;border-radius:6px;font-size:.85rem;font-weight:700;cursor:pointer;display:inline-flex;align-items:center;justify-content:center;gap:6px;">
                    <i class="fas fa-trash"></i> Delete
                </button>
                <button type="button" onclick="closePanel()"
                    style="flex:0 0 auto;padding:10px 14px;background:#f1f5f9;color:#374151;border:none;border-radius:6px;font-size:.85rem;font-weight:600;cursor:pointer;">
                    <i class="fas fa-times"></i>
                </button>
            </div>`;
    } else {
        foot.innerHTML = `<button type="button" onclick="closePanel()" style="width:100%;padding:10px;background:#f1f5f9;color:#374151;border:none;border-radius:6px;font-size:.88rem;font-weight:600;cursor:pointer;"><i class="fas fa-times"></i> Close</button>`;
    }

    let itemRows = b.items.map((it, i) => `
        <tr>
            <td>${i+1}</td>
            <td>${escHtml(it.serial_number)||'—'}</td>
            <td>${escHtml(it.country)||'—'}</td>
            <td><strong>${escHtml(it.material)}</strong></td>
            <td>${escHtml(it.batch_no)||'—'}</td>
            <td style="white-space:nowrap;">${escHtml(it.mfg_date)||'—'}</td>
            <td style="white-space:nowrap;">${escHtml(it.exp_date)||'—'}</td>
            <td>${escHtml(it.from_storage_location)||'—'}</td>
            <td>${escHtml(it.to_storage_location)||'—'}</td>
            <td style="white-space:nowrap;">${escHtml(it.item_date)||'—'}</td>
            <td><strong>${it.qty}</strong></td>
            <td style="color:#10b981;font-weight:600;">${it.accepted_qty}</td>
            <td style="color:#8b5cf6;font-weight:600;">${it.pending_qty}</td>
            <td>${escHtml(it.box_no)||'—'}</td>
            <td>${badge(it.status, it.status_display)}</td>
            <td style="max-width:110px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;" title="${escHtml(it.remark)}">${escHtml(it.remark)||'—'}</td>
        </tr>`).join('');

    document.getElementById('panelBody').innerHTML = `
        <div class="panel-meta">
            <div class="panel-meta-item"><label>Reference #</label><p style="font-family:monospace;color:#1d4ed8;">${escHtml(b.reference_number)}</p></div>
            <div class="panel-meta-item"><label>Status</label><p>${badge(b.status, b.status_display)}</p></div>
            <div class="panel-meta-item"><label>Vertical</label><p>${escHtml(b.vertical)}</p></div>
            <div class="panel-meta-item"><label>Department</label><p>${escHtml(b.department)}</p></div>
            <div class="panel-meta-item"><label>Requested By</label><p>${b.requested_by ? `<span style="background:#e0f2fe;color:#0369a1;padding:2px 9px;border-radius:10px;font-size:.73rem;font-weight:700;">${escHtml(b.requested_by)}</span>` : '—'}</p></div>
            <div class="panel-meta-item"><label>Uploaded At</label><p>${b.uploaded_at}</p></div>
            <div class="panel-meta-item"><label>HOD Reviewed</label><p>${b.hod_reviewed_at}</p></div>
            ${b.subject ? `<div class="panel-meta-item full"><label>Email Subject</label><p style="font-weight:600;color:#0076c8;">${escHtml(b.subject)}</p></div>` : ''}
            ${b.hod_comments ? `<div class="panel-meta-item full"><label>HOD Comments</label><div class="hod-comments-box">"${escHtml(b.hod_comments)}"</div></div>` : ''}
        </div>

        <p class="panel-section-title"><i class="fas fa-list-ul" style="color:#1d4ed8;margin-right:5px;"></i>Items (${b.items.length})</p>
        ${b.items.length ? `
        <table class="panel-items-table" style="min-width:900px;">
            <thead><tr><th>#</th><th>Serial No</th><th>Country</th><th>Code</th><th>Batch</th><th>Mfg Date</th><th>Exp Date</th><th>From Storage</th><th>To Storage</th><th>Uploaded At</th><th>Qty</th><th>Accepted</th><th>Pending</th><th>Box No</th><th>Status</th><th>Remarks</th></tr></thead>
            <tbody>${itemRows}</tbody>
        </table>` : '<p style="color:#9ca3af;font-size:.85rem;text-align:center;padding:20px;">No items found.</p>'}
    `;
}

function closePanel() {
    document.getElementById('slideOverlay').classList.remove('open');
}

var _myupDelBatchId = null;

function openDeleteConfirm(batchId, refNum) {
    _myupDelBatchId = batchId;
    document.getElementById('delRefDisplay').textContent = refNum;
    document.getElementById('myup_del_err').style.display = 'none';
    var delBtn = document.getElementById('myupDelBtn');
    if (delBtn) { delBtn.disabled = false; delBtn.innerHTML = '<i class="fas fa-trash"></i> Yes, Delete'; }
    document.getElementById('deleteOverlay').style.display = 'flex';
    closePanel();
}

function closeDeleteConfirm() {
    document.getElementById('deleteOverlay').style.display = 'none';
}

function doMyupDelete() {
    if (!_myupDelBatchId) return;
    var delBtn = document.getElementById('myupDelBtn');
    delBtn.disabled = true; delBtn.innerHTML = '<i class="fas fa-spinner fa-spin"></i> Deleting…';
    document.getElementById('myup_del_err').style.display = 'none';

    var fd = new FormData();
    fd.append('csrfmiddlewaretoken', getCookie('csrftoken') || '');
    fd.append('next', window.location.pathname);

    closeDeleteConfirm();
    document.getElementById('pageLoader').style.display = 'flex';

    fetch(DELETE_URL_TPL + _myupDelBatchId + '/', {
        method: 'POST',
        body: fd,
        headers: { 'X-Requested-With': 'XMLHttpRequest' }
    })
    .then(function(r) { return r.json(); })
    .then(function(data) {
        document.getElementById('pageLoader').style.display = 'none';
        if (data.ok) {
            var row = document.querySelector('tr[data-batch-id="' + _myupDelBatchId + '"]');
            if (row) row.remove();
            var tbody = document.querySelector('.md-table tbody');
            if (tbody && !tbody.querySelector('tr')) {
                var wrap = document.querySelector('.md-table-wrap');
                if (wrap) wrap.innerHTML = '<div class="md-empty"><i class="fas fa-inbox" style="font-size:2rem;margin-bottom:10px;display:block;"></i>No uploads yet. <a href="' + UPLOAD_URL + '" style="color:#0076c8;font-weight:700;">Upload your first batch</a>.</div>';
            }
            showMyupToast('Batch ' + (data.ref || '') + ' deleted.');
        } else {
            openDeleteConfirm(_myupDelBatchId, document.getElementById('delRefDisplay').textContent);
            document.getElementById('myup_del_err').textContent = data.error || 'Delete failed.';
            document.getElementById('myup_del_err').style.display = 'block';
            delBtn.disabled = false; delBtn.innerHTML = '<i class="fas fa-trash"></i> Yes, Delete';
        }
    })
    .catch(function(){
        document.getElementById('pageLoader').style.display = 'none';
        openDeleteConfirm(_myupDelBatchId, document.getElementById('delRefDisplay').textContent);
        document.getElementById('myup_del_err').textContent = 'Network error.';
        document.getElementById('myup_del_err').style.display = 'block';
        delBtn.disabled = false; delBtn.innerHTML = '<i class="fas fa-trash"></i> Yes, Delete';
    });
}

function showMyupToast(msg) {
    var t = document.createElement('div');
    t.style.cssText = 'position:fixed;top:20px;right:20px;z-index:9999999;padding:12px 18px;border-radius:8px;font-size:.87rem;font-weight:600;background:#ecfdf5;border:1px solid #6ee7b7;color:#065f46;box-shadow:0 4px 20px rgba(0,0,0,.15);display:flex;align-items:center;gap:8px;';
    t.innerHTML = '<i class="fas fa-check-circle"></i><span>' + msg + '</span>';
    document.body.appendChild(t);
    setTimeout(function(){ t.style.transition='opacity .4s'; t.style.opacity='0'; setTimeout(function(){ t.remove(); },400); },3000);
}

function statusBadgeHtml(status) {
    const s = STATUS_BADGE[status];
    if (!s) return `<span class="badge" style="background:#f3f4f6;color:#374151;">${escHtml(status)}</span>`;
    return `<span class="badge" style="background:${s[0]};color:${s[1]};">${escHtml(s[2])}</span>`;
}

function pollUploads() {
    fetch(POLL_URL, { headers: { 'X-Requested-With': 'XMLHttpRequest' } })
    .then(function(r) { return r.json(); })
    .then(function(data) {
        var rows = document.querySelectorAll('tr[data-batch-id]');
        var knownIds = new Set();
        rows.forEach(function(row) { knownIds.add(row.getAttribute('data-batch-id')); });

        var serverIds = Object.keys(data.batches);
        var hasNew = serverIds.some(function(id) { return !knownIds.has(id); });
        if (hasNew) { location.reload(); return; }

        rows.forEach(function(row) {
            var id = row.getAttribute('data-batch-id');
            var b  = data.batches[id];
            if (!b) return;

            var oldStatus = row.getAttribute('data-status');
            if (oldStatus === b.status) return;

            row.setAttribute('data-status', b.status);

            var statusCell = row.querySelector('[data-field="status"]');
            if (statusCell) statusCell.innerHTML = statusBadgeHtml(b.status);

            var hodDateCell = row.querySelector('[data-field="hod_reviewed"]');
            if (hodDateCell) hodDateCell.textContent = b.hod_reviewed_at;

            var hodCommCell = row.querySelector('[data-field="hod_comments"]');
            if (hodCommCell) hodCommCell.textContent = b.hod_comments ? b.hod_comments.substring(0, 40) + (b.hod_comments.length > 40 ? '…' : '') : '—';

            if (BATCHES[id]) {
                BATCHES[id].status          = b.status;
                BATCHES[id].hod_reviewed_at = b.hod_reviewed_at;
                BATCHES[id].hod_comments    = b.hod_comments;
            }

            var label = (STATUS_BADGE[b.status] || [null, null, b.status])[2];
            showMyupToast('Batch ' + escHtml(b.ref) + ': status → ' + escHtml(label));
        });
    })
    .catch(function() {});
}

setInterval(pollUploads, 30000);

document.addEventListener('visibilitychange', function() {
    if (!document.hidden) pollUploads();
});
