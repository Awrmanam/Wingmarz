from aiogram import Router

from operations_service import operations_service
from premium_ui_service import premium_ui_service
from rebecca_permissions_sync import reconcile_managed_admin_permissions
from trial_experience_service import trial_experience_service


operations_bootstrap_router = Router(name="operations_bootstrap")


async def bootstrap_operations() -> None:
    """Initialize operational tables and restore DB-backed runtime settings."""
    await operations_service.ensure_schema()
    await trial_experience_service.ensure_schema()
    await premium_ui_service.init()
    await operations_service.sync_runtime_admins()
    # Security repair for accounts created before explicit Rebecca permissions
    # were introduced. This is best-effort and never blocks startup on outage.
    await reconcile_managed_admin_permissions()


operations_bootstrap_router.startup.register(bootstrap_operations)
