# The HyperFetch logo

`logo.svg` in this folder is the logo. Everything else is that drawing at a size.

It is a rounded square, filled corner to corner with a gradient from indigo
`#6366f1` (top left) to violet `#8b5cf6` (bottom right), with a white bolt on it.
On a 512 grid the corner radius is 112.64 (22%) and the bolt is the polygon
`297,31 271.5,215 379,215 204.5,482 230.5,287 133,287`.

## Where the copies are

| File | Size | Used by |
| --- | --- | --- |
| `assets/icon.png` | 256 | the app: sidebar, settings header, toasts, the web client |
| `assets/icon.ico` | 16 to 256 | the window, the taskbar, the installer |
| `docs/logo.png` | 256 | the site's pages (the same bytes as `assets/icon.png`) |
| `docs/logo.svg` | vector | the same bytes as `logo.svg` here |
| `chrome_ext/icons/icon16.png`, `icon48.png`, `icon128.png` | 16, 48, 128 | the extension: toolbar, popup, welcome page (copied to `edge_ext/` and `firefox_ext/`) |
| `assets/store/store_icon_128.png` | 128 | the store listings |

The public address of the master is
<https://tanumay-deb.github.io/HyperFetch/logo.svg>, and of the 256 px PNG
<https://tanumay-deb.github.io/HyperFetch/logo.png>. Fetch it from there.

## Rules

- A page or a window shows one of these files. It does not draw the logo: no
  gradient tile with a bolt character on it, and never the bolt emoji, which
  every system draws its own way (yellow and orange on Windows).
- Do not redraw, recolour, stretch or crop it. A new size is `logo.svg`
  rendered at that size.
- `tests/test_brand_logo.py` renders `logo.svg` at each copy's size and fails
  if a copy is not that picture, or if a page stops showing the file.

The master was traced from `assets/icon.png` on 2026-10-04 and matches every
copy above to within anti-aliasing (a mean difference of 0.04 to 0.08 out of
255 per channel).
