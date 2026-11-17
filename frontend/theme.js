/* Presentation only: no backend writes, permissions or machine actions. */
(() => {
  const key = 'templatecutting.theme';
  const root = document.documentElement;
  let theme = 'dark';
  try { if (localStorage.getItem(key) === 'light') theme = 'light'; } catch (_) {}
  function render() {
    root.dataset.theme = theme;
    root.style.colorScheme = theme;
    document.querySelectorAll('[data-theme-toggle]').forEach(button => {
      const next = theme === 'dark' ? 'light' : 'dark';
      button.textContent = '';
      button.innerHTML = next === 'light'
        ? '<svg viewBox="0 0 24 24" width="20" height="20" fill="none" stroke="currentColor" stroke-width="1.7" stroke-linecap="round" aria-hidden="true"><circle cx="12" cy="12" r="4"/><path d="M12 2v2m0 16v2M2 12h2m16 0h2M5 5l1.4 1.4m11.2 11.2L19 19M5 19l1.4-1.4M17.6 6.4L19 5"/></svg>'
        : '<svg viewBox="0 0 24 24" width="20" height="20" fill="none" stroke="currentColor" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M20.4 13A8.5 8.5 0 0 1 11 3.6 8.5 8.5 0 1 0 20.4 13Z"/></svg>';
      button.setAttribute('aria-label', 'Switch to ' + next + ' mode');
      button.setAttribute('aria-pressed', String(theme === 'light'));
      button.title = 'Current appearance: ' + theme;
    });
  }
  render();
  document.addEventListener('DOMContentLoaded', () => {
    render();
    document.querySelectorAll('[data-theme-toggle]').forEach(button => {
      button.addEventListener('click', () => {
        theme = theme === 'dark' ? 'light' : 'dark';
        try { localStorage.setItem(key, theme); } catch (_) {}
        render();
        window.dispatchEvent(new Event('themechange'));
      });
    });
  });
})();
