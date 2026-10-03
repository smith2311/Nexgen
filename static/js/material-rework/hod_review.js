let _pendingAction = null;

function triggerConfirm(action) {
    _pendingAction = action;
    const comments = document.getElementById('commentsInput').value.trim();
    const icon   = document.getElementById('confirmIcon');
    const title  = document.getElementById('confirmTitle');
    const sub    = document.getElementById('confirmSub');
    const goBtn  = document.getElementById('confirmGoBtn');
    const cc     = document.getElementById('confirmComments');

    if (action === 'approve') {
        icon.className  = 'confirm-icon green';
        icon.innerHTML  = '<i class="fas fa-check-circle"></i>';
        title.textContent = 'Confirm Approval';
        sub.textContent   = 'You are about to approve all ' + ITEM_COUNT + ' item(s) in this batch. This will move them to production review.';
        goBtn.className   = 'confirm-go-approve';
        goBtn.innerHTML   = '<i class="fas fa-check"></i> Yes, Approve';
    } else {
        icon.className  = 'confirm-icon red';
        icon.innerHTML  = '<i class="fas fa-times-circle"></i>';
        title.textContent = 'Confirm Rejection';
        sub.textContent   = 'You are about to reject this batch. The submitter will be notified. This action cannot be undone.';
        goBtn.className   = 'confirm-go-reject';
        goBtn.innerHTML   = '<i class="fas fa-times"></i> Yes, Reject';
    }

    if (comments) {
        cc.style.display = 'block';
        cc.textContent = '"' + comments + '"';
    } else {
        cc.style.display = 'none';
    }

    document.getElementById('confirmOverlay').classList.add('open');
}

function closeConfirm() {
    document.getElementById('confirmOverlay').classList.remove('open');
}

function submitDecision() {
    document.getElementById('actionInput').value    = _pendingAction;
    document.getElementById('commentsHidden').value = document.getElementById('commentsInput').value;
    const msg = _pendingAction === 'approve' ? 'Approving batch…' : 'Rejecting batch…';
    document.getElementById('pageLoaderMsg').textContent = msg;
    document.getElementById('pageLoader').style.display  = 'flex';
    document.getElementById('realForm').submit();
}

function openItemDetail(num, material, batchNo, qty, boxNo, remark, materialName, country, mfgDate, expDate, fromLoc, toLoc, uploadedAt) {
    const body = document.getElementById('itemModalBody');
    const cell = (label, val) => `<div class="detail-cell"><label>${label}</label><p>${escHtml(val||'') || '—'}</p></div>`;
    body.innerHTML = `
        <div class="detail-row">
            ${cell('Item #', String(num))}
            ${cell('Serial No', materialName)}
            ${cell('Country', country)}
        </div>
        <div class="detail-row">
            ${cell('Code', material)}
            ${cell('Batch No', batchNo)}
            ${cell('Qty', qty)}
        </div>
        <div class="detail-row">
            ${cell('Mfg Date', mfgDate)}
            ${cell('Exp Date', expDate)}
            ${cell('Box No', boxNo)}
        </div>
        <div class="detail-row">
            ${cell('From Storage', fromLoc)}
            ${cell('To Storage', toLoc)}
            ${cell('Uploaded At', uploadedAt)}
        </div>
        <div><label style="font-size:.7rem;font-weight:700;color:#94a3b8;text-transform:uppercase;letter-spacing:.07em;display:block;margin-bottom:6px;">Remarks</label>
        <div class="remark-box">${escHtml(remark) || '<span style="color:#9ca3af;font-style:italic;">No remark provided</span>'}</div></div>
    `;
    document.getElementById('itemOverlay').classList.add('open');
}

function closeItemDetail() {
    document.getElementById('itemOverlay').classList.remove('open');
}

function escHtml(str) {
    return String(str || '').replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;');
}

document.getElementById('confirmOverlay').addEventListener('click', function(e){ if(e.target===this) closeConfirm(); });
document.getElementById('itemOverlay').addEventListener('click', function(e){ if(e.target===this) closeItemDetail(); });
