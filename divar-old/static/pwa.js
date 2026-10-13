/* PWA install + Web Push — بدون چشمک بین صفحات */
(function () {
  const LS_INSTALLED = 'pwa_installed';
  const LS_PUSH = 'pwa_push';
  const LS_GATE_OK = 'pwa_gate_ok';
  const LS_SKIP = 'pwa_gate_skip';

  function isStandalone() {
    return (
      window.matchMedia('(display-mode: standalone)').matches ||
      window.matchMedia('(display-mode: minimal-ui)').matches ||
      window.navigator.standalone === true
    );
  }

  function isInstalled() {
    if (isStandalone()) {
      try { localStorage.setItem(LS_INSTALLED, '1'); } catch (_) {}
      return true;
    }
    try { return localStorage.getItem(LS_INSTALLED) === '1'; } catch (_) { return false; }
  }

  function notifGranted() {
    return typeof Notification !== 'undefined' && Notification.permission === 'granted';
  }

  function gateAlreadyOk() {
    try {
      if (localStorage.getItem(LS_GATE_OK) === '1') return true;
      if (localStorage.getItem(LS_SKIP) === '1') return true;
    } catch (_) {}
    return false;
  }

  function markGateOk() {
    try { localStorage.setItem(LS_GATE_OK, '1'); } catch (_) {}
  }

  let deferredPrompt = null;
  function $(id) { return document.getElementById(id); }

  function ensureOverlay() {
    if ($('pwaInstallOverlay')) return $('pwaInstallOverlay');
    const el = document.createElement('div');
    el.id = 'pwaInstallOverlay';
    el.className = 'hidden'; // مهم: از اول مخفی تا چشمک نزند
    el.setAttribute('aria-hidden', 'true');
    el.innerHTML = `
      <div class="pwa-sheet" role="dialog" aria-modal="true">
        <div class="pwa-icon">📡</div>
        <h2>نصب دیده‌بان دیوار</h2>
        <p>برای دریافت به‌موقع آگهی‌ها، اپ را نصب کنید و نوتیفیکیشن را فعال کنید.</p>
        <ul class="pwa-steps">
          <li>۱) نصب روی صفحه اصلی / دسکتاپ</li>
          <li>۲) اجازه نوتیفیکیشن</li>
          <li>۳) استفاده از جستجو و مانیتور</li>
        </ul>
        <div id="pwaIosHelp" class="pwa-ios" style="display:none;">
          <strong>آیفون / سافاری:</strong>
          Share ← Add to Home Screen / افزودن به صفحه اصلی
        </div>
        <button type="button" id="pwaInstallBtn" class="pwa-btn primary">نصب اپلیکیشن</button>
        <button type="button" id="pwaNotifBtn" class="pwa-btn">فعال‌سازی نوتیفیکیشن</button>
        <button type="button" id="pwaContinueBtn" class="pwa-btn ghost">الان نه — ادامه</button>
        <p id="pwaStatus" class="pwa-status"></p>
      </div>`;
    document.body.appendChild(el);

    if (!$('pwaGateStyle')) {
      const style = document.createElement('style');
      style.id = 'pwaGateStyle';
      style.textContent = `
        #pwaInstallOverlay{
          position:fixed;inset:0;z-index:99999;display:flex;align-items:center;justify-content:center;
          background:rgba(7,10,18,.78);backdrop-filter:blur(8px);padding:16px;
        }
        #pwaInstallOverlay.hidden{display:none!important}
        .pwa-sheet{
          width:min(420px,100%);background:var(--panel,#121826);color:var(--text,#E8EDF7);
          border:1px solid var(--line,rgba(148,163,184,.15));border-radius:20px;padding:22px 20px;
          box-shadow:0 24px 60px rgba(0,0,0,.45);font-family:Vazirmatn,Tahoma,sans-serif;text-align:center;
        }
        .pwa-icon{font-size:2rem;margin-bottom:8px}
        .pwa-sheet h2{margin:0 0 8px;font-size:1.15rem}
        .pwa-sheet p{margin:0 0 12px;font-size:.88rem;color:var(--text-2,#9AA6BF);line-height:1.7}
        .pwa-steps{text-align:right;list-style:none;padding:0;margin:0 0 14px;font-size:.82rem;color:var(--text,#E8EDF7)}
        .pwa-steps li{margin:6px 0}
        .pwa-ios{background:rgba(245,185,66,.12);color:#F5B942;border-radius:12px;padding:10px;font-size:.8rem;margin-bottom:12px;text-align:right;line-height:1.6}
        .pwa-btn{width:100%;border:none;border-radius:12px;padding:12px;margin-top:8px;font-weight:700;cursor:pointer;font-family:inherit;font-size:.9rem}
        .pwa-btn.primary{background:linear-gradient(135deg,#2EE6A6,#1BC48A);color:#04140f}
        .pwa-btn.ghost{background:transparent;color:var(--text-3,#6B778F);border:1px solid var(--line,rgba(148,163,184,.2))}
        .pwa-status{min-height:1.2em;margin-top:10px;font-size:.8rem;color:#2EE6A6;font-weight:600}
      `;
      document.head.appendChild(style);
    }

    $('pwaInstallBtn').addEventListener('click', onInstallClick);
    $('pwaNotifBtn').addEventListener('click', () => enableNotifications());
    $('pwaContinueBtn').addEventListener('click', () => {
      try { localStorage.setItem(LS_SKIP, '1'); } catch (_) {}
      hideOverlay();
    });
    return el;
  }

  function hideOverlay() {
    const overlay = $('pwaInstallOverlay');
    if (!overlay) return;
    overlay.classList.add('hidden');
    overlay.setAttribute('aria-hidden', 'true');
  }

  function showOverlay() {
    const overlay = ensureOverlay();
    overlay.classList.remove('hidden');
    overlay.setAttribute('aria-hidden', 'false');
  }

  function setStatus(t) {
    ensureOverlay();
    const s = $('pwaStatus');
    if (s) s.textContent = t || '';
  }

  function isIos() {
    return /iphone|ipad|ipod/i.test(navigator.userAgent);
  }

  function refreshGate() {
    // اگر قبلاً کامل شده یا کاربر رد کرده → هیچ overlay ساخته/نشان داده نشود
    if (isInstalled() && notifGranted()) {
      markGateOk();
      hideOverlay();
      return true;
    }
    if (gateAlreadyOk()) {
      hideOverlay();
      return true;
    }
    // در حالت standalone فقط اگر نوتیف نداده، یک‌بار ملایم نشان بده
    if (isInstalled()) {
      if (notifGranted()) {
        markGateOk();
        hideOverlay();
        return true;
      }
      // نصب شده، نوتیف نه — فقط اگر skip نکرده
      if (localStorage.getItem(LS_SKIP) === '1') {
        hideOverlay();
        return true;
      }
      showOverlay();
      if (isIos()) {
        $('pwaIosHelp').style.display = 'block';
        $('pwaInstallBtn').style.display = 'none';
      }
      setStatus('اپ نصب است — برای اعلان لحظه‌ای نوتیفیکیشن را فعال کنید');
      return false;
    }

    // نصب نشده
    if (localStorage.getItem(LS_SKIP) === '1') {
      hideOverlay();
      return true;
    }
    showOverlay();
    if (isIos()) {
      $('pwaIosHelp').style.display = 'block';
      $('pwaInstallBtn').style.display = 'none';
    }
    if (!deferredPrompt && !isIos()) {
      setStatus('از منوی مرورگر گزینه Install app را بزنید یا دکمه نصب را امتحان کنید');
    }
    return false;
  }

  async function onInstallClick() {
    if (deferredPrompt) {
      deferredPrompt.prompt();
      const choice = await deferredPrompt.userChoice;
      deferredPrompt = null;
      if (choice && choice.outcome === 'accepted') {
        try { localStorage.setItem(LS_INSTALLED, '1'); } catch (_) {}
        setStatus('نصب شد. الان نوتیفیکیشن را فعال کنید.');
        await enableNotifications();
        refreshGate();
      } else {
        setStatus('نصب انجام نشد. دوباره تلاش کنید.');
      }
      return;
    }
    if (isIos()) {
      setStatus('از Share → Add to Home Screen استفاده کنید');
      return;
    }
    setStatus('مرورگر دکمه نصب نداده — از منوی مرورگر Install را بزنید');
  }

  async function registerSW() {
    if (!('serviceWorker' in navigator)) return null;
    try {
      // پاکسازی کش‌های قدیمی که HTML را ذخیره می‌کردند
      try {
        const keys = await caches.keys();
        await Promise.all(
          keys
            .filter((k) => k === 'divar-monitor-v1' || k === 'divar-monitor-v2')
            .map((k) => caches.delete(k))
        );
      } catch (_) {}

      const reg = await navigator.serviceWorker.register('/sw.js?v=3', { scope: '/' });
      try {
        if (reg.update) await reg.update();
      } catch (_) {}
      return reg;
    } catch (e) {
      console.warn('SW register failed', e);
      return null;
    }
  }

  async function enableNotifications() {
    if (!('Notification' in window)) {
      setStatus('این مرورگر نوتیفیکیشن را پشتیبانی نمی‌کند');
      return;
    }
    const perm = await Notification.requestPermission();
    if (perm !== 'granted') {
      setStatus('اجازه نوتیفیکیشن داده نشد');
      refreshGate();
      return;
    }
    setStatus('نوتیفیکیشن فعال شد');
    try { localStorage.setItem(LS_PUSH, '1'); } catch (_) {}
    const reg = await registerSW();
    if (reg) await subscribePush(reg);
    markGateOk();
    refreshGate();
    try {
      reg && reg.showNotification('دیده‌بان دیوار', {
        body: 'نوتیفیکیشن با موفقیت فعال شد',
        icon: '/static/icons/icon-192.png',
        dir: 'rtl'
      });
    } catch (_) {}
  }

  async function subscribePush(reg) {
    try {
      // اگر سابسکریپشن قدیمی هست، اول چک کن
      let sub = await reg.pushManager.getSubscription();
      const res = await fetch('/api/push/vapid-public', { credentials: 'same-origin' });
      const data = await res.json();
      if (!data.success || !data.publicKey) return;
      const key = urlBase64ToUint8Array(data.publicKey);
      if (!sub) {
        sub = await reg.pushManager.subscribe({
          userVisibleOnly: true,
          applicationServerKey: key
        });
      }
      await fetch('/api/push/subscribe', {
        method: 'POST',
        credentials: 'same-origin',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(sub.toJSON())
      });
      try { localStorage.setItem(LS_PUSH, '1'); } catch (_) {}
    } catch (e) {
      console.warn('push subscribe failed', e);
    }
  }

  async function resetPushSubscription() {
    try {
      if ('serviceWorker' in navigator) {
        const reg = await navigator.serviceWorker.ready;
        const sub = await reg.pushManager.getSubscription();
        if (sub) await sub.unsubscribe();
      }
      await fetch('/api/push/reset', { method: 'POST', credentials: 'same-origin' });
      await enableNotifications();
    } catch (e) {
      console.warn('reset push failed', e);
    }
  }

  function urlBase64ToUint8Array(base64String) {
    const padding = '='.repeat((4 - base64String.length % 4) % 4);
    const base64 = (base64String + padding).replace(/-/g, '+').replace(/_/g, '/');
    const raw = atob(base64);
    const out = new Uint8Array(raw.length);
    for (let i = 0; i < raw.length; ++i) out[i] = raw.charCodeAt(i);
    return out;
  }

  window.addEventListener('beforeinstallprompt', (e) => {
    e.preventDefault();
    deferredPrompt = e;
    // فقط اگر هنوز نصب/رد نشده، وضعیت را به‌روز کن — overlay را به‌زور نشان نده
    if (!isInstalled() && localStorage.getItem(LS_SKIP) !== '1') {
      ensureOverlay();
      setStatus('آماده نصب — دکمه نصب را بزنید');
    }
  });

  window.addEventListener('appinstalled', () => {
    try { localStorage.setItem(LS_INSTALLED, '1'); } catch (_) {}
    setStatus('نصب با موفقیت انجام شد');
    if (!notifGranted()) enableNotifications();
    else { markGateOk(); hideOverlay(); }
  });

  // API عمومی
  window.DivarPWA = {
    enableNotifications,
    refreshGate,
    isInstalled,
    resetPushSubscription,
    subscribePush,
  };

  // ثبت SW و در صورت نیاز تجدید سابسکریپشن پوش
  if ('serviceWorker' in navigator) {
    registerSW().then(async (reg) => {
      if (reg && notifGranted()) {
        try { await subscribePush(reg); } catch (_) {}
      }
    });
  }

  // تشخیص سریع standalone قبل از paint UI
  if (isStandalone()) {
    try { localStorage.setItem(LS_INSTALLED, '1'); } catch (_) {}
    if (notifGranted()) markGateOk();
  }

  // فقط اگر واقعاً لازم است gate را چک کن — با تأخیر کوتاه تا layout نپرد
  function boot() {
    if (gateAlreadyOk() || (isInstalled() && notifGranted())) {
      // اصلاً overlay نساز
      return;
    }
    refreshGate();
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', () => setTimeout(boot, 50));
  } else {
    setTimeout(boot, 50);
  }
})();
