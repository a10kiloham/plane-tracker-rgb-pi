import logging
from datetime import datetime
from utilities.temperature import grab_forecast
from utilities.animator import Animator
from setup import colours, fonts, frames
from rgbmatrix import graphics

try:
    from config import ISS_ENABLED
except (ModuleNotFoundError, ImportError):
    ISS_ENABLED = False

try:
    from utilities.iss import get_iss_alert
except ImportError:
    get_iss_alert = None

# Setup
DATE_FONT = fonts.extrasmall
DATE_POSITION = (40, 11)
CHAR_WIDTH = 4
# Steel blue for passes that won't be eye-visible (matches isspass.py theme)
ISS_ALERT_DIM_COLOUR = graphics.Color(100, 130, 180)

class DateScene(object):
    def __init__(self):
        super().__init__()
        self._last_date = None
        self.today_moonphase = None
        self.last_fetched_moonphase = None  # Store the date of the last forecast 


    def moonphase(self):
        now = datetime.now()

        # Only fetch forecast if it's a new day
        if self.last_fetched_moonphase != now.day:
            try:
                forecast = grab_forecast(tag="DateScene")
                if not forecast:  # None or empty list
                    logging.error("Forecast data missing or API error (moon phase).")
                    # Return cached moon phase if available, otherwise None
                    return self.today_moonphase

                for day in forecast:
                    forecast_date = day['startTime'][:10]
                    if forecast_date == now.strftime('%Y-%m-%d'):
                        utc_moonphase = int(day["values"]["moonPhase"])
                        self.today_moonphase = utc_moonphase
                        self.last_fetched_moonphase = now.day
                        break

            except Exception as e:
                logging.error(f"Error fetching forecast for moon phase: {e}")
                return self.today_moonphase  # Return cached if available

        # Return cached value if fetch is not needed or on error
        return self.today_moonphase

    def map_moon_phase_to_color(self, moonphase):
        # Define the two colors for the specific moon phases
        colors = [
            [colours.DARK_PURPLE, colours.DARK_PURPLE],  # Moon phase 0
            [colours.DARK_PURPLE, colours.DARK_MID_PURPLE],  # Moon phase 1
            [colours.DARK_PURPLE, colours.WHITE],  # Moon phase 2
            [colours.DARK_MID_PURPLE, colours.WHITE],  # Moon phase 3
            [colours.GREY, colours.GREY],  # Moon phase 4 (no gradient, same color)
            [colours.WHITE, colours.DARK_MID_PURPLE],  # Moon phase 5
            [colours.WHITE, colours.DARK_PURPLE],  # Moon phase 6
            [colours.DARK_MID_PURPLE, colours.DARK_PURPLE]  # Moon phase 7 (middle_purple to PINK_DARK gradient)
            # Define colors for the remaining phases as needed
        ]

        # Ensure moonphase is within the valid range
        moonphase = min(max(moonphase, 0), 7)

        # Get the corresponding colors for the moon phase
        gradient_start_color, gradient_end_color = colors[moonphase]

        return gradient_start_color, gradient_end_color  # Return both colors

    def draw_gradient_text(self, text, x, y, start_color, end_color):
        text_length = len(text)
        char_width = 4  # Width of each character
        for i, char in enumerate(text):
            position = i / (text_length - 1)
            r = int(start_color.red + (end_color.red - start_color.red) * position)
            g = int(start_color.green + (end_color.green - start_color.green) * position)
            b = int(start_color.blue + (end_color.blue - start_color.blue) * position)
            char_color = graphics.Color(r, g, b)
            char_x = x + (i * char_width)
            _ = graphics.DrawText(
                self.canvas,
                DATE_FONT,
                char_x,
                y,
                char_color,
                char,
            )

    def _get_iss_alert(self):
        """Upcoming ISS pass alert ({"text": "ISS 3m", ...}) or None."""
        if not ISS_ENABLED or get_iss_alert is None:
            return None
        try:
            return get_iss_alert()
        except Exception as e:
            logging.debug(f"ISS alert unavailable: {e}")
            return None

    @Animator.KeyFrame.add(frames.PER_SECOND * 1)
    def date(self, count):
        # ISS takeover — yield and redraw when it ends
        if getattr(self, "_iss_active", False):
            self._redraw_date = True
            return

        now = datetime.now()
        current_date = now.strftime("%b %d")

        # Flag for forced redraw if new data arrived
        if len(self._data):
            self._redraw_date = True
            return

        # When an ISS pass is imminent, alternate the date with the alert
        # text ("ISS 3m") every 4 seconds
        iss_alert = self._get_iss_alert()
        show_alert = bool(iss_alert) and (now.second // 4) % 2 == 1

        if show_alert:
            display_text = iss_alert["text"]
            # Right-align so longer alerts ("ISS 10m") don't clip
            display_x = 64 - len(display_text) * CHAR_WIDTH
        else:
            display_text = current_date
            display_x = DATE_POSITION[0]

        # Get moon phase
        moon_phase_value = self.moonphase()
        if moon_phase_value is None:
            start_color = end_color = colours.RED
        else:
            start_color, end_color = self.map_moon_phase_to_color(moon_phase_value)

        # Clear previous text if needed
        if self._last_date and (self._last_date != display_text or getattr(self, "_redraw_date", False)):
            graphics.DrawText(
                self.canvas,
                DATE_FONT,
                getattr(self, "_last_date_x", DATE_POSITION[0]),
                DATE_POSITION[1],
                colours.BLACK,
                self._last_date,
            )

        self._last_date = display_text
        self._last_date_x = display_x

        if show_alert:
            alert_colour = (
                colours.WHITE if iss_alert.get("visible") else ISS_ALERT_DIM_COLOUR
            )
            graphics.DrawText(
                self.canvas,
                DATE_FONT,
                display_x,
                DATE_POSITION[1],
                alert_colour,
                display_text,
            )
        else:
            # Draw date unconditionally
            self.draw_gradient_text(current_date, DATE_POSITION[0], DATE_POSITION[1], start_color, end_color)

        # Reset redraw flag
        self._redraw_date = False