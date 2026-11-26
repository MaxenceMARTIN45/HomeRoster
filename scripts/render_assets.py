"""Render the PNG assets from their SVG sources with a headless Chromium browser (Edge, Chrome or Chromium).

    python scripts/render_assets.py

Outputs:
  src/homeroster/static/icon-192.png, icon-512.png     web app icons
  src/homeroster/static/apps/<connector>.png           app logos embedded in the welcome e-mail
  docs/assets/social-preview.png                       GitHub social preview (1280x640)
  docs/assets/how-it-works-{light,dark}.png            README diagram, from docs/assets/how-it-works.html

Only needed when you change a logo or add a built-in connector; the PNGs are committed.
"""

from __future__ import annotations

from pathlib import Path

from _browser import find_browser, screenshot_html, screenshot_url

ROOT = Path(__file__).resolve().parent.parent
STATIC = ROOT / "src" / "homeroster" / "static"
DOCS = ROOT / "docs" / "assets"

# connector -> (background colour, glyph path from static/icons.svg)
APP_ICONS = {
    "jellyfin": ("#8b5cf6", '<rect x="3" y="4" width="18" height="16" rx="2.5"/><path d="M7 4v16M17 4v16M3 9h4M3 15h4M17 9h4M17 15h4"/>'),
    "seerr": ("#6366f1", '<path d="M15 5v2M15 11v2M15 17v2"/><path d="M5 5h14a2 2 0 0 1 2 2v3a2 2 0 0 0 0 4v3a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-3a2 2 0 0 0 0-4V7a2 2 0 0 1 2-2z"/>'),
    "immich": ("#0ea5e9", '<rect x="3" y="4" width="18" height="16" rx="3"/><circle cx="9" cy="9.5" r="1.8"/><path d="M21 15.5l-5-5L5 20"/>'),
    "openwebui": ("#10b981", '<path d="M5 18l-2 3V6a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2v10a2 2 0 0 1-2 2z"/><path d="M8 9.5h8M8 13h5"/>'),
    "plex": ("#e5a00d", '<path d="M8 4l9 8-9 8"/>'),
    "emby": ("#52b54b", '<path d="M9 7l7 5-7 5z"/><rect x="3" y="3" width="18" height="18" rx="5"/>'),
    "audiobookshelf": ("#f97316", '<path d="M4 18V7a2 2 0 0 1 2-2h4v15H6a2 2 0 0 1-2-2zM10 5h4v15h-4zM14 6.5l3.8-1 3 13.6-3.8 1z"/>'),
    "navidrome": ("#0ea5e9", '<path d="M9 18V6l11-2v12"/><circle cx="6.5" cy="18" r="2.5"/><circle cx="17.5" cy="16" r="2.5"/>'),
    "vaultwarden": ("#175ddc", '<path d="M12 3l8 3v6c0 4.6-3.4 8.3-8 9-4.6-.7-8-4.4-8-9V6z"/><path d="M12 8v8"/>'),
    "homeassistant": ("#18bcf2", '<path d="M3 11l9-7 9 7"/><path d="M5.5 9.5V20h13V9.5"/><circle cx="12" cy="14" r="2"/>'),
    "app": ("#5b4cf5", '<rect x="4" y="4" width="6.5" height="6.5" rx="1.6"/><rect x="13.5" y="4" width="6.5" height="6.5" rx="1.6"/><rect x="4" y="13.5" width="6.5" height="6.5" rx="1.6"/><rect x="13.5" y="13.5" width="6.5" height="6.5" rx="1.6"/>'),
}


def page_for_svg(svg: str, width: int, height: int) -> str:
    return (f"<!doctype html><html><head><style>html,body{{margin:0;background:transparent;overflow:hidden}}"
            f"svg{{display:block;width:{width}px;height:{height}px}}</style></head><body>{svg}</body></html>")


def app_icon_svg(color: str, glyph: str) -> str:
    return (f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 96 96"><rect width="96" height="96" rx="26" fill="{color}"/>'
            f'<g transform="translate(24 24) scale(2)" fill="none" stroke="#fff" stroke-width="1.9" '
            f'stroke-linecap="round" stroke-linejoin="round">{glyph}</g></svg>')


def main() -> None:
    browser = find_browser()
    print(f"Rendering with {browser}")
    logo = (STATIC / "icon.svg").read_text(encoding="utf-8")
    for size in (192, 512):
        screenshot_html(browser, page_for_svg(logo, size, size), STATIC / f"icon-{size}.png", size, size)
    for name, (color, glyph) in APP_ICONS.items():
        screenshot_html(browser, page_for_svg(app_icon_svg(color, glyph), 96, 96), STATIC / "apps" / f"{name}.png", 96, 96)
    banner = (DOCS / "banner.svg").read_text(encoding="utf-8")
    screenshot_html(browser, page_for_svg(banner, 1280, 640), DOCS / "social-preview.png", 1280, 640)
    diagram = (DOCS / "how-it-works.html").as_uri()
    for theme in ("light", "dark"):
        screenshot_url(browser, diagram, DOCS / f"how-it-works-{theme}.png", 1600, 860, scale=1.25, dark=theme == "dark")


if __name__ == "__main__":
    main()
