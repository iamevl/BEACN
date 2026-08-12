(() => {
  'use strict';

  const STORAGE_KEY = 'beacn.appearance';
  const MEDIA_QUERY = '(prefers-color-scheme: dark)';
  const VALID_APPEARANCES = new Set(['dark', 'light', 'system']);
  const root = document.documentElement;

  let mediaQuery = null;
  let appearance = normalise(root.dataset.appearance);
  let effectiveTheme = validTheme(root.dataset.theme)
    ? root.dataset.theme
    : resolveTheme(appearance);

  function normalise(value) {
    return VALID_APPEARANCES.has(value) ? value : 'dark';
  }

  function validTheme(value) {
    return value === 'dark' || value === 'light';
  }

  function getMediaQuery() {
    if (mediaQuery) {
      return mediaQuery;
    }

    try {
      mediaQuery = window.matchMedia(MEDIA_QUERY);
    } catch (_error) {
      mediaQuery = {
        matches: true,
        addEventListener() {},
      };
    }

    return mediaQuery;
  }

  function resolveTheme(value) {
    const preference = normalise(value);

    if (preference !== 'system') {
      return preference;
    }

    return getMediaQuery().matches ? 'dark' : 'light';
  }

  function updateThemeColour(theme) {
    document.querySelectorAll('meta[name="theme-color"]').forEach(meta => {
      meta.setAttribute(
        'content',
        theme === 'light' ? '#F8FAFC' : '#0F172A'
      );
    });
  }

  function dispatchChange() {
    document.dispatchEvent(new CustomEvent('beacn:themechange', {
      detail: {
        appearance,
        preference: appearance,
        theme: effectiveTheme,
        effectiveTheme,
      },
    }));
  }

  function apply(value, options = {}) {
    const nextAppearance = normalise(value);
    const nextTheme = resolveTheme(nextAppearance);
    const changed = (
      appearance !== nextAppearance ||
      effectiveTheme !== nextTheme ||
      root.dataset.appearance !== nextAppearance ||
      root.dataset.theme !== nextTheme
    );

    appearance = nextAppearance;
    effectiveTheme = nextTheme;
    root.dataset.appearance = appearance;
    root.dataset.theme = effectiveTheme;
    root.style.colorScheme = effectiveTheme;
    updateThemeColour(effectiveTheme);

    if (options.persist !== false) {
      try {
        window.localStorage.setItem(STORAGE_KEY, appearance);
      } catch (_error) {
        // The in-memory preference remains active for this page.
      }
    }

    if (changed) {
      dispatchChange();
    }

    return {
      appearance,
      preference: appearance,
      theme: effectiveTheme,
      effectiveTheme,
    };
  }

  function readStoredPreference() {
    try {
      return normalise(window.localStorage.getItem(STORAGE_KEY));
    } catch (_error) {
      return 'dark';
    }
  }

  function handleMediaChange() {
    if (appearance === 'system') {
      apply('system', {persist: false});
    }
  }

  function handleStorage(event) {
    if (event.key === STORAGE_KEY) {
      apply(event.newValue, {persist: false});
    }
  }

  const systemPreference = getMediaQuery();

  if (typeof systemPreference.addEventListener === 'function') {
    systemPreference.addEventListener('change', handleMediaChange);
  } else if (typeof systemPreference.addListener === 'function') {
    systemPreference.addListener(handleMediaChange);
  }

  window.addEventListener('storage', handleStorage);

  window.BEACNTheme = Object.freeze({
    storageKey: STORAGE_KEY,
    appearances: Object.freeze(['dark', 'light', 'system']),
    getAppearance: () => appearance,
    getEffectiveTheme: () => effectiveTheme,
    readStoredPreference,
    resolveTheme,
    apply,
  });

  apply(readStoredPreference(), {persist: false});
})();
