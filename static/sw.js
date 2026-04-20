const CACHE_NAME = 'skilled-v1';
const ASSETS = [
  '/inventario/movil',
  '/static/css/inventario_movil.css',
  '/static/js/inventario_movil.js',
  '/static/imagenes/pwa-icon.png',
  '/static/imagenes/fondo.png'
];

self.addEventListener('install', event => {
  event.waitUntil(
    caches.open(CACHE_NAME).then(cache => cache.addAll(ASSETS))
  );
});

self.addEventListener('fetch', event => {
  event.respondWith(
    caches.match(event.request).then(response => {
      return response || fetch(event.request);
    })
  );
});
