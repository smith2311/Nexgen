const API = '/meritrack/api';

let dt = null;
let allProducts = [];
let editingId = null;
let detailId = null;
let confirmAction = null;
let catFilterVal = '';
let importFileObj = null;

const CAT_LABELS = {
    stent:'Coronary Stent', balloon:'Balloon Catheter', guidewire:'Guidewire',
    valve:'Heart Valve', graft:'Vascular Graft', closure:'Closure Device',
    accessory:'Accessory', other:'Other'
};

// ─── Init DataTable ──────────────────────────────────────────────────────
function initTable() {
    dt = $('#catalogTable').DataTable({
        scrollX: false,
        autoWidth: false,
        pageLength: 25,
        lengthMenu: [10, 25, 50, 100, 200],
        order: [[0, 'asc']],
        columnDefs: [{ targets: 11, orderable: false, searchable: false }],
        language: { search: '', searchPlaceholder: 'Search products…', emptyTable: 'No products found.' },
    });
}

// ─── Load All Products (paginated) ───────────────────────────────────────
async function loadCatalog() {
    try {
        let url = `${API}/catalog/?page_size=2000&include_inactive=1`;
        const resp = await fetch(url, { credentials: 'same-origin' });
        if (!resp.ok) { showToast(`API error ${resp.status}`, 'error'); return; }
        const data = await resp.json();
        // API has pagination_class=None so returns a plain array; guard for both formats
        allProducts = Array.isArray(data) ? data : (data.results || []);
        updateStats();
        applyFilter();
    } catch(e) {
        console.error(e);
        showToast('Failed to load catalog', 'error');
    }
}

function updateStats() {
    const active = allProducts.filter(p => p.is_active).length;
    const cats = new Set(allProducts.map(p => p.category)).size;
    const cold = allProducts.filter(p => p.requires_cold_chain).length;
    document.getElementById('statTotal').textContent = allProducts.length;
    document.getElementById('statActive').textContent = active;
    document.getElementById('statCategories').textContent = cats;
    document.getElementById('statColdChain').textContent = cold;
}

// ─── Category filter tabs ─────────────────────────────────────────────────
function setCatFilter(cat, el) {
    catFilterVal = cat;
    document.querySelectorAll('.filter-tab').forEach(t => t.classList.remove('active'));
    el.classList.add('active');
    applyFilter();
}

function applyFilter() {
    let rows = allProducts;
    if (catFilterVal) rows = allProducts.filter(p => p.category === catFilterVal);
    renderTable(rows);
}

// ─── Render Table ─────────────────────────────────────────────────────────
function renderTable(products) {
    if (!dt) return;
    dt.clear();
    products.forEach(p => {
        const catCls = `cat cat-${p.category || 'other'}`;
        const catLabel = CAT_LABELS[p.category] || p.category || '—';
        const statusBadge = p.is_active
            ? "<span class='badge badge-green'>Active</span>"
            : "<span class='badge badge-red'>Inactive</span>";
        const coldBadge = p.requires_cold_chain
            ? '<i class="fas fa-snowflake" style="color:var(--mt-secondary)" title="Cold chain required"></i>'
            : '<span style="color:var(--mt-gray-300);">—</span>';
        const actions = `<div style="display:flex;gap:3px;justify-content:center;">
            <button class="btn-mt btn-info btn-sm" onclick="viewDetail(${p.id})" title="View Details"><i class="fas fa-eye"></i></button>
            <button class="btn-mt btn-primary btn-sm" onclick="openProductModal(${p.id})" title="Edit"><i class="fas fa-edit"></i></button>
            ${p.is_active
                ? `<button class="btn-mt btn-danger btn-sm" onclick="confirmDeactivate(${p.id},'${escHtml(p.name)}')" title="Deactivate"><i class="fas fa-ban"></i></button>`
                : `<button class="btn-mt btn-success btn-sm" onclick="confirmReactivate(${p.id},'${escHtml(p.name)}')" title="Reactivate"><i class="fas fa-redo"></i></button>`
            }
        </div>`;
        dt.row.add([
            `<span style="font-family:monospace;font-size:0.85em;font-weight:700;color:var(--mt-gray-700);">${escHtml(p.material_code)}</span>`,
            `<strong>${escHtml(p.name)}</strong>${p.description ? `<br><small style="color:var(--mt-gray-400);font-size:0.82em;">${escHtml(p.description.substring(0,60))}${p.description.length > 60 ? '…' : ''}</small>` : ''}`,
            escHtml(p.brand || '—'),
            `<span class="${catCls}">${catLabel}</span>`,
            escHtml(p.size || '—'),
            `<span style="font-family:monospace;font-size:0.8em;">${escHtml(p.gtin_code || '—')}</span>`,
            p.unit_price ? `€${parseFloat(p.unit_price).toFixed(2)}` : '—',
            p.shelf_life_months ? `${p.shelf_life_months} mo` : '—',
            escHtml(p.material_group_description || p.material_group || '—'),
            coldBadge,
            statusBadge,
            actions,
        ]);
    });
    dt.draw();
}

// ─── View Detail ──────────────────────────────────────────────────────────
function viewDetail(id) {
    const p = allProducts.find(x => x.id === id);
    if (!p) return;
    detailId = id;
    const catLabel = CAT_LABELS[p.category] || p.category || '—';
    document.getElementById('detailTitle').innerHTML =
        `<i class="fas fa-box" style="color:var(--mt-primary)"></i> ${escHtml(p.name)}`;

    document.getElementById('detailBody').innerHTML = `
        <div class="detail-section">
            <div class="detail-section-title">Core Details</div>
            <div class="detail-grid">
                <div class="detail-cell"><label>Material Code</label><span style="font-family:monospace;font-weight:700">${escHtml(p.material_code)}</span></div>
                <div class="detail-cell"><label>GTIN</label><span style="font-family:monospace">${escHtml(p.gtin_code || '—')}</span></div>
                <div class="detail-cell"><label>Brand</label><span>${escHtml(p.brand || '—')}</span></div>
                <div class="detail-cell"><label>Category</label><span><span class="cat cat-${p.category}">${catLabel}</span></span></div>
                <div class="detail-cell"><label>Size</label><span>${escHtml(p.size || '—')}</span></div>
                <div class="detail-cell"><label>Base UOM</label><span>${escHtml(p.base_uom || '—')}</span></div>
                <div class="detail-cell"><label>Unit Price</label><span>${p.unit_price ? '€' + parseFloat(p.unit_price).toFixed(2) : '—'}</span></div>
                <div class="detail-cell"><label>Shelf Life</label><span>${p.shelf_life_months ? p.shelf_life_months + ' months' : '—'}</span></div>
                <div class="detail-cell"><label>Cold Chain</label><span>${p.requires_cold_chain ? '<i class="fas fa-snowflake" style="color:var(--mt-secondary)"></i> Required' : 'Not required'}</span></div>
                <div class="detail-cell"><label>Status</label><span>${p.is_active ? "<span class='badge badge-green'>Active</span>" : "<span class='badge badge-red'>Inactive</span>"}</span></div>
                <div class="detail-cell"><label>Created</label><span>${p.created_at ? new Date(p.created_at).toLocaleDateString('en-GB',{day:'2-digit',month:'short',year:'numeric'}) : '—'}</span></div>
                <div class="detail-cell"><label>Last Updated</label><span>${p.updated_at ? new Date(p.updated_at).toLocaleDateString('en-GB',{day:'2-digit',month:'short',year:'numeric'}) : '—'}</span></div>
            </div>
        </div>
        ${p.description ? `<div class="detail-section"><div class="detail-section-title">Description</div><div class="detail-desc">${escHtml(p.description)}</div></div>` : ''}
        <div class="detail-section">
            <div class="detail-section-title">SAP Classification</div>
            <div class="detail-grid">
                <div class="detail-cell"><label>Material Type</label><span style="font-family:monospace">${escHtml(p.material_type || '—')}</span></div>
                <div class="detail-cell"><label>Type Description</label><span>${escHtml(p.material_type_description || '—')}</span></div>
                <div class="detail-cell" style="grid-column:1/-1;display:none"></div>
                <div class="detail-cell"><label>Material Group</label><span style="font-family:monospace">${escHtml(p.material_group || '—')}</span></div>
                <div class="detail-cell"><label>Group Description</label><span>${escHtml(p.material_group_description || '—')}</span></div>
            </div>
        </div>
    `;
    document.getElementById('detailModal').classList.add('active');
    document.body.style.overflow = 'hidden';
}

function editFromDetail() {
    closeDetailModal();
    openProductModal(detailId);
}

function closeDetailModal() {
    document.getElementById('detailModal').classList.remove('active');
    document.body.style.overflow = '';
}

// ─── Add / Edit Modal ─────────────────────────────────────────────────────
function openProductModal(id = null) {
    editingId = id;
    document.getElementById('productModalError').style.display = 'none';
    document.getElementById('fActiveGroup').style.display = id ? 'block' : 'none';

    if (id) {
        const p = allProducts.find(x => x.id === id);
        if (!p) return;
        document.getElementById('productModalTitle').innerHTML = `<i class="fas fa-edit" style="color:var(--mt-warning)"></i> Edit Product`;
        document.getElementById('fCode').value = p.material_code || '';
        document.getElementById('fGtin').value = p.gtin_code || '';
        document.getElementById('fName').value = p.name || '';
        document.getElementById('fBrand').value = p.brand || '';
        document.getElementById('fCategory').value = p.category || 'other';
        document.getElementById('fSize').value = p.size || '';
        document.getElementById('fUom').value = p.base_uom || 'EA';
        document.getElementById('fDesc').value = p.description || '';
        document.getElementById('fPrice').value = p.unit_price || '';
        document.getElementById('fShelf').value = p.shelf_life_months || 36;
        document.getElementById('fColdChain').checked = !!p.requires_cold_chain;
        document.getElementById('fMatType').value = p.material_type || '';
        document.getElementById('fMatTypeDesc').value = p.material_type_description || '';
        document.getElementById('fMatGroup').value = p.material_group || '';
        document.getElementById('fMatGroupDesc').value = p.material_group_description || '';
        document.getElementById('fActive').checked = !!p.is_active;
    } else {
        document.getElementById('productModalTitle').innerHTML = `<i class="fas fa-plus" style="color:var(--mt-primary)"></i> Add Product`;
        ['fCode','fGtin','fName','fBrand','fSize','fDesc','fPrice','fMatType','fMatTypeDesc','fMatGroup','fMatGroupDesc'].forEach(f => document.getElementById(f).value = '');
        document.getElementById('fCategory').value = 'other';
        document.getElementById('fUom').value = 'EA';
        document.getElementById('fShelf').value = 36;
        document.getElementById('fColdChain').checked = false;
    }

    document.getElementById('productModal').classList.add('active');
    document.body.style.overflow = 'hidden';
    setTimeout(() => document.getElementById('fCode').focus(), 120);
}

function closeProductModal() {
    document.getElementById('productModal').classList.remove('active');
    document.body.style.overflow = '';
}

async function saveProduct() {
    const code = document.getElementById('fCode').value.trim();
    const name = document.getElementById('fName').value.trim();
    if (!code) { showProductError('Material code is required.'); return; }
    if (!name) { showProductError('Product name is required.'); return; }

    const priceVal = document.getElementById('fPrice').value.trim();
    const payload = {
        material_code: code,
        gtin_code: document.getElementById('fGtin').value.trim() || null,
        name,
        brand: document.getElementById('fBrand').value.trim() || null,
        category: document.getElementById('fCategory').value,
        size: document.getElementById('fSize').value.trim() || null,
        base_uom: document.getElementById('fUom').value.trim() || 'EA',
        description: document.getElementById('fDesc').value.trim() || null,
        unit_price: priceVal ? parseFloat(priceVal) : null,
        shelf_life_months: parseInt(document.getElementById('fShelf').value) || 36,
        requires_cold_chain: document.getElementById('fColdChain').checked,
        material_type: document.getElementById('fMatType').value.trim() || null,
        material_type_description: document.getElementById('fMatTypeDesc').value.trim() || null,
        material_group: document.getElementById('fMatGroup').value.trim() || null,
        material_group_description: document.getElementById('fMatGroupDesc').value.trim() || null,
    };
    if (editingId) payload.is_active = document.getElementById('fActive').checked;

    const btn = document.getElementById('productSaveBtn');
    btn.disabled = true; btn.innerHTML = '<i class="fas fa-spinner fa-spin"></i> Saving…';
    try {
        const resp = await fetch(editingId ? `${API}/catalog/${editingId}/` : `${API}/catalog/`, {
            method: editingId ? 'PATCH' : 'POST', credentials: 'same-origin',
            headers: {'Content-Type':'application/json','X-CSRFToken':CSRF},
            body: JSON.stringify(payload),
        });
        const data = await resp.json();
        if (!resp.ok) {
            showProductError(Object.entries(data).map(([k,v]) => `${k}: ${[].concat(v).join(', ')}`).join(' | ') || `Error ${resp.status}`);
            return;
        }
        closeProductModal();
        showToast(editingId ? 'Product updated' : 'Product created', 'success');
        await loadCatalog();
    } catch(e) { showProductError('Network error. Try again.'); }
    finally { btn.disabled = false; btn.innerHTML = '<i class="fas fa-check"></i> Save'; }
}

function showProductError(msg) {
    const el = document.getElementById('productModalError');
    el.textContent = msg; el.style.display = 'block';
}

// ─── Deactivate / Reactivate ─────────────────────────────────────────────
function confirmDeactivate(id, name) {
    document.getElementById('confirmTitle').innerHTML = `<i class="fas fa-ban" style="color:var(--mt-danger)"></i> Deactivate Product`;
    document.getElementById('confirmMsg').textContent = `Deactivate "${name}"? It will be hidden from orders and scanning but can be reactivated.`;
    const btn = document.getElementById('confirmBtn');
    btn.className = 'btn-mt btn-danger'; btn.textContent = 'Deactivate';
    confirmAction = async () => {
        const resp = await fetch(`${API}/catalog/${id}/`, {
            method:'PATCH', credentials:'same-origin',
            headers:{'Content-Type':'application/json','X-CSRFToken':CSRF},
            body: JSON.stringify({is_active: false}),
        });
        if (resp.ok) { showToast(`"${name}" deactivated`, 'warning'); await loadCatalog(); }
        else showToast('Failed to deactivate', 'error');
    };
    document.getElementById('confirmModal').classList.add('active');
    document.body.style.overflow = 'hidden';
}

function confirmReactivate(id, name) {
    document.getElementById('confirmTitle').innerHTML = `<i class="fas fa-redo" style="color:var(--mt-success)"></i> Reactivate Product`;
    document.getElementById('confirmMsg').textContent = `Reactivate "${name}"?`;
    const btn = document.getElementById('confirmBtn');
    btn.className = 'btn-mt btn-success'; btn.textContent = 'Reactivate';
    confirmAction = async () => {
        const resp = await fetch(`${API}/catalog/${id}/`, {
            method:'PATCH', credentials:'same-origin',
            headers:{'Content-Type':'application/json','X-CSRFToken':CSRF},
            body: JSON.stringify({is_active: true}),
        });
        if (resp.ok) { showToast(`"${name}" reactivated`, 'success'); await loadCatalog(); }
        else showToast('Failed to reactivate', 'error');
    };
    document.getElementById('confirmModal').classList.add('active');
    document.body.style.overflow = 'hidden';
}

async function runConfirmAction() {
    closeConfirmModal();
    if (confirmAction) await confirmAction();
    confirmAction = null;
}

function closeConfirmModal() {
    document.getElementById('confirmModal').classList.remove('active');
    document.body.style.overflow = '';
}

// ─── Import ───────────────────────────────────────────────────────────────
function openImportModal() {
    importFileObj = null;
    document.getElementById('importFile').value = '';
    document.getElementById('importFileName').textContent = '';
    document.getElementById('importResult').style.display = 'none';
    document.getElementById('importBtn').disabled = true;
    document.getElementById('importModal').classList.add('active');
    document.body.style.overflow = 'hidden';
}

function closeImportModal() {
    document.getElementById('importModal').classList.remove('active');
    document.body.style.overflow = '';
}

function handleDragOver(e) { e.preventDefault(); document.getElementById('importDropZone').classList.add('dragover'); }
function handleDragLeave() { document.getElementById('importDropZone').classList.remove('dragover'); }
function handleDrop(e) {
    e.preventDefault();
    document.getElementById('importDropZone').classList.remove('dragover');
    const file = e.dataTransfer.files[0];
    const ext = file.name.split('.').pop().toLowerCase();
    if (file && ['csv','xlsx','xls'].includes(ext)) { importFileObj = file; document.getElementById('importFileName').textContent = file.name; document.getElementById('importBtn').disabled = false; }
    else showToast('Please drop a .csv, .xlsx or .xls file', 'error');
}
function onFileSelected(input) {
    importFileObj = input.files[0];
    if (!importFileObj) return;
    document.getElementById('importFileName').textContent = importFileObj.name;
    document.getElementById('importResult').style.display = 'none';
    document.getElementById('importBtn').disabled = false;
}

async function runImport() {
    if (!importFileObj) return;
    const btn = document.getElementById('importBtn');
    btn.disabled = true; btn.innerHTML = '<i class="fas fa-spinner fa-spin"></i> Importing…';
    const fd = new FormData();
    fd.append('file', importFileObj);
    try {
        const resp = await fetch(`${API}/catalog/import-catalog/`, {
            method:'POST', credentials:'same-origin', headers:{'X-CSRFToken':CSRF}, body:fd,
        });
        const data = await resp.json();
        document.getElementById('importSummary').innerHTML =
            `<div class="result-item result-ok"><i class="fas fa-plus-circle"></i> Created: <strong>${data.created||0}</strong></div>
             <div class="result-item result-ok"><i class="fas fa-sync"></i> Updated: <strong>${data.updated||0}</strong></div>
             <div class="result-item result-err"><i class="fas fa-exclamation-triangle"></i> Errors: <strong>${(data.errors||[]).length}</strong></div>`;
        document.getElementById('importErrors').innerHTML = (data.errors||[]).map(e => `<div class="result-item result-err"><i class="fas fa-times"></i> ${escHtml(e)}</div>`).join('');
        document.getElementById('importResult').style.display = 'block';
        if (resp.ok) { showToast(`Done: ${data.created||0} created, ${data.updated||0} updated`, 'success'); await loadCatalog(); }
        else showToast(data.error || 'Import failed', 'error');
    } catch(e) { showToast('Network error during import', 'error'); }
    finally { btn.disabled = false; btn.innerHTML = '<i class="fas fa-upload"></i> Import'; }
}

// ─── Export CSV ───────────────────────────────────────────────────────────
function exportCSV() {
    const src = catFilterVal ? allProducts.filter(p => p.category === catFilterVal) : allProducts;
    const headers = ['material_code','gtin_code','name','brand','category','size','base_uom','description','unit_price','shelf_life_months','requires_cold_chain','material_type','material_type_description','material_group','material_group_description','is_active'];
    const rows = [headers, ...src.map(p => headers.map(h => {
        const v = p[h];
        if (v == null) return '';
        if (typeof v === 'boolean') return v ? '1' : '0';
        const s = String(v);
        return s.includes(',') || s.includes('"') || s.includes('\n') ? `"${s.replace(/"/g,'""')}"` : s;
    }))];
    const csv = rows.map(r => r.join(',')).join('\n');
    const blob = new Blob([csv], {type:'text/csv'});
    const a = document.createElement('a');
    a.href = URL.createObjectURL(blob);
    a.download = `meritrack_catalog${catFilterVal ? '_' + catFilterVal : ''}.csv`;
    a.click();
}

// ─── Helpers ──────────────────────────────────────────────────────────────
function escHtml(s) {
    if (!s) return '';
    return String(s).replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/"/g,'&quot;');
}

let toastTimer;
function showToast(msg, type='success') {
    const t = document.getElementById('toast');
    t.className = `toast toast-${type}`;
    t.innerHTML = `<i class="fas fa-${type==='success'?'check-circle':type==='error'?'times-circle':'exclamation-triangle'}"></i> ${msg}`;
    clearTimeout(toastTimer);
    toastTimer = setTimeout(() => t.classList.add('hidden'), 3500);
}

document.addEventListener('keydown', e => {
    if (e.key === 'Escape') { closeDetailModal(); closeProductModal(); closeConfirmModal(); closeImportModal(); }
});

// ─── Boot ─────────────────────────────────────────────────────────────────
document.addEventListener('DOMContentLoaded', () => {
    initTable();
    loadCatalog();
});
