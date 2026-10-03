const API = '/meritrack/api';
const headers = {'X-CSRFToken': csrfToken, 'Content-Type': 'application/json'};
let currentReconId = null;
let scannedItems = [];

function setStep(n) {
    ['step1','step2','step3'].forEach((s,i) => {
        const el = document.getElementById(s);
        el.className = 'step ' + (i+1 < n ? 'done' : i+1 === n ? 'active' : 'pending');
    });
    document.getElementById('setupStep').style.display = n === 1 ? 'block' : 'none';
    document.getElementById('scanStep').style.display = n === 2 ? 'block' : 'none';
    document.getElementById('summaryStep').style.display = n === 3 ? 'block' : 'none';
}

async function startRecon() {
    const hospital = document.getElementById('hospitalSelect').value;
    const type = document.getElementById('reconType').value;
    const notes = document.getElementById('reconNotes').value;

    if (!hospital) { alert('Please select a hospital'); return; }

    try {
        const res = await fetch(`${API}/reconciliations/start-reconciliation/`, {
            method: 'POST', headers,
            body: JSON.stringify({ hospital_id: hospital, recon_type: type, notes })
        });
        const data = await res.json();
        if (!res.ok) { alert(data.error || 'Failed to start'); return; }

        currentReconId = data.id;
        document.getElementById('reconInfo').innerHTML =
            `<strong>${data.hospital_name}</strong> — ${data.recon_type} — Expected: <strong>${data.total_expected}</strong> items`;
        document.getElementById('expectedCount').textContent = `${data.total_expected} expected`;
        scannedItems = [];
        setStep(2);
        document.getElementById('barcodeInput').focus();
    } catch(e) {
        alert('Error starting reconciliation');
    }
}

async function scanItem() {
    const input = document.getElementById('barcodeInput');
    const barcode = input.value.trim();
    if (!barcode || !currentReconId) return;

    input.value = '';
    input.focus();

    try {
        const res = await fetch(`${API}/reconciliations/${currentReconId}/scan-item/`, {
            method: 'POST', headers,
            body: JSON.stringify({ barcode })
        });
        const data = await res.json();

        scannedItems.unshift(data);
        updateScanFeed();
        updateProgress();
    } catch(e) {
        addScanError(barcode, 'Scan failed');
    }
}

function updateScanFeed() {
    const feed = document.getElementById('scanFeed');
    feed.innerHTML = scannedItems.map(s => {
        const status = s.match_status || 'extra';
        const statusClass = status === 'matched' ? 'matched' : status === 'extra' ? 'extra' : 'error';
        return `<div class="scan-item">
            <div class="scan-status ${statusClass}"></div>
            <div class="scan-info">
                <div class="product">${s.product_name || s.barcode_data || 'Unknown'}</div>
                <div class="detail">${s.batch_number || ''} ${s.serial_number ? '| SN: ' + s.serial_number : ''}</div>
            </div>
            <span class="badge badge-${statusClass === 'matched' ? 'green' : statusClass === 'extra' ? 'yellow' : 'red'}">${status}</span>
        </div>`;
    }).join('');
    document.getElementById('scanBadge').textContent = scannedItems.length;
}

function updateProgress() {
    const expected = parseInt(document.getElementById('expectedCount').textContent) || 1;
    const scanned = scannedItems.length;
    const pct = Math.min(100, (scanned / expected * 100));
    document.getElementById('scanProgress').style.width = pct + '%';
    document.getElementById('scanCount').textContent = `${scanned} scanned`;
}

function addScanError(barcode, msg) {
    scannedItems.unshift({barcode_data: barcode, match_status: 'error', product_name: msg});
    updateScanFeed();
}

async function completeRecon() {
    if (!currentReconId) return;
    if (!confirm('Complete this reconciliation? Missing items will be flagged.')) return;

    try {
        const res = await fetch(`${API}/reconciliations/${currentReconId}/complete/`, {
            method: 'POST', headers
        });
        const data = await res.json();

        document.getElementById('summaryContent').innerHTML = `
            <h3><i class="fas fa-check-circle" style="color:var(--mt-success)"></i> Reconciliation Complete</h3>
            <div style="text-align:center;margin:20px 0">
                <div style="font-size:3em;font-weight:800;color:${data.accuracy_rate >= 90 ? 'var(--mt-success)' : data.accuracy_rate >= 70 ? 'var(--mt-warning)' : 'var(--mt-danger)'}">${data.accuracy_rate || 0}%</div>
                <div style="color:var(--mt-gray-600);margin-top:4px">Accuracy Rate</div>
            </div>
            <div style="display:grid;grid-template-columns:repeat(auto-fit,minmax(150px,1fr));gap:12px;text-align:center">
                <div><strong>${data.total_expected || 0}</strong><br><span style="font-size:0.82em;color:var(--mt-gray-600)">Expected</span></div>
                <div><strong>${data.total_scanned || 0}</strong><br><span style="font-size:0.82em;color:var(--mt-gray-600)">Scanned</span></div>
                <div style="color:var(--mt-success)"><strong>${data.matched || 0}</strong><br><span style="font-size:0.82em">Matched</span></div>
                <div style="color:var(--mt-danger)"><strong>${data.missing || 0}</strong><br><span style="font-size:0.82em">Missing</span></div>
                <div style="color:var(--mt-warning)"><strong>${data.extra || 0}</strong><br><span style="font-size:0.82em">Extra</span></div>
            </div>
            ${data.missing > 0 ? '<div style="margin-top:20px;padding:12px;background:var(--mt-danger-light);border-radius:8px;font-size:0.88em;color:var(--mt-danger-dark)"><i class="fas fa-exclamation-triangle"></i> ' + data.missing + ' items were not found during scanning. Please review.</div>' : ''}
        `;
        setStep(3);
    } catch(e) {
        alert('Error completing reconciliation');
    }
}

async function cancelRecon() {
    if (!confirm('Cancel this reconciliation?')) return;
    currentReconId = null;
    scannedItems = [];
    setStep(1);
}

document.addEventListener('DOMContentLoaded', () => {
    document.getElementById('barcodeInput')?.focus();
});
