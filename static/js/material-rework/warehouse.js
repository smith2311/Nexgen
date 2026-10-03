function getCookie(name) {
    var c = document.cookie.split(';').map(function(s){ return s.trim(); }).find(function(s){ return s.startsWith(name + '='); });
    return c ? decodeURIComponent(c.split('=')[1]) : null;
}

/* ── Pending table: checkbox / recv input wiring ── */
const cbs       = document.querySelectorAll('.row-cb');
const btn       = document.getElementById('receiveBtn');
const selCount  = document.getElementById('selCount');
const selectAll = document.getElementById('selectAll');

function toggleRecv(cb) {
    const inp    = document.getElementById('recv_'    + cb.value);
    const rmk    = document.getElementById('wh_remark_' + cb.value);
    const inward = document.getElementById('inward_'  + cb.value);
    const outward= document.getElementById('outward_' + cb.value);
    if (inp) {
        inp.disabled = !cb.checked;
        if (!cb.checked) {
            inp.classList.remove('recv-err');
            const e = inp.parentNode.querySelector('.recv-err-msg');
            if (e) e.remove();
        }
    }
    if (rmk)    rmk.disabled    = !cb.checked;
    if (inward) inward.disabled  = !cb.checked;
    if (outward) outward.disabled = !cb.checked;
    updateBtn();
}

function updateBtn() {
    const checked = document.querySelectorAll('.row-cb:checked').length;
    btn.disabled = checked === 0;
    selCount.textContent = checked ? `${checked} item(s) selected` : '';
}

function validateRecvInput(inp) {
    const val   = parseInt(inp.value, 10) || 0;
    const limit = parseInt(inp.dataset.max, 10) || 0;
    const wrap  = inp.parentNode;
    let errEl   = wrap.querySelector('.recv-err-msg');
    if (val > limit) {
        inp.classList.add('recv-err');
        if (!errEl) {
            errEl = document.createElement('span');
            errEl.className = 'recv-err-msg';
            wrap.appendChild(errEl);
        }
        errEl.textContent = `Max: ${limit}`;
    } else {
        inp.classList.remove('recv-err');
        if (errEl) errEl.remove();
    }
}

cbs.forEach(cb => cb.addEventListener('change', () => toggleRecv(cb)));
if (selectAll) {
    selectAll.addEventListener('change', () => {
        cbs.forEach(cb => { cb.checked = selectAll.checked; toggleRecv(cb); });
    });
}

document.getElementById('whForm').addEventListener('submit', function(e) {
    let hasErr = false;
    document.querySelectorAll('.recv-input:not([disabled])').forEach(inp => {
        validateRecvInput(inp);
        if (inp.classList.contains('recv-err')) hasErr = true;
    });
    if (hasErr) {
        e.preventDefault();
        const first = document.querySelector('.recv-input.recv-err');
        if (first) first.scrollIntoView({ behavior: 'smooth', block: 'center' });
    }
});

/* ── Batch detail slide panel ── */
function openBatchDetail(batchId) {
    const data = BATCH_ITEMS[String(batchId)];
    if (!data) return;

    document.getElementById('panelRefTitle').textContent = data.ref;

    const batchStatusBadge = data.batchStatus === 'completed'
        ? `<span style="background:#d1fae5;color:#065f46;padding:2px 9px;border-radius:10px;font-size:.73rem;font-weight:700;"><i class="fas fa-check"></i> Completed</span>`
        : `<span style="background:#ede9fe;color:#5b21b6;padding:2px 9px;border-radius:10px;font-size:.73rem;font-weight:700;"><i class="fas fa-clock"></i> In Progress</span>`;

    let itemRows = '';
    for (const it of data.items) {
        const recv = parseFloat(it.qtyReceived) || 0;
        const itemBadge = it.status === 'received'
            ? `<span style="background:#d1fae5;color:#065f46;padding:1px 7px;border-radius:8px;font-size:.7rem;font-weight:700;white-space:nowrap;"><i class="fas fa-check"></i> Received</span>`
            : (it.status === 'accepted'
                ? `<span style="background:#d1fae5;color:#065f46;padding:1px 7px;border-radius:8px;font-size:.7rem;font-weight:700;white-space:nowrap;"><i class="fas fa-check-double"></i> Prod. Accepted</span>`
                : (recv > 0
                    ? `<span style="background:#fef3c7;color:#92400e;padding:1px 7px;border-radius:8px;font-size:.7rem;font-weight:700;white-space:nowrap;"><i class="fas fa-adjust"></i> Partial</span>`
                    : `<span style="background:#ede9fe;color:#5b21b6;padding:1px 7px;border-radius:8px;font-size:.7rem;font-weight:700;white-space:nowrap;"><i class="fas fa-clock"></i> Pending</span>`));

        const displayQty = it.status === 'accepted' ? it.acceptedQty : it.qtyReceived;

        itemRows += `
        <tr style="border-bottom:1px solid #f3f4f6;">
            <td style="padding:8px 10px;font-size:.78rem;color:#64748b;">${escHtml(it.serialNumber)||'—'}</td>
            <td style="padding:8px 10px;font-size:.78rem;color:#64748b;">${escHtml(it.country)||'—'}</td>
            <td style="padding:8px 10px;font-size:.83rem;font-weight:600;color:#06152b;">${escHtml(it.material)}</td>
            <td style="padding:8px 10px;font-size:.78rem;color:#64748b;">${escHtml(it.batchNo)||'—'}</td>
            <td style="padding:8px 10px;font-size:.78rem;white-space:nowrap;color:#64748b;">${escHtml(it.mfgDate)||'—'}</td>
            <td style="padding:8px 10px;font-size:.78rem;white-space:nowrap;color:#64748b;">${escHtml(it.expDate)||'—'}</td>
            <td style="padding:8px 10px;font-size:.78rem;color:#64748b;">${escHtml(it.fromStorageLocation)||'—'}</td>
            <td style="padding:8px 10px;font-size:.78rem;color:#64748b;">${escHtml(it.toStorageLocation)||'—'}</td>
            <td style="padding:8px 10px;font-size:.83rem;color:#0076c8;font-weight:700;text-align:right;">${it.acceptedQty}</td>
            <td style="padding:8px 10px;font-size:.83rem;color:#10b981;font-weight:700;text-align:right;">${displayQty}</td>
            <td style="padding:8px 10px;font-size:.78rem;color:#64748b;">${escHtml(it.boxNo)||'—'}</td>
            <td style="padding:8px 10px;font-size:.78rem;white-space:nowrap;color:#64748b;">${escHtml(it.uploadedAt)||'—'}</td>
            <td style="padding:8px 10px;font-size:.78rem;color:#374151;max-width:160px;">${escHtml(it.whRemarks)||'—'}</td>
            <td style="padding:8px 10px;">${itemBadge}</td>
        </tr>`;
    }

    document.getElementById('panelBody').innerHTML = `
        <div class="detail-section">
            <div class="detail-section-title">Batch Information</div>
            <div class="detail-grid">
                <div class="detail-cell full"><label>Reference #</label>
                    <p style="font-family:monospace;color:#1d4ed8;">${escHtml(data.ref)}</p></div>
                <div class="detail-cell"><label>Vertical</label><p>${escHtml(data.vertical)}</p></div>
                <div class="detail-cell"><label>Department</label><p>${escHtml(data.department)}</p></div>
                <div class="detail-cell"><label>Requested By</label><p>${escHtml(data.requestedBy)}</p></div>
                <div class="detail-cell"><label>Uploaded By</label><p>${escHtml(data.uploadedBy)}</p></div>
                <div class="detail-cell full"><label>Batch Status</label><p>${batchStatusBadge}</p></div>
                ${data.subject ? `<div class="detail-cell full"><label>Email Subject</label><p style="font-weight:600;color:#0076c8;">${escHtml(data.subject)}</p></div>` : ''}
            </div>
        </div>
        <div class="detail-section">
            <div class="detail-section-title">Items — ${data.items.length} total</div>
            <div style="overflow-x:auto;border:1px solid #e5e7eb;border-radius:8px;">
                <table style="width:100%;border-collapse:collapse;min-width:900px;">
                    <thead>
                        <tr style="background:#f8fafc;">
                            <th style="padding:7px 10px;font-size:.7rem;font-weight:700;color:#6b7280;border-bottom:1px solid #e5e7eb;">Serial No</th>
                            <th style="padding:7px 10px;font-size:.7rem;font-weight:700;color:#6b7280;border-bottom:1px solid #e5e7eb;">Country</th>
                            <th style="padding:7px 10px;font-size:.7rem;font-weight:700;color:#6b7280;border-bottom:1px solid #e5e7eb;">Code</th>
                            <th style="padding:7px 10px;font-size:.7rem;font-weight:700;color:#6b7280;border-bottom:1px solid #e5e7eb;">Batch</th>
                            <th style="padding:7px 10px;font-size:.7rem;font-weight:700;color:#6b7280;border-bottom:1px solid #e5e7eb;">Mfg Date</th>
                            <th style="padding:7px 10px;font-size:.7rem;font-weight:700;color:#6b7280;border-bottom:1px solid #e5e7eb;">Exp Date</th>
                            <th style="padding:7px 10px;font-size:.7rem;font-weight:700;color:#6b7280;border-bottom:1px solid #e5e7eb;">From Storage</th>
                            <th style="padding:7px 10px;font-size:.7rem;font-weight:700;color:#6b7280;border-bottom:1px solid #e5e7eb;">To Storage</th>
                            <th style="padding:7px 10px;font-size:.7rem;font-weight:700;color:#0076c8;text-align:right;border-bottom:1px solid #e5e7eb;">WH Qty</th>
                            <th style="padding:7px 10px;font-size:.7rem;font-weight:700;color:#10b981;text-align:right;border-bottom:1px solid #e5e7eb;">Received</th>
                            <th style="padding:7px 10px;font-size:.7rem;font-weight:700;color:#6b7280;border-bottom:1px solid #e5e7eb;">Box No</th>
                            <th style="padding:7px 10px;font-size:.7rem;font-weight:700;color:#6b7280;border-bottom:1px solid #e5e7eb;">Uploaded At</th>
                            <th style="padding:7px 10px;font-size:.7rem;font-weight:700;color:#6b7280;border-bottom:1px solid #e5e7eb;">WH Remarks</th>
                            <th style="padding:7px 10px;font-size:.7rem;font-weight:700;color:#6b7280;border-bottom:1px solid #e5e7eb;">Status</th>
                        </tr>
                    </thead>
                    <tbody>${itemRows}</tbody>
                </table>
            </div>
        </div>`;

    document.getElementById('slideOverlay').classList.add('open');
}

function closeDetail() {
    document.getElementById('slideOverlay').classList.remove('open');
}

/* ── Received history: toggle group rows ── */
function toggleGroup(grpId) {
    const rows = document.querySelectorAll('.' + grpId);
    const icon = document.getElementById(grpId + '-icon');
    const hidden = rows.length > 0 && rows[0].style.display === 'none';
    rows.forEach(r => r.style.display = hidden ? '' : 'none');
    if (icon) icon.style.transform = hidden ? 'rotate(90deg)' : '';
}

/* ── Edit received qty modal ── */
let _erAccepted = 0;

function openEditRecv(itemId, material, acceptedQty, currentRecv, currentRemarks) {
    _erAccepted = parseInt(acceptedQty, 10) || 0;
    document.getElementById('er_item_id').value               = itemId;
    document.getElementById('er_material_name').textContent   = material;
    document.getElementById('er_accepted_display').textContent = acceptedQty;
    document.getElementById('er_current_recv').textContent    = currentRecv;
    const inp = document.getElementById('er_new_qty');
    inp.value = currentRecv;
    inp.max   = acceptedQty;
    inp.classList.remove('recv-err');
    document.getElementById('er_remarks').value = currentRemarks || '';
    document.querySelector('.btn-save-sm').disabled = false;
    updatePendingPreview();
    document.getElementById('erOverlay').classList.add('open');
}

function closeEditRecv() {
    document.getElementById('erOverlay').classList.remove('open');
}

function updatePendingPreview() {
    const inp     = document.getElementById('er_new_qty');
    const newRecv = parseInt(inp.value, 10) || 0;
    const preview = document.getElementById('er_pending_preview');
    const saveBtn = document.querySelector('.btn-save-sm');

    if (newRecv > _erAccepted) {
        inp.classList.add('recv-err');
        if (saveBtn) saveBtn.disabled = true;
        preview.innerHTML = `<span style="color:#ef4444;font-weight:700;font-size:.8rem;"><i class="fas fa-exclamation-circle"></i> Exceeds accepted qty — max allowed is <strong>${_erAccepted}</strong></span>`;
        return;
    }

    inp.classList.remove('recv-err');
    if (saveBtn) saveBtn.disabled = false;

    const clamped = Math.min(Math.max(newRecv, 0), _erAccepted);
    const pending = Math.max(0, _erAccepted - clamped);
    if (pending <= 0) {
        preview.innerHTML = `After update: item will be marked <strong style="color:#065f46;">Received</strong> (pending = 0)`;
    } else {
        preview.innerHTML = `After update: Qty Pending will be <strong>${pending}</strong>`;
    }
}

function escHtml(str) {
    return String(str||'').replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;');
}

/* openDelModal / closeDelModal provided by _delete_batch_modal.html */

function onBatchDeleted(batchId) {
    document.querySelectorAll('[data-batch-id="' + batchId + '"]').forEach(function(el){ el.remove(); });
    showWhToast('Batch deleted.');
}

function showWhToast(msg) {
    var t = document.createElement('div');
    t.style.cssText = 'position:fixed;top:20px;right:20px;z-index:9999999;padding:12px 18px;border-radius:8px;font-size:.87rem;font-weight:600;background:#ecfdf5;border:1px solid #6ee7b7;color:#065f46;box-shadow:0 4px 20px rgba(0,0,0,.15);display:flex;align-items:center;gap:8px;';
    t.innerHTML = '<i class="fas fa-check-circle"></i><span>' + escHtml(msg) + '</span>';
    document.body.appendChild(t);
    setTimeout(function(){ t.style.transition='opacity .4s'; t.style.opacity='0'; setTimeout(function(){ t.remove(); },400); },3000);
}

function submitEditRecv() {
    var itemId = document.getElementById('er_item_id').value;
    var qty    = document.getElementById('er_new_qty').value;
    if (!itemId) return;
    document.getElementById('er_ajax_err').style.display = 'none';

    var saveBtn = document.getElementById('erSaveBtn');
    saveBtn.disabled = true; saveBtn.innerHTML = '<i class="fas fa-spinner fa-spin"></i> Saving…';

    var remarks = document.getElementById('er_remarks').value;
    var fd = new FormData();
    fd.append('action', 'edit_received');
    fd.append('item_id', itemId);
    fd.append('new_qty_received', qty);
    fd.append('warehouse_remarks', remarks);
    fd.append('csrfmiddlewaretoken', getCookie('csrftoken') || '');

    fetch(window.location.href, {
        method: 'POST',
        body: fd,
        headers: { 'X-Requested-With': 'XMLHttpRequest' }
    })
    .then(function(r) { return r.json(); })
    .then(function(data) {
        if (data.ok) {
            saveBtn.disabled = false; saveBtn.innerHTML = '<i class="fas fa-check"></i> Update';
            closeEditRecv();
            var row = document.querySelector('tr[data-item-id="' + data.item_id + '"]');
            if (row) {
                var recvCell = row.querySelector('[data-cell="qty_received"]');
                var pendCell = row.querySelector('[data-cell="pending_qty"]');
                if (recvCell) recvCell.textContent = data.qty_received;
                if (pendCell) {
                    var pendInp = pendCell.querySelector('input');
                    if (pendInp) {
                        pendInp.value = data.pending_qty;
                        pendInp.max   = data.pending_qty;
                        pendInp.setAttribute('data-max', data.pending_qty);
                    } else {
                        pendCell.textContent = data.pending_qty;
                    }
                }
                if (data.status === 'received') {
                    var batchClass = Array.from(row.classList).find(function(c){ return c.startsWith('pgrp') && c !== 'pgrp-row'; });
                    row.remove();
                    if (batchClass && !document.querySelector('tr.' + batchClass)) {
                        var hdr = document.querySelector('tr.pgrp-hdr[data-batch-id="' + data.batch_id + '"]');
                        if (hdr) hdr.remove();
                    }
                    var pendChip = document.querySelector('.count-chip:not(.green)');
                    if (pendChip) {
                        var cur = parseInt(pendChip.textContent) || 1;
                        var n = Math.max(0, cur - 1);
                        pendChip.textContent = n + ' item' + (n !== 1 ? 's' : '');
                    }
                    var recvChip = document.querySelector('.count-chip.green');
                    if (recvChip) {
                        var cur2 = parseInt(recvChip.textContent) || 0;
                        var n2 = cur2 + 1;
                        recvChip.textContent = n2 + ' item' + (n2 !== 1 ? 's' : '');
                    }
                } else {
                    var editBtn = row.querySelector('.btn-edit-recv');
                    if (editBtn) {
                        var mat = editBtn.getAttribute('onclick').match(/openEditRecv\(\d+,'([^']*)'/);
                        var matName = mat ? mat[1] : '';
                        var newRemarks = (data.warehouse_remarks || '').replace(/'/g, "\\'");
                        editBtn.setAttribute('onclick',
                            "openEditRecv(" + data.item_id + ",'" + matName + "','" + data.accepted_qty + "','" + data.qty_received + "','" + newRemarks + "')");
                    }
                }
            }
            var bId = String(data.batch_id);
            if (BATCH_ITEMS[bId]) {
                var items = BATCH_ITEMS[bId].items;
                for (var i = 0; i < items.length; i++) {
                    if (items[i].id == data.item_id) {
                        items[i].qtyReceived  = data.qty_received;
                        items[i].pendingQty   = data.pending_qty;
                        items[i].status       = data.status;
                        items[i].whRemarks    = data.warehouse_remarks || '';
                        break;
                    }
                }
            }
            showWhToast('Received qty updated.');
        } else {
            document.getElementById('er_ajax_err').textContent = data.error || 'Update failed.';
            document.getElementById('er_ajax_err').style.display = 'block';
            saveBtn.disabled = false; saveBtn.innerHTML = '<i class="fas fa-check"></i> Update';
        }
    })
    .catch(function(){
        document.getElementById('er_ajax_err').textContent = 'Network error.';
        document.getElementById('er_ajax_err').style.display = 'block';
        saveBtn.disabled = false; saveBtn.innerHTML = '<i class="fas fa-check"></i> Update';
    });
}

/* ──────────────────────────────────────────────────────────────
   Auto-sync: poll the warehouse signature and refresh when items
   are inserted or updated by someone else — without a manual reload.
   We never reload while the user is mid-entry (that would wipe their
   typed quantities); in that case we show a dismissible banner instead.
   ────────────────────────────────────────────────────────────── */
(function () {
    if (typeof WH_STATUS_URL === 'undefined') return;   // template didn't wire it up
    var knownSignature = (typeof WH_SIGNATURE !== 'undefined') ? WH_SIGNATURE : null;
    var bannerShown    = false;

    // True when the user has work in progress that a reload would discard.
    function isUserBusy() {
        if (document.querySelectorAll('.row-cb:checked').length > 0) return true;
        var loader = document.getElementById('pageLoader');
        if (loader && loader.style.display !== 'none' && loader.offsetParent !== null) return true;
        if (document.querySelector('#erOverlay.open'))     return true;   // edit-qty modal
        if (document.querySelector('#slideOverlay.open'))  return true;   // batch detail panel
        if (document.querySelector('#delOverlay.open'))    return true;   // delete confirm
        return false;
    }

    function showUpdateBanner() {
        if (bannerShown) return;
        bannerShown = true;
        var bar = document.createElement('div');
        bar.id = 'whUpdateBanner';
        bar.style.cssText = 'position:fixed;left:50%;bottom:24px;transform:translateX(-50%);z-index:9999999;'
            + 'background:#1e293b;color:#fff;padding:12px 18px;border-radius:10px;box-shadow:0 8px 30px rgba(0,0,0,.3);'
            + 'display:flex;align-items:center;gap:14px;font-size:.88rem;font-weight:600;';
        bar.innerHTML = '<i class="fas fa-sync-alt" style="color:#10b981;"></i>'
            + '<span>New warehouse updates are available.</span>'
            + '<button type="button" id="whRefreshNow" style="background:#10b981;color:#fff;border:none;'
            + 'padding:7px 14px;border-radius:7px;font-weight:700;cursor:pointer;font-size:.83rem;">Refresh now</button>'
            + '<button type="button" id="whBannerDismiss" style="background:transparent;color:#94a3b8;border:none;'
            + 'cursor:pointer;font-size:1.1rem;line-height:1;">&times;</button>';
        document.body.appendChild(bar);
        document.getElementById('whRefreshNow').addEventListener('click', function () { location.reload(); });
        document.getElementById('whBannerDismiss').addEventListener('click', function () { bar.remove(); });
    }

    function pollWarehouse() {
        if (document.hidden) return;
        fetch(WH_STATUS_URL, { headers: { 'X-Requested-With': 'XMLHttpRequest' } })
            .then(function (r) { return r.ok ? r.json() : null; })
            .then(function (data) {
                if (!data || !data.signature) return;
                if (knownSignature === null) { knownSignature = data.signature; return; }
                if (data.signature === knownSignature) return;   // nothing changed
                // Data changed on the server.
                if (isUserBusy()) {
                    showUpdateBanner();                          // don't clobber their input
                } else {
                    location.reload();                           // safe to sync immediately
                }
            })
            .catch(function () {});   // network blips: just try again next tick
    }

    setInterval(pollWarehouse, 30000);
    document.addEventListener('visibilitychange', function () {
        if (!document.hidden) pollWarehouse();
    });
})();

/* ── The rework round trip: warehouse's two legs ─────────────────────────
   Release to production, then confirm what comes back. Both are per-line
   POSTs to their own endpoints rather than actions on the batch-grouped form
   above, because a line is released and returns on its own schedule.

   Errors are shown as a toast and the row is left alone, so a rejected
   submission never looks like it succeeded. */

function showWhError(msg) {
    var t = document.createElement('div');
    t.style.cssText = 'position:fixed;top:20px;right:20px;z-index:9999999;padding:12px 18px;border-radius:8px;font-size:.87rem;font-weight:600;background:#fef2f2;border:1px solid #fecaca;color:#991b1b;box-shadow:0 4px 20px rgba(0,0,0,.15);display:flex;align-items:center;gap:8px;max-width:420px;';
    t.innerHTML = '<i class="fas fa-exclamation-circle"></i><span>' + escHtml(msg) + '</span>';
    document.body.appendChild(t);
    setTimeout(function () {
        t.style.transition = 'opacity .4s'; t.style.opacity = '0';
        setTimeout(function () { t.remove(); }, 400);
    }, 5000);
}

function _reworkPost(url, fields, btn, rowId, okMsg) {
    var fd = new FormData();
    Object.keys(fields).forEach(function (k) { fd.append(k, fields[k]); });
    fd.append('csrfmiddlewaretoken', getCookie('csrftoken') || '');

    var orig = btn ? btn.innerHTML : '';
    if (btn) { btn.disabled = true; btn.innerHTML = '<i class="fas fa-spinner fa-spin"></i>'; }

    return fetch(url, {
        method: 'POST',
        body: fd,
        headers: { 'X-Requested-With': 'XMLHttpRequest' }
    })
    .then(function (r) { return r.json().then(function (d) { return { ok: r.ok, body: d }; }); })
    .then(function (res) {
        var data = res.body || {};
        if (data.ok) {
            // The line has moved to another team's queue, so drop it from this
            // table rather than leaving a stale row that looks actionable.
            var row = document.getElementById(rowId);
            if (row) { row.remove(); }
            showWhToast(okMsg);
        } else {
            if (btn) { btn.disabled = false; btn.innerHTML = orig; }
            showWhError(data.error || 'Could not save. Please try again.');
        }
    })
    .catch(function () {
        if (btn) { btn.disabled = false; btn.innerHTML = orig; }
        showWhError('Network error — nothing was saved. Please try again.');
    });
}

function reworkRelease(itemId) {
    var qtyEl = document.getElementById('rel_qty_' + itemId);
    var remEl = document.getElementById('rel_rem_' + itemId);
    var qty   = qtyEl ? qtyEl.value : '';
    var rem   = remEl ? remEl.value.trim() : '';
    if (!rem) {
        showWhError('A remark is required to confirm the handover.');
        if (remEl) { remEl.focus(); }
        return;
    }
    _reworkPost(
        REWORK_RELEASE_URL.replace('0', itemId),
        { released_qty: qty, remarks: rem },
        event && event.target ? event.target.closest('button') : null,
        'rw-release-' + itemId,
        'Released to production.'
    );
}

function reworkReceive(itemId) {
    var qtyEl = document.getElementById('rcv_qty_' + itemId);
    var remEl = document.getElementById('rcv_rem_' + itemId);
    var qty   = qtyEl ? qtyEl.value : '';
    var rem   = remEl ? remEl.value.trim() : '';
    var sent  = qtyEl ? qtyEl.dataset.sent : null;
    // Client-side mirror of the server rule, so a mismatch asks for the reason
    // before a round trip rather than after.
    if (sent !== null && String(qty) !== String(sent) && !rem) {
        showWhError('Production sent ' + sent + ' but you entered ' + qty + ' — a remark is required.');
        if (remEl) { remEl.focus(); }
        return;
    }
    _reworkPost(
        REWORK_RECEIVE_URL.replace('0', itemId),
        { received_qty: qty, remarks: rem },
        event && event.target ? event.target.closest('button') : null,
        'rw-receipt-' + itemId,
        'Receipt confirmed.'
    );
}
