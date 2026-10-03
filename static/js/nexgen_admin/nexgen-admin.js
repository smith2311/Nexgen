/* ==========================================================================
   NexGen Admin — behaviour layer
   --------------------------------------------------------------------------
   Progressive enhancement only. Every feature here degrades to plain Django
   admin if the script fails to load: the CSS carries the styling, this file
   adds stagger indices, the dashboard filter, and per-app hue routing.
   ========================================================================== */

'use strict';

(function () {
    var reduceMotion = window.matchMedia('(prefers-reduced-motion: reduce)').matches;

    /* ----------------------------------------------------------------------
       Stagger indices — CSS animates on --nx-i, we just number the elements.
       ---------------------------------------------------------------------- */
    function stagger(selector, cap) {
        var nodes = document.querySelectorAll(selector);
        var limit = cap || 40;
        for (var i = 0; i < nodes.length; i++) {
            nodes[i].style.setProperty('--nx-i', String(Math.min(i, limit)));
        }
    }

    /* ----------------------------------------------------------------------
       Hue routing — the dashboard shows the whole spectrum; hovering a card
       previews that app's hue in the header aurora and page wash. Inside an
       app the `app-<label>` body class already pins the hue, so this only
       runs on the dashboard.

       The hue itself lives in CSS (the app→hue map in nexgen-admin.css), so
       we read the resolved value off the card rather than duplicating the
       map here. A new app only ever needs one CSS line.
       ---------------------------------------------------------------------- */
    function initHuePreview() {
        var body = document.body;
        if (!body.classList.contains('dashboard')) { return; }

        var cards = document.querySelectorAll('.nx-card');
        if (!cards.length) { return; }

        var restore = null;

        function release() {
            window.clearTimeout(restore);
            restore = window.setTimeout(function () {
                body.style.removeProperty('--nx-h');
            }, 260);
        }

        cards.forEach(function (card) {
            var hue = window.getComputedStyle(card).getPropertyValue('--nx-h').trim();
            if (!hue) { return; }

            function preview() {
                window.clearTimeout(restore);
                body.style.setProperty('--nx-h', hue);
            }

            card.addEventListener('mouseenter', preview);
            card.addEventListener('focusin', preview);
            card.addEventListener('mouseleave', release);
            card.addEventListener('focusout', release);
        });
    }

    /* ----------------------------------------------------------------------
       Dashboard filter — narrows the grid to matching apps and models.
       Matches app name, app label, and model names.
       ---------------------------------------------------------------------- */
    function initGridFilter() {
        var input = document.getElementById('nx-grid-filter');
        var grid = document.getElementById('nx-grid');
        if (!input || !grid) { return; }

        var cards = Array.prototype.slice.call(grid.querySelectorAll('.nx-card'));
        var empty = document.getElementById('nx-grid-empty');

        var index = cards.map(function (card) {
            return {
                card: card,
                haystack: (card.getAttribute('data-search') || '').toLowerCase(),
                rows: Array.prototype.slice.call(card.querySelectorAll('.nx-model')).map(function (row) {
                    return { row: row, text: (row.textContent || '').trim().toLowerCase() };
                })
            };
        });

        function apply() {
            var q = input.value.trim().toLowerCase();
            var visible = 0;

            index.forEach(function (entry) {
                if (!q) {
                    entry.card.hidden = false;
                    entry.rows.forEach(function (r) { r.row.hidden = false; });
                    visible++;
                    return;
                }

                var appHit = entry.haystack.indexOf(q) !== -1;
                var rowHits = 0;

                entry.rows.forEach(function (r) {
                    var hit = appHit || r.text.indexOf(q) !== -1;
                    r.row.hidden = !hit;
                    if (hit) { rowHits++; }
                });

                var show = appHit || rowHits > 0;
                entry.card.hidden = !show;
                if (show) { visible++; }
            });

            if (empty) { empty.hidden = visible !== 0; }
        }

        input.addEventListener('input', apply);
        input.addEventListener('keydown', function (event) {
            if (event.key === 'Escape') {
                input.value = '';
                apply();
            }
        });

        // "/" focuses the filter, the way search works elsewhere in the platform.
        document.addEventListener('keydown', function (event) {
            if (event.key !== '/' || event.ctrlKey || event.metaKey || event.altKey) { return; }
            var el = document.activeElement;
            var tag = el ? el.tagName : '';
            if (tag === 'INPUT' || tag === 'TEXTAREA' || tag === 'SELECT' || (el && el.isContentEditable)) { return; }
            event.preventDefault();
            input.focus();
            input.select();
        });
    }

    /* ----------------------------------------------------------------------
       Reveal cards as they scroll into view on long dashboards, so the
       stagger keeps working below the fold instead of firing all at once.
       ---------------------------------------------------------------------- */
    function initScrollReveal() {
        if (reduceMotion || !('IntersectionObserver' in window)) { return; }

        var late = Array.prototype.slice.call(document.querySelectorAll('.nx-card'))
            .filter(function (card) { return card.getBoundingClientRect().top > window.innerHeight; });

        if (!late.length) { return; }

        late.forEach(function (card) { card.style.animationPlayState = 'paused'; });

        var observer = new IntersectionObserver(function (entries) {
            entries.forEach(function (entry) {
                if (!entry.isIntersecting) { return; }
                entry.target.style.animationPlayState = 'running';
                observer.unobserve(entry.target);
            });
        }, { rootMargin: '0px 0px -8% 0px' });

        late.forEach(function (card) { observer.observe(card); });
    }

    /* ----------------------------------------------------------------------
       Auto-dismiss success messages; errors and warnings stay put because
       they carry information the user still needs.
       ---------------------------------------------------------------------- */
    function initMessages() {
        document.querySelectorAll('.messagelist li.success').forEach(function (item) {
            window.setTimeout(function () {
                item.style.transition = 'opacity 320ms ease, transform 320ms ease, margin 320ms ease, padding 320ms ease, max-height 320ms ease';
                item.style.overflow = 'hidden';
                item.style.maxHeight = item.offsetHeight + 'px';
                requestAnimationFrame(function () {
                    item.style.opacity = '0';
                    item.style.transform = 'translateY(-6px)';
                    item.style.maxHeight = '0';
                    item.style.marginBottom = '0';
                    item.style.paddingTop = '0';
                    item.style.paddingBottom = '0';
                });
                window.setTimeout(function () { item.remove(); }, 360);
            }, 6000);
        });
    }

    function init() {
        stagger('.nx-card');
        stagger('#changelist tbody tr', 30);
        stagger('.actionlist li', 12);
        stagger('#content-main fieldset.module', 12);

        initHuePreview();
        initGridFilter();
        initScrollReveal();
        initMessages();
    }

    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', init);
    } else {
        init();
    }
})();
