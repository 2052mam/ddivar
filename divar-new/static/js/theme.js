(function () {
  const KEY = 'divar_theme';
  function preferred() {
    try {
      const saved = localStorage.getItem(KEY);
      if (saved === 'light' || saved === 'dark') return saved;
    } catch (_) {}
    return 'dark';
  }
  function apply(theme) {
    document.documentElement.setAttribute('data-theme', theme);
    try { localStorage.setItem(KEY, theme); } catch (_) {}
    document.querySelectorAll('[data-theme-toggle]').forEach((btn) => {
      const isLight = theme === 'light';
      btn.setAttribute('aria-pressed', isLight ? 'true' : 'false');
      const ico = btn.querySelector('.theme-ico');
      const lbl = btn.querySelector('.theme-lbl');
      if (ico) ico.textContent = isLight ? '🌙' : '☀️';
      if (lbl) lbl.textContent = isLight ? 'تم تیره' : 'تم روشن';
    });
  }
  function toggle() {
    const cur = document.documentElement.getAttribute('data-theme') || preferred();
    apply(cur === 'light' ? 'dark' : 'light');
  }
  apply(preferred());
  document.addEventListener('click', (e) => {
    const btn = e.target.closest('[data-theme-toggle]');
    if (btn) toggle();
  });

  // منوی همبرگری موبایل — مطمئن و بالاتر از overlay
  function openSide() {
    const side = document.getElementById('sidebar');
    const overlay = document.getElementById('sideOverlay');
    if (side) side.classList.add('open');
    if (overlay) {
      overlay.classList.add('show');
      overlay.setAttribute('aria-hidden', 'false');
    }
    document.documentElement.classList.add('menu-open');
    document.body.classList.add('menu-open');
  }
  function closeSide() {
    const side = document.getElementById('sidebar');
    const overlay = document.getElementById('sideOverlay');
    if (side) side.classList.remove('open');
    if (overlay) {
      overlay.classList.remove('show');
      overlay.setAttribute('aria-hidden', 'true');
    }
    document.documentElement.classList.remove('menu-open');
    document.body.classList.remove('menu-open');
  }
  // اگر کاربر روی لینک داخل سایدبار کلیک کرد، منو بسته شود
  document.addEventListener('click', (e) => {
    const link = e.target.closest('.sidebar .side-link, .sidebar a');
    if (link && window.matchMedia('(max-width:900px)').matches) {
      // کمی تأخیر تا ناوبری انجام شود
      setTimeout(closeSide, 80);
    }
  });
  // Escape
  document.addEventListener('keydown', (e) => {
    if (e.key === 'Escape') closeSide();
  });

  window.openSide = openSide;
  window.closeSide = closeSide;
  window.DivarTheme = { apply, toggle, preferred, openSide, closeSide };
})();
