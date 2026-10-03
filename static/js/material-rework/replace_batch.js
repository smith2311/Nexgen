const form        = document.getElementById('replaceForm');
const fileInput   = document.getElementById('excelFileInput');
const overlay     = document.getElementById('previewOverlay');
const previewBody = document.getElementById('previewBody');
const previewMeta = document.getElementById('previewMeta');
const previewWarn = document.getElementById('previewWarn');
const confirmBtn  = document.getElementById('confirmBtn');
const footNote    = document.getElementById('previewFootNote');
let _confirmed = false;

form.addEventListener('submit', function(e) {
    if (_confirmed) return;
    e.preventDefault();
    const file = fileInput.files[0];
    if (!file) return;
    openPreview(file);
});

function openPreview(file) {
    overlay.classList.add('open');
    previewBody.innerHTML = '<div class="prev-loading"><i class="fas fa-spinner fa-spin" style="font-size:1.5rem;margin-bottom:10px;display:block;"></i>Reading file…</div>';
    previewMeta.innerHTML = '';
    previewWarn.style.display = 'none';
    confirmBtn.disabled = true;
    footNote.textContent = '';

    const reader = new FileReader();
    reader.onload = function(ev) {
        try {
            const data = new Uint8Array(ev.target.result);
            const wb   = XLSX.read(data, { type: 'array' });
            const ws   = wb.Sheets[wb.SheetNames[0]];
            const rows = XLSX.utils.sheet_to_json(ws, { header: 1, defval: '' });

            if (!rows || rows.length < 2) {
                previewBody.innerHTML = '<div class="prev-err"><i class="fas fa-exclamation-circle"></i> The file appears to be empty or has no data rows.</div>';
                return;
            }

            const headers  = rows[0];
            const dataRows = rows.slice(1).filter(r => r.some(c => c !== ''));
            const total    = dataRows.length;
            const preview  = dataRows.slice(0, 100);

            previewMeta.innerHTML = `
                <span class="prev-meta-chip"><i class="fas fa-file-excel" style="color:#16a34a"></i> <strong>${escHtml(file.name)}</strong></span>
                <span class="prev-meta-chip"><i class="fas fa-columns"></i> <strong>${headers.length}</strong> column${headers.length !== 1 ? 's' : ''}</span>
                <span class="prev-meta-chip"><i class="fas fa-list-ol"></i> <strong>${total}</strong> data row${total !== 1 ? 's' : ''}</span>
                <span class="prev-meta-chip"><i class="fas fa-hdd"></i> <strong>${(file.size / 1024).toFixed(1)} KB</strong></span>
            `;

            if (total > 100) previewWarn.style.display = 'block';

            let html = '<table class="prev-table"><thead><tr><th>#</th>';
            headers.forEach(h => { html += `<th>${escHtml(String(h))}</th>`; });
            html += '</tr></thead><tbody>';
            preview.forEach((row, i) => {
                html += `<tr><td>${i + 1}</td>`;
                headers.forEach((_, ci) => {
                    html += `<td title="${escHtml(String(row[ci] ?? ''))}">${escHtml(String(row[ci] ?? ''))}</td>`;
                });
                html += '</tr>';
            });
            html += '</tbody></table>';
            previewBody.innerHTML = html;

            footNote.textContent = total > 100
                ? `Previewing 100 of ${total} rows`
                : `${total} row${total !== 1 ? 's' : ''} ready to re-submit`;
            confirmBtn.disabled = false;

        } catch (err) {
            previewBody.innerHTML = `<div class="prev-err"><i class="fas fa-exclamation-circle"></i> Could not read file: ${escHtml(err.message)}</div>`;
        }
    };
    reader.onerror = function() {
        previewBody.innerHTML = '<div class="prev-err"><i class="fas fa-exclamation-circle"></i> Failed to read the file.</div>';
    };
    reader.readAsArrayBuffer(file);
}

function closePreview() {
    overlay.classList.remove('open');
    _confirmed = false;
}

function confirmUpload() {
    _confirmed = true;
    overlay.classList.remove('open');
    const loader = document.getElementById('uploadLoader');
    loader.style.display = 'flex';
    form.submit();
}

function escHtml(str) {
    return String(str)
        .replace(/&/g, '&amp;')
        .replace(/</g, '&lt;')
        .replace(/>/g, '&gt;')
        .replace(/"/g, '&quot;');
}

overlay.addEventListener('click', function(e) {
    if (e.target === overlay) closePreview();
});
