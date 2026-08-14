"""Small, deliberately limited Rebecca API adapter.

Only the documented admin creation route needed by sales issuance is used.
Unsupported management operations fail locally rather than guessing routes.
"""
from typing import Any, Dict, Optional

import httpx

import config


class RebeccaAPIError(RuntimeError):
    pass


class RebeccaConflict(RebeccaAPIError):
    pass


class RebeccaAPI:
    def __init__(self) -> None:
        self.base_url = config.REBECCA_URL.rstrip("/")
        self._token = config.REBECCA_BEARER_TOKEN

    def _headers(self) -> Dict[str, str]:
        return {"Authorization": f"Bearer {self._token}", "Content-Type": "application/json"}

    async def test_connection(self) -> bool:
        # There is no safely documented connection-test route in this integration.
        return bool(self.base_url and self._token)

    async def admin_exists(self, username: str) -> bool:
        # Creation is the authoritative uniqueness check (HTTP 409).
        return False

    async def create_admin_verified(
        self, username: str, password: str, telegram_id: int, *,
        data_limit: Optional[int], expire: Optional[int], users_limit: Optional[int]
    ) -> Dict[str, Any]:
        payload = {
            "username": username,
            "password": password,
            "role": "standard",
            "telegram_id": telegram_id,
            "data_limit": data_limit,
            "expire": expire,
            "users_limit": users_limit,
        }
        try:
            async with httpx.AsyncClient(timeout=config.API_TIMEOUT) as client:
                response = await client.post(
                    f"{self.base_url}/api/admin", headers=self._headers(), json=payload
                )
        except httpx.HTTPError as exc:
            # Never include headers/token or the password-bearing request in errors.
            raise RebeccaAPIError("Rebecca admin creation request failed") from exc
        if response.status_code == 409:
            raise RebeccaConflict("Rebecca username already exists")
        if response.status_code not in (200, 201):
            raise RebeccaAPIError(f"Rebecca admin creation failed (HTTP {response.status_code})")
        try:
            admin = response.json()
        except ValueError as exc:
            raise RebeccaAPIError("Rebecca returned an invalid admin response") from exc
        admin = admin.get("admin", admin) if isinstance(admin, dict) else {}
        if admin.get("username") != username or admin.get("role") != "standard":
            raise RebeccaAPIError("Rebecca admin verification failed")
        if str(admin.get("status", "")).lower() != "active":
            raise RebeccaAPIError("Rebecca admin is not active")
        if admin.get("telegram_id") is not None and int(admin["telegram_id"]) != int(telegram_id):
            raise RebeccaAPIError("Rebecca Telegram identity verification failed")
        for key, expected in (("data_limit", data_limit), ("expire", expire), ("users_limit", users_limit)):
            if key in admin and admin[key] != expected:
                raise RebeccaAPIError(f"Rebecca {key} verification failed")
        return admin

    def __getattr__(self, name: str):
        async def unsupported(*args, **kwargs):
            raise RebeccaAPIError(f"Rebecca operation '{name}' has no safely mapped API route")
        return unsupported


rebecca_api = RebeccaAPI()
