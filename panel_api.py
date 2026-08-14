"""Provider selector preserving the existing marzban_api-shaped interface."""
import config
from marzban_api import marzban_api as _marzban_api
from rebecca_api import rebecca_api as _rebecca_api

panel_api = _rebecca_api if config.PANEL_PROVIDER == "rebecca" else _marzban_api
# Compatibility name keeps handler changes mechanical and small.
marzban_api = panel_api
