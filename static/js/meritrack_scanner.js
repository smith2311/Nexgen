/**
 * MeriTrack Scanner — Camera-based QR / Barcode / GS1 DataMatrix scanning
 * ═══════════════════════════════════════════════════════════════════════════
 * Adapted from product_scanning/qr_scanning_form.js for the MeriTrack
 * consignment management system.
 *
 * Key differences from product_scanning:
 *  • Department-aware (hospital → department hierarchy)
 *  • Action selector (verify / add / remove_expired / use)
 *  • Session types: audit, replenish, removal (not normal/transfer)
 *  • API endpoints: /meritrack/api/sessions/...
 *  • Uses ScanLog model instead of ScannedProduct
 */

(function () {
    'use strict';

    // ── Config from page ─────────────────────────────────────────────────
    const _cfgEl = document.getElementById('mt-page-config');
    const _cfg = _cfgEl ? JSON.parse(_cfgEl.textContent) : {};

    // ── Global State ─────────────────────────────────────────────────────
    const state = {
        sessionKey: null,
        hospitalId: null,
        hospitalName: null,
        departmentId: null,
        sessionType: 'audit',
        toHospitalId: null,
        toHospitalName: null,
        currentAction: 'verify',
        scannedProducts: [],
        scanner: null,
        isScanning: false,
        lastScanTime: 0,
        minScanInterval: 1200,
        scanPaused: false,
        pendingSerial: null,
        serialModalCtx: null,
        awaitingSNForProduct: null,
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
        _startingSession: false,
        _submitting: false,        // double-submit guard for Complete & Submit button
        // Scanner Gun / optimistic UI state
        inputMode: 'camera',          // 'camera' | 'scanner'
        skipSerialPrompt: false,
        sgDebounceTimer: null,
        sgQueue: [],
        sgProcessing: false,
        errorTray: [],
        _nextItemId: 1,
    };

    const SALESPERSON_NAME = _cfg.salespersonName || 'User';

    // H2H Transfer mode reroutes the session lifecycle to the dedicated
    // /transfers/ API (which actually moves stock between hospitals).
    function isTransfer() { return state.sessionType === 'transfer'; }

    // ── API paths ────────────────────────────────────────────────────────
    const API = {
        base: '/meritrack/api',
        departments: (hid) => `${API.base}/departments/by-hospital/${hid}/`,
        startSession: () => isTransfer() ? `${API.base}/transfers/start_transfer/` : `${API.base}/sessions/start_session/`,
        scanProduct: () => isTransfer() ? `${API.base}/transfers/scan_product/` : `${API.base}/sessions/scan_product/`,
        completeSession: () => isTransfer() ? `${API.base}/transfers/complete_transfer/` : `${API.base}/sessions/complete_session/`,
        deleteProduct: (id) => `${API.base}/sessions/delete_scan_log/`,
        deleteTransferProduct: (id) => `${API.base}/transfers/products/${id}/`,
    };

    // ── Utilities ────────────────────────────────────────────────────────
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

    function normalizeScanText(text) {
        return String(text || '')
            .replace(/[\u0000-\u001F\u007F]/g, '')
            .replace(/^\](C1|d2|e0|Q3|J4)/i, '')
            .trim();
    }

    function getUserInitials() {
        const parts = SALESPERSON_NAME.trim().split(/\s+/).filter(Boolean);
        if (parts.length >= 2) return (parts[0][0] + parts[parts.length - 1][0]).toUpperCase();
        return SALESPERSON_NAME.replace(/[^a-zA-Z]/g, '').slice(0, 2).toUpperCase() || 'XX';
    }

    function computeSessionId() {
        const now = new Date();
        const dd = String(now.getDate()).padStart(2, '0');
        const mm = String(now.getMonth() + 1).padStart(2, '0');
        const yy = String(now.getFullYear()).slice(-2);
        const HH = String(now.getHours()).padStart(2, '0');
        const MI = String(now.getMinutes()).padStart(2, '0');
        const SS = String(now.getSeconds()).padStart(2, '0');
        const initials = getUserInitials();
        const hospitalOpt = document.getElementById('mtHospitalSelect')?.selectedOptions[0];
        const sapCode = hospitalOpt?.getAttribute('data-sap-code')?.trim();
        const timePart = `${HH}${MI}${SS}`;
        if (sapCode) return `${dd}${mm}${yy}-${initials}-${sapCode}-${timePart}`;
        return `${dd}${mm}${yy}-${initials}-${timePart}`;
    }

    function showAlert(message, type) {
        type = type || 'info';
        const c = document.getElementById('mtAlertContainer');
        if (!c) return;
        const d = document.createElement('div');
        d.className = `mt-alert mt-alert-${type}`;
        d.innerHTML = `${message} <span style="float:right;cursor:pointer;font-size:1.2em;" onclick="this.parentElement.remove()">×</span>`;
        c.prepend(d);
        setTimeout(() => { if (d.parentElement) d.remove(); }, 5000);
    }

    function showLoading(show) {
        const el = document.getElementById('mtLoadingOverlay');
        if (el) el.classList.toggle('show', !!show);
    }

    function hideSection(id) { const el = document.getElementById(id); if (el) el.classList.remove('active'); }
    function showSection(id) { const el = document.getElementById(id); if (el) el.classList.add('active'); }

    function clamp(v, min, max) {
        return Math.min(max, Math.max(min, v));
    }

    function escHtml(str) {
        return String(str || '')
            .replace(/&/g, '&amp;').replace(/</g, '&lt;')
            .replace(/>/g, '&gt;').replace(/"/g, '&quot;');
    }

    function safeResumeScanner() {
        if (state.inputMode === 'camera' && state.scanner && !state.scanPaused && state.isScanning) {
            try { state.scanner.resume(); } catch (e) { /* ignore */ }
        }
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
        const z = state.zoom;
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
        const z = state.zoom;
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
            state.zoom.supported = false;
            return;
        }

        teardownPinchZoom();

        const z = state.zoom;
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
        document.querySelectorAll('.mt-step').forEach((el, idx) => {
            el.classList.remove('active', 'completed');
            if (idx + 1 < step) el.classList.add('completed');
            if (idx + 1 === step) el.classList.add('active');
        });
    }

    // ── Department loading ───────────────────────────────────────────────
    async function loadDepartments() {
        const hid = document.getElementById('mtHospitalSelect').value;
        const deptSel = document.getElementById('mtDepartmentSelect');
        if (!deptSel) return;  // this scanner page has no department dropdown
        deptSel.innerHTML = '<option value="">All / Auto</option>';
        deptSel.disabled = true;
        if (!hid) return;
        try {
            const resp = await fetch(API.departments(hid), { credentials: 'same-origin' });
            if (!resp.ok) return;
            const depts = await resp.json();
            for (const d of depts) {
                const opt = document.createElement('option');
                opt.value = d.id;
                opt.textContent = d.name + (d.floor ? ` (Floor ${d.floor})` : '');
                deptSel.appendChild(opt);
            }
            deptSel.disabled = false;
        } catch (e) { console.error('Dept load error:', e); }
    }

    // ── Hospital info ────────────────────────────────────────────────────
    function updateHospitalInfo() {
        const select = document.getElementById('mtHospitalSelect');
        const info = document.getElementById('mtHospitalInfo');
        if (!select.value) { if (info) info.style.display = 'none'; updateSessionIdDisplay(); return; }
        const opt = select.selectedOptions[0];
        const el = (id) => document.getElementById(id);
        if (el('mtInfoName')) el('mtInfoName').textContent = opt.getAttribute('data-name') || opt.text.split(' (')[0];
        if (el('mtInfoSap')) el('mtInfoSap').textContent = opt.getAttribute('data-sap-code') || 'N/A';
        if (el('mtInfoCity')) el('mtInfoCity').textContent = opt.getAttribute('data-city') || 'N/A';
        if (info) info.style.display = 'block';
        updateSessionIdDisplay();
        loadDepartments();
        revealStartButton();
    }

    /** Picking a hospital grows Step 1 (info panel, session ID box, note), which
     *  used to push Start off the bottom of a phone with nothing to hint at it.
     *  CSS now pins the button while Step 1 is up; this is the safety net for
     *  when it isn't pinned — desktop, or if the pin is ever defeated — and
     *  no-ops whenever the button is already on screen. */
    function revealStartButton() {
        const btn = document.getElementById('mtStartBtn');
        if (!btn) return;
        requestAnimationFrame(() => {
            const r = btn.getBoundingClientRect();
            const offScreen = r.bottom > window.innerHeight - 8 || r.top < 0;
            if (offScreen) btn.scrollIntoView({ block: 'nearest', behavior: 'smooth' });
        });
    }

    function updateSessionIdDisplay() {
        const el = document.getElementById('mtSessionIdDisplay');
        if (el) el.textContent = computeSessionId();
    }

    /** The saved key is stamped server-side the moment Start is pressed, so the
     *  preview has to follow the clock — frozen at hospital-select time it drifts
     *  further from the real key the longer the rep spends on the setup step. */
    function startSessionIdTicker() {
        setInterval(() => {
            const step1 = document.getElementById('mtStep1');
            const box = document.getElementById('mtSessionIdBox');
            if (!step1 || !step1.classList.contains('active')) return;
            if (!box || box.style.display === 'none') return;
            updateSessionIdDisplay();
        }, 1000);
    }

    // ── Action selector ──────────────────────────────────────────────────
    function setAction(btn) {
        document.querySelectorAll('.mt-action-btn').forEach(b => b.classList.remove('active'));
        btn.classList.add('active');
        state.currentAction = btn.dataset.action;
        const shelfGroup = document.getElementById('mtShelfGroup');
        if (shelfGroup) shelfGroup.style.display = state.currentAction === 'add' ? 'block' : 'none';
    }

    // ══════════════════════════════════════════════════════════════════════
    // GS1 Parser (client-side)
    // ══════════════════════════════════════════════════════════════════════
    function parseGS1Data(gs1String) {
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
        const AI_KEYS = Object.keys(AI_DEFS).sort((a,b) => b.length - a.length);
        const FIXED_AI_KEYS = AI_KEYS.filter((k) => AI_DEFS[k].l > 0);
        const FNC1 = String.fromCharCode(29);
        const result = {};

        function findFixedAiBoundary(str, startPos) {
            for (let i = startPos + 1; i < str.length; i++) {
                for (const key of FIXED_AI_KEYS) {
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

        let s = gs1String
            .replace(/^\](C1|d2|e0|Q3|J4)/i, '')
            .replace(/[\u001d\x1d]/g, FNC1)
            .replace(/\[GS\]/gi, FNC1)
            .replace(/\{FNC1\}/gi, FNC1)
            .replace(/<(?:FNC1|GS)>/gi, FNC1);

        if (/\(\d{2,4}\)/.test(s)) {
            s = s.replace(/\((\d{2,4})\)/g, (_, ai) => FNC1 + ai);
            if (s[0] !== FNC1) s = FNC1 + s;
        }

        let pos = 0;
        while (pos < s.length) {
            if (s[pos] === FNC1) { pos++; continue; }
            let matched = null;
            for (const key of AI_KEYS) { if (s.startsWith(key, pos)) { matched = key; break; } }
            if (!matched) {
                const rest = s.slice(pos).replace(new RegExp(FNC1, 'g'), '');
                if (/^\d{8,14}$/.test(rest)) result.gtin_code = result.gtin_code || rest;
                break;
            }
            const def = AI_DEFS[matched];
            pos += matched.length;
            let value;
            if (def.l > 0) { value = s.slice(pos, pos + def.l); pos += def.l; }
            else {
                const fi = s.indexOf(FNC1, pos);
                if (fi === -1) {
                    const boundary = findFixedAiBoundary(s, pos);
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
            if (def.d && value.length >= 6) {
                const yy = parseInt(value.slice(0,2));
                const mm = value.slice(2,4), dd = value.slice(4,6);
                value = `${yy <= 49 ? 2000+yy : 1900+yy}-${mm}-${dd}`;
            }
            if (!result[def.f]) result[def.f] = value;
        }
        return result;
    }

    // Convert GS1 to pipe-separated format for backend
    function processGS1Data(scanData) {
        const hasGS1 = /(\u001d|\[GS\]|\x1d|\{FNC1\}|^(01|02|10|11|12|13|15|17|20|21|240|250|310|3101|3102|3103|00|30)\d)/.test(scanData);
        if (!hasGS1) return scanData;
        const parsed = parseGS1Data(scanData);
        const parts = [];
        if (parsed.serial_number) parts.push(`SERIAL:${parsed.serial_number}`);
        if (parsed.gtin_code) parts.push(`GTIN:${parsed.gtin_code}`);
        if (parsed.batch_number) parts.push(`BATCH:${parsed.batch_number}`);
        if (parsed.expiry_date) parts.push(`EXPIRY:${parsed.expiry_date}`);
        if (parsed.production_date) parts.push(`PRODUCED:${parsed.production_date}`);
        if (parsed.best_before_date) parts.push(`BESTBEFORE:${parsed.best_before_date}`);
        if (parsed.quantity) parts.push(`QTY:${parsed.quantity}`);
        if (parsed.additional_id) parts.push(`ADDITIONALID:${parsed.additional_id}`);
        if (parsed.secondary_data) parts.push(`REF:${parsed.secondary_data}`);
        if (parts.length > 0) return parts.join('|');
        return scanData;
    }

    // ══════════════════════════════════════════════════════════════════════
    // Barcode Validation
    // ══════════════════════════════════════════════════════════════════════
    function isValidBarcodeScan(decodedText, decodedResult) {
        const text = normalizeScanText(decodedText);
        if (!text) return false;
        const fmt = decodedResult?.result?.format?.formatName || '';
        if (fmt === 'CODE_128') {
            if (text.length < 4) {

                return false;
            }
        } else if (text.length < 8) {

            return false;
        }
        if (fmt === 'CODE_128') {
            const sp = (text.match(/[#:,]/g) || []).length;
            const an = (text.match(/[a-zA-Z0-9]/g) || []).length;
            if (sp > an * 0.6 && an < 3) return false;
            if (/[#:,]{3,}/.test(text)) return false;
        }
        if (fmt && (fmt.includes('EAN') || fmt.includes('UPC'))) {
            if (!/^\d+$/.test(text)) return false;
        }
        return true;
    }

    // ══════════════════════════════════════════════════════════════════════
    // Camera Scanner Init
    // ══════════════════════════════════════════════════════════════════════
    function initializeQRScanner() {
        try {
            state.scanner = new Html5Qrcode('qr-reader');
            const config = {
                fps: 30,
                qrbox: { width: 300, height: 300 },
                aspectRatio: 1.0,
                showTorchButtonIfSupported: true,
                disableFlip: true,
                verbose: false,
                experimentalFeatures: { useBarCodeDetectorIfSupported: true },
                formatsToSupport: [
                    Html5QrcodeSupportedFormats.QR_CODE,
                    Html5QrcodeSupportedFormats.DATA_MATRIX,
                    Html5QrcodeSupportedFormats.PDF_417,
                    Html5QrcodeSupportedFormats.AZTEC,
                    Html5QrcodeSupportedFormats.CODE_128,
                    Html5QrcodeSupportedFormats.CODE_39,
                    Html5QrcodeSupportedFormats.CODE_93,
                    Html5QrcodeSupportedFormats.EAN_13,
                    Html5QrcodeSupportedFormats.EAN_8,
                    Html5QrcodeSupportedFormats.UPC_A,
                    Html5QrcodeSupportedFormats.UPC_E,
                    Html5QrcodeSupportedFormats.ITF,
                    Html5QrcodeSupportedFormats.CODABAR,
                ],
            };
            state.scanner.start(
                { facingMode: 'environment' },
                config,
                (decodedText, decodedResult) => {
                    const normalizedText = normalizeScanText(decodedText);
                    const fmt = decodedResult?.result?.format?.formatName || '';

                    if (!isValidBarcodeScan(normalizedText, decodedResult)) return;
                    // CODE_128: require 2 identical reads
                    if (fmt === 'CODE_128') {
                        const buf = state.code128Buffer;
                        if (buf.text === normalizedText) {
                            buf.count++;
                            if (buf.count >= 2) {
                                clearTimeout(buf.timer);
                                buf.text = null; buf.count = 0; buf.timer = null;
                                handleScan(normalizedText);
                            }
                        } else {
                            clearTimeout(buf.timer);
                            buf.text = normalizedText; buf.count = 1;
                            buf.timer = setTimeout(() => { buf.text = null; buf.count = 0; buf.timer = null; }, 2000);
                        }
                        return;
                    }
                    handleScan(normalizedText);
                },
                () => {} // silent scan errors
            ).then(() => {
                state.isScanning = true;
                initPinchZoom();
                showAlert('Camera ready — scan QR, barcodes, or GS1 DataMatrix codes.', 'success');
            }).catch((err) => {
                if (err.name === 'NotAllowedError') showAlert('Camera access denied. Please allow and refresh.', 'error');
                else if (err.name === 'NotFoundError') showAlert('No camera found. Use manual entry below.', 'warning');
                else showAlert(`Camera error: ${err.message}. Use manual entry.`, 'warning');
            });
        } catch (err) {
            console.error('Scanner init error:', err);
            showAlert(`Scanner init error: ${err.message}`, 'error');
        }
    }

    // ══════════════════════════════════════════════════════════════════════
    // 2-Scan Workflow
    // ══════════════════════════════════════════════════════════════════════
    function updatePendingSerialUI() {
        const banner = document.getElementById('mtPendingBanner');
        const snSpan = document.getElementById('mtPendingSN');
        if (!banner || !snSpan) return;
        if (state.pendingSerial) { snSpan.textContent = state.pendingSerial; banner.style.display = 'block'; }
        else { banner.style.display = 'none'; }
    }

    function clearPendingSerial() {
        state.pendingSerial = null;
        updatePendingSerialUI();
        showAlert('Pending serial cleared.', 'info');
    }

    function updateAwaitingSNUI() {
        const banner = document.getElementById('mtAwaitingBanner');
        const details = document.getElementById('mtAwaitingDetails');
        if (!banner || !details) return;
        if (state.awaitingSNForProduct) {
            const pf = state.awaitingSNForProduct.parsedFields || {};
            const parts = [];
            if (pf.gtin_code) parts.push(`GTIN: ${pf.gtin_code}`);
            if (pf.batch_number) parts.push(`Batch: ${pf.batch_number}`);
            if (pf.expiry_date) parts.push(`Expiry: ${pf.expiry_date}`);
            details.textContent = parts.length ? `Product: ${parts.join(' · ')}` : 'Product data held — scan SN barcode now';
            banner.style.display = 'block';
        } else { banner.style.display = 'none'; }
    }

    function clearAwaitingSN() {
        state.awaitingSNForProduct = null;
        updateAwaitingSNUI();
        showAlert('SN scan cancelled. Product discarded.', 'info');
        if (state.scanner && !state.scanPaused) state.scanner.resume();
    }

    // ── Core scan handler ────────────────────────────────────────────────
    async function handleScan(qrData) {
        const now = Date.now();
        if (now - state.lastScanTime < state.minScanInterval) return;
        if (state.scanPaused) { showAlert('Scanning paused. Resume to continue.', 'info'); return; }
        state.lastScanTime = now;
        if (state.scanner) state.scanner.pause();

        // Awaiting SN mode (reverse 2-scan)
        if (state.awaitingSNForProduct) {
            const ctx = state.awaitingSNForProduct;
            state.awaitingSNForProduct = null;
            updateAwaitingSNUI();
            const snParsed = parseGS1Data(qrData);
            const extractedSN = normalizeScanText(snParsed.serial_number || qrData);
            if (!extractedSN) {
                showAlert('Could not extract serial from scan. Try again.', 'error');
                state.awaitingSNForProduct = ctx;
                updateAwaitingSNUI();
                if (state.scanner && !state.scanPaused) state.scanner.resume();
                return;
            }
            showAlert(`Serial captured: <strong>${extractedSN}</strong>`, 'info');
            await submitScan(ctx.qrData, extractedSN);
            return;
        }

        // Quick GS1 parse for 2-scan detection
        const quick = parseGS1Data(qrData);
        const hasSN = !!quick.serial_number;
        const hasDetail = !!(quick.gtin_code || quick.batch_number || quick.expiry_date);

        // SN-only barcode → hold as pending
        if (hasSN && !hasDetail) {
            state.pendingSerial = quick.serial_number;
            updatePendingSerialUI();
            showAlert(
                `Serial <strong>${quick.serial_number}</strong> captured!<br>Now scan the <strong>product details barcode</strong>.`,
                'info'
            );
            if (state.scanner && !state.scanPaused) state.scanner.resume();
            return;
        }

        // All other → submit (inject pending serial if available)
        // Skip-serial mode: no SN + no pending serial → queue without prompting
        if (!hasSN && !state.pendingSerial && state.skipSerialPrompt) {
            _addToScanQueue(qrData, null, true, quick);
            safeResumeScanner();
            return;
        }
        await submitScan(qrData, state.pendingSerial || null);
    }
    async function submitScan(qrData, serialOverride) {
        showLoading(true);
        try {
            const body = {
                session_key: state.sessionKey,
                qr_data: qrData,
                action: state.currentAction,
                shelf_location: document.getElementById('mtShelfLocation')?.value || '',
            };
            if (serialOverride) {
                body.serial_number = serialOverride;

            }

            const resp = await fetch(API.scanProduct(), {
                method: 'POST',
                headers: { 'Content-Type': 'application/json', 'X-CSRFToken': getCookie('csrftoken') },
                credentials: 'same-origin',
                body: JSON.stringify(body),
            });
            const result = await resp.json();

            if (!resp.ok) {
                if (result.error_code === 'SERIAL_REQUIRED') {
                    showSerialModal(qrData, serialOverride, result.parsed_fields || {});
                } else if (result.error_code === 'DUPLICATE_SERIAL' || result.error === 'duplicate') {
                    state.pendingSerial = null;
                    updatePendingSerialUI();
                    showAlert(`<strong>Already Scanned!</strong> ${result.message || result.error || ''}`, 'warning');
                } else {
                    showAlert(result.error || result.message || 'Scan failed', 'error');
                }
                return;
            }

            const product = result.scan_log || result.data || result;
            state.scannedProducts.push(product);
            updateProductsList();
            const sn = product.serial_number || serialOverride || 'N/A';
            const linked = serialOverride ? ' <em>(2-scan)</em>' : '';
            showAlert(
                `Scanned! SN: <strong>${sn}</strong>${linked} · ${product.product_name || ''}`,
                'success'
            );
            if (serialOverride && state.pendingSerial) {
                state.pendingSerial = null;
                updatePendingSerialUI();
            }
        } catch (err) {
            console.error('Scan error:', err);
            showAlert(`Scan error: ${err.message}`, 'error');
        } finally {
            showLoading(false);
            if (state.scanner && !state.scanPaused) state.scanner.resume();
        }
    }

    // ══════════════════════════════════════════════════════════════════════
    // Serial Number Dialog (shown when backend returns SERIAL_REQUIRED)
    // ══════════════════════════════════════════════════════════════════════
    function showSerialModal(qrData, serialOverride, parsedFields) {
        state.serialModalCtx = { qrData, serialOverride, parsedFields };
        const lines = [];
        if (parsedFields.gtin_code) lines.push(`GTIN: <strong>${parsedFields.gtin_code}</strong>`);
        if (parsedFields.batch_number) lines.push(`Batch: <strong>${parsedFields.batch_number}</strong>`);
        if (parsedFields.expiry_date) lines.push(`Expiry: <strong>${parsedFields.expiry_date}</strong>`);
        if (parsedFields.production_date) lines.push(`Prod: <strong>${parsedFields.production_date}</strong>`);
        document.getElementById('mtSmFoundData').innerHTML = lines.length
            ? lines.join(' &nbsp;·&nbsp; ')
            : '<em style="color:var(--mt-gray-400);">No structured data detected.</em>';
        const area = document.getElementById('mtSmSerialInputArea');
        if (area) area.classList.remove('show');
        const inp = document.getElementById('mtSmSerialInput');
        if (inp) { inp.value = ''; inp.style.border = ''; }
        document.getElementById('mtSerialModal').classList.add('show');
    }

    function smChooseYes() {
        const ctx = state.serialModalCtx; state.serialModalCtx = null;
        document.getElementById('mtSerialModal').classList.remove('show');
        if (!ctx) return;
        state.awaitingSNForProduct = ctx;
        updateAwaitingSNUI();
        showAlert('Now scan the <strong>Serial Number</strong> barcode.', 'info');
        if (state.scanner && !state.scanPaused) state.scanner.resume();
    }

    function smShowManualInput() {
        const area = document.getElementById('mtSmSerialInputArea');
        area.classList.add('show');
        setTimeout(() => document.getElementById('mtSmSerialInput').focus(), 80);
    }

    function smChooseNo() {
        const ctx = state.serialModalCtx; state.serialModalCtx = null;
        document.getElementById('mtSerialModal').classList.remove('show');
        if (!ctx) return;
        _addToScanQueue(ctx.qrData, ctx.serialOverride || null, true, ctx.parsedFields || {});
        if (state.inputMode === 'scanner' && state.sgQueue.length && !state.sgProcessing) {
            setTimeout(() => _processScannerGunQueue(), 250);
        }
        safeResumeScanner();
    }

    function smSubmitWithSerial() {
        const sn = document.getElementById('mtSmSerialInput').value.trim();
        if (!sn) {
            const inp = document.getElementById('mtSmSerialInput');
            inp.style.border = '2px solid var(--mt-danger)'; inp.placeholder = 'Enter serial number!'; inp.focus();
            return;
        }
        const ctx = state.serialModalCtx; state.serialModalCtx = null;
        document.getElementById('mtSerialModal').classList.remove('show');
        if (!ctx) return;
        _addToScanQueue(ctx.qrData, sn, false, ctx.parsedFields || {});
        if (state.inputMode === 'scanner' && state.sgQueue.length && !state.sgProcessing) {
            setTimeout(() => _processScannerGunQueue(), 250);
        }
        safeResumeScanner();
    }

    function smCancel() {
        state.serialModalCtx = null;
        document.getElementById('mtSerialModal').classList.remove('show');
        if (state.scanner && !state.scanPaused) state.scanner.resume();
        showAlert('Scan cancelled.', 'info');
    }

    // ══════════════════════════════════════════════════════════════════════
    // Pause / Resume / Toggle
    // ══════════════════════════════════════════════════════════════════════
    function toggleScanning() {
        state.scanPaused = !state.scanPaused;
        const btn = document.getElementById('mtPauseBtn');
        // Stops the animated sweep line so "paused" reads at a glance.
        document.querySelector('.mt-scanner-container')?.classList.toggle('is-paused', state.scanPaused);
        if (state.scanPaused) {
            if (btn) { btn.textContent = '▶ Resume'; btn.style.background = 'var(--mt-success)'; }
            showAlert('Scanning paused.', 'info');
        } else {
            if (btn) { btn.textContent = '⏸ Pause'; btn.style.background = 'var(--mt-orange)'; }
            if (state.scanner) state.scanner.resume();
            showAlert('Scanning resumed!', 'success');
        }
    }

    // ══════════════════════════════════════════════════════════════════════
    // Manual Input
    // ══════════════════════════════════════════════════════════════════════
    async function processManualQR() {
        const input = document.getElementById('mtManualQRInput').value.trim();
        if (!input) { showAlert('Enter QR data', 'error'); return; }
        await submitScan(input, state.pendingSerial || null);
        document.getElementById('mtManualQRInput').value = '';
    }

    const MANUAL_FIELDS = ['serial', 'gtin', 'matcode', 'name', 'batch', 'expiry', 'qty'];

    /* There are two manual-entry field groups on the page — one in the camera
       panel and one in the scanner-gun fallback. Resolve the group the user
       actually typed into, otherwise a hidden group's empty values win. */
    function manualFieldGroup(originBtn) {
        const groups = Array.from(document.querySelectorAll('[data-mf-group]'));
        if (!groups.length) return null;
        if (originBtn && originBtn.closest) {
            const own = originBtn.closest('[data-mf-group]');
            if (own) return own;
            // the submit button usually sits just outside its own field grid
            const panel = originBtn.parentElement;
            const nested = panel && panel.querySelector('[data-mf-group]');
            if (nested) return nested;
        }
        // Fall back to the first group that is actually on screen.
        return groups.find(g => g.offsetParent !== null) || groups[0];
    }

    async function submitManualFields(originBtn) {
        const scope = manualFieldGroup(originBtn);
        const field = (key) => (scope
            ? scope.querySelector(`[data-mf="${key}"]`)
            : document.getElementById(`mt_f_${key}`));
        const g = (key) => field(key)?.value.trim() || '';

        const serial = g('serial') || state.pendingSerial;
        if (!serial) { showAlert('Serial Number is required.', 'error'); return; }
        const parts = [`SERIAL:${serial}`];
        if (g('gtin')) parts.push(`GTIN:${g('gtin')}`);
        if (g('matcode')) parts.push(`MATCODE:${g('matcode')}`);
        if (g('name')) parts.push(`NAME:${g('name')}`);
        if (g('batch')) parts.push(`BATCH:${g('batch')}`);
        if (g('expiry')) parts.push(`EXPIRY:${g('expiry')}`);
        if (g('qty')) parts.push(`QTY:${g('qty')}`);
        await submitScan(parts.join('|'), null);
        MANUAL_FIELDS.forEach(key => { const el = field(key); if (el) el.value = ''; });
    }

    // ══════════════════════════════════════════════════════════════════════
    // Step 1 → Start Session
    // ══════════════════════════════════════════════════════════════════════
    async function startScanning() {
        if (state._startingSession) return;
        state._startingSession = true;
        const safety = setTimeout(() => { state._startingSession = false; }, 15000);

        const hospitalId = document.getElementById('mtHospitalSelect').value;
        if (!hospitalId) { showAlert('Please select a hospital', 'error'); clearTimeout(safety); state._startingSession = false; return; }

        const departmentId = document.getElementById('mtDepartmentSelect')?.value || '';
        const sessionType = currentSessionType();
        if (!sessionType) { showAlert('Please select a session type', 'error'); clearTimeout(safety); state._startingSession = false; return; }

        // H2H Transfer requires a distinct destination hospital.
        let toHospitalId = '';
        if (sessionType === 'transfer') {
            toHospitalId = document.getElementById('mtToHospitalSelect')?.value || '';
            if (!toHospitalId) { showAlert('Please select a destination hospital', 'error'); clearTimeout(safety); state._startingSession = false; return; }
            if (String(toHospitalId) === String(hospitalId)) { showAlert('Source and destination hospitals must differ', 'error'); clearTimeout(safety); state._startingSession = false; return; }
        }

        showLoading(true);
        try {
            state.hospitalId = hospitalId;
            const hSelect = document.getElementById('mtHospitalSelect');
            state.hospitalName = hSelect.selectedOptions[0]?.getAttribute('data-name') || hSelect.selectedOptions[0]?.text.split(' (')[0];
            state.sessionType = sessionType;
            state.departmentId = departmentId;

            let body;
            if (sessionType === 'transfer') {
                const toSel = document.getElementById('mtToHospitalSelect');
                state.toHospitalId = toHospitalId;
                state.toHospitalName = toSel.selectedOptions[0]?.getAttribute('data-name') || toSel.selectedOptions[0]?.text.split(' (')[0];
                body = { from_hospital_id: hospitalId, to_hospital_id: toHospitalId };
            } else {
                body = { hospital_id: hospitalId, department_id: departmentId || null, session_type: sessionType };
            }

            const resp = await fetch(API.startSession(), {
                method: 'POST',
                headers: { 'Content-Type': 'application/json', 'X-CSRFToken': getCookie('csrftoken') },
                credentials: 'same-origin',
                body: JSON.stringify(body),
            });
            if (!resp.ok) { const err = await resp.json(); throw new Error(err.error || 'Failed to start session'); }
            const session = await resp.json();
            state.sessionKey = session.session_key;

            // Update step 2 display
            const el = (id) => document.getElementById(id);
            if (el('mtDisplaySession')) el('mtDisplaySession').textContent = session.transfer_id || state.sessionKey;
            if (el('mtDisplayHospital')) el('mtDisplayHospital').textContent = isTransfer() ? `${state.hospitalName} → ${state.toHospitalName}` : state.hospitalName;
            if (el('mtDisplayType')) el('mtDisplayType').textContent = isTransfer() ? 'H2H Transfer' : (session.session_type || sessionType);
            if (el('mtProductCount')) el('mtProductCount').textContent = '0';
            if (el('mtSessionInfoBar')) el('mtSessionInfoBar').style.display = '';

            hideSection('mtStep1');
            showSection('mtStep2');
            updateStepIndicator(2);
            initializeQRScanner();
            showAlert('Session started! Ready to scan.', 'success');
        } catch (err) {
            showAlert(`Error: ${err.message}`, 'error');
        } finally {
            showLoading(false);
            clearTimeout(safety);
            state._startingSession = false;
        }
    }

    // ══════════════════════════════════════════════════════════════════════
    // Product List
    // ══════════════════════════════════════════════════════════════════════
    function updateProductsList() {
        const container = document.getElementById('mtProductsList');
        const count = state.scannedProducts.length;
        const countEl = document.getElementById('mtProductCount');
        if (countEl) countEl.textContent = count;

        if (!count) {
            container.innerHTML = '<div class="mt-empty-state">No products scanned yet</div>';
            renderErrorTray();
            return;
        }

        const fmt = (d) => {
            if (!d) return '';
            try { return new Date(d).toLocaleDateString('en-GB', {day:'2-digit',month:'short',year:'numeric'}); }
            catch { return d; }
        };
        const fmtSN = (sn) => {
            if (!sn) return '<em style="color:var(--mt-gray-400);">No Serial</em>';
            if (sn.startsWith('NSN-')) return '<em style="color:var(--mt-gray-400);">No Serial</em>';
            return `<span style="font-family:ui-monospace,monospace;">${escHtml(sn)}</span>`;
        };
        const isDateSoon = (ds) => {
            if (!ds) return false;
            try { const d = new Date(ds); const t = new Date(); return d <= new Date(t.getTime()+30*86400000) && d >= t; }
            catch { return false; }
        };

        const rows = [...state.scannedProducts].reverse().map((p, revIdx) => {
            const origIdx = state.scannedProducts.length - 1 - revIdx;
            const action = p.action || state.currentAction;
            const isPending = !!p._pending;
            const isFailed  = !!p._failed;
            /* State drives a class, not an inline hex — scanner.css used to match
               on the literal colour via [style*=], which broke whenever the
               palette moved. */
            const stateClass = isPending ? ' is-pending' : (isFailed ? ' is-failed' : '');
            const statusBadge = isPending
                ? '<span style="background:var(--mt-warning-light);color:var(--mt-warning-text);border:1px solid var(--mt-warning);padding:2px 7px;border-radius:20px;font-size:0.68em;font-weight:700;">⏳ Saving…</span>'
                : (isFailed ? '<span style="background:var(--mt-danger-light);color:var(--mt-danger);border:1px solid var(--mt-danger-border);padding:2px 7px;border-radius:20px;font-size:0.68em;font-weight:700;">✗ Failed</span>' : '');
            const pills = [
                `<span class="mt-pill">SN ${fmtSN(p.serial_number)}</span>`,
                p.gtin_code     ? `<span class="mt-pill">GTIN <span style="font-family:ui-monospace,monospace;">${escHtml(p.gtin_code)}</span></span>` : '',
                p.batch_number  ? `<span class="mt-pill">Lot <span>${escHtml(p.batch_number)}</span></span>` : '',
                p.expiry_date   ? `<span class="mt-pill" style="color:${isDateSoon(p.expiry_date)?'var(--mt-danger)':'var(--mt-success)'}">Exp <span>${fmt(p.expiry_date)}</span></span>` : '',
                p.product_name  ? `<span class="mt-pill"><span>${escHtml(p.product_name)}</span></span>` : '',
                p.material_code ? `<span class="mt-pill"><span>${escHtml(p.material_code)}</span></span>` : '',
                p.quantity && p.quantity > 1 ? `<span class="mt-pill">Qty <span>${p.quantity}</span></span>` : '',
                p.production_date ? `<span class="mt-pill">Prod <span>${fmt(p.production_date)}</span></span>` : '',
                `<span class="mt-action-badge ${action}">${action.replace('_', ' ')}</span>`,
                statusBadge,
            ].filter(Boolean).join('');
            return `<div class="mt-product-item${stateClass}">
                <div class="mt-product-main">${pills}</div>
                <div style="display:flex;gap:5px;flex-shrink:0;">
                    ${(!isPending && !isTransfer()) ? `<button class="mt-btn mt-btn-edit" onclick="window.mtEditProduct(${origIdx})" title="Edit">✏</button>` : ''}
                    <button class="mt-btn mt-btn-delete" onclick="window.mtDeleteProduct(${origIdx})" title="Remove">×</button>
                </div>
            </div>`;
        });

        container.innerHTML =
            `<div class="mt-scan-list-header">
                <span>${count} scanned</span>
                <div style="display:flex;gap:8px;align-items:center;">
                    <span style="font-weight:400;font-size:0.85em;">latest first</span>
                    <button onclick="window.mtClearAllProducts()" style="background:var(--mt-danger-light);color:var(--mt-danger);border:1px solid var(--mt-danger-border);padding:3px 10px;border-radius:6px;font-size:0.75em;font-weight:700;cursor:pointer;">Clear All</button>
                </div>
            </div>` +
            `<div style="border:1px solid rgba(0,0,0,0.08);border-top:none;border-radius:0 0 6px 6px;background:#fff;">${rows.join('')}</div>`;

        renderErrorTray();
    }

    // ── Optimistic queue: add item + auto-submit ─────────────────────────
    function _addToScanQueue(qrData, serial, noSerial, clientParsed) {
        // Client-side duplicate guard (only for committed items)
        if (serial && !noSerial) {
            const dup = state.scannedProducts.find(p => p.serial_number === serial && !p._pending && !p._failed);
            if (dup) {
                showAlert(`⚠️ Already scanned — skipping duplicate SN: <strong>${escHtml(serial)}</strong>`, 'warning');
                if (state.pendingSerial === serial) { state.pendingSerial = null; updatePendingSerialUI(); }
                return;
            }
        }

        const itemId = state._nextItemId++;
        const parsed = clientParsed || {};
        const optimistic = {
            _itemId:       itemId,
            _pending:      true,
            _qrData:       qrData,
            _noSerial:     !!noSerial,
            serial_number: serial || (noSerial ? 'NSN-pending' : null),
            gtin_code:     parsed.gtin_code || null,
            batch_number:  parsed.batch_number || null,
            expiry_date:   parsed.expiry_date || null,
            production_date: parsed.production_date || null,
            product_name:  parsed.product_name || null,
            material_code: parsed.material_code || null,
            quantity:      parsed.quantity || null,
            action:        state.currentAction,
        };
        state.scannedProducts.push(optimistic);
        updateProductsList();

        if (serial && state.pendingSerial === serial) {
            state.pendingSerial = null; updatePendingSerialUI();
        }

        _autoSubmitItem(optimistic);
    }

    async function _autoSubmitItem(item) {
        const body = {
            session_key:    state.sessionKey,
            qr_data:        item._qrData,
            action:         item.action || state.currentAction,
            shelf_location: document.getElementById('mtShelfLocation')?.value || '',
        };
        if (item._noSerial) {
            body.no_serial = true;
        } else if (item.serial_number && !item.serial_number.startsWith('NSN-')) {
            body.serial_number = item.serial_number;
        }

        try {
            const resp = await fetch(API.scanProduct(), {
                method: 'POST',
                headers: { 'Content-Type': 'application/json', 'X-CSRFToken': getCookie('csrftoken') },
                credentials: 'same-origin',
                body: JSON.stringify(body),
            });
            const result = await resp.json();

            const idx = state.scannedProducts.findIndex(p => p._itemId === item._itemId);
            if (idx === -1) return; // item was removed by user

            if (!resp.ok) {
                if (result.error_code === 'DUPLICATE_SERIAL') {
                    // Remove optimistic — it's a duplicate
                    state.scannedProducts.splice(idx, 1);
                    updateProductsList();
                    showAlert(`⚠️ Duplicate — <strong>${escHtml(item.serial_number)}</strong> already in session`, 'warning');
                } else {
                    // Mark failed — push to error tray
                    state.scannedProducts.splice(idx, 1);
                    state.errorTray.push({
                        id:      item._itemId,
                        qrData:  item._qrData,
                        serial:  item.serial_number,
                        noSerial: item._noSerial,
                        parsed:  { gtin_code: item.gtin_code, batch_number: item.batch_number, expiry_date: item.expiry_date },
                        error:   result.error || result.message || resp.statusText,
                        action:  item.action,
                    });
                    updateProductsList();
                    showAlert(`✗ Failed: ${escHtml(result.error || 'Scan failed')}`, 'error');
                }
                return;
            }

            const committed = result.scan_log || result.data || result;
            state.scannedProducts[idx] = committed;
            updateProductsList();
            const sn = committed.serial_number || '—';
            const snDisplay = sn.startsWith('NSN-') ? '<em>No Serial</em>' : `<strong>${escHtml(sn)}</strong>`;
            showAlert(
                `✓ ${snDisplay}` +
                (committed.gtin_code    ? ` · GTIN: ${escHtml(committed.gtin_code)}`    : '') +
                (committed.batch_number ? ` · Batch: ${escHtml(committed.batch_number)}` : ''),
                'success'
            );
        } catch (err) {
            const idx = state.scannedProducts.findIndex(p => p._itemId === item._itemId);
            if (idx !== -1) {
                state.scannedProducts.splice(idx, 1);
                state.errorTray.push({
                    id:     item._itemId,
                    qrData: item._qrData,
                    serial: item.serial_number,
                    noSerial: item._noSerial,
                    parsed: { gtin_code: item.gtin_code, batch_number: item.batch_number },
                    error:  err.message,
                    action: item.action,
                });
                updateProductsList();
            }
            showAlert(`✗ Network error: ${escHtml(err.message)}`, 'error');
        }
    }

    // ── Error Tray ───────────────────────────────────────────────────────
    function renderErrorTray() {
        const section = document.getElementById('mtErrorTraySection');
        if (!section) return;
        if (!state.errorTray.length) { section.style.display = 'none'; return; }
        section.style.display = '';
        const items = state.errorTray.map(item => `
            <div style="display:flex;justify-content:space-between;align-items:flex-start;padding:10px 12px;border-bottom:1px solid var(--mt-danger-light);background:#fff;gap:10px;">
                <div style="flex:1;min-width:0;">
                    <div style="font-size:0.78em;color:var(--mt-danger);font-weight:700;margin-bottom:3px;">✗ ${escHtml(item.error)}</div>
                    ${item.serial ? `<div style="font-size:0.75em;color:var(--mt-gray-600);">SN: <span style="font-family:ui-monospace,monospace;">${escHtml(item.serial)}</span></div>` : ''}
                    ${item.parsed.gtin_code ? `<div style="font-size:0.75em;color:var(--mt-gray-600);">GTIN: ${escHtml(item.parsed.gtin_code)}</div>` : ''}
                    ${item.parsed.batch_number ? `<div style="font-size:0.75em;color:var(--mt-gray-600);">Lot: ${escHtml(item.parsed.batch_number)}</div>` : ''}
                </div>
                <div style="display:flex;gap:5px;flex-shrink:0;">
                    <button onclick="window.mtRetryErrorItem(${item.id})" style="background:var(--mt-warning-light);color:var(--mt-warning-text);border:1px solid var(--mt-warning-border);padding:4px 10px;border-radius:6px;font-size:0.75em;font-weight:700;cursor:pointer;">Retry</button>
                    <button onclick="window.mtDismissErrorItem(${item.id})" style="background:var(--mt-gray-100);color:var(--mt-gray-600);border:1px solid var(--mt-gray-200);padding:4px 8px;border-radius:6px;font-size:0.75em;cursor:pointer;">✕</button>
                </div>
            </div>`).join('');
        section.innerHTML = `
            <div style="background:var(--mt-danger-light);border:1.5px solid var(--mt-danger);border-radius:10px;overflow:hidden;margin-bottom:14px;">
                <div style="display:flex;justify-content:space-between;align-items:center;padding:10px 12px;background:var(--mt-danger-light);border-bottom:1px solid var(--mt-danger-border);">
                    <span style="font-weight:700;color:var(--mt-danger);font-size:0.9em;">⚠ ${state.errorTray.length} Failed Scan${state.errorTray.length > 1 ? 's' : ''}</span>
                    <button onclick="window.mtClearErrorTray()" style="background:var(--mt-danger);color:#fff;border:none;padding:4px 12px;border-radius:6px;font-size:0.75em;font-weight:700;cursor:pointer;">Clear All</button>
                </div>
                ${items}
            </div>`;
    }

    function mtRetryErrorItem(id) {
        const idx = state.errorTray.findIndex(i => i.id === id);
        if (idx === -1) return;
        const item = state.errorTray.splice(idx, 1)[0];
        _addToScanQueue(item.qrData, item.noSerial ? null : item.serial, item.noSerial, item.parsed);
    }

    function mtDismissErrorItem(id) {
        state.errorTray = state.errorTray.filter(i => i.id !== id);
        renderErrorTray();
    }

    function mtClearErrorTray() {
        state.errorTray = [];
        renderErrorTray();
    }

    // ── Delete product (server + local) ─────────────────────────────────
    async function deleteProduct(index) {
        const product = state.scannedProducts[index];
        if (!product) return;

        // Pending optimistic item — just remove locally
        if (product._pending) {
            state.scannedProducts.splice(index, 1);
            updateProductsList();
            showAlert('Pending item removed.', 'info');
            return;
        }

        if (product.id) {
            try {
                const resp = isTransfer()
                    ? await fetch(API.deleteTransferProduct(product.id), {
                        method: 'DELETE',
                        headers: { 'X-CSRFToken': getCookie('csrftoken') },
                        credentials: 'same-origin',
                    })
                    : await fetch(API.deleteProduct(), {
                        method: 'POST',
                        headers: { 'Content-Type': 'application/json', 'X-CSRFToken': getCookie('csrftoken') },
                        credentials: 'same-origin',
                        body: JSON.stringify({ scan_log_id: product.id }),
                    });
                if (!resp.ok && resp.status !== 404) {
                    const err = await resp.json().catch(() => ({}));
                    showAlert(`Could not remove: ${err.error || resp.statusText}`, 'error');
                    return;
                }
            } catch (e) {
                showAlert(`Network error: ${e.message}`, 'error');
                return;
            }
        }
        state.scannedProducts.splice(index, 1);
        updateProductsList();
        showAlert('Product removed — you can scan it again.', 'info');
    }

    // ── Clear all products ───────────────────────────────────────────────
    async function clearAllProducts() {
        if (!state.scannedProducts.length) return;
        if (!confirm(`Remove all ${state.scannedProducts.length} scanned item(s)? This cannot be undone.`)) return;
        showLoading(true);
        const ids = state.scannedProducts.filter(p => p.id && !p._pending).map(p => p.id);
        await Promise.all(ids.map(id =>
            (isTransfer()
                ? fetch(API.deleteTransferProduct(id), {
                    method: 'DELETE',
                    headers: { 'X-CSRFToken': getCookie('csrftoken') },
                    credentials: 'same-origin',
                })
                : fetch(API.deleteProduct(), {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json', 'X-CSRFToken': getCookie('csrftoken') },
                    credentials: 'same-origin',
                    body: JSON.stringify({ scan_log_id: id }),
                })
            ).catch(() => {})
        ));
        state.scannedProducts = [];
        state.errorTray = [];
        updateProductsList();
        showLoading(false);
        showAlert('All items cleared.', 'info');
    }

    // ── Edit product bottom sheet ────────────────────────────────────────
    function editProduct(index) {
        const product = state.scannedProducts[index];
        if (!product || product._pending) return;
        _closeEditSheet();

        const toDateInput = (ds) => {
            if (!ds) return '';
            try { return new Date(ds).toISOString().slice(0, 10); } catch { return ''; }
        };
        const isFakeSerial = !product.serial_number || product.serial_number.startsWith('NSN-');
        const serialVal = isFakeSerial ? '' : (product.serial_number || '');

        const sheet = document.createElement('div');
        sheet.id = 'mtEditProductSheet';
        sheet.setAttribute('data-index', index);
        sheet.innerHTML = `
            <!-- Backdrop -->
            <div onclick="window.mtCancelProductEdit()" style="position:fixed;inset:0;background:rgba(0,0,0,0.45);z-index:9998;animation:mtFadeIn 0.15s ease;"></div>
            <!-- Sheet -->
            <div style="position:fixed;bottom:0;left:50%;transform:translateX(-50%) translateY(0);width:100%;max-width:480px;background:#fff;border-radius:22px 22px 0 0;z-index:9999;max-height:92vh;overflow-y:auto;box-shadow:0 -8px 40px rgba(0,0,0,0.22);animation:mtSlideUpSheet 0.22s cubic-bezier(0.34,1.12,0.64,1);">
                <div style="text-align:center;padding:12px 0 4px;">
                    <div style="width:36px;height:4px;background:var(--mt-gray-200);border-radius:2px;display:inline-block;"></div>
                </div>
                <div style="display:flex;justify-content:space-between;align-items:center;padding:6px 18px 14px;border-bottom:1px solid var(--mt-gray-100);">
                    <div>
                        <div style="font-weight:800;font-size:1em;color:var(--mt-gray-900);">Edit Scan</div>
                        <div style="font-size:0.75em;color:var(--mt-gray-400);margin-top:2px;">Item #${state.scannedProducts.length - index} &nbsp;·&nbsp; Correct any field below</div>
                    </div>
                    <button onclick="window.mtCancelProductEdit()" style="background:var(--mt-gray-100);color:var(--mt-gray-600);border:none;width:34px;height:34px;border-radius:50%;font-size:1.1em;cursor:pointer;display:flex;align-items:center;justify-content:center;font-weight:700;">&#x2715;</button>
                </div>
                <div style="padding:16px 18px;">
                    <div style="margin-bottom:12px;">
                        <label style="font-size:0.72em;font-weight:700;color:var(--mt-gray-600);text-transform:uppercase;letter-spacing:0.06em;display:block;margin-bottom:5px;">Serial Number</label>
                        <input id="mt_edt_serial" value="${escHtml(serialVal)}" autocomplete="off" autocorrect="off"
                            placeholder="${isFakeSerial ? 'None — leave blank to keep no-serial' : 'e.g. SN0012345'}"
                            style="width:100%;padding:11px 13px;border:2px solid var(--mt-gray-200);border-radius:10px;font-family:ui-monospace,monospace;font-size:0.92em;color:var(--mt-gray-900);outline:none;box-sizing:border-box;"
                            onfocus="this.style.borderColor='var(--mt-primary)'" onblur="this.style.borderColor='var(--mt-gray-200)'">
                    </div>
                    <div style="display:grid;grid-template-columns:1fr 1fr;gap:10px;margin-bottom:12px;">
                        <div>
                            <label style="font-size:0.72em;font-weight:700;color:var(--mt-gray-600);text-transform:uppercase;letter-spacing:0.06em;display:block;margin-bottom:5px;">GTIN</label>
                            <input id="mt_edt_gtin" value="${escHtml(product.gtin_code || '')}" autocomplete="off"
                                style="width:100%;padding:11px 13px;border:2px solid var(--mt-gray-200);border-radius:10px;font-family:ui-monospace,monospace;font-size:0.88em;color:var(--mt-gray-900);outline:none;box-sizing:border-box;"
                                onfocus="this.style.borderColor='var(--mt-primary)'" onblur="this.style.borderColor='var(--mt-gray-200)'">
                        </div>
                        <div>
                            <label style="font-size:0.72em;font-weight:700;color:var(--mt-gray-600);text-transform:uppercase;letter-spacing:0.06em;display:block;margin-bottom:5px;">Batch / Lot</label>
                            <input id="mt_edt_batch" value="${escHtml(product.batch_number || '')}" autocomplete="off"
                                style="width:100%;padding:11px 13px;border:2px solid var(--mt-gray-200);border-radius:10px;font-size:0.88em;color:var(--mt-gray-900);outline:none;box-sizing:border-box;"
                                onfocus="this.style.borderColor='var(--mt-primary)'" onblur="this.style.borderColor='var(--mt-gray-200)'">
                        </div>
                    </div>
                    <div style="display:grid;grid-template-columns:1fr 1fr;gap:10px;margin-bottom:12px;">
                        <div>
                            <label style="font-size:0.72em;font-weight:700;color:var(--mt-gray-600);text-transform:uppercase;letter-spacing:0.06em;display:block;margin-bottom:5px;">Expiry Date</label>
                            <input id="mt_edt_expiry" type="date" value="${toDateInput(product.expiry_date)}"
                                style="width:100%;padding:11px 13px;border:2px solid var(--mt-gray-200);border-radius:10px;font-size:0.88em;color:var(--mt-gray-900);outline:none;box-sizing:border-box;"
                                onfocus="this.style.borderColor='var(--mt-warning)'" onblur="this.style.borderColor='var(--mt-gray-200)'">
                        </div>
                        <div>
                            <label style="font-size:0.72em;font-weight:700;color:var(--mt-gray-600);text-transform:uppercase;letter-spacing:0.06em;display:block;margin-bottom:5px;">Qty</label>
                            <input id="mt_edt_qty" type="number" min="1" value="${product.quantity || 1}"
                                style="width:100%;padding:11px 13px;border:2px solid var(--mt-gray-200);border-radius:10px;font-size:0.88em;color:var(--mt-gray-900);outline:none;box-sizing:border-box;"
                                onfocus="this.style.borderColor='var(--mt-primary)'" onblur="this.style.borderColor='var(--mt-gray-200)'">
                        </div>
                    </div>
                    <div style="margin-bottom:16px;">
                        <label style="font-size:0.72em;font-weight:700;color:var(--mt-gray-600);text-transform:uppercase;letter-spacing:0.06em;display:block;margin-bottom:5px;">Material Code</label>
                        <input id="mt_edt_matcode" value="${escHtml(product.material_code || '')}" autocomplete="off"
                            style="width:100%;padding:11px 13px;border:2px solid var(--mt-gray-200);border-radius:10px;font-size:0.88em;color:var(--mt-gray-900);outline:none;box-sizing:border-box;"
                            onfocus="this.style.borderColor='var(--mt-primary)'" onblur="this.style.borderColor='var(--mt-gray-200)'">
                    </div>
                    <div id="mtEditSheetMsg" style="display:none;margin-bottom:10px;padding:9px 12px;border-radius:8px;font-size:0.82em;font-weight:600;"></div>
                    <div style="display:flex;gap:10px;">
                        <button onclick="window.mtCancelProductEdit()" style="flex:1;background:var(--mt-gray-50);color:var(--mt-gray-600);border:1.5px solid var(--mt-gray-200);padding:13px;border-radius:12px;font-weight:700;font-size:0.88em;cursor:pointer;">Cancel</button>
                        <button id="mtEditSaveBtn" onclick="window.mtSaveProductEdit()" style="flex:2;background:linear-gradient(135deg,var(--mt-primary),var(--mt-primary));color:white;border:none;padding:13px;border-radius:12px;font-weight:700;font-size:0.88em;cursor:pointer;box-shadow:0 4px 14px rgba(0,86,179,0.3);">Save Changes</button>
                    </div>
                </div>
            </div>`;
        document.body.appendChild(sheet);
        setTimeout(() => document.getElementById('mt_edt_serial')?.focus(), 220);
    }

    function _closeEditSheet() {
        const el = document.getElementById('mtEditProductSheet');
        if (el) el.remove();
    }

    function cancelProductEdit() { _closeEditSheet(); }

    async function saveProductEdit() {
        const sheet = document.getElementById('mtEditProductSheet');
        if (!sheet) return;
        const index = parseInt(sheet.getAttribute('data-index'));
        const product = state.scannedProducts[index];
        if (!product) return;

        const serial  = document.getElementById('mt_edt_serial')?.value.trim()  || '';
        const gtin    = document.getElementById('mt_edt_gtin')?.value.trim()    || '';
        const batch   = document.getElementById('mt_edt_batch')?.value.trim()   || '';
        const expiry  = document.getElementById('mt_edt_expiry')?.value.trim()  || '';
        const matcode = document.getElementById('mt_edt_matcode')?.value.trim() || '';
        const qty     = parseInt(document.getElementById('mt_edt_qty')?.value)  || 1;

        const patch = { gtin_code: gtin||null, batch_number: batch||null, expiry_date: expiry||null, material_code: matcode||null, quantity: qty };
        if (serial) patch.serial_number = serial;

        const saveBtn = document.getElementById('mtEditSaveBtn');
        const msgEl   = document.getElementById('mtEditSheetMsg');
        if (saveBtn) { saveBtn.disabled = true; saveBtn.textContent = 'Saving…'; }

        if (product.id) {
            try {
                const resp = await fetch(`/meritrack/api/sessions/scan_logs/${product.id}/`, {
                    method: 'PATCH',
                    headers: { 'Content-Type': 'application/json', 'X-CSRFToken': getCookie('csrftoken') },
                    credentials: 'same-origin',
                    body: JSON.stringify(patch),
                });
                if (!resp.ok) {
                    // PATCH may not exist — update locally only
                    Object.assign(state.scannedProducts[index], patch);
                    _closeEditSheet();
                    updateProductsList();
                    showAlert('Changes saved locally.', 'success');
                    return;
                }
                const updated = await resp.json();
                Object.assign(state.scannedProducts[index], updated);
            } catch (e) {
                if (msgEl) { msgEl.textContent = `Network error: ${e.message}`; msgEl.style.cssText = 'display:block;background:var(--mt-danger-light);color:var(--mt-danger);margin-bottom:10px;padding:9px 12px;border-radius:8px;font-size:0.82em;font-weight:600;'; }
                if (saveBtn) { saveBtn.disabled = false; saveBtn.textContent = 'Save Changes'; }
                return;
            }
        } else {
            Object.assign(state.scannedProducts[index], patch);
        }
        _closeEditSheet();
        updateProductsList();
        showAlert('Changes saved.', 'success');
    }

    // ── Skip Serial Toggle ───────────────────────────────────────────────
    function toggleSkipSerial() {
        state.skipSerialPrompt = !state.skipSerialPrompt;
        const btn   = document.getElementById('mtSkipSerialBtn');
        const pill  = document.getElementById('mtSkipSerialPill');
        const sub   = document.getElementById('mtSkipSerialSublabel');
        if (state.skipSerialPrompt) {
            if (btn)  btn.style.borderColor  = 'var(--mt-warning)';
            if (btn)  btn.style.background   = 'var(--mt-warning-light)';
            if (pill) { pill.textContent = 'ON'; pill.style.background = 'var(--mt-warning)'; pill.style.color = '#fff'; }
            if (sub)  sub.textContent = 'Products without serial numbers will be saved automatically';
            showAlert('Skip Serial ON — products without SN will be saved automatically', 'info');
        } else {
            if (btn)  btn.style.borderColor  = 'var(--mt-gray-200)';
            if (btn)  btn.style.background   = 'var(--mt-gray-50)';
            if (pill) { pill.textContent = 'OFF'; pill.style.background = 'var(--mt-gray-200)'; pill.style.color = 'var(--mt-gray-600)'; }
            if (sub)  sub.textContent = 'Tap to enable for products without serial numbers';
            showAlert('Skip Serial OFF — you will be prompted for serial numbers', 'info');
        }
    }

    // ── Input Mode Switcher (Camera ↔ Scanner Gun) ───────────────────────
    function switchInputMode(mode) {
        state.inputMode = mode;
        const cameraSection = document.getElementById('mtCameraScanSection');
        const scannerPanel  = document.getElementById('mtScannerGunPanel');
        const btnCamera     = document.getElementById('mtModeBtnCamera');
        const btnScanner    = document.getElementById('mtModeBtnScanner');

        const activeStyle = 'background:var(--mt-primary);color:white;box-shadow:0 2px 8px rgba(0,86,179,0.3);flex:1;padding:9px 12px;border:none;border-radius:8px;font-size:0.85em;font-weight:700;cursor:pointer;transition:all 0.2s;';
        const idleStyle   = 'background:transparent;color:var(--mt-gray-600);box-shadow:none;flex:1;padding:9px 12px;border:none;border-radius:8px;font-size:0.85em;font-weight:700;cursor:pointer;transition:all 0.2s;';

        if (mode === 'scanner') {
            if (cameraSection) cameraSection.style.display = 'none';
            if (scannerPanel)  scannerPanel.style.display  = '';
            if (btnCamera)  btnCamera.setAttribute('style',  idleStyle);
            if (btnScanner) btnScanner.setAttribute('style', activeStyle);
            if (state.scanner && state.isScanning) {
                teardownPinchZoom();
                try { state.scanner.stop().catch(() => {}); } catch (e) {}
                state.scanner = null; state.isScanning = false;
            }
            setTimeout(() => document.getElementById('mtScannerGunInput')?.focus(), 150);
        } else {
            if (cameraSection) cameraSection.style.display = '';
            if (scannerPanel)  scannerPanel.style.display  = 'none';
            if (btnCamera)  btnCamera.setAttribute('style',  activeStyle);
            if (btnScanner) btnScanner.setAttribute('style', idleStyle);
            if (!state.isScanning) {
                setTimeout(() => {
                    const r = document.getElementById('qr-reader');
                    if (r) r.innerHTML = '';
                    state.scanner = null;
                    initializeQRScanner();
                }, 150);
            }
        }
    }

    // ── Scanner Gun Input Handler ────────────────────────────────────────
    function handleScannerGunInput() {
        const textarea = document.getElementById('mtScannerGunInput');
        if (!textarea) return;
        const raw = textarea.value;
        if (/[\r\n]/.test(raw)) {
            clearTimeout(state.sgDebounceTimer);
            _processScannerGunText(raw);
            return;
        }
        clearTimeout(state.sgDebounceTimer);
        state.sgDebounceTimer = setTimeout(() => {
            const val = document.getElementById('mtScannerGunInput')?.value || '';
            if (val.trim()) _processScannerGunText(val);
        }, 350);
    }

    function _processScannerGunText(raw) {
        const lines = raw.split(/[\r\n]+/).map(l => normalizeScanText(l)).filter(Boolean);
        if (!lines.length) return;
        const textarea = document.getElementById('mtScannerGunInput');
        setTimeout(() => { if (textarea) textarea.value = ''; }, 600);
        state.sgQueue.push(...lines);
        _updateSgQueueStatus();
        if (!state.sgProcessing) _processScannerGunQueue();
    }

    async function _processScannerGunQueue() {
        if (!state.sgQueue.length) {
            state.sgProcessing = false;
            _updateSgQueueStatus();
            setTimeout(() => {
                if (!state.sgQueue.length && !state.sgProcessing) {
                    const prev = document.getElementById('mtScannerGunPreview');
                    if (prev) prev.style.display = 'none';
                }
            }, 2000);
            return;
        }

        if (state.awaitingSNForProduct) {
            state.sgProcessing = true;
            const ctx = state.awaitingSNForProduct;
            state.awaitingSNForProduct = null;
            updateAwaitingSNUI();
            const serialData = state.sgQueue.shift();
            _updateSgQueueStatus();
            const snParsed = parseGS1Data(serialData);
            const extractedSN = snParsed.serial_number || serialData.trim();
            if (!extractedSN) {
                showAlert('Could not extract serial. Try scanning again.', 'error');
                state.awaitingSNForProduct = ctx;
                updateAwaitingSNUI();
                state.sgProcessing = false;
                _updateSgQueueStatus();
                return;
            }
            _addToScanQueue(ctx.qrData, extractedSN, false, ctx.parsedFields || {});
            setTimeout(() => _processScannerGunQueue(), 250);
            return;
        }

        state.sgProcessing = true;
        const scanData = state.sgQueue.shift();
        _updateSgQueueStatus();

        const parsed = parseGS1Data(scanData);
        _showScannerGunPreview(parsed, scanData);

        const hasSN     = !!parsed.serial_number;
        const hasDetail = !!(parsed.gtin_code || parsed.batch_number || parsed.expiry_date);

        if (hasSN && !hasDetail) {
            state.pendingSerial = parsed.serial_number;
            updatePendingSerialUI();
            showAlert(`Serial <strong>${escHtml(parsed.serial_number)}</strong> captured — scan product barcode next`, 'info');
            state.sgProcessing = false;
            setTimeout(() => _processScannerGunQueue(), 250);
            return;
        }

        if (!hasSN && !state.pendingSerial) {
            if (state.skipSerialPrompt) {
                _addToScanQueue(scanData, null, true, parsed);
                state.sgProcessing = false;
                setTimeout(() => _processScannerGunQueue(), 250);
            } else {
                showSerialModal(scanData, null, parsed);
                state.sgProcessing = false;
                _updateSgQueueStatus();
            }
            return;
        }

        const effectiveSN = state.pendingSerial || parsed.serial_number || null;
        if (state.pendingSerial) { state.pendingSerial = null; updatePendingSerialUI(); }
        _addToScanQueue(scanData, effectiveSN, false, parsed);
        state.sgProcessing = false;
        setTimeout(() => _processScannerGunQueue(), 250);
    }

    function _showScannerGunPreview(parsed, rawData) {
        const preview = document.getElementById('mtScannerGunPreview');
        if (!preview) return;
        const fmtDate = (d) => {
            if (!d) return d;
            try { return new Date(d).toLocaleDateString('en-GB', {day:'2-digit',month:'short',year:'numeric'}); }
            catch { return d; }
        };
        const fields = [
            parsed.serial_number    ? { label: 'Serial',      value: parsed.serial_number,            color: 'var(--mt-info)' } : null,
            parsed.gtin_code        ? { label: 'GTIN',         value: parsed.gtin_code,                 color: 'var(--mt-purple)' } : null,
            parsed.batch_number     ? { label: 'Batch / Lot',  value: parsed.batch_number,              color: 'var(--mt-secondary)' } : null,
            parsed.expiry_date      ? { label: 'Expiry',       value: fmtDate(parsed.expiry_date),      color: 'var(--mt-warning)' } : null,
            parsed.production_date  ? { label: 'Produced',     value: fmtDate(parsed.production_date),  color: 'var(--mt-success)' } : null,
            parsed.best_before_date ? { label: 'Best Before',  value: fmtDate(parsed.best_before_date), color: 'var(--mt-gray-600)' } : null,
            parsed.quantity         ? { label: 'Qty',           value: parsed.quantity,                  color: 'var(--mt-danger)' } : null,
        ].filter(Boolean);

        if (!fields.length) {
            preview.innerHTML = `<div style="color:var(--mt-gray-600);font-size:0.78em;margin-bottom:4px;">Processing raw data...</div><div style="color:var(--mt-gray-700);font-family:ui-monospace,monospace;font-size:0.72em;word-break:break-all;opacity:0.7;">${escHtml(rawData.substring(0, 100))}</div>`;
        } else {
            const cells = fields.map(f => `
                <div style="background:rgba(255,255,255,0.06);border:1px solid rgba(255,255,255,0.1);border-radius:8px;padding:8px 10px;">
                    <div style="color:var(--mt-gray-600);font-size:0.65em;font-weight:700;text-transform:uppercase;letter-spacing:0.06em;margin-bottom:3px;">${f.label}</div>
                    <div style="color:${f.color};font-size:0.85em;font-weight:700;font-family:ui-monospace,monospace;word-break:break-all;">${escHtml(String(f.value))}</div>
                </div>`).join('');
            preview.innerHTML = `<div style="color:var(--mt-gray-600);font-size:0.7em;font-weight:600;margin-bottom:8px;text-transform:uppercase;letter-spacing:0.06em;">Parsed ↓</div><div style="display:grid;grid-template-columns:repeat(2,1fr);gap:6px;">${cells}</div>`;
        }
        preview.style.display = 'block';
    }

    function _updateSgQueueStatus() {
        const el = document.getElementById('mtSgQueueStatus');
        if (!el) return;
        const remaining = state.sgQueue.length;
        if (!remaining && !state.sgProcessing) { el.style.display = 'none'; return; }
        el.style.display = '';
        el.textContent = state.sgProcessing
            ? (remaining > 0 ? `Processing… ${remaining} more in queue` : 'Processing…')
            : `${remaining} item${remaining !== 1 ? 's' : ''} queued`;
    }

    // ── Camera manual input with live preview ────────────────────────────
    function handleCameraManualInput() {
        const preview   = document.getElementById('mtCameraManualPreview');
        const submitBtn = document.getElementById('mtCameraManualSubmitBtn');
        if (preview)   preview.style.display  = 'none';
        if (submitBtn) submitBtn.style.display = 'none';
        clearTimeout(state.sgDebounceTimer);
        state.sgDebounceTimer = setTimeout(() => {
            const val = (document.getElementById('mtManualQRInput')?.value || '').trim();
            if (!val) return;
            const parsed = parseGS1Data(val);
            const fmtDate = (d) => {
                if (!d) return d;
                try { return new Date(d).toLocaleDateString('en-GB', {day:'2-digit',month:'short',year:'numeric'}); } catch { return d; }
            };
            const fields = [
                parsed.serial_number    ? { label: 'Serial',     value: parsed.serial_number,            color: 'var(--mt-primary)' } : null,
                parsed.gtin_code        ? { label: 'GTIN',        value: parsed.gtin_code,                 color: 'var(--mt-purple)' } : null,
                parsed.batch_number     ? { label: 'Batch / Lot', value: parsed.batch_number,              color: 'var(--mt-secondary)' } : null,
                parsed.expiry_date      ? { label: 'Expiry',      value: fmtDate(parsed.expiry_date),      color: 'var(--mt-warning-dark)' } : null,
                parsed.production_date  ? { label: 'Produced',    value: fmtDate(parsed.production_date),  color: 'var(--mt-success)' } : null,
                parsed.best_before_date ? { label: 'Best Before', value: fmtDate(parsed.best_before_date), color: 'var(--mt-gray-600)' } : null,
                parsed.quantity         ? { label: 'Qty',          value: parsed.quantity,                  color: 'var(--mt-danger)' } : null,
            ].filter(Boolean);
            if (!preview) return;
            if (!fields.length) {
                preview.innerHTML = `<span style="color:var(--mt-gray-400);font-size:0.82em;">No structured GS1 fields detected — raw data will be sent as-is.</span>`;
            } else {
                const cells = fields.map(f => `<div style="display:flex;align-items:baseline;gap:6px;margin-bottom:4px;"><span style="font-size:0.7em;font-weight:700;color:var(--mt-gray-400);text-transform:uppercase;letter-spacing:0.05em;min-width:68px;flex-shrink:0;">${f.label}</span><span style="font-family:ui-monospace,monospace;font-size:0.85em;font-weight:700;color:${f.color};word-break:break-all;">${escHtml(String(f.value))}</span></div>`).join('');
                preview.innerHTML = `<div style="font-size:0.72em;font-weight:700;color:var(--mt-gray-600);text-transform:uppercase;letter-spacing:0.06em;margin-bottom:6px;">Parsed fields</div>${cells}`;
            }
            preview.style.display = 'block';
            if (submitBtn) submitBtn.style.display = '';
        }, 400);
    }

    function submitCameraManualQR() {
        const val = (document.getElementById('mtManualQRInput')?.value || '').trim();
        if (!val) { showAlert('Enter QR / barcode data first.', 'error'); return; }
        const parsed = parseGS1Data(val);
        const hasSN  = !!parsed.serial_number;
        if (!hasSN && !state.skipSerialPrompt) {
            _addToScanQueue(val, state.pendingSerial || null, false, parsed);
        } else if (!hasSN && state.skipSerialPrompt) {
            _addToScanQueue(val, null, true, parsed);
        } else {
            _addToScanQueue(val, state.pendingSerial || parsed.serial_number || null, false, parsed);
        }
        const textarea = document.getElementById('mtManualQRInput');
        if (textarea) textarea.value = '';
        const preview = document.getElementById('mtCameraManualPreview');
        if (preview) preview.style.display = 'none';
        const submitBtn = document.getElementById('mtCameraManualSubmitBtn');
        if (submitBtn) submitBtn.style.display = 'none';
    }

    // ── Awaiting SN manual area ──────────────────────────────────────────
    function toggleAwaitingSNManualInput() {
        const area = document.getElementById('mtAwaitingSNManualArea');
        if (!area) return;
        const visible = area.style.display !== 'none';
        area.style.display = visible ? 'none' : 'block';
        if (!visible) setTimeout(() => document.getElementById('mtAwaitingSNManualInput')?.focus(), 80);
    }

    function submitAwaitingSNManual() {
        const sn = (document.getElementById('mtAwaitingSNManualInput')?.value || '').trim();
        if (!sn) { showAlert('Enter the serial number first.', 'error'); return; }
        const ctx = state.awaitingSNForProduct;
        if (!ctx) { showAlert('No product waiting for serial.', 'warning'); return; }
        state.awaitingSNForProduct = null;
        updateAwaitingSNUI();
        document.getElementById('mtAwaitingSNManualArea').style.display = 'none';
        document.getElementById('mtAwaitingSNManualInput').value = '';
        _addToScanQueue(ctx.qrData, sn, false, ctx.parsedFields || {});
        safeResumeScanner();
    }

    // ── Update smChooseNo/smSubmitWithSerial to use _addToScanQueue ──────
    // (override the existing functions already defined above — they're hoisted
    // but we redefine here so the queue-aware version takes effect)

    function finishScanning() {
        const pendingCount = state.scannedProducts.filter(p => p._pending).length;
        if (pendingCount > 0) {
            showAlert(`⏳ ${pendingCount} scan${pendingCount > 1 ? 's' : ''} still saving… please wait and try again.`, 'warning');
            return;
        }
        if (state.errorTray.length > 0) {
            if (!confirm(`${state.errorTray.length} scan${state.errorTray.length > 1 ? 's' : ''} failed and will be discarded. Complete session anyway?`)) return;
        }
        if (!state.scannedProducts.length) { showAlert('Scan at least one product', 'error'); return; }
        teardownPinchZoom();
        if (state.scanner) { try { state.scanner.stop(); } catch(e) {} }
        hideSection('mtStep2');
        showSection('mtStep3');
        updateStepIndicator(3);
        const el = (id) => document.getElementById(id);
        if (el('mtReviewSession')) el('mtReviewSession').textContent = state.sessionKey;
        if (el('mtReviewHospital')) el('mtReviewHospital').textContent = state.hospitalName;
        if (el('mtReviewCount')) el('mtReviewCount').textContent = state.scannedProducts.length;
        updateReviewList();
        loadStep3Remarks();
    }

    function updateReviewList() {
        const c = document.getElementById('mtReviewProductsList');
        const fmt = (d) => {
            if (!d) return '';
            try { return new Date(d).toLocaleDateString('en-US', {year:'numeric',month:'short',day:'numeric'}); }
            catch { return d; }
        };
        c.innerHTML = state.scannedProducts.map(p => `<div class="mt-review-item">
            ${p.serial_number && !p.serial_number.startsWith('NSN-') ? `<p><strong>Serial:</strong> <span style="font-family:monospace;">${p.serial_number}</span></p>` : '<p><em style="color:var(--mt-gray-400);">No Serial Number</em></p>'}
            ${p.gtin_code ? `<p><strong>GTIN:</strong> <span style="font-family:monospace;">${p.gtin_code}</span></p>` : ''}
            ${p.material_code ? `<p><strong>Material:</strong> <span style="font-family:monospace;">${p.material_code}</span></p>` : ''}
            ${p.product_name ? `<p><strong>Product:</strong> ${p.product_name}</p>` : ''}
            ${p.batch_number ? `<p><strong>Lot / Batch:</strong> ${p.batch_number}</p>` : ''}
            ${p.expiry_date ? `<p><strong>Expiry:</strong> <span style="font-weight:700;color:${isDateExpiring(p.expiry_date)?'var(--mt-danger)':'var(--mt-success)'}">${fmt(p.expiry_date)}</span></p>` : ''}
            ${p.action ? `<p><strong>Action:</strong> <span class="mt-action-badge ${p.action}">${(p.action||'').replace('_',' ')}</span></p>` : ''}
        </div>`).join('');
    }

    function isDateExpiring(ds) {
        if (!ds) return false;
        try { const d = new Date(ds); return d <= new Date(Date.now()+30*86400000); } catch { return false; }
    }

    // ── Step 3 Remarks ────────────────────────────────────────────────────
    async function loadStep3Remarks() {
        if (!state.sessionKey) return;
        try {
            const resp = await fetch(`/meritrack/api/session-remarks/?session=${encodeURIComponent(state.sessionKey)}`, { credentials: 'same-origin' });
            if (resp.ok) {
                const data = await resp.json();
                renderStep3Remarks(Array.isArray(data) ? data : []);
            }
        } catch (e) {}
    }

    function renderStep3Remarks(remarks) {
        const el = document.getElementById('mtStep3RemarksList');
        if (!el) return;
        if (!remarks.length) {
            el.innerHTML = '<p style="font-size:0.83em;color:var(--mt-purple-border);margin:0 0 4px;">No remarks yet.</p>';
            return;
        }
        el.innerHTML = remarks.map(r => `
            <div style="margin-bottom:8px;padding:8px 11px;background:#fff;border:1px solid var(--mt-purple-border);border-radius:8px;font-size:0.85em;color:var(--mt-gray-800);">
                <strong style="color:var(--mt-purple);">${r.created_by_name || 'You'}</strong>
                <span style="color:var(--mt-gray-400);font-size:0.82em;margin-left:6px;">${r.created_at ? new Date(r.created_at).toLocaleTimeString([], {hour:'2-digit',minute:'2-digit'}) : ''}</span>
                <div style="margin-top:3px;">${r.remark}</div>
            </div>`).join('');
    }

    async function addStep3Remark() {
        const textarea = document.getElementById('mtStep3RemarkText');
        if (!textarea) return;
        const text = textarea.value.trim();
        if (!text || !state.sessionKey) return;
        try {
            const resp = await fetch('/meritrack/api/session-remarks/', {
                method: 'POST', credentials: 'same-origin',
                headers: { 'Content-Type': 'application/json', 'X-CSRFToken': getCookie('csrftoken') },
                body: JSON.stringify({ session: state.sessionKey, remark: text }),
            });
            if (resp.ok) {
                textarea.value = '';
                loadStep3Remarks();
            }
        } catch (e) {}
    }

    // ── Submit session ───────────────────────────────────────────────────
    async function submitSession(event) {
        // Re-entrancy guard, mirroring _startingSession on the Start Session button.
        // Without it a held Enter/Space key on the focused button fires the OS key-repeat
        // rate (10-25/sec) straight at complete_session, re-running the whole submission
        // fan-out on every keystroke.
        if (state._submitting) return;
        state._submitting = true;

        const submitBtn = (event && event.currentTarget) || document.querySelector('#mtStep3 .mt-btn-success');
        if (submitBtn) {
            submitBtn.disabled = true;
            submitBtn.blur();   // drop focus so key auto-repeat cannot re-trigger the click
        }

        showLoading(true);
        try {
            const resp = await fetch(API.completeSession(), {
                method: 'POST',
                headers: { 'Content-Type': 'application/json', 'X-CSRFToken': getCookie('csrftoken') },
                credentials: 'same-origin',
                body: JSON.stringify({ session_key: state.sessionKey }),
            });
            if (!resp.ok) throw new Error('Failed to complete session');
            let data = {};
            try { data = await resp.json(); } catch(e) {}
            if (isTransfer()) {
                showAlert('Transfer completed! Upload the confirmation document to close it and move the stock. Redirecting…', 'success');
                const key = state.sessionKey;
                setTimeout(() => { window.location.href = `/meritrack/transfers/${key}/`; }, 2200);
            } else if (data.replenishment_order_id) {
                showAlert(`Session completed! Replenishment order ${data.replenishment_order_id} created — redirecting to Orders…`, 'success');
                setTimeout(() => { window.location.href = '/meritrack/orders/'; }, 2500);
            } else {
                showAlert('Session completed! Redirecting…', 'success');
                setTimeout(() => { window.location.href = '/meritrack/dashboard/'; }, 2000);
            }
        } catch (err) {
            showAlert(`Error: ${err.message}`, 'error');
            // Only a genuine failure re-arms the button. Every success path above navigates
            // away, so the guard stays latched to keep the duplicate-submit window closed.
            state._submitting = false;
            if (submitBtn) submitBtn.disabled = false;
        } finally { showLoading(false); }
    }

    // ── Navigation ───────────────────────────────────────────────────────
    function goBackToScanning() {
        hideSection('mtStep3'); showSection('mtStep2'); updateStepIndicator(2);
        setTimeout(() => {
            const r = document.getElementById('qr-reader');
            if (r) r.innerHTML = '';
            state.scanner = null; state.isScanning = false;
            initializeQRScanner();
        }, 300);
        showAlert('Back to scanning.', 'info');
    }

    function goBackToSetup() {
        teardownPinchZoom();
        if (state.scanner) { try { state.scanner.stop(); } catch(e) {} }
        state.scannedProducts = [];
        state.pendingSerial = null;
        state.awaitingSNForProduct = null;
        updatePendingSerialUI();
        updateAwaitingSNUI();
        hideSection('mtStep2');
        showSection('mtStep1');
        updateStepIndicator(1);
    }

    // ══════════════════════════════════════════════════════════════════════
    // Session-type toggle (Audit/Replenish/Removal vs H2H Transfer)
    // ══════════════════════════════════════════════════════════════════════
    // Session type is a dropdown that starts blank; keep one reader so callers
    // never touch the control directly. Returns '' until the rep picks a type —
    // deliberately no 'audit' fallback, or a blank pick would silently file the
    // session as a stock audit.
    function currentSessionType() {
        return document.getElementById('mtSessionTypeSelect')?.value || '';
    }

    function toggleSessionType() {
        const val = currentSessionType();
        const transfer = val === 'transfer';
        const sel = document.getElementById('mtSessionTypeSelect');
        if (sel) sel.classList.toggle('transfer', transfer);
        const show = (id, on) => { const e = document.getElementById(id); if (e) e.style.display = on ? '' : 'none'; };
        show('mtToHospitalGroup', transfer);
        show('mtSessionIdBox', !transfer);
        show('mtSetupNote', !transfer);
        const heading = document.getElementById('mtSetupHeading');
        if (heading) heading.textContent = !val ? 'Start Session' : (transfer ? 'New H2H Transfer' : 'Start Audit Session');
        const btn = document.getElementById('mtStartBtn');
        if (btn) btn.textContent = !val ? 'Start' : (transfer ? 'Start Transfer' : 'Start Audit');
        if (transfer) updateRoutePreview();
    }

    function updateRoutePreview() {
        const fromSel = document.getElementById('mtHospitalSelect');
        const toSel = document.getElementById('mtToHospitalSelect');
        const preview = document.getElementById('mtRoutePreview');
        if (!fromSel || !toSel || !preview) return;
        const fromName = fromSel.selectedOptions[0]?.getAttribute('data-name') || '';
        const toName = toSel.selectedOptions[0]?.getAttribute('data-name') || '';
        if (fromSel.value && toSel.value) {
            document.getElementById('mtRouteFrom').textContent = fromName || '—';
            document.getElementById('mtRouteTo').textContent = toName || '—';
            preview.style.display = '';
        } else {
            preview.style.display = 'none';
        }
    }

    // ══════════════════════════════════════════════════════════════════════
    // Expose to window (for onclick handlers in HTML)
    // ══════════════════════════════════════════════════════════════════════
    window.mtToggleSessionType = toggleSessionType;
    window.mtUpdateRoutePreview = updateRoutePreview;
    window.mtStartScanning = startScanning;
    window.mtSetAction = setAction;
    window.mtToggleScanning = toggleScanning;
    window.mtFinishScanning = finishScanning;
    window.mtGoBackToScanning = goBackToScanning;
    window.mtGoBackToSetup = goBackToSetup;
    window.mtSubmitSession = submitSession;
    window.mtProcessManualQR = processManualQR;
    window.mtSubmitManualFields = submitManualFields;
    window.mtClearPendingSerial = clearPendingSerial;
    window.mtClearAwaitingSN = clearAwaitingSN;
    window.mtSmChooseYes = smChooseYes;
    window.mtSmChooseNo = smChooseNo;
    window.mtSmShowManualInput = smShowManualInput;
    window.mtSmSubmitWithSerial = smSubmitWithSerial;
    window.mtSmCancel = smCancel;
    window.mtUpdateHospitalInfo = updateHospitalInfo;
    window._mtDeleteProduct = deleteProduct;
    window.mtDeleteProduct = deleteProduct;
    window.mtAddStep3Remark = addStep3Remark;
    window.mtEditProduct = editProduct;
    window.mtSaveProductEdit = saveProductEdit;
    window.mtCancelProductEdit = cancelProductEdit;
    window.mtClearAllProducts = clearAllProducts;
    window.mtRetryErrorItem = mtRetryErrorItem;
    window.mtDismissErrorItem = mtDismissErrorItem;
    window.mtClearErrorTray = mtClearErrorTray;
    window.mtToggleSkipSerial = toggleSkipSerial;
    window.mtSwitchInputMode = switchInputMode;
    window.mtHandleScannerGunInput = handleScannerGunInput;
    window.mtHandleCameraManualInput = handleCameraManualInput;
    window.mtSubmitCameraManualQR = submitCameraManualQR;
    window.mtToggleAwaitingSNManualInput = toggleAwaitingSNManualInput;
    window.mtSubmitAwaitingSNManual = submitAwaitingSNManual;

    // ── Init on load ─────────────────────────────────────────────────────
    // ?type=<session type> preselects the dropdown. H2H Transfer lost its own
    // page, so the Transfers dashboard links here with ?type=transfer.
    function applyTypeFromQuery() {
        const sel = document.getElementById('mtSessionTypeSelect');
        if (!sel) return;
        const want = new URLSearchParams(window.location.search).get('type');
        if (!want) return;
        if (![...sel.options].some(o => o.value === want)) return;
        sel.value = want;
        toggleSessionType();
    }

    window.addEventListener('load', function () {
        applyTypeFromQuery();
        updateSessionIdDisplay();
        startSessionIdTicker();
    });

})();
