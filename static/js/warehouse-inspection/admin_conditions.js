const API_BASE = '/warehouse-inspection/api';
const ADMIN_URLS = window.WAREHOUSE_INSPECTION_ADMIN_URLS || {};
const ACCESS_USERS_URL = ADMIN_URLS.accessUsers || '';
const ACCESS_CREATE_URL = ADMIN_URLS.accessCreate || '';
const ACCESS_UPDATE_ROLE_URL = ADMIN_URLS.accessUpdateRole || '';
const ACCESS_REMOVE_URL = ADMIN_URLS.accessRemove || '';
const REPORT_RECIPIENT_ADD_URL = ADMIN_URLS.reportRecipientAdd || '';
const REPORT_RECIPIENT_REMOVE_URL = ADMIN_URLS.reportRecipientRemove || '';
const READ_TIMEOUT_MS = 30000;
const WRITE_TIMEOUT_MS = 120000;
const nativeFetch = window.fetch.bind(window);
const fetch = guardedFetch;
let accessUsers = [];
let currentEditingConditionId = null;
let currentDeleteConditionId = null;
let currentDeleteBuildingId = null;
let currentDeleteType = 'condition';
let currentBuildingId = null;
let currentEditingBuildingId = null;
let currentEditingFloorId = null;
let loadedConditions = [];
let loadedEditingCheckpoints = [];
let loadedBuildings = [];
let loadedFloors = [];
let floorsLoaded = false;
let isSavingCondition = false;
let currentSetupBuildingId = null;
let isSavingSetup = false;

function wait(ms) {
    return new Promise(resolve => setTimeout(resolve, ms));
}

function requestTimeoutMessage(method) {
    return method === 'GET'
        ? 'The request timed out. Please check your connection and try again.'
        : 'The save/delete request is taking too long. Please refresh before retrying to avoid duplicate changes.';
}

async function guardedFetch(input, init = {}) {
    const method = String(init.method || 'GET').toUpperCase();
    const timeoutMs = method === 'GET' ? READ_TIMEOUT_MS : WRITE_TIMEOUT_MS;
    const retryCount = method === 'GET' ? 1 : 0;
    let lastError = null;

    for (let attempt = 0; attempt <= retryCount; attempt += 1) {
        const controller = new AbortController();
        const timeout = setTimeout(() => controller.abort(), timeoutMs);
        try {
            const response = await nativeFetch(input, {
                credentials: 'same-origin',
                ...init,
                signal: controller.signal,
            });
            clearTimeout(timeout);
            return response;
        } catch (error) {
            clearTimeout(timeout);
            lastError = error;
            const canRetry = method === 'GET' && attempt < retryCount;
            if (canRetry) {
                await wait(700 * (attempt + 1));
                continue;
            }

            if (error?.name === 'AbortError') {
                throw new Error(requestTimeoutMessage(method));
            }
            if (navigator.onLine === false) {
                throw new Error('You appear to be offline. Please reconnect and try again.');
            }
            if (error instanceof TypeError) {
                throw new Error('Connection interrupted. Please check your network and try again.');
            }
            throw error;
        }
    }

    throw lastError || new Error('Something went wrong. Please try again.');
}

// ────────────────────────────────────────────────────────────────
// Message Dialog (no jQuery; matches existing UI)
// ────────────────────────────────────────────────────────────────

function showMessageDialog(message, options = {}) {
    const overlay = document.getElementById('messageDialog');
    const titleEl = document.getElementById('messageDialogTitle');
    const bodyEl = document.getElementById('messageDialogBody');
    const okBtn = document.getElementById('messageDialogOk');

    if (!overlay || !titleEl || !bodyEl || !okBtn) {
        // Fallback if template is partially loaded.
        window.alert(message);
        return;
    }

    const {
        title = 'Message',
        variant = 'info', // info | success | error | warning
        okText = 'OK',
        onClose = null,
    } = options;

    overlay.dataset.variant = variant;
    titleEl.textContent = title;
    bodyEl.textContent = String(message ?? '');
    okBtn.textContent = okText;

    // Replace any prior handler safely
    okBtn.onclick = () => {
        closeMessageDialog();
        if (typeof onClose === 'function') onClose();
    };

    overlay.classList.add('active');
}

function closeMessageDialog() {
    const overlay = document.getElementById('messageDialog');
    if (!overlay) return;
    overlay.classList.remove('active');
    overlay.dataset.variant = 'info';
}

// ────────────────────────────────────────────────────────────────
// Warehouse Inspection User Access
// ────────────────────────────────────────────────────────────────

function accessRoleFor(user) {
    if (user.is_wi_admin) return 'admin';
    if (user.has_wi_user_role) return 'user';
    return 'none';
}

function accessRoleLabel(role) {
    if (role === 'admin') return 'Admin';
    if (role === 'user') return 'User';
    return 'No access';
}

function accessStatusBadge(user) {
    if (!user.is_allowed_domain) {
        return '<span class="access-badge invalid"><i class="fas fa-ban"></i> Invalid domain</span>';
    }
    if (user.is_inherited_admin) {
        return '<span class="access-badge locked"><i class="fas fa-lock"></i> Global Admin</span>';
    }
    if (user.is_wi_admin) {
        return '<span class="access-badge admin"><i class="fas fa-user-shield"></i> Admin</span>';
    }
    if (user.has_wi_user_role) {
        return '<span class="access-badge user"><i class="fas fa-user"></i> User</span>';
    }
    return '<span class="access-badge none"><i class="fas fa-user-slash"></i> No access</span>';
}

function canReceiveReportEmails(user) {
    return Boolean(user && user.is_wi_admin && user.is_allowed_domain);
}

function upsertAccessUser(user) {
    if (!user) return;
    const index = accessUsers.findIndex(item => Number(item.id) === Number(user.id));
    if (index >= 0) {
        accessUsers[index] = user;
    } else {
        accessUsers.unshift(user);
    }
}

function removeAccessUserFromTable(userId) {
    accessUsers = accessUsers.filter(item => Number(item.id) !== Number(userId));
}

async function loadAccessUsers() {
    const container = document.getElementById('accessTableContainer');
    if (!container) return;

    try {
        const response = await fetch(ACCESS_USERS_URL);
        const data = await parseJsonSafe(response);
        if (!response.ok) throw data;
        accessUsers = Array.isArray(data.users) ? data.users : [];
        renderAccessUsers();
    } catch (err) {
        console.error(err);
        container.innerHTML = '<div class="access-empty">Could not load users.</div>';
    }
}

function renderAccessUsers() {
    const container = document.getElementById('accessTableContainer');
    const searchInput = document.getElementById('accessSearch');
    if (!container) return;

    const query = (searchInput?.value || '').trim().toLowerCase();
    const filteredUsers = query
        ? accessUsers.filter(user =>
            String(user.full_name || '').toLowerCase().includes(query) ||
            String(user.username || '').toLowerCase().includes(query) ||
            String(user.email || '').toLowerCase().includes(query)
        )
        : accessUsers;

    if (!filteredUsers.length) {
        container.innerHTML = '<div class="access-empty">No users found.</div>';
        return;
    }

    const rows = filteredUsers.map(user => {
        const role = accessRoleFor(user);
        const locked = user.is_current_user || user.is_inherited_admin || !user.is_allowed_domain;
        const reportLocked = !canReceiveReportEmails(user);
        const reportTitle = reportLocked
            ? 'Only Warehouse Inspection admins can receive daily report and summary emails.'
            : 'Send daily report and summary emails to this admin.';
        const action = user.is_current_user
            ? '<span class="access-note">Current user</span>'
            : !user.is_allowed_domain && role === 'none'
                ? '<span class="access-note">Only @merillife.com allowed</span>'
                : user.is_inherited_admin
                ? '<span class="access-note">Managed by global role</span>'
                : `<button type="button" class="btn btn-danger btn-small" onclick="removeWarehouseAccess(${user.id})" title="Remove access">
                       <i class="fas fa-trash-alt"></i>
                   </button>`;

        return `
            <tr>
                <td class="access-user">
                    <strong>${escapeHtml(user.full_name || user.username || '')}</strong>
                    <span>@${escapeHtml(user.username || '')}</span>
                </td>
                <td>${escapeHtml(user.email || '-')}</td>
                <td>${accessStatusBadge(user)}</td>
                <td>
                    <select class="access-role" data-user-id="${user.id}" onchange="updateWarehouseAccessRole(this)" ${locked ? 'disabled' : ''}>
                        <option value="none" ${role === 'none' ? 'selected' : ''}>No access</option>
                        <option value="user" ${role === 'user' ? 'selected' : ''}>User</option>
                        <option value="admin" ${role === 'admin' ? 'selected' : ''}>Admin</option>
                    </select>
                </td>
                <td>
                    <label class="access-report-toggle ${reportLocked ? 'is-disabled' : ''}" title="${escapeHtml(reportTitle)}">
                        <input type="checkbox"
                               data-user-id="${user.id}"
                               onchange="toggleReportRecipient(this)"
                               ${user.receives_report_emails ? 'checked' : ''}
                               ${reportLocked ? 'disabled' : ''}>
                        <span></span>
                    </label>
                </td>
                <td>
                    <div class="access-actions">${action}</div>
                </td>
            </tr>
        `;
    }).join('');

    container.innerHTML = `
        <table class="access-table">
            <thead>
                <tr>
                    <th>User</th>
                    <th>Email</th>
                    <th>Status</th>
                    <th>Role</th>
                    <th>Daily Report</th>
                    <th>Actions</th>
                </tr>
            </thead>
            <tbody>${rows}</tbody>
        </table>
    `;
}

function openAccessUserDialog() {
    const dialog = document.getElementById('accessUserDialog');
    const form = document.getElementById('accessUserForm');
    if (form) form.reset();
    const role = document.getElementById('accessUserRole');
    if (role) role.value = 'user';
    syncAccessUserReportOption();
    dialog?.classList.add('active');
    document.getElementById('accessUserEmail')?.focus();
}

function closeAccessUserDialog() {
    document.getElementById('accessUserDialog')?.classList.remove('active');
}

async function submitAccessUserForm(event) {
    event.preventDefault();
    const email = document.getElementById('accessUserEmail')?.value.trim() || '';
    const role = document.getElementById('accessUserRole')?.value || 'user';
    const sendReports = Boolean(document.getElementById('accessUserReports')?.checked) && role === 'admin';
    const submitBtn = document.getElementById('accessUserSubmit');

    if (!email) {
        showMessageDialog('Email address is required.', { title: 'Validation', variant: 'warning' });
        return;
    }
    if (!email.toLowerCase().endsWith('@merillife.com')) {
        showMessageDialog('Only @merillife.com email addresses can access Warehouse Inspection.', { title: 'Invalid domain', variant: 'warning' });
        return;
    }

    if (submitBtn) submitBtn.disabled = true;
    try {
        const response = await fetch(ACCESS_CREATE_URL, {
            method: 'POST',
            headers: {
                'Content-Type': 'application/json',
                'X-CSRFToken': getCSRFToken(),
            },
            body: JSON.stringify({ email, role }),
        });
        const data = await parseJsonSafe(response);
        if (!response.ok || !data.success) throw data;

        if (sendReports) {
            await setReportRecipient(data.user, true);
        }
        upsertAccessUser(data.user);
        closeAccessUserDialog();
        await loadAccessUsers();
        showMessageDialog(data.message || 'User added to Warehouse Inspection.', { title: 'Added', variant: 'success' });
    } catch (err) {
        console.error(err);
        showMessageDialog(formatApiError(err), { title: 'Could not add user', variant: 'error' });
    } finally {
        if (submitBtn) submitBtn.disabled = false;
    }
}

async function updateWarehouseAccessRole(selectEl) {
    const userId = Number(selectEl.dataset.userId);
    const user = accessUsers.find(item => Number(item.id) === userId);
    const previousRole = user ? accessRoleFor(user) : 'none';
    const newRole = selectEl.value;
    const label = user ? (user.full_name || user.username) : 'this user';

    if (newRole === previousRole) return;
    if (!window.confirm(`Change ${label} from ${accessRoleLabel(previousRole)} to ${accessRoleLabel(newRole)}?`)) {
        selectEl.value = previousRole;
        return;
    }

    selectEl.disabled = true;
    try {
        const response = await fetch(ACCESS_UPDATE_ROLE_URL, {
            method: 'POST',
            headers: {
                'Content-Type': 'application/json',
                'X-CSRFToken': getCSRFToken(),
            },
            body: JSON.stringify({ user_id: userId, role: newRole }),
        });
        const data = await parseJsonSafe(response);
        if (!response.ok || !data.success) throw data;

        if (data.removed || newRole === 'none') {
            removeAccessUserFromTable(userId);
        } else {
            if (newRole !== 'admin') {
                data.user.receives_report_emails = false;
                data.user.report_recipient_id = null;
            }
            upsertAccessUser(data.user);
        }
        renderAccessUsers();
        showMessageDialog(data.message || 'Warehouse Inspection role updated.', { title: data.removed ? 'Removed' : 'Updated', variant: 'success' });
    } catch (err) {
        console.error(err);
        selectEl.value = previousRole;
        selectEl.disabled = false;
        showMessageDialog(formatApiError(err), { title: 'Could not update role', variant: 'error' });
    }
}

async function removeWarehouseAccess(userId) {
    const user = accessUsers.find(item => Number(item.id) === Number(userId));
    const label = user ? (user.full_name || user.username) : 'this user';
    if (!user) return;

    if (!window.confirm(`Remove Warehouse Inspection access for ${label}?`)) {
        return;
    }

    try {
        const response = await fetch(ACCESS_REMOVE_URL, {
            method: 'POST',
            headers: {
                'Content-Type': 'application/json',
                'X-CSRFToken': getCSRFToken(),
            },
            body: JSON.stringify({ user_id: userId }),
        });
        const data = await parseJsonSafe(response);
        if (!response.ok || !data.success) throw data;

        removeAccessUserFromTable(userId);
        renderAccessUsers();
        showMessageDialog(data.message || 'Warehouse Inspection access removed.', { title: 'Removed', variant: 'success' });
    } catch (err) {
        console.error(err);
        showMessageDialog(formatApiError(err), { title: 'Could not remove access', variant: 'error' });
    }
}

async function setReportRecipient(user, enabled) {
    if (!user?.email) {
        throw new Error('User email is required.');
    }
    const url = enabled ? REPORT_RECIPIENT_ADD_URL : REPORT_RECIPIENT_REMOVE_URL;
    if (!url) {
        throw new Error('Report recipient URL is not configured.');
    }

    const response = await fetch(url, {
        method: 'POST',
        headers: {
            'Content-Type': 'application/json',
            'X-CSRFToken': getCSRFToken(),
        },
        body: JSON.stringify({
            id: user.report_recipient_id || '',
            email: user.email,
            name: user.full_name || user.username || '',
        }),
    });
    const data = await parseJsonSafe(response);
    if (!response.ok || data.error) throw data;

    if (enabled) {
        user.receives_report_emails = true;
        user.report_recipient_id = data.recipient?.id || user.report_recipient_id || null;
    } else {
        user.receives_report_emails = false;
        user.report_recipient_id = null;
    }
    return data;
}

async function toggleReportRecipient(inputEl) {
    const userId = Number(inputEl.dataset.userId);
    const user = accessUsers.find(item => Number(item.id) === userId);
    const enabled = inputEl.checked;
    if (!user || !canReceiveReportEmails(user)) {
        inputEl.checked = false;
        return;
    }

    inputEl.disabled = true;
    try {
        const data = await setReportRecipient(user, enabled);
        upsertAccessUser(user);
        renderAccessUsers();
        showMessageDialog(
            data.message || (enabled ? 'Report emails enabled.' : 'Report emails disabled.'),
            { title: enabled ? 'Reports enabled' : 'Reports disabled', variant: 'success' }
        );
    } catch (err) {
        console.error(err);
        inputEl.checked = !enabled;
        inputEl.disabled = false;
        showMessageDialog(formatApiError(err), { title: 'Could not update report emails', variant: 'error' });
    }
}

function syncAccessUserReportOption() {
    const role = document.getElementById('accessUserRole')?.value || 'user';
    const wrapper = document.getElementById('accessUserReportsWrap');
    const input = document.getElementById('accessUserReports');
    const isAdmin = role === 'admin';
    if (wrapper) wrapper.classList.toggle('is-disabled', !isAdmin);
    if (input) {
        input.disabled = !isAdmin;
        if (!isAdmin) input.checked = false;
    }
}

// ────────────────────────────────────────────────────────────────
// Dialog Functions
// ────────────────────────────────────────────────────────────────

async function openConditionDialog() {
    if (!currentBuildingId) {
        showMessageDialog('Please add and select a building before creating conditions.', { title: 'Validation', variant: 'warning' });
        return;
    }

    if (!floorsLoaded) {
        await loadFloors();
    }

    currentEditingConditionId = null;
    loadedEditingCheckpoints = [];
    document.getElementById('conditionForm').reset();
    document.getElementById('dialogTitle').textContent = 'Add New Condition';
    document.getElementById('submitBtnText').textContent = 'Save Condition';
    document.getElementById('checkpointsList').innerHTML = '';
    document.getElementById('conditionDialog').classList.add('active');
    // Close expandable sections
    document.querySelectorAll('.expandable-section').forEach(el => {
        el.classList.remove('expanded');
    });
}

function closeConditionDialog() {
    document.getElementById('conditionDialog').classList.remove('active');
    document.getElementById('conditionForm').reset();
    currentEditingConditionId = null;
    loadedEditingCheckpoints = [];
}

function closeDeleteDialog() {
    document.getElementById('deleteDialog').classList.remove('active');
    currentDeleteConditionId = null;
    currentDeleteBuildingId = null;
    currentDeleteType = 'condition';
}

// ────────────────────────────────────────────────────────────────
// Form Functions
// ────────────────────────────────────────────────────────────────

function toggleExpandable(headerEl) {
    const section = headerEl.closest('.expandable-section');
    section.classList.toggle('expanded');
}

function addCheckpoint(checkpointOrValue = '', checkpointId = '') {
    const checkpointsList = document.getElementById('checkpointsList');
    const index = checkpointsList.children.length;
    const checkpoint = typeof checkpointOrValue === 'object' && checkpointOrValue !== null
        ? checkpointOrValue
        : {
            checkpoint_name: checkpointOrValue,
            checkpoint_id: checkpointId,
            internal_department_email: '',
            floor_assignments: [],
        };
    
    const checkpointHtml = `
        <div class="form-group checkpoint-row" id="checkpoint-${index}" data-checkpoint-id="${checkpoint.checkpoint_id || ''}">
            <label>Checkpoint ${index + 1}</label>
            <div class="checkpoint-input-row">
                <input type="text" placeholder="Checkpoint name" 
                       class="checkpoint-name" data-index="${index}" value="${escapeHtml(checkpoint.checkpoint_name || '')}">
                <button type="button" class="checkpoint-delete-icon" title="Remove checkpoint"
                        aria-label="Remove checkpoint" onclick="removeCheckpoint(${index})">
                    <i class="fas fa-trash-alt"></i>
                </button>
            </div>
            <small style="color: #6b7280;">e.g., "Lighting: Ensure adequate lighting"</small>
            <div class="checkpoint-email-panel">
                <label><i class="fas fa-envelope"></i> Internal Department Email ID</label>
                <input type="email" class="checkpoint-internal-email"
                       placeholder="internal.department@merillife.com"
                       value="${escapeHtml(checkpoint.internal_department_email || '')}">

                <div class="floor-assignment-title">
                    <i class="fas fa-layer-group"></i> Floor Email IDs
                </div>
                <div class="floor-assignment-list">
                    ${renderFloorAssignmentInputs(checkpoint.floor_assignments || [])}
                </div>
            </div>
        </div>
    `;
    
    checkpointsList.insertAdjacentHTML('beforeend', checkpointHtml);
}

function renderFloorAssignmentInputs(assignments = []) {
    const activeFloors = loadedFloors.filter(floor => floor.is_active !== false);
    if (!activeFloors.length) {
        return '<div class="floor-email-empty">Add active floor masters before saving checkpoint email IDs.</div>';
    }

    const assignmentByFloorId = new Map(
        assignments.map(assignment => [String(assignment.floor), assignment])
    );

    return activeFloors.map(floor => {
        const assignment = assignmentByFloorId.get(String(floor.floor_id)) || {};
        const isApplicable = assignment.is_applicable !== false;
        return `
            <div class="floor-assignment-row" data-floor-id="${floor.floor_id}" data-assignment-id="${assignment.assignment_id || ''}">
                <label class="floor-assignment-toggle" title="${escapeHtml(floor.floor_name || '')}">
                    <input type="checkbox" class="floor-assignment-applicable" ${isApplicable ? 'checked' : ''}>
                    <span>${escapeHtml(floor.floor_name || '')}</span>
                </label>
                <input type="email" class="floor-assignment-email"
                       placeholder="${escapeHtml(floor.floor_name || 'Floor')} email ID"
                       value="${escapeHtml(assignment.responsible_email || '')}">
            </div>
        `;
    }).join('');
}

function collectFloorAssignmentsFromRow(row) {
    return Array.from(row.querySelectorAll('.floor-assignment-row')).map(assignmentRow => ({
        assignment_id: assignmentRow.dataset.assignmentId || '',
        floor: assignmentRow.dataset.floorId || '',
        is_applicable: Boolean(assignmentRow.querySelector('.floor-assignment-applicable')?.checked),
        responsible_email: assignmentRow.querySelector('.floor-assignment-email')?.value.trim() || '',
    }));
}

function refreshCheckpointFloorFields() {
    document.querySelectorAll('#checkpointsList .checkpoint-row:not(.is-removed)').forEach(row => {
        const list = row.querySelector('.floor-assignment-list');
        if (!list) return;
        list.innerHTML = renderFloorAssignmentInputs(collectFloorAssignmentsFromRow(row));
    });
}

function removeCheckpoint(index) {
    const el = document.getElementById(`checkpoint-${index}`);
    if (!el) return;

    if (el.dataset.checkpointId) {
        el.dataset.removed = 'true';
        el.classList.add('is-removed');
    } else {
        el.remove();
    }
    refreshCheckpointLabels();
}

function refreshCheckpointLabels() {
    let visibleIndex = 0;
    document.querySelectorAll('#checkpointsList .checkpoint-row').forEach((row, rowIndex) => {
        const label = row.querySelector('label');
        const input = row.querySelector('.checkpoint-name');
        row.id = `checkpoint-${rowIndex}`;
        if (!row.classList.contains('is-removed')) {
            visibleIndex += 1;
            if (label) label.textContent = `Checkpoint ${visibleIndex}`;
            if (input) input.dataset.index = visibleIndex - 1;
        }
        const button = row.querySelector('button');
        if (button) button.setAttribute('onclick', `removeCheckpoint(${rowIndex})`);
    });
}

function collectCheckpoints() {
    const rows = Array.from(
        document.querySelectorAll('#checkpointsList .checkpoint-row:not(.is-removed)')
    );
    const seen = new Set();
    const checkpoints = [];

    rows.forEach((row, index) => {
        const name = row.querySelector('.checkpoint-name')?.value.trim() || '';
        if (!name) return;

        const key = name.toLowerCase();
        if (seen.has(key)) return;
        seen.add(key);

        checkpoints.push({
            checkpoint_id: row.dataset.checkpointId || '',
            checkpoint_name: name,
            internal_department_email: row.querySelector('.checkpoint-internal-email')?.value.trim() || '',
            floor_assignments: collectFloorAssignmentsFromRow(row),
            checkpoint_order: index + 1,
        });
    });

    return checkpoints;
}

function validateCheckpointEmailMasters(checkpoints) {
    const activeFloors = loadedFloors.filter(floor => floor.is_active !== false);
    if (!activeFloors.length) {
        return 'Please add at least 1 active floor master before saving conditions.';
    }

    for (const checkpoint of checkpoints) {
        const applicableAssignments = checkpoint.floor_assignments.filter(assignment => assignment.is_applicable);
        if (!applicableAssignments.length) {
            return `Select at least 1 applicable floor for "${checkpoint.checkpoint_name}".`;
        }
    }

    return '';
}

async function saveCheckpoints(conditionId) {
    const checkpoints = collectCheckpoints();
    const activeExistingIds = new Set(
        checkpoints
            .filter(checkpoint => checkpoint.checkpoint_id)
            .map(checkpoint => String(checkpoint.checkpoint_id))
    );
    const deactivateRequests = loadedEditingCheckpoints
        .filter(checkpoint => !activeExistingIds.has(String(checkpoint.checkpoint_id)))
        .map(checkpoint => fetch(`${API_BASE}/checkpoints/${checkpoint.checkpoint_id}/`, {
            method: 'PATCH',
            headers: {
                'Content-Type': 'application/json',
                'X-CSRFToken': getCSRFToken(),
            },
            body: JSON.stringify({ is_active: false }),
        }).then(async response => {
            const data = await parseJsonSafe(response);
            if (!response.ok) {
                throw data;
            }
            return data;
        }));

    const requests = checkpoints.map(async checkpoint => {
        const isExisting = Boolean(checkpoint.checkpoint_id);
        const url = isExisting
            ? `${API_BASE}/checkpoints/${checkpoint.checkpoint_id}/`
            : `${API_BASE}/checkpoints/`;
        const method = isExisting ? 'PATCH' : 'POST';
        const payload = {
            condition_category: conditionId,
            checkpoint_name: checkpoint.checkpoint_name,
            internal_department_email: checkpoint.internal_department_email || null,
            checkpoint_order: checkpoint.checkpoint_order,
            is_active: true,
        };

        const response = await fetch(url, {
            method,
            headers: {
                'Content-Type': 'application/json',
                'X-CSRFToken': getCSRFToken(),
            },
            body: JSON.stringify(payload),
        });
        const data = await parseJsonSafe(response);
        if (!response.ok) {
            throw data;
        }

        const savedCheckpointId = data.checkpoint_id || checkpoint.checkpoint_id;
        await saveFloorAssignments(savedCheckpointId, checkpoint.floor_assignments);
        return data;
    });

    return Promise.all([...requests, ...deactivateRequests]);
}

async function saveFloorAssignments(checkpointId, assignments) {
    return Promise.all(assignments.map(assignment => saveFloorAssignment(checkpointId, assignment)));
}

async function saveFloorAssignment(checkpointId, assignment) {
    const payload = {
        checkpoint: checkpointId,
        floor: assignment.floor,
        is_applicable: assignment.is_applicable,
        responsible_email: assignment.responsible_email || null,
    };

    const assignmentId = assignment.assignment_id;
    const url = assignmentId
        ? `${API_BASE}/floor-assignments/${assignmentId}/`
        : `${API_BASE}/floor-assignments/`;
    const method = assignmentId ? 'PATCH' : 'POST';

    const response = await fetch(url, {
        method,
        headers: {
            'Content-Type': 'application/json',
            'X-CSRFToken': getCSRFToken(),
        },
        body: JSON.stringify(payload),
    });
    const data = await parseJsonSafe(response);
    if (response.ok) return data;

    if (!assignmentId) {
        const existingAssignment = await findExistingFloorAssignment(checkpointId, assignment.floor);
        if (existingAssignment?.assignment_id) {
            return saveFloorAssignment(checkpointId, {
                ...assignment,
                assignment_id: existingAssignment.assignment_id,
            });
        }
    }

    throw data;
}

async function findExistingFloorAssignment(checkpointId, floorId) {
    const response = await fetch(`${API_BASE}/floor-assignments/?checkpoint_id=${checkpointId}&floor_id=${floorId}`);
    const data = await parseJsonSafe(response);
    if (!response.ok) throw data;
    return normalizeList(data)[0] || null;
}

// ────────────────────────────────────────────────────────────────
// Building Setup Wizard
// ────────────────────────────────────────────────────────────────

async function openBuildingSetupDialog(buildingId) {
    await selectBuilding(buildingId);
    currentSetupBuildingId = String(buildingId);
    const building = getCurrentBuilding();
    document.getElementById('setupDialogTitle').innerHTML =
        `<i class="fas fa-sitemap"></i> Setup ${escapeHtml(building?.building_name || 'Building')}`;

    const conditionDetails = await Promise.all(
        loadedConditions.map(condition => fetchConditionDetail(condition.condition_category_id))
    );

    renderSetupFloors(loadedFloors);
    renderSetupConditions(conditionDetails);
    document.getElementById('buildingSetupDialog').classList.add('active');
}

function closeBuildingSetupDialog() {
    document.getElementById('buildingSetupDialog').classList.remove('active');
    document.getElementById('setupFloorsList').innerHTML = '';
    document.getElementById('setupConditionsList').innerHTML = '';
    currentSetupBuildingId = null;
    isSavingSetup = false;
    document.getElementById('setupSaveText').textContent = 'Save Setup';
}

async function fetchConditionDetail(conditionId) {
    const response = await fetch(`${API_BASE}/conditions/${conditionId}/`);
    const data = await parseJsonSafe(response);
    if (!response.ok) throw data;
    return data;
}

function renderSetupFloors(floors = []) {
    const container = document.getElementById('setupFloorsList');
    container.innerHTML = '';

    if (!floors.length) {
        addSetupFloorRow();
        return;
    }

    floors.forEach(floor => addSetupFloorRow(floor));
}

function addSetupFloorRow(floor = {}) {
    const container = document.getElementById('setupFloorsList');
    const key = floor.floor_id ? String(floor.floor_id) : `new-${Date.now()}-${Math.random().toString(16).slice(2)}`;
    const order = floor.floor_order ?? nextSetupFloorOrder();

    container.insertAdjacentHTML('beforeend', `
        <div class="setup-floor-row" data-floor-key="${key}" data-floor-id="${floor.floor_id || ''}">
            <input type="text" class="setup-floor-name" placeholder="Floor name"
                   value="${escapeHtml(floor.floor_name || '')}" oninput="refreshSetupFloorEmailGrids()" required>
            <input type="number" class="setup-floor-order" min="0" step="1"
                   value="${escapeHtml(order)}" placeholder="Order">
            <button type="button" class="checkpoint-delete-icon" title="Remove floor" onclick="removeSetupFloorRow(this)">
                <i class="fas fa-trash-alt"></i>
            </button>
        </div>
    `);

    refreshSetupFloorEmailGrids();
}

function removeSetupFloorRow(button) {
    button.closest('.setup-floor-row')?.remove();
    refreshSetupFloorEmailGrids();
}

function nextSetupFloorOrder() {
    const floors = collectSetupFloors(false);
    const maxOrder = floors.reduce((max, floor) => Math.max(max, Number(floor.floor_order || 0)), 0);
    return maxOrder + 1;
}

function collectSetupFloors(trimEmpty = true) {
    const rows = Array.from(document.querySelectorAll('#setupFloorsList .setup-floor-row'));
    return rows.map((row, index) => ({
        key: row.dataset.floorKey,
        floor_id: row.dataset.floorId || '',
        floor_name: row.querySelector('.setup-floor-name')?.value.trim() || '',
        floor_order: Number(row.querySelector('.setup-floor-order')?.value || index + 1),
    })).filter(floor => !trimEmpty || floor.floor_name);
}

function renderSetupConditions(conditions = []) {
    const container = document.getElementById('setupConditionsList');
    container.innerHTML = '';

    if (!conditions.length) {
        addSetupConditionBlock();
        return;
    }

    conditions.forEach(condition => addSetupConditionBlock(condition));
}

function addSetupConditionBlock(condition = {}, options = {}) {
    const container = document.getElementById('setupConditionsList');
    const blockKey = `condition-${Date.now()}-${Math.random().toString(16).slice(2)}`;
    const startCollapsed = options.collapsed ?? Boolean(condition.condition_category_id);
    container.insertAdjacentHTML('beforeend', `
        <div class="setup-condition-block ${startCollapsed ? 'is-collapsed' : ''}" data-condition-key="${blockKey}" data-condition-id="${condition.condition_category_id || ''}">
            <div class="setup-condition-header">
                <button type="button" class="setup-condition-toggle" title="Show or hide checklist details" onclick="toggleSetupConditionBlock(this)">
                    <i class="fas fa-chevron-down"></i>
                </button>
                <input type="text" class="setup-condition-name" placeholder="Condition name"
                       value="${escapeHtml(condition.condition_category_name || '')}" required>
                <button type="button" class="checkpoint-delete-icon" title="Remove condition" onclick="removeSetupConditionBlock(this)">
                    <i class="fas fa-trash-alt"></i>
                </button>
            </div>
            <div class="setup-checklist-list"></div>
            <div class="setup-checklist-actions" style="padding: 0 12px 12px;">
                <button type="button" class="btn btn-secondary btn-small" onclick="addSetupChecklistRow(this.closest('.setup-condition-block'))">
                    <i class="fas fa-plus-circle"></i> Add Checklist Item
                </button>
            </div>
        </div>
    `);

    const block = container.querySelector(`[data-condition-key="${blockKey}"]`);
    const checkpoints = Array.isArray(condition.checkpoints) ? condition.checkpoints : [];
    if (checkpoints.length) {
        checkpoints.forEach(checkpoint => addSetupChecklistRow(block, checkpoint));
    } else {
        addSetupChecklistRow(block);
    }
}

function toggleSetupConditionBlock(button) {
    const block = button.closest('.setup-condition-block');
    if (!block) return;
    block.classList.toggle('is-collapsed');
}

function removeSetupConditionBlock(button) {
    button.closest('.setup-condition-block')?.remove();
}

function addSetupChecklistRow(conditionBlock, checkpoint = {}) {
    if (!conditionBlock) return;
    const list = conditionBlock.querySelector('.setup-checklist-list');
    const rowKey = `item-${Date.now()}-${Math.random().toString(16).slice(2)}`;
    list.insertAdjacentHTML('beforeend', `
        <div class="setup-checklist-row" data-checkpoint-key="${rowKey}" data-checkpoint-id="${checkpoint.checkpoint_id || ''}">
            <div class="setup-checklist-main">
                <input type="text" class="setup-checkpoint-name" placeholder="Checklist item"
                       value="${escapeHtml(checkpoint.checkpoint_name || '')}" required>
                <input type="email" class="setup-internal-email" placeholder="Internal department email"
                       value="${escapeHtml(checkpoint.internal_department_email || '')}">
                <button type="button" class="checkpoint-delete-icon" title="Remove checklist item" onclick="removeSetupChecklistRow(this)">
                    <i class="fas fa-trash-alt"></i>
                </button>
            </div>
            <div class="setup-floor-email-grid">
                ${renderSetupFloorEmailInputs(checkpoint.floor_assignments || [])}
            </div>
        </div>
    `);
}

function removeSetupChecklistRow(button) {
    button.closest('.setup-checklist-row')?.remove();
}

function renderSetupFloorEmailInputs(assignments = []) {
    const floors = collectSetupFloors();
    if (!floors.length) {
        return '<div class="setup-empty">Add floors before entering floor email IDs.</div>';
    }

    const assignmentByFloorId = new Map(
        assignments
            .filter(assignment => assignment.floor)
            .map(assignment => [String(assignment.floor), assignment])
    );

    return floors.map(floor => {
        const assignment = assignmentByFloorId.get(String(floor.floor_id || floor.key)) || {};
        const isApplicable = assignment.is_applicable !== false;
        return `
            <div class="floor-assignment-row" data-floor-key="${floor.key}" data-assignment-id="${assignment.assignment_id || ''}">
                <label class="floor-assignment-toggle" title="${escapeHtml(floor.floor_name || '')}">
                    <input type="checkbox" class="setup-floor-applicable" ${isApplicable ? 'checked' : ''}>
                    <span>${escapeHtml(floor.floor_name || 'Floor')}</span>
                </label>
                <input type="email" class="setup-floor-email"
                       placeholder="${escapeHtml(floor.floor_name || 'Floor')} email ID"
                       value="${escapeHtml(assignment.responsible_email || '')}">
            </div>
        `;
    }).join('');
}

function collectSetupFloorAssignmentsFromGrid(grid) {
    return Array.from(grid.querySelectorAll('.floor-assignment-row')).map(row => ({
        assignment_id: row.dataset.assignmentId || '',
        floor_key: row.dataset.floorKey || '',
        is_applicable: Boolean(row.querySelector('.setup-floor-applicable')?.checked),
        responsible_email: row.querySelector('.setup-floor-email')?.value.trim() || '',
    }));
}

function refreshSetupFloorEmailGrids() {
    document.querySelectorAll('.setup-floor-email-grid').forEach(grid => {
        const currentAssignments = collectSetupFloorAssignmentsFromGrid(grid);
        grid.innerHTML = renderSetupFloorEmailInputs(currentAssignments.map(assignment => ({
            assignment_id: assignment.assignment_id,
            floor: assignment.floor_key,
            is_applicable: assignment.is_applicable,
            responsible_email: assignment.responsible_email,
        })));
    });
}

function collectBuildingSetup() {
    const floors = collectSetupFloors();
    const conditions = Array.from(document.querySelectorAll('#setupConditionsList .setup-condition-block')).map((block, conditionIndex) => ({
        condition_id: block.dataset.conditionId || '',
        condition_name: block.querySelector('.setup-condition-name')?.value.trim() || '',
        condition_order: conditionIndex + 1,
        checkpoints: Array.from(block.querySelectorAll('.setup-checklist-row')).map((row, checkpointIndex) => ({
            checkpoint_id: row.dataset.checkpointId || '',
            checkpoint_name: row.querySelector('.setup-checkpoint-name')?.value.trim() || '',
            internal_department_email: row.querySelector('.setup-internal-email')?.value.trim() || '',
            checkpoint_order: checkpointIndex + 1,
            floor_assignments: collectSetupFloorAssignmentsFromGrid(row.querySelector('.setup-floor-email-grid')),
        })),
    })).filter(condition => condition.condition_name || condition.checkpoints.some(checkpoint => checkpoint.checkpoint_name));

    return { floors, conditions };
}

function validateBuildingSetup(setup) {
    if (!setup.floors.length) return 'Add at least 1 floor.';

    const floorNames = new Set();
    for (const floor of setup.floors) {
        const key = floor.floor_name.toLowerCase();
        if (floorNames.has(key)) return `Floor "${floor.floor_name}" is duplicated.`;
        floorNames.add(key);
    }

    if (!setup.conditions.length) return 'Add at least 1 condition.';

    const conditionNames = new Set();
    for (const condition of setup.conditions) {
        if (!condition.condition_name) return 'Condition name is required.';
        const conditionKey = condition.condition_name.toLowerCase();
        if (conditionNames.has(conditionKey)) return `Condition "${condition.condition_name}" is duplicated.`;
        conditionNames.add(conditionKey);
        if (!condition.checkpoints.length) return `Add at least 1 checklist item for "${condition.condition_name}".`;

        for (const checkpoint of condition.checkpoints) {
            if (!checkpoint.checkpoint_name) return `Checklist item name is required for "${condition.condition_name}".`;

            const applicableAssignments = checkpoint.floor_assignments.filter(assignment => assignment.is_applicable);
            if (!applicableAssignments.length) return `Select at least 1 applicable floor for "${checkpoint.checkpoint_name}".`;
        }
    }

    return '';
}

async function submitBuildingSetupForm(event) {
    event.preventDefault();

    if (!currentSetupBuildingId || isSavingSetup) return;

    const setup = collectBuildingSetup();
    const validationError = validateBuildingSetup(setup);
    if (validationError) {
        showMessageDialog(validationError, { title: 'Validation', variant: 'warning' });
        return;
    }

    isSavingSetup = true;
    document.getElementById('setupSaveText').textContent = 'Saving...';

    try {
        const floorIdByKey = new Map();
        for (const floor of setup.floors) {
            const savedFloor = await saveSetupFloor(floor);
            floorIdByKey.set(floor.key, savedFloor.floor_id || floor.floor_id);
        }

        for (const condition of setup.conditions) {
            const savedCondition = await saveSetupCondition(condition);
            const conditionId = savedCondition.condition_category_id || condition.condition_id;

            for (const checkpoint of condition.checkpoints) {
                const savedCheckpoint = await saveSetupCheckpoint(conditionId, checkpoint);
                const checkpointId = savedCheckpoint.checkpoint_id || checkpoint.checkpoint_id;
                const mappedAssignments = checkpoint.floor_assignments.map(assignment => ({
                    assignment_id: assignment.assignment_id,
                    floor: floorIdByKey.get(assignment.floor_key),
                    is_applicable: assignment.is_applicable,
                    responsible_email: assignment.responsible_email,
                })).filter(assignment => assignment.floor);

                await saveFloorAssignments(checkpointId, mappedAssignments);
            }
        }

        await Promise.all([loadFloors(), loadConditions()]);
        closeBuildingSetupDialog();
        showMessageDialog('Building setup saved successfully!', { title: 'Saved', variant: 'success' });
    } catch (err) {
        console.error(err);
        showMessageDialog(formatApiError(err), { title: 'Could not save setup', variant: 'error' });
    } finally {
        isSavingSetup = false;
        document.getElementById('setupSaveText').textContent = 'Save Setup';
    }
}

async function saveSetupFloor(floor) {
    const url = floor.floor_id
        ? `${API_BASE}/floors/${floor.floor_id}/`
        : `${API_BASE}/floors/`;
    const method = floor.floor_id ? 'PATCH' : 'POST';
    const response = await fetch(url, {
        method,
        headers: {
            'Content-Type': 'application/json',
            'X-CSRFToken': getCSRFToken(),
        },
        body: JSON.stringify({
            building: Number(currentSetupBuildingId),
            floor_name: floor.floor_name,
            floor_order: floor.floor_order,
            is_active: true,
        }),
    });
    const data = await parseJsonSafe(response);
    if (!response.ok) throw data;
    return data;
}

async function saveSetupCondition(condition) {
    const url = condition.condition_id
        ? `${API_BASE}/conditions/${condition.condition_id}/`
        : `${API_BASE}/conditions/`;
    const method = condition.condition_id ? 'PATCH' : 'POST';
    const response = await fetch(url, {
        method,
        headers: {
            'Content-Type': 'application/json',
            'X-CSRFToken': getCSRFToken(),
        },
        body: JSON.stringify({
            building: Number(currentSetupBuildingId),
            condition_category_name: condition.condition_name,
        }),
    });
    const data = await parseJsonSafe(response);
    if (!response.ok) throw data;
    return data;
}

async function saveSetupCheckpoint(conditionId, checkpoint) {
    const url = checkpoint.checkpoint_id
        ? `${API_BASE}/checkpoints/${checkpoint.checkpoint_id}/`
        : `${API_BASE}/checkpoints/`;
    const method = checkpoint.checkpoint_id ? 'PATCH' : 'POST';
    const response = await fetch(url, {
        method,
        headers: {
            'Content-Type': 'application/json',
            'X-CSRFToken': getCSRFToken(),
        },
        body: JSON.stringify({
            condition_category: conditionId,
            checkpoint_name: checkpoint.checkpoint_name,
            internal_department_email: checkpoint.internal_department_email,
            checkpoint_order: checkpoint.checkpoint_order,
            is_active: true,
        }),
    });
    const data = await parseJsonSafe(response);
    if (!response.ok) throw data;
    return data;
}

async function submitConditionForm(event) {
    event.preventDefault();

    if (isSavingCondition) return;

    if (!currentBuildingId) {
        showMessageDialog('Please select a building before saving this condition.', { title: 'Validation', variant: 'warning' });
        return;
    }
    
    const formData = {
        building: Number(currentBuildingId),
        condition_category_name: document.getElementById('conditionName').value.trim(),
    };
    
    if (!formData.condition_category_name.trim()) {
        showMessageDialog('Condition name is required', { title: 'Validation', variant: 'warning' });
        return;
    }

    const checkpoints = collectCheckpoints();
    if (checkpoints.length === 0) {
        showMessageDialog(
            'Please add at least 1 checkpoint before saving this condition.',
            { title: 'Validation', variant: 'warning' }
        );
        return;
    }

    const emailMasterError = validateCheckpointEmailMasters(checkpoints);
    if (emailMasterError) {
        showMessageDialog(emailMasterError, { title: 'Validation', variant: 'warning' });
        return;
    }

    isSavingCondition = true;
    setConditionFormSaving(true);
    
    const duplicateCondition = loadedConditions.find(cond => 
        String(cond.condition_category_name || '').trim().toLowerCase() === formData.condition_category_name.toLowerCase()
        && Number(cond.building) === Number(currentBuildingId)
        && Number(cond.condition_category_id) !== Number(currentEditingConditionId)
    );
    if (duplicateCondition) {
        showMessageDialog(
            `Condition "${formData.condition_category_name}" already exists. Use Edit to update it.`,
            { title: 'Duplicate condition', variant: 'warning' }
        );
        editCondition(duplicateCondition.condition_category_id);
        isSavingCondition = false;
        setConditionFormSaving(false);
        return;
    }
    
    // Create or update condition
    const url = currentEditingConditionId 
        ? `${API_BASE}/conditions/${currentEditingConditionId}/`
        : `${API_BASE}/conditions/`;
    
    const method = currentEditingConditionId ? 'PATCH' : 'POST';

    try {
        const response = await fetch(url, {
            method: method,
            headers: {
                'Content-Type': 'application/json',
                'X-CSRFToken': getCSRFToken(),
            },
            body: JSON.stringify(formData)
        });
        const data = await parseJsonSafe(response);
        if (!response.ok) {
            throw data;
        }

        const conditionId = await resolveSavedConditionId(data, formData.condition_category_name);
        await saveCheckpoints(conditionId);
        showMessageDialog(
            data.already_exists ? 'Condition already exists. Checkpoints saved.' : 'Condition saved successfully!',
            { title: 'Saved', variant: 'success' }
        );
        closeConditionDialog();
        loadConditions();
    } catch (err) {
        console.error(err);
        showMessageDialog(formatApiError(err), { title: 'Could not save', variant: 'error' });
    } finally {
        isSavingCondition = false;
        setConditionFormSaving(false);
    }
}

function setConditionFormSaving(isSaving) {
    const submitBtn = document.querySelector('#conditionForm button[type="submit"]');
    const submitText = document.getElementById('submitBtnText');
    if (!submitBtn || !submitText) return;

    submitBtn.disabled = isSaving;
    submitText.textContent = isSaving
        ? 'Saving...'
        : (currentEditingConditionId ? 'Update Condition' : 'Save Condition');
}

async function resolveSavedConditionId(data, conditionName) {
    const directId = data?.condition_category_id || data?.id;
    if (directId) return directId;

    const response = await fetch(`${API_BASE}/conditions/?building_id=${currentBuildingId || ''}`);
    const payload = await parseJsonSafe(response);
    if (!response.ok) throw payload;

    const conditions = sortConditionsOldestFirst(normalizeList(payload));
    loadedConditions = conditions;
    const matchedCondition = conditions.find(condition =>
        String(condition.condition_category_name || '').trim().toLowerCase() === conditionName.toLowerCase()
        && Number(condition.building) === Number(currentBuildingId)
    );

    const resolvedId = matchedCondition?.condition_category_id || matchedCondition?.id;
    if (resolvedId) return resolvedId;

    throw { detail: 'Condition was saved, but its ID could not be found for checkpoint saving. Please refresh and try again.' };
}

// ────────────────────────────────────────────────────────────────
// Building Master Functions
// ────────────────────────────────────────────────────────────────

async function loadBuildings(preferredBuildingId = currentBuildingId) {
    try {
        const response = await fetch(`${API_BASE}/buildings/`);
        const data = await parseJsonSafe(response);
        if (!response.ok) throw data;

        loadedBuildings = sortBuildings(normalizeList(data));
        const selectedExists = loadedBuildings.some(building => String(building.building_id) === String(preferredBuildingId));
        currentBuildingId = selectedExists
            ? String(preferredBuildingId)
            : (loadedBuildings[0]?.building_id ? String(loadedBuildings[0].building_id) : null);

        renderBuildingSelect();
        renderBuildingsTable();
        updateSelectedBuildingLabels();
        await Promise.all([loadFloors(), loadConditions()]);
    } catch (err) {
        console.error(err);
        document.getElementById('buildingsTableContainer').innerHTML =
            '<p style="color: #ef4444;">Error loading buildings</p>';
    }
}

function sortBuildings(buildings) {
    return [...buildings].sort((a, b) => {
        const orderDiff = Number(a.building_order || 0) - Number(b.building_order || 0);
        if (orderDiff !== 0) return orderDiff;
        return String(a.building_name || '').localeCompare(String(b.building_name || ''));
    });
}

function getCurrentBuilding() {
    return loadedBuildings.find(building => String(building.building_id) === String(currentBuildingId)) || null;
}

function renderBuildingSelect() {
    const select = document.getElementById('buildingSelect');
    if (!select) return;

    select.innerHTML = '<option value="">Select building</option>';
    loadedBuildings.forEach(building => {
        select.insertAdjacentHTML(
            'beforeend',
            `<option value="${building.building_id}">${escapeHtml(building.building_name || '')}${building.is_active ? '' : ' (Inactive)'}</option>`
        );
    });
    select.value = currentBuildingId || '';
}

function updateSelectedBuildingLabels() {
    const building = getCurrentBuilding();
    const labelText = building ? `- ${building.building_name}` : '';
    const floorLabel = document.getElementById('floorBuildingLabel');
    const conditionLabel = document.getElementById('conditionBuildingLabel');
    if (floorLabel) floorLabel.textContent = labelText;
    if (conditionLabel) conditionLabel.textContent = labelText;
}

async function handleBuildingSelectionChange() {
    currentBuildingId = document.getElementById('buildingSelect').value || null;
    floorsLoaded = false;
    resetFloorForm();
    updateSelectedBuildingLabels();
    await Promise.all([loadFloors(), loadConditions()]);
}

function renderBuildingsTable() {
    const container = document.getElementById('buildingsTableContainer');
    if (!container) return;

    if (!loadedBuildings.length) {
        container.innerHTML = `
            <div style="padding: 24px; text-align: center; color: #64748b;">
                <i class="fas fa-warehouse" style="font-size: 2em; color: #cbd5e1; margin-bottom: 10px; display:block;"></i>
                No building masters added yet
            </div>
        `;
        return;
    }

    const rows = loadedBuildings.map(building => {
        const statusClass = building.is_active ? 'is-active' : 'is-inactive';
        const statusText = building.is_active ? 'Active' : 'Inactive';
        const toggleText = building.is_active ? 'Deactivate' : 'Activate';
        const toggleIcon = building.is_active ? 'fa-eye-slash' : 'fa-eye';
        const deleteNameArg = jsStringForHtmlAttribute(building.building_name || '');
        const rowStyle = String(building.building_id) === String(currentBuildingId)
            ? 'background:#eff6ff;'
            : '';
        return `
            <tr style="${rowStyle}">
                <td><strong>${escapeHtml(building.building_name || '')}</strong></td>
                <td>${Number(building.building_order || 0)}</td>
                <td><span class="floor-status ${statusClass}">${statusText}</span></td>
                <td>
                    <div class="floor-actions">
                        <button type="button" class="btn btn-primary btn-small" onclick="selectBuilding(${building.building_id})" title="Select building">
                            <i class="fas fa-check-circle"></i>
                        </button>
                        <button type="button" class="btn btn-primary btn-small" onclick="openBuildingSetupDialog(${building.building_id})" title="Setup building">
                            <i class="fas fa-sitemap"></i>
                        </button>
                        <button type="button" class="btn btn-primary btn-small" onclick="editBuildingMaster(${building.building_id})" title="Edit building">
                            <i class="fas fa-pencil-alt"></i>
                        </button>
                        <button type="button" class="btn btn-secondary btn-small" onclick="toggleBuildingActive(${building.building_id}, ${building.is_active ? 'false' : 'true'})" title="${toggleText} building">
                            <i class="fas ${toggleIcon}"></i> ${toggleText}
                        </button>
                        <button type="button" class="btn btn-danger btn-small" onclick="deleteBuilding(${building.building_id}, ${deleteNameArg})" title="Delete building">
                            <i class="fas fa-trash-alt"></i>
                        </button>
                    </div>
                </td>
            </tr>
        `;
    }).join('');

    container.innerHTML = `
        <div class="table-responsive-wrap" style="padding: 0;">
            <table class="conditions-table">
                <thead>
                    <tr>
                        <th>Building Name</th>
                        <th style="width: 120px;">Order</th>
                        <th style="width: 120px;">Status</th>
                        <th style="width: 380px;">Actions</th>
                    </tr>
                </thead>
                <tbody>${rows}</tbody>
            </table>
        </div>
    `;
}

async function selectBuilding(buildingId) {
    currentBuildingId = String(buildingId);
    renderBuildingSelect();
    updateSelectedBuildingLabels();
    floorsLoaded = false;
    resetFloorForm();
    await Promise.all([loadFloors(), loadConditions()]);
    renderBuildingsTable();
}

async function submitBuildingForm(event) {
    event.preventDefault();

    const buildingName = document.getElementById('buildingName').value.trim();
    const buildingOrderInput = document.getElementById('buildingOrder').value;
    const buildingOrder = buildingOrderInput === ''
        ? nextBuildingOrder()
        : Number(buildingOrderInput);

    if (!buildingName) {
        showMessageDialog('Building name is required.', { title: 'Validation', variant: 'warning' });
        return;
    }

    const duplicateBuilding = loadedBuildings.find(building =>
        String(building.building_name || '').trim().toLowerCase() === buildingName.toLowerCase()
    );
    if (duplicateBuilding) {
        showMessageDialog(`Building "${buildingName}" already exists.`, { title: 'Duplicate building', variant: 'warning' });
        return;
    }

    try {
        const response = await fetch(`${API_BASE}/buildings/`, {
            method: 'POST',
            headers: {
                'Content-Type': 'application/json',
                'X-CSRFToken': getCSRFToken(),
            },
            body: JSON.stringify({
                building_name: buildingName,
                building_order: Number.isNaN(buildingOrder) ? nextBuildingOrder() : buildingOrder,
                is_active: true,
            }),
        });
        const data = await parseJsonSafe(response);
        if (!response.ok) throw data;

        const savedBuildingId = data.building_id || currentBuildingId;
        resetBuildingForm();
        await loadBuildings(savedBuildingId);
        await openBuildingSetupDialog(savedBuildingId);
    } catch (err) {
        console.error(err);
        showMessageDialog(formatApiError(err), { title: 'Could not save building', variant: 'error' });
    }
}

function nextBuildingOrder() {
    const maxOrder = loadedBuildings.reduce((max, building) => Math.max(max, Number(building.building_order || 0)), 0);
    return maxOrder + 1;
}

function editBuildingMaster(buildingId) {
    const building = loadedBuildings.find(item => Number(item.building_id) === Number(buildingId));
    if (!building) return;

    currentEditingBuildingId = building.building_id;
    document.getElementById('updateBuildingName').value = building.building_name || '';
    document.getElementById('updateBuildingOrder').value = building.building_order || '';
    document.getElementById('buildingUpdateDialog').classList.add('active');
}

function closeBuildingUpdateDialog() {
    document.getElementById('buildingUpdateDialog').classList.remove('active');
    document.getElementById('buildingUpdateForm').reset();
    currentEditingBuildingId = null;
}

async function submitBuildingUpdateForm(event) {
    event.preventDefault();

    if (!currentEditingBuildingId) return;

    const buildingName = document.getElementById('updateBuildingName').value.trim();
    const buildingOrderInput = document.getElementById('updateBuildingOrder').value;
    const buildingOrder = buildingOrderInput === ''
        ? nextBuildingOrder()
        : Number(buildingOrderInput);

    if (!buildingName) {
        showMessageDialog('Building name is required.', { title: 'Validation', variant: 'warning' });
        return;
    }

    const duplicateBuilding = loadedBuildings.find(building =>
        String(building.building_name || '').trim().toLowerCase() === buildingName.toLowerCase()
        && Number(building.building_id) !== Number(currentEditingBuildingId)
    );
    if (duplicateBuilding) {
        showMessageDialog(`Building "${buildingName}" already exists.`, { title: 'Duplicate building', variant: 'warning' });
        return;
    }

    const buildingId = currentEditingBuildingId;

    try {
        const response = await fetch(`${API_BASE}/buildings/${buildingId}/`, {
            method: 'PATCH',
            headers: {
                'Content-Type': 'application/json',
                'X-CSRFToken': getCSRFToken(),
            },
            body: JSON.stringify({
                building_name: buildingName,
                building_order: Number.isNaN(buildingOrder) ? nextBuildingOrder() : buildingOrder,
            }),
        });
        const data = await parseJsonSafe(response);
        if (!response.ok) throw data;

        closeBuildingUpdateDialog();
        await loadBuildings(buildingId);
        showMessageDialog('Building master updated successfully!', { title: 'Updated', variant: 'success' });
    } catch (err) {
        console.error(err);
        showMessageDialog(formatApiError(err), { title: 'Could not update building', variant: 'error' });
    }
}

function resetBuildingForm() {
    document.getElementById('buildingForm').reset();
    document.getElementById('buildingSubmitText').textContent = 'Add Building';
}

async function toggleBuildingActive(buildingId, isActive) {
    try {
        const response = await fetch(`${API_BASE}/buildings/${buildingId}/`, {
            method: 'PATCH',
            headers: {
                'Content-Type': 'application/json',
                'X-CSRFToken': getCSRFToken(),
            },
            body: JSON.stringify({ is_active: isActive }),
        });
        const data = await parseJsonSafe(response);
        if (!response.ok) throw data;

        await loadBuildings(currentBuildingId);
    } catch (err) {
        console.error(err);
        showMessageDialog(formatApiError(err), { title: 'Could not update building', variant: 'error' });
    }
}

// ────────────────────────────────────────────────────────────────
// Floor Master Functions
// ────────────────────────────────────────────────────────────────

async function loadFloors() {
    if (!currentBuildingId) {
        loadedFloors = [];
        floorsLoaded = true;
        renderFloorsTable();
        refreshCheckpointFloorFields();
        return;
    }

    try {
        const response = await fetch(`${API_BASE}/floors/?building_id=${currentBuildingId}`);
        const data = await parseJsonSafe(response);
        if (!response.ok) throw data;

        loadedFloors = sortFloors(normalizeList(data));
        floorsLoaded = true;
        renderFloorsTable();
        refreshCheckpointFloorFields();
    } catch (err) {
        console.error(err);
        const container = document.getElementById('floorsTableContainer');
        if (container) container.innerHTML = '<p style="color: #ef4444;">Error loading floors</p>';
    }
}

function sortFloors(floors) {
    return [...floors].sort((a, b) => {
        const orderDiff = Number(a.floor_order || 0) - Number(b.floor_order || 0);
        if (orderDiff !== 0) return orderDiff;
        return String(a.floor_name || '').localeCompare(String(b.floor_name || ''));
    });
}

function renderFloorsTable() {
    const container = document.getElementById('floorsTableContainer');
    if (!container) return;

    if (!loadedFloors.length) {
        container.innerHTML = `
            <div style="padding: 24px; text-align: center; color: #64748b;">
                <i class="fas fa-layer-group" style="font-size: 2em; color: #cbd5e1; margin-bottom: 10px; display:block;"></i>
                ${currentBuildingId ? 'No floor masters added yet' : 'Select a building first'}
            </div>
        `;
        return;
    }

    const rows = loadedFloors.map(floor => {
        const statusClass = floor.is_active ? 'is-active' : 'is-inactive';
        const statusText = floor.is_active ? 'Active' : 'Inactive';
        const toggleText = floor.is_active ? 'Deactivate' : 'Activate';
        const toggleIcon = floor.is_active ? 'fa-eye-slash' : 'fa-eye';
        return `
            <tr>
                <td><strong>${escapeHtml(floor.floor_name || '')}</strong></td>
                <td>${Number(floor.floor_order || 0)}</td>
                <td><span class="floor-status ${statusClass}">${statusText}</span></td>
                <td>
                    <div class="floor-actions">
                        <button type="button" class="btn btn-primary btn-small" onclick="editFloorMaster(${floor.floor_id})" title="Edit floor">
                            <i class="fas fa-pencil-alt"></i>
                        </button>
                        <button type="button" class="btn btn-secondary btn-small" onclick="toggleFloorActive(${floor.floor_id}, ${floor.is_active ? 'false' : 'true'})" title="${toggleText} floor">
                            <i class="fas ${toggleIcon}"></i> ${toggleText}
                        </button>
                    </div>
                </td>
            </tr>
        `;
    }).join('');

    container.innerHTML = `
        <div class="table-responsive-wrap" style="padding: 0;">
            <table class="conditions-table">
                <thead>
                    <tr>
                        <th>Floor Name</th>
                        <th style="width: 120px;">Order</th>
                        <th style="width: 120px;">Status</th>
                        <th style="width: 210px;">Actions</th>
                    </tr>
                </thead>
                <tbody>${rows}</tbody>
            </table>
        </div>
    `;
}

async function submitFloorForm(event) {
    event.preventDefault();

    if (!currentBuildingId) {
        showMessageDialog('Please select a building before saving floors.', { title: 'Validation', variant: 'warning' });
        return;
    }

    const floorNameEl = document.getElementById('floorName');
    const floorOrderEl = document.getElementById('floorOrder');
    const floorName = floorNameEl.value.trim();
    const floorOrder = floorOrderEl.value === ''
        ? nextFloorOrder()
        : Number(floorOrderEl.value);

    if (!floorName) {
        showMessageDialog('Floor name is required.', { title: 'Validation', variant: 'warning' });
        return;
    }

    const duplicateFloor = loadedFloors.find(floor =>
        String(floor.floor_name || '').trim().toLowerCase() === floorName.toLowerCase()
        && Number(floor.floor_id) !== Number(currentEditingFloorId)
    );
    if (duplicateFloor) {
        showMessageDialog(`Floor "${floorName}" already exists.`, { title: 'Duplicate floor', variant: 'warning' });
        return;
    }

    const url = currentEditingFloorId
        ? `${API_BASE}/floors/${currentEditingFloorId}/`
        : `${API_BASE}/floors/`;
    const method = currentEditingFloorId ? 'PATCH' : 'POST';

    try {
        const response = await fetch(url, {
            method,
            headers: {
                'Content-Type': 'application/json',
                'X-CSRFToken': getCSRFToken(),
            },
            body: JSON.stringify({
                building: Number(currentBuildingId),
                floor_name: floorName,
                floor_order: Number.isNaN(floorOrder) ? nextFloorOrder() : floorOrder,
                is_active: true,
            }),
        });
        const data = await parseJsonSafe(response);
        if (!response.ok) throw data;

        resetFloorForm();
        await loadFloors();
        showMessageDialog('Floor master saved successfully!', { title: 'Saved', variant: 'success' });
    } catch (err) {
        console.error(err);
        showMessageDialog(formatApiError(err), { title: 'Could not save floor', variant: 'error' });
    }
}

function nextFloorOrder() {
    const maxOrder = loadedFloors.reduce((max, floor) => Math.max(max, Number(floor.floor_order || 0)), 0);
    return maxOrder + 1;
}

function editFloorMaster(floorId) {
    const floor = loadedFloors.find(item => Number(item.floor_id) === Number(floorId));
    if (!floor) return;

    currentEditingFloorId = floor.floor_id;
    document.getElementById('floorName').value = floor.floor_name || '';
    document.getElementById('floorOrder').value = floor.floor_order || '';
    document.getElementById('floorSubmitText').textContent = 'Update Floor';
    document.getElementById('floorCancelEdit').style.display = '';
}

function resetFloorForm() {
    currentEditingFloorId = null;
    const form = document.getElementById('floorForm');
    const submitText = document.getElementById('floorSubmitText');
    const cancelEdit = document.getElementById('floorCancelEdit');
    if (form) form.reset();
    if (submitText) submitText.textContent = 'Add Floor';
    if (cancelEdit) cancelEdit.style.display = 'none';
}

async function toggleFloorActive(floorId, isActive) {
    try {
        const response = await fetch(`${API_BASE}/floors/${floorId}/`, {
            method: 'PATCH',
            headers: {
                'Content-Type': 'application/json',
                'X-CSRFToken': getCSRFToken(),
            },
            body: JSON.stringify({ is_active: isActive }),
        });
        const data = await parseJsonSafe(response);
        if (!response.ok) throw data;

        await loadFloors();
    } catch (err) {
        console.error(err);
        showMessageDialog(formatApiError(err), { title: 'Could not update floor', variant: 'error' });
    }
}

// ────────────────────────────────────────────────────────────────
// Data Loading Functions
// ────────────────────────────────────────────────────────────────

function loadConditions() {
    if (!currentBuildingId) {
        loadedConditions = [];
        renderConditionsTable(loadedConditions);
        return Promise.resolve();
    }

    return fetch(`${API_BASE}/conditions/?building_id=${currentBuildingId}`)
        .then(async r => {
            const data = await parseJsonSafe(r);
            if (!r.ok) throw data;
            return data;
        })
        .then(data => {
            loadedConditions = sortConditionsOldestFirst(normalizeList(data));
            renderConditionsTable(loadedConditions);
        })
        .catch(err => {
            console.error(err);
            const container = document.getElementById('conditionsTableContainer');
            if (container) container.innerHTML = '<p style="color: #ef4444;">Error loading conditions</p>';
        });
}

function normalizeList(data) {
    if (Array.isArray(data)) return data;
    if (data && Array.isArray(data.results)) return data.results;
    return [];
}

function sortConditionsOldestFirst(conditions) {
    return [...conditions].sort((a, b) => {
        const aTime = Date.parse(a.created_at || '');
        const bTime = Date.parse(b.created_at || '');

        if (!Number.isNaN(aTime) && !Number.isNaN(bTime) && aTime !== bTime) {
            return aTime - bTime;
        }

        return Number(a.condition_category_id || 0) - Number(b.condition_category_id || 0);
    });
}

function formatApiError(error) {
    if (!error) return 'Something went wrong.';
    if (typeof error === 'string') return error;
    if (error.error) return error.error;
    if (error.detail) return error.detail;
    if (error.non_field_errors) {
        return [].concat(error.non_field_errors).join(' ');
    }
    if (error.condition_category_name) {
        return `Condition name: ${[].concat(error.condition_category_name).join(' ')}`;
    }
    if (error.floor_name) {
        return `Floor name: ${[].concat(error.floor_name).join(' ')}`;
    }
    if (error.building_name) {
        return `Building name: ${[].concat(error.building_name).join(' ')}`;
    }
    if (error.building) {
        return `Building: ${[].concat(error.building).join(' ')}`;
    }
    if (error.internal_department_email) {
        return `Internal Department Email ID: ${[].concat(error.internal_department_email).join(' ')}`;
    }
    if (error.responsible_email) {
        return `Floor Email ID: ${[].concat(error.responsible_email).join(' ')}`;
    }
    if (error instanceof Error) return error.message || 'Something went wrong.';
    return 'Error: ' + JSON.stringify(error);
}

async function parseJsonSafe(response) {
    const contentType = response.headers.get('content-type') || '';
    const text = await response.text();
    if (!text) return {};
    if (contentType.includes('application/json')) {
        try {
            return JSON.parse(text);
        } catch (e) {
            return { detail: text };
        }
    }
    return { detail: text };
}

function renderConditionsTable(conditions) {
    const container = document.getElementById('conditionsTableContainer');
    if (!container) return;

    if (!conditions || conditions.length === 0) {
        const emptyText = currentBuildingId ? 'No conditions found' : 'Select a building first';
        container.innerHTML = `<div style="padding: 60px 20px; text-align: center;"><i class="fas fa-inbox" style="font-size: 3em; color: #cbd5e1; margin-bottom: 16px; display: block;"></i><p style="color: #64748b; font-size: 1.05em;">${emptyText}</p></div>`;
        return;
    }
    
    let html = `
        <table class="conditions-table">
            <thead>
                <tr>
                    <th>Condition Name</th>
                    <th style="width: 200px;">Checkpoints</th>
                    <th>Created By</th>
                    <th style="width: 120px;">Created Date</th>
                    <th style="width: 180px;">Actions</th>
                </tr>
            </thead>
            <tbody>
    `;
    
    conditions.forEach((cond, index) => {
        const createdDate = new Date(cond.created_at).toLocaleDateString();
        const checkpoints = Array.isArray(cond.checkpoints) ? cond.checkpoints : [];
        const checkpointCount = cond.checkpoint_count || checkpoints.length || 0;

        const conditionName = escapeHtml(cond.condition_category_name);
        const createdBy = escapeHtml(cond.created_by || '');
        const deleteNameArg = jsStringForHtmlAttribute(cond.condition_category_name || '');
        const rowClass = index % 2 === 0 ? 'even-row' : 'odd-row';

        html += `
            <tr data-condition-row-id="${cond.condition_category_id}" class="${rowClass}">
                <td>
                    <span style="font-weight: 700; font-size: 1em; color: #1a202c;">${conditionName}</span>
                </td>
                <td>
                    <span class="checkpoint-count" title="${checkpointCount} checkpoint${checkpointCount !== 1 ? 's' : ''}">${checkpointCount}</span>
                </td>
                <td><span style="color: #64748b; font-size: 0.95em;">${createdBy}</span></td>
                <td><span style="color: #6b7280; font-weight: 500;">${createdDate}</span></td>
                <td>
                    <div style="display: flex; gap: 8px;">
                        <button class="btn btn-primary btn-small" onclick="editCondition(${cond.condition_category_id})" title="Edit condition">
                            <i class="fas fa-pencil-alt"></i>
                        </button>
                        <button class="btn btn-danger btn-small" onclick="deleteCondition(${cond.condition_category_id}, ${deleteNameArg})" title="Delete condition">
                            <i class="fas fa-trash-alt"></i>
                        </button>
                    </div>
                </td>
            </tr>
        `;
    });

    html += `</tbody></table>`;
    container.innerHTML = html;
}

async function editCondition(conditionId) {
    try {
        if (!floorsLoaded) {
            await loadFloors();
        }

        const response = await fetch(`${API_BASE}/conditions/${conditionId}/`);
        const data = await parseJsonSafe(response);
        if (!response.ok) throw data;

        currentEditingConditionId = conditionId;
        document.getElementById('conditionName').value = data.condition_category_name;
        document.getElementById('checkpointsList').innerHTML = '';
        loadedEditingCheckpoints = data.checkpoints || [];
        (data.checkpoints || []).forEach(checkpoint => {
            addCheckpoint(checkpoint);
        });
        document.getElementById('dialogTitle').textContent = 'Edit Condition';
        document.getElementById('submitBtnText').textContent = 'Update Condition';
        document.getElementById('conditionDialog').classList.add('active');
        // Expand details section
        document.getElementById('detailsSection').classList.add('expanded');
    } catch (err) {
        console.error(err);
        showMessageDialog(
            formatApiError(err),
            { title: 'Error loading condition', variant: 'error' }
        );
    }
}

function deleteCondition(conditionId, conditionName) {
    currentDeleteType = 'condition';
    currentDeleteConditionId = conditionId;
    currentDeleteBuildingId = null;
    document.getElementById('deleteDialogText').textContent = 'Are you sure you want to delete this condition category? This action cannot be undone.';
    document.getElementById('deleteItemName').textContent = conditionName;
    document.getElementById('deleteDialog').classList.add('active');
}

function deleteBuilding(buildingId, buildingName) {
    currentDeleteType = 'building';
    currentDeleteBuildingId = buildingId;
    currentDeleteConditionId = null;
    document.getElementById('deleteDialogText').textContent = 'Are you sure you want to delete this building? All linked floors, conditions, checkpoints, submissions, and responses for this building will also be deleted. This action cannot be undone.';
    document.getElementById('deleteItemName').textContent = buildingName;
    document.getElementById('deleteDialog').classList.add('active');
}

async function confirmDelete() {
    try {
        if (currentDeleteType === 'building') {
            if (!currentDeleteBuildingId) return;

            const deletedBuildingId = String(currentDeleteBuildingId);
            const response = await fetch(`${API_BASE}/buildings/${currentDeleteBuildingId}/`, {
                method: 'DELETE',
                headers: {
                    'X-CSRFToken': getCSRFToken(),
                }
            });
            const data = await parseJsonSafe(response);
            if (!response.ok) throw data;

            closeDeleteDialog();
            if (String(currentBuildingId) === deletedBuildingId) {
                currentBuildingId = null;
            }
            await loadBuildings();
            showMessageDialog('Building deleted successfully!', { title: 'Deleted', variant: 'success' });
            return;
        }

        if (!currentDeleteConditionId) return;

        const response = await fetch(`${API_BASE}/conditions/${currentDeleteConditionId}/soft_delete/`, {
            method: 'POST',
            headers: {
                'X-CSRFToken': getCSRFToken(),
            }
        });
        const data = await parseJsonSafe(response);
        if (!response.ok) throw data;

        closeDeleteDialog();
        await loadConditions();
        showMessageDialog('Condition deleted successfully!', { title: 'Deleted', variant: 'success' });
    } catch (err) {
        console.error(err);
        showMessageDialog(formatApiError(err), { title: 'Could not delete', variant: 'error' });
    }
}

function getCSRFToken() {
    return document.querySelector('[name=csrfmiddlewaretoken]')?.value || 
           document.cookie.split('; ').find(row => row.startsWith('csrftoken='))?.split('=')[1] || '';
}

function escapeHtml(value) {
    return String(value ?? '').replace(/[&<>"']/g, char => ({
        '&': '&amp;',
        '<': '&lt;',
        '>': '&gt;',
        '"': '&quot;',
        "'": '&#39;'
    }[char]));
}

function jsStringForHtmlAttribute(value) {
    return JSON.stringify(String(value ?? '')).replace(/"/g, '&quot;');
}

// ────────────────────────────────────────────────────────────────
// Search & Filter
// ────────────────────────────────────────────────────────────────

document.getElementById('searchConditions')?.addEventListener('keyup', function() {
    const search = this.value.toLowerCase();
    document.querySelectorAll('#conditionsTableContainer .conditions-table tbody tr').forEach(row => {
        const text = row.textContent.toLowerCase();
        row.style.display = text.includes(search) ? '' : 'none';
    });
});

document.getElementById('accessSearch')?.addEventListener('input', renderAccessUsers);

// ────────────────────────────────────────────────────────────────
// Initialize
// ────────────────────────────────────────────────────────────────

document.addEventListener('DOMContentLoaded', function() {
    loadAccessUsers();
    loadBuildings();
});
