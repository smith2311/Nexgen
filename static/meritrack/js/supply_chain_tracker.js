const API = '/meritrack/api';
const headers = {'X-CSRFToken': csrfToken, 'Content-Type': 'application/json'};

const eventTypeColors = {
    manufactured:'gray', shipped_to_warehouse:'blue', received_at_warehouse:'blue',
    shipped_to_hospital:'blue', received_at_hospital:'blue', stored_in_department:'purple',
    used_in_surgery:'green', returned_to_warehouse:'yellow', returned_to_manufacturer:'yellow',
    expired:'red', destroyed:'red', transferred:'blue', recalled:'red',
    quality_hold:'yellow', released:'green'
};

async function searchProduct() {
    const query = document.getElementById('searchInput').value.trim();
    if (!query) return;

    const resultsEl = document.getElementById('results');
    resultsEl.innerHTML = '<div style="text-align:center;padding:40px;color:var(--mt-gray-400)"><i class="fas fa-spinner fa-spin"></i> Searching...</div>';

    try {
        // Try product journey first
        let res = await fetch(`${API}/supply-chain/product-journey/${encodeURIComponent(query)}/`, {headers});
        let data = await res.json();

        if (!data.events || !data.events.length) {
            // Try batch journey
            res = await fetch(`${API}/supply-chain/batch-journey/${encodeURIComponent(query)}/`, {headers});
            data = await res.json();
        }

        if (!data.events || !data.events.length) {
            resultsEl.innerHTML = `<div class="empty-state">
                <i class="fas fa-search"></i>
                <div style="font-size:1.1em;font-weight:600">No results found</div>
                <div>No supply chain events found for "${query}"</div>
            </div>`;
            return;
        }

        renderJourney(data, resultsEl);
    } catch(e) {
        resultsEl.innerHTML = `<div style="text-align:center;padding:40px;color:var(--mt-danger)"><i class="fas fa-exclamation-circle"></i> Error searching. Please try again.</div>`;
    }
}

function renderJourney(data, container) {
    const productHtml = data.product ? `
        <div class="mt-card">
            <h3><i class="fas fa-box" style="color:var(--mt-primary)"></i> Product Information</h3>
            <div class="product-info">
                <div class="info-item"><div class="label">Product</div><div class="value">${data.product.product_name || 'N/A'}</div></div>
                <div class="info-item"><div class="label">Material Code</div><div class="value">${data.product.material_code || 'N/A'}</div></div>
                <div class="info-item"><div class="label">Batch</div><div class="value">${data.product.batch_number || 'N/A'}</div></div>
                <div class="info-item"><div class="label">Serial #</div><div class="value">${data.product.serial_number || 'N/A'}</div></div>
                <div class="info-item"><div class="label">Current Status</div><div class="value"><span class="badge badge-${data.product.status === 'on_shelf' ? 'green' : data.product.status === 'used' ? 'blue' : 'yellow'}">${data.product.status || 'N/A'}</span></div></div>
                <div class="info-item"><div class="label">Location</div><div class="value">${data.product.location || 'N/A'}</div></div>
            </div>
        </div>` : '';

    const timelineHtml = data.events.map(e => {
        const cls = eventTypeColors[e.event_type] || '';
        return `<div class="timeline-event ${e.event_type}">
            <div class="event-header">
                <span class="event-type">${(e.event_type||'').replace(/_/g,' ').replace(/\b\w/g, l => l.toUpperCase())}</span>
                <span class="event-time">${e.event_date ? new Date(e.event_date).toLocaleString() : '-'}</span>
            </div>
            <div class="event-detail">${e.description || ''}</div>
            ${e.location ? `<div class="event-location"><i class="fas fa-map-marker-alt"></i> ${e.location}</div>` : ''}
            ${e.performed_by_name ? `<div class="event-location"><i class="fas fa-user"></i> ${e.performed_by_name}</div>` : ''}
            ${e.sap_document_number ? `<div class="event-location"><i class="fas fa-file-alt"></i> SAP Doc: ${e.sap_document_number}</div>` : ''}
        </div>`;
    }).join('');

    container.innerHTML = `
        ${productHtml}
        <div class="mt-card">
            <h3><i class="fas fa-history" style="color:var(--mt-secondary)"></i> Journey Timeline
                <span class="badge badge-blue" style="margin-left:auto">${data.events.length} events</span>
            </h3>
            <div class="timeline">${timelineHtml}</div>
        </div>`;
}

// Auto-search if URL has query param
document.addEventListener('DOMContentLoaded', () => {
    const params = new URLSearchParams(window.location.search);
    const q = params.get('q') || params.get('serial');
    if (q) {
        document.getElementById('searchInput').value = q;
        searchProduct();
    }
});
