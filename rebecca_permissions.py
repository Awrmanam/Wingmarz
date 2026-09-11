"""Explicit least-privilege permissions for Wingmarz-managed Rebecca admins."""
from __future__ import annotations

from typing import Any


def standard_user_only_permissions() -> dict[str, Any]:
    """Return the only permission profile Wingmarz grants to reseller admins.

    Managed admins may fully manage *their own users*, but have no access to
    Rebecca infrastructure, hosts, services, other admins, or sudo sections.
    A fresh dict is returned on every call so callers cannot mutate a global.
    """
    return {
        "users": {
            "create": True,
            "delete": True,
            "reset_usage": True,
            "periodic_usage_reset": True,
            "revoke": True,
            "create_on_hold": True,
            "allow_unlimited_data": True,
            "allow_unlimited_expire": True,
            "allow_next_plan": True,
            "advanced_actions": True,
            "set_flow": True,
            "allow_custom_key": True,
            "max_data_limit_per_user": None,
        },
        "admin_management": {
            "can_view": False,
            "can_edit": False,
            "can_manage_sudo": False,
            "manage_sessions": False,
            "manage_2fa": False,
        },
        "sections": {
            "usage": False,
            "admins": False,
            "services": False,
            "hosts": False,
            "nodes": False,
            "integrations": False,
            "xray": False,
        },
        "self_permissions": {
            "self_myaccount": True,
            "self_change_password": True,
            "self_api_keys": True,
            "self_sessions": True,
            "self_2fa": True,
            "self_placeholders": False,
        },
        "sudo": {
            "nodes": False,
            "xray": False,
            "settings": False,
            "subscriptions": False,
            "backups": False,
            "maintenance": False,
            "phpmyadmin": False,
        },
    }


def permissions_are_user_only(raw: Any) -> bool:
    if not isinstance(raw, dict):
        return False
    expected = standard_user_only_permissions()
    for group in ("admin_management", "sections", "sudo"):
        current = raw.get(group)
        if not isinstance(current, dict):
            return False
        for key, value in expected[group].items():
            if current.get(key) is not value:
                return False
    users = raw.get("users")
    if not isinstance(users, dict):
        return False
    for key, value in expected["users"].items():
        if key == "max_data_limit_per_user":
            continue
        if users.get(key) is not value:
            return False
    return True
