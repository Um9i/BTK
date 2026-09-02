// Site-wide behavior, loaded once (deferred) on every page rather than
// inlined per-template -- cacheable across navigations instead of re-parsed
// on every page load, and it's what makes a real CSP feasible later. Each
// piece below guards on the DOM elements it needs existing at all, since
// this same file runs on every page and most pages don't have most of these
// elements (e.g. only "/" has #status-pill, only /web/planets has
// #planets-form). The pre-paint theme-init snippet in base.html's <head> is
// the one exception left inline -- it has to block before first paint to
// avoid a flash of the wrong theme, which an external, deferred script
// can't do.

(function () {
    // "/" jumps to the page's primary search box, wherever it lives -- the
    // hero search on "/", the compact one on /web/planets. Pages with
    // neither (detail pages, /login) have no #site-search at all, so this
    // is a no-op there rather than throwing on a null input. Ignored while
    // already typing in a field (so it doesn't hijack a literal "/" in a
    // query) or with a modifier held (so it doesn't fight browser/OS
    // shortcuts).
    var input = document.getElementById("site-search");
    if (!input) return;
    var hint = document.querySelector(".kbd-hint");
    document.addEventListener("keydown", function (e) {
        if (e.key !== "/" || e.metaKey || e.ctrlKey || e.altKey) return;
        var tag = document.activeElement ? document.activeElement.tagName : "";
        if (tag === "INPUT" || tag === "TEXTAREA") return;
        e.preventDefault();
        input.focus();
        input.select();
    });
    if (hint) {
        input.addEventListener("focus", function () { hint.style.display = "none"; });
        input.addEventListener("blur", function () { hint.style.display = ""; });
    }
})();

(function () {
    // Remember the last few hero-search queries in localStorage and offer
    // them back as a dropdown on focus -- no backend involved, this is
    // purely a per-browser convenience for repeated lookups. Built as a
    // combobox/listbox pair (aria-activedescendant, not real DOM focus
    // moving into the list) so arrow keys can move through the suggestions
    // while typing still works and the input never loses focus -- the
    // standard accessible-autocomplete shape.
    var STORAGE_KEY = "btk_recent_searches";
    var MAX_ENTRIES = 6;
    var form = document.getElementById("header-search-form");
    var input = document.getElementById("site-search");
    var list = document.getElementById("recent-searches");
    if (!form || !input || !list) return;

    input.setAttribute("role", "combobox");
    input.setAttribute("aria-autocomplete", "list");
    input.setAttribute("aria-expanded", "false");
    input.setAttribute("aria-controls", "recent-searches");
    list.setAttribute("role", "listbox");

    var activeIndex = -1;

    function load() {
        try {
            return JSON.parse(localStorage.getItem(STORAGE_KEY)) || [];
        } catch (e) {
            return [];
        }
    }
    function remember(q) {
        q = q.trim();
        if (!q) return;
        var items = load().filter(function (v) { return v.toLowerCase() !== q.toLowerCase(); });
        items.unshift(q);
        try {
            localStorage.setItem(STORAGE_KEY, JSON.stringify(items.slice(0, MAX_ENTRIES)));
        } catch (e) {
            // Storage full or disabled (private browsing) -- not worth surfacing.
        }
    }
    function options() {
        return list.querySelectorAll('[role="option"]');
    }
    function setActive(i) {
        var opts = options();
        opts.forEach(function (opt) { opt.classList.remove("active"); });
        activeIndex = i;
        if (i >= 0 && i < opts.length) {
            opts[i].classList.add("active");
            input.setAttribute("aria-activedescendant", opts[i].id);
        } else {
            input.removeAttribute("aria-activedescendant");
        }
    }
    function choose(q) {
        input.value = q;
        form.submit();
    }
    function render() {
        var items = load();
        list.innerHTML = "";
        activeIndex = -1;
        if (!items.length) {
            list.hidden = true;
            input.setAttribute("aria-expanded", "false");
            return;
        }
        items.forEach(function (q, i) {
            var li = document.createElement("li");
            var btn = document.createElement("button");
            btn.type = "button";
            btn.id = "recent-search-option-" + i;
            btn.setAttribute("role", "option");
            btn.textContent = q;
            // mousedown (not click) fires before the input's blur, so the
            // dropdown is still open and readable when this handler runs.
            btn.addEventListener("mousedown", function (e) {
                e.preventDefault();
                choose(q);
            });
            li.appendChild(btn);
            list.appendChild(li);
        });
        list.hidden = false;
        input.setAttribute("aria-expanded", "true");
    }
    function close() {
        list.hidden = true;
        input.setAttribute("aria-expanded", "false");
        input.removeAttribute("aria-activedescendant");
        activeIndex = -1;
    }

    form.addEventListener("submit", function () { remember(input.value); });
    input.addEventListener("focus", render);
    input.addEventListener("blur", function () {
        setTimeout(close, 100);
    });
    input.addEventListener("keydown", function (e) {
        var opts = options();
        if (e.key === "Escape") {
            close();
        } else if (e.key === "ArrowDown" && opts.length) {
            e.preventDefault();
            setActive(activeIndex < opts.length - 1 ? activeIndex + 1 : 0);
        } else if (e.key === "ArrowUp" && opts.length) {
            e.preventDefault();
            setActive(activeIndex > 0 ? activeIndex - 1 : opts.length - 1);
        } else if (e.key === "Enter" && activeIndex >= 0 && opts[activeIndex]) {
            e.preventDefault();
            choose(opts[activeIndex].textContent);
        }
    });
})();

(function () {
    // Every wide table on the site sits in a .table-scroll box that scrolls
    // horizontally rather than blowing out the page (see style.css) -- but
    // a box that overflows has no built-in hint that it does, and on a long
    // table the browser's own horizontal scrollbar sits at the *bottom* of
    // that box, off-screen until you've already scrolled past every row.
    // This adds a fade plus a small "scroll for more" label right at the
    // top of each table, but only when that table's content is actually
    // wider than its box -- never on a table that already fits.
    var boxes = document.querySelectorAll(".table-scroll");
    var pairs = [];
    boxes.forEach(function (box) {
        var hint = document.createElement("div");
        hint.className = "table-scroll-hint";
        hint.textContent = "scroll for more →";
        box.parentNode.insertBefore(hint, box);
        pairs.push([box, hint]);
    });
    function syncOne(box, hint) {
        // Once scrolled all the way to the trailing edge there's nothing
        // left to hint at -- drop the fade/label there rather than leaving
        // it glued to the edge permanently.
        var moreToRight = box.scrollWidth - box.clientWidth - box.scrollLeft > 1;
        box.classList.toggle("has-overflow-x", moreToRight);
        hint.classList.toggle("visible", moreToRight);
    }
    function sync() {
        pairs.forEach(function (pair) { syncOne(pair[0], pair[1]); });
    }
    pairs.forEach(function (pair) {
        pair[0].addEventListener("scroll", function () { syncOne(pair[0], pair[1]); }, { passive: true });
    });
    sync();
    window.addEventListener("resize", sync);
})();

(function () {
    // Detail-page section tabs (Roster/Planets/Intel + Log) -- every page's
    // panels are real stacked <section>s server-side (so JS-off, print, and
    // Ctrl+F still see everything, same fallback shape as the live-search
    // form elsewhere on the site), progressively enhanced here into a proper
    // tablist: only the active panel stays visible, arrow keys move focus
    // between tabs, and the choice is deep-linkable via location.hash so a
    // link can point straight at e.g. #tab-log.
    document.querySelectorAll('[role="tablist"]').forEach(function (tablist) {
        var tabs = Array.prototype.slice.call(tablist.querySelectorAll('[role="tab"]'));
        if (!tabs.length) return;
        var panels = tabs.map(function (tab) {
            return document.getElementById(tab.getAttribute("aria-controls"));
        });
        // Some tablists (e.g. Movers' Gainers/Crashers/Ratio) are plain
        // navigation links with no in-page panel to swap -- each tab is a
        // full page load to a different query string. Only progressively
        // enhance into a real single-page tablist (preventDefault + panel
        // swap) when every tab actually has a panel to show/hide; otherwise
        // leave the links alone so clicking them navigates normally.
        if (panels.some(function (p) { return !p; })) return;
        function activate(tab, focus) {
            tabs.forEach(function (t, i) {
                var selected = t === tab;
                t.setAttribute("aria-selected", selected ? "true" : "false");
                t.tabIndex = selected ? 0 : -1;
                if (panels[i]) {
                    panels[i].hidden = !selected;
                    // A panel that's `hidden` measures 0x0 -- anything inside
                    // it that sizes itself off its container at build time
                    // (the Trends chart below) needs telling once it's
                    // actually visible and has real dimensions to read.
                    if (selected) panels[i].dispatchEvent(new CustomEvent("btk:panelshown"));
                }
            });
            if (focus) tab.focus();
        }
        var initial = tabs.filter(function (t) {
            return "#" + t.getAttribute("aria-controls") === location.hash;
        })[0] || tabs[0];
        activate(initial, false);
        tabs.forEach(function (tab, i) {
            tab.addEventListener("click", function (e) {
                e.preventDefault();
                activate(tab, false);
                history.replaceState(null, "", "#" + tab.getAttribute("aria-controls"));
            });
            tab.addEventListener("keydown", function (e) {
                if (e.key !== "ArrowRight" && e.key !== "ArrowLeft") return;
                e.preventDefault();
                var next = e.key === "ArrowRight" ? (i + 1) % tabs.length : (i - 1 + tabs.length) % tabs.length;
                activate(tabs[next], true);
            });
        });
    });
})();

(function () {
    var btn = document.getElementById("theme-toggle");
    if (!btn) return;
    var root = document.documentElement;
    function sync() {
        var dark = root.classList.contains("dark");
        btn.setAttribute("aria-label", dark ? "Switch to light mode" : "Switch to dark mode");
        btn.setAttribute("aria-pressed", dark ? "true" : "false");
    }
    btn.addEventListener("click", function () {
        var dark = root.classList.toggle("dark");
        localStorage.setItem("theme", dark ? "dark" : "light");
        sync();
        // The Trends chart (below) draws its line/grid/axis colors onto a
        // <canvas> from the CSS tokens at build time -- unlike everything
        // else on the page, those pixels don't repaint themselves just
        // because --fg/--line changed, so it needs telling explicitly.
        window.dispatchEvent(new Event("btk:themechange"));
    });
    sync();
})();

(function () {
    // Homepage-only: PA ticks hourly, 1 minute past the hour (UTC) -- no
    // server round-trip needed, the schedule is fixed, so this counts down
    // entirely client-side.
    var el = document.getElementById("countdown");
    if (!el) return;
    // The tick spine's fill is the same clock drawn to scale (see .spine-fill
    // in style.css) -- advanced here rather than by its own timer so the
    // number and the bar can never disagree. Rendered server-side too, so it
    // starts in the right place before this runs.
    var track = document.getElementById("tick-track");
    function render() {
        var now = new Date();
        var next = new Date(Date.UTC(
            now.getUTCFullYear(), now.getUTCMonth(), now.getUTCDate(),
            now.getUTCHours(), 1, 0
        ));
        if (next <= now) {
            next.setUTCHours(next.getUTCHours() + 1);
        }
        var s = Math.max(0, Math.floor((next - now) / 1000));
        var h = String(Math.floor(s / 3600)).padStart(2, "0");
        var m = String(Math.floor((s % 3600) / 60)).padStart(2, "0");
        var sec = String(s % 60).padStart(2, "0");
        el.textContent = "T−" + h + ":" + m + ":" + sec;
        if (track) {
            track.style.setProperty("--tick-progress", ((3600 - s) / 36).toFixed(2) + "%");
        }
    }
    render();
    setInterval(render, 1000);
})();

(function () {
    // Homepage-only: the countdown above only ever predicts when a tick
    // *should* land, not whether it actually has -- ingest can lag or skip
    // (see btk-ingest's ticking check). Poll the tick number itself and
    // surface it plainly when it moves, rather than leaving the page
    // quietly stale until a manual reload. Round/tick values come from
    // #status-pill's data attributes (set server-side in index.html) since
    // this file has no access to Jinja template variables.
    var pill = document.getElementById("status-pill");
    if (!pill) return;
    var initialRound = pill.dataset.round ? Number(pill.dataset.round) : null;
    var initialTick = pill.dataset.tick ? Number(pill.dataset.tick) : null;
    if (initialTick === null) return;
    var hint = document.getElementById("new-tick-hint");
    var POLL_MS = 20000;

    function poll() {
        // No point spending a network round-trip on a tab nobody's looking
        // at -- catches up immediately on the visibilitychange listener
        // below instead of waiting out the rest of the interval once the
        // tab comes back.
        if (document.hidden) return;
        fetch("/web/tick-status")
            .then(function (r) { return r.json(); })
            .then(function (data) {
                if (data.round === initialRound && data.tick !== null && data.tick > initialTick) {
                    hint.hidden = false;
                    pill.classList.add("flash");
                    clearInterval(timer);
                }
            })
            .catch(function () {}); // a failed poll just tries again next interval
    }
    var timer = setInterval(poll, POLL_MS);
    document.addEventListener("visibilitychange", function () {
        if (!document.hidden) poll();
    });
})();

(function () {
    // Detail pages' Trends tab -- a full-size Chart.js line chart standing in
    // for the tiny vitals sparklines (those stay as-is; this is a separate,
    // much bigger chart for actually reading tick-by-tick history). Guards on
    // Chart existing since the library is only loaded on the three detail
    // page templates that have this tab, via their own extra_scripts block.
    var canvas = document.getElementById("trend-chart");
    var dataEl = document.getElementById("trend-chart-data");
    if (!canvas || !dataEl || typeof Chart === "undefined") return;

    var series = JSON.parse(dataEl.textContent);
    var chips = Array.prototype.slice.call(document.querySelectorAll(".chart-metric-chip"));
    var root = document.documentElement;
    var chart = null;

    function themeColor(name) {
        return getComputedStyle(root).getPropertyValue(name).trim();
    }

    // Compact axis labels ("2.9M") -- the tooltip below gives the exact
    // figure on hover, so the axis only needs to be scannable at a glance.
    function abbreviate(n) {
        var sign = n < 0 ? "-" : "";
        var abs = Math.abs(n);
        if (abs >= 1e9) return sign + trimZero((abs / 1e9).toFixed(2)) + "B";
        if (abs >= 1e6) return sign + trimZero((abs / 1e6).toFixed(2)) + "M";
        if (abs >= 1e3) return sign + trimZero((abs / 1e3).toFixed(1)) + "K";
        return sign + abs;
    }
    function trimZero(s) {
        return s.replace(/\.0+$/, "");
    }

    // A plain positioned element instead of Chart.js's canvas-drawn tooltip --
    // this one is styled entirely from style.css (site tokens, theme-aware
    // for free), where the line/grid/axis below has to be recolored by hand
    // on every theme change since canvas pixels don't pick up CSS variables.
    var tooltipEl = document.createElement("div");
    tooltipEl.className = "chart-tooltip";
    canvas.parentNode.appendChild(tooltipEl);

    function externalTooltip(context) {
        var tt = context.tooltip;
        if (tt.opacity === 0) {
            tooltipEl.classList.remove("visible");
            return;
        }
        var point = tt.dataPoints && tt.dataPoints[0];
        if (point) {
            tooltipEl.innerHTML =
                '<div class="chart-tooltip-tick">Tick ' + point.label + "</div>" +
                '<div class="chart-tooltip-value">' + point.parsed.y.toLocaleString() + "</div>";
        }
        var box = canvas.parentNode.getBoundingClientRect();
        var canvasBox = canvas.getBoundingClientRect();
        var x = canvasBox.left - box.left + tt.caretX;
        var y = canvasBox.top - box.top + tt.caretY;
        // Flip to the left of the cursor once there's no room on the right,
        // rather than letting the tooltip run off the edge of the chart.
        var flip = x + 140 > box.width;
        tooltipEl.classList.toggle("flip", flip);
        tooltipEl.style.left = x + "px";
        tooltipEl.style.top = y + "px";
        tooltipEl.classList.add("visible");
    }

    function buildConfig(metric) {
        var points = series[metric] || [];
        var fg = themeColor("--fg");
        var line = themeColor("--line");
        var fgFaint = themeColor("--fg-faint");
        var fontFamily = themeColor("--font-data") || "monospace";
        return {
            type: "line",
            data: {
                labels: points.map(function (p) { return p.t; }),
                datasets: [{
                    data: points.map(function (p) { return p.v; }),
                    borderColor: fg,
                    borderWidth: 1.5,
                    pointRadius: 0,
                    pointHitRadius: 8,
                    pointHoverRadius: 3,
                    pointHoverBackgroundColor: fg,
                    pointHoverBorderWidth: 0,
                    tension: 0.15,
                    fill: true,
                    // fg is always a plain #rrggbb site token -- appending a
                    // hex alpha is simplest, rather than reparsing it into rgba().
                    backgroundColor: fg + "12",
                }],
            },
            options: {
                responsive: true,
                maintainAspectRatio: false,
                animation: { duration: 200 },
                interaction: { mode: "index", intersect: false },
                scales: {
                    x: {
                        grid: { display: false },
                        border: { color: line },
                        ticks: { color: fgFaint, font: { family: fontFamily, size: 10 }, maxRotation: 0, maxTicksLimit: 8 },
                    },
                    y: {
                        grid: { color: line },
                        border: { display: false },
                        ticks: { color: fgFaint, font: { family: fontFamily, size: 10 }, maxTicksLimit: 5, callback: abbreviate },
                    },
                },
                plugins: {
                    legend: { display: false },
                    tooltip: { enabled: false, external: externalTooltip },
                },
            },
        };
    }

    function render(metric) {
        var config = buildConfig(metric);
        if (chart) {
            chart.destroy();
        }
        chart = new Chart(canvas, config);
    }

    var current = (chips[0] && chips[0].dataset.metric) || Object.keys(series)[0];

    // Trends starts out as a `hidden` tab panel (Roster/Intel/Planets is the
    // default tab on every page that has this chart), and a hidden element
    // measures 0x0 -- building the chart before its panel is ever shown
    // would permanently lock it to Chart.js's fallback canvas size. Wait for
    // the tabs code above to actually reveal it; build on the first reveal,
    // just resize on any later one (switching tabs away and back).
    var panel = canvas.closest('[role="tabpanel"]');
    if (!panel || !panel.hidden) {
        render(current);
    } else {
        panel.addEventListener("btk:panelshown", function () {
            if (chart) {
                chart.resize();
            } else {
                render(current);
            }
        });
    }

    chips.forEach(function (chip) {
        chip.addEventListener("click", function () {
            if (chip.classList.contains("active")) return;
            chips.forEach(function (c) { c.classList.remove("active"); });
            chip.classList.add("active");
            current = chip.dataset.metric;
            render(current);
        });
    });

    // Re-theming destroys and rebuilds with fresh colors read from the CSS
    // tokens above -- simpler and cheap enough (one chart, a few hundred
    // points) next to hand-patching every color option Chart.js exposes.
    window.addEventListener("btk:themechange", function () { render(current); });
})();
