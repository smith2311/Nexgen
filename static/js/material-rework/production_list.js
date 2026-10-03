function escHtml(str) {
    return String(str||'').replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;');
}

function openPreview(batchId) {
    const b = BATCHES[batchId];
    if (!b) return;
    document.getElementById('slideOverlay').classList.add('open');
    document.getElementById('panelFoot').style.display = 'none';

    const srcBadge = b.source
        ? `<span class="badge-src ${b.source}">${escHtml(b.source_display)}</span>`
        : '—';

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
            <div class="panel-meta-item"><label>Reference #</label><p style="font-family:monospace;color:#1d4ed8;">${escHtml(b.reference_number)}</p></div>
            <div class="panel-meta-item"><label>Source</label><p>${srcBadge}</p></div>
            <div class="panel-meta-item"><label>Vertical</label><p>${escHtml(b.vertical)}</p></div>
            <div class="panel-meta-item"><label>Department</label><p>${escHtml(b.department)}</p></div>
            <div class="panel-meta-item"><label>Submitted By</label><p>${escHtml(b.uploaded_by)}</p></div>
            <div class="panel-meta-item"><label>Upload Date</label><p>${b.uploaded_at}</p></div>
            <div class="panel-meta-item"><label>HOD Approved</label><p>${b.hod_approved}</p></div>
            ${b.subject ? `<div class="panel-meta-item" style="grid-column:1/-1;"><label>Email Subject</label><p style="font-weight:600;color:#0076c8;">${escHtml(b.subject)}</p></div>` : ''}
            ${b.hod_comments ? `<div class="panel-meta-item" style="grid-column:1/-1;"><label>HOD Comments</label><p style="font-weight:400;font-style:italic;color:#374151;">"${escHtml(b.hod_comments)}"</p></div>` : ''}
        </div>
        <p class="panel-section-title"><i class="fas fa-list-ul" style="color:#1d4ed8;margin-right:5px;"></i> Items (${b.items.length})</p>
        <div style="overflow-x:auto;">
        <table class="panel-items-table" style="min-width:900px;">
            <thead><tr><th>#</th><th>Serial No</th><th>Country</th><th>Code</th><th>Batch</th><th>Mfg Date</th><th>Exp Date</th><th>From Storage</th><th>To Storage</th><th>Qty</th><th>Box No</th><th>Remarks</th><th>Uploaded At</th></tr></thead>
            <tbody>${rows}</tbody>
        </table>
        </div>`;
}

function closePreview() {
    document.getElementById('slideOverlay').classList.remove('open');
}

/* openDelModal and closeDelModal are provided by _delete_batch_modal.html */

function onBatchDeleted(batchId) {
    var row = document.querySelector('tr[data-batch-id="' + batchId + '"]');
    if (row) row.remove();
    showProdToast('Batch deleted.');
}

function showProdToast(msg) {
    var t = document.createElement('div');
    t.style.cssText = 'position:fixed;top:20px;right:20px;z-index:9999999;padding:12px 18px;border-radius:8px;font-size:.87rem;font-weight:600;background:#ecfdf5;border:1px solid #6ee7b7;color:#065f46;box-shadow:0 4px 20px rgba(0,0,0,.15);display:flex;align-items:center;gap:8px;';
    t.innerHTML = '<i class="fas fa-check-circle"></i><span>' + msg + '</span>';
    document.body.appendChild(t);
    setTimeout(function(){ t.style.transition='opacity .4s'; t.style.opacity='0'; setTimeout(function(){ t.remove(); },400); },3000);
}

/* ── Rework round trip, leg 3: send the reworked units back ──────────────
   Production reports what it actually reworked. Fewer than were released is
   allowed — that is how breakage is recorded — but the server requires a
   reason, and this mirrors that check so the operator finds out before the
   round trip rather than after. */

function _prodToast(msg, isError) {
    var ok = !isError;
    var t = document.createElement('div');
    t.style.cssText = 'position:fixed;top:20px;right:20px;z-index:9999999;padding:12px 18px;border-radius:8px;'
        + 'font-size:.87rem;font-weight:600;box-shadow:0 4px 20px rgba(0,0,0,.15);display:flex;align-items:center;gap:8px;max-width:420px;'
        + (ok ? 'background:#ecfdf5;border:1px solid #6ee7b7;color:#065f46;'
              : 'background:#fef2f2;border:1px solid #fecaca;color:#991b1b;');
    var icon = ok ? 'fa-check-circle' : 'fa-exclamation-circle';
    t.innerHTML = '<i class="fas ' + icon + '"></i><span></span>';
    t.querySelector('span').textContent = msg;
    document.body.appendChild(t);
    setTimeout(function () {
        t.style.transition = 'opacity .4s'; t.style.opacity = '0';
        setTimeout(function () { t.remove(); }, 400);
    }, ok ? 3000 : 5000);
}

function _prodCsrf() {
    var m = document.cookie.match(/(?:^|;\s*)csrftoken=([^;]*)/);
    return m ? decodeURIComponent(m[1]) : '';
}

function reworkReturn(itemId) {
    var qtyEl    = document.getElementById('rwk_qty_' + itemId);
    var reasonEl = document.getElementById('rwk_reason_' + itemId);
    var qty      = qtyEl ? qtyEl.value : '';
    var reason   = reasonEl ? reasonEl.value.trim() : '';
    var released = qtyEl ? Number(qtyEl.dataset.released) : null;

    if (released !== null && Number(qty) < released && !reason) {
        _prodToast((released - Number(qty)) + ' unit(s) short of what was released — a reason is required.', true);
        if (reasonEl) { reasonEl.focus(); }
        return;
    }

    var btn = event && event.target ? event.target.closest('button') : null;
    var orig = btn ? btn.innerHTML : '';
    if (btn) { btn.disabled = true; btn.innerHTML = '<i class="fas fa-spinner fa-spin"></i>'; }

    var fd = new FormData();
    fd.append('reworked_qty', qty);
    fd.append('loss_reason', reason);
    fd.append('csrfmiddlewaretoken', _prodCsrf());

    fetch(REWORK_RETURN_URL.replace('0', itemId), {
        method: 'POST',
        body: fd,
        headers: { 'X-Requested-With': 'XMLHttpRequest' }
    })
    .then(function (r) { return r.json(); })
    .then(function (data) {
        if (data.ok) {
            // Now warehouse's move, so it leaves this table.
            var row = document.getElementById('rw-inwork-' + itemId);
            if (row) { row.remove(); }
            _prodToast('Sent back to warehouse.');
        } else {
            if (btn) { btn.disabled = false; btn.innerHTML = orig; }
            _prodToast(data.error || 'Could not save. Please try again.', true);
        }
    })
    .catch(function () {
        if (btn) { btn.disabled = false; btn.innerHTML = orig; }
        _prodToast('Network error — nothing was saved. Please try again.', true);
    });
}
