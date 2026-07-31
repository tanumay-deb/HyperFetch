"""HyperFetch v2 theme — one source of truth for colours + the app stylesheet.

Deliberately NOT `from gui.theme import *`: that pattern caused stale-colour
bugs in v1 (module-level copies didn't track theme switches). Here the colours
live in one dict and the stylesheet is built from it, so there is nothing to go
stale. Custom-painted widgets import COLORS and read it at paint time.
"""

# Accent options offered in Settings → Appearance (key -> hex).
ACCENTS = {
    "purple": "#7c5cff",
    "blue":   "#3b82f6",
    "cyan":   "#06b6d4",
    "green":  "#22c55e",
    "orange": "#f59e0b",
    "red":    "#ef4444",
    "pink":   "#ec4899",
}

# Two base palettes; set_theme() swaps the active one (accent kept). COLORS is the
# live dict everything reads — qss() and inline styles. Theme is applied at
# startup before the UI is built, so every read picks up the right palette.
DARK = {
    "bg":        "#12141c",   # app background (soft charcoal, not pure black)
    "surface":   "#191c25",   # sidebar / panels
    "surface2":  "#20242f",   # cards, inputs
    "card":      "#191c25",   # download card
    "card_hover":"#232733",
    "border":    "#2a2f3b",
    "border2":   "#353b48",
    "text":      "#f1f5f9",
    "muted":     "#8b97ad",
    "faint":     "#5b6678",
    "accent":    "#7c5cff",
    "accent2":   "#9277ff",
    "success":   "#22c55e",
    "warning":   "#f59e0b",
    "error":     "#ef4444",
    "info":      "#38bdf8",
}
LIGHT = {
    "bg":        "#f4f6fb",   # app background (soft gray-blue)
    "surface":   "#ffffff",   # sidebar / panels
    "surface2":  "#eef1f7",   # cards, inputs
    "card":      "#ffffff",   # download card
    "card_hover":"#f1f4fa",
    "border":    "#e4e8f1",
    "border2":   "#d3d9e6",
    "text":      "#0f1729",
    "muted":     "#5b6678",
    "faint":     "#98a2b3",
    "accent":    "#7c5cff",
    "accent2":   "#9277ff",
    "success":   "#16a34a",
    "warning":   "#d97706",
    "error":     "#dc2626",
    "info":      "#0284c7",
}
COLORS = dict(DARK)
_THEME = "dark"

# ---- design tokens (use instead of scattered magic numbers) ----
RADIUS_SM, RADIUS_MD, RADIUS_LG = 6, 9, 12          # corner rounding scale
SPACE_XS, SPACE_SM, SPACE_MD, SPACE_LG, SPACE_XL = 2, 4, 8, 12, 16   # layout spacing
DIALOG_MARGIN = (22, 20, 22, 18)                    # L, T, R, B — standard dialog padding


def _system_is_light():
    """Best-effort Windows light/dark detection for the 'System' theme."""
    try:
        import winreg
        k = winreg.OpenKey(winreg.HKEY_CURRENT_USER,
                           r"Software\Microsoft\Windows\CurrentVersion\Themes\Personalize")
        val, _ = winreg.QueryValueEx(k, "AppsUseLightTheme")
        return bool(val)
    except Exception:
        return False


def set_theme(name):
    """Swap the active palette to 'dark' / 'light' (or resolve 'system'). Keeps
    the current accent. Call BEFORE building the UI; a live switch needs a restart
    because widgets bake colours into inline styles at construction."""
    global _THEME
    if name == "system":
        name = "light" if _system_is_light() else "dark"
    _THEME = "light" if name == "light" else "dark"
    base = LIGHT if _THEME == "light" else DARK
    acc, acc2 = COLORS.get("accent"), COLORS.get("accent2")
    COLORS.clear()
    COLORS.update(base)
    if acc:
        COLORS["accent"], COLORS["accent2"] = acc, acc2
    return _THEME


def active_theme():
    return _THEME


def set_accent(key_or_hex):
    """Set the accent colour by ACCENTS key (e.g. 'green') or a raw hex."""
    hexv = ACCENTS.get(key_or_hex, key_or_hex)
    if not (isinstance(hexv, str) and hexv.startswith("#")):
        hexv = ACCENTS["purple"]
    COLORS["accent"] = hexv
    # a slightly lighter sibling for gradients
    COLORS["accent2"] = _lighten(hexv, 0.15)
    return hexv


def _lighten(hex_color, amt):
    try:
        h = hex_color.lstrip("#")
        r, g, b = (int(h[i:i + 2], 16) for i in (0, 2, 4))
        r = min(255, int(r + (255 - r) * amt))
        g = min(255, int(g + (255 - g) * amt))
        b = min(255, int(b + (255 - b) * amt))
        return f"#{r:02x}{g:02x}{b:02x}"
    except (ValueError, IndexError):
        return hex_color


# ---------------------------------------------------------------- font scale
# Settings -> Appearance -> Font Size. Qt stylesheet font-size ALWAYS beats
# QApplication.setFont(), and this UI pins a size on most widgets, so the
# setting did nothing on its own. Every size is now routed through fpx(), which
# multiplies by this scale. Set it BEFORE any widget is constructed — inline
# styles bake their size in at construction, exactly like the theme colours do.
FONT_SCALE = 1.0
FONT_SIZES = {"Small": 0.85, "Medium": 1.0, "Large": 1.2}


def set_font_scale(name):
    """Apply a Font Size setting name ('Small'/'Medium'/'Large')."""
    global FONT_SCALE
    FONT_SCALE = FONT_SIZES.get(name, 1.0)
    return FONT_SCALE


def fpx(px):
    """A scaled 'Npx' font-size string. Never drops below 8px — below that the
    UI stops being readable rather than merely small."""
    return f"{max(8, round(px * FONT_SCALE))}px"


def qss():
    """The application stylesheet, built from the active palette."""
    c = COLORS
    return f"""
* {{
    font-family: 'Segoe UI Variable Display', 'Segoe UI', 'Inter';
    font-size: {fpx(13)};
    color: {c['text']};
}}
QWidget#root {{ background: {c['bg']}; }}

/* ---------- dense desktop console ---------- */
QFrame#consoleHeader {{
    background: {c['surface']}; border: 1px solid {c['border']};
    border-top: none; border-radius: 0 0 10px 10px;
}}
QLabel#consoleBrand {{ font-size: {fpx(16)}; font-weight: 800; color: {c['text']}; background: transparent; }}
QLabel#activityTitle {{ font-size: {fpx(12)}; color: {c['text']}; background: transparent; }}
QPushButton#consoleIcon {{ background: transparent; border: none; border-radius: 6px; padding: 4px; }}
QPushButton#consoleIcon:hover {{ background: {c['surface2']}; }}
QFrame#consoleNavFrame {{ background: {c['surface']}; border: 1px solid {c['border']}; border-radius: 8px; }}
QPushButton#consoleNav {{
    background: transparent; border: none; border-right: 1px solid {c['border']}; border-radius: 5px;
    min-height: 28px; padding: 5px 14px; color: {c['muted']}; font-size: {fpx(11)}; font-weight: 650;
}}
QPushButton#consoleNav:hover {{ background: {c['surface2']}; color: {c['text']}; }}
QPushButton#consoleNav:checked {{ background: {c['accent']}; color: white; }}
QFrame#metricCard {{ background: {c['surface']}; border: 1px solid {c['border']}; border-radius: 8px; min-height: 50px; }}
QLabel#metricCaption {{ color: {c['muted']}; font-size: {fpx(10)}; background: transparent; }}
QLabel#metricValue {{ color: {c['text']}; font-size: {fpx(14)}; font-weight: 750; background: transparent; }}
QLabel#consolePageTitle {{ color: {c['text']}; font-size: {fpx(14)}; font-weight: 750; background: transparent; }}
QLabel#consoleSummary {{ color: {c['muted']}; font-size: {fpx(11)}; background: transparent; }}
QWidget#activityGraph {{ background: {c['surface']}; border: 1px solid {c['border']}; border-radius: 8px; }}
QTableWidget#consoleTable {{ background: {c['surface']}; border: 1px solid {c['border']}; border-radius: 8px; }}
QTableWidget#consoleTable::item {{ padding: 5px 9px; }}

/* ---------- sidebar ---------- */
QFrame#sidebar {{ background: {c['surface']}; border: none; border-right: 1px solid {c['border']}; }}
QWidget#mainPane {{ background: {c['bg']}; }}
/* Every QScrollArea's inner content is transparent so it shows the container's
   themed background instead of the Qt palette default (which rendered as a light
   panel in dark mode — the download list, settings pages, drawer tabs, etc.).
   Cards/panels inside keep their own bg via #panel / #card selectors. */
QWidget#listInner {{ background: transparent; }}
QScrollArea > QWidget > QWidget {{ background: transparent; }}

QLabel#brand {{ font-size: {fpx(18)}; font-weight: 800; }}
QLabel#sectionTitle {{ color: {c['muted']}; font-size: {fpx(11)}; font-weight: 800; letter-spacing: 1px; }}

/* nav buttons (one real button per row — no paint delegate) */
QPushButton#navItem {{
    background: transparent; border: none; border-radius: 9px;
    padding: 9px 12px; text-align: left; color: {c['muted']}; font-weight: 600;
}}
QPushButton#navItem:hover {{ background: {c['surface2']}; color: {c['text']}; }}
QPushButton#navItem:checked {{ background: {c['surface2']}; color: {c['text']}; }}

/* ---------- buttons ---------- */
QPushButton {{
    background: {c['surface2']}; border: 1px solid {c['border']}; border-radius: 9px;
    padding: 8px 14px; font-weight: 600; color: {c['text']};
}}
QPushButton:hover {{ background: {c['card_hover']}; border-color: {c['border2']}; }}
QPushButton:disabled {{ color: {c['faint']}; background: {c['surface']}; }}

QPushButton#primary {{
    background: qlineargradient(x1:0,y1:0,x2:1,y2:1, stop:0 {c['accent']}, stop:1 {c['accent2']});
    border: none; color: white; padding: 11px 18px; font-weight: 700; font-size: {fpx(14)};
}}
QPushButton#primary:hover {{ background: {c['accent2']}; }}
QPushButton#ghost {{ background: transparent; border: none; color: {c['muted']}; padding: 8px 10px; }}
QPushButton#ghost:hover {{ color: {c['text']}; }}
QPushButton#iconbtn {{ background: transparent; border: none; border-radius: 8px; padding: 6px; color: {c['muted']}; }}
QPushButton#iconbtn:hover {{ background: {c['surface2']}; color: {c['text']}; }}

/* filter pills */
QPushButton#pill {{
    background: transparent; border: 1px solid {c['border']}; border-radius: 15px;
    padding: 6px 16px; color: {c['muted']}; font-weight: 600;
}}
QPushButton#pill:hover {{ color: {c['text']}; border-color: {c['border2']}; }}
QPushButton#pill:checked {{ background: {c['accent']}; border-color: {c['accent']}; color: white; }}

/* ---------- inputs ---------- */
QLineEdit, QComboBox, QSpinBox, QTimeEdit, QDateTimeEdit {{
    background: {c['surface2']}; border: 1px solid {c['border']}; border-radius: 9px;
    padding: 9px 12px; selection-background-color: {c['accent']}; color: {c['text']};
}}
QLineEdit:focus, QComboBox:focus, QSpinBox:focus, QTimeEdit:focus, QDateTimeEdit:focus {{ border-color: {c['accent']}; }}
QComboBox::drop-down {{ border: none; width: 22px; }}
QComboBox QAbstractItemView {{
    background: {c['surface2']}; border: 1px solid {c['border']};
    selection-background-color: {c['accent']}; outline: none;
}}
QSpinBox::up-button, QSpinBox::down-button {{ width: 18px; background: {c['surface']}; border: none; }}

/* ---------- dialogs ---------- */
QDialog {{ background: {c['bg']}; }}
QLabel#dlgTitle {{ font-size: {fpx(17)}; font-weight: 800; }}
QLabel#fieldLabel {{ color: {c['muted']}; font-weight: 700; font-size: {fpx(12)}; background: transparent; }}

/* ---------- tabs ---------- */
QTabWidget::pane {{ border: none; top: -1px; }}
QTabBar {{ qproperty-drawBase: 0; }}
QTabBar::tab {{
    background: {c['surface2']}; color: {c['muted']}; border: 1px solid {c['border']};
    padding: 9px 22px; margin-right: 6px; border-radius: 9px; font-weight: 700;
}}
QTabBar::tab:selected {{ background: {c['accent']}; color: white; border-color: {c['accent']}; }}
QTabBar::tab:hover:!selected {{ color: {c['text']}; }}

/* ---------- checkbox / toggle ---------- */
QCheckBox {{ spacing: 8px; background: transparent; }}
QCheckBox::indicator {{ width: 18px; height: 18px; border-radius: 5px; border: 1px solid {c['border2']}; background: {c['surface2']}; }}
QCheckBox::indicator:checked {{ background: {c['accent']}; border-color: {c['accent']}; }}

/* ---------- group cards in settings/dialogs ---------- */
QFrame#panel {{ background: {c['surface2']}; border: 1px solid {c['border']}; border-radius: 10px; }}

/* ---------- cards ---------- */
QFrame#card {{ background: {c['card']}; border: 1px solid {c['border']}; border-radius: 12px; }}
QFrame#card:hover {{ background: {c['card_hover']}; border-color: {c['border2']}; }}
QFrame#statsCard {{ background: {c['surface2']}; border: 1px solid {c['border']}; border-radius: 12px; }}

/* ---------- progress ---------- */
QProgressBar {{ background: {c['surface2']}; border: none; border-radius: 4px; max-height: 6px; text-align: center; color: transparent; }}
QProgressBar::chunk {{
    background: qlineargradient(x1:0,y1:0,x2:1,y2:0, stop:0 {c['accent']}, stop:1 {c['accent2']});
    border-radius: 4px;
}}

/* ---------- scrollbars ---------- */
QScrollArea {{ border: none; background: transparent; }}
QScrollBar:vertical {{ background: transparent; width: 10px; margin: 2px; }}
QScrollBar::handle:vertical {{ background: {c['border2']}; border-radius: 5px; min-height: 30px; }}
QScrollBar::handle:vertical:hover {{ background: {c['accent']}; }}
QScrollBar::add-line, QScrollBar::sub-line {{ height: 0; }}
QScrollBar::add-page, QScrollBar::sub-page {{ background: transparent; }}

QToolTip {{ background: {c['surface2']}; color: {c['text']}; border: 1px solid {c['border']}; padding: 4px; }}

/* ---------- tables (History) ---------- */
QTableWidget, QTableView {{
    background: {c['surface']}; alternate-background-color: {c['surface2']};
    color: {c['text']}; border: 1px solid {c['border']}; border-radius: 10px;
    gridline-color: {c['border']}; outline: none;
}}
QTableWidget::item, QTableView::item {{ padding: 6px 8px; border: none; }}
QTableWidget::item:selected, QTableView::item:selected {{ background: {c['accent']}; color: white; }}
QHeaderView::section {{
    background: {c['bg']}; color: {c['muted']}; border: none;
    border-bottom: 1px solid {c['border']}; padding: 7px 8px; font-weight: 700;
}}
QTableCornerButton::section {{ background: {c['bg']}; border: none; }}
"""
