function toggleDepts(vid) {
    var row   = document.getElementById('depts-' + vid);
    var chev  = document.getElementById('chev-' + vid);
    var open  = row.style.display !== 'none';
    row.style.display    = open ? 'none' : 'table-row';
    chev.style.transform = open ? '' : 'rotate(90deg)';
}

/* ── Toast ───────────────────────────────────────────────────── */
function showToast(msg, type) {
    var t = document.createElement('div');
    var styles = {
        success: {bg:'#ecfdf5', border:'1px solid #6ee7b7', color:'#065f46', icon:'fa-check-circle'},
        error:   {bg:'#fef2f2', border:'1px solid #fecaca', color:'#991b1b', icon:'fa-exclamation-circle'},
        warning: {bg:'#fffbeb', border:'1px solid #fde68a', color:'#92400e', icon:'fa-exclamation-triangle'},
    };
    var s = styles[type] || styles.success;
    t.style.cssText = 'position:fixed;top:20px;right:20px;z-index:9999999;padding:12px 18px;border-radius:8px;font-size:.87rem;font-weight:600;box-shadow:0 4px 20px rgba(0,0,0,.18);animation:mdToastIn .25s ease;max-width:380px;display:flex;align-items:center;gap:8px;background:' + s.bg + ';border:' + s.border + ';color:' + s.color + ';';
    t.innerHTML = '<i class="fas ' + s.icon + '"></i><span>' + escHtml(msg) + '</span>';
    document.body.appendChild(t);
    setTimeout(function(){ t.style.transition='opacity .4s'; t.style.opacity='0'; setTimeout(function(){ t.remove(); }, 400); }, 3500);
}

function escHtml(s) { return String(s||'').replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;'); }

/* ── Email domain validation ─────────────────────────────────── */
var _MD_BLOCKED_DOMAINS = ['gmail.com','gmail.co.in','googlemail.com',
    'yahoo.com','yahoo.in','yahoo.co.in','yahoo.co.uk',
    'outlook.com','outlook.in','hotmail.com','hotmail.in','hotmail.co.uk',
    'rediffmail.com','live.com','live.in','live.co.uk',
    'icloud.com','me.com','mac.com','aol.com','ymail.com','msn.com',
    'protonmail.com','mail.com'];
var _MD_BLOCKED_MSG = 'Personal email domains (Gmail, Yahoo, Outlook, Hotmail, etc.) are not allowed. Please use a company email address.';

function _mdBlockedDomain(email) {
    var m = email.toLowerCase().match(/@([^,;\s@]+)$/);
    return m && _MD_BLOCKED_DOMAINS.indexOf(m[1]) !== -1;
}

function _mdCheckEmailFields(form) {
    var emailFields = ['hod_email','warehouse_email','production_review_email','email'];
    for (var i = 0; i < emailFields.length; i++) {
        var el = form.elements[emailFields[i]];
        if (!el || !el.value.trim()) continue;
        var parts = el.value.split(/[,;\s]+/);
        for (var j = 0; j < parts.length; j++) {
            if (parts[j].trim() && _mdBlockedDomain(parts[j].trim())) {
                return _MD_BLOCKED_MSG;
            }
        }
    }
    return null;
}

/* ── Modal error display ─────────────────────────────────────── */
function mdShowError(modalId, msg) {
    var modal = document.getElementById(modalId);
    var err = modal.querySelector('.md-ajax-err');
    if (!err) {
        err = document.createElement('div');
        err.className = 'md-ajax-err';
        err.style.cssText = 'background:#fef2f2;border:1px solid #fecaca;border-radius:5px;padding:8px 12px;margin-bottom:12px;color:#b91c1c;font-size:.82rem;display:flex;align-items:center;gap:7px;';
        var form = modal.querySelector('form');
        if (form) form.insertBefore(err, form.firstChild);
    }
    err.innerHTML = '<i class="fas fa-exclamation-circle"></i><span>' + escHtml(msg) + '</span>';
    err.style.display = 'flex';
}

function mdClearError(modalId) {
    var err = document.getElementById(modalId).querySelector('.md-ajax-err');
    if (err) err.style.display = 'none';
}

/* ── modal ───────────────────────────────────────────────────── */
function mdOpenModal(id)  { document.getElementById(id).classList.add('open'); }
function mdCloseModal(id) { mdClearError(id); document.getElementById(id).classList.remove('open'); }
document.querySelectorAll('.md-overlay').forEach(function(m) {
    m.addEventListener('click', function(e) { if (e.target === m) { mdCloseModal(m.id); } });
});

/* ── AJAX modal form submit ──────────────────────────────────── */
document.querySelectorAll('.md-overlay form').forEach(function(form) {
    var modal   = form.closest('.md-overlay');
    var modalId = modal.id;
    form.addEventListener('submit', function(e) {
        e.preventDefault();
        mdClearError(modalId);
        var domainErr = _mdCheckEmailFields(form);
        if (domainErr) { mdShowError(modalId, domainErr); return; }
        var btn  = form.querySelector('button[type=submit]');
        var orig = btn ? btn.innerHTML : '';
        if (btn) { btn.disabled = true; btn.innerHTML = '<i class="fas fa-spinner fa-spin"></i> Saving…'; }

        fetch(form.getAttribute('action') || window.location.href, {
            method: 'POST',
            body: new FormData(form),
            headers: { 'X-Requested-With': 'XMLHttpRequest' }
        })
        .then(function(r) { return r.json(); })
        .then(function(data) {
            if (data.ok) {
                mdCloseModal(modalId);
                var msgs = data.messages || [];
                if (msgs.length) {
                    msgs.forEach(function(m) {
                        var type = (m.tags||'').includes('error') ? 'error' : (m.tags||'').includes('warning') ? 'warning' : 'success';
                        showToast(m.text, type);
                    });
                } else { showToast('Saved.', 'success'); }
                setTimeout(function() { location.reload(); }, 900);
            } else {
                var msgs = data.messages || [];
                var errMsg = msgs.find(function(m){ return (m.tags||'').includes('error'); });
                mdShowError(modalId, errMsg ? errMsg.text : (data.error || 'An error occurred.'));
                if (btn) { btn.disabled = false; btn.innerHTML = orig; }
            }
        })
        .catch(function() {
            mdShowError(modalId, 'Network error. Please try again.');
            if (btn) { btn.disabled = false; btn.innerHTML = orig; }
        });
    });
});

/* ── tabs ────────────────────────────────────────────────────── */
document.querySelectorAll('.md-tab').forEach(function(tab) {
    tab.addEventListener('click', function() {
        document.querySelectorAll('.md-tab').forEach(function(t) { t.classList.remove('active'); });
        document.querySelectorAll('.md-panel').forEach(function(p) { p.classList.remove('active'); });
        tab.classList.add('active');
        document.getElementById('panel-' + tab.dataset.tab).classList.add('active');
    });
});

/* ── search ──────────────────────────────────────────────────── */
function filterRows(tbodyId, q) {
    q = q.toLowerCase();
    document.querySelectorAll('#' + tbodyId + ' tr[data-search]').forEach(function(row) {
        row.style.display = row.dataset.search.includes(q) ? '' : 'none';
    });
}

/* ── edit helpers ────────────────────────────────────────────── */
function openEditVertical(id, name, active) {
    document.getElementById('ev_id').value       = id;
    document.getElementById('ev_name').value     = name;
    document.getElementById('ev_active').checked = active;
    mdOpenModal('editVertical');
}
/* Named fields rather than positional arguments.
 *
 * A department now carries five notification addresses, and every new one
 * added to a positional list shifts `active` along by one — the checkbox then
 * silently receives an email string, which is truthy, so an inactive
 * department quietly reads as active. Naming them removes that whole class of
 * mistake, and the single caller in masters.html passes an object literal. */
function openEditDept(d) {
    document.getElementById('ed_id').value       = d.id;
    document.getElementById('ed_name').value     = d.name;
    document.getElementById('ed_hod').value      = d.hod || '';
    document.getElementById('ed_wh').value       = d.wh || '';
    document.getElementById('ed_pr').value       = d.pr || '';
    document.getElementById('ed_om').value       = d.om || '';
    document.getElementById('ed_ppic').value     = d.ppic || '';
    document.getElementById('ed_active').checked = !!d.active;
    mdOpenModal('editDept');
}
function openEditRequester(id, name, active) {
    document.getElementById('erq_id').value       = id;
    document.getElementById('erq_name').value     = name;
    document.getElementById('erq_active').checked = active;
    mdOpenModal('editRequester');
}
/* One edit-opener for every "simple" master (Country / Type of Rework /
   Location). Their modal ids are derived from the same action stem the server
   uses, so this needs no per-master branch — see
   _simple_master_modals.html. */
function openEditSimpleMaster(action, id, name, active) {
    document.getElementById('e_' + action + '_id').value       = id;
    document.getElementById('e_' + action + '_name').value     = name;
    document.getElementById('e_' + action + '_active').checked = active;
    mdOpenModal('edit_' + action);
}

function confirmDel(modalId, inputId, nameId, label, pk) {
    document.getElementById(nameId).textContent = label;
    document.getElementById(inputId).value = pk;
    mdOpenModal(modalId);
}

/* ── bulk select ─────────────────────────────────────────────── */
var grpCfg = {
    v:  { bar:'vBulkBar',  count:'vSelCount',  form:'vBulkForm',  ids:'vBulkIds',  all:'vSelectAll',  cls:'v-cb'  },
    d:  { bar:'dBulkBar',  count:'dSelCount',  form:'dBulkForm',  ids:'dBulkIds',  all:'dSelectAll',  cls:'d-cb'  },
    rq: { bar:'rqBulkBar', count:'rqSelCount', form:'rqBulkForm', ids:'rqBulkIds', all:'rqSelectAll', cls:'rq-cb' }
};

function updateBar(g) {
    var c = grpCfg[g];
    var n = document.querySelectorAll('.' + c.cls + ':checked').length;
    document.getElementById(c.count).textContent = n;
    document.getElementById(c.bar).classList.toggle('show', n > 0);
    var all = document.querySelectorAll('.' + c.cls);
    document.getElementById(c.all).checked = all.length > 0 && Array.from(all).every(function(cb){ return cb.checked; });
}

function clearSel(g) {
    var c = grpCfg[g];
    document.querySelectorAll('.' + c.cls).forEach(function(cb) {
        cb.checked = false;
        cb.closest('tr').classList.remove('selected');
    });
    document.getElementById(c.all).checked = false;
    updateBar(g);
}

function bulkDelete(g) {
    var c = grpCfg[g];
    var ids = Array.from(document.querySelectorAll('.' + c.cls + ':checked')).map(function(cb){ return cb.dataset.id; });
    if (!ids.length) return;
    if (!confirm('Delete ' + ids.length + ' record(s)? This cannot be undone.')) return;
    var container = document.getElementById(c.ids);
    container.innerHTML = '';
    ids.forEach(function(id) {
        var inp = document.createElement('input'); inp.type='hidden'; inp.name='ids'; inp.value=id;
        container.appendChild(inp);
    });
    var form = document.getElementById(c.form);
    var fd   = new FormData(form);
    fetch(form.getAttribute('action'), {
        method: 'POST',
        body: fd,
        headers: { 'X-Requested-With': 'XMLHttpRequest' }
    })
    .then(function(r) { return r.json(); })
    .then(function(data) {
        var msgs = data.messages || [];
        msgs.forEach(function(m) {
            var type = (m.tags||'').includes('error') ? 'error' : (m.tags||'').includes('warning') ? 'warning' : 'success';
            showToast(m.text, type);
        });
        if (!msgs.length) showToast(data.ok ? 'Deleted.' : (data.error||'Error.'), data.ok ? 'success' : 'error');
        if (data.ok) setTimeout(function(){ location.reload(); }, 900);
    })
    .catch(function() { showToast('Network error.', 'error'); });
}

Object.keys(grpCfg).forEach(function(g) {
    var c = grpCfg[g];
    document.getElementById(c.all).addEventListener('change', function() {
        var checked = this.checked;
        document.querySelectorAll('.' + c.cls).forEach(function(cb) {
            if (cb.closest('tr').style.display !== 'none') {
                cb.checked = checked;
                cb.closest('tr').classList.toggle('selected', checked);
            }
        });
        updateBar(g);
    });
    document.querySelectorAll('.' + c.cls).forEach(function(cb) {
        cb.addEventListener('change', function() {
            this.closest('tr').classList.toggle('selected', this.checked);
            updateBar(g);
        });
    });
});
