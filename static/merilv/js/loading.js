(function () {
    'use strict';

    var overlay = document.getElementById('mvLoadingOverlay');
    if (!overlay) { return; }

    var titleEl = overlay.querySelector('[data-mv-loading-title]');
    var messageEl = overlay.querySelector('[data-mv-loading-message]');
    var active = false;

    function setCopy(form) {
        if (!titleEl || !messageEl) { return; }
        titleEl.textContent = form.getAttribute('data-mv-loading-title') || 'Working…';
        messageEl.textContent = form.getAttribute('data-mv-loading-message') ||
            'Please wait while the request is processed.';
    }

    function show(form) {
        if (active) { return; }
        active = true;
        setCopy(form);
        overlay.hidden = false;
        overlay.classList.add('is-open');
        document.documentElement.classList.add('mv-has-loading');
    }

    document.querySelectorAll('form[data-mv-loading]').forEach(function (form) {
        if (form.dataset.mvLoadingBound === '1') { return; }
        form.dataset.mvLoadingBound = '1';
        form.addEventListener('submit', function (event) {
            if (event.defaultPrevented || form.hasAttribute('data-mv-async')) { return; }
            show(form);
        });
    });

    window.mvLoadingOverlay = {
        show: show,
        hide: function () {
            active = false;
            overlay.classList.remove('is-open');
            overlay.hidden = true;
            document.documentElement.classList.remove('mv-has-loading');
        },
    };
})();
