// Read server-injected config from the <script id="page-config"> element
    const _cfg = JSON.parse(document.getElementById('page-config')?.textContent || '{}');

// Global State
    const appState = {
        sessionKey: null,
        batchId: null,
        hospitalId: null,
        hospitalName: null,
        sessionType: 'normal',      // 'normal' or 'transfer'
        toHospitalId: null,         // destination hospital for H2H transfers
        toHospitalName: null,       // destination hospital name
        scannedProducts: [],
        scanner: null,
        isScanning: false,
        lastScanTime: 0,
        minScanInterval: 1000, // 1 second between scans to prevent duplicates
        scanPaused: false, // Manual pause/resume control
        pendingSerial: null,      // 2-scan workflow: SN captured, waiting for product details barcode
        serialModalCtx: null,      // context stored while serial-question modal is open {qrData, serialOverride}
        awaitingSNForProduct: null, // reverse 2-scan: product data held, waiting for SN barcode scan
        gs1Parser: null,           // GS1 parser instance
        // CODE_128 confirmation buffer — require 2 identical reads before accepting
        // to filter out single-frame misreads (e.g. '-' read as ',' or '1' as 's')
        code128Buffer: { text: null, count: 0, timer: null },
        zoom: {
            supported: false,
            min: 1,
            max: 1,
            step: 0.1,
            current: 1,
            startDistance: null,
            startZoom: 1,
            videoEl: null,
            touchStartHandler: null,
            touchMoveHandler: null,
            touchEndHandler: null,
            applying: false,
            pending: null,
            hintShown: false,
        },
        _startingSession: false,   // double-click guard for Start Audit button
        _submitting: false,        // double-submit guard for Complete & Submit button
        inputMode: 'camera',         // 'camera' | 'scanner' — current input mode
        sgDebounceTimer: null,       // debounce timer for scanner gun textarea
        sgQueue: [],                 // queue of raw scan strings (bulk mode)
        sgProcessing: false,         // is queue currently being drained
        scanQueue:     [],           // kept for compat; no longer used for staging
        sqSubmitting:  false,        // guard: only one submitScanQueue() run at a time
        errorTray:     [],           // scans that failed auto-submit — shown for retry/dismiss
        skipSerialPrompt: false,     // when true: products without SN skip the serial modal and queue as noSerial
    };

    // Salesperson name from Django context (for initials in session ID)
    const SALESPERSON_NAME = _cfg.salespersonName;

    function getUserInitials() {
        const parts = SALESPERSON_NAME.trim().split(/\s+/).filter(Boolean);
        if (parts.length >= 2) {
            return (parts[0][0] + parts[parts.length - 1][0]).toUpperCase();
        }
        return SALESPERSON_NAME.replace(/[^a-zA-Z]/g, '').slice(0, 2).toUpperCase() || 'XX';
    }

    function computeBatchId(hospitalOpt) {
        const now = new Date();
        const dd = String(now.getDate()).padStart(2, '0');
        const mm = String(now.getMonth() + 1).padStart(2, '0');
        const yy = String(now.getFullYear()).slice(-2);
        const initials = getUserInitials();
        if (hospitalOpt && hospitalOpt.value) {
            const sapCode = (hospitalOpt.getAttribute('data-sap-code') || '').trim();
            if (sapCode) {
                return `${dd}${mm}${yy}-${initials}-${sapCode}`;
            }
            const hospName = hospitalOpt.text.split('(')[0].trim();
            const hospShort = hospName.replace(/[^a-zA-Z0-9]/g, '').toUpperCase().slice(0, 6);
            return `${dd}${mm}${yy}-${initials}-${hospShort}`;
        }
        return `${dd}${mm}${yy}-${initials}`;
    }

    // Generate Session/Batch ID (kept for compatibility)
    function generateBatchId() {
        return computeBatchId(null);
    }

    // API Endpoints
    const API = {
        base: '/product_scanning/api',
        createBatch: () => `${API.base}/batches/create_batch/`,
        listHospitals: () => `${API.base}/hospitals/list_hospitals/`,
        startSession: () => `${API.base}/sessions/start_session/`,
        resumeCheck: (hospitalId) => `${API.base}/sessions/resume_check/?hospital_id=${hospitalId}`,
        sessionByKey: (key) => `${API.base}/sessions/by_session_key/?session_key=${encodeURIComponent(key)}`,
        scanProduct: () => `${API.base}/products/scan_product/`,
        completeSession: () => `${API.base}/sessions/complete_session/`,
    };

    // Utility Functions
    function getCookie(name) {
        let cookieValue = null;
        if (document.cookie && document.cookie !== '') {
            const cookies = document.cookie.split(';');
            for (let i = 0; i < cookies.length; i++) {
                const cookie = cookies[i].trim();
                if (cookie.substring(0, name.length + 1) === (name + '=')) {
                    cookieValue = decodeURIComponent(cookie.substring(name.length + 1));
                    break;
                }
            }
        }
        return cookieValue;
    }

    function normalizeScanText(text) {
        return String(text || '')
            .replace(/[\u0000-\u001F\u007F]/g, '')
            .replace(/^\](C1|d2|e0|Q3|J4)/i, '')
            .trim();
    }

    function showAlert(message, type = 'info') {
        const alertDiv = document.createElement('div');
        alertDiv.className = `alert alert-${type}`;
        alertDiv.innerHTML = `
            <strong>${type.toUpperCase()}:</strong> ${message}
            <span style="float:right; cursor:pointer; font-size:1.2em;" onclick="this.parentElement.remove()">×</span>
        `;
        document.getElementById('alertContainer').prepend(alertDiv);
        setTimeout(() => {
            if (alertDiv.parentElement) alertDiv.remove();
        }, 5000);
    }

    function showLoading(show = true) {
        document.getElementById('loadingOverlay').classList.toggle('show', show);
    }

    function hideSection(id) {
        document.getElementById(id).classList.remove('active');
    }

    function showSection(id) {
        document.getElementById(id).classList.add('active');
    }

    function clamp(value, min, max) {
        return Math.min(max, Math.max(min, value));
    }

    function getTouchDistance(t1, t2) {
        const dx = t1.clientX - t2.clientX;
        const dy = t1.clientY - t2.clientY;
        return Math.hypot(dx, dy);
    }

    function getActiveVideoTrack() {
        const video = document.querySelector('#qr-reader video');
        const stream = video && video.srcObject;
        if (!stream || !stream.getVideoTracks) return null;
        const tracks = stream.getVideoTracks();
        return tracks.length ? tracks[0] : null;
    }

    async function applyZoom(zoomValue) {
        const z = appState.zoom;
        z.pending = zoomValue;
        if (z.applying) return;
        z.applying = true;

        while (z.pending !== null) {
            const next = z.pending;
            z.pending = null;
            const track = getActiveVideoTrack();
            if (!track) break;
            try {
                await track.applyConstraints({ advanced: [{ zoom: next }] });
                z.current = next;
            } catch (err) {

                break;
            }
        }

        z.applying = false;
    }

    function teardownPinchZoom() {
        const z = appState.zoom;
        if (z.videoEl) {
            if (z.touchStartHandler) z.videoEl.removeEventListener('touchstart', z.touchStartHandler);
            if (z.touchMoveHandler) z.videoEl.removeEventListener('touchmove', z.touchMoveHandler);
            if (z.touchEndHandler) z.videoEl.removeEventListener('touchend', z.touchEndHandler);
            if (z.touchEndHandler) z.videoEl.removeEventListener('touchcancel', z.touchEndHandler);
        }
        z.videoEl = null;
        z.touchStartHandler = null;
        z.touchMoveHandler = null;
        z.touchEndHandler = null;
        z.startDistance = null;
        z.pending = null;
        z.applying = false;
    }

    function initPinchZoom(retries) {
        const remaining = typeof retries === 'number' ? retries : 10;
        const video = document.querySelector('#qr-reader video');
        const track = getActiveVideoTrack();

        if (!video || !track) {
            if (remaining > 0) setTimeout(() => initPinchZoom(remaining - 1), 200);
            return;
        }

        const capabilities = track.getCapabilities ? track.getCapabilities() : {};
        if (!capabilities || typeof capabilities.zoom === 'undefined') {
            appState.zoom.supported = false;
            return;
        }

        teardownPinchZoom();

        const z = appState.zoom;
        const settings = track.getSettings ? track.getSettings() : {};
        z.supported = true;
        z.min = Number(capabilities.zoom.min ?? 1);
        z.max = Number(capabilities.zoom.max ?? 1);
        z.step = Number(capabilities.zoom.step || 0.1);
        z.current = Number(settings.zoom ?? z.min);
        z.videoEl = video;

        video.style.touchAction = 'none';

        z.touchStartHandler = function (e) {
            if (e.touches.length === 2) {
                z.startDistance = getTouchDistance(e.touches[0], e.touches[1]);
                z.startZoom = z.current;
            }
        };

        z.touchMoveHandler = function (e) {
            if (!z.supported || e.touches.length !== 2 || !z.startDistance) return;
            e.preventDefault();
            const newDistance = getTouchDistance(e.touches[0], e.touches[1]);
            if (!newDistance) return;

            const ratio = newDistance / z.startDistance;
            let target = z.startZoom * ratio;
            target = clamp(target, z.min, z.max);

            if (z.step > 0) {
                target = Math.round(target / z.step) * z.step;
                target = clamp(target, z.min, z.max);
            }

            if (Math.abs(target - z.current) < z.step * 0.5) return;
            applyZoom(target);
        };

        z.touchEndHandler = function (e) {
            if (e.touches.length < 2) {
                z.startDistance = null;
            }
        };

        video.addEventListener('touchstart', z.touchStartHandler, { passive: true });
        video.addEventListener('touchmove', z.touchMoveHandler, { passive: false });
        video.addEventListener('touchend', z.touchEndHandler, { passive: true });
        video.addEventListener('touchcancel', z.touchEndHandler, { passive: true });

        if (!z.hintShown) {
            z.hintShown = true;
            showAlert('Tip: Use two fingers on camera preview to zoom in/out.', 'info');
        }
    }

    function updateStepIndicator(step) {
        document.querySelectorAll('.step').forEach((el, idx) => {
            el.classList.remove('active', 'completed');
            if (idx + 1 < step) el.classList.add('completed');
            if (idx + 1 === step) el.classList.add('active');
        });
    }

    function toggleSessionType() {
        const sessionType = document.querySelector('input[name="sessionType"]:checked').value;
        appState.sessionType = sessionType;
        const toHospitalGroup = document.getElementById('toHospitalGroup');
        const normalLabel = document.getElementById('typeNormalLabel');
        const transferLabel = document.getElementById('typeTransferLabel');
        
        if (sessionType === 'transfer') {
            toHospitalGroup.style.display = 'block';
            normalLabel.style.borderColor = '#e5e7eb';
            normalLabel.style.background = '#fff';
            transferLabel.style.borderColor = '#f97316';
            transferLabel.style.background = '#fff7ed';
        } else {
            toHospitalGroup.style.display = 'none';
            appState.toHospitalId = null;
            appState.toHospitalName = null;
            document.getElementById('toHospitalSelect').value = '';
            document.getElementById('transferRoutePreview').style.display = 'none';
            normalLabel.style.borderColor = '#0076c8';
            normalLabel.style.background = '#f0f9ff';
            transferLabel.style.borderColor = '#e5e7eb';
            transferLabel.style.background = '#fff';
        }
        updateTransferRoutePreview();
    }

    function updateToHospitalInfo() {
        const select = document.getElementById('toHospitalSelect');
        const option = select.options[select.selectedIndex];
        
        if (select.value) {
            appState.toHospitalId = select.value;
            appState.toHospitalName = option.getAttribute('data-name') || option.text.split(' (')[0];
        } else {
            appState.toHospitalId = null;
            appState.toHospitalName = null;
        }
        updateTransferRoutePreview();
    }

    function updateTransferRoutePreview() {
        const preview = document.getElementById('transferRoutePreview');
        const fromSelect = document.getElementById('hospitalSelect');
        const toSelect = document.getElementById('toHospitalSelect');
        
        if (appState.sessionType === 'transfer' && fromSelect.value && toSelect.value) {
            const fromName = fromSelect.options[fromSelect.selectedIndex].text.split(' (')[0];
            const toName = toSelect.options[toSelect.selectedIndex].text.split(' (')[0];
            document.getElementById('routeFrom').textContent = fromName;
            document.getElementById('routeTo').textContent = toName;
            preview.style.display = 'block';
        } else {
            preview.style.display = 'none';
        }
    }

    function updateHospitalInfo() {
        const select = document.getElementById('hospitalSelect');
        const option = select.options[select.selectedIndex];
        const infoDiv = document.getElementById('hospitalInfo');

        if (!select.value) {
            infoDiv.style.display = 'none';
            appState.batchId = computeBatchId(null);
            document.getElementById('batchIdDisplay').textContent = appState.batchId;
            updateTransferRoutePreview();
            return;
        }

        const hospitalName = option.text.split(' (')[0]; // Remove SAP code from display
        const sapCode = option.getAttribute('data-sap-code') || 'N/A';
        const province = option.getAttribute('data-province') || 'N/A';

        document.getElementById('infoHospitalName').textContent = hospitalName;
        document.getElementById('infoSapCode').textContent = sapCode;
        document.getElementById('infoProvince').textContent = province;

        infoDiv.style.display = 'block';

        // Recompute session ID with selected hospital's SAP code / short name
        appState.batchId = computeBatchId(option);
        document.getElementById('batchIdDisplay').textContent = appState.batchId;
        
        updateTransferRoutePreview();
    }

    // Step 1: Start Scanning
    async function startScanning() {
        // Prevent double-submit (rapid taps / multiple clicks)
        if (appState._startingSession) {

            return;
        }
        appState._startingSession = true;
        // Safety: auto-reset flag after 15s in case of unexpected failure
        const safetyTimer = setTimeout(() => { appState._startingSession = false; }, 15000);

        const hospitalId = document.getElementById('hospitalSelect').value;

        if (!hospitalId) {
            showAlert('Please select a hospital', 'error');
            clearTimeout(safetyTimer);
            appState._startingSession = false;
            return;
        }

        // For H2H transfers, validate destination hospital
        if (appState.sessionType === 'transfer') {
            const toHospitalId = document.getElementById('toHospitalSelect').value;
            if (!toHospitalId) {
                showAlert('Please select a destination hospital for the transfer', 'error');
                clearTimeout(safetyTimer);
                appState._startingSession = false;
                return;
            }
            if (toHospitalId === hospitalId) {
                showAlert('Source and destination hospitals must be different', 'error');
                clearTimeout(safetyTimer);
                appState._startingSession = false;
                return;
            }
            appState.toHospitalId = toHospitalId;
            const toSelect = document.getElementById('toHospitalSelect');
            appState.toHospitalName = toSelect.options[toSelect.selectedIndex].getAttribute('data-name') || toSelect.options[toSelect.selectedIndex].text.split('(')[0].trim();
        }

        // Get hospital name (needed for resume path too)
        appState.hospitalId = hospitalId;
        const hospitalSelect = document.getElementById('hospitalSelect');
        appState.hospitalName = hospitalSelect.options[hospitalSelect.selectedIndex].text.split('(')[0].trim();

        // Check for an abandoned active session for this hospital (normal sessions only)
        if (appState.sessionType !== 'transfer') {
            const resumeData = await fetch(API.resumeCheck(hospitalId), {
                headers: { 'X-Requested-With': 'XMLHttpRequest' }
            }).then(r => r.json()).catch(() => ({ session: null }));

            if (resumeData.session) {
                const s = resumeData.session;
                const choice = await showResumeModal(s.batch_id, s.scans_count, s.started_at);
                if (choice === 'continue') {
                    await resumeExistingSession(s.session_key);
                    clearTimeout(safetyTimer);
                    appState._startingSession = false;
                    return;
                }
            }
        }

        // Use the pre-generated batch ID
        const batchId = appState.batchId;

        showLoading(true);

        try {

            // Build request body
            const requestBody = {
                batch_id: batchId,
                hospital_id: appState.hospitalId,
                session_type: appState.sessionType
            };
            if (appState.sessionType === 'transfer' && appState.toHospitalId) {
                requestBody.to_hospital_id = appState.toHospitalId;
            }

            // Start session (which will create batch if needed)
            const sessionResponse = await fetch(API.startSession(), {
                method: 'POST',
                headers: {
                    'Content-Type': 'application/json',
                    'X-CSRFToken': getCookie('csrftoken')
                },
                credentials: 'same-origin',
                body: JSON.stringify(requestBody)
            });

            if (!sessionResponse.ok) {
                const errorData = await sessionResponse.json();
                throw new Error(errorData.error || 'Failed to start session');
            }
            
            const session = await sessionResponse.json();
            appState.sessionKey = session.session_key;

            // Update UI
            document.getElementById('displayBatch').textContent = appState.batchId;
            
            // Show hospital info with route for transfers
            if (appState.sessionType === 'transfer') {
                document.getElementById('displayHospital').innerHTML = `<span style="color:#f97316;font-weight:700;">${appState.hospitalName} → ${appState.toHospitalName}</span>`;
            } else {
                document.getElementById('displayHospital').textContent = appState.hospitalName;
            }
            document.getElementById('sessionInfo').style.display = 'block';

            // Move to Step 2
            hideSection('step1');
            showSection('step2');
            updateStepIndicator(2);

            // Initialize QR Scanner
            initializeQRScanner();

            showAlert('Session started! Ready to scan products.', 'success');
        } catch (error) {
            showAlert(`Error: ${error.message}`, 'error');
            console.error(error);
        } finally {
            showLoading(false);
            clearTimeout(safetyTimer);
            appState._startingSession = false;  // release the guard
        }
    }

    // ── Resume session helpers ────────────────────────────────────────────────

    function showResumeModal(batchId, scansCount, startedAt) {
        return new Promise(resolve => {
            const modal = document.createElement('div');
            modal.id = 'resumeSessionModal';
            modal.style.cssText = 'position:fixed;inset:0;background:rgba(15,23,42,.55);z-index:9999;display:flex;align-items:flex-end;justify-content:center;';
            modal.innerHTML =
                '<div style="background:#fff;border-radius:20px 20px 0 0;width:min(520px,100%);padding:0 0 env(safe-area-inset-bottom,0) 0;box-shadow:0 -8px 40px rgba(0,0,0,.2);">' +
                  '<div style="text-align:center;padding:12px 0 4px;"><div style="width:36px;height:4px;background:#e2e8f0;border-radius:2px;display:inline-block;"></div></div>' +
                  '<div style="padding:6px 20px 20px;">' +
                    '<div style="font-weight:800;font-size:1.05em;color:#0f172a;margin-bottom:6px;">⏸ Unfinished Session Found</div>' +
                    '<div style="font-size:.88em;color:#64748b;margin-bottom:16px;">' +
                      'Batch <b>' + batchId + '</b> — <b>' + scansCount + ' item' + (scansCount !== 1 ? 's' : '') + '</b> already scanned on ' + startedAt +
                    '</div>' +
                    '<button id="resumeBtn" style="width:100%;background:#0771f4;color:#fff;border:none;border-radius:10px;padding:13px;font-weight:700;font-size:.95em;cursor:pointer;margin-bottom:10px;">' +
                      '▶ Continue from where I left off' +
                    '</button>' +
                    '<button id="newBatchBtn" style="width:100%;background:#f1f5f9;color:#475569;border:none;border-radius:10px;padding:13px;font-weight:600;font-size:.9em;cursor:pointer;">' +
                      '+ Start a New Batch' +
                    '</button>' +
                  '</div>' +
                '</div>';

            function cleanup(choice) {
                modal.remove();
                resolve(choice);
            }
            modal.addEventListener('click', function(e) { if (e.target === modal) cleanup('new'); });
            modal.querySelector('#resumeBtn').addEventListener('click', function() { cleanup('continue'); });
            modal.querySelector('#newBatchBtn').addEventListener('click', function() { cleanup('new'); });
            document.body.appendChild(modal);
        });
    }

    async function resumeExistingSession(sessionKey) {
        showLoading(true);
        try {
            const data = await fetch(API.sessionByKey(sessionKey), {
                headers: { 'X-Requested-With': 'XMLHttpRequest' }
            }).then(r => r.json());

            appState.sessionKey      = sessionKey;
            appState.batchId         = data.batch_details.batch_id;
            appState.scannedProducts = data.scanned_products || [];

            document.getElementById('displayBatch').textContent    = appState.batchId;
            document.getElementById('displayHospital').textContent = appState.hospitalName;
            document.getElementById('sessionInfo').style.display   = 'block';

            hideSection('step1');
            showSection('step2');
            updateStepIndicator(2);
            updateProductsList();
            showAlert(`Resumed — ${appState.scannedProducts.length} item${appState.scannedProducts.length !== 1 ? 's' : ''} already scanned.`, 'success');
            initializeQRScanner();
        } catch (err) {
            showAlert('Failed to resume session. Please try again.', 'error');
            console.error(err);
        } finally {
            showLoading(false);
        }
    }

    // GS1 Application Identifier (AI) Parser
    // Uses PROPER fixed-length definitions so fixed-length AIs (e.g. AI 01 = 14 chars)
    // are not misread when immediately followed by another field without FNC1.
    function parseGS1Data(gs1String) {
        // AI table: field name, fixed length (-1 = variable, terminated by FNC1), isDate
        const AI_DEFS = {
            '00':  { f: 'sscc',              l: 18, d: false },
            '01':  { f: 'gtin_code',         l: 14, d: false },
            '02':  { f: 'gtin_contained',    l: 14, d: false },
            '10':  { f: 'batch_number',      l: -1, d: false },
            '11':  { f: 'production_date',   l:  6, d: true  },
            '12':  { f: 'due_date',          l:  6, d: true  },
            '13':  { f: 'packaging_date',    l:  6, d: true  },
            '15':  { f: 'best_before_date',  l:  6, d: true  },
            '17':  { f: 'expiry_date',       l:  6, d: true  },
            '20':  { f: 'variant',           l:  2, d: false },
            '21':  { f: 'serial_number',     l: -1, d: false },
            '22':  { f: 'secondary_data',    l: -1, d: false },
            '30':  { f: 'quantity',          l: -1, d: false },
            '240': { f: 'additional_id',     l: -1, d: false },
            '241': { f: 'customer_part',     l: -1, d: false },
            '250': { f: 'secondary_serial',  l: -1, d: false },
            '251': { f: 'source_entity',     l: -1, d: false },
            '310': { f: 'net_weight_kg',     l:  6, d: false },
            '311': { f: 'length_m',          l:  6, d: false },
            '315': { f: 'volume_l',          l:  6, d: false },
            '316': { f: 'volume_m3',         l:  6, d: false },
            '400': { f: 'purchase_order',    l: -1, d: false },
            '7001':{ f: 'nato_stock_number', l: 13, d: false },
        };
        // Sort longest-first for greedy AI prefix matching
        const AI_KEYS = Object.keys(AI_DEFS).sort((a, b) => b.length - a.length);
        const FIXED_AI_KEYS = AI_KEYS.filter((k) => AI_DEFS[k].l > 0);
        // Variable-length AIs used as heuristic split boundaries when FNC1 is absent.
        // NOTE: '22' (additional product ID) intentionally excluded — its 2-digit code
        // appears commonly inside alphanumeric serial numbers (e.g. MOAW-2231) causing
        // false splits. AI 22 is very rarely used in pharma/medical GS1 barcodes.
        const VARIABLE_BOUNDARY_AIs = new Set(['21', '10', '240', '241', '250', '251', '30']);
        const FNC1 = String.fromCharCode(29);
        const result = {};

        // Primary GS1 identifiers that start a new message — never appear mid-string,
        // so must be excluded from fixed-length boundary detection (false-positive prone).
        // e.g. '00' inside 'L25A006' + 18-digit serial looks like an SSCC boundary.
        const PRIMARY_AI_EXCLUDES = new Set(['00', '01', '02']);

        // Finds the position in `str` (starting after `startPos`) where the next
        // AI begins — used when FNC1 separators were dropped by the scanner.
        // Two-pass: variable-length boundaries (semantically preferred) first,
        // then fixed-length boundaries as a fallback (excluding primary identifiers).
        function findAiBoundary(str, startPos, currentAi) {
            // Pass 1 — variable-length AI boundaries (most semantically reliable)
            for (let i = startPos + 1; i < str.length; i++) {
                for (const key of VARIABLE_BOUNDARY_AIs) {
                    if (key === currentAi) continue;
                    if (!str.startsWith(key, i)) continue;
                    if (i > startPos && (i + key.length) < str.length) {
                        return i;
                    }
                }
            }
            // Pass 2 — fixed-length AI boundaries (fallback)
            for (let i = startPos + 1; i < str.length; i++) {
                for (const key of FIXED_AI_KEYS) {
                    if (PRIMARY_AI_EXCLUDES.has(key)) continue; // never mid-string
                    if (!str.startsWith(key, i)) continue;
                    const len = AI_DEFS[key].l;
                    const valueStart = i + key.length;
                    const valueEnd = valueStart + len;
                    if (valueEnd > str.length) continue;
                    const candidate = str.slice(valueStart, valueEnd);
                    if (!/^\d+$/.test(candidate)) continue;
                    return i;
                }
            }
            return -1;
        }

        // Normalise FNC1 variants and AIM reader prefixes
        let s = gs1String
            .replace(/^\](C1|d2|e0|Q3|J4)/i, '')   // AIM symbology identifiers
            .replace(/[\u001d\x1d]/g, FNC1)
            .replace(/\[GS\]/gi, FNC1)
            .replace(/\{FNC1\}/gi, FNC1)
            .replace(/<(?:FNC1|GS)>/gi, FNC1);

        // Parenthesised format: (01)val(17)val  →  FNC101valFNC117val
        if (/\(\d{2,4}\)/.test(s)) {
            s = s.replace(/\((\d{2,4})\)/g, (_, ai) => FNC1 + ai);
            if (s[0] !== FNC1) s = FNC1 + s;
        }

        let pos = 0;
        while (pos < s.length) {
            if (s[pos] === FNC1) { pos++; continue; }

            // Match longest AI prefix first
            let matched = null;
            for (const key of AI_KEYS) {
                if (s.startsWith(key, pos)) { matched = key; break; }
            }

            if (!matched) {
                // Not a recognised AI — check if remainder is a plain barcode
                const rest = s.slice(pos).replace(new RegExp(FNC1, 'g'), '');
                if (/^\d{8,14}$/.test(rest)) {
                    result.gtin_code = result.gtin_code || rest;
                }
                break;
            }

            const def = AI_DEFS[matched];
            pos += matched.length;

            let value;
            if (def.l > 0) {
                // Fixed-length field: read exactly def.l characters
                value = s.slice(pos, pos + def.l);
                pos += def.l;
            } else {
                // Variable-length: read until next FNC1 or end of string
                const fi = s.indexOf(FNC1, pos);
                if (fi === -1) {
                    const boundary = findAiBoundary(s, pos, matched);
                    if (boundary !== -1) {
                        value = s.slice(pos, boundary);
                        pos = boundary;
                    } else {
                        value = s.slice(pos);
                        pos = s.length;
                    }
                } else {
                    value = s.slice(pos, fi);
                    pos = fi;
                }
            }

            if (!value) continue;
            value = value.replace(/[\u0000-\u001F\u007F]/g, '').trim();

            // Convert YYMMDD dates to YYYY-MM-DD
            if (def.d && value.length >= 6) {
                const yy = parseInt(value.slice(0, 2));
                const mm = value.slice(2, 4);
                const dd = value.slice(4, 6);
                const yyyy = yy <= 49 ? 2000 + yy : 1900 + yy;
                value = `${yyyy}-${mm}-${dd}`;
            }

            if (!result[def.f]) result[def.f] = value;  // first occurrence wins
        }

        return result;
    }

    // Parse GS1 DataMatrix and convert to standard format
    function processGS1Data(scanData) {
        // Check if it looks like GS1 data (contains FNC1, {FNC1}, or AI code patterns)
        const hasGS1Markers = /(\u001d|\[GS\]|\x1d|\{FNC1\}|^(01|02|10|11|12|13|15|17|20|21|240|250|310|3101|3102|3103|00|30)\d)/.test(scanData);
        
        if (hasGS1Markers) {

            const parsed = parseGS1Data(scanData);
            
            // Map parsed fields to backend format (using correct field names)
            const parts = [];
            if (parsed.serial_number) parts.push(`SERIAL:${parsed.serial_number}`);
            if (parsed.gtin_code) parts.push(`GTIN:${parsed.gtin_code}`);
            if (parsed.batch_number) parts.push(`BATCH:${parsed.batch_number}`);
            if (parsed.expiry_date) parts.push(`EXPIRY:${parsed.expiry_date}`);
            if (parsed.production_date) parts.push(`PRODUCED:${parsed.production_date}`);
            if (parsed.best_before_date) parts.push(`BESTBEFORE:${parsed.best_before_date}`);
            if (parsed.packaging_date) parts.push(`PACKAGED:${parsed.packaging_date}`);
            if (parsed.due_date) parts.push(`DUEDATE:${parsed.due_date}`);
            if (parsed.net_weight_kg) parts.push(`WEIGHT_KG:${parsed.net_weight_kg}`);
            if (parsed.net_weight_lbs) parts.push(`WEIGHT_LBS:${parsed.net_weight_lbs}`);
            if (parsed.quantity) parts.push(`QTY:${parsed.quantity}`);
            if (parsed.variant) parts.push(`VARIANT:${parsed.variant}`);
            if (parsed.additional_id) parts.push(`ADDITIONALID:${parsed.additional_id}`);
            if (parsed.secondary_serial) parts.push(`SECONDARYSERIAL:${parsed.secondary_serial}`);
            
            // If we found any data, use pipe-separated format
            if (parts.length > 0) {

                return parts.join('|');
            }
        }
        
        return scanData; // Return as-is if not GS1 format
    }

    // ── Barcode Validation ─────────────────────────────────────────────────
    // Validates whether a detected barcode is legitimate to prevent false positives
    function isValidBarcodeScan(decodedText, decodedResult) {
        const normalized = normalizeScanText(decodedText);
        if (!normalized) {
            return false;
        }

        const formatName = decodedResult?.result?.format?.formatName || '';
        const trimmed = normalized;
        
        // CODE_128 serial-only barcodes can be short; keep stricter rule for other formats.
        if (formatName === 'CODE_128') {
            if (trimmed.length < 4) {

                return false;
            }
        } else if (trimmed.length < 8) {

            return false;
        }

        // CODE_128 specific validation (most error-prone format)
        if (formatName === 'CODE_128') {
            // Reject if contains too many special characters (sign of false detection)
            const specialCharCount = (trimmed.match(/[#:,]/g) || []).length;
            const alphaNum = (trimmed.match(/[a-zA-Z0-9]/g) || []).length;
            
            // Reject only clearly noisy reads; keep valid serial-only scans accepted.
            if (specialCharCount > alphaNum * 0.6 && alphaNum < 3) {

                return false;
            }
            
            // Reject if looks like garbage (too many consecutive special chars)
            if (/[#:,]{3,}/.test(trimmed)) {

                return false;
            }
        }

        // EAN/UPC validation - should be mostly digits
        if (formatName && (formatName.includes('EAN') || formatName.includes('UPC'))) {
            if (!/^\d+$/.test(trimmed)) {

                return false;
            }
        }

        return true;
    }

    // Initialize QR/Barcode/DataMatrix Scanner with dual library support
    function initializeQRScanner() {
        try {
            appState.scanner = new Html5Qrcode("qr-reader");

            // Optimized config for QR codes, barcodes, and DataMatrix (GS1)
            const config = {
                fps: 30,  // High FPS for faster 1D barcode detection (CODE_128, EAN-13)
                qrbox: { width: 350, height: 350 },  // Larger scan box for better detection
                aspectRatio: 1.0,
                showTorchButtonIfSupported: true,
                disableFlip: false,
                verbose: false,
                // ★ Use browser-native BarcodeDetector when available (far superior for GS1-128)
                experimentalFeatures: {
                    useBarCodeDetectorIfSupported: true
                },
                formatsToSupport: [
                    // ── 2D formats (most reliable) ──────────────────────────────────────────
                    Html5QrcodeSupportedFormats.QR_CODE,
                    Html5QrcodeSupportedFormats.DATA_MATRIX,   // GS1 DataMatrix (most medical devices)
                    Html5QrcodeSupportedFormats.PDF_417,        // GS1 DataBar Expanded / PDF417
                    Html5QrcodeSupportedFormats.AZTEC,          // Aztec code (some medical/pharma devices)
                    // ── 1D barcode formats — reliable GS1 standard formats ──────
                    Html5QrcodeSupportedFormats.CODE_128,       // GS1-128 / serial barcodes (confirmed via 2-read buffer)
                    Html5QrcodeSupportedFormats.CODE_39,        // Fallback for various devices
                    Html5QrcodeSupportedFormats.CODE_93,
                    Html5QrcodeSupportedFormats.EAN_13,         // GS1 EAN-13 (reliable)
                    Html5QrcodeSupportedFormats.EAN_8,          // GS1 EAN-8 (reliable)
                    Html5QrcodeSupportedFormats.UPC_A,          // UPC-A (reliable)
                    Html5QrcodeSupportedFormats.UPC_E,          // UPC-E (reliable)
                    Html5QrcodeSupportedFormats.ITF,            // GS1 ITF-14 (reliable)
                    Html5QrcodeSupportedFormats.CODABAR,        // NW-7 / Codabar (if needed)
                ]
            };

            appState.scanner.start(
                { facingMode: "environment" },  // Use back camera
                config,
                (decodedText, decodedResult) => {
                    const normalizedText = normalizeScanText(decodedText);
                    const formatName = decodedResult?.result?.format?.formatName || '';

                    // Validate barcode scan quality and content
                    if (!isValidBarcodeScan(normalizedText, decodedResult)) {

                        return;
                    }

                    // CODE_128 confirmation buffer:
                    // Require 2 consecutive identical reads before accepting.
                    // This eliminates single-frame character misreads (e.g. '-'→',' or '1'→'s').
                    if (formatName === 'CODE_128') {
                        const buf = appState.code128Buffer;
                        if (buf.text === normalizedText) {
                            buf.count++;

                            if (buf.count >= 2) {
                                // Two identical reads — accept it
                                clearTimeout(buf.timer);
                                buf.text = null; buf.count = 0; buf.timer = null;
                                handleQRScan(normalizedText);
                            }
                        } else {
                            // New or different read — start fresh
                            clearTimeout(buf.timer);
                            buf.text = normalizedText;
                            buf.count = 1;
                            // Auto-clear buffer after 2 s if no confirmation arrives
                            buf.timer = setTimeout(() => {
                                buf.text = null; buf.count = 0; buf.timer = null;
                            }, 2000);

                        }
                        return; // Do not process until confirmed
                    }

                    // All other formats — accept immediately
                    handleQRScan(normalizedText);
                },
                (errorMessage) => {
                    // Silent error handling for continuous scanning
                    // Only log if verbose mode enabled
                    if (config.verbose && errorMessage && !errorMessage.includes('No MultiFormat Readers')) {

                    }
                }
            ).then(() => {

                appState.isScanning = true;
                initPinchZoom();
                showAlert('Camera ready! Scan QR codes, barcodes, or GS1 DataMatrix codes.', 'success');
            }).catch((err) => {
                console.error("Scanner error:", err);
                if (err.name === 'NotAllowedError') {
                    showAlert('Camera access denied. Please allow camera access and refresh page.', 'danger');
                } else if (err.name === 'NotFoundError') {
                    showAlert('No camera found. Use manual input below.', 'warning');
                } else {
                    showAlert(`Camera error: ${err.message}. Using manual input.`, 'warning');
                }
            });
        } catch (err) {
            console.error("Failed to initialize scanner:", err);
            showAlert(`Error initializing camera: ${err.message}`, 'error');
        }
    }

    // ─── 2-scan workflow helpers ──────────────────────────────────────────────
    // Shows / hides the pending-serial banner in Step 2
    function updatePendingSerialUI() {
        const banner = document.getElementById('pendingSerialBanner');
        const snSpan  = document.getElementById('pendingSerialValue');
        if (!banner || !snSpan) return;
        if (appState.pendingSerial) {
            snSpan.textContent = appState.pendingSerial;
            banner.style.display = 'block';
        } else {
            banner.style.display = 'none';
        }
    }

    // Clear the pending serial and dismiss the banner
    function clearPendingSerial() {
        appState.pendingSerial = null;
        updatePendingSerialUI();
        showAlert('↩️ Pending serial cleared.', 'info');
    }

    // Shows / hides the "awaiting SN scan" banner (reverse 2-scan workflow)
    function updateAwaitingSNUI() {
        const banner = document.getElementById('awaitingSNBanner');
        const details = document.getElementById('awaitingSNDetails');
        if (!banner || !details) return;
        if (appState.awaitingSNForProduct) {
            const pf = appState.awaitingSNForProduct.parsedFields || {};
            const parts = [];
            if (pf.gtin_code) parts.push(`GTIN: ${pf.gtin_code}`);
            if (pf.batch_number) parts.push(`Batch: ${pf.batch_number}`);
            if (pf.expiry_date) parts.push(`Expiry: ${pf.expiry_date}`);
            details.textContent = parts.length ? `Product: ${parts.join(' · ')}` : 'Product data held — scan SN barcode now';
            banner.style.display = 'block';
        } else {
            banner.style.display = 'none';
        }
    }

    // Cancel the awaiting SN scan mode
    function clearAwaitingSN() {
        appState.awaitingSNForProduct = null;
        updateAwaitingSNUI();
        showAlert('↩️ SN scan cancelled. Product discarded.', 'info');
        safeResumeScanner();
    }

    // Toggle manual serial input area in the awaiting SN banner
    function toggleAwaitingSNManualInput() {
        const area = document.getElementById('awaitingSNManualArea');
        if (!area) return;
        const isHidden = area.style.display === 'none';
        area.style.display = isHidden ? 'block' : 'none';
        if (isHidden) {
            setTimeout(() => document.getElementById('awaitingSNManualInput')?.focus(), 80);
        }
    }

    // Submit manually typed serial number from the awaiting SN banner
    async function submitAwaitingSNManual() {
        const input = document.getElementById('awaitingSNManualInput');
        const sn = (input?.value || '').trim();
        if (!sn) {
            input.style.border = '2px solid #dc2626';
            showAlert('Please enter a serial number.', 'error');
            return;
        }
        if (!appState.awaitingSNForProduct) {
            showAlert('No product data waiting. Please scan the product barcode first.', 'error');
            return;
        }
        const ctx = appState.awaitingSNForProduct;
        appState.awaitingSNForProduct = null;
        updateAwaitingSNUI();
        input.value = '';
        showAlert(`Serial entered: <strong>${sn}</strong> — added to queue`, 'info');
        _addToScanQueue(ctx.qrData, sn, false, ctx.parsedFields || {});
    }

    // ─── Core scan handler — handles 2-scan (SN-first) workflow ─────────────
    // Flow:
    //   • SN-only scan   → store as pendingSerial, show banner
    //   • Details-only   → requires pendingSerial; injects it and submits
    //   • Full scan (SN+details) → submit normally
    async function handleQRScan(qrData) {
        const now = Date.now();
        if (now - appState.lastScanTime < appState.minScanInterval) {

            return;
        }
        if (appState.scanPaused) {
            showAlert('⏸️ Scanning is paused. Resume to continue.', 'info');
            return;
        }
        appState.lastScanTime = now;
        if (appState.scanner) appState.scanner.pause();

        // ── Awaiting SN scan mode (reverse 2-scan: product details held, scanning SN barcode) ──
        if (appState.awaitingSNForProduct) {
            const ctx = appState.awaitingSNForProduct;
            appState.awaitingSNForProduct = null;
            updateAwaitingSNUI();

            // Try to extract SN from this scan
            const snParsed = parseGS1Data(qrData);
            const extractedSN = normalizeScanText(snParsed.serial_number || qrData);

            if (!extractedSN) {
                showAlert('Could not extract a serial number from this scan. Try again.', 'error');
                appState.awaitingSNForProduct = ctx;
                updateAwaitingSNUI();
                safeResumeScanner();
                return;
            }

            showAlert(`Serial captured: <strong>${extractedSN}</strong> — added to queue`, 'info');
            _addToScanQueue(ctx.qrData, extractedSN, false, ctx.parsedFields || {});
            safeResumeScanner();
            return;
        }

        // Quick client-side GS1 parse — only used to detect SN-only barcodes for 2-scan workflow.
        // NOTE: The JS parser uses PROPER fixed lengths per GS1 AI spec so it doesn't misclassify
        //       full barcodes (e.g. AI 01 [14 chars fixed] followed immediately by AI 17 etc.).
        const quickParsed = parseGS1Data(qrData);
        const clientHasSN = !!quickParsed.serial_number;
        const hasAnyDetail = !!(quickParsed.gtin_code || quickParsed.batch_number || quickParsed.expiry_date);

        // SN-only barcode (serial present, no other product details) → hold as pending SN
        if (clientHasSN && !hasAnyDetail) {
            appState.pendingSerial = quickParsed.serial_number;
            updatePendingSerialUI();
            showAlert(
                `⏳ Serial <strong>${quickParsed.serial_number}</strong> captured!<br>` +
                `Now scan the <strong>product details barcode</strong> (GTIN / Batch / Expiry).`,
                'info'
            );
            safeResumeScanner();
            return;
        }

        // No serial in this scan and no pending serial from a previous 2-scan step →
        // If "Skip Serial" mode is ON, queue immediately with noSerial=true (no prompt).
        // Otherwise show the serial modal to let the user provide/skip a serial number.
        if (!clientHasSN && !appState.pendingSerial) {
            if (appState.skipSerialPrompt) {
                _addToScanQueue(qrData, null, true, quickParsed);
                // safeResumeScanner is called inside _addToScanQueue
            } else {
                _showSerialModal(qrData, null, quickParsed);
                // Camera is already paused (we called scanner.pause() at entry); modal
                // resumes it via smChooseYes / smChooseNo / smCancel / smSubmitWithSerial.
                showLoading(false);
            }
            return;
        }

        // All other cases (full scan, details-only WITH pending SN, unknown format with pending SN)
        // → add to local queue; camera resumes immediately, backend hit on “Submit”.
        // Always pass serial explicitly — pendingSerial takes priority, then client-side parsed serial.
        const effectiveSerial = appState.pendingSerial || quickParsed.serial_number || null;
        _addToScanQueue(qrData, effectiveSerial, false, quickParsed);
        safeResumeScanner();
    }

    // Send a scan to the backend; optionally inject a serial override from the 2-scan workflow
    async function _submitProductScan(qrData, serialOverride) {
        showLoading(true);
        try {
            const body = { session_key: appState.sessionKey, qr_data: qrData };
            if (serialOverride) {
                body.serial_number = serialOverride;

            }

            const response = await fetch(API.scanProduct(), {
                method: 'POST',
                headers: { 'Content-Type': 'application/json', 'X-CSRFToken': getCookie('csrftoken') },
                credentials: 'same-origin',
                body: JSON.stringify(body)
            });
            const result = await response.json();

            if (!response.ok) {
                if (result.error_code === 'SERIAL_REQUIRED') {
                    // Show interactive dialog instead of a dead-end error
                    _showSerialModal(qrData, serialOverride, result.parsed_fields || {});
                } else if (result.error_code === 'DUPLICATE_SERIAL') {
                    // Product already scanned (globally across all sessions)
                    // Clear any pending serial state since the scan is done (even if duplicate)
                    appState.pendingSerial = null;
                    updatePendingSerialUI();
                    showAlert(
                        `⚠️ <strong>Already Scanned!</strong><br>${result.error}`,
                        'warning'
                    );
                } else {
                    showAlert(`${result.error || 'Scan failed'}`, 'error');
                }
                return;
            }

            appState.scannedProducts.push(result.data);
            updateProductsList();
            const pf = result.parsed_fields || {};
            const sn = pf.serial_number || serialOverride || 'N/A';
            const linkedNote = serialOverride ? ` <em>(SN from 2-scan)</em>` : '';

            // Build a concise but complete scan-success summary line
            const summaryParts = [
                `SN: <strong>${sn}</strong>${linkedNote}`,
            ];
            if (pf.gtin_code)         summaryParts.push(`GTIN: ${pf.gtin_code}`);
            if (pf.batch_number)      summaryParts.push(`Lot: ${pf.batch_number}`);
            if (pf.expiry_date)       summaryParts.push(`Exp: ${pf.expiry_date}`);
            if (pf.production_date)   summaryParts.push(`Prod: ${pf.production_date}`);
            if (pf.best_before_date)  summaryParts.push(`BB: ${pf.best_before_date}`);
            if (pf.packaging_date)    summaryParts.push(`Pkg: ${pf.packaging_date}`);
            if (pf.quantity && pf.quantity !== 1) summaryParts.push(`Qty: ${pf.quantity}`);
            if (pf.sscc)              summaryParts.push(`SSCC: ${pf.sscc}`);
            if (pf.secondary_serial)  summaryParts.push(`SN2: ${pf.secondary_serial}`);
            if (pf.net_weight_kg)     summaryParts.push(`Wt: ${pf.net_weight_kg}kg`);
            if (pf.product_name)      summaryParts.push(`Name: ${pf.product_name}`);
            if (pf.material_code)     summaryParts.push(`Mat: ${pf.material_code}`);
            showAlert(`&#10003; Scanned! ${summaryParts.join(' · ')}`, 'success');
            // Clear pending serial after successful linked scan
            if (serialOverride && appState.pendingSerial) {
                appState.pendingSerial = null;
                updatePendingSerialUI();
            }
        } catch (error) {
            console.error('Scan error:', error);
            showAlert(`Scan Error: ${error.message}`, 'error');
        } finally {
            showLoading(false);
            safeResumeScanner();
        }
    }

    // Pause scanning (for bulk scanning mode)
    function pauseScanning() {
        appState.scanPaused = true;
        const btn = document.getElementById('pauseScanBtn');
        if (btn) {
            btn.textContent = '▶️ Resume Scanning';
            btn.style.background = '#10b981';
        }
        showAlert('⏸️ Scanning paused. Products can still be added manually.', 'info');
    }

    // Resume scanning
    function resumeScanning() {
        appState.scanPaused = false;
        const btn = document.getElementById('pauseScanBtn');
        if (btn) {
            btn.textContent = '⏸️ Pause Scanning';
            btn.style.background = '#f97316';
        }
        safeResumeScanner();
        showAlert('Scanning resumed!', 'success');
    }

    function toggleScanning() {
        if (appState.scanPaused) {
            resumeScanning();
        } else {
            pauseScanning();
        }
    }

    // Toggle "Skip Serial" mode — when ON, products without a serial number are
    // queued immediately (noSerial=true) instead of showing the serial-input modal.
    // Designed for bulk-scanning products like balloons that genuinely have no SN.
    function toggleSkipSerial() {
        appState.skipSerialPrompt = !appState.skipSerialPrompt;
        const on = appState.skipSerialPrompt;

        // Camera-mode button (has dedicated sub-elements)
        const btn    = document.getElementById('skipSerialBtn');
        const icon   = document.getElementById('skipSerialIcon');
        const lbl    = document.getElementById('skipSerialLabel');
        const sublbl = document.getElementById('skipSerialSublabel');
        const pill   = document.getElementById('skipSerialPill');

        if (btn) {
            btn.style.background = on ? '#ecfdf5'                                                    : 'linear-gradient(135deg,#fffbeb 0%,#fef3c7 100%)';
            btn.style.border     = on ? '2px solid #10b981'                                          : '2px solid #fbbf24';
            btn.style.color      = on ? '#065f46'                                                    : '#78350f';
            btn.style.boxShadow  = on ? '0 0 0 3px rgba(16,185,129,0.15)' : '0 1px 4px rgba(251,191,36,0.25)';
        }
        if (icon)   icon.textContent   = on ? '✅' : '🏷️';
        if (lbl) {
            lbl.textContent  = on ? 'No Serial Mode: ON' : 'Products Without Serial Numbers';
            lbl.style.color  = on ? '#065f46' : '#78350f';
        }
        if (sublbl) {
            sublbl.textContent  = on ? 'Serial prompts skipped — tap to disable'  : 'Tap to skip serial prompts (e.g. balloons, consumables)';
            sublbl.style.color  = on ? '#059669' : '#a16207';
        }
        if (pill) {
            pill.textContent         = on ? 'ON' : 'OFF';
            pill.style.background    = on ? '#10b981' : '#fbbf24';
            pill.style.color         = on ? '#ffffff' : '#78350f';
        }

        // Toast feedback visible in both modes
        if (on) {
            showAlert('✅ <strong>No Serial Mode ON</strong> — products without serial numbers will be queued directly.', 'success');
        } else {
            showAlert('🏷️ <strong>No Serial Mode OFF</strong> — serial prompts restored.', 'info');
        }
    }

    // Safe scanner resume — handles cases where scanner is already stopped/invalid
    function safeResumeScanner() {
        if (!appState.scanner || appState.scanPaused) return;
        try {
            appState.scanner.resume();
        } catch (err) {
            // Ignore: scanner may already be stopped or paused

        }
    }

    // ─── Scan Queue — local staging (scans queue here; backend only on Submit) ─

    function escHtml(str) {
        return String(str ?? '').replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/"/g,'&quot;').replace(/'/g,'&#39;');
    }

    function _addToScanQueue(qrData, serial, noSerial, clientParsed) {
        const id = 'sq_' + Date.now() + '_' + Math.random().toString(36).slice(2, 6);
        const parsed = clientParsed || parseGS1Data(qrData);

        // ── Client-side duplicate serial guard ─────────────────────────────────
        if (serial && !noSerial) {
            const snKey = serial.trim().toLowerCase();
            const inScanned = appState.scannedProducts.some(
                p => p.serial_number &&
                     !p.serial_number.startsWith('NSN-') &&
                     p.serial_number.trim().toLowerCase() === snKey
            );
            if (inScanned) {
                showAlert(
                    `⚠️ <strong>Already Scanned!</strong><br>` +
                    `Serial <code style="background:#fef2f2;padding:1px 5px;border-radius:3px;font-size:0.9em;">${escHtml(serial)}</code> ` +
                    `is already in this session — not added.`,
                    'warning'
                );
                safeResumeScanner();
                return;
            }
        }
        // ── End duplicate guard ────────────────────────────────────────────────

        const scanItem = {
            id, qrData,
            serial:     serial || '',
            noSerial:   !!noSerial,
            parsed,
            failed:     false,
            failReason: '',
        };

        // Clear 2-scan pendingSerial if this scan consumed it
        if (serial && appState.pendingSerial) {
            appState.pendingSerial = null;
            updatePendingSerialUI();
        }

        // Optimistic: show immediately in products list with a "saving…" spinner
        const optimisticProduct = {
            id:              id,
            queueItemId:     id,
            serial_number:   serial || parsed.serial_number || (noSerial ? 'NSN-PENDING' : ''),
            gtin_code:       parsed.gtin_code || '',
            material_code:   parsed.material_code || '',
            product_name:    parsed.product_name || '',
            batch_number:    parsed.batch_number || '',
            received_date:   parsed.received_date || null,
            expiry_date:     parsed.expiry_date || null,
            production_date: parsed.production_date || null,
            best_before_date: parsed.best_before_date || null,
            packaging_date:  parsed.packaging_date || null,
            reference_number: '',
            quantity:        parsed.quantity || 1,
            qr_data:         qrData,
            gs1_parsed_data: parsed,
            scanned_at:      new Date().toISOString(),
            updated_at:      new Date().toISOString(),
            _pending:        true,
            _queueId:        id,
        };
        appState.scannedProducts.push(optimisticProduct);
        updateProductsList();

        // Resume camera immediately — submit happens in background
        safeResumeScanner();

        // Auto-submit to backend
        _autoSubmitItem(scanItem);
    }

    function _removeScanQueueItem(id) {
        appState.scanQueue = appState.scanQueue.filter(i => i.id !== id);
        renderScanQueue();
    }

    function _editQueueLot(id, value) {
        const item = appState.scanQueue.find(i => i.id === id);
        if (item) item.batchOverride = value;
    }

    function _editQueueSerial(id, value) {
        const item = appState.scanQueue.find(i => i.id === id);
        if (item) { item.serial = value.trim(); if (value.trim()) item.noSerial = false; }
    }

    function clearScanQueue() {
        if (!appState.scanQueue.length) return;
        if (!confirm('Remove all ' + appState.scanQueue.length + ' queued scan(s)?')) return;
        appState.scanQueue = [];
        renderScanQueue();
    }

    function renderScanQueue() {
        const container = document.getElementById('scanQueueSection');
        if (!container) return;
        const q = appState.scanQueue;
        // Update header badge
        const badge = document.getElementById('sqBadge');
        if (badge) badge.textContent = q.length || '';
        if (q.length === 0) { container.style.display = 'none'; return; }
        container.style.display = 'block';
        const fmt = d => { if (!d) return ''; try { return new Date(d).toLocaleDateString('en-GB',{day:'2-digit',month:'short',year:'2-digit'}); } catch(_){return d;} };
        const failCount = q.filter(i => i.failed).length;
        const rows = q.map((item, idx) => {
            const lot     = item.batchOverride !== null ? item.batchOverride : (item.parsed.batch_number || '');
            const sn      = item.serial || '';
            const lotWarn = !lot;
            return `<div style="display:flex;align-items:center;gap:8px;padding:8px 10px;border-bottom:1px solid #f1f5f9;${item.failed?'border-left:3px solid #ef4444;background:#fef2f2;':''}">
              <span style="font-size:0.72em;color:#94a3b8;font-weight:700;min-width:20px;">#${idx+1}</span>
              <div style="flex:1;min-width:0;display:flex;flex-wrap:wrap;gap:5px;align-items:center;">
                ${item.parsed.gtin_code?`<span style="font-size:0.72em;color:#475569;font-family:monospace;background:#f1f5f9;padding:1px 5px;border-radius:3px;">${escHtml(item.parsed.gtin_code)}</span>`:''}
                <span style="display:flex;align-items:center;gap:3px;font-size:0.73em;color:#64748b;font-weight:600;">Lot:<input type="text" value="${escHtml(lot)}" oninput="_editQueueLot('${item.id}',this.value)" placeholder="lot" style="padding:2px 5px;border:1.5px solid ${lotWarn?'#f59e0b':'#d1d5db'};border-radius:4px;font-size:0.95em;font-family:monospace;width:80px;background:${lotWarn?'#fffbeb':'#fff'};"></span>
                <span style="display:flex;align-items:center;gap:3px;font-size:0.73em;color:#64748b;font-weight:600;">SN:<input type="text" value="${escHtml(sn)}" oninput="_editQueueSerial('${item.id}',this.value)" placeholder="${item.noSerial?'none':'serial'}" style="padding:2px 5px;border:1.5px solid #d1d5db;border-radius:4px;font-size:0.95em;font-family:monospace;width:100px;"></span>
                ${item.parsed.expiry_date?`<span style="font-size:0.7em;color:#059669;font-weight:600;">Exp ${fmt(item.parsed.expiry_date)}</span>`:''}
                ${item.failed?`<span style="font-size:0.7em;color:#ef4444;font-weight:600;">${escHtml(item.failReason)}</span>`:''}
              </div>
              <button onclick="_removeScanQueueItem('${item.id}')" title="Remove" style="background:#fee2e2;color:#dc2626;border:none;width:24px;height:24px;border-radius:50%;cursor:pointer;font-size:1.1em;line-height:1;flex-shrink:0;">&#215;</button>
            </div>`;
        }).join('');
        container.innerHTML = `<div style="background:#f0fdf4;border:2px solid #22c55e;border-radius:12px;overflow:visible;margin-bottom:14px;">
          <div style="display:flex;align-items:center;justify-content:space-between;padding:10px 14px;background:#dcfce7;border-bottom:1px solid #bbf7d0;border-radius:12px 12px 0 0;">
            <span style="font-weight:700;font-size:0.9em;color:#15803d;">${q.length} scan${q.length>1?'s':''} queued${failCount?` &nbsp;<span style="color:#ef4444">${failCount} failed</span>`:''}</span>
            <div style="display:flex;gap:6px;">
              <button onclick="clearScanQueue()" style="background:#fff;color:#dc2626;border:1.5px solid #fca5a5;padding:4px 10px;border-radius:6px;font-size:0.75em;font-weight:700;cursor:pointer;">Clear</button>
              <button onclick="submitScanQueue()" id="sqSubmitBtn" style="background:#16a34a;color:#fff;border:none;padding:6px 14px;border-radius:6px;font-size:0.85em;font-weight:700;cursor:pointer;min-width:80px;">Submit ${q.length}</button>
            </div>
          </div>
          <div style="max-height:280px;overflow-y:auto;">${rows}</div>
        </div>`;
    }

    async function submitScanQueue() {
        if (appState.sqSubmitting || !appState.scanQueue.length) return;
        appState.sqSubmitting = true;
        showLoading(true);
        const ids   = appState.scanQueue.map(i => i.id);
        let done = 0, failed = 0;
        for (const id of ids) {
            const item = appState.scanQueue.find(i => i.id === id);
            if (!item) continue;
            const btn = document.getElementById('sqSubmitBtn');
            if (btn) btn.textContent = `${done + 1}/${ids.length}\u2026`;
            const body = { session_key: appState.sessionKey, qr_data: item.qrData };
            if (item.noSerial) {
                body.no_serial = true;
            } else if (item.serial) {
                body.serial_number = item.serial;
            }
            // Pass lot override if user edited it in the queue UI
            const batchVal = (item.batchOverride !== null && item.batchOverride !== '') ? item.batchOverride : null;
            if (batchVal) body.batch_number = batchVal;
            try {
                const resp   = await fetch(API.scanProduct(), {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json', 'X-CSRFToken': getCookie('csrftoken') },
                    credentials: 'same-origin',
                    body: JSON.stringify(body),
                });
                const result = await resp.json();
                if (!resp.ok) {
                    if (result.error_code === 'DUPLICATE_SERIAL') {
                        // Remove optimistic product for this duplicate scan
                        appState.scannedProducts = appState.scannedProducts.filter(p => p._queueId !== id);
                        appState.scanQueue = appState.scanQueue.filter(i => i.id !== id);
                        showAlert(`⚠️ Duplicate: ${result.error || 'already scanned'} — skipped`, 'warning');
                    } else if (result.error_code === 'SERIAL_REQUIRED') {
                        // Serial was not found — pause queue, show the serial-input modal.
                        // The modal callbacks (smChooseNo / smSubmitWithSerial) will re-queue
                        // the item with the corrected serial or no-serial flag.
                        appState.scannedProducts = appState.scannedProducts.filter(p => p._queueId !== id);
                        appState.scanQueue = appState.scanQueue.filter(i => i.id !== id);
                        appState.sqSubmitting = false;
                        showLoading(false);
                        _showSerialModal(item.qrData, item.serial || null, result.parsed_fields || item.parsed || {});
                        return;  // halt queue drain — modal callbacks resume it
                    } else {
                        // Mark as failed (both queue item and optimistic product)
                        item.failed = true;
                        item.failReason = result.error || 'Failed';
                        const optimistic = appState.scannedProducts.find(p => p._queueId === id);
                        if (optimistic) {
                            optimistic._failed = true;
                            optimistic._failReason = result.error || 'Failed';
                        }
                        failed++;
                        showAlert(`✗ Scan failed: ${result.error || 'Unknown error'}`, 'error');
                    }
                } else {
                    // Success: replace optimistic item with real backend data
                    const optimisticIdx = appState.scannedProducts.findIndex(p => p._queueId === id);
                    if (optimisticIdx >= 0) {
                        appState.scannedProducts[optimisticIdx] = result.data;  // Replace with real data
                    } else {
                        appState.scannedProducts.push(result.data);  // Fallback: just add if not found
                    }
                    appState.scanQueue = appState.scanQueue.filter(i => i.id !== id);
                    done++;
                    const pf = result.parsed_fields || {};
                    const snTxt = pf.serial_number || '?';
                    showAlert(`✓ Submitted: <strong>${escHtml(snTxt)}</strong>`, 'success');
                }
            } catch (err) { item.failed = true; item.failReason = err.message; failed++; }
            renderScanQueue();
            updateProductsList();
            await new Promise(r => setTimeout(r, 80));
        }
        appState.sqSubmitting = false;
        showLoading(false);
        if (done && !failed)  showAlert(`&#10003; All ${done} scan${done > 1 ? 's' : ''} submitted successfully!`, 'success');
        else if (done)        showAlert(`${done} saved \u00b7 ${failed} failed (shown in red \u2014 fix and resubmit)`, 'warning');
        renderScanQueue();
    }

    // ── Auto-submit (no-queue flow) ────────────────────────────────────────────
    async function _autoSubmitItem(item) {
        const body = { session_key: appState.sessionKey, qr_data: item.qrData };
        if (item.noSerial) {
            body.no_serial = true;
        } else if (item.serial) {
            body.serial_number = item.serial;
        }
        try {
            const resp   = await fetch(API.scanProduct(), {
                method: 'POST',
                headers: { 'Content-Type': 'application/json', 'X-CSRFToken': getCookie('csrftoken') },
                credentials: 'same-origin',
                body: JSON.stringify(body),
            });
            const result = await resp.json();
            if (!resp.ok) {
                // Remove optimistic entry
                appState.scannedProducts = appState.scannedProducts.filter(p => p._queueId !== item.id);
                if (result.error_code === 'DUPLICATE_SERIAL') {
                    showAlert(`⚠️ Duplicate: ${result.error || 'already scanned'} — skipped`, 'warning');
                } else if (result.error_code === 'SERIAL_REQUIRED') {
                    _showSerialModal(item.qrData, item.serial || null, result.parsed_fields || item.parsed || {});
                } else {
                    // Non-recoverable error — push to error tray
                    item.failed     = true;
                    item.failReason = result.error || 'Submission failed';
                    appState.errorTray.push(item);
                    renderErrorTray();
                    showAlert(`✗ Scan failed: ${result.error || 'Unknown error'} — check error tray below`, 'error');
                }
                updateProductsList();
            } else {
                // Replace optimistic with confirmed backend data
                const idx = appState.scannedProducts.findIndex(p => p._queueId === item.id);
                if (idx >= 0) {
                    appState.scannedProducts[idx] = result.data;
                } else {
                    appState.scannedProducts.push(result.data);
                }
                updateProductsList();
                const pf    = result.parsed_fields || {};
                const snTxt = pf.serial_number || item.serial || (item.noSerial ? '(no serial)' : '?');
                const bn    = (pf.batch_number || item.parsed.batch_number) ? ` · Lot: ${pf.batch_number || item.parsed.batch_number}` : '';
                showAlert(`✓ Saved: <strong>${escHtml(snTxt)}</strong>${bn}`, 'success');
            }
        } catch (err) {
            appState.scannedProducts = appState.scannedProducts.filter(p => p._queueId !== item.id);
            item.failed     = true;
            item.failReason = err.message;
            appState.errorTray.push(item);
            renderErrorTray();
            updateProductsList();
        }
    }

    // ── Error tray (retryable failed scans) ───────────────────────────────────
    function renderErrorTray() {
        const container = document.getElementById('errorTraySection');
        if (!container) return;
        const tray = appState.errorTray;
        if (!tray.length) { container.style.display = 'none'; return; }
        container.style.display = 'block';
        const fmt = d => { if (!d) return ''; try { return new Date(d).toLocaleDateString('en-GB',{day:'2-digit',month:'short',year:'2-digit'}); } catch(_){return d;} };
        const rows = tray.map(item => {
            const sn  = item.serial || (item.noSerial ? '(no serial)' : '—');
            const lot = item.parsed.batch_number || '—';
            return `<div style="display:flex;align-items:center;gap:8px;padding:9px 12px;border-bottom:1px solid #fee2e2;">
              <div style="flex:1;min-width:0;">
                <div style="display:flex;flex-wrap:wrap;gap:6px;align-items:center;margin-bottom:3px;">
                  ${item.parsed.gtin_code ? `<span style="font-size:0.72em;color:#475569;font-family:monospace;background:#f1f5f9;padding:1px 5px;border-radius:3px;">${escHtml(item.parsed.gtin_code)}</span>` : ''}
                  <span style="font-size:0.73em;color:#64748b;">SN: <strong>${escHtml(sn)}</strong></span>
                  <span style="font-size:0.73em;color:#64748b;">Lot: ${escHtml(lot)}</span>
                  ${item.parsed.expiry_date ? `<span style="font-size:0.7em;color:#059669;font-weight:600;">Exp ${fmt(item.parsed.expiry_date)}</span>` : ''}
                </div>
                <div style="font-size:0.72em;color:#ef4444;font-weight:600;">&#x26A0; ${escHtml(item.failReason)}</div>
              </div>
              <div style="display:flex;gap:5px;flex-shrink:0;">
                <button onclick="_retryErrorItem('${item.id}')" style="background:#2563eb;color:#fff;border:none;padding:5px 12px;border-radius:6px;font-size:0.76em;font-weight:700;cursor:pointer;">Retry</button>
                <button onclick="_dismissErrorItem('${item.id}')" title="Dismiss" style="background:#fee2e2;color:#dc2626;border:none;width:26px;height:26px;border-radius:50%;cursor:pointer;font-size:1.1em;line-height:1;flex-shrink:0;">&#215;</button>
              </div>
            </div>`;
        }).join('');
        container.innerHTML = `<div style="background:#fff5f5;border:2px solid #ef4444;border-radius:12px;overflow:hidden;margin-bottom:14px;">
          <div style="display:flex;align-items:center;justify-content:space-between;padding:8px 14px;background:#fee2e2;border-bottom:1px solid #fecaca;">
            <span style="font-weight:700;font-size:0.88em;color:#b91c1c;">&#x26A0; ${tray.length} scan${tray.length > 1 ? 's' : ''} failed — retry or dismiss</span>
            <button onclick="_clearErrorTray()" style="background:#fff;color:#dc2626;border:1.5px solid #fca5a5;padding:3px 10px;border-radius:6px;font-size:0.75em;font-weight:700;cursor:pointer;">Clear all</button>
          </div>
          <div>${rows}</div>
        </div>`;
    }

    function _retryErrorItem(id) {
        const idx = appState.errorTray.findIndex(i => i.id === id);
        if (idx < 0) return;
        const item = appState.errorTray.splice(idx, 1)[0];
        item.failed     = false;
        item.failReason = '';
        renderErrorTray();
        // Re-add optimistic entry and re-submit
        appState.scannedProducts.push({
            id: item.id, queueItemId: item.id,
            serial_number:   item.serial || item.parsed.serial_number || (item.noSerial ? 'NSN-PENDING' : ''),
            gtin_code:       item.parsed.gtin_code || '',
            material_code:   item.parsed.material_code || '',
            product_name:    item.parsed.product_name || '',
            batch_number:    item.parsed.batch_number || '',
            expiry_date:     item.parsed.expiry_date || null,
            qr_data:         item.qrData,
            gs1_parsed_data: item.parsed,
            scanned_at:      new Date().toISOString(),
            updated_at:      new Date().toISOString(),
            _pending:        true,
            _queueId:        item.id,
        });
        updateProductsList();
        _autoSubmitItem(item);
    }

    function _dismissErrorItem(id) {
        appState.errorTray = appState.errorTray.filter(i => i.id !== id);
        renderErrorTray();
    }

    function _clearErrorTray() {
        appState.errorTray = [];
        renderErrorTray();
    }

    // Image Upload Functions (disabled — not needed right now)
    /*
    function toggleImageUpload() {
        const fileInput = document.getElementById('qrImageUpload');
        fileInput.click();
    }

    function processImageUpload(event) {
        const file = event.target.files[0];
        if (!file) return;
        if (!file.type.startsWith('image/')) {
            showAlert('Please select a valid image file', 'error');
            return;
        }
        const reader = new FileReader();
        reader.onload = function(e) {
            const imageData = e.target.result;
            const preview = document.getElementById('imagePreview');
            preview.src = imageData;
            document.getElementById('imagePreviewContainer').style.display = 'block';
            appState.uploadedImageData = imageData;
        };
        reader.readAsDataURL(file);
    }

    async function decodeUploadedImage() { ... }

    function clearImagePreview() {
        document.getElementById('imagePreviewContainer').style.display = 'none';
        document.getElementById('imagePreview').src = '';
        document.getElementById('qrImageUpload').value = '';
        appState.uploadedImageData = null;
    }
    */

    // Parse JSON or pipe-delimited QR data to consistent format (with GS1 support)
    function parseQRData(input) {
        let qrData = '';
        
        // Try GS1 format first
        const hasGS1 = /(\u001d|\[GS\]|\x1d|^01\d{2}|^10\d{2}|^17\d{2}|^21\d{2})/.test(input);
        if (hasGS1) {
            const parsed = parseGS1Data(input);
            if (Object.keys(parsed).length > 0) {
                const parts = [];
                if (parsed.serial) parts.push(`SERIAL:${parsed.serial}`);
                if (parsed.gtin) parts.push(`GTIN:${parsed.gtin}`);
                if (parsed.batch) parts.push(`BATCH:${parsed.batch}`);
                if (parsed.expiry_date) parts.push(`EXPIRY:${parsed.expiry_date}`);
                if (parts.length > 0) return parts.join('|');
            }
        }
        
        // Try JSON format
        try {
            const json = JSON.parse(input);
            const parts = [];
            if (json.serial_number || json.sn) parts.push(`SERIAL:${json.serial_number || json.sn}`);
            if (json.gtin_code || json.gtin) parts.push(`GTIN:${json.gtin_code || json.gtin}`);
            if (json.material_code || json.matcode) parts.push(`MATCODE:${json.material_code || json.matcode}`);
            if (json.product_name || json.name) parts.push(`NAME:${json.product_name || json.name}`);
            if (json.batch_number || json.batch) parts.push(`BATCH:${json.batch_number || json.batch}`);
            if (json.received_date || json.received) parts.push(`RECEIVED:${json.received_date || json.received}`);
            if (json.expiry_date || json.expiry) parts.push(`EXPIRY:${json.expiry_date || json.expiry}`);
            if (json.quantity || json.qty) parts.push(`QTY:${json.quantity || json.qty}`);
            
            if (parts.length > 0) {
                qrData = parts.join('|');

                return qrData;
            }
        } catch (e) {
            // Not JSON, try pipe format or raw barcode
        }
        
        // Check if input is raw barcode (pure digits, 8-14 characters)
        if (/^\d+$/.test(input) && input.length >= 8 && input.length <= 14) {

            return `GTIN:${input}`;
        }
        
        // Return input as-is if it looks like pipe-delimited format
        if (input.includes('|') || input.includes(':')) {
            return input;
        }
        
        throw new Error('Invalid QR format. Enter: pipe-delimited (SERIAL:xx|GTIN:xx), JSON, raw barcode (8-14 digits), or GS1 DataMatrix');
    }

    // Process Manual QR Input (raw QR data) — send directly to backend GS1 parser
    async function processManualQR() {
        const input = document.getElementById('manualQRInput').value.trim();
        if (!input) {
            showAlert('Please enter QR data', 'error');
            return;
        }

        // Client-side pre-check: if no serial found and no pending serial,
        // show the serial modal immediately (avoids SERIAL_REQUIRED round-trip).
        const quickParsed = parseGS1Data(input);
        const clientHasSN = !!quickParsed.serial_number;
        if (!clientHasSN && !appState.pendingSerial) {
            _showSerialModal(input, null, quickParsed);
            document.getElementById('manualQRInput').value = '';
            return;
        }

        // Add to local queue; inject pendingSerial from 2-scan workflow if available
        const manualEffectiveSerial = appState.pendingSerial || quickParsed.serial_number || null;
        _addToScanQueue(input, manualEffectiveSerial, false, quickParsed);
        document.getElementById('manualQRInput').value = '';
    }

    // Submit Manual Field Entry — Serial Number is required (or pendingSerial from 2-scan workflow)
    async function submitManualFields() {
        const serial = document.getElementById('f_serial').value.trim();
        const gtin = document.getElementById('f_gtin').value.trim();
        const name = document.getElementById('f_name').value.trim();
        const matcode = document.getElementById('f_matcode').value.trim();
        const batch = document.getElementById('f_batch').value.trim();
        const received = document.getElementById('f_received').value.trim();
        const expiry = document.getElementById('f_expiry').value.trim();
        const qty = document.getElementById('f_qty').value.trim();

        // Use entered serial OR pending serial from 2-scan workflow
        const effectiveSerial = serial || appState.pendingSerial;

        if (!effectiveSerial) {
            showAlert('Serial Number is required. Enter it in the Serial Number field above, or scan the SN barcode first.', 'error');
            return;
        }

        // Build QR data
        const parts = [];
        if (effectiveSerial) parts.push(`SERIAL:${effectiveSerial}`);
        if (matcode) parts.push(`MATCODE:${matcode}`);
        if (gtin) parts.push(`GTIN:${gtin}`);
        if (name) parts.push(`NAME:${name}`);
        if (batch) parts.push(`BATCH:${batch}`);
        if (received) parts.push(`RECEIVED:${received}`);
        if (expiry) parts.push(`EXPIRY:${expiry}`);
        if (qty) parts.push(`QTY:${qty}`);

        const qrData = parts.join('|');

        _addToScanQueue(qrData, null, false, {
            serial_number: effectiveSerial, gtin_code: gtin, batch_number: batch,
            expiry_date: expiry, product_name: name, material_code: matcode,
        });
        // Clear form fields
        ['f_serial','f_gtin','f_matcode','f_name','f_batch','f_received','f_expiry','f_qty'].forEach(id => {
            document.getElementById(id).value = '';
        });
    }

    // Update Products List
    function updateProductsList() {
        const container = document.getElementById('productsList');
        const count = appState.scannedProducts.length;
        document.getElementById('productCount').textContent = count;

        if (count === 0) {
            container.innerHTML = '<div class="empty-state"><div class="empty-state-icon"></div><p>No products scanned yet</p></div>';
            return;
        }

        const fmt = (d) => {
            if (!d) return '';
            try { return new Date(d).toLocaleDateString('en-GB', {day:'2-digit', month:'short', year:'numeric'}); }
            catch { return d; }
        };

        // Helper: display serial (hide auto-generated NSN- placeholders)
        const fmtSN = (sn) => {
            if (!sn) return null;
            return sn.startsWith('NSN-') ? '<em style="color:#9ca3af;">No Serial</em>' : `<span>${sn}</span>`;
        };

        // Build rows in reverse order (newest first)
        const rows = [...appState.scannedProducts].reverse().map((product, revIdx) => {
            // original index for delete / edit
            const origIdx = appState.scannedProducts.length - 1 - revIdx;

            // Merge dedicated model fields + anything stored in gs1_parsed_data
            const extra = product.gs1_parsed_data || {};

            const pills = [
                product.serial_number   ? `<span class="product-pill">SN ${fmtSN(product.serial_number)}</span>` : '',
                product.gtin_code       ? `<span class="product-pill">GTIN <span>${product.gtin_code}</span></span>` : '',
                product.batch_number    ? `<span class="product-pill">Lot <span>${product.batch_number}</span></span>` : '',
                product.expiry_date     ? `<span class="product-pill" style="color:${isDateSoon(product.expiry_date)?'#ef4444':'#059669'};">Exp <span>${fmt(product.expiry_date)}</span></span>` : '',
                product.production_date   ? `<span class="product-pill">Prod <span>${fmt(product.production_date)}</span></span>` : '',
                product.best_before_date  ? `<span class="product-pill">BB <span>${fmt(product.best_before_date)}</span></span>` : '',
                product.packaging_date    ? `<span class="product-pill">Pkg <span>${fmt(product.packaging_date)}</span></span>` : '',
                (product.quantity && product.quantity !== 1) ? `<span class="product-pill">Qty <span>${product.quantity}</span></span>` : '',
                product.product_name    ? `<span class="product-pill">Name <span>${product.product_name}</span></span>` : '',
                product.material_code   ? `<span class="product-pill">Mat <span>${product.material_code}</span></span>` : ``,
                // Extra GS1 fields from gs1_parsed_data
                extra.sscc              ? `<span class="product-pill">SSCC <span>${extra.sscc}</span></span>` : '',
                extra.secondary_serial  ? `<span class="product-pill">SN2 <span>${extra.secondary_serial}</span></span>` : '',
                extra.secondary_data    ? `<span class="product-pill">Sec <span>${extra.secondary_data}</span></span>` : '',
                extra.additional_id     ? `<span class="product-pill">AltID <span>${extra.additional_id}</span></span>` : '',
                extra.customer_part     ? `<span class="product-pill">CustPart <span>${extra.customer_part}</span></span>` : '',
                extra.net_weight_kg     ? `<span class="product-pill">Wt <span>${extra.net_weight_kg}kg</span></span>` : '',
                extra.volume_l          ? `<span class="product-pill">Vol <span>${extra.volume_l}L</span></span>` : '',
                extra.purchase_order    ? `<span class="product-pill">PO <span>${extra.purchase_order}</span></span>` : '',
                extra.consignment_number? `<span class="product-pill">Consign <span>${extra.consignment_number}</span></span>` : '',
                extra.expiry_time       ? `<span class="product-pill">ExpTime <span>${extra.expiry_time}</span></span>` : '',
                extra.due_date          ? `<span class="product-pill">Due <span>${fmt(extra.due_date)}</span></span>` : '',
                extra.sell_by_date      ? `<span class="product-pill">SellBy <span>${fmt(extra.sell_by_date)}</span></span>` : '',
                extra.variant           ? `<span class="product-pill">Var <span>${extra.variant}</span></span>` : '',
                extra.nato_stock_number ? `<span class="product-pill">NATO <span>${extra.nato_stock_number}</span></span>` : '',
                extra.giai              ? `<span class="product-pill">GIAI <span>${extra.giai}</span></span>` : '',
                extra.grai              ? `<span class="product-pill">GRAI <span>${extra.grai}</span></span>` : '',
                extra.gsrn              ? `<span class="product-pill">GSRN <span>${extra.gsrn}</span></span>` : '',
                extra.product_url       ? `<span class="product-pill"><a href="${extra.product_url}" target="_blank" rel="noopener" style="color:inherit;">URL</a></span>` : '',
            ].filter(Boolean).join('');
            
            // Add status indicator for pending/failed items
            const statusBadge = product._failed 
                ? `<span style="display:inline-block;background:#fee2e2;color:#dc2626;padding:2px 8px;border-radius:4px;font-size:0.7em;font-weight:700;margin-left:8px;">✗ ${product._failReason || 'Failed'}</span>`
                : product._pending
                ? `<span style="display:inline-block;background:#fef3c7;color:#92400e;padding:2px 8px;border-radius:4px;font-size:0.7em;font-weight:700;margin-left:8px;animation:pulse 1.5s ease-in-out infinite;">⏳ Pending</span>`
                : '';
            
            const rowStyle = product._failed
                ? 'border-left:3px solid #ef4444;background:#fef2f2;'
                : product._pending
                ? 'border-left:3px solid #fbbf24;background:#fffbeb;'
                : '';
            const rowExtraClass = product._remark && !product._failed && !product._pending ? ' has-remark' : '';

            // Remark preview: show first 60 chars of saved remark
            const remarkPreview = product._remark
                ? `<div style="margin-top:6px;padding:5px 8px;background:#f5f3ff;border-left:3px solid #7c3aed;border-radius:4px;font-size:0.76em;color:#5b21b6;line-height:1.4;">💬 ${product._remark.length > 60 ? product._remark.slice(0,60)+'…' : product._remark}</div>`
                : '';

            // Remark button (only for successfully saved products)
            const remarkBtn = product.id && !product._pending && !product._failed
                ? `<button onclick="toggleProductRemark(${origIdx})" title="Add Remark"
                        style="background:${product._remark?'#f5f3ff':'#f8fafc'};color:${product._remark?'#7c3aed':'#64748b'};
                               padding:8px 10px;font-size:0.78em;min-width:36px;
                               border:1px solid ${product._remark?'#ddd6fe':'#e2e8f0'};border-radius:6px;
                               cursor:pointer;font-weight:700;">💬</button>`
                : '';

            // Inline remark editor (hidden by default)
            const remarkEditor = `
                <div id="remark-editor-${origIdx}" style="display:none;margin-top:8px;padding:10px;background:#faf5ff;border:1px solid #ddd6fe;border-radius:8px;">
                    <textarea id="remark-text-${origIdx}" placeholder="Add a remark about this item…"
                        style="width:100%;padding:8px 10px;border:1.5px solid #c4b5fd;border-radius:6px;font-size:0.85em;resize:vertical;min-height:56px;font-family:inherit;outline:none;"
                        onfocus="this.style.borderColor='#7c3aed'" onblur="this.style.borderColor='#c4b5fd'"
                    >${product._remark || ''}</textarea>
                    <div style="display:flex;gap:8px;margin-top:6px;justify-content:flex-end;">
                        <button onclick="toggleProductRemark(${origIdx})"
                            style="background:#f1f5f9;color:#64748b;border:1px solid #e2e8f0;padding:6px 14px;border-radius:6px;font-size:0.8em;cursor:pointer;">Cancel</button>
                        <button onclick="saveProductRemark(${origIdx})"
                            style="background:#7c3aed;color:#fff;border:none;padding:6px 14px;border-radius:6px;font-size:0.8em;font-weight:700;cursor:pointer;">Save Remark</button>
                    </div>
                </div>`;

            return `<div class="product-item${rowExtraClass}" id="product-row-${origIdx}" style="${rowStyle}">
                <div class="product-item-main">${pills || '<em style="color:#999;font-size:0.8em;">No fields extracted</em>'}${statusBadge}${remarkPreview}</div>
                <div class="product-actions">
                    <button class="btn-edit" onclick="editProduct(${origIdx})" title="Edit" style="background:#eff6ff;color:#2563eb;padding:8px 10px;font-size:0.78em;min-width:36px;border:1px solid #bfdbfe;border-radius:6px;cursor:pointer;font-weight:700;">&#9998;</button>
                    ${remarkBtn}
                    <button class="btn-delete" onclick="deleteProduct(${origIdx})" title="Remove">&times;</button>
                </div>
                ${remarkEditor}
            </div>`;
        });

        container.innerHTML =
            `<div class="scan-list-header" style="display:flex;justify-content:space-between;align-items:center;">
                <span>Scanned Items (${count})</span>
                <div style="display:flex;align-items:center;gap:10px;">
                    <span style="font-weight:400;font-size:0.85em;">latest first</span>
                    <button onclick="clearAllProducts()" style="background:#fee2e2;color:#dc2626;border:1px solid #fecaca;padding:4px 10px;border-radius:6px;font-size:0.75em;font-weight:700;cursor:pointer;">Clear All</button>
                </div>
            </div>` +
            `<div style="border:1px solid rgba(0,0,0,0.08);border-top:none;border-radius:0 0 6px 6px;background:#ffffff;">${rows.join('')}</div>`;
    }

    // Check if expiry date is within 30 days
    function isDateSoon(dateStr) {
        if (!dateStr) return false;
        try {
            const expiryDate = new Date(dateStr);
            const today = new Date();
            const thirtyDaysAhead = new Date(today.getTime() + 30 * 24 * 60 * 60 * 1000);
            return expiryDate <= thirtyDaysAhead && expiryDate >= today;
        } catch {
            return false;
        }
    }

    // ── Product-level Remarks (salesperson side) ──────────────────────────

    function toggleProductRemark(origIdx) {
        const editor = document.getElementById(`remark-editor-${origIdx}`);
        if (!editor) return;
        const isVisible = editor.style.display !== 'none';
        editor.style.display = isVisible ? 'none' : 'block';
        if (!isVisible) {
            const ta = document.getElementById(`remark-text-${origIdx}`);
            if (ta) setTimeout(() => ta.focus(), 50);
        }
    }

    async function saveProductRemark(origIdx) {
        const product = appState.scannedProducts[origIdx];
        if (!product || !product.id) return;
        const ta = document.getElementById(`remark-text-${origIdx}`);
        const text = (ta ? ta.value : '').trim();
        if (!text) {
            // Clear remark if empty
            product._remark = '';
            toggleProductRemark(origIdx);
            updateProductsList();
            return;
        }
        try {
            const resp = await fetch('/product_scanning/api/product-remarks/', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json', 'X-CSRFToken': getCookie('csrftoken') },
                credentials: 'same-origin',
                body: JSON.stringify({ product: product.id, remark: text }),
            });
            if (!resp.ok) throw new Error('Failed to save remark');
            product._remark = text;
            toggleProductRemark(origIdx);
            updateProductsList();
        } catch (err) {
            showAlert('Could not save remark: ' + err.message, 'error');
        }
    }

    // Edit Product — bottom-sheet modal
    function editProduct(index) {
        const product = appState.scannedProducts[index];
        if (!product) return;

        // Close any already-open edit sheet
        _closeEditSheet();

        const toDateInput = (d) => {
            if (!d) return '';
            try { return new Date(d).toISOString().slice(0, 10); } catch { return d; }
        };

        const isFakeSerial = product.serial_number && product.serial_number.startsWith('NSN-');
        const serialVal    = isFakeSerial ? '' : (product.serial_number || '');

        // Build sheet HTML
        const sheet = document.createElement('div');
        sheet.id = 'editProductSheet';
        sheet.setAttribute('data-index', index);
        sheet.innerHTML = `
            <div id="editProductBackdrop" onclick="cancelProductEdit()" style="
                position:fixed;inset:0;background:rgba(15,23,42,0.55);z-index:1200;
                backdrop-filter:blur(3px);animation:fadeIn 0.18s ease;"></div>
            <div style="
                position:fixed;bottom:0;left:50%;transform:translateX(-50%);
                width:min(560px,100%);max-height:88vh;overflow-y:auto;
                background:#fff;border-radius:20px 20px 0 0;
                box-shadow:0 -8px 40px rgba(0,0,0,0.22);
                z-index:1201;padding:0 0 env(safe-area-inset-bottom,0) 0;
                animation:slideUpSheet 0.22s cubic-bezier(0.34,1.12,0.64,1);">

                <!-- Handle -->
                <div style="text-align:center;padding:12px 0 4px;">
                    <div style="width:36px;height:4px;background:#e2e8f0;border-radius:2px;display:inline-block;"></div>
                </div>

                <!-- Header -->
                <div style="display:flex;justify-content:space-between;align-items:center;padding:6px 18px 14px;border-bottom:1px solid #f1f5f9;">
                    <div>
                        <div style="font-weight:800;font-size:1em;color:#0f172a;">Edit Scan</div>
                        <div style="font-size:0.75em;color:#94a3b8;margin-top:2px;">Item #${appState.scannedProducts.length - index} &nbsp;&middot;&nbsp; Correct any field below</div>
                    </div>
                    <button onclick="cancelProductEdit()" style="background:#f1f5f9;color:#64748b;border:none;width:34px;height:34px;border-radius:50%;font-size:1.1em;cursor:pointer;display:flex;align-items:center;justify-content:center;font-weight:700;">&#x2715;</button>
                </div>

                <!-- Fields -->
                <div style="padding:16px 18px;">
                    <!-- Serial Number — full width -->
                    <div style="margin-bottom:12px;">
                        <label style="font-size:0.72em;font-weight:700;color:#64748b;text-transform:uppercase;letter-spacing:0.06em;display:block;margin-bottom:5px;">Serial Number</label>
                        <input id="edt_serial" value="${serialVal}" autocomplete="off" autocorrect="off"
                            placeholder="${isFakeSerial ? 'None — leave blank to keep no-serial' : 'e.g. SN0012345'}"
                            style="width:100%;padding:11px 13px;border:2px solid #e2e8f0;border-radius:10px;font-family:ui-monospace,monospace;font-size:0.92em;color:#0f172a;outline:none;transition:border-color 0.15s;"
                            onfocus="this.style.borderColor='#0076c8'" onblur="this.style.borderColor='#e2e8f0'">
                    </div>

                    <!-- GTIN + Batch row -->
                    <div style="display:grid;grid-template-columns:1fr 1fr;gap:10px;margin-bottom:12px;">
                        <div>
                            <label style="font-size:0.72em;font-weight:700;color:#64748b;text-transform:uppercase;letter-spacing:0.06em;display:block;margin-bottom:5px;">GTIN</label>
                            <input id="edt_gtin" value="${product.gtin_code || ''}" autocomplete="off"
                                placeholder="14-digit GTIN"
                                style="width:100%;padding:11px 13px;border:2px solid #e2e8f0;border-radius:10px;font-family:ui-monospace,monospace;font-size:0.88em;color:#0f172a;outline:none;transition:border-color 0.15s;"
                                onfocus="this.style.borderColor='#0076c8'" onblur="this.style.borderColor='#e2e8f0'">
                        </div>
                        <div>
                            <label style="font-size:0.72em;font-weight:700;color:#64748b;text-transform:uppercase;letter-spacing:0.06em;display:block;margin-bottom:5px;">Batch / Lot</label>
                            <input id="edt_batch" value="${product.batch_number || ''}" autocomplete="off"
                                placeholder="e.g. BT2024"
                                style="width:100%;padding:11px 13px;border:2px solid #e2e8f0;border-radius:10px;font-size:0.88em;color:#0f172a;outline:none;transition:border-color 0.15s;"
                                onfocus="this.style.borderColor='#0076c8'" onblur="this.style.borderColor='#e2e8f0'">
                        </div>
                    </div>

                    <!-- Expiry + Qty row -->
                    <div style="display:grid;grid-template-columns:1fr 1fr;gap:10px;margin-bottom:12px;">
                        <div>
                            <label style="font-size:0.72em;font-weight:700;color:#64748b;text-transform:uppercase;letter-spacing:0.06em;display:block;margin-bottom:5px;">Expiry Date</label>
                            <input id="edt_expiry" type="date" value="${toDateInput(product.expiry_date)}"
                                style="width:100%;padding:11px 13px;border:2px solid #e2e8f0;border-radius:10px;font-size:0.88em;color:#0f172a;outline:none;transition:border-color 0.15s;"
                                onfocus="this.style.borderColor='#f59e0b'" onblur="this.style.borderColor='#e2e8f0'">
                        </div>
                        <div>
                            <label style="font-size:0.72em;font-weight:700;color:#64748b;text-transform:uppercase;letter-spacing:0.06em;display:block;margin-bottom:5px;">Qty</label>
                            <input id="edt_qty" type="number" min="1" value="${product.quantity || 1}"
                                style="width:100%;padding:11px 13px;border:2px solid #e2e8f0;border-radius:10px;font-size:0.88em;color:#0f172a;outline:none;transition:border-color 0.15s;"
                                onfocus="this.style.borderColor='#0076c8'" onblur="this.style.borderColor='#e2e8f0'">
                        </div>
                    </div>

                    <!-- Material Code — full width -->
                    <div style="margin-bottom:16px;">
                        <label style="font-size:0.72em;font-weight:700;color:#64748b;text-transform:uppercase;letter-spacing:0.06em;display:block;margin-bottom:5px;">Material Code</label>
                        <input id="edt_matcode" value="${product.material_code || ''}" autocomplete="off"
                            placeholder="SAP material code"
                            style="width:100%;padding:11px 13px;border:2px solid #e2e8f0;border-radius:10px;font-size:0.88em;color:#0f172a;outline:none;transition:border-color 0.15s;"
                            onfocus="this.style.borderColor='#0076c8'" onblur="this.style.borderColor='#e2e8f0'">
                    </div>

                    <!-- Validation message -->
                    <div id="editSheetMsg" style="display:none;margin-bottom:10px;padding:9px 12px;border-radius:8px;font-size:0.82em;font-weight:600;"></div>

                    <!-- Action buttons -->
                    <div style="display:flex;gap:10px;">
                        <button onclick="cancelProductEdit()" style="flex:1;background:#f8fafc;color:#64748b;border:1.5px solid #e2e8f0;padding:13px;border-radius:12px;font-weight:700;font-size:0.88em;cursor:pointer;">Cancel</button>
                        <button id="editSaveBtn" onclick="saveProductEdit()" style="flex:2;background:linear-gradient(135deg,#0076c8,#0062cc);color:white;border:none;padding:13px;border-radius:12px;font-weight:700;font-size:0.88em;cursor:pointer;box-shadow:0 4px 14px rgba(0,98,204,0.3);">Save Changes</button>
                    </div>
                </div>
            </div>`;

        document.body.appendChild(sheet);
        // Focus first field after animation
        setTimeout(() => document.getElementById('edt_serial')?.focus(), 220);
    }

    function _closeEditSheet() {
        const existing = document.getElementById('editProductSheet');
        if (existing) existing.remove();
    }

    function cancelProductEdit() {
        _closeEditSheet();
    }

    async function saveProductEdit() {
        const sheet = document.getElementById('editProductSheet');
        if (!sheet) return;
        const index = parseInt(sheet.getAttribute('data-index'));
        const product = appState.scannedProducts[index];
        if (!product) return;

        const serial  = document.getElementById('edt_serial')?.value.trim()  || '';
        const gtin    = document.getElementById('edt_gtin')?.value.trim()    || '';
        const batch   = document.getElementById('edt_batch')?.value.trim()   || '';
        const expiry  = document.getElementById('edt_expiry')?.value.trim()  || '';
        const matcode = document.getElementById('edt_matcode')?.value.trim() || '';
        const qty     = parseInt(document.getElementById('edt_qty')?.value)  || 1;

        // Build patch — never send serial_number: null (model requires a value).
        // If the user left the serial field blank, omit it from the patch so the
        // server preserves the existing value (including NSN- placeholders).
        const patch = {
            gtin_code:     gtin    || null,
            batch_number:  batch   || null,
            expiry_date:   expiry  || null,
            material_code: matcode || null,
            quantity:      qty,
        };
        if (serial) patch.serial_number = serial;

        const saveBtn = document.getElementById('editSaveBtn');
        const msgEl   = document.getElementById('editSheetMsg');
        if (saveBtn) { saveBtn.disabled = true; saveBtn.textContent = 'Saving…'; }

        // Update server if product has a DB id
        if (product.id) {
            try {
                const resp = await fetch(`/product_scanning/api/products/${product.id}/`, {
                    method: 'PATCH',
                    headers: { 'Content-Type': 'application/json', 'X-CSRFToken': getCookie('csrftoken') },
                    credentials: 'same-origin',
                    body: JSON.stringify(patch),
                });
                if (!resp.ok) {
                    const err = await resp.json().catch(() => ({}));
                    if (msgEl) {
                        msgEl.textContent = `Could not save: ${err.error || resp.statusText}`;
                        msgEl.style.cssText = 'display:block;background:#fee2e2;color:#dc2626;margin-bottom:10px;padding:9px 12px;border-radius:8px;font-size:0.82em;font-weight:600;';
                    }
                    if (saveBtn) { saveBtn.disabled = false; saveBtn.textContent = 'Save Changes'; }
                    return;
                }
                const updated = await resp.json();
                Object.assign(appState.scannedProducts[index], updated);
            } catch (e) {
                if (msgEl) {
                    msgEl.textContent = `Network error: ${e.message}`;
                    msgEl.style.cssText = 'display:block;background:#fee2e2;color:#dc2626;margin-bottom:10px;padding:9px 12px;border-radius:8px;font-size:0.82em;font-weight:600;';
                }
                if (saveBtn) { saveBtn.disabled = false; saveBtn.textContent = 'Save Changes'; }
                return;
            }
        } else {
            Object.assign(appState.scannedProducts[index], patch);
        }

        _closeEditSheet();
        updateProductsList();
        showAlert('Changes saved.', 'success');
    }

    // Clear All Products
    async function clearAllProducts() {
        if (appState.scannedProducts.length === 0) return;
        if (!confirm(`Remove all ${appState.scannedProducts.length} scanned items? This cannot be undone.`)) return;

        showLoading(true);
        const ids = appState.scannedProducts.map(p => p.id).filter(Boolean);

        // Delete each from server in parallel
        await Promise.all(ids.map(id =>
            fetch(`/product_scanning/api/products/${id}/`, {
                method: 'DELETE',
                headers: { 'X-CSRFToken': getCookie('csrftoken') },
                credentials: 'same-origin',
            }).catch(() => {})
        ));

        appState.scannedProducts = [];
        updateProductsList();
        showLoading(false);
        showAlert('All scanned items cleared.', 'info');
    }

    // Delete Product — removes from DB first so the serial is freed for re-scanning
    async function deleteProduct(index) {
        const product = appState.scannedProducts[index];
        if (!product) return;

        // If the product has a DB id, delete it on the server first
        if (product.id) {
            try {
                const resp = await fetch(`/product_scanning/api/products/${product.id}/`, {
                    method: 'DELETE',
                    headers: { 'X-CSRFToken': getCookie('csrftoken') },
                    credentials: 'same-origin',
                });
                if (!resp.ok && resp.status !== 404) {
                    const err = await resp.json().catch(() => ({}));
                    showAlert(`Could not remove product: ${err.error || resp.statusText}`, 'error');
                    return; // Don't remove from UI if server rejected it
                }
            } catch (e) {
                showAlert(`Network error while removing product: ${e.message}`, 'error');
                return;
            }
        }

        appState.scannedProducts.splice(index, 1);
        updateProductsList();
        showAlert('Product removed — you can scan it again.', 'info');
    }

    // Finish Scanning
    async function finishScanning() {
        // Warn if any scans are still being auto-submitted in the background
        const pendingCount = appState.scannedProducts.filter(p => p._pending).length;
        if (pendingCount > 0) {
            showAlert(`⏳ ${pendingCount} scan${pendingCount > 1 ? 's' : ''} still saving… please wait a moment and try again.`, 'warning');
            return;
        }
        // Warn if any scans are in the error tray (failed, not yet retried/dismissed)
        if (appState.errorTray.length > 0) {
            if (!confirm(`${appState.errorTray.length} scan${appState.errorTray.length > 1 ? 's' : ''} failed to save and will be discarded. Complete session anyway?`)) return;
        }

        if (appState.scannedProducts.length === 0) {
            showAlert('Please scan at least one product', 'error');
            return;
        }

        // Stop scanner
        if (appState.scanner) {
            teardownPinchZoom();
            try {
                appState.scanner.stop();
            } catch (err) {
                console.warn('Scanner stop error (already stopped?):', err);
            }
        }

        hideSection('step2');
        showSection('step3');
        updateStepIndicator(3);

        document.getElementById('reviewBatch').textContent = appState.batchId;
        document.getElementById('reviewHospital').textContent = appState.hospitalName;
        document.getElementById('reviewCount').textContent = appState.scannedProducts.length;

        updateReviewList();
    }

    // View Dashboard (even without completing scan)
    function viewDashboard() {
        window.location.href = '/product_scanning/dashboard/';
    }

    // Update Review List
    function updateReviewList() {
        const container = document.getElementById('reviewProductsList');
        
        const formatDate = (dateStr) => {
            if (!dateStr) return '';
            try {
                const date = new Date(dateStr);
                return date.toLocaleDateString('en-US', { year: 'numeric', month: 'short', day: 'numeric' });
            } catch {
                return dateStr;
            }
        };
        
        container.innerHTML = appState.scannedProducts.map(product => `
            <div class="product-item">
                <div class="product-info">
                    ${product.serial_number && !product.serial_number.startsWith('NSN-') ? `<p><strong>Serial:</strong> <span style="font-family: monospace;">${product.serial_number}</span></p>` : '<p><em style="color:#9ca3af;">No Serial Number</em></p>'}
                    ${product.gtin_code ? `<p><strong>GTIN:</strong> <span style="font-family: monospace;">${product.gtin_code}</span></p>` : ''}
                    ${product.material_code ? `<p><strong>Material Code:</strong> <span style="font-family: monospace;">${product.material_code}</span></p>` : ''}
                    ${product.product_name ? `<p><strong> Product Name:</strong> ${product.product_name}</p>` : ''}
                    ${product.batch_number ? `<p><strong>Lot / Batch No.:</strong> ${product.batch_number}</p>` : ''}
                    ${product.expiry_date ? `<p><strong>Expiry Date:</strong> <span style="color: ${isDateSoon(product.expiry_date) ? '#ef4444' : '#10b981'}; font-weight: bold;">${formatDate(product.expiry_date)}</span></p>` : ''}
                    ${product.production_date ? `<p><strong>Prod. Date:</strong> ${formatDate(product.production_date)}</p>` : ''}
                    ${product.best_before_date ? `<p><strong>Best Before:</strong> ${formatDate(product.best_before_date)}</p>` : ''}
                    ${product.received_date ? `<p><strong>📥 Received Date:</strong> ${formatDate(product.received_date)}</p>` : ''}
                    ${product.quantity && product.quantity > 1 ? `<p><strong>Quantity:</strong> ${product.quantity}</p>` : ''}
                    ${product._remark ? `<p style="margin-top:6px;padding:5px 8px;background:#f5f3ff;border-left:3px solid #7c3aed;border-radius:4px;font-size:0.85em;color:#5b21b6;">💬 <em>${product._remark}</em></p>` : ''}
                </div>
            </div>
        `).join('');
    }

    // Submit Session
    async function submitSession(event) {
        // Re-entrancy guard, mirroring _startingSession on the Start Audit button.
        // Without it a held Enter/Space key on the focused button fires the OS key-repeat
        // rate (10-25/sec) straight at complete_session — one incident produced 883 submits
        // and 883 notification fan-outs in 81 seconds.
        if (appState._submitting) return;
        appState._submitting = true;

        const submitBtn = (event && event.currentTarget) || document.querySelector('#step3 .btn-success');
        if (submitBtn) {
            submitBtn.disabled = true;
            submitBtn.blur();   // drop focus so key auto-repeat cannot re-trigger the click
        }
        const releaseButton = () => {
            appState._submitting = false;
            if (submitBtn) submitBtn.disabled = false;
        };

        showLoading(true);

        try {
            const notes = document.getElementById('sessionNotes').value;
            const response = await fetch(API.completeSession(), {
                method: 'POST',
                headers: {
                    'Content-Type': 'application/json',
                    'X-CSRFToken': getCookie('csrftoken')
                },
                credentials: 'same-origin',
                body: JSON.stringify({
                    session_key: appState.sessionKey,
                    notes: notes
                })
            });

            if (!response.ok) throw new Error('Failed to complete session');

            // Save batch-level remark if provided
            const batchRemarkEl = document.getElementById('batchRemark');
            const batchRemarkText = batchRemarkEl ? batchRemarkEl.value.trim() : '';
            if (batchRemarkText && appState.sessionKey) {
                try {
                    await fetch('/product_scanning/api/session-remarks/', {
                        method: 'POST',
                        headers: { 'Content-Type': 'application/json', 'X-CSRFToken': getCookie('csrftoken') },
                        credentials: 'same-origin',
                        body: JSON.stringify({ session: appState.sessionKey, remark: batchRemarkText }),
                    });
                } catch (remarkErr) {
                    console.warn('Batch remark save failed (non-critical):', remarkErr);
                }
            }

            showAlert('Session completed! Redirecting to dashboard...', 'success');

            // Guard stays latched on success — we are navigating away, and re-enabling the
            // button during the 2s delay would just re-open the duplicate-submit window.
            setTimeout(() => {
                window.location.href = '/product_scanning/dashboard/';
            }, 2000);
        } catch (error) {
            showAlert(`Error: ${error.message}`, 'error');
            releaseButton();   // only a genuine failure re-arms the button for a retry
        } finally {
            showLoading(false);
        }
    }

    // Go back to scanning from review (Step 3 → Step 2), restart camera
    function goBackToScanning() {
        hideSection('step3');
        showSection('step2');
        updateStepIndicator(2);
        // Re-initialise scanner (it was stopped in finishScanning)
        setTimeout(() => {
            const readerEl = document.getElementById('qr-reader');
            if (readerEl) readerEl.innerHTML = '';  // Clear stale scanner DOM
            appState.scanner = null;
            appState.isScanning = false;
            initializeQRScanner();
        }, 300);
        showAlert('Back to scanning. Add more products or adjust the list.', 'info');
    }

    // Go back to setup (Step 2 → Step 1), stop scanner and reset
    function goBackToSetup() {
        if (appState.scanner) {
            teardownPinchZoom();
            try { appState.scanner.stop(); } catch(e) {}
        }
        appState.scannedProducts = [];
        appState.pendingSerial = null;
        appState.awaitingSNForProduct = null;
        updatePendingSerialUI();
        updateAwaitingSNUI();
        hideSection('step2');
        showSection('step1');
        updateStepIndicator(1);
    }

    // ─── Serial Number Dialog (shown when backend returns SERIAL_REQUIRED) ────
    function _showSerialModal(qrData, serialOverride, parsedFields) {
        appState.serialModalCtx = { qrData, serialOverride, parsedFields };

        // Build "what we found" summary
        const lines = [];
        if (parsedFields.gtin_code)       lines.push(`GTIN: <strong>${parsedFields.gtin_code}</strong>`);
        if (parsedFields.batch_number)    lines.push(`Batch: <strong>${parsedFields.batch_number}</strong>`);
        if (parsedFields.expiry_date)     lines.push(`Expiry: <strong>${parsedFields.expiry_date}</strong>`);
        if (parsedFields.production_date) lines.push(`Produced: <strong>${parsedFields.production_date}</strong>`);
        document.getElementById('smFoundData').innerHTML = lines.length
            ? lines.join(' &nbsp;·&nbsp; ')
            : '<em style="color:#9ca3af;">No structured data detected in this scan.</em>';

        // Reset input state
        document.getElementById('smSerialInputArea').classList.remove('show');
        document.getElementById('smSerialInput').value = '';
        document.getElementById('smSerialInput').style.border = '';
        document.getElementById('serialModal').classList.add('show');
    }

    function smChooseYes() {
        const ctx = appState.serialModalCtx;
        appState.serialModalCtx = null;
        document.getElementById('serialModal').classList.remove('show');
        if (!ctx) return;

        // Enter "awaiting SN scan" mode → scanner resumes, blue banner appears
        appState.awaitingSNForProduct = ctx;
        updateAwaitingSNUI();
        showAlert('Now scan the <strong>Serial Number</strong> barcode.', 'info');
        safeResumeScanner();
    }

    function smShowManualInput() {
        const area = document.getElementById('smSerialInputArea');
        area.classList.add('show');
        setTimeout(() => document.getElementById('smSerialInput').focus(), 80);
    }

    function smChooseNo() {
        const ctx = appState.serialModalCtx;
        appState.serialModalCtx = null;
        document.getElementById('serialModal').classList.remove('show');
        if (!ctx) return;
        // Add to local queue as “no serial” — no backend call here
        _addToScanQueue(ctx.qrData, ctx.serialOverride || null, true, ctx.parsedFields || {});
        // Resume scanner gun queue if items are pending
        if (appState.inputMode === 'scanner' && appState.sgQueue.length > 0 && !appState.sgProcessing) {
            setTimeout(() => processScannerGunQueue(), 250);
        }
    }

    function smSubmitWithSerial() {
        const sn = document.getElementById('smSerialInput').value.trim();
        if (!sn) {
            const inp = document.getElementById('smSerialInput');
            inp.style.border = '2px solid #ef4444';
            inp.placeholder = 'Enter serial number!';
            inp.focus();
            return;
        }
        const ctx = appState.serialModalCtx;
        appState.serialModalCtx = null;
        document.getElementById('serialModal').classList.remove('show');
        if (!ctx) return;
        _addToScanQueue(ctx.qrData, sn, false, ctx.parsedFields || {});
        // Resume scanner gun queue if items are pending
        if (appState.inputMode === 'scanner' && appState.sgQueue.length > 0 && !appState.sgProcessing) {
            setTimeout(() => processScannerGunQueue(), 250);
        }
    }

    function smCancel() {
        appState.serialModalCtx = null;
        document.getElementById('serialModal').classList.remove('show');
        safeResumeScanner();
        showAlert('↩️ Scan cancelled.', 'info');
    }

    // ─── Input Mode Switcher (Camera ↔ Scanner Gun) ───────────────────────────

    function switchInputMode(mode) {
        appState.inputMode = mode;

        const cameraSection = document.getElementById('cameraScanSection');
        const scannerPanel  = document.getElementById('scannerGunPanel');
        const btnCamera     = document.getElementById('modeBtnCamera');
        const btnScanner    = document.getElementById('modeBtnScanner');

        const activeStyle = 'background:#0076c8; color:white; box-shadow:0 2px 8px rgba(0,118,200,0.3); flex:1; padding:9px 12px; border:none; border-radius:8px; font-size:0.85em; font-weight:700; cursor:pointer; transition:all 0.2s;';
        const idleStyle   = 'background:transparent; color:#64748b; box-shadow:none; flex:1; padding:9px 12px; border:none; border-radius:8px; font-size:0.85em; font-weight:700; cursor:pointer; transition:all 0.2s;';

        if (mode === 'scanner') {
            if (cameraSection) cameraSection.style.display = 'none';
            if (scannerPanel)  scannerPanel.style.display  = '';
            if (btnCamera)  btnCamera.setAttribute('style',  idleStyle);
            if (btnScanner) btnScanner.setAttribute('style', activeStyle);

            // Stop camera to save battery & avoid scroll confusion
            if (appState.scanner && appState.isScanning) {
                teardownPinchZoom();
                try {
                    appState.scanner.stop().catch(() => {});
                } catch (e) { /* ignore */ }
                appState.scanner = null;
                appState.isScanning = false;
            }

            // Auto-focus the textarea so the scanner gun fires straight into it
            setTimeout(() => document.getElementById('scannerGunInput')?.focus(), 150);

        } else {
            if (cameraSection) cameraSection.style.display = '';
            if (scannerPanel)  scannerPanel.style.display  = 'none';
            if (btnCamera)  btnCamera.setAttribute('style',  activeStyle);
            if (btnScanner) btnScanner.setAttribute('style', idleStyle);

            // Restart camera if it was stopped
            if (!appState.isScanning) {
                setTimeout(() => {
                    const readerEl = document.getElementById('qr-reader');
                    if (readerEl) readerEl.innerHTML = '';
                    appState.scanner = null;
                    initializeQRScanner();
                }, 150);
            }
        }
    }

    // ─── Scanner Gun Input Handler ────────────────────────────────────────────
    // Scanner guns emit the full barcode string followed by \r\n (or \n) in
    // milliseconds. We detect the newline terminator and process immediately.
    // For manual typing we use a 250 ms debounce.

    function handleScannerGunInput() {
        const textarea = document.getElementById('scannerGunInput');
        if (!textarea) return;
        const raw = textarea.value;

        // Newline = scanner gun fired (has a complete scan)
        if (/[\r\n]/.test(raw)) {
            clearTimeout(appState.sgDebounceTimer);
            processScannerGunText(raw);
            return;
        }

        // Debounce for manual typing
        clearTimeout(appState.sgDebounceTimer);
        appState.sgDebounceTimer = setTimeout(() => {
            const val = document.getElementById('scannerGunInput')?.value || '';
            if (val.trim()) processScannerGunText(val);
        }, 250);
    }

    function processScannerGunText(raw) {
        // Split on any newline to support bulk scans (multiple barcodes fired rapidly)
        const lines = raw.split(/[\r\n]+/).map(l => normalizeScanText(l)).filter(Boolean);
        if (lines.length === 0) return;

        // Clear textarea after 600 ms so the salesperson can briefly see what was scanned,
        // then the gun can fire the next barcode into a clean field.
        const textarea = document.getElementById('scannerGunInput');
        setTimeout(() => { if (textarea) textarea.value = ''; }, 600);

        appState.sgQueue.push(...lines);
        updateSgQueueStatus();

        if (!appState.sgProcessing) {
            processScannerGunQueue();
        }
    }

    async function processScannerGunQueue() {
        if (appState.sgQueue.length === 0) {
            appState.sgProcessing = false;
            updateSgQueueStatus();
            // Fade preview out after 2 s of idle
            setTimeout(() => {
                if (appState.sgQueue.length === 0 && !appState.sgProcessing) {
                    const prev = document.getElementById('scannerGunPreview');
                    if (prev) prev.style.display = 'none';
                }
            }, 2000);
            return;
        }

        // If awaiting a serial scan (product barcode already held), treat the next
        // queued item as the serial — any format accepted (GS1, Code 128, plain text).
        if (appState.awaitingSNForProduct) {
            appState.sgProcessing = true;
            const ctx = appState.awaitingSNForProduct;
            appState.awaitingSNForProduct = null;
            updateAwaitingSNUI();
            const serialData = appState.sgQueue.shift();
            updateSgQueueStatus();
            const snParsed = parseGS1Data(serialData);
            // Accept GS1-parsed serial or the raw scan text as a plain serial number
            const extractedSN = snParsed.serial_number || serialData.trim();
            if (!extractedSN) {
                showAlert('Could not extract a serial number. Try scanning again.', 'error');
                appState.awaitingSNForProduct = ctx;  // restore — wait for next scan
                updateAwaitingSNUI();
                appState.sgProcessing = false;
                updateSgQueueStatus();
                return;
            }
            showAlert(`Serial captured: <strong>${extractedSN}</strong> — added to queue`, 'info');
            _addToScanQueue(ctx.qrData, extractedSN, false, ctx.parsedFields || {});
            setTimeout(() => processScannerGunQueue(), 250);
            return;
        }

        appState.sgProcessing = true;
        const scanData = appState.sgQueue.shift();
        updateSgQueueStatus();

        // Client-side GS1 parse for live preview (no server round-trip)
        const parsed = parseGS1Data(scanData);
        showScannerGunPreview(parsed, scanData);

        const hasSN     = !!parsed.serial_number;
        const hasDetail = !!(parsed.gtin_code || parsed.batch_number || parsed.expiry_date);

        // 2-scan: serial-only barcode → hold serial, then keep draining queue.
        // Remaining queue items (e.g. product details barcode already queued) will
        // pick up the pendingSerial on the very next iteration.
        if (hasSN && !hasDetail) {
            appState.pendingSerial = parsed.serial_number;
            updatePendingSerialUI();
            showAlert(`Serial captured: <strong>${parsed.serial_number}</strong> — scan product barcode next`, 'info');
            // Continue draining — don't stall the queue
            setTimeout(() => processScannerGunQueue(), 250);
            return;
        }

        // No serial and no pending serial → show the "has serial?" modal.
        // Queue pauses until the modal is answered (smChooseYes/No/smSubmitWithSerial
        // all resume it). Covers detail-only and unrecognised/non-GS1 barcodes.
        // EXCEPTION: if "Skip Serial" mode is ON, skip the modal and queue with noSerial=true.
        if (!hasSN && !appState.pendingSerial) {
            if (appState.skipSerialPrompt) {
                _addToScanQueue(scanData, null, true, parsed);
                setTimeout(() => processScannerGunQueue(), 250);
            } else {
                _showSerialModal(scanData, null, parsed);
                appState.sgProcessing = false;
                updateSgQueueStatus();
            }
            return;
        }

        // Add to local queue using any pending serial from a prior 2-scan step.
        // Fall back to the client-side parsed serial so it is always sent explicitly.
        const effectiveSN = appState.pendingSerial || parsed.serial_number || null;
        _addToScanQueue(scanData, effectiveSN, false, parsed);

        // Brief pause between queue items so alerts are readable
        setTimeout(() => processScannerGunQueue(), 250);
    }

    // Submit one scanner-gun scan. On SERIAL_REQUIRED, auto-proceeds with
    // no_serial=true instead of showing the blocking modal — keeps bulk flow smooth.
    async function _submitScannerGunItem(qrData, serialOverride) {
        try {
            const body = { session_key: appState.sessionKey, qr_data: qrData };
            if (serialOverride) body.serial_number = serialOverride;

            const response = await fetch(API.scanProduct(), {
                method: 'POST',
                headers: { 'Content-Type': 'application/json', 'X-CSRFToken': getCookie('csrftoken') },
                credentials: 'same-origin',
                body: JSON.stringify(body)
            });
            const result = await response.json();

            if (!response.ok) {
                if (result.error_code === 'SERIAL_REQUIRED') {
                    // Auto-submit with no_serial — no modal interruption in gun mode
                    await _submitScannerGunNoSerial(qrData, serialOverride);
                } else if (result.error_code === 'DUPLICATE_SERIAL') {
                    appState.pendingSerial = null;
                    updatePendingSerialUI();
                    showAlert('⚠️ Already scanned — skipping duplicate', 'warning');
                } else {
                    showAlert(`${result.error || 'Scan failed'}`, 'error');
                }
                return;
            }

            appState.scannedProducts.push(result.data);
            updateProductsList();
            const pf  = result.parsed_fields || {};
            const sn  = pf.serial_number || serialOverride || '—';
            const snDisplay = (sn.startsWith && sn.startsWith('NSN-')) ? '<em>No Serial</em>' : `<strong>${sn}</strong>`;
            showAlert(
                `✓ ${snDisplay}` +
                (pf.gtin_code    ? ` · GTIN: ${pf.gtin_code}`    : '') +
                (pf.batch_number ? ` · Batch: ${pf.batch_number}` : ''),
                'success'
            );

            if (serialOverride && appState.pendingSerial) {
                appState.pendingSerial = null;
                updatePendingSerialUI();
            }
        } catch (err) {
            showAlert(`Error: ${err.message}`, 'error');
        }
    }

    async function _submitScannerGunNoSerial(qrData, serialOverride) {
        try {
            const body = { session_key: appState.sessionKey, qr_data: qrData, no_serial: true };
            if (serialOverride) body.serial_number = serialOverride;

            const response = await fetch(API.scanProduct(), {
                method: 'POST',
                headers: { 'Content-Type': 'application/json', 'X-CSRFToken': getCookie('csrftoken') },
                credentials: 'same-origin',
                body: JSON.stringify(body)
            });
            const result = await response.json();

            if (!response.ok) {
                showAlert(`${result.error || 'Save failed'}`, 'error');
                return;
            }

            appState.scannedProducts.push(result.data);
            updateProductsList();
            const pf = result.parsed_fields || {};
            showAlert(
                `✓ Saved (no serial)` +
                (pf.gtin_code    ? ` · GTIN: ${pf.gtin_code}`    : '') +
                (pf.batch_number ? ` · Batch: ${pf.batch_number}` : ''),
                'success'
            );

            if (serialOverride && appState.pendingSerial) {
                appState.pendingSerial = null;
                updatePendingSerialUI();
            }
        } catch (err) {
            showAlert(`Error: ${err.message}`, 'error');
        }
    }

    // Render a live parsed-fields breakdown inside the scanner gun panel.
    // All parsing is 100% client-side — no server round-trip needed.
    function showScannerGunPreview(parsed, rawData) {
        const preview = document.getElementById('scannerGunPreview');
        if (!preview) return;

        const fmtDate = (d) => {
            if (!d) return d;
            try { return new Date(d).toLocaleDateString('en-GB', { day: '2-digit', month: 'short', year: 'numeric' }); }
            catch { return d; }
        };

        const fields = [
            parsed.serial_number   ? { label: 'Serial',     value: parsed.serial_number,           color: '#3b82f6' } : null,
            parsed.gtin_code       ? { label: 'GTIN',        value: parsed.gtin_code,                color: '#8b5cf6' } : null,
            parsed.batch_number    ? { label: 'Batch / Lot', value: parsed.batch_number,             color: '#06b6d4' } : null,
            parsed.expiry_date     ? { label: 'Expiry',      value: fmtDate(parsed.expiry_date),     color: '#f59e0b' } : null,
            parsed.production_date ? { label: 'Produced',    value: fmtDate(parsed.production_date), color: '#10b981' } : null,
            parsed.best_before_date? { label: 'Best Before', value: fmtDate(parsed.best_before_date),color: '#6b7280' } : null,
            parsed.quantity        ? { label: 'Qty',          value: parsed.quantity,                 color: '#ec4899' } : null,
        ].filter(Boolean);

        if (fields.length === 0) {
            preview.innerHTML = `
                <div style="color:#64748b; font-size:0.78em; margin-bottom:4px;">Processing raw data...</div>
                <div style="color:#475569; font-family:ui-monospace,monospace; font-size:0.72em; word-break:break-all; opacity:0.7;">${rawData.substring(0, 100)}</div>`;
        } else {
            const cells = fields.map(f => `
                <div style="background:rgba(255,255,255,0.06); border:1px solid rgba(255,255,255,0.1); border-radius:8px; padding:8px 10px;">
                    <div style="color:#64748b; font-size:0.65em; font-weight:700; text-transform:uppercase; letter-spacing:0.06em; margin-bottom:3px;">${f.label}</div>
                    <div style="color:${f.color}; font-size:0.85em; font-weight:700; font-family:ui-monospace,monospace; word-break:break-all;">${f.value}</div>
                </div>`).join('');
            preview.innerHTML = `
                <div style="color:#64748b; font-size:0.7em; font-weight:600; margin-bottom:8px; text-transform:uppercase; letter-spacing:0.06em;">Parsed ↓</div>
                <div style="display:grid; grid-template-columns:repeat(2,1fr); gap:6px;">${cells}</div>`;
        }

        preview.style.display = 'block';
    }

    function updateSgQueueStatus() {
        const el = document.getElementById('sgQueueStatus');
        if (!el) return;
        const remaining = appState.sgQueue.length;
        if (remaining === 0 && !appState.sgProcessing) {
            el.style.display = 'none';
            return;
        }
        el.style.display = '';
        el.textContent = appState.sgProcessing
            ? (remaining > 0 ? `Processing... ${remaining} more in queue` : 'Processing...')
            : `${remaining} item${remaining !== 1 ? 's' : ''} queued`;
    }

    // Camera-mode paste textarea: show a live parsed-field preview after 2 s of idle.
    // Does NOT auto-submit — the user confirms by clicking "Submit this scan".
    function handleCameraManualInput() {
        // Hide previous preview immediately when user starts typing again
        const preview   = document.getElementById('cameraManualPreview');
        const submitBtn = document.getElementById('cameraManualSubmitBtn');
        if (preview)   preview.style.display   = 'none';
        if (submitBtn) submitBtn.style.display  = 'none';

        clearTimeout(appState.sgDebounceTimer);
        appState.sgDebounceTimer = setTimeout(() => {
            const val = (document.getElementById('manualQRInput')?.value || '').trim();
            if (!val) return;

            // Client-side GS1 parse only — no server call
            const parsed = parseGS1Data(val);
            const fmtDate = (d) => {
                if (!d) return d;
                try { return new Date(d).toLocaleDateString('en-GB', { day:'2-digit', month:'short', year:'numeric' }); }
                catch { return d; }
            };

            const fields = [
                parsed.serial_number    ? { label: 'Serial',      value: parsed.serial_number,            color: '#2563eb' } : null,
                parsed.gtin_code        ? { label: 'GTIN',         value: parsed.gtin_code,                 color: '#7c3aed' } : null,
                parsed.batch_number     ? { label: 'Batch / Lot',  value: parsed.batch_number,              color: '#0891b2' } : null,
                parsed.expiry_date      ? { label: 'Expiry',       value: fmtDate(parsed.expiry_date),      color: '#d97706' } : null,
                parsed.production_date  ? { label: 'Produced',     value: fmtDate(parsed.production_date),  color: '#059669' } : null,
                parsed.best_before_date ? { label: 'Best Before',  value: fmtDate(parsed.best_before_date), color: '#6b7280' } : null,
                parsed.quantity         ? { label: 'Qty',           value: parsed.quantity,                  color: '#db2777' } : null,
            ].filter(Boolean);

            if (!preview) return;

            if (fields.length === 0) {
                preview.innerHTML = `<span style="color:#94a3b8;font-size:0.82em;">No structured GS1 fields detected — raw data will be sent as-is.</span>`;
            } else {
                const cells = fields.map(f => `
                    <div style="display:flex;align-items:baseline;gap:6px;margin-bottom:4px;">
                        <span style="font-size:0.7em;font-weight:700;color:#94a3b8;text-transform:uppercase;letter-spacing:0.05em;min-width:68px;flex-shrink:0;">${f.label}</span>
                        <span style="font-family:ui-monospace,monospace;font-size:0.85em;font-weight:700;color:${f.color};word-break:break-all;">${f.value}</span>
                    </div>`).join('');
                preview.innerHTML = `
                    <div style="font-size:0.72em;font-weight:700;color:#64748b;text-transform:uppercase;letter-spacing:0.06em;margin-bottom:6px;">Parsed fields</div>
                    ${cells}`;
            }

            preview.style.display   = 'block';
            if (submitBtn) submitBtn.style.display = 'block';
        }, 2000);
    }

    // Explicit submit from the camera-mode paste field (triggered by button, not auto)
    async function submitCameraManualQR() {
        const val = (document.getElementById('manualQRInput')?.value || '').trim();
        if (!val) return;
        await processManualQR();
        // Clear preview + button after submit
        const preview   = document.getElementById('cameraManualPreview');
        const submitBtn = document.getElementById('cameraManualSubmitBtn');
        if (preview)   preview.style.display   = 'none';
        if (submitBtn) submitBtn.style.display  = 'none';
    }

    // Initialize on page load
    window.addEventListener('load', function() {
        // Generate batch ID
        appState.batchId = generateBatchId();
        document.getElementById('batchIdDisplay').textContent = appState.batchId;
    });