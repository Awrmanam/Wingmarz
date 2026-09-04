from aiogram import Router

from operations_service import operations_service
from trial_experience_service import trial_experience_service


operations_bootstrap_router = Router(name="operations_bootstrap")


async def bootstrap_operations() -> None:
    """Initialize operational tables and restore DB-backed SUDO admins."""
    await operations_service.ensure_schema()
    await trial_experience_service.ensure_schema()
    await operations_service.sync_runtime_admins()


operations_bootstrap_router.startup.register(bootstrap_operations)
