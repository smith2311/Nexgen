let appState = {
        sessionKey: null,
        transferId: null,
        fromHospitalId: null,
        fromHospitalName: '',
        toHospitalId: null,
        toHospitalName: '',
        products: [],
        isScanning: false,
        _processing: false
    };
    let html5QrCode = null;

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

    async function loadHospitals() {
        try {
            const resp = await fetch('/product_scanning/api/hospitals/', {
                headers: { 'Content-Type': 'application/json' },
                credentials: 'same-origin'
            });
            const data = await resp.json();
            const hospitals = data.results || data;
            
            const fromSelect = document.getElementById('fromHospital');
            const toSelect = document.getElementById('toHospital');

            hospitals.forEach(h => {
                const opt1 = new Option(`${h.name} (${h.sap_code})`, h.id);
                const opt2 = new Option(`${h.name} (${h.sap_code})`, h.id);
                opt1.dataset.name = h.name;
                opt2.dataset.name = h.name;
                fromSelect.add(opt1);
                toSelect.add(opt2);
            });
        } catch (err) {
            showAlert('Failed to load hospitals', 'error');
            console.error(err);
        }
    }

    function updateStartButton() {
        const from = document.getElementById('fromHospital').value;
        const to = document.getElementById('toHospital').value;
        const btn = document.getElementById('startTransferBtn');
        btn.disabled = !(from && to && from !== to);
    }

    async function startTransfer() {
        const fromSelect = document.getElementById('fromHospital');
        const toSelect = document.getElementById('toHospital');

        if (fromSelect.value === toSelect.value) {
            showAlert('Source and destination must be different', 'error');
            return;
        }

        appState.fromHospitalId = fromSelect.value;
        appState.fromHospitalName = fromSelect.options[fromSelect.selectedIndex].dataset.name;
        appState.toHospitalId = toSelect.value;
        appState.toHospitalName = toSelect.options[toSelect.selectedIndex].dataset.name;

        const btn = document.getElementById('startTransferBtn');
        btn.disabled = true;
        btn.innerHTML = '<span class="spinner"></span> Starting...';

        try {
            const resp = await fetch('/product_scanning/api/transfers/start_transfer/', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json', 'X-CSRFToken': getCookie('csrftoken') },
                credentials: 'same-origin',
                body: JSON.stringify({
                    from_hospital_id: appState.fromHospitalId,
                    to_hospital_id: appState.toHospitalId
                })
            });
            const data = await resp.json();

            if (!resp.ok) throw new Error(data.error || 'Failed to start transfer');

            appState.sessionKey = data.session_key;
            appState.transferId = data.transfer_id;

            document.getElementById('setupForm').style.display = 'none';
            document.getElementById('scannerWrapper').classList.add('active');
            document.getElementById('routeDisplay').textContent = `${appState.fromHospitalName} → ${appState.toHospitalName}`;
            document.getElementById('transferIdDisplay').textContent = `Transfer ID: ${appState.transferId}`;

            initScanner();
        } catch (err) {
            showAlert(err.message, 'error');
            btn.disabled = false;
            btn.innerHTML = '🚀 Start Transfer';
        }
    }

    function initScanner() {
        html5QrCode = new Html5Qrcode('reader');
        html5QrCode.start(
            { facingMode: 'environment' },
            { fps: 10, qrbox: { width: 250, height: 250 } },
            onScanSuccess,
            () => {}
        ).then(() => {
            appState.isScanning = true;
        }).catch(err => {
            console.warn('Camera not available:', err);
            showAlert('Camera not available. Use manual entry.', 'warning');
        });
    }

    function onScanSuccess(decodedText) {
        if (appState._processing) return;
        document.getElementById('manualBarcode').value = decodedText;
        scanProduct();
    }

    async function scanProduct() {
        const barcode = document.getElementById('manualBarcode').value.trim();
        if (!barcode || appState._processing) return;

        appState._processing = true;

        try {
            const resp = await fetch('/product_scanning/api/transfers/scan_product/', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json', 'X-CSRFToken': getCookie('csrftoken') },
                credentials: 'same-origin',
                body: JSON.stringify({
                    session_key: appState.sessionKey,
                    barcode: barcode
                })
            });
            const data = await resp.json();

            if (!resp.ok) {
                showAlert(data.error || 'Scan failed', 'error');
            } else {
                const product = data.product;
                appState.products.unshift(product);
                renderProducts();
                showAlert(`Added: ${product.product_name || product.gtin_code}`, 'success');

                if (data.warnings && data.warnings.length) {
                    data.warnings.forEach(w => showAlert(w, 'warning'));
                }
            }
        } catch (err) {
            showAlert('Network error', 'error');
        }

        document.getElementById('manualBarcode').value = '';
        appState._processing = false;
    }

    function renderProducts() {
        const container = document.getElementById('productList');
        const count = appState.products.length;
        document.getElementById('productCount').textContent = `${count} product${count !== 1 ? 's' : ''}`;
        document.getElementById('listCount').textContent = count;

        if (count === 0) {
            container.innerHTML = '<div style="text-align:center;color:#9ca3af;padding:20px;">No products scanned yet</div>';
            return;
        }

        container.innerHTML = appState.products.map((p, i) => {
            const isExpired = p.is_expired;
            return `
            <div class="product-item ${isExpired ? 'expiry-warning' : ''}">
                <div class="product-details">
                    <div class="product-name">
                        ${p.product_name || 'Unknown'}
                        ${isExpired ? '<span class="expiry-badge">EXPIRED</span>' : ''}
                    </div>
                    <div class="product-meta">
                        GTIN: ${p.gtin_code || 'N/A'} | Lot: ${p.batch_number || 'N/A'} | Mat: ${p.material_code || 'N/A'} | Exp: ${p.expiry_date || 'N/A'}
                    </div>
                </div>
                <button class="btn-remove" onclick="removeProduct(${p.id})">🗑️</button>
            </div>`;
        }).join('');
    }

    async function removeProduct(productId) {
        try {
            await fetch(`/product_scanning/api/transfers/products/${productId}/`, {
                method: 'DELETE',
                headers: { 'X-CSRFToken': getCookie('csrftoken') },
                credentials: 'same-origin'
            });
            appState.products = appState.products.filter(p => p.id !== productId);
            renderProducts();
        } catch (err) {
            showAlert('Failed to remove', 'error');
        }
    }

    async function completeTransfer() {
        if (appState.products.length === 0) {
            showAlert('Scan at least one product', 'error');
            return;
        }

        if (!confirm(`Complete transfer of ${appState.products.length} product(s)?`)) return;

        try {
            await fetch('/product_scanning/api/transfers/complete_transfer/', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json', 'X-CSRFToken': getCookie('csrftoken') },
                credentials: 'same-origin',
                body: JSON.stringify({ session_key: appState.sessionKey })
            });
            showAlert('Transfer completed!', 'success');
            setTimeout(() => {
                window.location.href = '{% url "product_scanning:transfer_dashboard" %}';
            }, 1500);
        } catch (err) {
            showAlert('Failed to complete transfer', 'error');
        }
    }

    async function cancelTransfer() {
        if (!confirm('Cancel this transfer? All scanned products will be lost.')) return;

        try {
            await fetch('/product_scanning/api/transfers/terminate_transfer/', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json', 'X-CSRFToken': getCookie('csrftoken') },
                credentials: 'same-origin',
                body: JSON.stringify({ session_key: appState.sessionKey })
            });
            window.location.href = '{% url "product_scanning:transfer_dashboard" %}';
        } catch (err) {
            showAlert('Failed to cancel', 'error');
        }
    }

    document.getElementById('fromHospital').addEventListener('change', updateStartButton);
    document.getElementById('toHospital').addEventListener('change', updateStartButton);

    document.addEventListener('DOMContentLoaded', () => {
        loadHospitals();
        renderProducts();
    });