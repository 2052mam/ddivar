/* Service Worker — دیده‌بان دیوار v3
   قوانین:
   - درخواست‌های navigate / HTML را هرگز intercept نکن (رفع خطای redirect mode)
   - فقط استاتیک را کش کن
   - push و notificationclick همچنان فعال‌اند
*/
const CACHE = 'divar-monitor-v3';
const PRECACHE = [
  '/static/manifest.webmanifest',
  '/static/icons/icon-192.png',
  '/static/icons/icon-512.png',
  '/static/css/app.css',
  '/static/js/theme.js',
  '/static/pwa.js'
];

self.addEventListener('install', (event) => {
  event.waitUntil(
    caches.open(CACHE)
      .then((c) => c.addAll(PRECACHE).catch(() => {}))
      .then(() => self.skipWaiting())
  );
});

self.addEventListener('activate', (event) => {
  event.waitUntil(
    caches.keys().then((keys) =>
      Promise.all(keys.filter((k) => k !== CACHE).map((k) => caches.delete(k)))
    ).then(() => self.clients.claim())
  );
});

self.addEventListener('fetch', (event) => {
  const req = event.request;

  // فقط GET
  if (req.method !== 'GET') return;

  let url;
  try {
    url = new URL(req.url);
  } catch (_) {
    return;
  }

  // فقط same-origin
  if (url.origin !== self.location.origin) return;

  // === مهم: navigation و HTML را دست نزن ===
  // اگر SW ریدایرکت (مثلاً / → /login) را برگرداند، مرورگر این خطا را می‌دهد:
  // "a redirected response was used for a request whose redirect mode is not follow"
  if (req.mode === 'navigate') return;
  const accept = req.headers.get('accept') || '';
  if (accept.includes('text/html')) return;

  // API را هم دست نزن
  if (url.pathname.startsWith('/api/')) return;

  // مسیرهای غیر استاتیک را دست نزن
  if (!url.pathname.startsWith('/static/')) return;

  // استاتیک: شبکه اول، کش پشتیبان
  event.respondWith(
    fetch(req)
      .then((res) => {
        if (res && res.ok && res.type === 'basic') {
          const clone = res.clone();
          caches.open(CACHE).then((c) => c.put(req, clone)).catch(() => {});
        }
        return res;
      })
      .catch(() => caches.match(req).then((c) => c || Response.error()))
  );
});

self.addEventListener('push', (event) => {
  let data = { title: 'دیده‌بان دیوار', body: 'آگهی جدید پیدا شد', url: '/' };
  try {
    if (event.data) {
      const j = event.data.json();
      data = Object.assign(data, j);
    }
  } catch (e) {
    try {
      data.body = event.data.text();
    } catch (_) {}
  }
  event.waitUntil(
    self.registration.showNotification(data.title || 'دیده‌بان دیوار', {
      body: data.body || '',
      icon: '/static/icons/icon-192.png',
      badge: '/static/icons/icon-192.png',
      data: { url: data.url || '/' },
      dir: 'rtl',
      lang: 'fa',
      vibrate: [120, 60, 120],
      tag: data.tag || 'divar-ad',
      renotify: true,
    })
  );
});

self.addEventListener('notificationclick', (event) => {
  event.notification.close();
  const target = (event.notification.data && event.notification.data.url) || '/';
  event.waitUntil(
    clients.matchAll({ type: 'window', includeUncontrolled: true }).then((list) => {
      for (const c of list) {
        if ('focus' in c) {
          try { c.navigate(target); } catch (_) {}
          return c.focus();
        }
      }
      if (clients.openWindow) return clients.openWindow(target);
    })
  );
});
