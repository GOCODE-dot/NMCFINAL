# What changed: turning NMMS into an installable app (iOS / Android / iPad / desktop)

Your Flask app was already responsive (correct viewport meta tag, mobile
breakpoints, separate mobile/desktop hero images), so **the desktop
experience is unchanged and needs nothing further.** What was missing was
the layer that makes a phone/tablet treat it like a real app instead of a
browser tab. That's what got added:

## New files
- `static/manifest.json` — the PWA manifest (name, colors, icons, standalone
  display mode). This is what Android/Chrome/desktop Chrome read to offer
  "Install app" and to launch it full-screen, no address bar.
- `static/sw.js` — a minimal service worker. It **only** caches static
  assets (icons, manifest) — never pages, orders, or payment data — so
  meal cutoffs, dues and cook-sheet numbers are always fetched live. Its
  main job is satisfying the installability requirement and speeding up
  repeat asset loads.
- `static/icons/*.png` — a full icon set (16px favicon up to 512px, plus
  maskable variants for Android's adaptive-icon shape and a 180px Apple
  touch icon), generated in your brand green (#1a6b3a) with a simple
  plate/fork/knife mark matching the 🍽️ logo already used in your nav bar.

## Changed files
- `templates/base.html` — added, in `<head>`:
  - `<link rel="manifest" ...>` so Chrome/Edge/Android offer install.
  - `apple-mobile-web-app-capable`, `apple-mobile-web-app-status-bar-style`,
    `apple-mobile-web-app-title`, and `apple-touch-icon` — iOS Safari
    **ignores** manifest.json entirely and only reads these specific tags
    for "Add to Home Screen" behavior (standalone window, status bar style,
    home screen icon/name).
  - `theme-color` and favicon links.
  - A small script before `</body>` that registers `sw.js`.

  Every real page in this app (`index`, both dashboards, all login/register/
  reset pages, admin) extends `base.html`, so this one edit covers the
  whole site. The many `*_patch.html` / `*_snippet.html` files in
  `templates/` are your own dev reference notes, not live routes (`app.py`
  never calls `render_template()` on them) — left untouched.

## How each platform gets the "app" experience
- **Android / Chrome (phone or tablet):** visiting the site shows a native
  "Install app" prompt (or the ⋮ menu → "Install app"). Once installed it
  opens in its own window, no browser chrome, with your icon on the
  home/app-drawer.
- **iPad / iPhone (Safari):** Share button → **Add to Home Screen**. This
  has no auto-prompt on iOS (Apple doesn't offer one) — it's a manual,
  one-time step for each user, same as any iOS web app. After that it opens
  standalone with your icon.
- **Desktop (Chrome/Edge):** an install icon appears in the address bar;
  installs as a normal desktop app window. Unchanged otherwise — the
  existing responsive layout was already fine here.

## Nothing else was touched
No routes, database logic, payment gateway code, or existing templates'
markup/behavior were modified — this is additive only.

## To verify after deploying
1. Deploy as usual (Railway/Docker — `Procfile`/`Dockerfile` unchanged).
2. Visit the live HTTPS URL on a phone. **Note:** browsers only offer
   install / register the service worker over HTTPS (or `localhost`) — make
   sure your deployment serves HTTPS, which Railway does by default.
3. Chrome DevTools → Application tab → Manifest / Service Workers, to
   confirm both are recognized with no errors.
