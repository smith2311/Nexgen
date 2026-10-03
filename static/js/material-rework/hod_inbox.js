function getCookie(name) {
    var c = document.cookie.split(';').map(function(s){ return s.trim(); }).find(function(s){ return s.startsWith(name + '='); });
    return c ? decodeURIComponent(c.split('=')[1]) : null;
}

function escHtml(str) {
    return String(str||'').replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;');
}

function showToast(msg, type) {
    var s = {success:{bg:'#ecfdf5',border:'1px solid #6ee7b7',color:'#065f46',icon:'fa-check-circle'},error:{bg:'#fef2f2',border:'1px solid #fecaca',color:'#991b1b',icon:'fa-exclamation-circle'}};
    var st = s[type] || s.success;
    var t = document.createElement('div');
    t.style.cssText = 'position:fixed;top:20px;right:20px;z-index:9999999;padding:12px 18px;border-radius:8px;font-size:.87rem;font-weight:600;box-shadow:0 4px 20px rgba(0,0,0,.18);animation:hodToastIn .25s ease;display:flex;align-items:center;gap:8px;background:'+st.bg+';border:'+st.border+';color:'+st.color+';';
    t.innerHTML = '<i class="fas '+st.icon+'"></i><span>'+escHtml(msg)+'</span>';
    document.body.appendChild(t);
    var style = document.createElement('style');
    style.textContent = '@keyframes hodToastIn{from{transform:translateX(40px);opacity:0}to{transform:translateX(0);opacity:1}}';
    document.head.appendChild(style);
    setTimeout(function(){ t.style.transition='opacity .4s'; t.style.opacity='0'; setTimeout(function(){ t.remove(); },400); },3500);
}

function removeBatchRow(batchId) {
    var rows = document.querySelectorAll('#hodBatchTable tr');
    rows.forEach(function(row) {
        if (row.dataset.batchId == batchId) row.remove();
    });
    var tbody = document.querySelector('#hodBatchTable tbody');
    if (tbody && !tbody.querySelector('tr')) {
        var wrap = document.querySelector('.md-table-wrap');
        if (wrap) wrap.innerHTML = '<div class="md-empty"><i class="fas fa-check-double" style="font-size:2.5rem;margin-bottom:12px;display:block;color:#10b981;"></i>No pending requests. Your inbox is clear.</div>';
    }
}

function hodAjaxDecision(batchId, action, comments) {
    var fd = new FormData();
    fd.append('batch_id', batchId);
    fd.append('action', action);
    fd.append('comments', comments || '');
    fd.append('csrfmiddlewaretoken', getCookie('csrftoken') || '');
    return fetch(window.location.href, {
        method: 'POST',
        body: fd,
        headers: { 'X-Requested-With': 'XMLHttpRequest' }
    }).then(function(r) { return r.json(); });
}

function openPreview(batchId) {
    const b = BATCHES[batchId];
    if (!b) return;

    const pdBatchId = document.getElementById('pd_batch_id');
    if (pdBatchId) { pdBatchId.value = batchId; }
    const pdComments = document.getElementById('pd_comments');
    if (pdComments) { pdComments.value = ''; }
    document.getElementById('panelRefTitle').textContent = b.reference_number;

    let rows = b.items.map((item, i) => `
        <tr>
            <td>${i+1}</td>
            <td>${escHtml(item.serial_number)||'—'}</td>
            <td>${escHtml(item.country)||'—'}</td>
            <td><strong>${escHtml(item.material)}</strong></td>
            <td>${escHtml(item.batch_no)||'—'}</td>
            <td style="white-space:nowrap;">${escHtml(item.mfg_date)||'—'}</td>
            <td style="white-space:nowrap;">${escHtml(item.exp_date)||'—'}</td>
            <td>${escHtml(item.from_storage_location)||'—'}</td>
            <td>${escHtml(item.to_storage_location)||'—'}</td>
            <td><strong>${item.qty}</strong></td>
            <td>${escHtml(item.box_no)||'—'}</td>
            <td style="max-width:110px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;" title="${escHtml(item.remark)}">${escHtml(item.remark)||'—'}</td>
            <td style="white-space:nowrap;">${escHtml(item.item_date)||'—'}</td>
        </tr>`).join('');

    document.getElementById('panelBody').innerHTML = `
        <div class="panel-meta">
            <div class="panel-meta-item"><label>Reference #</label><p style="font-family:monospace;color:#d97706;">${escHtml(b.reference_number)}</p></div>
            <div class="panel-meta-item"><label>Upload Date</label><p>${b.uploaded_at}</p></div>
            <div class="panel-meta-item"><label>Vertical</label><p>${escHtml(b.vertical)}</p></div>
            <div class="panel-meta-item"><label>Department</label><p>${escHtml(b.department)}</p></div>
            <div class="panel-meta-item"><label>Requested By</label><p>${escHtml(b.requested_by)||'—'}</p></div>
            <div class="panel-meta-item"><label>Submitted By</label><p>${escHtml(b.uploaded_by)}</p></div>
            ${b.subject ? `<div class="panel-meta-item" style="grid-column:1/-1;"><label>Email Subject</label><p style="font-weight:600;color:#0076c8;">${escHtml(b.subject)}</p></div>` : ''}
        </div>
        <p class="panel-section-title"><i class="fas fa-list-ul" style="color:#f59e0b;margin-right:5px;"></i> Items (${b.items.length})</p>
        <div style="overflow-x:auto;">
        <table class="panel-items-table" style="min-width:900px;">
            <thead><tr><th>#</th><th>Serial No</th><th>Country</th><th>Code</th><th>Batch</th><th>Mfg Date</th><th>Exp Date</th><th>From Storage</th><th>To Storage</th><th>Qty</th><th>Box No</th><th>Remarks</th><th>Uploaded At</th></tr></thead>
            <tbody>${rows}</tbody>
        </table>
        </div>`;

    document.getElementById('slideOverlay').classList.add('open');
}

function closePreview() {
    document.getElementById('slideOverlay').classList.remove('open');
}

function submitPanelDecision(action) {
    var form = document.getElementById('panelDecisionForm');
    if (!form) return;
    var batchId  = document.getElementById('pd_batch_id').value;
    var comments = document.getElementById('pd_comments') ? document.getElementById('pd_comments').value : '';
    if (!batchId) { showToast('No batch selected.', 'error'); return; }
    // Mandatory on approve as well as reject — the view enforces the same
    // rule, this is just the faster feedback.
    if (!comments.trim()) {
        showToast('A remark is required before approving or rejecting.', 'error');
        var pd = document.getElementById('pd_comments');
        if (pd) { pd.focus(); }
        return;
    }
    var btns = form.querySelectorAll('button');
    btns.forEach(function(b){ b.disabled = true; b._orig = b.innerHTML; b.innerHTML = '<i class="fas fa-spinner fa-spin"></i>'; });

    hodAjaxDecision(batchId, action, comments)
    .then(function(data) {
        btns.forEach(function(b){ b.disabled = false; b.innerHTML = b._orig || b.innerHTML; });
        if (data.ok) {
            closePreview();
            removeBatchRow(data.batch_id);
            showToast(action === 'approve' ? 'Batch approved and forwarded.' : 'Batch rejected.', 'success');
        } else {
            showToast(data.error || 'Could not process decision.', 'error');
        }
    })
    .catch(function() {
        btns.forEach(function(b){ b.disabled = false; b.innerHTML = b._orig || b.innerHTML; });
        showToast('Network error — please try again.', 'error');
    });
}

function openDecision(batchId, refNum, action) {
    const isApprove = action === 'approve';
    const head      = document.getElementById('decisionHead');
    const icon      = document.getElementById('decisionIcon');
    const title     = document.getElementById('decisionTitle');
    const msg       = document.getElementById('decisionMsg');
    const submitBtn = document.getElementById('decisionSubmitBtn');

    head.className = 'decision-head ' + action;
    icon.className = isApprove ? 'fas fa-check-circle' : 'fas fa-times-circle';
    icon.style.color = isApprove ? '#10b981' : '#ef4444';
    title.textContent = isApprove ? 'Approve Batch' : 'Reject Batch';
    msg.innerHTML = isApprove
        ? `Approve batch <strong style="color:#d97706;font-family:monospace;">${escHtml(refNum)}</strong>? This will move it to Production Review.`
        : `Reject batch <strong style="color:#d97706;font-family:monospace;">${escHtml(refNum)}</strong>? The uploader will be notified.`;

    submitBtn.className = isApprove ? 'btn-confirm-approve' : 'btn-confirm-reject';
    submitBtn.innerHTML = isApprove
        ? '<i class="fas fa-check"></i> Yes, Approve'
        : '<i class="fas fa-times"></i> Yes, Reject';

    document.getElementById('d_batch_id').value = batchId;
    document.getElementById('d_action').value   = action;
    document.getElementById('d_comments').value = '';
    document.getElementById('decisionOverlay').classList.add('open');
}

function closeDecision() {
    document.getElementById('decisionOverlay').classList.remove('open');
}

var _decisionForm = document.getElementById('decisionForm');
if (_decisionForm) {
    _decisionForm.addEventListener('submit', function(e) {
        e.preventDefault();
        var batchId  = document.getElementById('d_batch_id').value;
        var action   = document.getElementById('d_action').value;
        var comments = document.getElementById('d_comments').value;
        if (!comments.trim()) {
            showToast('A remark is required before approving or rejecting.', 'error');
            document.getElementById('d_comments').focus();
            return;
        }
        var decBtn   = document.getElementById('decisionSubmitBtn');
        var orig     = decBtn.innerHTML;
        decBtn.disabled = true;
        decBtn.innerHTML = '<i class="fas fa-spinner fa-spin"></i> Processing…';

        hodAjaxDecision(batchId, action, comments)
        .then(function(data) {
            decBtn.disabled = false;
            decBtn.innerHTML = orig;
            if (data.ok) {
                closeDecision();
                removeBatchRow(data.batch_id);
                showToast(action === 'approve' ? 'Batch approved and forwarded.' : 'Batch rejected.', 'success');
            } else {
                showToast(data.error || 'Could not process decision.', 'error');
            }
        })
        .catch(function() {
            decBtn.disabled = false;
            decBtn.innerHTML = orig;
            showToast('Network error — please try again.', 'error');
        });
    });
}
