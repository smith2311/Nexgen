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
const API     = '/product_scanning/api/gtin-mappings/';
const IMP_URL = '/product_scanning/api/gtin-mappings/import-master/';
const LIST_URL = API + 'list_mappings/';
const _gpcfg  = document.getElementById('page-config');
const canViewOnly = _gpcfg ? JSON.parse(_gpcfg.textContent).canViewOnly : false;

let dt          = null;
let deleteTarget = null;
let currentRow  = null;

// ── Boot ──────────────────────────────────────────────────────────────────────
$(function () {
    initDataTable();
    setupUploadZone();
});

// ── DataTables server-side ────────────────────────────────────────────────────
function initDataTable() {
    dt = $('#gtinTable').DataTable({
        serverSide: true,
        processing: true,
        ajax: {
            url: LIST_URL,
            type: 'GET',
            xhrFields: { withCredentials: true },
            dataSrc: function(json) {
                document.getElementById('statTotal').textContent    = json.recordsTotal;
                document.getElementById('statFiltered').textContent = json.recordsFiltered;
                return json.data;
            },
        },
        columns: [
            { data: 'material_code',        title: 'Material',             render: function(d){ return '<strong>'+esc(d||'')+'</strong>'; } },
            { data: 'brand',                title: 'Brand',                render: function(d){ return d ? esc(d) : '<span style="color:#ccc">—</span>'; } },
            { data: 'product_desc',         title: 'Product Desc',         render: function(d){ return d ? esc(d) : '<span style="color:#ccc">—</span>'; } },
            { data: 'material_description', title: 'Material Description',  render: function(d){ return d ? esc(d) : '<span style="color:#ccc">—</span>'; } },
            { data: 'control_code',         title: 'Control Code',         render: function(d){ return d ? esc(d) : '<span style="color:#ccc">—</span>'; } },
            { data: 'gtin_code',            title: 'GTIN',                 render: function(d){ return '<code class="gtin-chip">'+esc(d||'')+'</code>'; } },
            { data: 'material_type_desc',   title: 'Material Type',        render: function(d){ return d ? esc(d) : '<span style="color:#ccc">—</span>'; } },
            {
                data: null, title: 'Actions', orderable: false, searchable: false,
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
                    var row = rowData;
                    td.querySelectorAll('button')[0].addEventListener('click', function() { openEditModal(row); });
                    td.querySelectorAll('button')[1].addEventListener('click', function() { openDeleteModal(row.id, row.gtin_code); });
                }
            },
        ],
        pageLength: 25,
        lengthMenu: [[10, 25, 50, 100], [10, 25, 50, 100]],
        order: [[0, 'asc']],
        language: { search: 'Search:', emptyTable: 'No mappings found.', processing: 'Loading...' },
    });
}

function esc(s) { return String(s ?? '').replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/"/g,'&quot;'); }

// ── Add Modal ─────────────────────────────────────────────────────────────────
function openAddModal() {
    clearForm();
    document.getElementById('editId').value = '';
    document.getElementById('modalTitle').textContent = 'Add GTIN Mapping';
    document.getElementById('saveBtn').textContent = 'Save Mapping';
    openModal('editModal');
    document.getElementById('fGtin').focus();
}

// ── Edit Modal (row object comes from DataTables render) ──────────────────────
function openEditModal(row) {
    if (!row) return;
    clearForm();
    currentRow = row;
    document.getElementById('editId').value          = row.id;
    document.getElementById('fGtin').value           = row.gtin_code || '';
    document.getElementById('fMatcode').value        = row.material_code || '';
    document.getElementById('fBrand').value          = row.brand || '';
    document.getElementById('fControlCode').value    = row.control_code || '';
    document.getElementById('fProductDesc').value    = row.product_desc || '';
    document.getElementById('fMaterialDesc').value   = row.material_description || '';
    document.getElementById('fMaterialType').value   = row.material_type_desc || '';
    document.getElementById('modalTitle').textContent = 'Edit GTIN Mapping';
    document.getElementById('saveBtn').textContent    = 'Update Mapping';
    openModal('editModal');
}

// ── Save ──────────────────────────────────────────────────────────────────────
function saveMapping() {
    hideAlert();
    if (!validate()) return;
    var id = document.getElementById('editId').value;
    var body = {
        gtin_code:            document.getElementById('fGtin').value.trim(),
        material_code:        document.getElementById('fMatcode').value.trim(),
        brand:                document.getElementById('fBrand').value.trim() || null,
        control_code:         document.getElementById('fControlCode').value.trim() || null,
        product_desc:         document.getElementById('fProductDesc').value.trim() || null,
        material_description: document.getElementById('fMaterialDesc').value.trim() || null,
        material_type_desc:   document.getElementById('fMaterialType').value.trim() || null,
    };
    var btn = document.getElementById('saveBtn');
    var orig = btn.textContent;
    btn.disabled = true;
    btn.innerHTML = '<span class="spinner"></span> Saving…';
    $.ajax({
        url:    id ? API + id + '/' : API,
        method: id ? 'PATCH' : 'POST',
        contentType: 'application/json',
        data: JSON.stringify(body),
        headers: { 'X-CSRFToken': CSRF },
        xhrFields: { withCredentials: true },
        success: function() {
            closeModal('editModal');
            showToast(id ? 'Mapping updated.' : 'Mapping added.', 'ok');
            dt.ajax.reload(null, false);
        },
        error: function(xhr) {
            var d = xhr.responseJSON || {};
            var msg = Object.entries(d).map(function(e){ return e[0]+': '+(Array.isArray(e[1])?e[1].join(', '):e[1]); }).join('\n') || 'Save failed.';
            showAlert(msg);
        },
        complete: function() { btn.disabled = false; btn.textContent = orig; },
    });
}

// ── Delete ────────────────────────────────────────────────────────────────────
function openDeleteModal(id, gtin) {
    deleteTarget = id;
    document.getElementById('delLabel').textContent = gtin;
    openModal('deleteModal');
}

function confirmDelete() {
    if (!deleteTarget) return;
    var btn  = document.getElementById('confirmDelBtn');
    var orig = btn.innerHTML;
    btn.disabled = true;
    btn.innerHTML = '<span class="spinner"></span> Deleting…';
    $.ajax({
        url:    API + deleteTarget + '/',
        method: 'DELETE',
        headers: { 'X-CSRFToken': CSRF },
        xhrFields: { withCredentials: true },
        success: function() {
            closeModal('deleteModal');
            showToast('Mapping deleted.', 'ok');
            dt.ajax.reload(null, false);
        },
        error: function() { showToast('Delete failed.', 'err'); },
        complete: function() { btn.disabled = false; btn.innerHTML = orig; deleteTarget = null; },
    });
}

// ── Form helpers ──────────────────────────────────────────────────────────────
function validate() {
    var ok = true;
    function chk(fId, eId, cond) {
        document.getElementById(fId).classList.toggle('err', cond);
        document.getElementById(eId).classList.toggle('show', cond);
        if (cond) ok = false;
    }
    chk('fGtin',    'fGtinErr',    !document.getElementById('fGtin').value.trim());
    chk('fMatcode', 'fMatcodeErr', !document.getElementById('fMatcode').value.trim());
    return ok;
}

function clearForm() {
    ['fGtin','fMatcode','fBrand','fControlCode','fProductDesc','fMaterialDesc','fMaterialType'].forEach(function(id){
        var el = document.getElementById(id); el.value = ''; el.classList.remove('err');
    });
    ['fGtinErr','fMatcodeErr'].forEach(function(id){ document.getElementById(id).classList.remove('show'); });
    hideAlert();
    currentRow = null;
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
    toastTimer = setTimeout(function(){ t.className = ''; }, 3200);
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
    var f = document.getElementById('fileInput').files[0]; if (!f) return;
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
            try {
                var d = JSON.parse(xhr.responseText);
                showImportResult(d, xhr.status);
                if (d.success && (d.summary.created + d.summary.updated) > 0) {
                    dt.ajax.reload(null, false);
                }
            } catch(e) { showToast('Unexpected response.', 'err'); }
            document.getElementById('uploadBtn').disabled = false;
        }, 400);
    };
    xhr.onerror = function(){ bar.style.display='none'; showToast('Network error.','err'); document.getElementById('uploadBtn').disabled=false; };
    var fd = new FormData(); fd.append('file', f); xhr.send(fd);
}

function showImportResult(data, status) {
    var el = document.getElementById('importResult'); el.style.display = 'block';
    if (!data.success || status >= 400) { el.innerHTML = '<div class="result-banner err">'+esc(data.error||'Import failed.')+'</div>'; return; }
    var s = data.summary;
    var cls = s.skipped > 0 ? (s.created+s.updated > 0 ? 'warn' : 'err') : 'ok';
    el.innerHTML = '<div class="result-banner '+cls+'">Import complete — '+(s.created+s.updated)+' processed, '+s.deleted+' deleted, '+s.skipped+' skipped.</div>'+
        '<div class="sum-grid">'+
        '<div class="sum-chip"><div class="sn">'+s.total_rows+'</div><div class="sl">Total</div></div>'+
        '<div class="sum-chip cr"><div class="sn">'+s.created+'</div><div class="sl">Created</div></div>'+
        '<div class="sum-chip up"><div class="sn">'+s.updated+'</div><div class="sl">Updated</div></div>'+
        '<div class="sum-chip" style="color:#c62828"><div class="sn">'+s.deleted+'</div><div class="sl">Deleted</div></div>'+
        '<div class="sum-chip sk"><div class="sn">'+s.skipped+'</div><div class="sl">Skipped</div></div></div>';
}

function resetUpload() {
    document.getElementById('fileInput').value = '';
    document.getElementById('fileChosen').textContent = '';
    document.getElementById('uploadBtn').disabled = true;
    document.getElementById('importResult').style.display = 'none';
    document.getElementById('progressBar').style.display = 'none';
}