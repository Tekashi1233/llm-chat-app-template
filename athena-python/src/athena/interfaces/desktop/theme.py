"""ATHENA Python v1 — palette and glass styles.

Direct port of the brushes in ``athena-app/.../App.xaml``:
Obsidian & Bronze, frosted-glass fills (translucent white gradient
+ thin bright edge) over pure black. No Qt import here — plain
strings, usable from tests and from the Qt widgets alike.
"""

from __future__ import annotations

# --- Palette (App.xaml) -------------------------------------------------
BACKGROUND = "#000000"      # shell background (MainWindow: pure black)
PANEL = "#16181C"
CARD = "#1B1E23"
GOLD = "#D4AF37"
OLIVE = "#4A6B5D"
TEXT = "#F3F1EC"
MUTED = "#9A9890"
USER_BUBBLE = "#2A2416"
ERROR = "#E88A8A"
ERROR_DARK = "#7A2E2E"

# --- Frosted glass (App.xaml BrushGlassFill / BrushGlassBorder) --------
# Translucent white, light at the top fading down, thin bright edge.
GLASS_FILL_TOP = "rgba(255, 255, 255, 0.20)"   # #33FFFFFF
GLASS_FILL_MID = "rgba(255, 255, 255, 0.12)"   # #1FFFFFFF
GLASS_FILL_BOTTOM = "rgba(255, 255, 255, 0.05)"  # #0DFFFFFF
GLASS_BORDER = "rgba(255, 255, 255, 0.35)"     # #59FFFFFF
GLASS_GOLD_BORDER = "rgba(212, 175, 55, 0.50)"  # #80D4AF37

# --- Reactor colours (MainWindow.xaml) ----------------------------------
REACTOR_RING = "#7A8A99"
REACTOR_COIL = "#9FB4C7"
REACTOR_RING_INNER = "#5A6B7A"
REACTOR_CORE_WHITE = "#FFFFFF"
REACTOR_CORE_MID = "#BFE0FF"
REACTOR_CORE_BLUE = "#2E5AAC"
REACTOR_GLOW_BLUE = "#7FB3FF"
GLOW_BLUE = "#2E5AAC"
GLOW_RED = "#A32E2E"

# --- Typography ----------------------------------------------------------
WORDMARK_FONT = "Georgia"
WORDMARK_IDLE_PT = 72   # idle wordmark; C# animates 28pt <-> 16pt in a
WORDMARK_CHAT_PT = 26   # 700px window — scaled up here for the Qt shell
TITLE_FONT_PT = 13

GLASS_QSS = f"""
QWidget {{
    background: transparent;
    color: {TEXT};
}}
"""

#: Stylesheet for a frosted-glass panel (title bar, input bar, overlay).
GLASS_PANEL_QSS = f"""
QWidget#GlassPanel {{
    background: qlineargradient(x1:0, y1:0, x2:0, y2:1,
        stop:0 {GLASS_FILL_TOP},
        stop:0.5 {GLASS_FILL_MID},
        stop:1 {GLASS_FILL_BOTTOM});
    border: 1px solid {GLASS_BORDER};
    border-radius: 10px;
}}
"""

#: Gold button (the C# default Button style: gold fill, dark text).
GOLD_BUTTON_QSS = f"""
QPushButton {{
    background: {GOLD};
    color: #0E0F11;
    font-weight: 600;
    padding: 8px 16px;
    border: none;
    border-radius: 4px;
}}
QPushButton:hover {{ background: #E2C25A; }}
QPushButton:disabled {{ background: #6B6136; color: #B0AA8E; }}
"""

#: Flat ghost button (menu rows, quick actions): transparent, left-aligned.
GHOST_BUTTON_QSS = f"""
QPushButton {{
    background: transparent;
    color: {TEXT};
    font-weight: normal;
    text-align: left;
    padding: 10px 12px;
    border: none;
    border-radius: 4px;
}}
QPushButton:hover {{ background: rgba(255, 255, 255, 0.08); }}
QPushButton:disabled {{ color: {MUTED}; }}
"""

#: Caption buttons in the custom title bar.
CAPTION_BUTTON_QSS = f"""
QPushButton {{
    background: transparent;
    color: {MUTED};
    font-size: 13px;
    border: none;
    min-width: 46px;
    min-height: 34px;
}}
QPushButton:hover {{ background: rgba(255, 255, 255, 0.10); color: {TEXT}; }}
QPushButton#CloseButton:hover {{ background: #7A2E2E; color: #FFFFFF; }}
"""

#: Chat input box: transparent, borderless (sits on the glass bar).
INPUT_QSS = f"""
QLineEdit, QTextEdit {{
    background: transparent;
    color: {TEXT};
    border: none;
    font-size: 14px;
    selection-background-color: {GOLD};
    selection-color: #0E0F11;
}}
"""

#: Card used by the section panels (ViewHelpers.Card port).
CARD_QSS = f"""
QFrame#Card {{
    background: {CARD};
    border: 1px solid {GOLD};
    border-radius: 6px;
}}
"""
