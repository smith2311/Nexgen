var _delBatchUrl = '';

function openDelModal(batchId, refNum) {
    document.getElementById('del_ref_display').textContent = refNum;
    document.getElementById('del_ajax_err').style.display = 'none';
    _delBatchUrl = '/material-rework/delete/' + batchId + '/';
    var btn = document.getElementById('delConfirmBtn');
    if (btn) { btn.disabled = false; btn.innerHTML = '<i class="fas fa-trash"></i> Yes, Delete'; }
    document.getElementById('delOverlay').classList.add('open');
}
function closeDelModal() { document.getElementById('delOverlay').classList.remove('open'); }

function doDeleteBatch() {
    var btn = document.getElementById('delConfirmBtn');
    btn.disabled = true;
    btn.innerHTML = '<i class="fas fa-spinner fa-spin"></i> Deleting…';
    document.getElementById('del_ajax_err').style.display = 'none';

    var fd = new FormData();
    fd.append('csrfmiddlewaretoken', getCookie('csrftoken') || '');
    fd.append('next', window.location.pathname);

    fetch(_delBatchUrl, {
        method: 'POST',
        body: fd,
        headers: { 'X-Requested-With': 'XMLHttpRequest' }
    })
    .then(function(r) { return r.json(); })
    .then(function(data) {
        if (data.ok) {
            closeDelModal();
            if (typeof onBatchDeleted === 'function') onBatchDeleted(data.batch_id, data.ref);
        } else {
            document.getElementById('del_ajax_err').textContent = data.error || 'Delete failed.';
            document.getElementById('del_ajax_err').style.display = 'block';
            btn.disabled = false;
            btn.innerHTML = '<i class="fas fa-trash"></i> Yes, Delete';
        }
    })
    .catch(function() {
        document.getElementById('del_ajax_err').textContent = 'Network error. Please try again.';
        document.getElementById('del_ajax_err').style.display = 'block';
        btn.disabled = false;
        btn.innerHTML = '<i class="fas fa-trash"></i> Yes, Delete';
    });
}
