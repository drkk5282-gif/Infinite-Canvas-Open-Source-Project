(function () {
  const page = document.documentElement.getAttribute("data-app-page") || "home";
  window.APP_PAGE = page;
  window.isAppHomePage = page === "home";
  window.isAppCanvasPage = page === "canvas";
  window.isAppWorkbenchPage = false;

  const ASSET_V = (document.querySelector('script[src*="app-page.js"]')?.src.match(/[?&]v=([^&]+)/) || [])[1] || "b199-mention-dismiss";

  function parseHashRoute(hash) {
    const h = String(hash || "");
    let m = h.match(/^#\/share\/([^/?#]+)/);
    if (m) return { kind: "canvas", share: decodeURIComponent(m[1]) };
    m = h.match(/^#\/b\/([^/?#]+)/);
    if (m) return { kind: "canvas", id: decodeURIComponent(m[1]) };
    return null;
  }

  function parseAppRoute() {
    const q = new URLSearchParams(location.search);
    const fromHash = parseHashRoute(location.hash);
    const share = q.get("share") || fromHash?.share || "";
    const id = q.get("id") || q.get("sessionId") || fromHash?.id || "";
    const episode = q.get("episode") || q.get("e") || fromHash?.episode || "";
    let kind = page;
    if (fromHash?.kind) kind = fromHash.kind;
    else if (share || id) kind = page === "home" ? (fromHash?.kind || page) : page;
    return { kind, id, episode, share };
  }

  function appPageUrl(kind, opts) {
    if (typeof opts === "string") {
      const parsed = parseHashRoute(opts);
      if (parsed) {
        kind = parsed.kind;
        opts = parsed;
      } else {
        opts = {};
      }
    }
    opts = opts || {};
    if (kind === "home") return "/";
    if (kind === "canvas") {
      if (opts.share) return "/canvas?share=" + encodeURIComponent(opts.share);
      if (opts.id) return "/canvas?id=" + encodeURIComponent(opts.id);
      return "/canvas";
    }
    return "/";
  }

  function showNavLoader() {
    const loader = document.getElementById("canvas-enter-loader");
    if (!loader) return;
    loader.classList.remove("hidden", "is-hiding");
    loader.classList.add("is-active");
    loader.setAttribute("aria-busy", "true");
    document.documentElement.classList.add("is-workspace-booting");
  }

  function goAppPage(kind, opts) {
    const url = appPageUrl(kind, opts);
    if (!url) return;
    const here = location.pathname + location.search;
    if (here === url || here === url.replace(/\/$/, "")) {
      return;
    }
    if (kind !== "home") showNavLoader();
    try {
      location.assign(url);
    } catch {
      location.href = url;
    }
  }

  function prefetchUrl(url, as) {
    if (!url || document.querySelector(`link[rel="prefetch"][href="${url}"]`)) return;
    const link = document.createElement("link");
    link.rel = "prefetch";
    if (as) link.as = as;
    link.href = url;
    document.head.appendChild(link);
  }

  function prefetchAppPage(kind) {
    if (kind === "canvas") {
      prefetchUrl("/canvas.html", "document");
      [
        "js/neowow-import.js",
        "js/agent/rules.js",
        "js/agent/parser.js",
        "js/agent/ops.js",
        "js/agent/runner.js",
        "js/agent/api.js",
        "js/agent/intent.js",
        "js/agent/panel.js",
        "js/agent/index.js",
      ].forEach((p) => prefetchUrl("/static/" + p + "?v=" + ASSET_V, "script"));
      return;
    }
  }

  window.parseAppRoute = parseAppRoute;
  window.parseHashRoute = parseHashRoute;
  window.appPageUrl = appPageUrl;
  window.goAppPage = goAppPage;
  window.prefetchAppPage = prefetchAppPage;
  window.showNavLoader = showNavLoader;

  if (page === "home") {
    const idle = window.requestIdleCallback || ((fn) => setTimeout(fn, 800));
    idle(() => {
      prefetchAppPage("canvas");
    });
  }
})();
