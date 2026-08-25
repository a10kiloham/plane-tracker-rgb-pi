from utilities.animator import Animator
from utilities import textclip
from setup import colours, fonts, screen
from setup import frames

from rgbmatrix import graphics

# Setup
FLIGHT_NO_DISTANCE_FROM_TOP = 24
FLIGHT_NO_TEXT_HEIGHT = 8  # based on font size
FLIGHT_NO_FONT = fonts.small
FLIGHT_NO_CLIP_FONT = textclip.small  # same 5x8.bdf, for boundary clipping

FLIGHT_NUMBER_ALPHA_COLOUR = colours.LIGHT_PURPLE
FLIGHT_NUMBER_NUMERIC_COLOUR = colours.LIGHT_ORANGE
LIVERY_COLOUR = colours.GREY

DATA_INDEX_POSITION = (52, 24)
DATA_INDEX_TEXT_HEIGHT = 7
DATA_INDEX_FONT = fonts.extrasmall
DATA_INDEX_CLIP_FONT = textclip.extrasmall  # width measurement only

DATA_INDEX_COLOUR = colours.GREY

# Minimum frames to display before allowing page advance (10 seconds)
MIN_PAGE_FRAMES = int(10 / frames.PERIOD)

# Maximum character length for livery note to be shown
MAX_LIVERY_LENGTH = 16

# The canvas is live (sync() discards SwapOnVSync's return, so self.canvas
# IS the displayed framebuffer). Every write is visible immediately —
# flicker-free rendering requires that the indicator zone (x >= 52) is
# written at most once per page change, never per frame. Scroll text is
# therefore clipped at the zone edge instead of drawn through and stamped.


class FlightDetailsScene(object):
    def __init__(self):
        super().__init__()
        self.flight_position = screen.WIDTH
        self._data_all_looped = False
        self._page_frame_count = 0
        self._indicator_state = "reset"

    @Animator.KeyFrame.add(1)
    def flight_details(self, count):

        # Guard against no data or ISS takeover
        if len(self._data) == 0 or getattr(self, "_iss_active", False):
            # Other scenes own the canvas; force an indicator redraw
            # when flight data returns.
            self._indicator_state = "reset"
            return

        # Increment page frame counter
        self._page_frame_count += 1

        has_indicator = len(self._data) > 1

        # Scroll text may use the full width when there is no page
        # indicator; with one there, it is clipped at the zone edge so
        # the zone is never touched by per-frame draws.
        boundary = DATA_INDEX_POSITION[0] if has_indicator else screen.WIDTH

        # Clear the scroll zone only — never the indicator zone
        self.draw_square(
            0,
            FLIGHT_NO_DISTANCE_FROM_TOP - FLIGHT_NO_TEXT_HEIGHT,
            boundary,
            FLIGHT_NO_DISTANCE_FROM_TOP,
            colours.BLACK,
        )

        # Draw indicator: once per page change, then leave untouched. On a
        # live canvas any per-frame rewrite here is visible flicker. Runs
        # before the text draw so a multi->single transition can't stamp
        # black over text just drawn at full width.
        indicator_text_length = 0
        if has_indicator:
            indicator_text = f"{self._data_index + 1}/{len(self._data)}"
            # Same value DrawText would return (advance = DWIDTH); counted
            # into the line length below so scroll timing is unchanged
            indicator_text_length = sum(
                DATA_INDEX_CLIP_FONT.advance(ch) for ch in indicator_text
            )
            indicator_state = (self._data_index, len(self._data))
        else:
            indicator_state = None

        if self._indicator_state != indicator_state:
            # Clear area where N of M might have been
            self.draw_square(
                DATA_INDEX_POSITION[0],
                FLIGHT_NO_DISTANCE_FROM_TOP - FLIGHT_NO_TEXT_HEIGHT,
                screen.WIDTH,
                FLIGHT_NO_DISTANCE_FROM_TOP,
                colours.BLACK,
            )

            if has_indicator:
                graphics.DrawText(
                    self.canvas,
                    DATA_INDEX_FONT,
                    DATA_INDEX_POSITION[0],
                    DATA_INDEX_POSITION[1],
                    DATA_INDEX_COLOUR,
                    indicator_text,
                )

            self._indicator_state = indicator_state

        # Build the scroll line: airline + flight number (alpha/numeric
        # colours), then optional livery note in grey.
        chars = []  # list of (char, colour)
        callsign = self._data[self._data_index]["callsign"]
        owner_icao = self._data[self._data_index]["owner_icao"]

        if callsign and callsign != "N/A":
            # Remove icao from flight number to get numeric part
            if owner_icao and callsign.startswith(owner_icao):
                flight_no = callsign[len(owner_icao):]
            else:
                flight_no = callsign

            # Use IATA flight number if available (e.g., "BA123")
            iata_flight = self._data[self._data_index].get("flight_number", "")
            if iata_flight:
                flight_no = iata_flight

            # Prepend airline name if available
            airline = self._data[self._data_index].get("airline", "")
            main_text = f"{airline} {flight_no}" if airline else flight_no

            for ch in main_text:
                colour = (
                    FLIGHT_NUMBER_NUMERIC_COLOUR
                    if ch.isnumeric()
                    else FLIGHT_NUMBER_ALPHA_COLOUR
                )
                chars.append((ch, colour))

            # Append livery note if present and short enough (in grey)
            livery_note = self._data[self._data_index].get("livery_note", "")
            if livery_note and len(livery_note) <= MAX_LIVERY_LENGTH:
                for ch in f" ({livery_note})":
                    chars.append((ch, LIVERY_COLOUR))

        # Draw the line, clipped per pixel column at the boundary.
        # Characters enter column-by-column at x=52 exactly as they do at
        # the hardware edge in single-flight mode.
        flight_no_text_length = 0
        for ch, colour in chars:
            char_x = self.flight_position + flight_no_text_length
            advance = FLIGHT_NO_CLIP_FONT.advance(ch)

            if char_x + advance <= boundary:
                # Fully left of the boundary — fast C++ draw
                graphics.DrawText(
                    self.canvas,
                    FLIGHT_NO_FONT,
                    char_x,
                    FLIGHT_NO_DISTANCE_FROM_TOP,
                    colour,
                    ch,
                )
            elif char_x < boundary:
                # Straddles the boundary — draw only columns < boundary
                FLIGHT_NO_CLIP_FONT.draw_char_clipped(
                    self.canvas,
                    char_x,
                    FLIGHT_NO_DISTANCE_FROM_TOP,
                    colour,
                    ch,
                    x_max=boundary,
                )
            # else: fully inside the indicator zone — draw nothing, but
            # still count the advance so scroll timing is unchanged

            flight_no_text_length += advance

        # Count the whole line length
        flight_no_text_length += indicator_text_length

        # Handle scrolling
        self.flight_position -= 1
        if self.flight_position + flight_no_text_length < 0:
            # Text has scrolled off — mark region as complete but don't advance yet
            # (display/__init__.py will advance when all regions are complete)
            if self._page_frame_count >= MIN_PAGE_FRAMES:
                self.mark_scroll_complete("flight_details")
            # Loop the scroll back to start
            self.flight_position = screen.WIDTH

    @Animator.KeyFrame.add(0)
    def reset_flight_scrolling(self):
        self.flight_position = screen.WIDTH
        self._page_frame_count = 0
        # reset_scene() also runs clear_screen(), which wipes the canvas —
        # the indicator must repaint on the next frame even when the page
        # state tuple is unchanged (e.g. new flight list, index still 0)
        self._indicator_state = "reset"
