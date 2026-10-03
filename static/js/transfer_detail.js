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
    let transferData = null;

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

    function showAlert(msg, type='info') {
        const div = document.createElement('div');
        div.className = `alert alert-${type}`;
        div.innerHTML = `${msg} <span style="float:right;cursor:pointer;" onclick="this.parentElement.remove()">×</span>`;
        document.getElementById('alertContainer').prepend(div);
        setTimeout(() => div.remove(), 5000);
    }

    async function loadTransfer() {
        try {
            const resp = await fetch(`/product_scanning/api/transfers/${sessionKey}/`, {
                headers: { 'Content-Type': 'application/json' },
                credentials: 'same-origin'
            });
            if (!resp.ok) throw new Error('Failed to load');
            transferData = await resp.json();
            renderTransfer();
        } catch (err) {
            showAlert('Failed to load transfer details', 'error');
        }
    }

    function renderTransfer() {
        const t = transferData;

        // Route
        document.getElementById('fromHospital').textContent = t.from_hospital_details?.name || 'Unknown';
        document.getElementById('toHospital').textContent = t.to_hospital_details?.name || 'Unknown';

        // Status badge
        const badge = document.getElementById('statusBadge');
        const statusColors = { active: 'status-active', completed: 'status-completed', closed: 'status-closed' };
        const statusLabels = { active: 'ONGOING', completed: 'PENDING CONFIRM', closed: 'COMPLETED' };
        badge.className = `status-badge ${statusColors[t.status] || ''}`;
        badge.textContent = statusLabels[t.status] || t.status.toUpperCase();

        // Info
        document.getElementById('transferId').textContent = t.transfer_id;
        document.getElementById('sessionKey').textContent = t.session_key;
        document.getElementById('initiatedBy').textContent = t.initiated_by_details?.first_name 
            ? `${t.initiated_by_details.first_name} ${t.initiated_by_details.last_name}`
            : t.initiated_by_details?.username || 'Unknown';
        document.getElementById('startedAt').textContent = new Date(t.started_at).toLocaleString();
        document.getElementById('completedAt').textContent = t.completed_at ? new Date(t.completed_at).toLocaleString() : '-';
        document.getElementById('totalProducts').textContent = t.transferred_products?.length || 0;

        // Products
        renderProducts(t.transferred_products || []);

        // Attachments
        renderAttachments(t.attachments || []);

        // Actions
        if (t.status !== 'closed') {
            document.getElementById('actionButtons').style.display = 'flex';
        } else {
            document.getElementById('uploadSection').style.display = 'none';
        }
    }

    function renderProducts(products) {
        const tbody = document.getElementById('productsBody');
        if (products.length === 0) {
            tbody.innerHTML = '<tr><td colspan="6" class="empty-state">No products transferred</td></tr>';
            return;
        }

        tbody.innerHTML = products.map((p, i) => {
            const isExpired = p.is_expired;
            return `
            <tr>
                <td>${p.serial_number || (i + 1)}</td>
                <td>
                    ${p.product_name || 'Unknown'}
                    ${isExpired ? '<span class="expiry-badge">EXPIRED</span>' : ''}
                </td>
                <td>${p.gtin_code || '-'}</td>
                <td>${p.batch_number || '-'}</td>
                <td class="${isExpired ? 'expiry-warning' : ''}">${p.expiry_date || '-'}</td>
                <td>${p.scanned_at ? new Date(p.scanned_at).toLocaleString() : '-'}</td>
            </tr>`;
        }).join('');

        initDataTable('transferProductsTable', { order: [[5, 'desc']] });
    }

    function renderAttachments(attachments) {
        const container = document.getElementById('attachmentsList');
        if (attachments.length === 0) {
            container.innerHTML = '<div class="empty-state">No attachments yet</div>';
            return;
        }

        container.innerHTML = attachments.map(a => `
            <div class="attachment-item">
                <div class="attachment-info">
                    <div class="attachment-icon">📄</div>
                    <div>
                        <div class="attachment-name">${a.file_name}</div>
                        <div class="attachment-meta">Uploaded by ${a.uploaded_by_name || 'Unknown'} on ${new Date(a.uploaded_at).toLocaleDateString()}</div>
                    </div>
                </div>
                <a href="${a.download_url}" class="btn-download">⬇️ Download</a>
            </div>
        `).join('');
    }

    async function uploadFile() {
        const input = document.getElementById('fileInput');
        const file = input.files[0];
        if (!file) return;

        const formData = new FormData();
        formData.append('file', file);
        formData.append('session_key', sessionKey);

        try {
            const resp = await fetch('/product_scanning/api/transfers/upload_attachment/', {
                method: 'POST',
                headers: { 'X-CSRFToken': getCookie('csrftoken') },
                credentials: 'same-origin',
                body: formData
            });
            if (!resp.ok) throw new Error('Upload failed');
            showAlert('File uploaded successfully', 'success');
            loadTransfer();
        } catch (err) {
            showAlert('Failed to upload file', 'error');
        }
        input.value = '';
    }

    async function confirmTransfer() {
        if (!confirm('Confirm and close this transfer?')) return;

        try {
            // Use close endpoint or update status
            const resp = await fetch(`/product_scanning/api/transfers/${sessionKey}/`, {
                method: 'PATCH',
                headers: { 'Content-Type': 'application/json', 'X-CSRFToken': getCookie('csrftoken') },
                credentials: 'same-origin',
                body: JSON.stringify({ status: 'closed' })
            });
            if (!resp.ok) throw new Error('Failed');
            showAlert('Transfer confirmed and closed', 'success');
            setTimeout(() => loadTransfer(), 1000);
        } catch (err) {
            showAlert('Failed to confirm transfer', 'error');
        }
    }

    async function terminateTransfer() {
        if (!confirm('Terminate this transfer? This action cannot be undone.')) return;

        try {
            await fetch('/product_scanning/api/transfers/terminate_transfer/', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json', 'X-CSRFToken': getCookie('csrftoken') },
                credentials: 'same-origin',
                body: JSON.stringify({ session_key: sessionKey })
            });
            showAlert('Transfer terminated', 'success');
            setTimeout(() => {
                window.location.href = '{% url "product_scanning:transfer_dashboard" %}';
            }, 1500);
        } catch (err) {
            showAlert('Failed to terminate', 'error');
        }
    }

    function exportProducts() {
        if (!transferData || !transferData.transferred_products?.length) {
            showAlert('No products to export', 'error');
            return;
        }

        const products = transferData.transferred_products;
        const headers = ['Serial Number', 'Product Name', 'GTIN', 'Batch Number', 'Material Code', 'Expiry Date', 'Is Expired', 'Scanned At'];
        const rows = products.map(p => [
            p.serial_number || '',
            p.product_name || '',
            p.gtin_code || '',
            p.batch_number || '',
            p.material_code || '',
            p.expiry_date || '',
            p.is_expired ? 'YES' : 'NO',
            p.scanned_at || ''
        ]);

        let csv = headers.join(',') + '\n';
        rows.forEach(row => {
            csv += row.map(cell => `"${String(cell).replace(/"/g, '""')}"`).join(',') + '\n';
        });

        const blob = new Blob([csv], { type: 'text/csv' });
        const url = URL.createObjectURL(blob);
        const a = document.createElement('a');
        a.href = url;
        a.download = `transfer_${transferData.transfer_id}.csv`;
        a.click();
        URL.revokeObjectURL(url);
    }

    document.addEventListener('DOMContentLoaded', loadTransfer);