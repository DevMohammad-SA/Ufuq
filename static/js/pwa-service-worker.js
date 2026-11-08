/* Only a fixed, public offline notice is cached. Account pages are network-only. */
'use strict';

const CACHE_NAME = 'rahhal-public-offline-v1';
const OFFLINE_URL = '/offline/';
const OWNED_CACHE_PREFIX = 'rahhal-public-offline-';

function isPublicOffline(response) {
  return Boolean(
    response && response.ok && !response.redirected &&
    response.headers.get('X-Rahhal-Offline') === 'public-v1' &&
    /^text\/html(?:\s*;|$)/i.test(response.headers.get('Content-Type') || '')
  );
}

self.addEventListener('install', event => {
  event.waitUntil((async () => {
    const response = await fetch(new Request(OFFLINE_URL, {
      credentials: 'omit',
      cache: 'reload'
    }));
    if (!isPublicOffline(response)) {
      throw new Error('Refusing a non-public offline response');
    }
    const cache = await caches.open(CACHE_NAME);
    await cache.put(OFFLINE_URL, response);
  })());
});

self.addEventListener('activate', event => {
  event.waitUntil((async () => {
    const names = await caches.keys();
    await Promise.all(names.filter(name =>
      name.startsWith(OWNED_CACHE_PREFIX) && name !== CACHE_NAME
    ).map(name => caches.delete(name)));
  })());
});

function emergencyOfflineResponse() {
  return new Response(
    '<!doctype html><html lang="ar" dir="rtl"><meta charset="utf-8">' +
    '<meta name="viewport" content="width=device-width,initial-scale=1">' +
    '<title>رحّال — الاتصال غير متاح</title>' +
    '<body style="margin:24px;background:#F7F3EA;color:#2B2F33;font-family:system-ui,sans-serif;line-height:1.9">' +
    '<h1>الاتصال غير متاح</h1>' +
    '<p>أنت غير متصل بالإنترنت. عرض بياناتك وحفظ التغييرات يحتاجان إلى اتصال. لا تحفظ التغييرات دون اتصال.</p>' +
    '<button type="button" style="min-height:44px" onclick="location.reload()">إعادة المحاولة</button> ' +
    '<a href="/">الصفحة الرئيسية</a></body></html>',
    {status: 503, headers: {
      'Content-Type': 'text/html; charset=utf-8',
      'Cache-Control': 'no-store',
      'X-Content-Type-Options': 'nosniff',
      'X-Rahhal-Offline': 'public-v1'
    }}
  );
}

async function offlineNotice() {
  try {
    // Never use global caches.match: only our cache and this one fixed URL.
    const cache = await caches.open(CACHE_NAME);
    const response = await cache.match(OFFLINE_URL);
    if (isPublicOffline(response)) return response;
  } catch (error) {
    // Cache storage is optional: a static notice is safe even when unavailable.
  }
  return emergencyOfflineResponse();
}

self.addEventListener('fetch', event => {
  const request = event.request;
  if (request.method !== 'GET' || request.mode !== 'navigate') return;
  // Downloads and subresources are not documents. The explicit export paths
  // also protect navigation to the application's existing download endpoints.
  if (request.destination !== 'document') return;
  const url = new URL(request.url);
  if (url.origin !== self.location.origin ||
      url.pathname.startsWith('/media/') ||
      url.pathname.startsWith('/static/') ||
      url.pathname === '/participants/reports/export/' ||
      url.pathname === '/participants/data/export-pdf/') return;

  event.respondWith((async () => {
    try {
      // Preserve the original request, including its authenticated credentials.
      // HTTP errors/redirects are real responses, not offline failures.
      return await fetch(request);
    } catch (error) {
      return offlineNotice();
    }
  })());
});
