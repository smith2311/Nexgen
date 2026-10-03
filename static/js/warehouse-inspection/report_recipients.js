(function () {
    'use strict';

    const URLS = window.WAREHOUSE_INSPECTION_REPORT_RECIPIENT_URLS || {};

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
            "'": '&#39;',
        }[char]));
    }

    function showMessage(text, variant) {
        const el = document.getElementById('wiRrMessage');
        if (!el) return;
        el.textContent = text;
        el.className = `wi-rr-message ${variant}`;
    }

    function renderTable(recipients) {
        const container = document.getElementById('wiRrTableContainer');
        if (!container) return;

        if (!recipients.length) {
            container.innerHTML = '<div class="wi-rr-empty">No report recipients yet. Add one above.</div>';
            return;
        }

        const rows = recipients.map(recipient => `
            <tr data-id="${recipient.id}">
                <td>${escapeHtml(recipient.name) || '—'}</td>
                <td>${escapeHtml(recipient.email)}</td>
                <td>${escapeHtml(recipient.created_by) || '—'}</td>
                <td>${escapeHtml(new Date(recipient.created_at).toLocaleDateString())}</td>
                <td>
                    <button type="button" class="wi-rr-btn-remove" data-remove-id="${recipient.id}">
                        <i class="fas fa-trash"></i> Remove
                    </button>
                </td>
            </tr>
        `).join('');

        container.innerHTML = `
            <table class="wi-rr-table">
                <thead>
                    <tr>
                        <th>Name</th>
                        <th>Email</th>
                        <th>Added by</th>
                        <th>Date added</th>
                        <th></th>
                    </tr>
                </thead>
                <tbody>${rows}</tbody>
            </table>
        `;

        container.querySelectorAll('[data-remove-id]').forEach(button => {
            button.addEventListener('click', () => removeRecipient(button.dataset.removeId));
        });
    }

    async function loadRecipients() {
        try {
            const response = await fetch(URLS.list);
            const data = await response.json();
            if (!response.ok) {
                showMessage(data.error || 'Failed to load recipients.', 'error');
                return;
            }
            renderTable(data.recipients || []);
        } catch (err) {
            showMessage('Failed to load recipients.', 'error');
        }
    }

    async function addRecipient(event) {
        event.preventDefault();
        const emailInput = document.getElementById('wiRrEmail');
        const nameInput = document.getElementById('wiRrName');
        const email = emailInput.value.trim();
        const name = nameInput.value.trim();
        if (!email) return;

        try {
            const response = await fetch(URLS.add, {
                method: 'POST',
                headers: {
                    'Content-Type': 'application/json',
                    'X-CSRFToken': getCSRFToken(),
                },
                body: JSON.stringify({ email, name }),
            });
            const data = await response.json();
            if (!response.ok || !data.success) {
                showMessage(data.error || 'Failed to add recipient.', 'error');
                return;
            }
            showMessage(data.message, 'success');
            emailInput.value = '';
            nameInput.value = '';
            loadRecipients();
        } catch (err) {
            showMessage('Failed to add recipient.', 'error');
        }
    }

    async function removeRecipient(id) {
        if (!confirm('Remove this recipient? They will stop receiving report emails.')) return;

        try {
            const response = await fetch(URLS.remove, {
                method: 'POST',
                headers: {
                    'Content-Type': 'application/json',
                    'X-CSRFToken': getCSRFToken(),
                },
                body: JSON.stringify({ id }),
            });
            const data = await response.json();
            if (!response.ok || !data.success) {
                showMessage(data.error || 'Failed to remove recipient.', 'error');
                return;
            }
            showMessage('Recipient removed.', 'success');
            loadRecipients();
        } catch (err) {
            showMessage('Failed to remove recipient.', 'error');
        }
    }

    document.addEventListener('DOMContentLoaded', () => {
        const form = document.getElementById('wiRrAddForm');
        if (form) form.addEventListener('submit', addRecipient);
        loadRecipients();
    });
})();
