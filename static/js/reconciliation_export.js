const API = {
    base: '/product_scanning/api',
    listBatches: () => `${API.base}/batches/list_batches/`,
    sessionByBatch: (batchId) => `${API.base}/sessions/by_batch/?batch_id=${batchId}`,
    hospitals: () => `${API.base}/hospitals/list_hospitals/`,
};

let allBatches = [];
let filteredBatches = [];

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
    alertDiv.innerHTML = `
        ${message}
        <span style="float:right; cursor:pointer; font-size:1.2em;" onclick="this.parentElement.remove()">&times;</span>
    `;
    document.getElementById('alertContainer').prepend(alertDiv);
    setTimeout(() => {
        if (alertDiv.parentElement) alertDiv.remove();
    }, 5000);
}

async function loadBatches() {
    try {
        let hospitals = [];

        // Load hospitals first
        try {
            const hospitalsResponse = await fetch(API.hospitals());
            if (hospitalsResponse.ok) {
                const hospitalsData = await hospitalsResponse.json();
                hospitals = Array.isArray(hospitalsData) ? hospitalsData : (hospitalsData.results || []);

            }
        } catch (err) {
            console.error('Error loading hospitals:', err);
        }

        // Load batches
        const response = await fetch(API.listBatches());
        if (!response.ok) throw new Error('Failed to load batches');

        const data = await response.json();
        allBatches = (Array.isArray(data) ? data : (data.results || [])).map(batch => {
            const hospital = hospitals.find(h => h.id === batch.hospital) ||
                            hospitals.find(h => h.id?.toString() === batch.hospital?.toString()) ||
                            { id: batch.hospital, name: 'Unknown Hospital', sap_code: 'N/A' };

            return {
                ...batch,
                hospital: hospital,
                scanning_sessions: batch.scanning_sessions || []
            };
        });

        filteredBatches = [...allBatches];

        updateStats();
        renderBatches();
    } catch (error) {
        console.error('Load error:', error);
        showAlert(`Error loading batches: ${error.message}`, 'error');
        document.getElementById('batchesContainer').innerHTML = `
            <div class="alert alert-error">
                Failed to load batches. <a href="javascript:loadBatches()">Try again</a>
            </div>
        `;
    }
}

function updateStats() {
    let totalProducts = 0;
    let completedSessions = 0;

    allBatches.forEach(batch => {
        if (batch && batch.scanning_sessions && Array.isArray(batch.scanning_sessions)) {
            batch.scanning_sessions.forEach(session => {
                if (session && session.products && Array.isArray(session.products)) {
                    totalProducts += session.products.length;
                }
                if (session && session.status === 'completed') {
                    completedSessions++;
                }
            });
        }
    });

    document.getElementById('totalBatches').textContent = allBatches.length;
    document.getElementById('totalProducts').textContent = totalProducts;
    document.getElementById('completedSessions').textContent = completedSessions;
}

function renderBatches() {
    const container = document.getElementById('batchesContainer');

    if (filteredBatches.length === 0) {
        container.innerHTML = `
            <div class="empty-state">
                <div class="empty-icon">📭</div>
                <div class="empty-title">No Batches Found</div>
                <p>Try adjusting your filters or start a new scanning session</p>
            </div>
        `;
        return;
    }

    container.innerHTML = filteredBatches.map(batch => {
        const totalProd = (batch.scanning_sessions || []).reduce((sum, s) => {
            return sum + (s.products ? s.products.length : 0);
        }, 0);

        return `
            <div class="batch-card">
                <div class="batch-header">
                    <div class="batch-info">
                        <div class="batch-id">📦 ${batch.batch_id || 'N/A'}</div>
                        <div class="batch-meta">
                            <div class="meta-item">
                                <span class="meta-label">Hospital:</span> ${batch.hospital ? batch.hospital.name : 'Unknown'}
                            </div>
                            <div class="meta-item">
                                <span class="meta-label">Created:</span> ${batch.created_at ? new Date(batch.created_at).toLocaleDateString() : 'N/A'}
                            </div>
                            <div class="meta-item">
                                <span class="meta-label">Products:</span> ${totalProd}
                            </div>
                        </div>
                    </div>
                    <div class="batch-status">
                        <div class="status-badge status-${batch.scanning_sessions && batch.scanning_sessions[0] ? batch.scanning_sessions[0].status : 'active'}">
                            ${batch.scanning_sessions && batch.scanning_sessions[0] ? batch.scanning_sessions[0].status.toUpperCase() : 'ACTIVE'}
                        </div>
                        <button class="btn-export btn-sm" onclick="exportBatch('${batch.id}', '${batch.batch_id}')">
                            📥 Export CSV
                        </button>
                    </div>
                </div>
                <table class="products-table" id="products-${batch.id}">
                    <thead>
                        <tr>
                            <th>Serial Number</th>
                            <th>GTIN Code</th>
                            <th>Product Name</th>
                            <th>Expiry Date</th>
                            <th>Material Code</th>
                            <th>Quantity</th>
                        </tr>
                    </thead>
                    <tbody id="tbody-${batch.id}">
                    </tbody>
                </table>
                <div style="text-align: center; margin-top: 10px;">
                    <button class="expand-toggle" onclick="toggleProductsTable('${batch.id}')">▼ View Products</button>
                </div>
            </div>
        `;
    }).join('');

    // Load products for each batch
    filteredBatches.forEach(batch => {
        loadBatchProducts(batch);
    });
}

async function loadBatchProducts(batch) {
    try {
        if (batch.scanning_sessions && batch.scanning_sessions.length > 0) {
            const products = batch.scanning_sessions[0].products || [];
            renderBatchProducts(batch.id, products);
        }
    } catch (error) {
        console.error(`Error loading products for batch ${batch.id}:`, error);
    }
}

function renderBatchProducts(batchId, products) {
    const tbody = document.getElementById(`tbody-${batchId}`);
    if (!tbody) return;

    if (!products || products.length === 0) {
        tbody.innerHTML = '<tr><td colspan="6" style="text-align:center; color:#999;">No products in this batch</td></tr>';
        return;
    }

    tbody.innerHTML = products.map(product => {
        const expiryDate = product.expiry_date ? new Date(product.expiry_date) : null;
        const today = new Date();
        let expiryClass = 'expiry-ok';
        let expiryText = product.expiry_date || 'N/A';

        if (expiryDate) {
            if (expiryDate < today) {
                expiryClass = 'expiry-expired';
                expiryText = expiryDate.toLocaleDateString() + ' (EXPIRED)';
            } else if ((expiryDate - today) / (1000 * 60 * 60 * 24) < 30) {
                expiryClass = 'expiry-warning';
                expiryText = expiryDate.toLocaleDateString() + ' (30 days)';
            } else {
                expiryText = expiryDate.toLocaleDateString();
            }
        }

        return `
            <tr>
                <td><span class="serial-code">${product.serial_number}</span></td>
                <td><span class="gtin-code">${product.gtin_code || 'N/A'}</span></td>
                <td>${product.product_name || 'N/A'}</td>
                <td class="${expiryClass}">${expiryText}</td>
                <td>${product.material_code || 'N/A'}</td>
                <td>${product.quantity || 1}</td>
            </tr>
        `;
    }).join('');
}

function toggleProductsTable(batchId) {
    const table = document.getElementById(`products-${batchId}`);
    const button = event.target;

    table.classList.toggle('expanded');
    button.textContent = table.classList.contains('expanded') ? '▲ Hide Products' : '▼ View Products';
}

async function exportBatch(batchId, batchName) {
    try {
        const batch = allBatches.find(b => b.id.toString() === batchId.toString());
        if (!batch || !batch.scanning_sessions || !batch.scanning_sessions[0]) {
            showAlert('No data to export', 'error');
            return;
        }

        const products = (batch.scanning_sessions && batch.scanning_sessions[0] && batch.scanning_sessions[0].products)
            ? batch.scanning_sessions[0].products
            : [];
        const hospitalName = batch.hospital ? batch.hospital.name : 'Unknown';
        let csv = 'Batch ID,Hospital,Created Date,Serial Number,GTIN Code,Product Name,Expiry Date,Material Code,Quantity\n';

        products.forEach(product => {
            csv += `"${batch.batch_id}","${hospitalName}","${batch.created_at ? new Date(batch.created_at).toLocaleDateString() : ''}","${product.serial_number}","${product.gtin_code || ''}","${product.product_name || ''}","${product.expiry_date || ''}","${product.material_code || ''}","${product.quantity || 1}"\n`;
        });

        const blob = new Blob([csv], { type: 'text/csv' });
        const url = window.URL.createObjectURL(blob);
        const a = document.createElement('a');
        a.href = url;
        a.download = `SAP_${batchName}_${new Date().toLocaleDateString()}.csv`;
        document.body.appendChild(a);
        a.click();
        window.URL.revokeObjectURL(url);
        document.body.removeChild(a);

        showAlert(`✅ Exported ${products.length} products from batch ${batchName}`, 'info');
    } catch (error) {
        showAlert(`Error exporting batch: ${error.message}`, 'error');
    }
}

function applyFilters() {
    const hospitalId = document.getElementById('filterHospital').value;
    const batchId = document.getElementById('filterBatchId').value.toLowerCase();
    const status = document.getElementById('filterStatus').value;
    const dateFrom = document.getElementById('filterDateFrom').value;
    const dateTo = document.getElementById('filterDateTo').value;

    filteredBatches = allBatches.filter(batch => {
        const hospital = batch.hospital || { id: null };
        const hospitalMatch = !hospitalId || hospital.id?.toString() === hospitalId;
        const batchMatch = !batchId || (batch.batch_id && batch.batch_id.toLowerCase().includes(batchId));
        const statusMatch = !status || (batch.scanning_sessions && batch.scanning_sessions[0] && batch.scanning_sessions[0].status === status);
        const dateFromMatch = !dateFrom || (batch.created_at && new Date(batch.created_at) >= new Date(dateFrom));
        const dateToMatch = !dateTo || (batch.created_at && new Date(batch.created_at) <= new Date(dateTo));

        return hospitalMatch && batchMatch && statusMatch && dateFromMatch && dateToMatch;
    });

    updateStats();
    renderBatches();
    showAlert(`Found ${filteredBatches.length} batches`, 'info');
}

function clearFilters() {
    document.getElementById('filterHospital').value = '';
    document.getElementById('filterBatchId').value = '';
    document.getElementById('filterStatus').value = '';
    document.getElementById('filterDateFrom').value = '';
    document.getElementById('filterDateTo').value = '';

    filteredBatches = [...allBatches];
    updateStats();
    renderBatches();
}

document.addEventListener('DOMContentLoaded', loadBatches);
