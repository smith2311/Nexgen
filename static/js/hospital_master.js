function getCookie(name) {
    let v = null;
    if (document.cookie) {
        for (const c of document.cookie.split(';')) {
            const t = c.trim();
            if (t.startsWith(name + '=')) { v = decodeURIComponent(t.slice(name.length + 1)); break; }
        }
    }
    return v;
}

const CSRF = getCookie('csrftoken');
const API       = '/product_scanning/api/hospitals/';
const IMP_URL   = '/product_scanning/api/hospitals/import-master/';
const _pcfg     = document.getElementById('page-config');
const canViewOnly = _pcfg ? JSON.parse(_pcfg.textContent).canViewOnly : false;

let dt           = null;
let deleteTarget = null;
let allHospitals = [];

// ── Boot ──────────────────────────────────────────────────────────────────────
$(function () {
    loadAndInit();
    setupUploadZone();
});

// ── Load data → init DataTable ────────────────────────────────────────────────
function loadAndInit() {
    fetch(API + 'list_hospitals/', { credentials: 'same-origin' })
        .then(function(r) { if (!r.ok) throw new Error(r.status); return r.json(); })
        .then(function(data) {
            allHospitals = data;
            updateStats(data);
            var rows = buildRows(data);
            if (dt) { dt.clear().rows.add(rows).draw(); return; }
            dt = $('#hospTable').DataTable({
                data: rows,
                columns: [
                    { data: '_idx',              title: '#',                 orderable: false },
                    { data: 'name',              title: 'Hospital Name',     render: function(d){ return '<strong>'+esc(d)+'</strong>'; } },
                    { data: 'sap_code',          title: 'SAP Code',          render: function(d){ return d ? esc(d) : '<span style="color:#ccc">—</span>'; } },
                    { data: 'province',          title: 'Province',          render: function(d){ return d ? esc(d) : '<span style="color:#ccc">—</span>'; } },
                    { data: 'location',          title: 'Location',          render: function(d){ return d ? esc(d) : '<span style="color:#ccc">—</span>'; } },
                    { data: 'contact_person',    title: 'Contact Person',    render: function(d){ return d ? esc(d) : '<span style="color:#ccc">—</span>'; } },
                    { data: 'phone',             title: 'Phone',             render: function(d){ return d ? esc(d) : '<span style="color:#ccc">—</span>'; } },
                    { data: 'salesperson_email', title: 'Salesperson Email',  render: function(d){ return d ? esc(d) : '<span style="color:#ccc">—</span>'; } },
                    {
                        data: null, defaultContent: '',
                        orderable: false, searchable: false,
                        visible: !canViewOnly,
                        render: function() { return ''; },
                        createdCell: function(td, cellData, rowData) {
                            if (canViewOnly) return;
                            td.innerHTML =
                                '<div class="dt-actions">' +
                                '<button type="button" class="btn btn-outline btn-sm" title="Edit">' +
                                  '<svg width="12" height="12" fill="none" stroke="currentColor" stroke-width="2" viewBox="0 0 24 24"><path d="M11 4H4a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h14a2 2 0 0 0 2-2v-7"/><path d="M18.5 2.5a2.121 2.121 0 0 1 3 3L12 15l-4 1 1-4z"/></svg>' +
                                '</button>' +
                                '<button type="button" class="btn btn-danger btn-sm" title="Delete">' +
                                  '<svg width="12" height="12" fill="none" stroke="currentColor" stroke-width="2" viewBox="0 0 24 24"><polyline points="3 6 5 6 21 6"/><path d="M19 6l-1 14a2 2 0 0 1-2 2H8a2 2 0 0 1-2-2L5 6"/></svg>' +
                                '</button>' +
                                '</div>';
                            var hid = rowData.id, hname = rowData.name;
                            td.querySelectorAll('button')[0].addEventListener('click', function() { openEditModal(hid); });
                            td.querySelectorAll('button')[1].addEventListener('click', function() { openDeleteModal(hid, hname); });
                        }
                    },
                ],
                pageLength: 25,
                lengthMenu: [[10, 25, 50, 100, -1], [10, 25, 50, 100, 'All']],
                order: [[1, 'asc']],
                language: { search: 'Search:', emptyTable: 'No hospitals found.' },
            });
        })
        .catch(function() { showToast('Failed to load hospitals.', 'err'); });
}

function buildRows(data) {
    return data.map(function(h, i) {
        return {
            _idx:              i + 1,
            id:                h.id,
            name:              h.name              || '',
            sap_code:          h.sap_code          || '',
            province:          h.province          || '',
            location:          h.location          || '',
            contact_person:    h.contact_person    || '',
            phone:             h.phone             || '',
            salesperson_email: h.salesperson_email || '',
        };
    });
}

function esc(s) { return String(s ?? '').replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/"/g,'&quot;'); }

function updateStats(list) {
    const withSap = list.filter(h => h.sap_code).length;
    $('#statTotal').text(list.length);
    $('#statWith').text(withSap);
    $('#statWithout').text(list.length - withSap);
}

// ── Add Modal ─────────────────────────────────────────────────────────────────
function openAddModal() {
    clearForm();
    document.getElementById('editId').value = '';
    document.getElementById('modalTitle').textContent = 'Add Hospital';
    document.getElementById('saveBtn').textContent = 'Save Hospital';
    openModal('editModal');
    document.getElementById('fName').focus();
}

// ── Edit Modal ────────────────────────────────────────────────────────────────
function openEditModal(id) {
    const h = allHospitals.find(x => x.id === id);
    if (!h) { showToast('Hospital not found — please refresh.', 'err'); return; }
    clearForm();
    document.getElementById('editId').value     = id;
    document.getElementById('fName').value      = h.name || '';
    document.getElementById('fSap').value       = h.sap_code || '';
    document.getElementById('fProvince').value  = h.province || '';
    document.getElementById('fLocation').value  = h.location || '';
    document.getElementById('fContact').value   = h.contact_person || '';
    document.getElementById('fPhone').value     = h.phone || '';
    document.getElementById('fEmail').value     = h.email || '';
    document.getElementById('fSaleEmail').value = h.salesperson_email || '';
    document.getElementById('modalTitle').textContent = 'Edit Hospital';
    document.getElementById('saveBtn').textContent    = 'Update Hospital';
    openModal('editModal');
}

// ── Save (Add / Update) ───────────────────────────────────────────────────────
function saveHospital() {
    hideAlert();
    if (!validate()) return;
    const id = document.getElementById('editId').value;
    const body = {
        name:              document.getElementById('fName').value.trim(),
        sap_code:          document.getElementById('fSap').value.trim(),
        province:          document.getElementById('fProvince').value.trim() || null,
        location:          document.getElementById('fLocation').value.trim() || null,
        contact_person:    document.getElementById('fContact').value.trim() || null,
        phone:             document.getElementById('fPhone').value.trim() || null,
        email:             document.getElementById('fEmail').value.trim() || null,
        salesperson_email: document.getElementById('fSaleEmail').value.trim() || null,
    };
    const btn  = document.getElementById('saveBtn');
    const orig = btn.textContent;
    btn.disabled = true;
    btn.innerHTML = '<span class="spinner"></span> Saving…';
    $.ajax({
        url:    id ? API + id + '/' : API,
        method: id ? 'PATCH' : 'POST',
        contentType: 'application/json',
        data: JSON.stringify(body),
        headers: { 'X-CSRFToken': CSRF },
        xhrFields: { withCredentials: true },
        success: function () {
            closeModal('editModal');
            showToast(id ? 'Hospital updated.' : 'Hospital created.', 'ok');
            loadAndInit();
        },
        error: function (xhr) {
            const d = xhr.responseJSON || {};
            showAlert(d.detail || d.error || 'Save failed. Please try again.');
        },
        complete: function () { btn.disabled = false; btn.textContent = orig; },
    });
}

// ── Delete ────────────────────────────────────────────────────────────────────
function openDeleteModal(id, name) {
    deleteTarget = id;
    document.getElementById('delLabel').textContent = name;
    openModal('deleteModal');
}

function confirmDelete() {
    if (!deleteTarget) return;
    const btn  = document.getElementById('confirmDelBtn');
    const orig = btn.innerHTML;
    btn.disabled = true;
    btn.innerHTML = '<span class="spinner"></span> Deleting…';
    $.ajax({
        url:    API + deleteTarget + '/',
        method: 'DELETE',
        headers: { 'X-CSRFToken': CSRF },
        xhrFields: { withCredentials: true },
        success: function () {
            closeModal('deleteModal');
            showToast('Hospital deleted.', 'ok');
            loadAndInit();
        },
        error: function () { showToast('Delete failed.', 'err'); },
        complete: function () { btn.disabled = false; btn.innerHTML = orig; deleteTarget = null; },
    });
}

// ── Form helpers ──────────────────────────────────────────────────────────────
function validate() {
    let ok = true;
    function chk(fId, eId, cond) {
        document.getElementById(fId).classList.toggle('err', cond);
        document.getElementById(eId).classList.toggle('show', cond);
        if (cond) ok = false;
    }
    chk('fName', 'fNameErr', !document.getElementById('fName').value.trim());
    chk('fSap',  'fSapErr',  !document.getElementById('fSap').value.trim());
    return ok;
}

function clearForm() {
    ['fName','fSap','fProvince','fLocation','fContact','fPhone','fEmail','fSaleEmail'].forEach(function(id) {
        var el = document.getElementById(id); el.value = ''; el.classList.remove('err');
    });
    ['fNameErr','fSapErr'].forEach(function(id) { document.getElementById(id).classList.remove('show'); });
    hideAlert();
}

function showAlert(msg) { var el = document.getElementById('modalAlert'); el.textContent = msg; el.style.display = 'block'; }
function hideAlert()    { var el = document.getElementById('modalAlert'); el.textContent = ''; el.style.display = 'none'; }

// ── Modal helpers ─────────────────────────────────────────────────────────────
function openModal(id)  { document.getElementById(id).classList.add('open'); }
function closeModal(id) { document.getElementById(id).classList.remove('open'); }

// ── Toast ─────────────────────────────────────────────────────────────────────
var toastTimer;
function showToast(msg, type) {
    var t = document.getElementById('toast');
    t.textContent = msg;
    t.className = 'show' + (type === 'err' ? ' t-err' : type === 'ok' ? ' t-ok' : '');
    clearTimeout(toastTimer);
    toastTimer = setTimeout(function() { t.className = ''; }, 3200);
}

// ── Upload zone ───────────────────────────────────────────────────────────────
function setupUploadZone() {
    var zone = document.getElementById('uploadZone');
    var fi   = document.getElementById('fileInput');
    if (!zone || !fi) return;
    zone.addEventListener('dragover',  function(e){ e.preventDefault(); zone.classList.add('drag-over'); });
    zone.addEventListener('dragleave', function()  { zone.classList.remove('drag-over'); });
    zone.addEventListener('drop', function(e){
        e.preventDefault(); zone.classList.remove('drag-over');
        if (e.dataTransfer.files.length) { fi.files = e.dataTransfer.files; handleFileSelect(); }
    });
    fi.addEventListener('change', handleFileSelect);
}

function handleFileSelect() {
    var f = document.getElementById('fileInput').files[0];
    if (!f) return;
    var ext = f.name.split('.').pop().toLowerCase();
    if (!['csv','xlsx','xls'].includes(ext)) { alert('Invalid file type.'); return; }
    if (f.size > 5*1024*1024) { alert('File too large (max 5 MB).'); return; }
    document.getElementById('fileChosen').textContent = f.name + ' (' + (f.size/1024).toFixed(1) + ' KB)';
    document.getElementById('uploadBtn').disabled = false;
    document.getElementById('importResult').style.display = 'none';
}

function uploadFile() {
    var f = document.getElementById('fileInput').files[0]; if (!f) return;
    document.getElementById('uploadBtn').disabled = true;
    var bar = document.getElementById('progressBar'), fill = document.getElementById('progressFill');
    bar.style.display = 'block';
    var xhr = new XMLHttpRequest();
    xhr.upload.addEventListener('progress', function(e){ if (e.lengthComputable) fill.style.width = Math.round(e.loaded/e.total*90)+'%'; });
    xhr.open('POST', IMP_URL); xhr.setRequestHeader('X-CSRFToken', CSRF);
    xhr.onload = function() {
        fill.style.width = '100%';
        setTimeout(function(){
            bar.style.display = 'none'; fill.style.width = '0%';
            try { var d = JSON.parse(xhr.responseText); showImportResult(d, xhr.status); if (d.success) loadAndInit(); }
            catch(e) { showToast('Unexpected response.', 'err'); }
            document.getElementById('uploadBtn').disabled = false;
        }, 400);
    };
    xhr.onerror = function(){ bar.style.display='none'; showToast('Network error.','err'); document.getElementById('uploadBtn').disabled=false; };
    var fd = new FormData(); fd.append('file', f); xhr.send(fd);
}

function showImportResult(data, status) {
    var el = document.getElementById('importResult'); el.style.display = 'block';
    if (!data.success || status >= 400) { el.innerHTML = '<div class="result-banner err">' + esc(data.error||'Import failed.') + '</div>'; return; }
    var s = data.summary;
    var cls = s.skipped > 0 ? (s.created+s.updated > 0 ? 'warn' : 'err') : 'ok';
    var protectedNote = (s.protected > 0)
        ? ' <span style="font-size:.82em;font-weight:400">(' + s.protected + ' protected — has scan data)</span>'
        : '';
    el.innerHTML = '<div class="result-banner ' + cls + '">Import complete — ' + (s.created+s.updated) + ' processed, ' + s.deleted + ' deleted' + protectedNote + ', ' + s.skipped + ' skipped.</div>' +
        '<div class="sum-grid"><div class="sum-chip"><div class="sn">' + s.total_rows + '</div><div class="sl">Total</div></div>' +
        '<div class="sum-chip cr"><div class="sn">' + s.created + '</div><div class="sl">Created</div></div>' +
        '<div class="sum-chip up"><div class="sn">' + s.updated + '</div><div class="sl">Updated</div></div>' +
        '<div class="sum-chip" style="color:#c62828"><div class="sn">' + s.deleted + '</div><div class="sl">Deleted</div></div>' +
        (s.protected > 0 ? '<div class="sum-chip" style="color:#e65100"><div class="sn">' + s.protected + '</div><div class="sl">Protected</div></div>' : '') +
        '<div class="sum-chip sk"><div class="sn">' + s.skipped + '</div><div class="sl">Skipped</div></div></div>';
}

function resetUpload() {
    document.getElementById('fileInput').value = '';
    document.getElementById('fileChosen').textContent = '';
    document.getElementById('uploadBtn').disabled = true;
    document.getElementById('importResult').style.display = 'none';
    document.getElementById('progressBar').style.display = 'none';
}