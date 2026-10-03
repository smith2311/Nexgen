/* Copy an RFI reference number to the clipboard.
 *
 * Deliberately standalone rather than part of vendorlink-components.js. The
 * reference appears on screens that do not load the component library at all
 * (the request list, the request detail page, Historical Data), and on Bid
 * Analytics it sits inside .vlx-cd-card-head — a click-to-expand header
 * carrying user-select:none, where the text cannot even be selected by hand.
 * Bundling the handler with the library meant it silently did nothing on three
 * of the four screens that render the button.
 *
 * No dependency on window.VL, and the listener is delegated from the document,
 * so load order cannot disable it and references inside collapsed cards are
 * covered too.
 *
 * vendorlink/test_refcopy.py asserts that every template rendering a
 * .vlx-refcopy button also loads this file.
 */
(function () {
    'use strict';

    function flash(button, ok) {
        button.classList.add(ok ? 'is-copied' : 'is-failed');
        var icon = button.querySelector('i');
        var previous = icon ? icon.className : null;
        if (icon) { icon.className = ok ? 'fas fa-check' : 'fas fa-xmark'; }
        window.setTimeout(function () {
            button.classList.remove('is-copied', 'is-failed');
            if (icon && previous) { icon.className = previous; }
        }, 1400);
    }

    /* execCommand is the fallback, not the primary path: navigator.clipboard
       needs a secure context, which http://localhost is but a plain-http LAN
       host is not — and this app is reached both ways. */
    function legacyCopy(value) {
        var scratch = document.createElement('textarea');
        scratch.value = value;
        scratch.setAttribute('readonly', '');
        scratch.style.position = 'fixed';
        scratch.style.top = '-1000px';
        scratch.style.opacity = '0';
        document.body.appendChild(scratch);
        scratch.select();
        var ok = false;
        try { ok = document.execCommand('copy'); } catch (err) { ok = false; }
        document.body.removeChild(scratch);
        return ok;
    }

    function copy(button) {
        var value = (button.dataset.ref || button.textContent || '').trim();
        if (!value) { return; }

        if (navigator.clipboard && window.isSecureContext) {
            navigator.clipboard.writeText(value).then(function () {
                flash(button, true);
            }).catch(function () {
                flash(button, legacyCopy(value));
            });
        } else {
            flash(button, legacyCopy(value));
        }
    }

    document.addEventListener('click', function (event) {
        var button = event.target.closest ? event.target.closest('.vlx-refcopy') : null;
        if (!button) { return; }
        /* The Bid Analytics card underneath this button toggles on click, so
           the event must not reach it. */
        event.preventDefault();
        event.stopPropagation();
        copy(button);
    });
}());
