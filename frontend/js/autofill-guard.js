(() => {
  const SKIP_TYPES = new Set(["hidden", "file", "checkbox", "radio", "range", "button", "submit", "reset", "image"]);

  function isTextLikeInput(el) {
    if (!el || el.tagName !== "INPUT") return false;
    const t = (el.type || "text").toLowerCase();
    return t === "text" || t === "search" || t === "email" || t === "tel" || t === "url" || t === "password" || t === "number";
  }

  function guardField(el) {
    if (!el) return;
    const tag = el.tagName;
    if (tag !== "INPUT" && tag !== "TEXTAREA" && tag !== "SELECT") return;
    if (tag === "INPUT" && SKIP_TYPES.has((el.type || "").toLowerCase())) return;

    const isApiKey =
      el.classList?.contains("api-key-input") ||
      /api[-_]?key/i.test(el.id || "") ||
      /api[-_]?key/i.test(el.name || "");

    if (isApiKey) {
      // Never use type=password — browsers treat it as login and offer strong-password UI.
      if ((el.type || "").toLowerCase() === "password") el.type = "text";
      el.setAttribute("autocomplete", "off");
      el.setAttribute("name", el.getAttribute("name") || "api-key-token");
    } else if (tag === "INPUT" && el.type === "password") {
      el.setAttribute("autocomplete", "new-password");
    } else {
      el.setAttribute("autocomplete", "off");
    }
    el.setAttribute("data-lpignore", "true");
    el.setAttribute("data-1p-ignore", "true");
    el.setAttribute("data-bwignore", "true");
    el.setAttribute("data-form-type", "other");
    if (isTextLikeInput(el) || tag === "TEXTAREA" || isApiKey) {
      el.setAttribute("spellcheck", "false");
      el.setAttribute("autocapitalize", "off");
      el.setAttribute("autocorrect", "off");
    }
  }

  function guardLoginField(el) {
    guardField(el);
    if (!el) return;
    const unlock = () => el.removeAttribute("readonly");
    el.addEventListener("mousedown", unlock, { once: false });
    el.addEventListener("focus", unlock, { once: false });
  }

  function guardForm(form) {
    if (!form) return;
    form.setAttribute("autocomplete", "off");
    form.querySelectorAll("input, textarea, select").forEach(guardField);
    form.querySelectorAll('input[type="text"], input[type="password"], input[type="search"], input[type="email"]').forEach(guardLoginField);
  }

  function guardTree(root) {
    if (!root || root.nodeType !== 1) return;
    if (root.matches?.("form")) guardForm(root);
    root.querySelectorAll?.("form").forEach(guardForm);
    root.querySelectorAll?.("input, textarea, select").forEach(guardField);
    ["login-gate-user", "login-gate-pass", "login-user", "login-pass"].forEach((id) => {
      const el = document.getElementById(id);
      if (el && (root === document || root.contains?.(el))) guardLoginField(el);
    });
  }

  window.applyAutofillGuard = guardTree;

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", () => guardTree(document));
  } else {
    guardTree(document);
  }

  const observer = new MutationObserver((mutations) => {
    for (const m of mutations) {
      m.addedNodes.forEach((node) => {
        if (node.nodeType === 1) guardTree(node);
      });
    }
  });
  observer.observe(document.documentElement, { childList: true, subtree: true });
})();
