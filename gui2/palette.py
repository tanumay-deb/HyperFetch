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
    "bg":        "#0a0e17",   # app background (deep navy)
    "surface":   "#11151f",   # sidebar / panels
    "surface2":  "#171c28",   # cards, inputs
    "card":      "#12161f",   # download card
    "card_hover":"#161b27",
    "border":    "#232838",
    "border2":   "#2c3344",
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


def qss():
    """The application stylesheet, built from the active palette."""
    c = COLORS
    return f"""
* {{
    font-family: 'Segoe UI Variable Display', 'Segoe UI', 'Inter';
    font-size: 13px;
    color: {c['text']};
}}
QWidget#root {{ background: {c['bg']}; }}

/* ---------- sidebar ---------- */
QFrame#sidebar {{ background: {c['surface']}; border: none; border-right: 1px solid {c['border']}; }}
QWidget#mainPane {{ background: {c['bg']}; }}

QLabel#brand {{ font-size: 18px; font-weight: 800; }}
QLabel#sectionTitle {{ color: {c['muted']}; font-size: 11px; font-weight: 800; letter-spacing: 1px; }}

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
    border: none; color: white; padding: 11px 18px; font-weight: 700; font-size: 14px;
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
QLineEdit, QComboBox, QSpinBox {{
    background: {c['surface2']}; border: 1px solid {c['border']}; border-radius: 9px;
    padding: 9px 12px; selection-background-color: {c['accent']};
}}
QLineEdit:focus, QComboBox:focus, QSpinBox:focus {{ border-color: {c['accent']}; }}
QComboBox::drop-down {{ border: none; width: 22px; }}
QComboBox QAbstractItemView {{
    background: {c['surface2']}; border: 1px solid {c['border']};
    selection-background-color: {c['accent']}; outline: none;
}}
QSpinBox::up-button, QSpinBox::down-button {{ width: 18px; background: {c['surface']}; border: none; }}

/* ---------- dialogs ---------- */
QDialog {{ background: {c['bg']}; }}
QLabel#dlgTitle {{ font-size: 17px; font-weight: 800; }}
QLabel#fieldLabel {{ color: {c['muted']}; font-weight: 700; font-size: 12px; background: transparent; }}

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
