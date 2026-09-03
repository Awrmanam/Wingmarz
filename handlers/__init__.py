"""Handler package bootstrap.

Rebecca's catalog router must see a few Rebecca-specific callbacks before the
legacy sudo router (which still owns the raw-ID fallback).  We compose the two
routers here without changing the large legacy handler module; Marzban traffic
continues through the original router unchanged.
"""
from aiogram import Router

from . import sudo_handlers as _sudo_handlers
from .rebecca_services import rebecca_services_router

_original_sudo_router = _sudo_handlers.sudo_router
_composed_sudo_router = Router(name="sudo_composed")
_composed_sudo_router.include_router(rebecca_services_router)
_composed_sudo_router.include_router(_original_sudo_router)

# bot.py imports this attribute from handlers.sudo_handlers after package
# initialization, so expose the composed router under the existing API.
_sudo_handlers.sudo_router = _composed_sudo_router
