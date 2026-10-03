function calcPending(el) {
    const total    = parseInt(el.dataset.total, 10) || 0;
    const accepted = parseInt(el.value, 10);
    const pendSpan = document.getElementById('pend_' + el.dataset.id);
    let errEl      = el.parentNode.querySelector('.acc-err');

    if (isNaN(accepted) || el.value.trim() === '') {
        if (pendSpan) { pendSpan.textContent = total; pendSpan.style.color = '#8b5cf6'; }
        el.classList.remove('qty-over');
        if (errEl) errEl.remove();
        return;
    }

    if (accepted > total) {
        el.classList.add('qty-over');
        if (!errEl) {
            errEl = document.createElement('div');
            errEl.className = 'acc-err';
            errEl.style.cssText = 'color:#dc2626;font-size:.72rem;margin-top:3px;font-weight:600;';
            el.parentNode.appendChild(errEl);
        }
        errEl.textContent = 'Max: ' + total;
        if (pendSpan) { pendSpan.textContent = '—'; pendSpan.style.color = '#8b5cf6'; }
    } else {
        el.classList.remove('qty-over');
        if (errEl) errEl.remove();
        const remainder = total - accepted;
        /* Special rule: if accepted = total (remainder = 0), ALL qty go to warehouse */
        const sendToWh = (remainder === 0 && accepted > 0) ? total : remainder;
        if (pendSpan) {
            pendSpan.textContent = sendToWh;
            pendSpan.style.color = sendToWh === total && remainder === 0
                ? '#dc2626'   /* red highlight for special "all to WH" case */
                : '#8b5cf6';
        }
    }
}

document.querySelectorAll('.qty-input').forEach(el => calcPending(el));

/* Block form submit if any accepted qty exceeds total */
const prodForm = document.querySelector('form[method="post"]');
if (prodForm) {
    prodForm.addEventListener('submit', function (e) {
        let hasErr = false;
        document.querySelectorAll('.qty-input').forEach(el => {
            const total    = parseInt(el.dataset.total, 10) || 0;
            const accepted = parseInt(el.value, 10);
            if (!isNaN(accepted) && accepted > total) {
                hasErr = true;
                el.classList.add('qty-over');
            }
        });
        if (hasErr) {
            e.preventDefault();
            const first = document.querySelector('.qty-input.qty-over');
            if (first) first.scrollIntoView({ behavior: 'smooth', block: 'center' });
        }
    });
}

function openItemModal(num, material, batchNo, qty, boxNo, remark, materialName, country, mfgDate, expDate, fromLoc, toLoc, uploadedAt) {
    const cell = (label, val) => `<div class="detail-cell"><label>${label}</label><p>${escHtml(val||'')||'—'}</p></div>`;
    document.getElementById('modalBody').innerHTML = `
        <div class="detail-row">
            ${cell('Item #', String(num))}
            ${cell('Serial No', materialName)}
            ${cell('Country', country)}
        </div>
        <div class="detail-row">
            ${cell('Code', material)}
            ${cell('Batch No', batchNo)}
            ${cell('Total Qty', qty)}
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
        <div class="detail-cell full" style="margin-bottom:0;">
            <label>Remarks</label>
            <div class="remark-box">${escHtml(remark)||'<span style="color:#9ca3af;font-style:italic;">No remark</span>'}</div>
        </div>`;
    document.getElementById('itemOverlay').classList.add('open');
}

function closeModal() {
    document.getElementById('itemOverlay').classList.remove('open');
}

function escHtml(str) {
    return String(str||'').replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;');
}
