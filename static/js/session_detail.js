const _cfg = JSON.parse(document.getElementById('page-config').textContent);
const sessionKey = _cfg.sessionKey;

    // ── DataTables helper ──────────────────────────────────────────
    function initDataTable(tableId, extraOpts) {
        if (typeof $ === 'undefined' || !$.fn.DataTable) return;
        const $t = $('#' + tableId);
        if ($.fn.DataTable.isDataTable($t)) $t.DataTable().destroy();
        $t.DataTable(Object.assign({
            paging: true,
            pageLength: 25,
            lengthMenu: [10, 25, 50, 100],
            searching: true,
            ordering: true,
            info: true,
            autoWidth: false,
            responsive: true,
            language: {
                search: '🔍',
                searchPlaceholder: 'Search...',
                lengthMenu: 'Show _MENU_',
                info: '_START_–_END_ of _TOTAL_',
                infoEmpty: 'No records',
                emptyTable: 'No data available',
                paginate: { previous: '‹', next: '›' },
            },
            dom: '<"dt-top"lf>rt<"dt-bottom"ip>',
        }, extraOpts || {}));
    }
    
    const API = {
        base: _cfg.apiBase || '/product_scanning/api',
        sessionDetail: () => `${API.base}/sessions/by_session_key/?session_key=${sessionKey}`,
        sessionProducts: () => `${API.base}/products/session_products/?session_key=${sessionKey}`,
        uploadAttachment: () => `${API.base}/sessions/upload_attachment/`,
        forceComplete: () => `${API.base}/sessions/force_complete_session/`,
        holdSession: () => `${API.base}/sessions/hold_session/`,
        resumeSession: () => `${API.base}/sessions/resume_session/`,
        allProductRemarks: () => `${API.base}/product-remarks/?session=${encodeURIComponent(sessionKey)}`,
        productRemarks: (productId) => `${API.base}/product-remarks/?product=${productId}`,
        addProductRemark: () => `${API.base}/product-remarks/`,
        sessionRemarks: () => `${API.base}/session-remarks/?session=${sessionKey}`,
        addSessionRemark: () => `${API.base}/session-remarks/`,
    };

    let sessionData = null;
    let productsData = [];
    let productRemarksMap = {};   // { productId: [remark, ...] }
    let sessionRemarksData = [];  // batch-level remarks
    let _activeRemarkProductId = null;
    let _activeRemarkSerial = '';

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

    function showAlert(message, type = 'info') {
        const alertDiv = document.createElement('div');
        alertDiv.className = `alert alert-${type}`;
        alertDiv.innerHTML = message + ' <span style="float:right; cursor:pointer; font-size:1.2em;" onclick="this.parentElement.remove()">×</span>';
        document.getElementById('alertContainer').prepend(alertDiv);
        setTimeout(() => {
            if (alertDiv.parentElement) alertDiv.remove();
        }, 5000);
    }

    async function loadSessionData() {
        try {
            // Load session details
            const sessionResponse = await fetch(API.sessionDetail(), {
                method: 'GET',
                headers: {
                    'Content-Type': 'application/json',
                    'X-CSRFToken': getCookie('csrftoken')
                },
                credentials: 'same-origin'
            });
            if (!sessionResponse.ok) {
                throw new Error(`Failed to load session: ${sessionResponse.status} ${sessionResponse.statusText}`);
            }
            sessionData = await sessionResponse.json();

            // Load products
            const productsResponse = await fetch(API.sessionProducts(), {
                method: 'GET',
                headers: {
                    'Content-Type': 'application/json',
                    'X-CSRFToken': getCookie('csrftoken')
                },
                credentials: 'same-origin'
            });
            if (productsResponse.ok) {
                const data = await productsResponse.json();
                productsData = data.results || (Array.isArray(data) ? data : []);
            }

            renderSessionInfo();
            renderActions();
            renderAttachmentUI();
            await loadAllRemarks(); // populates productRemarksMap then calls renderProducts()

        } catch (error) {
            console.error('Error loading session:', error);
            showAlert(`Error loading session: ${error.message}`, 'error');
            document.getElementById('sessionInfo').innerHTML = `
                <div class="alert alert-error">
                    Failed to load session data. <a href="javascript:loadSessionData()">Try again</a>
                </div>
            `;
        }
    }

    function renderSessionInfo() {
        const container = document.getElementById('sessionInfo');
        
        if (!sessionData) {
            container.innerHTML = '<p style="color: #888; text-align: center;">No session data</p>';
            return;
        }

        const statusClass = `status-${sessionData.status || 'active'}`;
        const statusText = (sessionData.status || 'active').toUpperCase();
        
        // Check if this is an H2H transfer
        const isTransfer = sessionData.session_type === 'transfer';
        const hospitalDisplay = isTransfer && sessionData.to_hospital_details
            ? `<span style="color:#f97316;font-weight:700;">${sessionData.hospital_details?.name || 'N/A'} → ${sessionData.to_hospital_details?.name || 'N/A'}</span>`
            : sessionData.hospital_details?.name || 'N/A';
        const sessionTypeLabel = isTransfer
            ? '<span style="background:#f97316;color:#fff;padding:4px 10px;border-radius:4px;font-size:0.8em;margin-left:8px;">H2H TRANSFER</span>'
            : '';

        container.innerHTML = `
            <div class="info-grid">
                <div class="info-item">
                    <div class="info-label">Session ID${sessionTypeLabel}</div>
                    <div class="info-value" style="font-family: monospace; font-size: 0.9em;">${sessionData.batch_details?.batch_id || sessionData.session_key || 'N/A'}</div>
                </div>
                <div class="info-item">
                    <div class="info-label">${isTransfer ? '🔄 Route' : 'Hospital'}</div>
                    <div class="info-value">${hospitalDisplay}</div>
                </div>
                <div class="info-item">
                    <div class="info-label">Sales Rep</div>
                    <div class="info-value">${(() => { const u = sessionData.scanned_by_details; if (!u) return 'Unknown'; const full = `${u.first_name||''} ${u.last_name||''}`.trim(); return full || u.username || 'Unknown'; })()}</div>
                </div>
                <div class="info-item">
                    <div class="info-label">Products Scanned</div>
                    <div class="info-value">${productsData.length}</div>
                </div>
                <div class="info-item">
                    <div class="info-label">Status</div>
                    <div><span class="status-badge ${statusClass}">${statusText}</span></div>
                </div>
                <div class="info-item">
                    <div class="info-label">Started</div>
                    <div class="info-value">${new Date(sessionData.started_at).toLocaleString()}</div>
                </div>
                ${sessionData.completed_at ? `
                <div class="info-item">
                    <div class="info-label">Completed</div>
                    <div class="info-value">${new Date(sessionData.completed_at).toLocaleString()}</div>
                </div>
                ` : ''}
                ${sessionData.notes ? `
                <div class="info-item" style="grid-column: 1/-1;">
                    <div class="info-label">Notes</div>
                    <div class="info-value" style="font-size:0.95em;font-weight:400;color:#555;">${sessionData.notes}</div>
                </div>
                ` : ''}
            </div>
        `;

        // Show force-complete banner for active sessions + warehouse users (not viewers)
        const banner = document.getElementById('forceCompleteBanner');
        if (banner) {
            banner.style.display = (sessionData.status === 'active' && canUseDashboard && !canUseScanner && !canViewOnly) ? 'block' : 'none';
        }

        // Show on-hold / overdue banner (not for viewers)
        const holdBanner = document.getElementById('onHoldBanner');
        if (holdBanner && canUseDashboard && !canViewOnly) {
            const days = sessionData.days_awaiting;
            if (sessionData.status === 'on_hold') {
                const heldSince = sessionData.on_hold_at ? new Date(sessionData.on_hold_at).toLocaleDateString() : '';
                holdBanner.innerHTML = `
                  <div class="alert" style="display:flex;align-items:center;justify-content:space-between;flex-wrap:wrap;gap:10px;background:#fef9c3;border:1px solid #fde047;border-left:4px solid #ca8a04;">
                    <div><strong>⏸ On Hold</strong> — This session is paused. Daily overdue reminders are stopped.${heldSince ? ` Put on hold on ${heldSince}.` : ''}</div>
                    <div style="display:flex;gap:8px;flex-wrap:wrap;">
                      <button class="btn-export" onclick="resumeSession()" style="background:#0076c8;">▶ Resume Awaiting</button>
                      <button class="btn-export" onclick="const el=document.getElementById('uploadSection');el.style.display='block';el.scrollIntoView({behavior:'smooth',block:'start'});" style="background:#16a34a;">📤 Upload & Close</button>
                    </div>
                  </div>`;
                holdBanner.style.display = 'block';
            } else if (sessionData.status === 'completed' && days >= 6) {
                holdBanner.innerHTML = `
                  <div class="alert alert-warning" style="display:flex;align-items:center;justify-content:space-between;flex-wrap:wrap;gap:10px;">
                    <div><strong>⚠️ Overdue — ${days} day${days !== 1 ? 's' : ''} awaiting confirmation.</strong>
                      Daily email reminders are being sent. Put this session on hold to pause notifications and buy more time.</div>
                    <button class="btn-export" onclick="holdSession()" style="background:#d97706;color:#fff;border:none;padding:8px 18px;border-radius:6px;font-weight:700;font-size:0.88em;cursor:pointer;">⏸ Put On Hold</button>
                  </div>`;
                holdBanner.style.display = 'block';
            } else {
                holdBanner.style.display = 'none';
            }
        }
    }

    function renderProducts() {
        const tbody = document.getElementById('productsBody');

        if (!productsData || productsData.length === 0) {
            tbody.innerHTML = `
                <tr>
                    <td colspan="11">
                        <div class="empty-state">
                            <div class="empty-icon">📭</div>
                            <p>No products scanned in this session</p>
                        </div>
                    </td>
                </tr>
            `;
            return;
        }

        tbody.innerHTML = productsData.map(product => {
            const expiryDate = product.expiry_date ? new Date(product.expiry_date) : null;
            const today = new Date();
            let expiryClass = 'expiry-ok';
            let expiryText = product.expiry_date ? new Date(product.expiry_date).toLocaleDateString() : 'N/A';

            if (expiryDate) {
                if (expiryDate < today) {
                    expiryClass = 'expiry-expired';
                    expiryText += ' (EXPIRED)';
                } else if ((expiryDate - today) / (1000 * 60 * 60 * 24) < 30) {
                    expiryClass = 'expiry-warning';
                    expiryText += ' (30 days)';
                }
            }

            // Remarks summary for this product
            const remarks = productRemarksMap[product.id] || [];
            const remarkCount = remarks.length;
            const hasWarehouseRemark = remarks.some(r => r.role === 'warehouse');
            const remarkBtnStyle = hasWarehouseRemark
                ? 'background:#f5f3ff;color:#7c3aed;border:1px solid #ddd6fe;'
                : remarkCount > 0
                ? 'background:#eff6ff;color:#2563eb;border:1px solid #bfdbfe;'
                : 'background:#f8fafc;color:#64748b;border:1px solid #e2e8f0;';
            const sn = product.serial_number && !product.serial_number.startsWith('NSN-') ? product.serial_number : '(no serial)';
            const gtin = (product.gtin_code || '').replace(/'/g, "\\'");
            const material = (product.material_code || '').replace(/'/g, "\\'");
            const rowHighlight = hasWarehouseRemark
                ? 'background:#f3e8ff;box-shadow:inset 4px 0 0 #7c3aed;'
                : remarkCount > 0
                ? 'background:#fef9c3;box-shadow:inset 4px 0 0 #f59e0b;'
                : '';

            return `
                <tr style="${rowHighlight}">
                    <td><span class="serial-code">${product.serial_number && !product.serial_number.startsWith('NSN-') ? product.serial_number : '<em style="color:#9ca3af;">No Serial</em>'}</span></td>
                    <td><span class="gtin-code">${product.gtin_code || '—'}</span></td>
                    <td>${product.batch_number || '\u2014'}</td>
                    <td>${product.product_name || '\u2014'}</td>
                    <td>${product.reference_number || '\u2014'}</td>
                    <td class="${expiryClass}">${expiryText}</td>
                    <td>${product.production_date ? new Date(product.production_date).toLocaleDateString() : '\u2014'}</td>
                    <td>${product.material_code || '—'}</td>
                    <td>${product.quantity || 1}</td>
                    <td>${new Date(product.scanned_at).toLocaleString()}</td>
                    <td style="white-space:nowrap;">
                        ${canViewOnly
                            ? (remarkCount > 0
                                ? `<button onclick="openRemarkModal(${product.id}, '${sn.replace(/'/g,"\\'")}', '${gtin}', '${material}')" style="padding:5px 10px;border-radius:6px;font-size:0.78em;font-weight:700;cursor:pointer;${remarkBtnStyle}">💬 ${remarkCount} remark${remarkCount>1?'s':''}</button>`
                                : '<span style="color:#ccc;font-size:.8em;">—</span>')
                            : `<button onclick="openRemarkModal(${product.id}, '${sn.replace(/'/g,"\\'")}', '${gtin}', '${material}')" style="padding:5px 10px;border-radius:6px;font-size:0.78em;font-weight:700;cursor:pointer;${remarkBtnStyle}">💬 ${remarkCount > 0 ? remarkCount + ' remark' + (remarkCount>1?'s':'') : 'Remark'}</button>`
                        }
                    </td>
                </tr>
            `;
        }).join('');

        initDataTable('productsTable', {
            order: [[9, 'desc']],
            scrollX: true,
            columnDefs: [
                { targets: [4, 6, 7], className: 'dt-hide-mobile' },  // Ref#, Prod Date, Material
            ]
        });
    }

    // RBAC: pass server-side permission flag into JS
    const canUseScanner = _cfg.canUseScanner;
    const canUseDashboard = _cfg.canUseDashboard;
    const canViewOnly = _cfg.canViewOnly || false;
    const dashboardUrl = _cfg.dashboardUrl || '/product_scanning/dashboard/';
    const scannerUrl = _cfg.scannerUrl || '/product_scanning/';

    function renderActions() {
        const container = document.getElementById('actions');
        
        if (!sessionData) return;

        const newSessionBtn = canUseScanner
            ? `<a href="${scannerUrl}" class="btn-primary" style="text-decoration: none;">
                    New Audit Session
               </a>`
            : '';

        const exportBtn = canViewOnly ? '' : `<button class="btn-export" onclick="exportSession()">Export to CSV</button>`;
        let html = `
            <div style="display: flex; gap: 10px; flex-wrap: wrap;">
                <a href="${dashboardUrl}" class="btn-secondary" style="text-decoration: none;">
                    Back to Dashboard
                </a>
                ${newSessionBtn}
                ${exportBtn}
            </div>
        `;

        container.innerHTML = html;
    }

    function exportSession() {
        if (!sessionData || !productsData) {
            showAlert('No data to export', 'error');
            return;
        }

        const u = sessionData.scanned_by_details;
        const scannedByName = u ? (`${u.first_name||''} ${u.last_name||''}`.trim() || u.username || 'Unknown') : 'Unknown';
        
        // Check if this is an H2H transfer
        const isTransfer = sessionData.session_type === 'transfer';
        const hospitalName = sessionData.hospital_details?.name || '';
        const toHospitalName = sessionData.to_hospital_details?.name || '';

        let csv = '\uFEFF'; // BOM for Excel UTF-8
        csv += 'Batch ID,Session Type,Hospital,To Hospital,Sales Rep,Serial Number,GTIN Code,Lot/Batch No.,Product Name,Ref#,Expiry Date,Prod. Date,Material Code,Quantity,Scanned Date\n';

        function escCSV(val) {
            const s = String(val || '').replace(/"/g, '""');
            return `"${s}"`;
        }

        productsData.forEach(product => {
            const snDisplay = (product.serial_number && !product.serial_number.startsWith('NSN-')) ? product.serial_number : '';
            csv += [
                escCSV(sessionData.batch_details?.batch_id || ''),
                escCSV(isTransfer ? 'H2H Transfer' : 'Stock Audit'),
                escCSV(hospitalName), escCSV(toHospitalName), escCSV(scannedByName),
                escCSV(snDisplay), escCSV(product.gtin_code),
                escCSV(product.batch_number), escCSV(product.product_name),
                escCSV(product.reference_number),
                escCSV(product.expiry_date), escCSV(product.production_date),
                escCSV(product.material_code), escCSV(product.quantity || 1),
                escCSV(new Date(product.scanned_at).toLocaleDateString())
            ].join(',') + '\n';
        });

        const blob = new Blob([csv], { type: 'text/csv;charset=utf-8;' });
        const url = window.URL.createObjectURL(blob);
        const a = document.createElement('a');
        a.href = url;
        a.download = `${sessionData.batch_details?.batch_id || 'session'}_${new Date().toISOString().split('T')[0]}.csv`;
        document.body.appendChild(a);
        a.click();
        window.URL.revokeObjectURL(url);
        document.body.removeChild(a);

        showAlert(`Exported ${productsData.length} products`, 'success');
    }

    // ─── Attachment Upload & Preview ──────────────────────────────────────
    let selectedFile = null;

    function renderAttachmentUI() {
        if (!sessionData) return;

        const uploadSection = document.getElementById('uploadSection');
        const previewSection = document.getElementById('docPreviewSection');

        if ((sessionData.status === 'completed' || sessionData.status === 'on_hold') && canUseDashboard && !canViewOnly) {
            uploadSection.style.display = 'block';
            previewSection.style.display = 'none';
            setupDropzone();
        } else if (sessionData.status === 'closed' && sessionData.attachment_details) {
            uploadSection.style.display = 'none';
            previewSection.style.display = 'block';
            renderDocPreview(sessionData.attachment_details);
        } else {
            uploadSection.style.display = 'none';
            previewSection.style.display = 'none';
        }
    }

    async function forceCompleteSession() {
        if (!sessionData || sessionData.status !== 'active') return;
        if (!confirm('Force-complete this audit session on behalf of the sales rep?\nThis will move it to "Awaiting Confirmation" status so a document can be uploaded.')) return;

        try {
            const resp = await fetch(API.forceComplete(), {
                method: 'POST',
                headers: { 'Content-Type': 'application/json', 'X-CSRFToken': getCookie('csrftoken') },
                credentials: 'same-origin',
                body: JSON.stringify({ session_key: sessionKey }),
            });
            const result = await resp.json();
            if (!resp.ok) throw new Error(result.error || 'Force-complete failed');
            showAlert('Audit session force-completed. Now upload the confirmation document to close it.', 'success');
            setTimeout(() => loadSessionData(), 800);
        } catch (err) {
            showAlert(`${err.message}`, 'error');
        }
    }

    function setupDropzone() {
        const dz = document.getElementById('uploadDropzone');
        if (!dz) return;
        dz.addEventListener('dragover', e => { e.preventDefault(); dz.classList.add('dragover'); });
        dz.addEventListener('dragleave', () => dz.classList.remove('dragover'));
        dz.addEventListener('drop', e => {
            e.preventDefault();
            dz.classList.remove('dragover');
            if (e.dataTransfer.files.length) {
                document.getElementById('attachmentFile').files = e.dataTransfer.files;
                onAttachmentSelected({ target: { files: e.dataTransfer.files } });
            }
        });
    }

    function onAttachmentSelected(event) {
        const file = event.target.files[0];
        if (!file) return;
        selectedFile = file;
        document.getElementById('uploadFileLabel').textContent = `${file.name} (${(file.size / 1024).toFixed(1)} KB)`;
        document.getElementById('uploadFilename').classList.add('show');
        document.getElementById('uploadSubmitBtn').classList.add('show');
    }

    function clearAttachment() {
        selectedFile = null;
        document.getElementById('attachmentFile').value = '';
        document.getElementById('uploadFilename').classList.remove('show');
        document.getElementById('uploadSubmitBtn').classList.remove('show');
    }

    async function submitAttachment() {
        if (!selectedFile) {
            showAlert('Please select a file first.', 'error');
            return;
        }

        const formData = new FormData();
        formData.append('session_key', sessionKey);
        formData.append('file', selectedFile);

        try {
            document.getElementById('uploadSubmitBtn').disabled = true;
            document.getElementById('uploadSubmitBtn').textContent = '⏳ Uploading...';

            const response = await fetch(API.uploadAttachment(), {
                method: 'POST',
                headers: { 'X-CSRFToken': getCookie('csrftoken') },
                credentials: 'same-origin',
                body: formData,
            });
            const result = await response.json();

            if (!response.ok) {
                throw new Error(result.error || 'Upload failed');
            }

            showAlert('Document uploaded! Session is now closed.', 'success');
            // Reload to reflect new status
            setTimeout(() => loadSessionData(), 800);
        } catch (error) {
            showAlert(`Upload error: ${error.message}`, 'error');
        } finally {
            const btn = document.getElementById('uploadSubmitBtn');
            if (btn) {
                btn.disabled = false;
                btn.textContent = '📤 Upload & Close Session';
            }
        }
    }

    function renderDocPreview(att) {
        const container = document.getElementById('docPreviewContent');
        if (!att) { container.innerHTML = ''; return; }

        const fileUrl = att.download_url; // Served from database via Django view
        const fname = att.original_filename || '';
        const ext = fname.split('.').pop().toLowerCase();
        const isPDF = ext === 'pdf' || att.content_type === 'application/pdf';
        const isImage = ['png','jpg','jpeg','gif','bmp','tiff','webp'].includes(ext) || (att.content_type && att.content_type.startsWith('image/'));

        let previewHTML = '';
        if (isPDF) {
            previewHTML = `<iframe src="${fileUrl}" class="doc-preview-frame" title="Document Preview"></iframe>`;
        } else if (isImage) {
            previewHTML = `<img src="${fileUrl}" alt="Confirmation Document" style="max-width:100%;border-radius:8px;border:1px solid rgba(0,0,0,0.08);">`;
        } else {
            previewHTML = `
                <div style="text-align:center;padding:40px;background:#ffffff;border-radius:8px;border:1px solid rgba(0,0,0,0.08);">
                    <div style="font-size:3em;"></div>
                    <p style="margin-top:10px;color:#666;">Preview not available for this file type.</p>
                </div>
            `;
        }

        const uploadedBy = att.uploaded_by_name || 'Unknown';
        const uploadedAt = att.uploaded_at ? new Date(att.uploaded_at).toLocaleString() : '';

        container.innerHTML = `
            ${previewHTML}
            <div class="doc-meta">
                <strong>${fname}</strong> · Uploaded by ${uploadedBy} on ${uploadedAt}
            </div>
            <a href="${fileUrl}&download=1" class="doc-download-link">Download Document</a>
        `;
    }

    // ── Remarks ────────────────────────────────────────────────────────────

    async function loadAllRemarks() {
        // Load all product-level remarks for this session in one request
        productRemarksMap = {};
        try {
            const resp = await fetch(API.allProductRemarks(), { credentials: 'same-origin' });
            if (resp.ok) {
                const all = await resp.json();
                (Array.isArray(all) ? all : (all.results || [])).forEach(r => {
                    const pid = r.scanned_product;
                    if (!productRemarksMap[pid]) productRemarksMap[pid] = [];
                    productRemarksMap[pid].push(r);
                });
            }
        } catch(e) { /* non-critical */ }

        // Load session-level remarks
        try {
            const resp = await fetch(API.sessionRemarks(), { credentials: 'same-origin' });
            if (resp.ok) sessionRemarksData = await resp.json();
        } catch(e) { /* non-critical */ }

        // Re-render products (to update remark counts) and session remarks list
        renderProducts();
        renderSessionRemarks();
    }

    function _remarkHtml(remark) {
        const isWH = remark.role === 'warehouse';
        const badge = isWH
            ? `<span style="background:#f5f3ff;color:#7c3aed;padding:2px 8px;border-radius:10px;font-size:0.72em;font-weight:700;">Warehouse</span>`
            : `<span style="background:#eff6ff;color:#2563eb;padding:2px 8px;border-radius:10px;font-size:0.72em;font-weight:700;">Sales Rep</span>`;
        const dt = remark.created_at ? new Date(remark.created_at).toLocaleString() : '';
        return `<div style="padding:10px 12px;border-radius:8px;background:${isWH?'#faf5ff':'#f8fafc'};border:1px solid ${isWH?'#ddd6fe':'#e2e8f0'};margin-bottom:8px;">
            <div style="display:flex;align-items:center;gap:8px;margin-bottom:5px;">${badge}
                <span style="font-size:0.75em;color:#94a3b8;">${remark.created_by_name || ''}</span>
                <span style="font-size:0.72em;color:#cbd5e1;margin-left:auto;">${dt}</span>
            </div>
            <div style="font-size:0.88em;color:#1e293b;line-height:1.5;">${remark.remark}</div>
        </div>`;
    }

    function renderSessionRemarks() {
        const list    = document.getElementById('sessionRemarksList');
        const section = document.getElementById('sessionRemarksSection');
        if (!list) return;

        const hasWarehouseReply = sessionRemarksData.some(r => r.role === 'warehouse');
        const totalRemarks = sessionRemarksData.length;

        // Visually highlight the section if warehouse has replied
        if (section) {
            if (hasWarehouseReply) {
                section.style.borderLeft  = '4px solid #7c3aed';
                section.style.paddingLeft = '12px';
                section.style.background  = 'linear-gradient(90deg,#faf5ff 0%,transparent 60%)';
                section.style.borderRadius = '0 8px 8px 0';
            } else if (totalRemarks > 0) {
                section.style.borderLeft  = '4px solid #2563eb';
                section.style.paddingLeft = '12px';
                section.style.background  = 'linear-gradient(90deg,#eff6ff 0%,transparent 60%)';
                section.style.borderRadius = '0 8px 8px 0';
            } else {
                section.style.borderLeft  = '';
                section.style.paddingLeft = '';
                section.style.background  = '';
                section.style.borderRadius = '';
            }
        }

        // Update section-title badge
        const titleEl = section && section.querySelector('.section-title span');
        if (titleEl) {
            if (hasWarehouseReply) {
                titleEl.innerHTML = '💬 Batch Remarks <span style="background:#f5f3ff;color:#7c3aed;border:1px solid #ddd6fe;border-radius:20px;padding:1px 10px;font-size:.75em;font-weight:700;margin-left:8px;">' + totalRemarks + ' remark' + (totalRemarks !== 1 ? 's' : '') + ' · WH replied</span>';
            } else if (totalRemarks > 0) {
                titleEl.innerHTML = '💬 Batch Remarks <span style="background:#eff6ff;color:#2563eb;border:1px solid #bfdbfe;border-radius:20px;padding:1px 10px;font-size:.75em;font-weight:700;margin-left:8px;">' + totalRemarks + ' remark' + (totalRemarks !== 1 ? 's' : '') + '</span>';
            } else {
                titleEl.textContent = '💬 Batch Remarks';
            }
        }

        if (!totalRemarks) {
            list.innerHTML = '<p style="color:#94a3b8;font-size:0.85em;font-style:italic;">No batch remarks yet.</p>';
            return;
        }
        list.innerHTML = sessionRemarksData.map(_remarkHtml).join('');
    }

    // ── Product remark modal ───────────────────────────────────────────────

    function openRemarkModal(productId, serialNumber, gtinCode, materialCode) {
        _activeRemarkProductId = productId;
        _activeRemarkSerial = serialNumber;
        const modal = document.getElementById('productRemarkModal');
        const subtitle = document.getElementById('remarkModalSubtitle');
        if (subtitle) {
            const parts = [];
            if (serialNumber && serialNumber !== '(no serial)') parts.push(`S/N: ${serialNumber}`);
            if (gtinCode) parts.push(`GTIN: ${gtinCode}`);
            if (materialCode) parts.push(`Material: ${materialCode}`);
            subtitle.textContent = parts.join(' · ');
        }
        _renderProductRemarksList(productId);
        modal.style.display = 'block';
        const ta = document.getElementById('newProductRemark');
        if (ta) { ta.value = ''; setTimeout(() => ta.focus(), 80); }
    }

    function closeRemarkModal() {
        document.getElementById('productRemarkModal').style.display = 'none';
        _activeRemarkProductId = null;
    }

    function _renderProductRemarksList(productId) {
        const list = document.getElementById('productRemarksList');
        const remarks = productRemarksMap[productId] || [];
        if (!remarks.length) {
            list.innerHTML = '<p style="color:#94a3b8;font-size:0.85em;font-style:italic;margin-bottom:8px;">No remarks yet.</p>';
            return;
        }
        list.innerHTML = remarks.map(_remarkHtml).join('');
    }

    async function submitProductRemark() {
        if (!_activeRemarkProductId) return;
        const ta = document.getElementById('newProductRemark');
        const text = (ta ? ta.value : '').trim();
        if (!text) return;
        try {
            const resp = await fetch(API.addProductRemark(), {
                method: 'POST',
                headers: { 'Content-Type': 'application/json', 'X-CSRFToken': getCookie('csrftoken') },
                credentials: 'same-origin',
                body: JSON.stringify({ product: _activeRemarkProductId, remark: text }),
            });
            if (!resp.ok) throw new Error('Failed to save remark');
            const newRemark = await resp.json();
            if (!productRemarksMap[_activeRemarkProductId]) productRemarksMap[_activeRemarkProductId] = [];
            productRemarksMap[_activeRemarkProductId].push(newRemark);
            ta.value = '';
            _renderProductRemarksList(_activeRemarkProductId);
            renderProducts();  // refresh remark count badge
            showAlert('Remark saved.', 'success');
        } catch (err) {
            showAlert('Could not save remark: ' + err.message, 'error');
        }
    }

    async function addSessionRemark() {
        const ta = document.getElementById('newSessionRemark');
        const text = (ta ? ta.value : '').trim();
        if (!text) return;
        try {
            const resp = await fetch(API.addSessionRemark(), {
                method: 'POST',
                headers: { 'Content-Type': 'application/json', 'X-CSRFToken': getCookie('csrftoken') },
                credentials: 'same-origin',
                body: JSON.stringify({ session: sessionKey, remark: text }),
            });
            if (!resp.ok) throw new Error('Failed to save remark');
            const newRemark = await resp.json();
            sessionRemarksData.push(newRemark);
            ta.value = '';
            renderSessionRemarks();
            showAlert('Batch remark saved.', 'success');
        } catch (err) {
            showAlert('Could not save remark: ' + err.message, 'error');
        }
    }

    // ── Hold / Resume ──────────────────────────────────────────────────────
    async function holdSession() {
        if (!sessionData) return;
        if (!confirm('Put this session on hold?\nDaily overdue email reminders will stop. You can resume it or close it later.')) return;
        try {
            const resp = await fetch(API.holdSession(), {
                method: 'POST',
                headers: { 'Content-Type': 'application/json', 'X-CSRFToken': getCookie('csrftoken') },
                credentials: 'same-origin',
                body: JSON.stringify({ session_key: sessionKey }),
            });
            const result = await resp.json();
            if (!resp.ok) throw new Error(result.error || 'Hold failed');
            sessionData = result.session;
            renderSessionInfo();
            renderAttachmentUI();
            showAlert('Session put on hold. Daily reminders are paused.', 'success');
        } catch (err) {
            showAlert(`${err.message}`, 'error');
        }
    }

    async function resumeSession() {
        if (!sessionData) return;
        if (!confirm('Resume this session?\nIt will go back to awaiting confirmation and daily reminders will restart from the very next day.')) return;
        try {
            const resp = await fetch(API.resumeSession(), {
                method: 'POST',
                headers: { 'Content-Type': 'application/json', 'X-CSRFToken': getCookie('csrftoken') },
                credentials: 'same-origin',
                body: JSON.stringify({ session_key: sessionKey }),
            });
            const result = await resp.json();
            if (!resp.ok) throw new Error(result.error || 'Resume failed');
            sessionData = result.session;
            renderSessionInfo();
            renderAttachmentUI();
            showAlert('Session resumed and is back in awaiting confirmation.', 'success');
        } catch (err) {
            showAlert(`${err.message}`, 'error');
        }
    }

    // Load on page load
    document.addEventListener('DOMContentLoaded', loadSessionData);