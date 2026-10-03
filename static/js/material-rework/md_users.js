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

function getCookie(name) {
    var c = document.cookie.split(';').map(function(s){ return s.trim(); }).find(function(s){ return s.startsWith(name + '='); });
    return c ? decodeURIComponent(c.split('=')[1]) : null;
}

function escHtml(str) {
    return String(str||'').replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;');
}

function slugify(s) {
    return s.toLowerCase().replace(/[^a-z0-9]+/g,'-').replace(/^-|-$/g,'');
}

/* ── Stats init ── */
document.addEventListener('DOMContentLoaded', () => {
    const rows = document.querySelectorAll('.user-row');
    ROLE_GROUPS.forEach(rg => {
        const el = document.getElementById('stat_' + slugify(rg.name));
        if (!el) return;
        const cnt = [...rows].filter(r => r.dataset.groups && r.dataset.groups.split(',').includes(rg.name)).length;
        el.textContent = cnt;
    });
});

/* ── Filtering ── */
let activeFilter = 'all';

function filterUsers() {
    // mdUserSearch, not searchInput — base.html's header search owns that id
    // and wins getElementById, which is why this filtered nothing.
    const box = document.getElementById('mdUserSearch');
    const q = (box ? box.value : '').trim().toLowerCase();
    document.querySelectorAll('.user-row').forEach(row => {
        const matchSearch = !q || row.dataset.name.includes(q) || row.dataset.email.includes(q);
        const groups = row.dataset.groups ? row.dataset.groups.split(',') : [];
        let matchFilter = true;
        if (activeFilter !== 'all') matchFilter = groups.includes(activeFilter);
        row.style.display = (matchSearch && matchFilter) ? '' : 'none';
    });
}

function setFilter(filter, filterBtn) {
    activeFilter = filter;
    document.querySelectorAll('.filter-btn').forEach(b => b.classList.remove('active'));
    filterBtn.classList.add('active');
    filterUsers();
}

/* ── Edit Roles Panel ── */
function openEditPanel(userId, userName, userEmail, userGroups, isSuperuser) {
    document.getElementById('ep_user_id').value = userId;
    document.getElementById('epUserName').textContent  = userName;
    document.getElementById('epUserEmail').textContent = userEmail;

    const warning = document.getElementById('epSuperuserWarning');
    if (warning) warning.style.display = isSuperuser ? 'block' : 'none';

    ROLE_GROUPS.forEach((rg, i) => {
        const idx   = i + 1;
        const row   = document.getElementById('row_' + idx);
        const chk   = document.getElementById('chk_' + idx);
        const icon  = document.getElementById('chkicon_' + idx);
        const isOn  = userGroups.includes(rg.name);
        chk.checked = isOn;
        if (isOn) { row.classList.add('selected'); icon.style.display = 'block'; }
        else       { row.classList.remove('selected'); icon.style.display = 'none'; }
    });

    document.getElementById('editPanelOverlay').classList.add('open');
}

function closeEditPanel() {
    document.getElementById('editPanelOverlay').classList.remove('open');
}

function toggleRoleRow(labelEl) {
    const chk  = labelEl.querySelector('input[type=checkbox]');
    const icon = labelEl.querySelector('.role-check i');
    chk.checked = !chk.checked;
    if (chk.checked) { labelEl.classList.add('selected'); icon.style.display = 'block'; }
    else              { labelEl.classList.remove('selected'); icon.style.display = 'none'; }
}

/* ── Create User Panel ── */
function openCreatePanel() {
    document.getElementById('createPanelOverlay').classList.add('open');
}

function closeCreatePanel() {
    document.getElementById('createPanelOverlay').classList.remove('open');
    document.getElementById('createUserForm').reset();
}

/* ── Style cu-role-row hover ── */
document.querySelectorAll('.cu-role-row').forEach(row => {
    const chk = row.querySelector('input');
    function update() {
        row.style.borderColor = chk.checked ? '#0076c8' : '#e5e7eb';
        row.style.background  = chk.checked ? '#eff6ff' : '';
    }
    update();
    chk.addEventListener('change', update);
});

/* ── AJAX helpers ── */
function mdUsersPost(fd, submitBtn, origHtml, onSuccess, errElId) {
    if (submitBtn) { submitBtn.disabled = true; submitBtn.innerHTML = '<i class="fas fa-spinner fa-spin"></i> Saving…'; }
    if (errElId) document.getElementById(errElId).style.display = 'none';

    fetch(window.location.href, {
        method: 'POST',
        body: fd,
        headers: { 'X-Requested-With': 'XMLHttpRequest' }
    })
    .then(r => r.json())
    .then(data => {
        if (data.ok) {
            if (submitBtn) { submitBtn.disabled = false; submitBtn.innerHTML = origHtml; }
            onSuccess(data);
        } else {
            const errMsg = data.error || ((data.messages||[]).find(m=>(m.tags||'').includes('error'))||{}).text || 'Error.';
            if (errElId) { document.getElementById(errElId).textContent = errMsg; document.getElementById(errElId).style.display = 'block'; }
            if (submitBtn) { submitBtn.disabled = false; submitBtn.innerHTML = origHtml; }
        }
    })
    .catch(() => {
        if (errElId) { document.getElementById(errElId).textContent = 'Network error.'; document.getElementById(errElId).style.display = 'block'; }
        if (submitBtn) { submitBtn.disabled = false; submitBtn.innerHTML = origHtml; }
    });
}

function showMdToast(msg, type) {
    var s = type==='success' ? {bg:'#ecfdf5',b:'1px solid #6ee7b7',c:'#065f46',i:'fa-check-circle'} : {bg:'#fef2f2',b:'1px solid #fecaca',c:'#991b1b',i:'fa-exclamation-circle'};
    var t = document.createElement('div');
    t.style.cssText = 'position:fixed;top:20px;right:20px;z-index:9999999;padding:12px 18px;border-radius:8px;font-size:.87rem;font-weight:600;box-shadow:0 4px 20px rgba(0,0,0,.18);display:flex;align-items:center;gap:8px;background:'+s.bg+';border:'+s.b+';color:'+s.c+';';
    t.innerHTML = '<i class="fas '+s.i+'"></i><span>'+escHtml(msg)+'</span>';
    document.body.appendChild(t);
    setTimeout(()=>{ t.style.transition='opacity .4s'; t.style.opacity='0'; setTimeout(()=>t.remove(),400); },3000);
}

function buildRoleChips(roles) {
    return roles.map(r =>
        `<span class="role-chip" style="background:${r.color}20;color:${r.color};border:1px solid ${r.color}40;">
            <i class="fas fa-${r.icon}" style="font-size:.6rem;"></i> ${escHtml(r.label)}
        </span>`
    ).join('') || '<span style="font-size:.75rem;color:#9ca3af;">No roles</span>';
}

/* ── Stat chip helpers ── */
function _updateTotalStat(delta) {
    const el = document.querySelector('.stats-bar .stat-num');
    if (el) el.textContent = Math.max(0, (parseInt(el.textContent) || 0) + delta);
}

function _recalcGroupStats() {
    const rows = document.querySelectorAll('.user-row');
    ROLE_GROUPS.forEach(rg => {
        const el = document.getElementById('stat_' + slugify(rg.name));
        if (!el) return;
        el.textContent = [...rows].filter(r => r.dataset.groups && r.dataset.groups.split(',').includes(rg.name)).length;
    });
}

function submitEditRoles() {
    const form = document.getElementById('editRolesForm');
    const epBtn  = document.getElementById('epSaveBtn');
    const fd   = new FormData(form);
    const orig = epBtn.innerHTML;
    mdUsersPost(fd, epBtn, orig, data => {
        closeEditPanel();
        const row = document.querySelector(`.user-row[data-user-id="${data.user_id}"]`);
        if (row) {
            row.querySelector('.role-chips-cell').innerHTML = buildRoleChips(data.roles);
            row.dataset.groups = (data.user_groups||[]).join(',');
            const editBtn = row.querySelector('.btn-edit-roles');
            if (editBtn) {
                const uName  = (row.dataset.displayName || '').replace(/\\/g,'\\\\').replace(/"/g,'\\"');
                const uEmail = (row.dataset.email || '').replace(/"/g,'\\"');
                const isSu   = row.dataset.isSuperuser === '1';
                editBtn.setAttribute('onclick',
                    `openEditPanel(${data.user_id},"${uName}","${uEmail}",${JSON.stringify(data.user_groups||[])},${isSu})`);
            }
        }
        _recalcGroupStats();
        showMdToast('Roles updated.', 'success');
    }, 'ep_err');
}

function submitCreateUser() {
    const form  = document.getElementById('createUserForm');
    const cuBtn = document.getElementById('cuSaveBtn');
    const errEl = document.getElementById('cu_err');
    errEl.style.display = 'none';
    const emailVal = (form.elements['email'] && form.elements['email'].value) || '';
    if (emailVal && _mdBlockedDomain(emailVal)) {
        errEl.textContent = _MD_BLOCKED_MSG;
        errEl.style.display = 'block';
        return;
    }
    const fd   = new FormData(form);
    const orig = cuBtn.innerHTML;
    mdUsersPost(fd, cuBtn, orig, data => {
        closeCreatePanel();
        const u = data.user;
        const avatarColors = ['#0076c8','#10b981','#8b5cf6','#f59e0b','#ef4444','#0e7490'];
        const color = avatarColors[u.id % avatarColors.length];
        const tr = document.createElement('tr');
        tr.className = 'user-row';
        tr.dataset.userId       = u.id;
        tr.dataset.isSuperuser  = '0';
        tr.dataset.name         = (u.full_name||'').toLowerCase();
        tr.dataset.email        = (u.email||'').toLowerCase();
        tr.dataset.groups       = (u.user_groups||[]).join(',');
        tr.innerHTML = `
            <td><div style="display:flex;align-items:center;gap:10px;">
                <span class="avatar" style="background:${color};">${escHtml(u.initials)}</span>
                <div><div style="font-weight:700;color:#06152b;font-size:.88rem;">${escHtml(u.full_name)}</div>
                <div style="font-size:.77rem;color:#9ca3af;">@${escHtml(u.username)}</div></div></div></td>
            <td style="font-size:.83rem;">${escHtml(u.email)}</td>
            <td class="role-chips-cell">${buildRoleChips(u.roles)}</td>
            <td><span class="${u.is_active ? 'badge-active' : 'badge-inactive'}">${u.is_active ? 'Active' : 'Inactive'}</span></td>
            <td style="font-size:.78rem;color:#9ca3af;">Just now</td>
            <td>
                <div style="display:flex;gap:6px;flex-wrap:wrap;">
                    <button class="btn-edit-roles"><i class="fas fa-user-tag"></i> Roles</button>
                </div>
            </td>`;
        tr.querySelector('.btn-edit-roles').addEventListener('click', function() {
            const isSu = tr.dataset.isSuperuser === '1';
            openEditPanel(u.id, u.full_name || u.username, u.email, u.user_groups || [], isSu);
        });
        let tbody = document.querySelector('.user-table tbody');
        if (!tbody) {
            const wrap = document.querySelector('.user-table-wrap');
            if (wrap) {
                wrap.innerHTML = `
                    <table class="user-table" id="userTable">
                        <thead><tr>
                            <th>User</th><th>Email</th><th>Roles</th>
                            <th>Status</th><th>Last Login</th><th>Actions</th>
                        </tr></thead>
                        <tbody></tbody>
                    </table>`;
                tbody = wrap.querySelector('tbody');
            }
        }
        if (tbody) tbody.insertBefore(tr, tbody.firstChild);
        _updateTotalStat(1);
        _recalcGroupStats();
        showMdToast('User created.', 'success');
    }, 'cu_err');
}

