const API = '/meritrack/api';
let dt = null;
let allHospitals = [];
let allRepUsers = [];        // MT Rep users loaded once on page load
let currentSpIds = [];       // selected salesperson user IDs for the open modal
let editingId = null;
let confirmAction = null;
let currentFilter = 'active';
let importFileObj = null;

function initTable() {
    dt = new DataTable('#hospitalsTable', {
        pageLength: 25,
        lengthMenu: [10, 25, 50, 100],
        order: [[0, 'asc']],
        columnDefs: [{ targets: 7, orderable: false, searchable: false }],
        language: { search: '', searchPlaceholder: 'Search hospitals…', emptyTable: 'No hospitals found.' },
    });
}

async function loadHospitals() {
    try {
        const resp = await fetch(`${API}/hospitals/?include_inactive=1&page_size=2000`, {credentials: 'same-origin'});
        const data = await resp.json();
        allHospitals = data.results || data || [];
        updateStats();
        applyFilter();
    } catch(e) {
        console.error(e);
        showToast('Failed to load hospitals', 'error');
    }
}

function updateStats() {
    const active = allHospitals.filter(h => h.is_active).length;
    document.getElementById('statTotal').textContent = allHospitals.length;
    document.getElementById('statActive').textContent = active;
    document.getElementById('statInactive').textContent = allHospitals.length - active;
}

function setFilter(f, el) {
    currentFilter = f;
    document.querySelectorAll('.filter-tab').forEach(t => t.classList.remove('active'));
    el.classList.add('active');
    applyFilter();
}

function applyFilter() {
    let rows = allHospitals;
    if (currentFilter === 'active') rows = allHospitals.filter(h => h.is_active);
    else if (currentFilter === 'inactive') rows = allHospitals.filter(h => !h.is_active);
    renderTable(rows);
}

function renderTable(hospitals) {
    if (!dt) return;
    dt.clear();
    hospitals.forEach(h => {
        const badge = h.is_active ? "<span class='badge badge-green'>Active</span>" : "<span class='badge badge-red'>Inactive</span>";
        const deptCount = (h.departments || []).length;
        const deptBtn = `<button class="btn-mt btn-sm" style="background:var(--mt-info-light);color:var(--mt-primary-dark);border:none;" onclick="openDeptModal(${h.id},'${escHtml(h.name)}')" title="Manage Departments"><i class="fas fa-layer-group"></i> ${deptCount}</button>`;
        const actions = h.is_active
            ? `<div style="display:flex;gap:4px;justify-content:center;flex-wrap:wrap;">
                   ${deptBtn}
                   <button class="btn-mt btn-primary btn-sm" onclick="openHospitalModal(${h.id})" title="Edit"><i class="fas fa-edit"></i></button>
                   <button class="btn-mt btn-danger btn-sm" onclick="confirmDeactivate(${h.id},'${escHtml(h.name)}')" title="Deactivate"><i class="fas fa-ban"></i></button>
               </div>`
            : `<div style="display:flex;gap:4px;justify-content:center;flex-wrap:wrap;">
                   ${deptBtn}
                   <button class="btn-mt btn-primary btn-sm" onclick="openHospitalModal(${h.id})" title="Edit"><i class="fas fa-edit"></i></button>
                   <button class="btn-mt btn-success btn-sm" onclick="confirmReactivate(${h.id},'${escHtml(h.name)}')" title="Reactivate"><i class="fas fa-redo"></i></button>
               </div>`;
        dt.row.add([
            `<strong style="color:var(--mt-primary);">${escHtml(h.name)}</strong>`,
            escHtml(h.sap_code || '—'),
            escHtml([h.country, h.province].filter(Boolean).join(' / ') || '—'),
            escHtml(h.city || '—'),
            escHtml(h.contact_person || '—'),
            (h.salespersons_detail && h.salespersons_detail.length)
                ? h.salespersons_detail.map((s, i) =>
                    `<span style="display:inline-block;background:${i===0?'var(--mt-info-light)':'var(--mt-success-light)'};color:${i===0?'var(--mt-primary-dark)':'var(--mt-success-dark)'};border-radius:12px;padding:2px 8px;font-size:0.78em;margin:1px;" title="${escHtml(s.email)}">${escHtml(s.name)}</span>`
                  ).join('')
                : '—',
            badge,
            actions,
        ]);
    });
    dt.draw();
}

function openHospitalModal(id = null) {
    editingId = id;
    document.getElementById('modalError').style.display = 'none';
    document.getElementById('fActiveGroup').style.display = id ? 'block' : 'none';
    if (id) {
        const h = allHospitals.find(x => x.id === id);
        if (!h) return;
        document.getElementById('modalTitle').innerHTML = `<i class="fas fa-edit" style="color:var(--mt-warning)"></i> Edit Hospital`;
        document.getElementById('fName').value = h.name || '';
        document.getElementById('fSapCode').value = h.sap_code || '';
        document.getElementById('fPlant').value = h.plant || '';
        document.getElementById('fCountry').value = h.country || '';
        document.getElementById('fProvince').value = h.province || '';
        document.getElementById('fCity').value = h.city || '';
        document.getElementById('fAddress').value = h.address || '';
        document.getElementById('fContact').value = h.contact_person || '';
        document.getElementById('fPhone').value = h.phone || '';
        document.getElementById('fEmail').value = h.email || '';
        document.getElementById('fActive').checked = h.is_active;
        // Populate salesperson chips from M2M detail
        currentSpIds = (h.salespersons_detail || []).map(s => s.id);
    } else {
        document.getElementById('modalTitle').innerHTML = `<i class="fas fa-plus" style="color:var(--mt-primary)"></i> Add Hospital`;
        ['fName','fSapCode','fPlant','fProvince','fCity','fAddress','fContact','fPhone','fEmail'].forEach(f => document.getElementById(f).value = '');
        document.getElementById('fCountry').value = '';
        currentSpIds = [];
    }
    populateRepSelect();
    renderSpChips();
    document.getElementById('hospitalModal').classList.add('active');
    document.body.style.overflow = 'hidden';
    setTimeout(() => document.getElementById('fName').focus(), 120);
}

function closeHospitalModal() {
    document.getElementById('hospitalModal').classList.remove('active');
    document.body.style.overflow = '';
}

async function saveHospital() {
    const name = document.getElementById('fName').value.trim();
    if (!name) { showModalError('Hospital name is required.'); return; }
    // Derive salesperson_email from first selected rep (primary)
    const primaryRep = currentSpIds.length > 0 ? allRepUsers.find(u => u.id === currentSpIds[0]) : null;
    const payload = {
        name,
        sap_code: document.getElementById('fSapCode').value.trim() || null,
        plant: document.getElementById('fPlant').value.trim() || null,
        country: document.getElementById('fCountry').value.trim() || null,
        province: document.getElementById('fProvince').value.trim() || null,
        city: document.getElementById('fCity').value.trim() || null,
        address: document.getElementById('fAddress').value.trim() || null,
        contact_person: document.getElementById('fContact').value.trim() || null,
        phone: document.getElementById('fPhone').value.trim() || null,
        email: document.getElementById('fEmail').value.trim() || null,
        salesperson_email: primaryRep ? primaryRep.email : null,
        salesperson_ids: currentSpIds,
    };
    if (editingId) payload.is_active = document.getElementById('fActive').checked;
    const btn = document.getElementById('modalSaveBtn');
    btn.disabled = true; btn.innerHTML = '<i class="fas fa-spinner fa-spin"></i> Saving…';
    try {
        const resp = await fetch(editingId ? `${API}/hospitals/${editingId}/` : `${API}/hospitals/`, {
            method: editingId ? 'PATCH' : 'POST', credentials: 'same-origin',
            headers: {'Content-Type':'application/json','X-CSRFToken':CSRF},
            body: JSON.stringify(payload),
        });
        const data = await resp.json();
        if (!resp.ok) { showModalError(Object.values(data).flat().join(' ') || `Error ${resp.status}`); return; }
        closeHospitalModal();
        showToast(editingId ? 'Hospital updated' : 'Hospital created', 'success');
        await loadHospitals();
    } catch(e) { showModalError('Network error. Please try again.'); }
    finally { btn.disabled = false; btn.innerHTML = '<i class="fas fa-check"></i> Save'; }
}

function showModalError(msg) {
    const el = document.getElementById('modalError');
    el.textContent = msg; el.style.display = 'block';
}

function confirmDeactivate(id, name) {
    document.getElementById('confirmTitle').innerHTML = `<i class="fas fa-ban" style="color:var(--mt-danger)"></i> Deactivate Hospital`;
    document.getElementById('confirmMsg').textContent = `Deactivate "${name}"? It will be hidden from inventory and orders but can be reactivated later.`;
    const btn = document.getElementById('confirmBtn');
    btn.className = 'btn-mt btn-danger'; btn.textContent = 'Deactivate';
    confirmAction = async () => {
        const resp = await fetch(`${API}/hospitals/${id}/deactivate/`, {method:'POST',credentials:'same-origin',headers:{'X-CSRFToken':CSRF}});
        if (resp.ok) { showToast(`"${name}" deactivated`, 'warning'); await loadHospitals(); }
        else showToast('Failed to deactivate', 'error');
    };
    document.getElementById('confirmModal').classList.add('active');
    document.body.style.overflow = 'hidden';
}

function confirmReactivate(id, name) {
    document.getElementById('confirmTitle').innerHTML = `<i class="fas fa-redo" style="color:var(--mt-success)"></i> Reactivate Hospital`;
    document.getElementById('confirmMsg').textContent = `Reactivate "${name}"? It will appear in inventory and orders again.`;
    const btn = document.getElementById('confirmBtn');
    btn.className = 'btn-mt btn-success'; btn.textContent = 'Reactivate';
    confirmAction = async () => {
        const resp = await fetch(`${API}/hospitals/${id}/reactivate/`, {method:'POST',credentials:'same-origin',headers:{'X-CSRFToken':CSRF}});
        if (resp.ok) { showToast(`"${name}" reactivated`, 'success'); await loadHospitals(); }
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

function openImportModal() {
    importFileObj = null;
    document.getElementById('importFile').value = '';
    document.getElementById('importFileName').textContent = '';
    document.getElementById('importResult').style.display = 'none';
    document.getElementById('importBtn').disabled = true;
    document.getElementById('importDropZone').classList.remove('dragover');
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
    const ext = file ? file.name.split('.').pop().toLowerCase() : '';
    if (file && ['csv','xlsx','xls'].includes(ext)) { importFileObj = file; document.getElementById('importFileName').textContent = file.name; document.getElementById('importBtn').disabled = false; }
    else showToast('Please drop a .csv, .xlsx or .xls file', 'error');
}

function onFileSelected(input) {
    const file = input.files[0];
    if (!file) return;
    importFileObj = file;
    document.getElementById('importFileName').textContent = file.name;
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
        const resp = await fetch(`${API}/hospitals/import-master/`, {
            method:'POST', credentials:'same-origin', headers:{'X-CSRFToken':CSRF}, body: fd,
        });
        const data = await resp.json();
        document.getElementById('importSummary').innerHTML =
            `<div class="result-item result-ok"><i class="fas fa-plus-circle"></i> Created: <strong>${data.created||0}</strong></div>
             <div class="result-item result-ok"><i class="fas fa-sync"></i> Updated: <strong>${data.updated||0}</strong></div>
             <div class="result-item result-err"><i class="fas fa-exclamation-triangle"></i> Errors: <strong>${(data.errors||[]).length}</strong></div>`;
        document.getElementById('importErrors').innerHTML = (data.errors||[]).map(e => `<div class="result-item result-err"><i class="fas fa-times"></i> ${escHtml(e)}</div>`).join('');
        document.getElementById('importResult').style.display = 'block';
        if (resp.ok) { showToast(`Import done: ${data.created||0} created, ${data.updated||0} updated`, 'success'); await loadHospitals(); }
        else showToast(data.error || 'Import failed', 'error');
    } catch(e) { showToast('Network error during import', 'error'); }
    finally { btn.disabled = false; btn.innerHTML = '<i class="fas fa-upload"></i> Import'; }
}

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
    if (e.key === 'Escape') { closeHospitalModal(); closeConfirmModal(); closeImportModal(); closeDeptModal(); }
});

document.addEventListener('DOMContentLoaded', () => {
    initTable();
    loadHospitals();
    loadRepUsers();
});

async function loadRepUsers() {
    try {
        const resp = await fetch(`${API}/hospitals/rep-users/`, {credentials: 'same-origin'});
        allRepUsers = await resp.json();
        populateRepSelect();
    } catch(e) { console.warn('Could not load rep users', e); }
}

function populateRepSelect() {
    const sel = document.getElementById('fSpSelect');
    if (!sel) return;
    sel.innerHTML = '<option value="">— Select to add salesperson —</option>';
    allRepUsers.forEach(u => {
        if (currentSpIds.includes(u.id)) return; // hide already-added reps
        const opt = document.createElement('option');
        opt.value = u.id;
        opt.textContent = `${u.name} (${u.email})`;
        sel.appendChild(opt);
    });
}

function renderSpChips() {
    const container = document.getElementById('spChips');
    if (!container) return;
    container.innerHTML = '';
    currentSpIds.forEach((uid, idx) => {
        const u = allRepUsers.find(r => r.id === uid);
        if (!u) return;
        const chip = document.createElement('span');
        chip.style.cssText = 'display:inline-flex;align-items:center;gap:5px;padding:4px 10px;background:var(--mt-info-light);color:var(--mt-primary-dark);border-radius:20px;font-size:0.83em;font-weight:500;';
        const primaryBadge = idx === 0 ? '<span style="font-size:0.75em;background:var(--mt-primary-dark);color:#fff;border-radius:10px;padding:1px 6px;margin-right:2px;">Primary</span>' : '';
        chip.innerHTML = `${primaryBadge}${escHtml(u.name)}<button type="button" onclick="removeSpChip(${uid})" style="background:none;border:none;cursor:pointer;color:var(--mt-primary-dark);font-size:1em;line-height:1;padding:0 2px;" title="Remove">&#x2715;</button>`;
        container.appendChild(chip);
    });
}

function addSpFromSelect() {
    const sel = document.getElementById('fSpSelect');
    const uid = parseInt(sel.value);
    if (!uid) { showToast('Please select a salesperson from the dropdown first.', 'warning'); return; }
    if (!currentSpIds.includes(uid)) {
        currentSpIds.push(uid);
        renderSpChips();
        populateRepSelect(); // refresh dropdown to hide just-added rep
    }
    sel.value = '';
}

function onSpSelectChange() {
    // Auto-add immediately on selection — no need to click "Add"
    addSpFromSelect();
}

function removeSpChip(uid) {
    currentSpIds = currentSpIds.filter(id => id !== uid);
    renderSpChips();
    populateRepSelect(); // restore removed rep to dropdown
}

// ─────────────────────────────────────────────────────────────────────────────
// DEPARTMENT MANAGEMENT
// ─────────────────────────────────────────────────────────────────────────────
let currentDeptHospitalId = null;
let editingDeptId = null;

async function openDeptModal(hospitalId, hospitalName) {
    currentDeptHospitalId = hospitalId;
    document.getElementById('deptModalTitle').innerHTML =
        `<i class="fas fa-layer-group" style="color:var(--mt-primary)"></i> Departments`;
    document.getElementById('deptHospName').textContent = hospitalName;
    showDeptList();
    document.getElementById('deptModal').classList.add('active');
    document.body.style.overflow = 'hidden';
    await loadDepts();
}

function closeDeptModal() {
    document.getElementById('deptModal').classList.remove('active');
    document.body.style.overflow = '';
    currentDeptHospitalId = null;
    editingDeptId = null;
}

async function loadDepts() {
    document.getElementById('deptTableBody').innerHTML =
        '<tr><td colspan="7" style="text-align:center;padding:24px;color:var(--mt-gray-400);"><i class="fas fa-spinner fa-spin"></i> Loading…</td></tr>';
    try {
        const resp = await fetch(`${API}/departments/by-hospital/${currentDeptHospitalId}/`, {credentials:'same-origin'});
        const data = await resp.json();
        renderDepts(Array.isArray(data) ? data : (data.results || []));
    } catch(e) {
        document.getElementById('deptTableBody').innerHTML =
            '<tr><td colspan="7" style="text-align:center;padding:24px;color:var(--mt-danger);">Failed to load departments</td></tr>';
    }
}

function renderDepts(depts) {
    const tbody = document.getElementById('deptTableBody');
    if (!depts.length) {
        tbody.innerHTML = '<tr><td colspan="7" style="text-align:center;padding:24px;color:var(--mt-gray-400);">No departments yet — click <strong>Add Department</strong> to create one.</td></tr>';
        return;
    }
    tbody.innerHTML = depts.map(d => {
        const badge = d.is_active
            ? "<span class='badge badge-green' style='font-size:0.78em;'>Active</span>"
            : "<span class='badge badge-red' style='font-size:0.78em;'>Inactive</span>";
        return `<tr style="border-bottom:1px solid var(--mt-gray-100);">
            <td style="padding:8px 10px;font-weight:500;">${escHtml(d.name)}</td>
            <td style="padding:8px 10px;font-family:monospace;color:var(--mt-gray-600);">${escHtml(d.code||'—')}</td>
            <td style="padding:8px 10px;">${escHtml(d.floor||'—')}</td>
            <td style="padding:8px 10px;">${escHtml(d.contact_person||'—')}</td>
            <td style="padding:8px 10px;text-align:center;">${d.product_count ?? 0}</td>
            <td style="padding:8px 10px;text-align:center;">${badge}</td>
            <td style="padding:8px 10px;text-align:center;">
                <div style="display:flex;gap:4px;justify-content:center;">
                    <button class="btn-mt btn-primary btn-sm" onclick="openDeptForm(${d.id})" title="Edit"><i class="fas fa-edit"></i></button>
                    <button class="btn-mt btn-danger btn-sm" onclick="confirmDeleteDept(${d.id},'${escHtml(d.name)}')" title="Delete"><i class="fas fa-trash"></i></button>
                </div>
            </td>
        </tr>`;
    }).join('');
}

function showDeptList() {
    document.getElementById('deptListSection').style.display = 'block';
    document.getElementById('deptFormSection').style.display = 'none';
    editingDeptId = null;
}

function openDeptForm(id = null) {
    editingDeptId = id;
    document.getElementById('deptFormError').style.display = 'none';
    document.getElementById('dfActiveGroup').style.display = id ? 'block' : 'none';

    if (id) {
        // fetch existing dept data from the rendered table rows instead of extra API call
        const rows = document.querySelectorAll('#deptTableBody tr');
        // We'll do a direct API fetch for accuracy
        fetch(`${API}/departments/${id}/`, {credentials:'same-origin'})
            .then(r => r.json())
            .then(d => {
                document.getElementById('deptFormTitle').innerHTML =
                    `<i class="fas fa-edit" style="color:var(--mt-warning)"></i> Edit Department`;
                document.getElementById('dfName').value = d.name || '';
                document.getElementById('dfCode').value = d.code || '';
                document.getElementById('dfFloor').value = d.floor || '';
                document.getElementById('dfContact').value = d.contact_person || '';
                document.getElementById('dfPhone').value = d.phone || '';
                document.getElementById('dfActive').checked = d.is_active;
            });
    } else {
        document.getElementById('deptFormTitle').innerHTML =
            `<i class="fas fa-plus-circle" style="color:var(--mt-primary)"></i> Add Department`;
        ['dfName','dfCode','dfFloor','dfContact','dfPhone'].forEach(f => document.getElementById(f).value = '');
        document.getElementById('dfActive').checked = true;
    }

    document.getElementById('deptListSection').style.display = 'none';
    document.getElementById('deptFormSection').style.display = 'block';
    setTimeout(() => document.getElementById('dfName').focus(), 80);
}

async function saveDept() {
    const name = document.getElementById('dfName').value.trim();
    if (!name) {
        const el = document.getElementById('deptFormError');
        el.textContent = 'Department name is required.'; el.style.display = 'block'; return;
    }
    const payload = {
        hospital: currentDeptHospitalId,
        name,
        code: document.getElementById('dfCode').value.trim() || null,
        floor: document.getElementById('dfFloor').value.trim() || null,
        contact_person: document.getElementById('dfContact').value.trim() || null,
        phone: document.getElementById('dfPhone').value.trim() || null,
    };
    if (editingDeptId) payload.is_active = document.getElementById('dfActive').checked;

    const btn = document.getElementById('deptSaveBtn');
    btn.disabled = true; btn.innerHTML = '<i class="fas fa-spinner fa-spin"></i> Saving…';
    try {
        const resp = await fetch(
            editingDeptId ? `${API}/departments/${editingDeptId}/` : `${API}/departments/`,
            {method: editingDeptId ? 'PATCH' : 'POST', credentials:'same-origin',
             headers:{'Content-Type':'application/json','X-CSRFToken':CSRF}, body:JSON.stringify(payload)}
        );
        const data = await resp.json();
        if (!resp.ok) {
            const el = document.getElementById('deptFormError');
            el.textContent = Object.values(data).flat().join(' ') || `Error ${resp.status}`;
            el.style.display = 'block'; return;
        }
        showToast(editingDeptId ? 'Department updated' : 'Department created', 'success');
        showDeptList();
        await loadDepts();
        await loadHospitals(); // refresh dept count badges in table
    } catch(e) {
        const el = document.getElementById('deptFormError');
        el.textContent = 'Network error. Please try again.'; el.style.display = 'block';
    } finally {
        btn.disabled = false; btn.innerHTML = '<i class="fas fa-check"></i> Save Department';
    }
}

function confirmDeleteDept(id, name) {
    if (!confirm(`Delete department "${name}"? This cannot be undone.`)) return;
    deleteDept(id, name);
}

async function deleteDept(id, name) {
    try {
        const resp = await fetch(`${API}/departments/${id}/`, {
            method:'DELETE', credentials:'same-origin', headers:{'X-CSRFToken':CSRF}
        });
        if (resp.ok || resp.status === 204) {
            showToast(`Department "${name}" deleted`, 'warning');
            await loadDepts();
            await loadHospitals();
        } else {
            const data = await resp.json().catch(() => ({}));
            showToast(data.detail || 'Failed to delete department', 'error');
        }
    } catch(e) { showToast('Network error', 'error'); }
}
