/* MineModelingPro — the one definition of the site menu bar.
   Every page renders from here: the front page and the model library load this
   as a plain script, and the Studio pages hand it the session app.js has
   already fetched, so there is no second /api/me round-trip. Pages differ only
   in `base` (path back to the site root) and `active` (which link is lit). */
(function (w, d) {
  function esc(s) {
    return String(s == null ? "" : s).replace(/[&<>"]/g, function (c) {
      return { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c];
    });
  }
  function apiBase() {
    return ((w.MMP_CONFIG && w.MMP_CONFIG.API_BASE) || "/api").replace(/\/$/, "");
  }

  function markup(base, active, user) {
    function link(href, label, key, cls) {
      var c = (key && key === active) ? "active" : "";
      if (cls) c = c ? c + " " + cls : cls;
      return '<a href="' + base + href + '"' + (c ? ' class="' + c + '"' : "") + ">" + label + "</a>";
    }
    var items = [];
    if (user) items.push(link("app/dashboard.html", "My projects", "dashboard"));
    items.push(link("models.html", "Model library", "models"));
    if (user) {
      if (user.role === "admin") items.push(link("app/admin.html", "Accounts", "admin"));
      items.push(link("app/account.html", esc(user.email), "account"));
      items.push('<a href="#" id="mmp-signout">Sign out</a>');
    } else {
      items.push(link("app/index.html", "Sign in", "signin", "signin"));
    }
    return '<div class="topwrap">' +
      '<a class="brand" href="' + base + 'index.html">' +
      '<span class="name">Mine<b>Modeling</b>Pro</span>' +
      '<span class="sub">Drillhole &amp; 43-101 Modelling</span></a>' +
      '<nav class="menu">' + items.join("") + "</nav></div>";
  }

  function signOut(base) {
    var go = function () { w.location.href = base + "index.html"; };
    try {
      fetch(apiBase() + "/auth/logout", { method: "POST", credentials: "same-origin" }).then(go, go);
    } catch (e) { go(); }
  }

  function paint(bar, base, active, user) {
    bar.innerHTML = markup(base, active, user);
    var so = d.getElementById("mmp-signout");
    if (so) so.onclick = function (e) { e.preventDefault(); signOut(base); };
  }

  /* render({base, active, user, el}) — pass `user` (an object or null) if the
     page already knows the session; leave it out and the bar paints signed-out
     and corrects itself when /api/me answers. */
  function render(opts) {
    opts = opts || {};
    var base = opts.base == null ? "" : opts.base;
    var active = (opts.active || "").replace(/\.html$/, "");
    var bar = opts.el || d.getElementById("topbar") || d.querySelector(".topbar");
    if (!bar) return Promise.resolve(null);
    if (bar.id !== "topbar") bar.id = "topbar";
    if (opts.user !== undefined) { paint(bar, base, active, opts.user); return Promise.resolve(opts.user); }
    paint(bar, base, active, null);
    return fetch(apiBase() + "/me", { credentials: "same-origin" })
      .then(function (r) { return r.json(); })
      .then(function (j) { var u = j && j.user; if (u) paint(bar, base, active, u); return u || null; })
      .catch(function () { return null; });
  }

  w.MMPNav = { render: render, markup: markup };
})(window, document);
