/* Show a processing overlay while a slow form submits.
 *
 * Saving a vendor was measured at 3.7ms for the row, 184ms for the portal
 * account and 916ms for the credentials email — and EMAIL_TIMEOUT is 20, so an
 * unreachable mail server held the page for twenty seconds with no sign that
 * anything was happening. The email is off the request thread now, but a save
 * that creates a login and an Excel import that walks a workbook are still
 * work worth showing, and a form with no feedback gets submitted twice.
 *
 * Opt in per form:
 *     <form data-busy-loader="Saving the vendor…" data-busy-note="…">
 *
 * Reuses the robot loader from _ai_loader_assets.html rather than defining a
 * second spinner, so there is one "working" visual across the module.
 */
(function () {
    'use strict';

    function overlayFor(form) {
        var template = document.getElementById('vlxAiLoaderTemplate');
        if (!template || !template.content) { return null; }

        var node = template.content.firstElementChild.cloneNode(true);
        node.classList.add('vlx-ailoader--overlay');

        var message = node.querySelector('.vlx-ailoader__msg');
        if (message) { message.textContent = form.dataset.busyLoader || 'Working…'; }

        var note = node.querySelector('.vlx-ailoader__sub');
        if (note) {
            /* The elapsed counter belongs to the AI loader, which has no idea
               how long a save takes either — but here the work is bounded and
               a ticking clock just makes it feel slower. */
            note.textContent = form.dataset.busyNote || '';
            note.hidden = !note.textContent;
        }
        return node;
    }

    document.addEventListener('submit', function (event) {
        var form = event.target;
        if (!form || !form.dataset || !form.dataset.busyLoader) { return; }

        /* Let validation win. A form the browser rejects never leaves the page,
           so covering it would strand the user behind an overlay with the
           error messages hidden underneath. novalidate forms report true here,
           which is correct — they submit. */
        if (typeof form.checkValidity === 'function' && !form.noValidate && !form.checkValidity()) {
            return;
        }

        var overlay = overlayFor(form);
        if (overlay) {
            /* Mounted inside .vlx: every --vlx-* token is declared on that
               class, not :root, so an overlay appended to <body> renders with
               no colours at all. */
            var host = document.querySelector('.vlx');
            if (host) {
                host.appendChild(overlay);
            } else {
                overlay.classList.add('vlx');
                document.body.appendChild(overlay);
            }
            document.body.style.overflow = 'hidden';
        }

        /* Disable the submit buttons too: the overlay stops stray clicks, but
           Enter in a text field does not go through it. */
        Array.prototype.forEach.call(
            form.querySelectorAll('button[type="submit"], input[type="submit"]'),
            function (button) { button.disabled = true; }
        );
    });

    /* Back/forward can restore this page from cache mid-submit with the
        overlay still in the DOM. Without this it would sit there for good. */
    window.addEventListener('pageshow', function (event) {
        if (!event.persisted) { return; }
        var stale = document.querySelector('.vlx-ailoader--overlay');
        if (stale && stale.parentNode) { stale.parentNode.removeChild(stale); }
        document.body.style.overflow = '';
        Array.prototype.forEach.call(
            document.querySelectorAll('button[type="submit"][disabled], input[type="submit"][disabled]'),
            function (button) { button.disabled = false; }
        );
    });
}());
