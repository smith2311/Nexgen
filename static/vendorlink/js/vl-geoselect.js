/**
 * vl-geoselect.js
 * Searchable country <select> + searchable city dropdown for VendorLink forms.
 * No external dependencies.
 *
 * Both panels use position:fixed so they are never clipped by a parent
 * container's overflow:hidden (e.g. .vr-card on the registration page).
 */
(function (global) {
    'use strict';

    /* ── Shared helpers ────────────────────────────────────────────────────── */

    function positionPanel(panel, trigger) {
        var rect      = trigger.getBoundingClientRect();
        var spaceDown = window.innerHeight - rect.bottom - 8;
        var spaceUp   = rect.top - 8;

        panel.style.left  = rect.left + 'px';
        panel.style.width = rect.width + 'px';
        panel.style.right = 'auto';

        if (spaceDown >= Math.min(280, spaceUp + 1)) {
            panel.style.top    = (rect.bottom + 4) + 'px';
            panel.style.bottom = 'auto';
        } else {
            panel.style.bottom = (window.innerHeight - rect.top + 4) + 'px';
            panel.style.top    = 'auto';
        }
    }

    function attachScrollClose(trigger, panel, getIsOpen, closeFn) {
        function onScrollResize() {
            if (!getIsOpen()) return;
            var rect = trigger.getBoundingClientRect();
            if (rect.bottom < 0 || rect.top > window.innerHeight) closeFn();
            else positionPanel(panel, trigger);
        }
        window.addEventListener('scroll', onScrollResize, true);
        window.addEventListener('resize', onScrollResize);
    }

    /* ── VlCountrySelect ───────────────────────────────────────────────────── */
    /**
     * Wraps a native <select> with a searchable fixed-position dropdown.
     * The native <select> stays hidden so Django validation works unchanged.
     *
     * @param {HTMLSelectElement} sel     The native select element.
     * @param {Object}            isoMap  { "India": "IN", … }
     * @param {Function}          onPick  Called with (countryName, isoCode).
     */
    function VlCountrySelect(sel, isoMap, onPick) {
        var wrap = document.createElement('div');
        wrap.className = 'vlcs-wrap';
        sel.parentNode.insertBefore(wrap, sel);
        wrap.appendChild(sel);
        sel.style.display = 'none';

        var trigger = document.createElement('div');
        trigger.className = 'vlcs-trigger';
        trigger.setAttribute('tabindex', '0');
        trigger.setAttribute('role', 'combobox');
        trigger.setAttribute('aria-expanded', 'false');
        trigger.innerHTML =
            '<span class="vlcs-val vlcs-placeholder"></span>' +
            '<i class="fas fa-chevron-down vlcs-arrow" aria-hidden="true"></i>';
        wrap.appendChild(trigger);

        var panel = document.createElement('div');
        panel.className = 'vlcs-panel';
        panel.style.display = 'none';
        panel.innerHTML =
            '<div class="vlcs-search-row">' +
            '<i class="fas fa-search vlcs-search-icon" aria-hidden="true"></i>' +
            '<input type="text" class="vlcs-search" placeholder="Search country…" autocomplete="off" aria-label="Search countries">' +
            '</div>' +
            '<ul class="vlcs-list" role="listbox"></ul>';
        document.body.appendChild(panel);

        var valSpan     = trigger.querySelector('.vlcs-val');
        var searchInput = panel.querySelector('.vlcs-search');
        var list        = panel.querySelector('.vlcs-list');
        var isOpen      = false;

        function syncLabel() {
            var opt = sel.options[sel.selectedIndex];
            if (opt && opt.value) {
                valSpan.textContent = opt.text;
                valSpan.classList.remove('vlcs-placeholder');
            } else {
                var blank = sel.options[0];
                valSpan.textContent = (blank && !blank.value) ? blank.text : 'Select a country…';
                valSpan.classList.add('vlcs-placeholder');
            }
        }

        function buildList() {
            list.innerHTML = '';
            Array.prototype.slice.call(sel.options).forEach(function (o) {
                if (!o.value) return;
                var li = document.createElement('li');
                li.className = 'vlcs-opt';
                li.textContent = o.text;
                li.dataset.val = o.value;
                li.setAttribute('role', 'option');
                li.addEventListener('mousedown', function (e) {
                    e.preventDefault();
                    pick(o.value);
                });
                list.appendChild(li);
            });
        }

        function filterList(q) {
            q = (q || '').toLowerCase();
            var items = list.querySelectorAll('.vlcs-opt');
            var hasVisible = false;
            items.forEach(function (li) {
                var match = !q || li.textContent.toLowerCase().indexOf(q) !== -1;
                li.style.display = match ? '' : 'none';
                if (match) hasVisible = true;
            });
            var noMatch = list.querySelector('.vlcs-no-match');
            if (!hasVisible) {
                if (!noMatch) {
                    noMatch = document.createElement('li');
                    noMatch.className = 'vlcs-no-match';
                    noMatch.textContent = 'No countries found';
                    list.appendChild(noMatch);
                }
            } else if (noMatch) {
                noMatch.parentNode.removeChild(noMatch);
            }
        }

        function pick(value) {
            sel.value = value;
            syncLabel();
            close();
            sel.dispatchEvent(new Event('change', { bubbles: true }));
            if (onPick) onPick(value, isoMap[value] || '');
        }

        function open() {
            isOpen = true;
            positionPanel(panel, trigger);
            panel.style.display = 'block';
            trigger.classList.add('vlcs-trigger-open');
            trigger.setAttribute('aria-expanded', 'true');
            searchInput.value = '';
            filterList('');
            var active = list.querySelector('.vlcs-opt[data-val="' + (sel.value || '').replace(/"/g, '\\"') + '"]');
            if (active) { active.classList.add('vlcs-focused'); active.scrollIntoView({ block: 'nearest' }); }
            searchInput.focus();
        }

        function close() {
            isOpen = false;
            panel.style.display = 'none';
            trigger.classList.remove('vlcs-trigger-open');
            trigger.setAttribute('aria-expanded', 'false');
            list.querySelectorAll('.vlcs-focused').forEach(function (el) { el.classList.remove('vlcs-focused'); });
        }

        trigger.addEventListener('click', function () { isOpen ? close() : open(); });
        trigger.addEventListener('keydown', function (e) {
            if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); open(); }
            if (e.key === 'Escape') close();
        });
        searchInput.addEventListener('input', function () { filterList(this.value); });
        searchInput.addEventListener('keydown', function (e) {
            if (e.key === 'Escape') { close(); trigger.focus(); }
        });
        document.addEventListener('click', function (e) {
            if (!wrap.contains(e.target) && !panel.contains(e.target)) close();
        });
        attachScrollClose(trigger, panel, function () { return isOpen; }, close);

        /* The searchable list is a copy of the native select's options, built
         * once. The continent → country cascade rewrites those options when a
         * continent is chosen, which would leave this panel showing the old
         * full country list while the native select held the narrowed one — so
         * it listens for the cascade's event and rebuilds.
         *
         * syncLabel() after the rebuild matters as much as the list: if the
         * cascade cleared the selection because the country was outside the new
         * continent, the trigger must fall back to its placeholder rather than
         * keep displaying a country the form no longer holds. */
        sel.addEventListener('vl:options-changed', function () {
            buildList();
            syncLabel();
        });

        buildList();
        syncLabel();
    }

    /* ── VlCitySelect ──────────────────────────────────────────────────────── */
    /**
     * Replaces a city text <input> with a searchable dropdown that loads cities
     * via AJAX when a country is chosen. The original <input> is kept hidden
     * and updated on selection so form submission works unchanged.
     * Users can also type a custom city name not in the dataset.
     *
     * @param {HTMLInputElement} cityInput  The original city text input.
     * @param {string}           apiUrl     Cities endpoint, e.g. "/vendorlink/api/cities/".
     */
    function VlCitySelect(cityInput, apiUrl) {
        cityInput.style.display = 'none';

        var wrap = document.createElement('div');
        wrap.className = 'vlcs-wrap';
        cityInput.parentNode.insertBefore(wrap, cityInput);
        wrap.appendChild(cityInput);

        var trigger = document.createElement('div');
        trigger.className = 'vlcs-trigger vlcs-city-trigger vlcs-disabled';
        trigger.setAttribute('tabindex', '-1');
        trigger.setAttribute('role', 'combobox');
        trigger.setAttribute('aria-expanded', 'false');
        trigger.innerHTML =
            '<span class="vlcs-val vlcs-placeholder">Select a country first…</span>' +
            '<i class="fas fa-chevron-down vlcs-arrow" aria-hidden="true"></i>';
        wrap.appendChild(trigger);

        var panel = document.createElement('div');
        panel.className = 'vlcs-panel';
        panel.style.display = 'none';
        panel.innerHTML =
            '<div class="vlcs-search-row">' +
            '<i class="fas fa-city vlcs-search-icon" aria-hidden="true"></i>' +
            '<input type="text" class="vlcs-search vlcs-city-search" placeholder="Type to search or enter any city…" autocomplete="off" aria-label="Search or enter city">' +
            '</div>' +
            '<ul class="vlcs-list" role="listbox"></ul>';
        document.body.appendChild(panel);

        var valSpan     = trigger.querySelector('.vlcs-val');
        var searchInput = panel.querySelector('.vlcs-city-search');
        var list        = panel.querySelector('.vlcs-list');
        var isOpen      = false;
        var allCities   = [];
        var disabled    = true;

        function syncLabel() {
            var v = cityInput.value;
            if (v) {
                valSpan.textContent = v;
                valSpan.classList.remove('vlcs-placeholder');
            } else {
                valSpan.textContent = disabled ? 'Select a country first…' : 'Select or type a city…';
                valSpan.classList.add('vlcs-placeholder');
            }
        }

        function renderList(cities, q) {
            list.innerHTML = '';
            cities.forEach(function (city) {
                var li = document.createElement('li');
                li.className = 'vlcs-opt';
                li.textContent = city;
                li.addEventListener('mousedown', function (e) {
                    e.preventDefault();
                    pick(city);
                });
                list.appendChild(li);
            });
            // If search text typed but not in list, show "Use 'X'" option.
            if (q && cities.length === 0) {
                var li = document.createElement('li');
                li.className = 'vlcs-opt vlcs-create-opt';
                li.innerHTML = 'Use &ldquo;' + q.replace(/</g, '&lt;') + '&rdquo;';
                li.addEventListener('mousedown', function (e) {
                    e.preventDefault();
                    pick(q);
                });
                list.appendChild(li);
            }
        }

        function filterList(q) {
            q = (q || '').trim();
            if (!q) {
                renderList(allCities, '');
                return;
            }
            var ql = q.toLowerCase();
            var starts = allCities.filter(function (c) { return c.toLowerCase().indexOf(ql) === 0; });
            var contains = allCities.filter(function (c) { return c.toLowerCase().indexOf(ql) > 0; });
            var merged = starts.concat(contains);
            renderList(merged, q);
        }

        function pick(value) {
            cityInput.value = value;
            syncLabel();
            close();
        }

        function posPanel() { positionPanel(panel, trigger); }

        function open() {
            if (disabled) return;
            isOpen = true;
            posPanel();
            panel.style.display = 'block';
            trigger.classList.add('vlcs-trigger-open');
            trigger.setAttribute('aria-expanded', 'true');
            searchInput.value = '';
            renderList(allCities, '');
            searchInput.focus();
        }

        function close() {
            // If user typed something in search without picking, save it as the city.
            var typed = (searchInput.value || '').trim();
            if (typed && !cityInput.value) pick(typed);
            isOpen = false;
            panel.style.display = 'none';
            trigger.classList.remove('vlcs-trigger-open');
            trigger.setAttribute('aria-expanded', 'false');
        }

        trigger.addEventListener('click', function () {
            if (disabled) return;
            isOpen ? close() : open();
        });
        trigger.addEventListener('keydown', function (e) {
            if (disabled) return;
            if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); open(); }
            if (e.key === 'Escape') close();
        });
        searchInput.addEventListener('input', function () { filterList(this.value); });
        searchInput.addEventListener('keydown', function (e) {
            if (e.key === 'Enter') {
                e.preventDefault();
                var typed = (this.value || '').trim();
                if (typed) pick(typed);
                else close();
            }
            if (e.key === 'Escape') { close(); trigger.focus(); }
        });
        document.addEventListener('click', function (e) {
            if (!wrap.contains(e.target) && !panel.contains(e.target)) {
                if (isOpen) close();
            }
        });
        attachScrollClose(trigger, panel, function () { return isOpen; }, close);

        syncLabel();

        /* Public API */
        return {
            /**
             * Load cities for the given ISO code.
             * @param {string}  iso        2-letter country ISO code.
             * @param {boolean} keepValue  If true, don't clear the current city value.
             */
            loadCities: function (iso, keepValue) {
                if (!iso) {
                    allCities = [];
                    disabled  = true;
                    trigger.classList.add('vlcs-disabled');
                    trigger.setAttribute('tabindex', '-1');
                    if (!keepValue) cityInput.value = '';
                    syncLabel();
                    return;
                }

                disabled = false;
                trigger.classList.remove('vlcs-disabled');
                trigger.setAttribute('tabindex', '0');
                if (!keepValue) { cityInput.value = ''; syncLabel(); }

                fetch(apiUrl + '?cc=' + encodeURIComponent(iso))
                    .then(function (r) { return r.json(); })
                    .then(function (data) {
                        allCities = data.cities || [];
                        if (!keepValue) syncLabel();
                    })
                    .catch(function () { allCities = []; });
            }
        };
    }

    /* ── VlPhoneAutofill ───────────────────────────────────────────────────── */
    /**
     * Defaults a phone/mobile <input> to a country's ISD dial code (e.g. "+91 ")
     * when the country changes. Never clobbers a number the user already typed —
     * only fills when the field is empty or still holds exactly what this
     * helper last wrote (so switching country again updates the code, but a
     * real typed number is always left alone).
     *
     * @param {HTMLInputElement} input  The phone/mobile text input.
     */
    function VlPhoneAutofill(input) {
        var lastAuto = null;
        return {
            apply: function (dialCode) {
                if (!input || !dialCode) return;
                var current = (input.value || '').trim();
                if (!current || current === lastAuto) {
                    input.value = dialCode + ' ';
                    lastAuto = dialCode;
                }
            }
        };
    }

    global.VlCountrySelect = VlCountrySelect;
    global.VlCitySelect    = VlCitySelect;
    global.VlPhoneAutofill = VlPhoneAutofill;

}(window));
