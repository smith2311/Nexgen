/* Replace window.confirm() with an in-page dialog.
 *
 * The native one renders as "localhost:7000 says…", which looks like a browser
 * warning rather than part of the product, cannot carry the action's own
 * styling, and gives a destructive action the same neutral OK button as a
 * harmless one.
 *
 * Drop-in: swap
 *     onclick="return confirm('Award this RFI to X?')"
 * for
 *     data-confirm="Award this RFI to X?"
 *     data-confirm-action="Award & notify"        (optional button label)
 *     data-confirm-tone="danger"                  (optional, for destructive)
 *
 * Works on links, submit buttons and forms. Standalone, with no dependency on
 * window.VL, because these buttons live on screens that do not all load the
 * component library.
 *
 * Load vl-confirm.css alongside it. The styling is in that file rather than in
 * vendorlink-components.css precisely because three of the six screens using
 * this do not load the component library -- there the dialog rendered as plain
 * text in the page flow, still correct and completely unstyled.
 */
(function () {
    'use strict';

    var scrim = null;
    var dialog = null;
    var lastFocus = null;

    function close() {
        if (scrim && scrim.parentNode) { scrim.parentNode.removeChild(scrim); }
        if (dialog && dialog.parentNode) { dialog.parentNode.removeChild(dialog); }
        scrim = dialog = null;
        document.body.style.overflow = '';
        /* Put focus back where it was, or the next Tab starts from the top of
           the document. */
        if (lastFocus && lastFocus.focus) { lastFocus.focus(); }
        lastFocus = null;
    }

    function open(options, onConfirm) {
        close();
        lastFocus = document.activeElement;

        var danger = options.tone === 'danger';

        scrim = document.createElement('div');
        scrim.className = 'vl-confirm__scrim';
        scrim.addEventListener('click', close);

        dialog = document.createElement('div');
        dialog.className = 'vl-confirm';
        dialog.setAttribute('role', 'dialog');
        dialog.setAttribute('aria-modal', 'true');

        var title = danger ? 'Please confirm' : 'Confirm';
        dialog.innerHTML =
            '<div class="vl-confirm__head' + (danger ? ' is-danger' : '') + '">'
          +   '<span class="vl-confirm__ic">' + (danger ? '&#9888;' : '&#63;') + '</span>'
          +   '<h2 class="vl-confirm__title"></h2>'
          + '</div>'
          + '<div class="vl-confirm__body"><p class="vl-confirm__msg"></p></div>'
          + '<div class="vl-confirm__foot">'
          +   '<button type="button" class="vl-confirm__btn vl-confirm__cancel">Cancel</button>'
          +   '<button type="button" class="vl-confirm__btn vl-confirm__ok'
          +     (danger ? ' is-danger' : '') + '"></button>'
          + '</div>';

        /* textContent, never innerHTML, for the caller's strings: these come
           from template data such as a vendor name, and building them into
           markup would make any apostrophe or angle bracket in a company name
           an injection point. */
        dialog.querySelector('.vl-confirm__title').textContent = title;
        dialog.querySelector('.vl-confirm__msg').textContent = options.message || 'Are you sure?';
        dialog.querySelector('.vl-confirm__ok').textContent = options.action || 'Continue';

        dialog.querySelector('.vl-confirm__cancel').addEventListener('click', close);
        dialog.querySelector('.vl-confirm__ok').addEventListener('click', function () {
            close();
            onConfirm();
        });

        /* Preferring a VendorLink container over <body> so the dialog picks up
           whichever token family the page declares -- --vlx-* on .vlx,
           --vl-* on .vl. vl-confirm.css names a literal fallback for every
           colour, so <body> is a correct outcome and not a broken one; this
           only makes the dialog match the screen it opened on. */
        var host = document.querySelector('.vlx')
                || document.querySelector('.vl')
                || document.querySelector('.vl-app')
                || document.body;
        host.appendChild(scrim);
        host.appendChild(dialog);
        document.body.style.overflow = 'hidden';

        var ok = dialog.querySelector('.vl-confirm__ok');
        if (ok) { ok.focus(); }
    }

    document.addEventListener('keydown', function (event) {
        if (event.key === 'Escape' && dialog) { close(); }
    });

    function optionsFrom(element) {
        return {
            message: element.dataset.confirm,
            action: element.dataset.confirmAction,
            tone: element.dataset.confirmTone,
        };
    }

    document.addEventListener('click', function (event) {
        var element = event.target.closest ? event.target.closest('[data-confirm]') : null;
        if (!element || element.tagName === 'FORM') { return; }
        /* Set just before the replay below, so the second pass falls through. */
        if (element.dataset.vlConfirmed === '1') { return; }

        event.preventDefault();
        event.stopPropagation();

        open(optionsFrom(element), function () {
            element.dataset.vlConfirmed = '1';
            element.click();
            delete element.dataset.vlConfirmed;
        });
    }, true);

    document.addEventListener('submit', function (event) {
        var form = event.target;
        if (!form || !form.dataset || !form.dataset.confirm) { return; }
        if (form.dataset.vlConfirmed === '1') { return; }

        event.preventDefault();
        open(optionsFrom(form), function () {
            form.dataset.vlConfirmed = '1';
            /* requestSubmit, not submit(): submit() skips submit handlers, and
               the busy-loader in vl-busy.js is one of them. */
            if (typeof form.requestSubmit === 'function') {
                form.requestSubmit();
            } else {
                form.submit();
            }
        });
    }, true);

    window.VLConfirm = { open: open, close: close };
}());
