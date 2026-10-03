/* ═══════════════════════════════════════════════════════════════════════
   Sales type master — add and edit the GstTransactionType → GSTR-1 mapping.

   Everything the form needs is already on the row as data- attributes, so
   opening the editor never asks the server for something the page has. After
   a save the page reloads rather than patching the row in place: a row carries
   several derived bits (the section chip, the two behaviour flags, the
   inactive styling) and one of them getting out of step with the database is
   exactly the drift this screen exists to prevent.
   ═══════════════════════════════════════════════════════════════════════ */
(function () {
    'use strict';

    var usage = JSON.parse(document.getElementById('rcStUsage').textContent);
    var group = new Intl.NumberFormat('en-IN');
    var editing = null;          // the row's pk, or null when adding

    function $(id) { return document.getElementById(id); }

    function csrf() {
        var m = document.cookie.match(/(?:^|;\s*)csrftoken=([^;]+)/);
        return m ? decodeURIComponent(m[1]) : '';
    }

    function toast(message, kind) {
        var el = document.createElement('div');
        el.className = 'rc-toast ' + (kind || 'ok');
        el.innerHTML = '<i class="fas fa-circle-check"></i><div></div>';
        el.querySelector('div').textContent = message;
        $('rcToasts').appendChild(el);
        setTimeout(function () { el.remove(); }, 7000);
    }

    // How many stored documents each code carries — the number that says
    // whether editing a row matters or is theoretical.
    document.querySelectorAll('[data-usage]').forEach(function (cell) {
        var n = usage[cell.dataset.usage];
        cell.innerHTML = n ? group.format(n) : '<span class="rc-dash">&mdash;</span>';
    });

    var search = $('rcStSearch');
    if (search) {
        search.addEventListener('input', function () {
            var needle = search.value.trim().toLowerCase();
            document.querySelectorAll('#rcStBody tr[data-code]').forEach(function (row) {
                row.hidden = needle !== '' &&
                    row.textContent.toLowerCase().indexOf(needle) === -1;
            });
        });
    }

    var modal = $('rcStModal');
    if (!modal) { return; }      // read-only for this role

    function close() { modal.hidden = true; editing = null; }

    document.querySelectorAll('[data-st-close]').forEach(function (el) {
        el.addEventListener('click', close);
    });
    modal.addEventListener('click', function (e) { if (e.target === modal) { close(); } });
    document.addEventListener('keydown', function (e) {
        if (e.key === 'Escape' && !modal.hidden) { close(); }
    });

    function open(options) {
        editing = options.pk || null;
        $('rcStTitle').textContent = options.title;
        $('stCode').value = options.code || '';
        $('stCode').disabled = Boolean(options.pk);   // the code is the identity
        $('stDescription').value = options.description || '';
        $('stSection').value = options.section || 'B2B';
        $('stExport').checked = Boolean(options.isExport);
        $('stB2c').checked = Boolean(options.isB2c);
        $('stActive').checked = options.isActive !== false;
        $('stNotes').value = options.notes || '';
        modal.hidden = false;
        (options.pk ? $('stDescription') : $('stCode')).focus();
    }

    $('rcStBody').addEventListener('click', function (event) {
        var button = event.target.closest('[data-edit]');
        if (!button) { return; }
        var row = button.closest('tr');
        open({
            pk: row.dataset.id,
            title: 'Edit ' + row.dataset.code,
            code: row.dataset.code,
            description: row.dataset.description,
            section: row.dataset.section,
            isExport: row.dataset.export === '1',
            isB2c: row.dataset.b2c === '1',
            isActive: row.dataset.active === '1',
            notes: row.dataset.notes
        });
    });

    var addButton = $('rcStAdd');
    if (addButton) {
        addButton.addEventListener('click', function () {
            open({ title: 'Add sales type', section: 'B2B', isActive: true });
        });
    }

    // "Add" beside a code the data carries but the master does not.
    document.querySelectorAll('[data-add]').forEach(function (button) {
        button.addEventListener('click', function () {
            open({
                title: 'Add ' + button.dataset.add,
                code: button.dataset.add,
                section: 'B2B',
                isActive: true
            });
        });
    });

    $('stSave').addEventListener('click', function () {
        var code = $('stCode').value.trim().toUpperCase();
        if (!code) { toast('A transaction-type code is required.', 'bad'); return; }

        var url = editing
            ? '/reconciliation/api/sales-type/' + editing + '/'
            : '/reconciliation/api/sales-type/new/';

        fetch(url, {
            method: 'POST',
            credentials: 'same-origin',
            headers: { 'Content-Type': 'application/json', 'X-CSRFToken': csrf() },
            body: JSON.stringify({
                code: code,
                description: $('stDescription').value,
                gstr1_section: $('stSection').value,
                is_export: $('stExport').checked,
                is_b2c: $('stB2c').checked,
                is_active: $('stActive').checked,
                notes: $('stNotes').value
            })
        }).then(function (r) { return r.json(); }).then(function (data) {
            if (data.ok === false) { throw new Error(data.error); }
            toast(data.message);
            close();
            setTimeout(function () { window.location.reload(); }, 700);
        }).catch(function (err) { toast(err.message || 'Could not save.', 'bad'); });
    });
}());
