const CACHE_NAME='freeonlinetools-v4';
const ASSETS=['/','/index.html','/css/style.css','/manifest.json'];

self.addEventListener('install',e=>{e.waitUntil(caches.open(CACHE_NAME).then(c=>c.addAll(ASSETS)).then(()=>self.skipWaiting()))});
self.addEventListener('activate',e=>{e.waitUntil(caches.keys().then(keys=>Promise.all(keys.filter(k=>k!==CACHE_NAME).map(k=>caches.delete(k)))).then(()=>self.clients.claim()))});
self.addEventListener('fetch',e=>{e.respondWith(caches.match(e.request).then(r=>r||fetch(e.request).then(resp=>{if(resp.status===200&&resp.type==='basic'){const clone=resp.clone();caches.open(CACHE_NAME).then(c=>c.put(e.request,clone))}return resp}).catch(()=>caches.match('/index.html'))))});
