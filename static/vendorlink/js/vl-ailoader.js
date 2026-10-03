/* Show the "robot writing a report" loader while an OpenAI call runs.
 *
 * The ?ai=1 report links are ordinary navigations whose server render blocks
 * on the model for several seconds. A link cannot report progress, so before
 * this the click produced nothing at all — the old page just sat there, which
 * reads as a dead button and gets clicked again.
 *
 * Standalone, with no dependency on window.VL: the report screens do not all
 * load the component library, and a loader that only works on some of them is
 * worse than none (you learn not to trust it).
 *
 * Anything with data-ai-loader is wired automatically:
 *     <a href="…?ai=1" data-ai-loader="Generating key points…">
 *
 * VLAiLoader.show(message) / .hide() are exposed for the fetch-driven cases
 * (MerilGPT) that need to control it around a promise.
 */
(function () {
    'use strict';

    var TEMPLATE_ID = 'vlxAiLoaderTemplate';
    var overlay = null;
    var timer = null;
    var startedAt = 0;

    /* The markup lives in a <template> rendered by _ai_loader.html, so the
       scene is defined once in the template rather than duplicated as a string
       in here — the two drifting apart is how a loader ends up unstyled. */
    function build(message) {
        var template = document.getElementById(TEMPLATE_ID);
        if (!template || !template.content) { return null; }

        var node = template.content.firstElementChild.cloneNode(true);
        node.classList.add('vlx-ailoader--overlay');
        if (message) {
            var line = node.querySelector('.vlx-ailoader__msg');
            if (line) { line.textContent = message; }
        }
        return node;
    }

    function tick() {
        if (!overlay) { return; }
        var readout = overlay.querySelector('[data-ai-elapsed]');
        if (!readout) { return; }
        var seconds = Math.round((Date.now() - startedAt) / 1000);
        /* Only after a few seconds: a counter that appears instantly makes a
           fast answer feel slow. Elapsed time is the only real progress figure
           available — the API reports none — so there is no percentage here. */
        if (seconds < 3) { return; }
        readout.hidden = false;
        readout.textContent = ' · ' + seconds + 's elapsed';
    }

    function show(message) {
        if (overlay) { return overlay; }
        overlay = build(message);
        if (!overlay) { return null; }

        /* Mount inside the .vlx wrapper, not on <body>.
           Every --vlx-* token is declared on the .vlx class rather than :root
           (deliberately, so the VendorLink palette cannot leak into Meril-V or
           Material Rework). Appended to <body> the overlay sits outside that
           scope, every var() resolves to nothing, and the robot renders as
           black silhouettes. Falling back to <body> + the class keeps it
           working on any page without the wrapper. */
        var host = document.querySelector('.vlx');
        if (host) {
            host.appendChild(overlay);
        } else {
            overlay.classList.add('vlx');
            document.body.appendChild(overlay);
        }
        /* Stops the page behind scrolling under the overlay. */
        document.body.style.overflow = 'hidden';
        startedAt = Date.now();
        timer = window.setInterval(tick, 1000);
        return overlay;
    }

    function hide() {
        if (timer) { window.clearInterval(timer); timer = null; }
        if (overlay && overlay.parentNode) { overlay.parentNode.removeChild(overlay); }
        overlay = null;
        document.body.style.overflow = '';
    }

    document.addEventListener('click', function (event) {
        var trigger = event.target.closest ? event.target.closest('[data-ai-loader]') : null;
        if (!trigger) { return; }
        /* Let modified clicks through untouched — a new tab keeps this page,
           so covering it would strand the user behind a loader for a
           navigation that never happens here. */
        if (event.metaKey || event.ctrlKey || event.shiftKey || event.button !== 0) { return; }
        show(trigger.dataset.aiLoader || undefined);
    });

    /* Back/forward out of a blocking render restores this page from cache with
       the overlay still in the DOM; without this it would sit there forever. */
    window.addEventListener('pageshow', function (event) {
        if (event.persisted) { hide(); }
    });

    window.VLAiLoader = { show: show, hide: hide };
}());
