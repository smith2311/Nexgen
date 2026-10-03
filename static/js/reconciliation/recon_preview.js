/* ═══════════════════════════════════════════════════════════════════════
   Preview modal — shared by the records export, the filing summary export
   and the full 204-column view of a staged upload.

   Deliberately tiny and standalone: the dashboard and the billing-type master
   are server-rendered pages with no page script of their own, and the records
   screen has a large one. Making them all depend on recon.js to show a preview
   would mean loading the whole records screen's behaviour on pages that have
   no records screen.

   The markup lives in templates/reconciliation/_preview_modal.html. The table
   inside is built server-side from the exporter's own palette — this file
   never styles a cell, it only puts the block on screen.
   ═══════════════════════════════════════════════════════════════════════ */
(function () {
    'use strict';

    var modal = document.getElementById('rcPvModal');
    if (!modal) { return; }

    function $(id) { return document.getElementById(id); }

    var group = new Intl.NumberFormat('en-IN');
    var token = 0;

    function close() {
        modal.hidden = true;
        document.body.style.overflow = '';
        // Dropped rather than left in place: a 40 × 210 table is a lot of DOM
        // to keep alive behind a closed modal, and the next preview replaces
        // it anyway.
        $('rcPvSheet').innerHTML = '';
    }

    Array.prototype.forEach.call(
        modal.querySelectorAll('[data-pv-close]'),
        function (el) { el.addEventListener('click', close); }
    );

    modal.addEventListener('click', function (event) {
        if (event.target === modal) { close(); }
    });

    document.addEventListener('keydown', function (event) {
        if (event.key === 'Escape' && !modal.hidden) { close(); }
    });

    /**
     * open({ url, title, downloadUrl })
     *
     * `url` returns { ok, preview: { html, shown, total, columns, truncated,
     * filename, describe } }. `downloadUrl` is optional — the staged-file
     * preview has nothing to download, because nothing has been written yet.
     */
    function open(options) {
        var mine = ++token;

        $('rcPvTitle').textContent = options.title || 'Preview';
        $('rcPvSub').textContent = '';
        $('rcPvNote').textContent = '';
        $('rcPvSheet').innerHTML = '';
        $('rcPvLoading').hidden = false;

        var download = $('rcPvDownload');
        download.hidden = !options.downloadUrl;
        if (options.downloadUrl) { download.href = options.downloadUrl; }

        modal.hidden = false;
        document.body.style.overflow = 'hidden';

        fetch(options.url, { credentials: 'same-origin' })
            .then(function (response) {
                return response.json().catch(function () {
                    throw new Error('The server returned an unexpected response (' +
                                    response.status + ').');
                }).then(function (data) {
                    if (!response.ok || data.ok === false) {
                        throw new Error(data.error || 'Could not build the preview.');
                    }
                    return data;
                });
            })
            .then(function (data) {
                if (mine !== token) { return; }   // a newer preview won
                var preview = data.preview;
                $('rcPvLoading').hidden = true;
                $('rcPvSheet').innerHTML = preview.html;

                var bits = [];
                if (preview.filename) { bits.push(preview.filename); }
                if (preview.columns) {
                    bits.push(group.format(preview.columns) + ' columns');
                }
                if (preview.describe && preview.describe.length) {
                    bits.push(preview.describe.join(' · '));
                }
                $('rcPvSub').textContent = bits.join('  ·  ');

                $('rcPvNote').textContent = preview.truncated
                    ? 'Showing the first ' + group.format(preview.shown) + ' of ' +
                      group.format(preview.total) + ' rows. The file contains all of them.'
                    : 'Showing all ' + group.format(preview.total) + ' row(s).';
            })
            .catch(function (err) {
                if (mine !== token) { return; }
                $('rcPvLoading').hidden = true;
                $('rcPvSheet').innerHTML =
                    '<div class="rc-preview-error"><i class="fas fa-circle-exclamation"></i> ' +
                    String(err.message).replace(/[&<>"']/g, function (ch) {
                        return { '&': '&amp;', '<': '&lt;', '>': '&gt;',
                                 '"': '&quot;', "'": '&#39;' }[ch];
                    }) + '</div>';
                $('rcPvDownload').hidden = true;
            });
    }

    window.ReconPreview = { open: open, close: close };
}());
