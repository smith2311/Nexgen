/* ============================================================================
 * Meril-V — hover-flyout submenu for the NexGen sidebar
 *
 * Attaches to the EXISTING sidebar link without editing the shared platform
 * template: it finds the anchor by href and injects a chevron plus a panel that
 * is appended to <body> (so no ancestor overflow/transform can clip it).
 *
 * Menu contents and tier gating come from a JSON descriptor rendered by Django
 * (templates/merilv/_shell/flyout_menu.html) so the server decides what the
 * user may see — items the tier cannot reach are never sent, not disabled.
 *
 * Generic by design: any sidebar anchor carrying a data-flyout attribute with
 * the same descriptor shape gets the same behaviour, so sibling modules can
 * adopt it without touching this file.
 * ========================================================================= */
(function () {
    'use strict';

    var OPEN_DELAY = 150;    // dwell before opening, so a passing pointer doesn't
    var CLOSE_GRACE = 250;   // grace so row -> panel diagonal travel survives
    var NARROW = 900;        // below this the panel becomes an off-canvas drawer

    /* Lucide-style icons, drawn locally: the design export ships no icon assets
     * and fetching from a CDN is not allowed here. 24x24, stroke 1.5, round. */
    var ICONS = {
        'scan':        '<rect x="3" y="6" width="18" height="12" rx="1"/><path d="M7 9v6M10 9v6M13 9v6M17 9v6"/>',
        'clipboard':   '<rect x="6" y="4" width="12" height="16" rx="2"/><path d="M9 4h6M9 12l2 2 4-4"/>',
        'transfer':    '<path d="M4 8h13l-3-3M20 16H7l3 3"/>',
        'wand':        '<path d="M5 19 19 5M14 5h5v5"/><path d="M6 9h3M7.5 7.5v3"/>',
        'barcode':     '<path d="M4 5v14M7 5v14M10 5v10M13 5v14M17 5v10M20 5v14"/>',
        'file':        '<path d="M14 3H7a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h10a2 2 0 0 0 2-2V8z"/><path d="M14 3v5h5M9 13h6M9 17h4"/>',
        'truck':       '<rect x="2" y="7" width="12" height="9" rx="1"/><path d="M14 10h4l3 3v3h-7z"/><circle cx="6.5" cy="18" r="1.6"/><circle cx="17.5" cy="18" r="1.6"/>',
        'history':     '<circle cx="12" cy="12" r="8"/><path d="M12 8v4l3 2"/>',
        'list':        '<path d="M8 6h12M8 12h12M8 18h12M4 6h.01M4 12h.01M4 18h.01"/>',
        'chart':       '<path d="M4 20V4"/><path d="M4 20h16"/><rect x="8" y="12" width="3" height="5"/><rect x="14" y="8" width="3" height="9"/>',
        'activity':    '<path d="M3 12h4l3-7 4 14 3-7h4"/>',
        'package':     '<path d="M12 3 4 7v10l8 4 8-4V7z"/><path d="M4 7l8 4 8-4M12 11v10"/>',
        'users':       '<circle cx="9" cy="9" r="3"/><path d="M3 20a6 6 0 0 1 12 0"/><path d="M16 7a3 3 0 0 1 0 6M17 20a6 6 0 0 0-2-4.3"/>',
        'box':         '<rect x="4" y="6" width="16" height="13" rx="1"/><path d="M4 10h16M12 10v9"/>',
        'building':    '<rect x="5" y="3" width="14" height="18" rx="1"/><path d="M9 7h2M13 7h2M9 11h2M13 11h2M9 15h2M13 15h2"/>',
        'layers':      '<path d="M12 3 3 8l9 5 9-5z"/><path d="M3 13l9 5 9-5"/>',
        'key':         '<circle cx="8" cy="14" r="4"/><path d="M11 11 20 2M17 5l2 2M15 7l2 2"/>',
        'scroll':      '<rect x="4" y="4" width="16" height="16" rx="2"/><path d="M8 9h8M8 13h8M8 17h5"/>',
        'monitor':     '<rect x="3" y="4" width="18" height="12" rx="1"/><path d="M8 20h8M12 16v4"/>',
        'chevron':     '<path d="M9 6l6 6-6 6"/>',
        'menu':        '<path d="M4 7h16M4 12h16M4 17h16"/>',
        'close':       '<path d="M6 6l12 12M18 6 6 18"/>'
    };

    function svg(name, size) {
        var d = ICONS[name] || ICONS.list;
        return '<svg viewBox="0 0 24 24" width="' + (size || 16) + '" height="' + (size || 16) +
            '" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" ' +
            'stroke-linejoin="round" aria-hidden="true" focusable="false">' + d + '</svg>';
    }

    function isNarrow() { return window.innerWidth < NARROW; }

    function Flyout(anchor, descriptor) {
        this.anchor = anchor;
        this.descriptor = descriptor;
        this.open = false;
        this.openTimer = null;
        this.closeTimer = null;
        this.build();
        this.bind();
    }

    Flyout.prototype.build = function () {
        var self = this;

        /* Chevron on the sidebar row. margin-left:auto in CSS pushes it right. */
        this.chevron = document.createElement('span');
        this.chevron.className = 'mv-flyout-chevron';
        this.chevron.innerHTML = svg(isNarrow() ? 'menu' : 'chevron', 16);
        this.anchor.appendChild(this.chevron);

        /* Scrim, drawer mode only (CSS hides it on wide viewports). */
        this.scrim = document.createElement('div');
        this.scrim.className = 'mv-flyout__scrim';
        document.body.appendChild(this.scrim);

        /* Panel. Appended to <body> so no ancestor can clip or stack over it. */
        this.panel = document.createElement('div');
        this.panel.className = 'mv-flyout';
        this.panel.setAttribute('role', 'menu');
        this.panel.setAttribute('aria-label', this.descriptor.label || 'Submenu');

        var html = '<button type="button" class="mv-flyout__close" aria-label="Close menu">' +
            svg('close', 16) + '<span>Close</span></button>';

        var here = window.location.pathname;
        (this.descriptor.groups || []).forEach(function (group) {
            if (!group.items || !group.items.length) { return; }
            if (group.label) {
                html += '<div class="mv-flyout__group-label">' + esc(group.label) + '</div>';
            }
            group.items.forEach(function (item) {
                /* Longest-prefix match marks the current page, so a detail URL
                 * still highlights its section. */
                var current = here === item.url ||
                    (item.url.length > 12 && here.indexOf(item.url) === 0);
                html += '<a class="mv-flyout__item' + (current ? ' is-current' : '') +
                    '" role="menuitem" href="' + esc(item.url) + '"' +
                    (current ? ' aria-current="page"' : '') + '>' +
                    svg(item.icon, 16) + '<span>' + esc(item.label) + '</span></a>';
            });
        });
        this.panel.innerHTML = html;
        document.body.appendChild(this.panel);

        this.items = Array.prototype.slice.call(
            this.panel.querySelectorAll('.mv-flyout__item'));
        this.closeBtn = this.panel.querySelector('.mv-flyout__close');
        if (this.closeBtn) {
            this.closeBtn.addEventListener('click', function () { self.hide(true); });
        }

        /* The sidebar row keeps its selected style when we are on a module page;
         * the platform CSS owns that, we only add the aria hook. */
        this.anchor.setAttribute('aria-haspopup', 'true');
        this.anchor.setAttribute('aria-expanded', 'false');
    };

    Flyout.prototype.bind = function () {
        var self = this;

        function scheduleOpen() {
            clearTimeout(self.closeTimer);
            if (self.open || isNarrow()) { return; }
            clearTimeout(self.openTimer);
            self.openTimer = setTimeout(function () { self.show(); }, OPEN_DELAY);
        }
        function scheduleClose() {
            clearTimeout(self.openTimer);
            clearTimeout(self.closeTimer);
            self.closeTimer = setTimeout(function () { self.hide(); }, CLOSE_GRACE);
        }

        this.anchor.addEventListener('mouseenter', scheduleOpen);
        this.anchor.addEventListener('mouseleave', scheduleClose);
        this.panel.addEventListener('mouseenter', function () { clearTimeout(self.closeTimer); });
        this.panel.addEventListener('mouseleave', scheduleClose);

        /* On a narrow viewport the chevron is a menu button: tap toggles the
         * drawer instead of following the link. */
        this.chevron.addEventListener('click', function (e) {
            if (!isNarrow()) { return; }
            e.preventDefault();
            e.stopPropagation();
            self.open ? self.hide(true) : self.show();
        });
        this.scrim.addEventListener('click', function () { self.hide(true); });

        /* Click-outside and Esc close immediately, no grace period. */
        document.addEventListener('click', function (e) {
            if (!self.open) { return; }
            if (self.panel.contains(e.target) || self.anchor.contains(e.target)) { return; }
            self.hide(true);
        });
        document.addEventListener('keydown', function (e) {
            if (e.key === 'Escape' && self.open) { self.hide(true); self.anchor.focus(); }
        });

        /* Keyboard model: Right opens and enters, Left closes and returns,
         * Up/Down cycle items (group captions are not focusable so they are
         * skipped for free), Enter follows, Tab closes and moves on. */
        this.anchor.addEventListener('keydown', function (e) {
            if (e.key === 'ArrowRight') {
                e.preventDefault();
                self.show();
                if (self.items[0]) { self.items[0].focus(); }
            }
        });
        this.panel.addEventListener('keydown', function (e) {
            var i = self.items.indexOf(document.activeElement);
            if (e.key === 'ArrowDown') {
                e.preventDefault();
                self.items[(i + 1 + self.items.length) % self.items.length].focus();
            } else if (e.key === 'ArrowUp') {
                e.preventDefault();
                self.items[(i - 1 + self.items.length) % self.items.length].focus();
            } else if (e.key === 'ArrowLeft') {
                e.preventDefault();
                self.hide(true);
                self.anchor.focus();
            } else if (e.key === 'Tab') {
                self.hide(true);
            }
        });

        window.addEventListener('resize', function () {
            self.chevron.innerHTML = svg(isNarrow() ? 'menu' : 'chevron', 16);
            if (self.open) {
                self.scrim.classList.toggle('is-open', isNarrow());
                self.position();
            }
        });
        window.addEventListener('scroll', function () {
            if (self.open && !isNarrow()) { self.position(); }
        }, { passive: true });
    };

    /* Top-align to the sidebar row, but never let the panel run off the bottom
     * or above the top of the viewport. */
    Flyout.prototype.position = function () {
        if (isNarrow()) { return; }              // drawer mode is CSS-positioned
        var r = this.anchor.getBoundingClientRect();
        this.panel.style.left = Math.round(r.right) + 'px';

        var h = this.panel.offsetHeight;
        var top = r.top;
        var overflow = (top + h) - (window.innerHeight - 8);
        if (overflow > 0) { top = Math.max(8, top - overflow); }
        this.panel.style.top = Math.round(top) + 'px';
    };

    Flyout.prototype.show = function () {
        clearTimeout(this.closeTimer);
        if (this.open) { return; }
        this.open = true;
        this.panel.classList.add('is-open');
        /* The scrim is the drawer's backdrop. On a wide viewport the flyout is
         * a lightweight hover panel beside the sidebar, not a modal — dimming
         * the whole page for it would be wrong, so the scrim only opens in
         * narrow/drawer mode. */
        if (isNarrow()) { this.scrim.classList.add('is-open'); }
        this.chevron.classList.add('is-open');
        this.anchor.setAttribute('aria-expanded', 'true');
        this.position();
    };

    Flyout.prototype.hide = function (immediate) {
        clearTimeout(this.openTimer);
        clearTimeout(this.closeTimer);
        if (!this.open) { return; }
        this.open = false;
        this.panel.classList.remove('is-open');
        this.scrim.classList.remove('is-open');
        this.chevron.classList.remove('is-open');
        this.anchor.setAttribute('aria-expanded', 'false');
        if (immediate) { /* class removal alone is immediate enough */ }
    };

    function esc(s) {
        return String(s === undefined || s === null ? '' : s)
            .replace(/&/g, '&amp;').replace(/</g, '&lt;')
            .replace(/>/g, '&gt;').replace(/"/g, '&quot;');
    }

    function findAnchor(url) {
        /* The sidebar link may or may not carry a trailing slash, and may be an
         * absolute path or a full URL. */
        var sel = 'a[href="' + url + '"], a[href="' + url.replace(/\/$/, '') + '"]';
        return document.querySelector(sel);
    }

    function init() {
        var mounted = [];

        /* 1. Descriptor rendered by Django for this module (tier-gated). */
        var node = document.getElementById('mv-flyout-data');
        if (node) {
            try {
                var d = JSON.parse(node.textContent);
                var a = findAnchor(d.anchor || '/meril-v/');
                if (a && !a.dataset.mvFlyoutMounted) {
                    a.dataset.mvFlyoutMounted = '1';
                    mounted.push(new Flyout(a, d));
                }
            } catch (err) {
                /* A malformed descriptor must not take the sidebar down with it. */
                if (window.console) { console.warn('[merilv] flyout descriptor invalid', err); }
            }
        }

        /* 2. Generic hook so any sibling module can opt in with an attribute. */
        Array.prototype.forEach.call(
            document.querySelectorAll('a[data-flyout]'), function (a) {
                if (a.dataset.mvFlyoutMounted) { return; }
                try {
                    var d = JSON.parse(a.getAttribute('data-flyout'));
                    a.dataset.mvFlyoutMounted = '1';
                    mounted.push(new Flyout(a, d));
                } catch (err) { /* ignore this one link only */ }
            });

        window.mvFlyouts = mounted;
    }

    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', init);
    } else {
        init();
    }
})();
