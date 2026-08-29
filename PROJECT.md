# I❤Tools.pro — Free Online Tools Website

> Monorepo for the I❤Tools.pro free online tools website — 40+ browser-based tools
> with zero backend/server dependencies. Everything runs client-side.

## 📋 Project Overview

| Item | Value |
|------|-------|
| Project name | I❤Tools.pro |
| Live URL | https://ilovetools.pro |
| Fallback URL | https://silent5441.github.io/tools-site/ |
| Owner | Aryan Verma (GitHub: `silent5441`) |
| Purpose | Free online tools website monetized with Google AdSense |
| Hosting | GitHub Pages (free, static-only) |
| Backend | **None** — all tools run client-side in the browser |
| Tech stack | Vanilla HTML + CSS + JavaScript, pdf-lib, pdf.js, JSZip |

## ⚠️ IMPORTANT: Site Policies

These are non-negotiable constraints:

1. **NO server / backend / database** — This is a static GitHub Pages site. Do NOT add
   server-side code. All processing happens in the browser. (A Python/Node backend was
   attempted and removed — it costs money to host and is not viable.)
2. **NO paid dependencies** — Everything must be free. CDNs used:
   - `https://unpkg.com/pdf-lib@1.17.1/dist/pdf-lib.min.js`
   - `https://cdnjs.cloudflare.com/ajax/libs/pdf.js/3.11.174/pdf.min.js`
   - `https://unpkg.com/jszip@3.10.1/dist/jszip.min.js`
   - `https://fonts.googleapis.com/css2?family=Inter...` and `JetBrains+Mono`
3. **AdSense** — Publisher ID `ca-pub-8427860023135459` is loaded on every page.
   Ad slot IDs are ALL placeholders (e.g. `1111111111`) and ads are hidden with
   `.ad-slot{display:none}` in CSS until real ad units are created.
4. **Privacy** — Tools must never upload user files. All file handling must be
   in-browser (FileReader, Blob, URL.createObjectURL).
5. **Language** — English only. No emojis in code comments.

## 🗂 File Structure

```
tools-site/                  ← repo root (served at / by GitHub Pages)
├── index.html               ← homepage: all 40+ tools, 9 categories
├── css/style.css            ← shared styles (light/dark theme)
├── manifest.json            ← PWA manifest
├── sw.js                    ← service worker (app cache)
├── favicon.ico              ← site favicon (purple bg + pink heart)
├── icon-192.png             ← PWA icon
├── icon-512.png             ← PWA icon (also used for og:image)
├── sitemap.xml              ← 43 URLs (homepage + 40 tools + privacy/terms), uses ilovetools.pro
├── robots.txt               ← sitemap reference
├── google2c2c9782d98953a0.html ← Google Search Console verification file
├── pages/
│   ├── privacy.html         ← privacy policy (keep updated with all tools)
│   └── terms.html           ← terms of service
└── tools/                   ← 40 individual tool pages
    ├── merge-pdf.html       ← PDF
    ├── split-pdf.html       ← PDF
    ├── pdf-to-image.html    ← PDF
    ├── image-to-pdf.html    ← PDF
    ├── rotate-pdf.html      ← PDF
    ├── delete-pdf-pages.html← PDF
    ├── pdf-to-text.html     ← PDF
    ├── watermark-pdf.html   ← PDF
    ├── resume-builder.html  ← Resume & CV
    ├── resume-templates.html← Resume & CV
    ├── qr-generator.html     ← Utility
    ├── word-counter.html    ← Text
    ├── case-converter.html  ← Text
    ├── text-remover.html    ← Text
    ├── duplicate-remover.html ← Text
    ├── lorem-ipsum.html     ← Text
    ├── text-diff.html       ← Text
    ├── base64-encode.html   ← Encoding
    ├── url-encode.html      ← Encoding
    ├── hash-generator.html  ← Encoding
    ├── html-encode.html     ← Encoding
    ├── json-formatter.html  ← Developer
    ├── css-minifier.html    ← Developer
    ├── html-minifier.html   ← Developer
    ├── javascript-minifier.html ← Developer
    ├── color-converter.html ← Developer
    ├── regex-tester.html    ← Developer
    ├── bmi-calculator.html  ← Calculators
    ├── age-calculator.html  ← Calculators
    ├── emi-calculator.html  ← Calculators
    ├── gst-calculator.html  ← Calculators
    ├── percentage-calculator.html ← Calculators
    ├── compound-interest.html ← Calculators
    ├── meta-tag-analyzer.html ← SEO
    ├── keyword-density.html ← SEO
    ├── robots-txt-generator.html ← SEO
    ├── sitemap-generator.html  ← SEO
    ├── readability-checker.html ← SEO
    └── serp-preview.html    ← SEO
    ├── brick-estimate.html   ← Construction (Home Easy: brick/cement/sand estimator)
```

## 🧩 Architecture

### How a tool page works

Every tool page follows the same pattern:

1. **Navbar** (identical across pages) — logo, category links, theme toggle,
   install button, hamburger menu. Links use relative paths (`../` for tools/).
2. **Tool section** — tool-specific UI (file upload, textarea, form, etc.)
3. **Footer** — copyright line
4. **Toast** — success/error notifications
5. **Script** — contains these ALWAYS-present functions (do not remove):
   ```js
   (function(){var s=localStorage.getItem('theme');if(s==='dark')document.documentElement.setAttribute('data-theme','dark')})();
   function toggleTheme(){...}
   var deferredPrompt=null;window.addEventListener('beforeinstallprompt',...);
   window.addEventListener('appinstalled',...);
   function installApp(){...}
   if('serviceWorker' in navigator){navigator.serviceWorker.register('../sw.js')...}
   function showToast(msg){document.getElementById('toastMsg').textContent=msg||'Done!';...}
   ```

### Critical: DO NOT modify shared/boilerplate parts

- The navbar theme toggle calls `toggleTheme()` — that function MUST exist on every page.
- The Install button calls `installApp()` — the beforeinstallprompt handler is required.
- Service worker registration uses `../sw.js` from tools pages, `sw.js` from root pages.
- The toast element MUST have exactly this structure:
  ```html
  <div class="toast" id="toast"><svg ...>...</svg> <span id="toastMsg">Done!</span></div>
  ```
  Do NOT nest another `#toastMsg` span inside it (a previous bug did this).

### Library CDN rules

- PDF tools that preview/render PDFs in the browser need **both** pdf-lib AND pdf.js.
- When using pdf.js, ALWAYS set the worker correctly on every page that uses it:
  ```js
  pdfjsLib.GlobalWorkerOptions.workerSrc='https://cdnjs.cloudflare.com/ajax/libs/pdf.js/3.11.174/pdf.worker.min.js';
  ```
  (using `.src` instead of `.workerSrc`, or `pdf.min.js` instead of `pdf.worker.min.js`,
  silently breaks PDF rendering — a previous bug.)
- To embed canvases into a PDF: `canvas.toBlob()` → `ArrayBuffer` → `embedJpg`.
  Do not use `atob()`/base64 conversion (it breaks on large files).

### Clipboard copy pattern

When copying from a `<textarea>`, read `.value` (NOT `.textContent` — that returns `""`):
```js
const el=document.getElementById(id);
const text=el.value||el.textContent;
```

## 🎨 Theming

- **Light mode is the default.** Dark mode applies `data-theme="dark"` on `<html>`.
- Colors are CSS custom properties in `css/style.css` (e.g. `--accent:#6c5ce7`,
  `--bg`, `--card`, `--border`, `--muted`, `--text`, `--green:#00b894`,
  `--accent3:#e84393`).
- Theme preference persists in `localStorage` key `theme`.

## 📱 PWA

- `manifest.json`: name "I❤Tools", start_url="/", theme `#6c5ce7`,
  icons `icon-192.png` / `icon-512.png`.
- The Install button appears only when `beforeinstallprompt` fires (browsers that
  support PWA install). It must NOT auto-show otherwise.

## 🚀 Deployment (GitHub Pages)

- Repo: `silent5441/tools-site`, branch `main`.
- Domain: `ilovetools.pro` (CNAME + DNS A records → GitHub Pages IPs).
- HTTPS is auto-provisioned by GitHub Pages.
- Deploy process: COMMIT + PUSH to `main`. GitHub Pages serves everything.
- This repo has no CI — just `git add -A && git commit && git push origin main`.
- `sitemap.xml` and every canonical URL must use `ilovetools.pro` (not the
  `.github.io` fallback).
- The repo has a CNAME file (not listed above but required for the custom domain).

## 🔧 Known Removals (independent of budgets)

- **Protect PDF** (`protect-pdf.html`) — REMOVED. pdf-lib encryption is unreliable
  client-side (files open without asking for password). Re-add only via a Node/Python
  backend, which this project does not have.
- **Compress PDF** (`compress-pdf.html`) — REMOVED. Client-side size reduction is a
  lossy "re-render to JPEG" hack that blurs text and often produces larger files.
- A Python Flask backend (`backend/server.py`) and Vercel config were attempted then
  removed. Do not reintroduce without a paid host.

## 🔎 SEO Notes

- Homepage targets: "QR scanner, file cleaner, document scanner, PDF editor, resume
  builder, converters, calculators, device tools".
- Each tool page has unique `<title>`, meta description, and `description` paragraph.
- `sitemap.xml` lists all 40 tools + homepage + legal pages. Update it when adding
  or removing tools.
- Google Search Console: property `ilovetools.pro` is verified (DNS TXT `_3LwEQpiICQaXsjabA9jacKKT0zdfyNeOMG8HrzH0Ak`).
- Sitemap URL submitted: `https://ilovetools.pro/sitemap.xml`.

## 📈 Monetization

- **AdSense**: account has publisher ID `ca-pub-8427860023135459`. Ad units not yet
  created — slot IDs in placeholder (`1111111111`, `2222222222`) and hidden by CSS.
  When real ad units are created, replace placeholders and un-hide `.ad-slot`.
- **Affiliate blog**: separate project at `https://silent5441.github.io/affiliate-blog/`.
- **Blogger blog**: `https://tazamewss.blogspot.com` (for promo/backlinks).

## 🧪 Testing Tools Manually

Tools are static files; test by opening locally or checking the live site:
- PDF tools: generate a test PDF, upload, verify thumbnails/downloads.
- Text/encode tools: paste sample input, verify output + Copy button.
- Calculator tools: verify math correctness on known inputs.
- Resume builder: create resume → download PDF/DOCX/JPG.

Known working set (verified): all 40 tools render, PDF thumbs work, copy buttons
work, theme toggle works, visitor counter counts.

## 📌 Future Ideas (traffic/SEO drivers)

- Add QR code scanner, file cleaner, document scanner tools (QR generator is live;
  scanner/file cleaner/doc scanner still TODO).
- Add image compressor, password generator, color palette generator.
- Share on Reddit (r/webdev, r/SEO, r/tools), Twitter/X, Facebook groups.
- Apply to Amazon.in Associates (`affiliate-program.amazon.in`) for India-friendly
  affiliate revenue.

## 🔐 Git / Credentials Warning

The user's GitHub PAT was exposed in an earlier session and should be regenerated.
Never commit secrets. If continuing deployment, use `gh auth` instead of raw tokens.