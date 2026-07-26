(() => {
  const STORAGE_KEY = "chessdb-theme";
  const root = document.documentElement;
  const systemDark = window.matchMedia("(prefers-color-scheme: dark)");
  const validPreferences = new Set(["auto", "light", "dark"]);

  function storedPreference() {
    try {
      const value = localStorage.getItem(STORAGE_KEY) || "auto";
      return validPreferences.has(value) ? value : "auto";
    } catch {
      return "auto";
    }
  }

  function resolvedTheme(preference) {
    return preference === "auto" ? (systemDark.matches ? "dark" : "light") : preference;
  }

  function applyTheme(preference, persist = false) {
    const next = validPreferences.has(preference) ? preference : "auto";
    root.dataset.themePreference = next;
    root.dataset.theme = resolvedTheme(next);
    root.style.colorScheme = root.dataset.theme;
    if (persist) {
      try {
        if (next === "auto") localStorage.removeItem(STORAGE_KEY);
        else localStorage.setItem(STORAGE_KEY, next);
      } catch {
        // Theme switching remains usable when storage is unavailable.
      }
    }
    document.querySelectorAll("[data-theme-choice]").forEach(button => {
      button.setAttribute("aria-pressed", String(button.dataset.themeChoice === next));
    });
  }

  applyTheme(storedPreference());

  document.addEventListener("DOMContentLoaded", () => {
    applyTheme(root.dataset.themePreference || "auto");
    document.querySelectorAll("[data-theme-choice]").forEach(button => {
      button.addEventListener("click", () => applyTheme(button.dataset.themeChoice, true));
    });
  });

  const handleSystemThemeChange = () => {
    if ((root.dataset.themePreference || "auto") === "auto") applyTheme("auto");
  };
  if (typeof systemDark.addEventListener === "function") {
    systemDark.addEventListener("change", handleSystemThemeChange);
  } else {
    systemDark.addListener(handleSystemThemeChange);
  }

  window.addEventListener("storage", event => {
    if (event.key === STORAGE_KEY) applyTheme(storedPreference());
  });
})();
