(() => {
  const sidebar = document.querySelector('.docs-sidebar');
  const disclosure = sidebar?.querySelector('.sidebar-disclosure');
  if (!sidebar || !disclosure) return;

  const storageKey = 'quality-graph:sidebar:v1';

  const updateEdges = () => {
    const remaining = sidebar.scrollHeight - sidebar.clientHeight - sidebar.scrollTop;
    sidebar.style.setProperty('--sidebar-fade-top', `${sidebar.scrollTop <= 1 ? 0 : Math.min(32, sidebar.scrollTop)}px`);
    sidebar.style.setProperty('--sidebar-fade-bottom', `${remaining <= 1 ? 0 : Math.min(32, remaining)}px`);
  };

  const restore = () => {
    try {
      const state = JSON.parse(sessionStorage.getItem(storageKey));
      if (!state || typeof state !== 'object') return;
      if (typeof state.open === 'boolean') disclosure.open = state.open;
      if (Number.isFinite(state.scrollTop) && state.scrollTop >= 0) {
        sidebar.scrollTop = state.scrollTop;
      }
    } catch {
      return;
    }
  };

  const save = () => {
    updateEdges();
    try {
      sessionStorage.setItem(storageKey, JSON.stringify({
        open: disclosure.open,
        scrollTop: sidebar.scrollTop,
      }));
    } catch {
      return;
    }
  };

  restore();
  updateEdges();
  new ResizeObserver(updateEdges).observe(sidebar);
  sidebar.addEventListener('scroll', save, { passive: true });
  disclosure.addEventListener('toggle', save);
  sidebar.addEventListener('click', save, { capture: true });
  window.addEventListener('pagehide', save);
  window.addEventListener('pageshow', () => {
    restore();
    updateEdges();
  });
})();
