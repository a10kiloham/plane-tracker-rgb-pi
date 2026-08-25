"""
ISS Overhead Pass — full display takeover scene.

When the ISS is actively overhead (1-6 minutes), this scene takes over the
entire display with an animated ISS sprite, progress bar, and countdown.
Ported from ajplotkin's iss-visibility-indicator branch.

Layout (64x32 LED matrix):
  Rows  0-4:  "ISS VISIBLE"/"ISS OVERHEAD" blinking text
  Rows  6-12: ISS pixel-art sprite moving left-to-right (position = progress)
              with dim trail dots behind it
  Row   20:   Direction + elevation text (e.g., "NW>SE 88°")
  Row   23:   Progress bar (dashed: flown vs remaining, + position marker)
  Rows 27-31: Countdown text (e.g., "3:42 LEFT")

If a plane enters the zone during a pass, the plane display gets one full
scroll cycle ("cameo") before the takeover resumes; see display/__init__.py.

Other scenes yield while `self._iss_active` is True.
"""

import logging
import time

from utilities.animator import Animator
from setup import colours, fonts, screen, frames
from rgbmatrix import graphics

try:
    from config import ISS_ENABLED
except (ModuleNotFoundError, ImportError):
    ISS_ENABLED = False

try:
    from utilities.iss import get_iss_pass_data, is_iss_visible_now
except ImportError:
    get_iss_pass_data = lambda: None
    is_iss_visible_now = lambda lat, lon: False

logger = logging.getLogger(__name__)


# Fonts
TITLE_FONT = fonts.extrasmall       # 4x6
INFO_FONT = fonts.extrasmall        # 4x6
COUNTDOWN_FONT = fonts.small        # 5x8

# Colour themes — warm (visible) vs cool (not visible)
THEME_VISIBLE = {
    "title": colours.WHITE,
    "title_dim": colours.LIGHT_GREY,
    "trail": graphics.Color(60, 50, 20),        # gold
    "flown": colours.LIMEGREEN,
    "remaining": colours.LIGHT_BLUE,
    "marker": colours.WHITE,
    "info": colours.LIGHT_ORANGE,
    "countdown": colours.YELLOW,
}

THEME_DIM = {
    "title": graphics.Color(100, 130, 180),      # steel blue
    "title_dim": graphics.Color(50, 65, 90),     # dark blue
    "trail": graphics.Color(30, 35, 60),         # dim navy
    "flown": graphics.Color(40, 100, 100),       # dim teal
    "remaining": graphics.Color(60, 60, 70),     # dark grey
    "marker": graphics.Color(140, 140, 160),     # muted white
    "info": graphics.Color(120, 120, 130),       # slate grey
    "countdown": graphics.Color(80, 160, 170),   # dim cyan
}

# Layout positions
TITLE_Y = 5           # baseline for title text
SPRITE_Y = 7          # top of sprite region
SPRITE_MID_Y = 9      # vertical center of sprite for trail dots
INFO_Y = 20           # baseline for direction + elevation
PROGRESS_Y = 23       # center row of progress bar
COUNTDOWN_Y = 31      # baseline for countdown text

# Safety cap for the plane "cameo" during a pass — if scroll completion
# never fires, resume the takeover after this many seconds.
CAMEO_MAX_SECONDS = 60

# Pixel-art ISS sprite: solar panel arrays either side of a central module,
# joined by a truss. Drawn procedurally (the ISS.png in logos/ has a solid
# white background, so it can't be used as a sprite).
#   P = solar panel, T = truss, B = body module
_SPRITE_ROWS = [
    "PPP.....PPP",
    "PPP..B..PPP",
    "PPPTTBTTPPP",
    "PPP..B..PPP",
    "PPP.....PPP",
]
_SPRITE_W = len(_SPRITE_ROWS[0])
_SPRITE_H = len(_SPRITE_ROWS)
_SPRITE_COLOURS = {
    "P": (80, 100, 220),    # solar panels — blue
    "T": (130, 130, 130),   # truss — grey
    "B": (230, 230, 230),   # body — white
}


def _draw_sprite(canvas, x0, y0):
    for py, row in enumerate(_SPRITE_ROWS):
        for px, ch in enumerate(row):
            rgb = _SPRITE_COLOURS.get(ch)
            if rgb:
                canvas.SetPixel(x0 + px, y0 + py, *rgb)


def _draw_plus_marker(canvas, x, y, colour):
    """Draw a + shaped marker (like trackedprogress.py plane marker)."""
    canvas.SetPixel(x, y, colour.red, colour.green, colour.blue)
    canvas.SetPixel(x - 1, y, colour.red, colour.green, colour.blue)
    canvas.SetPixel(x + 1, y, colour.red, colour.green, colour.blue)
    canvas.SetPixel(x, y - 1, colour.red, colour.green, colour.blue)
    canvas.SetPixel(x, y + 1, colour.red, colour.green, colour.blue)


class ISSPassScene(object):
    def __init__(self):
        super().__init__()
        self._iss_plane_shown = False   # plane cameo completed this pass
        self._iss_was_active = False
        self._iss_active = False        # checked by other scenes to yield
        self._iss_pass_active_now = False  # checked by display cameo logic
        self._iss_cameo_started = None

    @Animator.KeyFrame.add(frames.PER_SECOND * 1)
    def iss_pass_scene(self, count):
        iss = None
        if ISS_ENABLED:
            try:
                iss = get_iss_pass_data()
            except Exception as e:
                logger.debug(f"ISS pass data unavailable: {e}")
                iss = None

        self._iss_pass_active_now = bool(iss and iss.get("is_active"))

        if not self._iss_pass_active_now:
            if self._iss_was_active:
                # Pass just ended — wipe stale ISS pixels (sprite, progress
                # bar, countdown) that no other scene would overwrite.
                # Yielding scenes redraw via their _redraw flags.
                self.canvas.Clear()
                self._iss_was_active = False
                self._iss_plane_shown = False
            self._iss_active = False
            self._iss_cameo_started = None
            return

        self._iss_was_active = True

        # During ISS pass: allow ONE plane scroll cycle ("cameo"), then
        # suppress. display/__init__.py sets _iss_plane_shown once the
        # scroll regions complete; cap the cameo as a fallback.
        if len(self._data) > 0 and not self._iss_plane_shown:
            if self._iss_cameo_started is None:
                self._iss_cameo_started = time.monotonic()
            if time.monotonic() - self._iss_cameo_started < CAMEO_MAX_SECONDS:
                self._iss_active = False  # let plane scenes draw during cameo
                return
            self._iss_plane_shown = True

        self._iss_active = True  # suppress other scenes

        # Clear the entire canvas — other scenes yield during takeover, and
        # clear_screen (keyframe 0) only fires on scene resets, so stale
        # clock/weather/forecast pixels persist unless we wipe them here.
        self.canvas.Clear()

        progress = iss["progress"]
        time_remaining = iss["time_remaining_sec"]

        # Real-time visibility check (warm theme if visible, cool if not)
        try:
            import config as cfg
            visible = is_iss_visible_now(cfg.LOCATION_HOME[0], cfg.LOCATION_HOME[1])
        except Exception:
            visible = iss.get("visible", False)
        theme = THEME_VISIBLE if visible else THEME_DIM

        # --- 1. Title (rows 0-4), blinking bright/dim each second ---
        title_colour = theme["title"] if (count % 2 == 0) else theme["title_dim"]
        title_text = "ISS VISIBLE" if visible else "ISS OVERHEAD"
        title_width = len(title_text) * 4
        title_x = max(0, (screen.WIDTH - title_width) // 2)
        graphics.DrawText(self.canvas, TITLE_FONT, title_x, TITLE_Y,
                          title_colour, title_text)

        # --- 2. ISS sprite moving left-to-right ---
        usable_width = screen.WIDTH - _SPRITE_W
        sprite_x = int(progress * usable_width)
        sprite_x = max(0, min(usable_width, sprite_x))

        # Trail dots behind the sprite
        trail = theme["trail"]
        for tx in range(0, sprite_x, 2):
            self.canvas.SetPixel(tx, SPRITE_MID_Y,
                                 trail.red, trail.green, trail.blue)

        _draw_sprite(self.canvas, sprite_x, SPRITE_Y)

        # --- 3. Direction + elevation (row 20) ---
        rise_dir = iss["rise_compass"]
        set_dir = iss["set_compass"]
        max_elev = int(iss["max_elevation"])
        info_text = f"{rise_dir}>{set_dir} {max_elev}\xb0"
        info_width = len(info_text) * 4
        info_x = max(0, (screen.WIDTH - info_width) // 2)
        graphics.DrawText(self.canvas, INFO_FONT, info_x, INFO_Y,
                          theme["info"], info_text)

        # --- 4. Progress bar (row 23) ---
        bar_width = screen.WIDTH - 4  # leave 2px margin each side
        bar_start = 2
        flown_px = int(progress * bar_width)

        for x in range(bar_width):
            bx = bar_start + x
            colour = theme["flown"] if x < flown_px else theme["remaining"]
            # Dashed line: draw every other 2px group
            if (x // 2) % 2 == 0:
                self.canvas.SetPixel(bx, PROGRESS_Y,
                                     colour.red, colour.green, colour.blue)

        # + marker at current position
        marker_x = bar_start + min(flown_px, bar_width - 1)
        _draw_plus_marker(self.canvas, marker_x, PROGRESS_Y, theme["marker"])

        # --- 5. Countdown (rows 27-31) ---
        mins = time_remaining // 60
        secs = time_remaining % 60
        countdown_text = f"{mins}:{secs:02d} LEFT"
        countdown_width = len(countdown_text) * 5
        countdown_x = max(0, (screen.WIDTH - countdown_width) // 2)
        graphics.DrawText(self.canvas, COUNTDOWN_FONT, countdown_x,
                          COUNTDOWN_Y, theme["countdown"], countdown_text)
