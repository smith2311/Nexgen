/* ============================================================================
   VendorLink — Geo Command Centre controller
   ============================================================================

   Three tiers, one state object, one render path.

       tier 1  world      → continents, each a group of country polygons
       tier 2  continent  → its countries, everything else faded back
       tier 3  country    → states / regions with registered vendors
       tier 4  state      → pulsing pins, one per warehouse city

   THE STATE IS THE WHOLE DESIGN
   -----------------------------
   Everything on screen — which polygons are lit, where the camera is, what the
   panel says, whether Back exists — is derived from `state` and nothing else.
   A click does not draw anything; it sets state and calls render(). That is
   why the same code answers a map click, a panel row click, a Back press and
   the first page load, and why the three can never disagree about which tier
   is open.

   ONE BORDER FILE, THREE TIERS
   ----------------------------
   The country polygons are fetched once and then restyled, never rebuilt. A
   continent is not a shape in the file — it is the set of countries whose
   `continent` property matches — so tier 1 and tier 2 are the same layer under
   two different style functions, and drilling in is a repaint rather than a
   teardown. Repainting is also what makes the fade look continuous.

   Depends on: Leaflet 1.9 (CDN, loaded by the template before this file).
   ========================================================================= */

(function () {
    'use strict';

    var root = document.querySelector('[data-vlgeo]');
    if (!root) { return; }

    /* ── Config, handed over by the template ─────────────────────────────── */

    var THEME_KEY = 'vendorlink-geo-theme';
    var THEME_LIGHT = 'light';
    var THEME_DARK = 'dark';

    function readTheme() {
        try {
            var saved = window.localStorage.getItem(THEME_KEY);
            return saved === THEME_DARK ? THEME_DARK : THEME_LIGHT;
        } catch (err) {
            return THEME_LIGHT;
        }
    }

    function saveTheme(theme) {
        try {
            window.localStorage.setItem(THEME_KEY, theme);
        } catch (err) {
            /* Local storage can be blocked; the widget still works without it. */
        }
    }

    function applyTheme(theme) {
        var next = theme === THEME_DARK ? THEME_DARK : THEME_LIGHT;
        root.dataset.vlgeoTheme = next;
        saveTheme(next);

        if (els.themeToggle) {
            var dark = next === THEME_DARK;
            els.themeToggle.setAttribute('aria-pressed', String(dark));
            els.themeToggle.setAttribute('aria-label', dark ? 'Switch to light theme' : 'Switch to dark theme');
            els.themeToggle.title = dark ? 'Switch to light theme' : 'Switch to dark theme';
        }
        if (els.themeLabel) {
            els.themeLabel.textContent = next === THEME_DARK ? 'Light theme' : 'Dark theme';
        }
    }

    var API = {
        overview: root.dataset.apiOverview,
        /* The URLs carry a placeholder because Django reverses them with a
           sample argument — building them by string concatenation here would
           hard-code the URL shape in two places. */
        continent: root.dataset.apiContinent,
        country: root.dataset.apiCountry,
        state: root.dataset.apiState,
        hierarchy: root.dataset.apiHierarchy,
        borders: root.dataset.borders
    };

    var els = {
        map: root.querySelector('[data-vlgeo-map]'),
        stage: root.querySelector('[data-vlgeo-stage]'),
        back: root.querySelector('[data-vlgeo-back]'),
        tier: root.querySelector('[data-vlgeo-tier]'),
        trail: root.querySelector('[data-vlgeo-trail]'),
        scope: root.querySelector('[data-vlgeo-scope]'),
        scopeKind: root.querySelector('[data-vlgeo-scope-kind]'),
        kpis: root.querySelector('[data-vlgeo-kpis]'),
        list: root.querySelector('[data-vlgeo-list]'),
        listTitle: root.querySelector('[data-vlgeo-list-title]'),
        listCount: root.querySelector('[data-vlgeo-list-count]'),
        veil: root.querySelector('[data-vlgeo-veil]'),
        error: root.querySelector('[data-vlgeo-error]'),
        hint: root.querySelector('[data-vlgeo-hint]'),
        themeToggle: root.querySelector('[data-vlgeo-theme-toggle]'),
        themeLabel: root.querySelector('[data-vlgeo-theme-label]')
    };

    applyTheme(readTheme());

    /* Reduced motion is a JS concern too: CSS can shorten a transition but
       only this can stop Leaflet animating the camera across the Atlantic. */
    var PREFERS_STILL = window.matchMedia &&
        window.matchMedia('(prefers-reduced-motion: reduce)').matches;

    if (!window.L) {
        fail('Map library unavailable — the Leaflet CDN could not be reached.');
        return;
    }

    /* ── State ───────────────────────────────────────────────────────────── */

    var state = {
        level: 1,           // 1 | 2 | 3
        continent: null,    // 'EU'    — set at tiers 2 and 3
        country: null,      // 'DE'    — set at tiers 3 and 4
        stateCode: null,    // state slug — set at tier 4
        city: null,         // pin code — a selection, not a tier
        payload: null,      // the last response, whatever tier it was for
        hover: null,        // code under the cursor, for the highlight style
        busy: false
    };

    var lastValues = {};    // KPI values from the previous render, for the flash

    /* ── Map ─────────────────────────────────────────────────────────────── */

    var map = L.map(els.map, {
        zoomControl: true,
        attributionControl: false,
        /* The map sits in the middle of a long page. Grabbing the wheel here
           traps the reader mid-scroll, so the wheel is off until they click
           into the map — then it behaves like a map. */
        scrollWheelZoom: false,
        minZoom: 2,
        maxZoom: 11,
        worldCopyJump: true,
        zoomSnap: 0.25,
        fadeAnimation: !PREFERS_STILL,
        zoomAnimation: !PREFERS_STILL
    }).setView([25, 10], 2);

    map.on('click', function () { map.scrollWheelZoom.enable(); });
    map.on('mouseout', function () { map.scrollWheelZoom.disable(); });

    var borderLayer = null;                 // L.GeoJSON, built once
    var byCountry = {};                     // iso2 → layer, for fast restyle
    var pinLayer = L.layerGroup().addTo(map);
    var pinsByCode = {};                    // city id → marker

    /* ── Colour, shared with the stylesheet ──────────────────────────────── */

    var CSS = getComputedStyle(root);
    function token(name, fallback) {
        return (CSS.getPropertyValue(name) || '').trim() || fallback;
    }
    var SALES = token('--gc-sales', '#3B82F6');
    var WAREHOUSE = token('--gc-warehouse', '#10B981');
    var DIM = '#94A3B8';

    /* ── Fetch ───────────────────────────────────────────────────────────── */

    function getJSON(url) {
        return fetch(url, {
            credentials: 'same-origin',
            headers: { 'X-Requested-With': 'XMLHttpRequest' }
        }).then(function (res) {
            if (!res.ok) { throw new Error('HTTP ' + res.status); }
            return res.json();
        });
    }

    /* The border file is ~165KB and never changes within a session, so it is
       fetched once and the promise itself is the cache — two drills firing at
       the same time share one request rather than racing. */
    var bordersPromise = null;
    function loadBorders() {
        if (!bordersPromise) { bordersPromise = getJSON(API.borders); }
        return bordersPromise;
    }

    function endpoint(level, code, country) {
        if (level === 1) { return API.overview; }
        if (level === 2) { return API.continent.replace('__CODE__', code); }
        if (level === 3) { return API.country.replace('__CODE__', code); }
        /* The country is passed explicitly for tier 4 because a pin clicked
           from the world map knows which country it is in while `state` does
           not yet — it is still on tier 1. */
        return API.state
            .replace('__COUNTRY__', country || state.country)
            .replace('__STATE__', code);
    }

    function fail(message) {
        if (!els.error) { return; }
        els.error.textContent = message;
        els.error.hidden = false;
        busy(false);
    }

    function busy(on) {
        state.busy = on;
        if (els.veil) { els.veil.hidden = !on; }
    }

    /* ── Drill: the only way state changes ───────────────────────────────── */

    /**
     * Move to a tier and repaint everything from the response.
     *
     * `code` is a continent code at level 2 and an ISO2 at level 3. The parent
     * chain is derived from the response's own `scope`, not from what the
     * caller thought it was drilling from — so a direct jump to a country (a
     * panel row, a deep link later) still knows which continent to go Back to.
     */
    function drill(level, code, country) {
        if (state.busy) { return; }
        busy(true);
        if (els.error) { els.error.hidden = true; }

        Promise.all([loadBorders(), getJSON(endpoint(level, code, country))])
            .then(function (results) {
                ensureBorders(results[0]);
                var payload = results[1];

                state.level = payload.level;
                state.payload = payload;
                if (payload.level === 1) {
                    state.continent = null;
                    state.country = null;
                    state.stateCode = null;
                } else if (payload.level === 2) {
                    state.continent = payload.scope.code;
                    state.country = null;
                    state.stateCode = null;
                } else if (payload.level === 3) {
                    state.continent = payload.scope.parent;
                    state.country = payload.scope.code;
                    state.stateCode = null;
                } else {
                    state.continent = payload.scope.grandparent;
                    state.country = payload.scope.parent;
                    state.stateCode = payload.scope.code;
                }

                render();
                busy(false);
            })
            .catch(function (err) {
                fail('Could not load the network for this view (' + err.message + ').');
            });
    }

    /* ── Borders ─────────────────────────────────────────────────────────── */

    function ensureBorders(geojson) {
        if (borderLayer) { return; }

        borderLayer = L.geoJSON(geojson, {
            style: styleFor,
            onEachFeature: function (feature, layer) {
                var p = feature.properties;
                byCountry[p.iso2] = layer;

                layer.on({
                    mouseover: function () {
                        /* At tier 1 the hover unit is the continent, not the
                           country under the cursor — the reader is choosing a
                           region, and lighting one country of six reads as a
                           mis-hit. */
                        state.hover = hoverCode(p);
                        restyle();
                    },
                    mouseout: function () { state.hover = null; restyle(); },
                    click: function (ev) {
                        L.DomEvent.stopPropagation(ev);
                        onFeatureClick(p);
                    }
                });
            }
        }).addTo(map);
    }

    /** Which countries are "live" at the current tier — the ones with data. */
    /* ── Geography ───────────────────────────────────────────────────────

       Vendor presence is no longer a fill.

       It used to be: countries holding vendors were tinted blue. That answers
       a question nobody asked — a tinted Australia does not say whether the
       warehouse is in Melbourne or Perth — and at tier 1 it lit up half the
       world while the actual locations stayed invisible. The pins carry that
       fact now, placed where the fact is, which frees the polygons for the
       only job left: saying where the camera is.

       So the rules here are about scope, not data. In scope means "inside what
       the trail says you are looking at". Everything else is faint geography,
       present so the region still reads as part of a world. */

    function pinsIn(payload) {
        return (payload && payload.pins) || [];
    }

    /** Continents that hold at least one pin — a click target only if so. */
    function stockedContinents() {
        var out = {};
        pinsIn(state.payload).forEach(function (pin) { out[pin.continent] = true; });
        return out;
    }

    /** Countries that hold at least one pin, same reason. */
    function stockedCountries() {
        var out = {};
        pinsIn(state.payload).forEach(function (pin) { out[pin.iso2] = true; });
        return out;
    }

    function inScope(p) {
        if (state.level === 1) { return true; }
        if (state.level === 2) { return p.continent === state.continent; }
        return p.iso2 === state.country;
    }

    function styleFor(feature) {
        var p = feature.properties;
        var scoped = inScope(p);
        var hot = state.hover && (state.hover === p.iso2 || state.hover === p.continent);

        if (!scoped) {
            return {
                color: 'rgba(148,163,184,.12)',
                weight: 0.4,
                opacity: 0.32,
                fillColor: DIM,
                fillOpacity: 0.015,
                className: 'vlgeo-shape'
            };
        }

        /* One neutral emphasis for "this is what you are looking at". Not the
           accent — the accent belongs to the pins, and a map where the ground
           and the markers share a colour has no foreground. */
        return {
            color: hot ? 'rgba(226,232,240,.55)' : 'rgba(148,163,184,.34)',
            weight: hot ? 1.4 : 0.7,
            opacity: hot ? 0.95 : 0.7,
            fillColor: '#64748B',
            fillOpacity: hot ? 0.16 : (state.level === 1 ? 0.05 : 0.09),
            className: 'vlgeo-shape'
        };
    }

    function restyle() {
        if (borderLayer) { borderLayer.setStyle(styleFor); }
    }

    function hoverCode(p) {
        if (state.level === 1) { return p.continent; }
        if (state.level === 2) { return p.continent === state.continent ? p.iso2 : null; }
        return null;
    }

    function onFeatureClick(p) {
        /* A polygon is still a drill target — it is the biggest thing on the
           map to aim at — but only where there is something to drill into.
           Clicking an empty country and being told "no registered vendors"
           answers a question the reader did not ask. */
        if (state.level === 1) {
            if (stockedContinents()[p.continent]) { drill(2, p.continent); }
        } else if (state.level === 2 && p.continent === state.continent) {
            if (stockedCountries()[p.iso2]) { drill(3, p.iso2); }
        }
    }

    /* ── Camera ──────────────────────────────────────────────────────────── */

    function boundsOf(codes) {
        var bounds = L.latLngBounds([]);
        codes.forEach(function (code) {
            var layer = byCountry[code];
            if (layer) { bounds.extend(layer.getBounds()); }
        });
        return bounds.isValid() ? bounds : null;
    }

    function frame() {
        var options = {
            padding: [40, 40],
            duration: 1.05,
            easeLinearity: 0.22,
            animate: !PREFERS_STILL,
            maxZoom: state.level >= 3 ? 8 : 6
        };

        /* The pins are the subject at every tier, so they are what the camera
           frames — not the country outline. The US spans 4,500km with its
           warehouses on two coasts; framing the polygon puts both in corners
           and Nebraska in the middle. Below tier 3 the borders still set the
           frame, because a continent with three pins in it should look like a
           continent rather than zoom to the three. */
        var pts = pinsIn(state.payload).map(function (pin) { return [pin.lat, pin.lon]; });

        if (state.level >= 3 && pts.length) {
            if (pts.length === 1) {
                map.flyTo(pts[0], Math.min(9, options.maxZoom + 1), {
                    duration: options.duration, animate: options.animate
                });
                return;
            }
            map.flyToBounds(L.latLngBounds(pts).pad(0.35), options);
            return;
        }

        var codes = (state.payload && state.payload.focus && state.payload.focus.codes) || [];
        var bounds = boundsOf(codes);
        /* A continent whose polygons are missing still has pins; fall back to
           them rather than leaving the camera where it was. */
        if (!bounds && pts.length) { bounds = L.latLngBounds(pts).pad(0.4); }
        if (bounds) { map.flyToBounds(bounds, options); }
    }

    /* ── Map furniture: pins and region chips ────────────────────────────── */

    function esc(value) {
        return String(value == null ? '' : value)
            .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
            .replace(/"/g, '&quot;');
    }

    /* ── Pins ────────────────────────────────────────────────────────────

       One marker per city that holds registered vendors, at every tier. A
       teardrop rather than a dot, and anchored at its tip, so the point of the
       pin is on the coordinate the way a map pin is read.

       A city with several vendors is one pin carrying all of them: stacking
       markers on the same coordinate leaves only the top one hoverable, and
       the reader has no way of knowing the others are under it. The count
       badge is how the pin says there is more than one. */

    function pinIcon(pin) {
        var classes = 'vlgeo-pin' + (pin.located ? '' : ' vlgeo-pin--approx');
        return L.divIcon({
            className: '',                    // Leaflet's default draws a white box
            html:
                '<div class="' + classes + '">' +
                    '<span class="vlgeo-pin__pulse"></span>' +
                    '<svg class="vlgeo-pin__glyph" viewBox="0 0 24 32" aria-hidden="true">' +
                        '<path d="M12 .8C5.9.8 1 5.7 1 11.8 1 20 12 31.2 12 31.2S23 20 23 11.8C23 5.7 18.1.8 12 .8z"/>' +
                        '<circle class="vlgeo-pin__eye" cx="12" cy="11.8" r="4"/>' +
                    '</svg>' +
                    (pin.warehouses > 1
                        ? '<span class="vlgeo-pin__count">' + pin.warehouses + '</span>' : '') +
                '</div>',
            iconSize: [24, 32],
            iconAnchor: [12, 31],             // the tip, not the centre
            tooltipAnchor: [0, -28]
        });
    }

    function detailRow(label, value, href) {
        if (!value) { return ''; }
        var body = href
            ? '<a class="vlgeo-tip__link" href="' + esc(href) + '">' + esc(value) + '</a>'
            : esc(value);
        return '<div class="vlgeo-tip__row">' +
                   '<span class="vlgeo-tip__rk">' + esc(label) + '</span>' +
                   '<span class="vlgeo-tip__rv">' + body + '</span>' +
               '</div>';
    }

    function vendorBlock(vendor) {
        var caps = (vendor.capabilities || []).slice(0, 4).map(function (label) {
            return '<span class="vlgeo-tip__cap">' + esc(label) + '</span>';
        }).join('');

        return '<div class="vlgeo-tip__vendor">' +
                   '<div class="vlgeo-tip__vname">' + esc(vendor.name) + '</div>' +
                   detailRow('Contact', vendor.contact) +
                   detailRow('Email', vendor.email, vendor.email ? 'mailto:' + vendor.email : '') +
                   detailRow('Mobile', vendor.mobile) +
                   detailRow('Website', vendor.website, vendor.website) +
                   (vendor.warehouse_location
                       ? '<div class="vlgeo-tip__addr">' +
                             '<svg viewBox="0 0 16 16" aria-hidden="true">' +
                                 '<path d="M8 1.5a4.2 4.2 0 0 0-4.2 4.2c0 3.1 4.2 8.8 4.2 8.8s4.2-5.7 4.2-8.8A4.2 4.2 0 0 0 8 1.5z" ' +
                                 'fill="none" stroke="currentColor" stroke-width="1.2"/>' +
                                 '<circle cx="8" cy="5.7" r="1.5" fill="currentColor"/>' +
                             '</svg>' +
                             esc(vendor.warehouse_location) +
                         '</div>'
                       : '') +
                   (caps ? '<div class="vlgeo-tip__caps">' + caps + '</div>' : '') +
               '</div>';
    }

    /* The card reads top-down in the order the reader asked for it: where this
       is (continent → country → state → city), then who is there. */
    function pinTooltip(pin) {
        var shown = pin.vendors.slice(0, 3).map(vendorBlock).join('');
        var hidden = pin.vendors.length - 3;

        return '<div class="vlgeo-tip__card vlgeo-tip__card--pin">' +
                   '<div class="vlgeo-tip__place">' +
                       '<div class="vlgeo-tip__crumbs">' +
                           esc(pin.continent_name) + '<i>&rsaquo;</i>' + esc(pin.country) +
                       '</div>' +
                       '<div class="vlgeo-tip__city">' + esc(pin.city) + '</div>' +
                       '<div class="vlgeo-tip__state">' + esc(pin.state) +
                           (pin.located ? '' : ' <em>&middot; approximate</em>') +
                       '</div>' +
                   '</div>' +
                   '<div class="vlgeo-tip__vendors">' + shown +
                       (hidden > 0
                           ? '<div class="vlgeo-tip__more">+' + hidden +
                             ' more vendor' + (hidden === 1 ? '' : 's') + ' at this location</div>'
                           : '') +
                   '</div>' +
               '</div>';
    }

    function drawFurniture() {
        pinLayer.clearLayers();
        pinsByCode = {};

        pinsIn(state.payload).forEach(function (pin) {
            var marker = L.marker([pin.lat, pin.lon], {
                icon: pinIcon(pin),
                riseOnHover: true,
                keyboard: false,
                title: pin.city + ' — ' + pin.vendors.length +
                       ' vendor' + (pin.vendors.length === 1 ? '' : 's')
            });

            marker.bindTooltip(pinTooltip(pin), {
                className: 'vlgeo-tip',
                direction: 'top',
                offset: [0, 0],
                opacity: 1,
                /* Not interactive: a card that can take the pointer steals the
                   hover from the pin under it and flickers. Links inside are
                   therefore decoration — the panel row is the click target. */
                interactive: false
            });

            marker.on('mouseover', function () { highlightRow(pin.code, true); });
            marker.on('mouseout', function () { highlightRow(pin.code, false); });

            /* Clicking a pin goes to its state, which is the tier where that
               pin is one of a handful rather than one of fifty. */
            marker.on('click', function () {
                if (state.level < 4 && pin.state_slug) { drill(4, pin.state_slug, pin.iso2); }
            });

            marker.addTo(pinLayer);
            pinsByCode[pin.code] = marker;
        });
    }

    /* ── Panel ───────────────────────────────────────────────────────────── */

    function renderKpis() {
        var cards = (state.payload && state.payload.kpis) || [];
        els.kpis.innerHTML = '';

        cards.forEach(function (kpi) {
            var changed = lastValues[kpi.key] !== undefined && lastValues[kpi.key] !== kpi.value;
            lastValues[kpi.key] = kpi.value;

            var card = document.createElement('div');
            card.className = 'vlgeo-kpi' + (changed ? ' vlgeo-kpi--changed' : '');
            card.setAttribute('data-accent', kpi.accent);

            var html = '<span class="vlgeo-kpi__label">' + esc(kpi.label) + '</span>' +
                       '<span class="vlgeo-kpi__value">' + esc(kpi.display) + '</span>' +
                       '<span class="vlgeo-kpi__sub">' + esc(kpi.sub) + '</span>';
            if (typeof kpi.meter === 'number') {
                html += '<span class="vlgeo-kpi__meter"><i></i></span>';
            }
            card.innerHTML = html;
            els.kpis.appendChild(card);

            /* The meter is filled on the next frame so the width transition
               has a value to animate from. */
            if (typeof kpi.meter === 'number') {
                var bar = card.querySelector('.vlgeo-kpi__meter i');
                requestAnimationFrame(function () {
                    bar.style.width = Math.max(0, Math.min(100, kpi.meter)) + '%';
                });
            }
        });
    }

    var LIST_TITLE = { 1: 'Continents', 2: 'Countries', 3: 'States', 4: 'Warehouse cities' };

    function renderList() {
        var children = (state.payload && state.payload.children) || [];
        els.listTitle.textContent = LIST_TITLE[state.level];
        els.listCount.textContent = children.length + ' registered location' + (children.length === 1 ? '' : 's');
        els.list.innerHTML = '';

        children.forEach(function (child) {
            var row = document.createElement('li');
            var button = document.createElement('button');
            button.type = 'button';
            button.className = 'vlgeo__row';
            button.dataset.code = child.code;
            button.dataset.leaf = state.level === 4 ? '1' : '0';

            button.innerHTML =
                '<span class="vlgeo__row-name">' + esc(child.name) + '</span>' +
                '<span class="vlgeo__row-val">' + esc(child.display || child.warehouses) + '</span>' +
                '<span class="vlgeo__row-meta">' +
                    '<span>' + esc(child.warehouses) + ' warehouse' + (child.warehouses === 1 ? '' : 's') + '</span>' +
                    '<span>' + esc(child.vendors) + ' vendor' + (child.vendors === 1 ? '' : 's') + '</span>' +
                '</span>' +
                '<span class="vlgeo__row-bar"><i style="width:' +
                    Math.max(2, Math.min(100, child.share)) + '%"></i></span>';

            /* Rows mirror the map: at tiers 1 and 2 they drill, at tier 3 the
               leaf has nowhere to go, so it flies to the pin and shows its
               tooltip instead — the same information a hover would give. */
            button.addEventListener('click', function () {
                if (state.level === 1) { drill(2, child.code); }
                else if (state.level === 2) { drill(3, child.code); }
                else if (state.level === 3) { drill(4, child.code); }
                else { focusCity(child); }
            });
            button.addEventListener('mouseenter', function () {
                if (state.level >= 3) { return; }
                state.hover = state.level === 1 ? child.code : child.code;
                restyle();
            });
            button.addEventListener('mouseleave', function () {
                state.hover = null;
                restyle();
            });

            row.appendChild(button);
            els.list.appendChild(row);
        });
    }

    function focusCity(city) {
        var marker = pinsByCode[city.code];
        if (!marker) { return; }
        map.flyTo([city.lat, city.lon], Math.max(map.getZoom(), 7), {
            duration: 0.8, animate: !PREFERS_STILL
        });
        marker.openTooltip();
        setTimeout(function () { marker.closeTooltip(); }, 3200);
    }

    function highlightRow(code, on) {
        var row = els.list.querySelector('[data-code="' + code + '"]');
        if (row) { row.classList.toggle('is-active', !!on); }
    }

    function renderTrail() {
        var trail = (state.payload && state.payload.trail) || [];
        els.trail.innerHTML = '';

        trail.forEach(function (step, index) {
            if (index) {
                var sep = document.createElement('span');
                sep.className = 'vlgeo__crumb-sep';
                sep.textContent = '/';
                els.trail.appendChild(sep);
            }
            var crumb = document.createElement('button');
            crumb.type = 'button';
            crumb.className = 'vlgeo__crumb';
            crumb.textContent = step.label;
            var current = index === trail.length - 1;
            crumb.setAttribute('aria-current', String(current));
            if (current) {
                crumb.disabled = true;
            } else {
                crumb.addEventListener('click', function () { drill(step.level, step.code); });
            }
            els.trail.appendChild(crumb);
        });
    }

    var HINTS = {
        1: 'Hover a pin for the vendor there, or pick a continent above',
        2: 'Hover a pin, or click a country with pins for its states',
        3: 'Hover a pin, or click a state for its cities',
        4: 'Hover a pin for the vendors at that location'
    };

    function render() {
        var payload = state.payload;

        els.map.setAttribute('data-tier', String(state.level));
        els.tier.innerHTML = 'Tier <b>' + state.level + '</b> / 4';
        els.scope.textContent = payload.scope.name;
        els.scopeKind.textContent = payload.scope.kind === 'global' ? 'Worldwide' : payload.scope.kind;
        els.hint.textContent = HINTS[state.level];

        /* Back is the tier-1 test and nothing else: it is hidden at the top
           and present at every other tier, including one reached by a
           breadcrumb or a panel row. */
        els.back.hidden = state.level === 1;

        renderTrail();
        renderKpis();
        renderList();
        drawFurniture();
        syncSelects();
        restyle();
        frame();
    }

    /* ── Back ────────────────────────────────────────────────────────────── */

    els.back.addEventListener('click', function () {
        if (state.level === 4) { drill(3, state.country); }
        else if (state.level === 3) { drill(2, state.continent); }
        else if (state.level === 2) { drill(1, null); }
    });

    /* Escape is the same gesture, for anyone who never touches the mouse. */
    root.addEventListener('keydown', function (ev) {
        if (ev.key === 'Escape' && state.level > 1) {
            ev.preventDefault();
            els.back.click();
        }
        if (ev.key.toLowerCase() === 't' && !ev.altKey && !ev.ctrlKey && !ev.metaKey) {
            ev.preventDefault();
            if (els.themeToggle) { els.themeToggle.click(); }
        }
    });

    if (els.themeToggle) {
        els.themeToggle.addEventListener('click', function () {
            applyTheme(root.dataset.vlgeoTheme === THEME_DARK ? THEME_LIGHT : THEME_DARK);
        });
    }


    /* ── The four dependent selects ──────────────────────────────────────

       A second route to the same state the map clicks set — not a filter
       beside the map. Choosing Asia here and clicking Asia on the map land in
       exactly the same place, and a drill by click moves the selects to match,
       because two controls over one state that can disagree is two states.

       The whole vendor tree arrives in one request and is kept. Three
       dropdowns that each waited on a round trip would make choosing a city a
       three-network-wait exercise for data the browser already holds. */

    var hierarchy = null;
    var selects = {
        continent: root.querySelector('[data-vlgeo-select="continent"]'),
        country: root.querySelector('[data-vlgeo-select="country"]'),
        state: root.querySelector('[data-vlgeo-select="state"]'),
        city: root.querySelector('[data-vlgeo-select="city"]')
    };
    /* Set while the selects are being written from state, so the change
       handlers below do not fire a drill for a value nobody chose. */
    var syncing = false;

    function loadHierarchy() {
        return getJSON(API.hierarchy).then(function (body) {
            hierarchy = body;
            syncSelects();
        }).catch(function () {
            /* The map is fully usable without the selects; losing them is not
               worth a red banner over a working map. */
            Object.keys(selects).forEach(function (key) {
                if (selects[key]) { selects[key].disabled = true; }
            });
        });
    }

    function findContinent(code) {
        if (!hierarchy || !code) { return null; }
        return hierarchy.continents.filter(function (c) { return c.code === code; })[0] || null;
    }

    function findCountry(continentCode, iso2) {
        var continent = findContinent(continentCode);
        if (!continent || !iso2) { return null; }
        return continent.countries.filter(function (c) { return c.code === iso2; })[0] || null;
    }

    function findState(continentCode, iso2, slug) {
        var country = findCountry(continentCode, iso2);
        if (!country || !slug) { return null; }
        return country.states.filter(function (s) { return s.code === slug; })[0] || null;
    }

    function fillSelect(select, rows, placeholder, value, labelKey) {
        if (!select) { return; }
        select.innerHTML = '';
        var blank = document.createElement('option');
        blank.value = '';
        blank.textContent = placeholder;
        select.appendChild(blank);

        rows.forEach(function (row) {
            var option = document.createElement('option');
            option.value = row.code;
            option.textContent = labelKey ? row[labelKey] : row.name;
            select.appendChild(option);
        });

        select.value = value || '';
        select.disabled = rows.length === 0;
    }

    /** Write the selects from `state`. Never the other way round. */
    function syncSelects() {
        if (!hierarchy || !selects.continent) { return; }
        syncing = true;

        fillSelect(selects.continent, hierarchy.continents, 'All continents', state.continent);

        var continent = findContinent(state.continent);
        fillSelect(selects.country, continent ? continent.countries : [],
                   continent ? 'All countries' : 'Select a continent first', state.country);

        var country = findCountry(state.continent, state.country);
        fillSelect(selects.state, country ? country.states : [],
                   country ? 'All states' : 'Select a country first', state.stateCode);

        var stateEntry = findState(state.continent, state.country, state.stateCode);
        var cities = stateEntry ? stateEntry.cities : [];
        /* A city selected in the state you just left is not a selection any
           more; leaving it set would have the select showing blank while the
           state thought something was chosen. */
        if (state.city && !cities.some(function (c) { return c.code === state.city; })) {
            state.city = null;
        }
        fillSelect(selects.city, cities,
                   stateEntry ? 'All cities' : 'Select a state first', state.city);

        syncing = false;
    }

    /* Clearing a level goes up to its parent rather than doing nothing: the
       blank option reads as "all of the level above", and that is what it
       should do. */
    function onSelect(kind, value) {
        if (syncing) { return; }
        if (kind === 'continent') {
            value ? drill(2, value) : drill(1, null);
        } else if (kind === 'country') {
            value ? drill(3, value) : drill(2, state.continent);
        } else if (kind === 'state') {
            value ? drill(4, value, state.country) : drill(3, state.country);
        } else if (kind === 'city') {
            /* A city is not a tier — it is a pin inside the state already on
               screen. Selecting one flies to it and opens its card. */
            state.city = value;
            focusPin(value);
        }
    }

    Object.keys(selects).forEach(function (kind) {
        if (!selects[kind]) { return; }
        selects[kind].addEventListener('change', function (ev) {
            onSelect(kind, ev.target.value);
        });
    });

    function focusPin(code) {
        var marker = pinsByCode[code];
        if (!marker) { return; }
        map.flyTo(marker.getLatLng(), Math.max(map.getZoom(), 8), {
            duration: 0.8, animate: !PREFERS_STILL
        });
        marker.openTooltip();
        clearTimeout(focusPin.timer);
        focusPin.timer = setTimeout(function () { marker.closeTooltip(); }, 4200);
    }

    /* ── Size ────────────────────────────────────────────────────────────── */

    /* A Leaflet map in a flex/grid cell has to be told its box changed, and a
       window resize listener misses the cases that actually happen here: the
       panel stacking under the map at 900px, the sidebar collapsing, a font
       loading. ResizeObserver catches all of them; the window listener stays
       as the fallback for browsers without it. */
    var resizeTimer = null;
    function recalc() {
        clearTimeout(resizeTimer);
        resizeTimer = setTimeout(function () {
            map.invalidateSize({ animate: false });
        }, 120);
    }

    if (window.ResizeObserver) {
        new ResizeObserver(recalc).observe(els.stage);
    }
    window.addEventListener('resize', recalc);
    window.addEventListener('load', recalc);

    /* ── Go ──────────────────────────────────────────────────────────────── */

    loadHierarchy();
    drill(1, null);
}());
