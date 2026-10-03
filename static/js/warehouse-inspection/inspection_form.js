const API_BASE = '/warehouse-inspection/api';
const REQUEST_TIMEOUT_MS = 30000;
const SUBMIT_TIMEOUT_MS = 180000;
const SLOW_SUBMIT_NOTICE_MS = 12000;
let loadedCheckpoints = [];
let loadedBuildings = [];
let loadedFloors = [];
let currentChecklistDraftKey = '';
let checklistDrafts = {};
let floorChecklistRequirements = {};
let requirementsLoading = false;
let requirementsScopeKey = '';
let submitDialogState = 'idle';
// Draft keys (building|condition|floor) submitted during this page session, so
// the footer can report what has actually been recorded rather than what
// merely happens to be filled in locally.
let submittedFloorKeys = new Set();

function normalizeList(payload) {
    if (Array.isArray(payload)) return payload;
    if (payload && Array.isArray(payload.results)) return payload.results;
    return [];
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

function getCSRFToken() {
    const csrfInput = document.querySelector('[name=csrfmiddlewaretoken]');
    if (csrfInput) return csrfInput.value;
    const cookie = document.cookie.split('; ').find(row => row.startsWith('csrftoken='));
    return cookie ? cookie.split('=')[1] : '';
}

function showStatus(message, isError = false) {
    const panel = document.getElementById('statusPanel');
    panel.className = `status-panel${isError ? ' error' : ''}`;
    panel.innerHTML = message;
    panel.style.display = 'block';
}

function clearStatus() {
    const panel = document.getElementById('statusPanel');
    panel.style.display = 'none';
    panel.textContent = '';
}

function setSubmitDialog(state, title, message) {
    submitDialogState = state;
    const dialog = document.getElementById('submitDialog');
    const visual = document.getElementById('submitDialogVisual');
    const titleEl = document.getElementById('submitDialogTitle');
    const messageEl = document.getElementById('submitDialogMessage');
    const closeBtn = document.getElementById('submitDialogCloseBtn');

    if (!dialog || !visual || !titleEl || !messageEl || !closeBtn) return;

    visual.className = `submit-dialog-visual submit-dialog-${state}`;
    if (state === 'loading') {
        visual.innerHTML = '<span class="submit-dialog-spinner" aria-hidden="true"></span>';
        closeBtn.style.display = 'none';
    } else if (state === 'success') {
        visual.innerHTML = '<i class="fas fa-check" aria-hidden="true"></i>';
        closeBtn.textContent = 'OK';
        closeBtn.style.display = 'inline-flex';
    } else {
        visual.innerHTML = '<i class="fas fa-exclamation" aria-hidden="true"></i>';
        closeBtn.textContent = 'Close';
        closeBtn.style.display = 'inline-flex';
    }

    titleEl.textContent = title;
    messageEl.textContent = message;
    dialog.classList.add('active');
}

function showSubmitLoading(message = 'Saving checklist responses and sending email notifications. Please keep this page open.') {
    setSubmitDialog('loading', 'Submitting inspection', message);
}

function showSubmitSuccess(message) {
    setSubmitDialog('success', 'Success', message);
}

function showSubmitError(message) {
    setSubmitDialog('error', 'Submission failed', message);
}

function closeSubmitDialog() {
    if (submitDialogState === 'loading') return;
    const dialog = document.getElementById('submitDialog');
    if (dialog) dialog.classList.remove('active');
    submitDialogState = 'idle';
}

function wait(ms) {
    return new Promise(resolve => setTimeout(resolve, ms));
}

async function parseResponsePayload(response) {
    const contentType = response.headers.get('content-type') || '';
    const text = await response.text();
    if (!text) return {};
    if (contentType.includes('application/json')) {
        try {
            return JSON.parse(text);
        } catch (error) {
            return { detail: text };
        }
    }
    return { detail: text };
}

function collectPayloadMessages(value, messages = []) {
    if (!value) return messages;
    if (typeof value === 'string') {
        messages.push(value);
        return messages;
    }
    if (Array.isArray(value)) {
        value.forEach(item => collectPayloadMessages(item, messages));
        return messages;
    }
    if (typeof value === 'object') {
        ['detail', 'message', 'error', 'non_field_errors'].forEach(key => {
            if (Object.prototype.hasOwnProperty.call(value, key)) {
                collectPayloadMessages(value[key], messages);
            }
        });
        Object.entries(value).forEach(([key, item]) => {
            if (!['detail', 'message', 'error', 'non_field_errors'].includes(key)) {
                collectPayloadMessages(item, messages);
            }
        });
    }
    return messages;
}

function formatPayloadMessage(payload) {
    const messages = collectPayloadMessages(payload)
        .map(message => String(message).trim())
        .filter(Boolean);
    const uniqueMessages = [...new Set(messages)];
    if (uniqueMessages.length) return uniqueMessages.join(' ');
    return payload ? JSON.stringify(payload) : '';
}

function friendlyNetworkMessage(error, options = {}) {
    if (error?.name === 'AbortError') {
        return options.submit
            ? 'The submit request is taking too long. Your inspection may still have reached the server, so please refresh/check before submitting again.'
            : 'The request timed out. Please check your connection and try again.';
    }
    if (navigator.onLine === false) {
        return 'You appear to be offline. Please reconnect and try again.';
    }
    if (error?.isConnectivityError) {
        return 'Connection interrupted. Please check your network and try again.';
    }
    return error?.message || 'Something went wrong. Please try again.';
}

async function requestJson(path, options = {}, requestOptions = {}) {
    const {
        timeoutMs = REQUEST_TIMEOUT_MS,
        retries = 0,
        submit = false,
    } = requestOptions;
    const method = String(options.method || 'GET').toUpperCase();
    const url = path.startsWith('http') ? path : `${API_BASE}${path}`;
    let lastError = null;

    for (let attempt = 0; attempt <= retries; attempt += 1) {
        const controller = new AbortController();
        const timeout = setTimeout(() => controller.abort(), timeoutMs);
        try {
            const response = await fetch(url, {
                credentials: 'same-origin',
                ...options,
                signal: controller.signal,
            });
            clearTimeout(timeout);
            const payload = await parseResponsePayload(response);
            if (!response.ok) {
                throw new Error(formatPayloadMessage(payload) || `Request failed (${response.status})`);
            }
            return payload;
        } catch (error) {
            clearTimeout(timeout);
            if (error instanceof TypeError) {
                error.isConnectivityError = true;
            }
            lastError = error;
            const canRetry = method === 'GET' && attempt < retries;
            if (!canRetry) {
                throw new Error(friendlyNetworkMessage(error, { submit }));
            }
            await wait(700 * (attempt + 1));
        }
    }

    throw new Error(friendlyNetworkMessage(lastError, { submit }));
}

function getChecklistDraftKey(buildingId, conditionId, floorId) {
    if (!buildingId || !conditionId || !floorId) return '';
    return `${buildingId}|${conditionId}|${floorId}`;
}

function getRequirementScopeKey() {
    const buildingId = document.getElementById('buildingSelect').value;
    const conditionId = document.getElementById('conditionSelect').value;
    if (!buildingId || !conditionId) return '';
    return `${buildingId}|${conditionId}`;
}

function getDraftKeyForFloor(floorId) {
    return getChecklistDraftKey(
        document.getElementById('buildingSelect').value,
        document.getElementById('conditionSelect').value,
        floorId
    );
}

function getFloorName(floorId) {
    const floor = loadedFloors.find(item => String(item.floor_id) === String(floorId));
    return floor ? floor.floor_name : `Floor ${floorId}`;
}

function hasRequiredResponse(value) {
    return value === 'yes' || value === 'no';
}

/** Visible text of a <select>'s chosen option, i.e. exactly what the operator sees. */
function getSelectedLabel(selectId) {
    const el = document.getElementById(selectId);
    const option = el && el.selectedOptions && el.selectedOptions[0];
    return option ? option.textContent.trim() : '';
}

function clearChecklistDrafts() {
    checklistDrafts = {};
    floorChecklistRequirements = {};
    requirementsLoading = false;
    requirementsScopeKey = '';
    currentChecklistDraftKey = '';
    submittedFloorKeys = new Set();
    updateSubmitButtonState();
}

function saveCurrentChecklistDraft() {
    if (!currentChecklistDraftKey || !loadedCheckpoints.length) return;

    const existingResponses = (checklistDrafts[currentChecklistDraftKey] || {}).responses || {};
    const responses = {};
    const remarksEl = document.getElementById('floorRemarks');
    loadedCheckpoints.forEach(checkpoint => {
        const selected = document.querySelector(`input[name="response-${checkpoint.checkpoint_id}"]:checked`);
        const prior = existingResponses[checkpoint.checkpoint_id] || {};
        responses[checkpoint.checkpoint_id] = {
            response: selected ? selected.value : '',
            photoToken: prior.photoToken || null,
            photoUrl: prior.photoUrl || ''
        };
    });

    checklistDrafts[currentChecklistDraftKey] = {
        responses,
        remarks: remarksEl ? remarksEl.value : ''
    };
    updateSubmitButtonState();
}

function setCheckpointPhotoState(checkpointId, photoToken, photoUrl) {
    saveCurrentChecklistDraft();
    const draft = checklistDrafts[currentChecklistDraftKey];
    if (!draft) return;
    const entry = draft.responses[checkpointId] || { response: '' };
    entry.photoToken = photoToken;
    entry.photoUrl = photoUrl;
    draft.responses[checkpointId] = entry;
}

function setPhotoRowUiState(checkpointId, options = {}) {
    const { status = '', isError = false, photoUrl = '', busy = false } = options;
    const wrap = document.querySelector(`.wi-photo-wrap[data-checkpoint-photo="${checkpointId}"]`);
    if (!wrap) return;
    const btn = wrap.querySelector('.wi-photo-btn');
    const preview = wrap.querySelector('.wi-photo-preview');
    const thumb = wrap.querySelector('.wi-photo-thumb');
    const statusEl = wrap.querySelector('.wi-photo-status');
    const photoEnabled = wrap.dataset.photoEnabled === 'true';

    if (photoUrl) {
        if (btn) btn.style.display = 'none';
        if (preview) preview.style.display = 'flex';
        if (thumb) thumb.src = photoUrl;
    } else {
        if (btn) btn.style.display = 'inline-flex';
        if (preview) preview.style.display = 'none';
        if (thumb) thumb.removeAttribute('src');
    }

    if (btn) btn.disabled = busy || !photoEnabled;
    if (statusEl) {
        statusEl.textContent = status;
        statusEl.classList.toggle('error', isError);
    }
}

// A photo can only be attached to a checkpoint answered "No" - the capture
// button/input stay disabled otherwise, and any attached photo is dropped the
// moment the answer flips to "Yes" (server also enforces this, see
// WarehouseInspectionSubmissionResponseInputSerializer.validate).
function setPhotoEnabledState(checkpointId, enabled) {
    const wrap = document.querySelector(`.wi-photo-wrap[data-checkpoint-photo="${checkpointId}"]`);
    if (!wrap) return;
    wrap.dataset.photoEnabled = enabled ? 'true' : 'false';
    if (!enabled) {
        removeCheckpointPhoto(checkpointId);
    }
    setPhotoRowUiState(checkpointId, {
        status: enabled ? '' : 'Photo can only be attached when the answer is No.',
    });
}

async function handleTakePhoto(checkpointId) {
    const draft = checklistDrafts[currentChecklistDraftKey];
    const existingUrl = draft && draft.responses[checkpointId] ? draft.responses[checkpointId].photoUrl : '';

    let blob = null;
    try {
        // Opens the live camera (webcam on a laptop, rear camera on a phone)
        // and resolves once the operator accepts a shot, or null if they
        // close it without taking one.
        blob = await openCameraCapture();
    } catch (error) {
        setPhotoRowUiState(checkpointId, { photoUrl: existingUrl, status: error.message, isError: true, busy: false });
        return;
    }
    if (!blob) return;

    setPhotoRowUiState(checkpointId, { status: 'Uploading photo...', busy: true });
    try {
        const result = await uploadCheckpointPhoto(checkpointId, blob);
        setCheckpointPhotoState(checkpointId, result.upload_token, result.url);
        setPhotoRowUiState(checkpointId, { photoUrl: result.url, status: 'Photo attached.', busy: false });
    } catch (error) {
        setPhotoRowUiState(checkpointId, { photoUrl: existingUrl, status: error.message, isError: true, busy: false });
    }
}

function removeCheckpointPhoto(checkpointId) {
    setCheckpointPhotoState(checkpointId, null, '');
    setPhotoRowUiState(checkpointId, { photoUrl: '' });
}

function restoreChecklistDraft(draftKey) {
    const draft = checklistDrafts[draftKey];
    const remarksEl = document.getElementById('floorRemarks');
    if (!draft) {
        if (remarksEl) remarksEl.value = '';
        loadedCheckpoints.forEach(checkpoint => {
            setPhotoEnabledState(checkpoint.checkpoint_id, false);
        });
        updateSubmitButtonState();
        return;
    }

    Object.entries(draft.responses || {}).forEach(([checkpointId, item]) => {
        if (item.response) {
            const radio = document.querySelector(`input[name="response-${checkpointId}"][value="${item.response}"]`);
            if (radio) radio.checked = true;
        }
        setPhotoEnabledState(checkpointId, item.response === 'no');
        if (item.response === 'no') {
            setPhotoRowUiState(checkpointId, { photoUrl: item.photoUrl || '' });
        }
    });
    if (remarksEl) remarksEl.value = draft.remarks || '';

    updateSubmitButtonState();
}

function setFloorRequirement(floorId, checkpoints) {
    floorChecklistRequirements[String(floorId)] = checkpoints.map(
        checkpoint => String(checkpoint.checkpoint_id)
    );
    updateSubmitButtonState();
}

function areRequirementsLoaded() {
    const scopeKey = getRequirementScopeKey();
    if (!scopeKey || requirementsScopeKey !== scopeKey || requirementsLoading) return false;
    return loadedFloors.every(floor => Array.isArray(floorChecklistRequirements[String(floor.floor_id)]));
}

function getRequiredFloors() {
    if (!areRequirementsLoaded()) return [];
    return loadedFloors.filter(floor => {
        const requirementIds = floorChecklistRequirements[String(floor.floor_id)] || [];
        return requirementIds.length > 0;
    });
}

// Only list floors that actually have checklist items for the selected
// condition, instead of every floor in the building - called once
// getRequiredFloors() has something meaningful to say for the current
// building+condition scope (see loadConditionRequirements).
function renderFloorOptions() {
    const floorSelect = document.getElementById('floorSelect');
    const previousFloorId = floorSelect.value;
    const conditionId = document.getElementById('conditionSelect').value;

    if (!conditionId) {
        floorSelect.innerHTML = '<option value="">Select condition first</option>';
        floorSelect.disabled = true;
        loadChecklist();
        return;
    }

    const eligibleFloors = getRequiredFloors();
    if (!eligibleFloors.length) {
        floorSelect.innerHTML = '<option value="">No floors configured for this condition</option>';
        floorSelect.disabled = true;
        loadChecklist();
        return;
    }

    floorSelect.disabled = false;
    floorSelect.innerHTML = '<option value="">Select floor</option>';
    eligibleFloors.forEach(floor => {
        floorSelect.insertAdjacentHTML(
            'beforeend',
            `<option value="${floor.floor_id}">${escapeHtml(floor.floor_name)}</option>`
        );
    });

    const stillValid = eligibleFloors.some(floor => String(floor.floor_id) === String(previousFloorId));
    floorSelect.value = stillValid ? previousFloorId : '';
    loadChecklist();
}

function updateSubmitButtonState() {
    const submitBtn = document.getElementById('submitInspectionBtn');
    const statusText = document.getElementById('completionStatusText');
    if (!submitBtn || !statusText) return;

    submitBtn.disabled = true;
    statusText.classList.remove('ready');

    const buildingId = document.getElementById('buildingSelect').value;
    const conditionId = document.getElementById('conditionSelect').value;
    if (!buildingId || !conditionId) {
        statusText.textContent = '';
        return;
    }

    if (!loadedFloors.length) {
        statusText.textContent = 'No floors are available for this building.';
        return;
    }

    if (requirementsLoading || !areRequirementsLoaded()) {
        statusText.textContent = 'Checking floor completion...';
        return;
    }

    const requiredFloors = getRequiredFloors();
    if (!requiredFloors.length) {
        statusText.textContent = 'No checklist items are configured for any floor under this condition.';
        return;
    }

    // Each floor is submitted on its own - a floor whose items are all
    // answered can be submitted immediately, without waiting for the rest of
    // the building's floors to be inspected.
    const floorId = document.getElementById('floorSelect').value;
    if (!floorId) {
        statusText.textContent = `Select a floor to inspect (${requiredFloors.length} floor${requiredFloors.length === 1 ? '' : 's'} configured for this condition).`;
        return;
    }

    const floor = loadedFloors.find(item => String(item.floor_id) === String(floorId));
    const requirementIds = floorChecklistRequirements[String(floorId)] || [];
    if (!floor || !requirementIds.length) {
        statusText.textContent = 'No checklist items are configured for the selected floor.';
        return;
    }

    const draft = checklistDrafts[getDraftKeyForFloor(floorId)];
    const responses = draft?.responses || {};
    const answered = requirementIds.filter(
        checkpointId => hasRequiredResponse(responses[String(checkpointId)]?.response)
    ).length;

    if (answered < requirementIds.length) {
        statusText.textContent = `${answered}/${requirementIds.length} answered for ${floor.floor_name}. Answer every item to submit this floor.`;
        return;
    }

    // Only floors actually submitted in this session are reported as done -
    // drafts are in-memory, so anything else would just be describing
    // unsaved local state as though it had been recorded.
    const otherFloors = requiredFloors.filter(item => String(item.floor_id) !== String(floorId));
    const notSubmittedYet = otherFloors
        .filter(item => !submittedFloorKeys.has(getDraftKeyForFloor(item.floor_id)))
        .map(item => item.floor_name);

    statusText.textContent = notSubmittedYet.length
        ? `${floor.floor_name} is ready to submit. Not yet submitted in this session: ${notSubmittedYet.join(', ')}.`
        : `${floor.floor_name} is ready to submit.`;
    statusText.classList.add('ready');
    submitBtn.disabled = false;
}

async function apiGet(path) {
    return requestJson(
        path,
        { headers: { 'Accept': 'application/json' } },
        { retries: 1 }
    );
}

async function fetchFloorCheckpoints(buildingId, conditionId, floorId) {
    const payload = await apiGet(`/checkpoints/?building_id=${buildingId}&condition_id=${conditionId}&floor_id=${floorId}`);
    return normalizeList(payload);
}

async function loadConditionRequirements() {
    const buildingId = document.getElementById('buildingSelect').value;
    const conditionId = document.getElementById('conditionSelect').value;
    const scopeKey = getRequirementScopeKey();

    floorChecklistRequirements = {};
    requirementsScopeKey = '';

    if (!scopeKey || !loadedFloors.length) {
        requirementsLoading = false;
        renderFloorOptions();
        updateSubmitButtonState();
        return;
    }

    requirementsLoading = true;
    updateSubmitButtonState();

    try {
        const results = await Promise.all(loadedFloors.map(async floor => {
            const checkpoints = await fetchFloorCheckpoints(buildingId, conditionId, floor.floor_id);
            return {
                floorId: String(floor.floor_id),
                checkpointIds: checkpoints.map(checkpoint => String(checkpoint.checkpoint_id))
            };
        }));

        if (scopeKey !== getRequirementScopeKey()) return;

        floorChecklistRequirements = {};
        results.forEach(result => {
            floorChecklistRequirements[result.floorId] = result.checkpointIds;
        });
        requirementsScopeKey = scopeKey;
        // Must flip before renderFloorOptions() -> getRequiredFloors() ->
        // areRequirementsLoaded(), which treats requirementsLoading=true as
        // "not ready yet" and would otherwise render zero eligible floors
        // even though the data just finished loading.
        requirementsLoading = false;
        renderFloorOptions();
    } catch (error) {
        if (scopeKey === getRequirementScopeKey()) {
            showStatus(`Unable to check all floor requirements: ${escapeHtml(error.message)}`, true);
        }
    } finally {
        if (scopeKey === getRequirementScopeKey()) {
            requirementsLoading = false;
            updateSubmitButtonState();
        }
    }
}

async function loadMasters() {
    try {
        loadedBuildings = normalizeList(await apiGet('/buildings/'));
        const buildingSelect = document.getElementById('buildingSelect');

        buildingSelect.innerHTML = '<option value="">Select building</option>';
        loadedBuildings.forEach(building => {
            buildingSelect.insertAdjacentHTML(
                'beforeend',
                `<option value="${building.building_id}">${escapeHtml(building.building_name)}</option>`
            );
        });

        if (loadedBuildings.length === 1) {
            buildingSelect.value = loadedBuildings[0].building_id;
            await loadBuildingScopedMasters();
        } else {
            resetBuildingScopedMasters();
        }
    } catch (error) {
        showStatus(`Unable to load building masters: ${escapeHtml(error.message)}`, true);
    }
}

function resetBuildingScopedMasters() {
    document.getElementById('conditionSelect').innerHTML = '<option value="">Select condition</option>';
    const floorSelect = document.getElementById('floorSelect');
    floorSelect.innerHTML = '<option value="">Select condition first</option>';
    floorSelect.disabled = true;
    loadedCheckpoints = [];
    loadedFloors = [];
    floorChecklistRequirements = {};
    requirementsLoading = false;
    requirementsScopeKey = '';
    currentChecklistDraftKey = '';
    document.getElementById('inspectionForm').style.display = 'none';
    const emptyPanel = document.getElementById('emptyPanel');
    emptyPanel.textContent = 'Select a building, condition and floor to view checklist items.';
    emptyPanel.style.display = 'block';
    updateSubmitButtonState();
}

async function loadBuildingScopedMasters() {
    clearStatus();
    const buildingId = document.getElementById('buildingSelect').value;
    resetBuildingScopedMasters();

    if (!buildingId) {
        return;
    }

    try {
        const [conditionsPayload, floorsPayload] = await Promise.all([
            apiGet(`/conditions/?building_id=${buildingId}`),
            apiGet(`/floors/?building_id=${buildingId}`)
        ]);
        const conditions = normalizeList(conditionsPayload);
        const floors = normalizeList(floorsPayload).filter(floor => floor.is_active !== false);
        loadedFloors = floors;
        const conditionSelect = document.getElementById('conditionSelect');

        conditionSelect.innerHTML = '<option value="">Select condition</option>';
        conditions.forEach(condition => {
            conditionSelect.insertAdjacentHTML(
                'beforeend',
                `<option value="${condition.condition_category_id}">${escapeHtml(condition.condition_category_name)}</option>`
            );
        });

        // The floor <select> itself is populated once a condition is picked
        // (see renderFloorOptions) - it only ever lists floors that actually
        // have checklist items for that condition, not every floor in the
        // building.
        renderFloorOptions();
        updateSubmitButtonState();
    } catch (error) {
        showStatus(`Unable to load condition/floor masters: ${escapeHtml(error.message)}`, true);
    }
}

async function loadChecklist() {
    clearStatus();
    const buildingId = document.getElementById('buildingSelect').value;
    const conditionId = document.getElementById('conditionSelect').value;
    const floorId = document.getElementById('floorSelect').value;
    const form = document.getElementById('inspectionForm');
    const emptyPanel = document.getElementById('emptyPanel');
    const rowsEl = document.getElementById('checklistRows');
    const remarksEl = document.getElementById('floorRemarks');

    form.style.display = 'none';
    emptyPanel.style.display = 'block';
    rowsEl.innerHTML = '';
    if (remarksEl) remarksEl.value = '';
    loadedCheckpoints = [];
    currentChecklistDraftKey = '';
    document.getElementById('itemCountText').textContent = '';
    updateSubmitButtonState();

    if (!buildingId || !conditionId || !floorId) {
        emptyPanel.textContent = 'Select a building, condition and floor to view checklist items.';
        return;
    }

    try {
        loadedCheckpoints = await fetchFloorCheckpoints(buildingId, conditionId, floorId);
        setFloorRequirement(floorId, loadedCheckpoints);
        if (!loadedCheckpoints.length) {
            emptyPanel.textContent = 'No checklist items are configured for this condition and floor.';
            return;
        }

        const html = loadedCheckpoints.map((checkpoint, index) => {
            return `
                <tr data-checkpoint-id="${checkpoint.checkpoint_id}">
                    <td>${index + 1}</td>
                    <td>
                        <div class="checklist-point">${escapeHtml(checkpoint.checkpoint_name)}</div>
                    </td>
                    <td>
                        <div class="response-options">
                            <label>
                                <input type="radio" name="response-${checkpoint.checkpoint_id}" value="yes" required>
                                Yes
                            </label>
                            <label>
                                <input type="radio" name="response-${checkpoint.checkpoint_id}" value="no" required>
                                No
                            </label>
                        </div>
                    </td>
                    <td>
                        <div class="wi-photo-wrap" data-checkpoint-photo="${checkpoint.checkpoint_id}">
                            <button type="button" class="wi-photo-btn" data-checkpoint="${checkpoint.checkpoint_id}" disabled>
                                <i class="fas fa-camera"></i> Take Photo
                            </button>
                            <div class="wi-photo-preview" style="display:none;">
                                <img class="wi-photo-thumb" alt="Attached proof photo">
                                <button type="button" class="wi-photo-remove" data-checkpoint="${checkpoint.checkpoint_id}" title="Remove photo">
                                    <i class="fas fa-times"></i>
                                </button>
                            </div>
                            <div class="wi-photo-status">Photo can only be attached when the answer is No.</div>
                        </div>
                    </td>
                </tr>
            `;
        }).join('');

        rowsEl.innerHTML = html;
        currentChecklistDraftKey = getChecklistDraftKey(buildingId, conditionId, floorId);
        restoreChecklistDraft(currentChecklistDraftKey);
        document.getElementById('itemCountText').textContent = `${loadedCheckpoints.length} checklist item${loadedCheckpoints.length === 1 ? '' : 's'}`;
        emptyPanel.style.display = 'none';
        form.style.display = 'block';
    } catch (error) {
        emptyPanel.textContent = 'Unable to load checklist items.';
        showStatus(`Unable to load checklist: ${escapeHtml(error.message)}`, true);
    }
}

async function submitInspection(event) {
    event.preventDefault();
    saveCurrentChecklistDraft();
    clearStatus();

    const conditionId = document.getElementById('conditionSelect').value;
    const buildingId = document.getElementById('buildingSelect').value;
    const submitBtn = document.getElementById('submitInspectionBtn');

    if (!buildingId || !conditionId) {
        showStatus('Select a building and condition before submitting.', true);
        return;
    }

    if (!areRequirementsLoaded()) {
        showStatus('Please wait until all floor checklist requirements are checked.', true);
        return;
    }

    // One floor at a time: whichever floor is currently selected gets
    // submitted on its own, as soon as its own items are all answered. The
    // other floors of the building are inspected and submitted separately.
    const floorId = document.getElementById('floorSelect').value;
    const floor = loadedFloors.find(item => String(item.floor_id) === String(floorId));
    if (!floorId || !floor) {
        showStatus('Select a floor before submitting.', true);
        updateSubmitButtonState();
        return;
    }

    const checkpointIds = floorChecklistRequirements[String(floorId)] || [];
    const draft = checklistDrafts[getDraftKeyForFloor(floorId)];
    const draftResponses = draft?.responses || {};
    const unanswered = checkpointIds.filter(
        checkpointId => !hasRequiredResponse(draftResponses[String(checkpointId)]?.response)
    );
    if (!checkpointIds.length || unanswered.length) {
        showStatus(
            checkpointIds.length
                ? `Answer every checklist item for ${escapeHtml(floor.floor_name)} before submitting (${unanswered.length} still blank).`
                : 'No checklist items are configured for the selected floor.',
            true
        );
        updateSubmitButtonState();
        return;
    }

    submitBtn.disabled = true;
    submitBtn.innerHTML = '<i class="fas fa-spinner fa-spin"></i> Submitting';
    showSubmitLoading();
    const slowSubmitTimer = setTimeout(() => {
        const slowMessage = 'Still submitting. The network or mail server is slow, so please keep this page open and do not click submit again.';
        showSubmitLoading(slowMessage);
        showStatus(slowMessage);
    }, SLOW_SUBMIT_NOTICE_MS);

    try {
        const submissions = [{
            floor: Number(floorId),
            notes: draft?.remarks || '',
            responses: checkpointIds.map(checkpointId => {
                const entry = draftResponses[String(checkpointId)] || {};
                return {
                    checkpoint: Number(checkpointId),
                    response: entry.response,
                    photo_token: entry.photoToken || null
                };
            })
        }];

        const payload = await requestJson(
            '/submissions/batch/',
            {
                method: 'POST',
                headers: {
                    'Content-Type': 'application/json',
                    'Accept': 'application/json',
                    'X-CSRFToken': getCSRFToken()
                },
                body: JSON.stringify({
                    building: Number(buildingId),
                    condition_category: Number(conditionId),
                    submissions
                })
            },
            { timeoutMs: SUBMIT_TIMEOUT_MS, submit: true }
        );

        clearTimeout(slowSubmitTimer);
        // Name exactly what was recorded - an operator working through several
        // floors/conditions in a row needs to see which one this confirms.
        const scopeLines = [
            `Warehouse: ${getSelectedLabel('buildingSelect')}`,
            `Condition: ${getSelectedLabel('conditionSelect')}`,
            `Floor: ${floor.floor_name}`,
        ];
        const emailErrors = Array.isArray(payload.email?.errors) ? payload.email.errors : [];
        const headline = emailErrors.length
            ? 'Inspection submitted successfully, but email needs attention.'
            : 'Inspection submitted successfully.';
        const emailNote = emailErrors.length ? `\n\nEmail: ${emailErrors.join(' ')}` : '';

        showSubmitSuccess(`${headline}\n\n${scopeLines.join('\n')}${emailNote}`);
        showStatus(
            `<strong>${escapeHtml(headline)}</strong><br>${scopeLines.map(escapeHtml).join('<br>')}`
            + (emailErrors.length ? `<br>Email: ${escapeHtml(emailErrors.join(' '))}` : ''),
            emailErrors.length > 0
        );

        // Reset only the floor just submitted - any other floor the operator
        // has partly filled in stays intact for them to finish and submit.
        submittedFloorKeys.add(getDraftKeyForFloor(floorId));
        delete checklistDrafts[getDraftKeyForFloor(floorId)];
        try {
            await loadChecklist();
        } catch (refreshError) {
            showStatus(
                `${floor.floor_name} submitted successfully, but the page could not refresh automatically: ${escapeHtml(refreshError.message)}`,
                true
            );
        }
    } catch (error) {
        clearTimeout(slowSubmitTimer);
        showSubmitError(error.message);
        showStatus(`Submission failed: ${escapeHtml(error.message)}`, true);
    } finally {
        clearTimeout(slowSubmitTimer);
        updateSubmitButtonState();
        submitBtn.innerHTML = '<i class="fas fa-paper-plane"></i> Submit Inspection';
    }
}

document.addEventListener('DOMContentLoaded', function() {
    loadMasters();
    document.getElementById('submitDialogCloseBtn').addEventListener('click', closeSubmitDialog);
    document.getElementById('submitDialog').addEventListener('click', function(event) {
        if (event.target === event.currentTarget) {
            closeSubmitDialog();
        }
    });
    document.getElementById('loadChecklistBtn').addEventListener('click', function() {
        saveCurrentChecklistDraft();
        loadChecklist();
    });
    document.getElementById('buildingSelect').addEventListener('change', function() {
        clearChecklistDrafts();
        loadBuildingScopedMasters();
    });
    document.getElementById('conditionSelect').addEventListener('change', function() {
        clearChecklistDrafts();
        // loadConditionRequirements() calls renderFloorOptions() once the
        // eligible-floors check resolves, which itself calls loadChecklist()
        // with whatever floor selection survives the new condition - calling
        // loadChecklist() here too would race it with the previous floor.
        loadConditionRequirements();
    });
    document.getElementById('floorSelect').addEventListener('change', function() {
        saveCurrentChecklistDraft();
        loadChecklist();
    });
    document.getElementById('inspectionForm').addEventListener('change', function(event) {
        if (event.target.matches('input[type="radio"]')) {
            const checkpointId = event.target.name.replace(/^response-/, '');
            setPhotoEnabledState(checkpointId, event.target.value === 'no');
            saveCurrentChecklistDraft();
        }
    });
    document.getElementById('inspectionForm').addEventListener('click', function(event) {
        const removeBtn = event.target.closest('.wi-photo-remove');
        if (removeBtn) {
            removeCheckpointPhoto(removeBtn.dataset.checkpoint);
            return;
        }
        const addBtn = event.target.closest('.wi-photo-btn');
        if (addBtn && !addBtn.disabled) {
            handleTakePhoto(addBtn.dataset.checkpoint);
        }
    });
    document.getElementById('floorRemarks').addEventListener('input', saveCurrentChecklistDraft);
    document.getElementById('inspectionForm').addEventListener('submit', submitInspection);
});
