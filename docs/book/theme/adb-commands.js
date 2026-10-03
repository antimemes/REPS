// Command settings for the ADB user guide.
//
// Every runnable command is authored in the canonical local form `nix run .#<name> …`.
// This rewrites those occurrences to match the reader's setup, controlled from a gear
// menu in the toolbar and persisted in localStorage. In-page links to
// #adb-cmd-settings open the same menu.
//
// NOTE: the rewriting functions (ref/flakelessHead/rewriteLine/rewrite) are kept
// pure and DOM-free on purpose.
(function () {
  "use strict";

  var KEY = "adb-cmd-prefs";
  var GITHUB = "github:antimemetics-institute/agentdatabank";
  var TARBALL = "https://github.com/antimemetics-institute/agentdatabank/archive/main.tar.gz";
  // mode:"nix-build" by default — commands must work on a stock Nix install with
  // zero configuration, and the exec one-liner is the only form that does.
  // flakes:false likewise (armor flag until the reader enables them globally);
  // nixRun:false (the wrapped nix-shell form works without anything installed).
  // latest:true — a stale cached main.tar.gz silently running old code is worse
  // than a redundant HEAD check.
  var DEFAULTS = {
    // mode tabs: "nix-build" (stock-nix $(nix-build …)/exec one-liner), "flakes"
    // (nix run, armored until flakes are global), "nix-run" (classic runner;
    // nixRun = installed globally, else nix-shell-wrapped)
    mode: "nix-build",
    source: "github", flakes: false, registry: false, nixRun: false,
    // github source only: bypass Nix's download cache so a rerun picks up new
    // commits on main (tarball-ttl 0 / --refresh). Inert for a local checkout.
    latest: true,
  };

  function load() {
    try { return Object.assign({}, DEFAULTS, JSON.parse(localStorage.getItem(KEY) || "{}")); }
    catch (e) { return Object.assign({}, DEFAULTS); }
  }
  function save(p) { try { localStorage.setItem(KEY, JSON.stringify(p)); } catch (e) {} }

  // --- command rewriting -------------------------------------------------------------

  var ARMOR = " --extra-experimental-features 'nix-command flakes'";

  function ref(p) {
    if (p.source === "local") return ".";
    return p.registry ? "adb" : GITHUB;
  }
  // flakeless heads (running/nix.md), two flavors:
  //  nix-run:   `nix-run <src> -A experiment-<name> -- args` — resolves
  //             meta.mainProgram itself, takes program args after `--` (which our
  //             commands already carry, so only the head token changes)
  //  nix-build: `$(nix-build --no-out-link [<src>] -A exec.<name>) args` — the
  //             exec.* output IS the executable, so the `--` separator is dropped
  // The tarball URL is long enough to push the -A target off-screen, so the
  // github-source forms continue onto a fresh line before it (backslash-newline
  // holds inside $(…) and inside the nix-shell --run double quotes alike).
  function flakelessHead(cmd, p) {
    // "always fetch latest" spellings: nix-build takes the setting as a flag;
    // nix-run only documents --option. Only the github tarball is ever cached.
    var fresh = p.source === "github" && p.latest;
    if (p.mode === "nix-build") {
      cmd = cmd.replace(/^nix run \.#(\S+)/, function (_, name) {
        var ttl = fresh ? " --tarball-ttl 0" : "";
        var src = p.source === "local" ? " " : " " + TARBALL + " \\\n  ";
        return "$(nix-build --no-out-link" + ttl + src + "-A exec." + name + ")";
      });
      return cmd.replace(/(-A exec\.\S+\)) --(?=\s|$)/, "$1");
    }
    return cmd.replace(/^nix run \.#(\S+)/, function (_, name) {
      var pkg = name.indexOf("adb-") === 0 ? name : "experiment-" + name;
      var ttl = fresh ? "--option tarball-ttl 0 " : "";
      var src = p.source === "local" ? ". " : TARBALL + " \\\n  ";
      return "nix-run " + ttl + src + "-A " + pkg;
    });
  }
  function rewriteLine(line, p) {
    var idx = line.indexOf("nix run .#");
    if (idx === -1) return line;
    var before = line.slice(0, idx), cmd = line.slice(idx);
    if (p.mode === "nix-build" || p.mode === "nix-run") return before + flakelessHead(cmd, p);
    cmd = cmd.replace(/^nix run \.#(\S+)/, function (_, name) {
      var head = "nix run " + ref(p) + "#" + name;
      // the flags trail the installable (before any `--`), so the command reads
      // action-first: `nix run adb#inspect-hello --extra-experimental-features … -- …`
      if (!p.flakes) head += ARMOR;
      if (p.source === "github" && p.latest) head += " --refresh";
      return head;
    });
    return before + cmd;
  }
  // The `--run "…"` payload is read twice: the shell you paste into resolves the
  // double-quoted string, then nix-shell hands the result to another shell to run.
  // So everything the outer pass would eat ($ ` " \) has to be escaped for the
  // inner command to arrive intact. The one exception is a backslash that continues
  // a line: it stays live, and the outer pass folding the span back into one line
  // is exactly what the inner shell wants.
  var CONTINUATION = /\\\s*$/;
  function dqEscape(text) {
    return text.split("\n").map(function (line) {
      var m = CONTINUATION.exec(line);
      var cut = m ? m.index : line.length;
      return line.slice(0, cut).replace(/[$`"\\]/g, "\\$&") + line.slice(cut);
    }).join("\n");
  }
  // Block-level pass: commands span multiple lines via trailing backslashes, and
  // the no-nix-run-installed form must wrap the WHOLE span in
  //   nix-shell -p nix-run --run "…"
  // with the span dq-escaped so the nesting is paste-safe.
  function rewrite(text, p) {
    var lines = text.split("\n");
    var out = [];
    for (var i = 0; i < lines.length; i++) {
      var line = lines[i];
      var idx = line.indexOf("nix run .#");
      if (idx === -1) { out.push(line); continue; }
      var span = [line];
      while (/\\\s*$/.test(span[span.length - 1]) && i + 1 < lines.length) {
        i++;
        span.push(lines[i]);
      }
      var before = line.slice(0, idx);
      var head = rewriteLine(line, p);
      var tail = span.slice(1);
      if (p.mode === "nix-run" && !p.nixRun) {
        head = before + 'nix-shell -p nix-run --run "' + dqEscape(head.slice(before.length));
        tail = tail.map(dqEscape);
        if (tail.length) tail[tail.length - 1] += '"';
        else head += '"';
      }
      out.push(head);
      out.push.apply(out, tail);
    }
    return out.join("\n");
  }
  function preview(p) { return rewrite("nix run .#inspect-hello -- …", p); }

  // Authoring commands always target the local ADB checkout.
  function rewriteLocal(text, p) {
    return rewrite(text, Object.assign({}, p, { source: "local" }));
  }

  function collectBlocks() {
    var blocks = [];
    document.querySelectorAll("pre > code").forEach(function (code) {
      var orig = code.getAttribute("data-adb-orig");
      if (orig === null) {
        orig = code.textContent;
        if (orig.indexOf("nix run .#") === -1) return; // leave highlighting intact
        code.setAttribute("data-adb-orig", orig);
      }
      blocks.push(code);
    });
    return blocks;
  }
  function apply(p) {
    collectBlocks().forEach(function (code) {
      var fn = code.classList.contains("repo-local") ? rewriteLocal : rewrite;
      code.textContent = fn(code.getAttribute("data-adb-orig"), p);
    });
    // prose variants (adb-commands.css): .adb-when-flakes / .adb-when-flakeless
    // divs swap with the With tab, so pages can e.g. drop flake.nix from the
    // story entirely for plain-Nix readers (the no-JS/default state)
    document.documentElement.classList.toggle("adb-flakes", p.mode === "flakes");
  }

  // --- the gear menu -----------------------------------------------------------------

  function el(tag, attrs, kids) {
    var n = document.createElement(tag);
    if (attrs) Object.keys(attrs).forEach(function (k) {
      if (k === "class") n.className = attrs[k]; else if (k === "html") n.innerHTML = attrs[k]; else n.setAttribute(k, attrs[k]);
    });
    (kids || []).forEach(function (k) { n.appendChild(typeof k === "string" ? document.createTextNode(k) : k); });
    return n;
  }
  function seg(name, value, options, onChange) {
    var wrap = el("span", { class: "adb-seg" });
    options.forEach(function (o) {
      var id = "adb-" + name + "-" + o.value;
      var input = el("input", { type: "radio", name: "adb-" + name, id: id });
      input.checked = value === o.value;
      input.addEventListener("change", function () { onChange(o.value); });
      wrap.appendChild(input);
      wrap.appendChild(el("label", { for: id }, [o.label]));
    });
    return wrap;
  }
  function check(labelText, checked, onChange) {
    var input = el("input", { type: "checkbox" });
    input.checked = checked;
    input.addEventListener("change", function () { onChange(input.checked); });
    var lab = el("label", { class: "adb-check" }, []);
    lab.appendChild(input); lab.appendChild(document.createTextNode(" " + labelText));
    return lab;
  }

  function buildPopup(p, previewEl) {
    var pop = el("div", { class: "adb-popup", id: "adb-cmd-popup", role: "menu" });

    function update(patch) { Object.assign(p, patch); save(p); apply(p); previewEl.textContent = preview(p); render(); }

    function render() {
      // re-append previewEl every render: innerHTML = "" detaches it, which is
      // exactly the "preview vanishes after the first toggle" bug
      pop.innerHTML = "";
      pop.appendChild(el("div", { class: "adb-pop-title" }, ["Commands adapt to your setup"]));
      pop.appendChild(previewEl);

      var r1 = el("div", { class: "adb-row" });
      r1.appendChild(el("span", { class: "adb-row-label" }, ["From"]));
      r1.appendChild(seg("source", p.source, [
        { value: "github", label: "GitHub" },
        { value: "local", label: "local checkout (.)" }
      ], function (v) { update({ source: v }); }));
      pop.appendChild(r1);

      if (p.source === "github") {
        // a cached main.tar.gz can lag behind new commits; this adds
        // --tarball-ttl 0 / --refresh so every run re-checks. Inert for a
        // local checkout, so the row hides with it.
        var r4 = el("div", { class: "adb-row" });
        r4.appendChild(check("always fetch latest", p.latest, function (v) { update({ latest: v }); }));
        pop.appendChild(r4);
      }

      // the mode tabs — the rows below are contextual to the tab
      var r0 = el("div", { class: "adb-row" });
      r0.appendChild(el("span", { class: "adb-row-label" }, ["With"]));
      r0.appendChild(seg("mode", p.mode, [
        { value: "nix-build", label: "nix-build" },
        { value: "flakes", label: "flakes" },
        { value: "nix-run", label: "nix-run" }
      ], function (v) { update({ mode: v }); }));
      pop.appendChild(r0);

      if (p.mode === "flakes") {
        // the registry toggle is meaningless for a local checkout; the global-flakes
        // toggle decides whether commands carry the armor flag
        var r2 = el("div", { class: "adb-row" });
        r2.appendChild(check("flakes enabled globally", p.flakes, function (v) { update({ flakes: v }); }));
        if (p.source === "github") {
          r2.appendChild(check("adb registry added", p.registry, function (v) { update({ registry: v }); }));
        }
        pop.appendChild(r2);
      }

      if (p.mode === "nix-run") {
        var r3 = el("div", { class: "adb-row" });
        r3.appendChild(check("nix-run installed globally", p.nixRun, function (v) { update({ nixRun: v }); }));
        pop.appendChild(r3);
      }

      pop.appendChild(el("p", { class: "adb-pop-note" }, [
        "Persists across the guide. See “Working with Nix” for what each toggle means."
      ]));
    }
    render();
    return pop;
  }

  function injectMenu(p) {
    var left = document.querySelector("#mdbook-menu-bar .left-buttons") ||
               document.querySelector(".menu-bar .left-buttons");
    if (!left || document.getElementById("adb-cmd-toggle")) return;

    var btn = el("button", {
      id: "adb-cmd-toggle", class: "icon-button", type: "button",
      title: "Command settings", "aria-label": "Command settings", "aria-haspopup": "true",
      html: '<span class="adb-gear">&#9881;</span>'
    });
    var previewEl = el("code", { class: "adb-menu-preview" }, [preview(p)]);
    var pop = buildPopup(p, previewEl);
    pop.style.display = "none";

    function toggle(show) {
      var open = show === undefined ? pop.style.display === "none" : show;
      if (open) {
        var r = btn.getBoundingClientRect();
        var w = Math.min(window.innerWidth * 0.92, 24 * 16);
        pop.style.top = (r.bottom + 6) + "px";
        pop.style.left = Math.max(6, Math.min(r.left, window.innerWidth - w - 8)) + "px";
      }
      pop.style.display = open ? "block" : "none";
      btn.setAttribute("aria-expanded", open ? "true" : "false");
    }
    btn.addEventListener("click", function (e) { e.stopPropagation(); toggle(); });
    document.addEventListener("click", function (e) {
      if (pop.style.display !== "none" && !pop.contains(e.target) && e.target !== btn) toggle(false);
    });
    // in-page links open the menu too: [⚙ command settings](#adb-cmd-settings)
    document.addEventListener("click", function (e) {
      var a = e.target.closest && e.target.closest('a[href$="#adb-cmd-settings"]');
      if (a) { e.preventDefault(); toggle(true); }
    });

    left.appendChild(btn);
    left.appendChild(pop);
  }

  function init() {
    var p = load();
    apply(p);            // rewrite commands on every page, regardless of the menu
    injectMenu(p);
  }

  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", init);
  else init();
})();
