"""Small, deliberately limited Rebecca API adapter.

Only the documented admin creation route needed by sales issuance is used.
Unsupported management operations fail locally rather than guessing routes.
"""
from typing import Any, Dict, Optional

import httpx

import config


class RebeccaAPIError(RuntimeError):
    def __init__(self, message: str, *, status_code: Optional[int] = None):
        super().__init__(message)
        self.status_code = status_code

    @property
    def definitive(self) -> bool:
        return self.status_code in {400, 401, 403, 422}


class RebeccaConflict(RebeccaAPIError):
    pass


class RebeccaAPI:
    def __init__(self) -> None:
        self.base_url = config.REBECCA_URL.rstrip("/")
        self._token = config.REBECCA_BEARER_TOKEN

    def _headers(self) -> Dict[str, str]:
        return {"Authorization": f"Bearer {self._token}", "Content-Type": "application/json"}

    def verify_admin(self, admin: Dict[str, Any], username: str, telegram_id: int, *,
                     data_limit: Optional[int], expire: Optional[int], users_limit: Optional[int],
                     services: list[int]) -> Dict[str, Any]:
        if admin.get("username") != username or admin.get("role") != "standard":
            raise RebeccaAPIError("Rebecca admin verification failed")
        if str(admin.get("status", "")).lower() != "active":
            raise RebeccaAPIError("Rebecca admin is not active")
        if admin.get("telegram_id") is not None and int(admin["telegram_id"]) != int(telegram_id):
            raise RebeccaAPIError("Rebecca Telegram identity verification failed")
        for key, expected in (("data_limit", data_limit), ("expire", expire), ("users_limit", users_limit)):
            if key in admin and admin[key] != expected:
                raise RebeccaAPIError(f"Rebecca {key} verification failed")
        if "services" in admin:
            returned = {int(item.get("id")) if isinstance(item, dict) else int(item) for item in admin["services"]}
            if not set(services).issubset(returned):
                raise RebeccaAPIError("Rebecca services verification failed")
        return admin

    async def create_admin_verified(
        self, username: str, password: str, telegram_id: int, *,
        data_limit: Optional[int], expire: Optional[int], users_limit: Optional[int],
        services: list[int],
    ) -> Dict[str, Any]:
        payload = {
            "username": username,
            "password": password,
            "role": "standard",
            "telegram_id": telegram_id,
            "data_limit": data_limit,
            "expire": expire,
            "users_limit": users_limit,
            "services": services,
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
            raise RebeccaAPIError(f"Rebecca admin creation failed (HTTP {response.status_code})", status_code=response.status_code)
        try:
            admin = response.json()
        except ValueError as exc:
            raise RebeccaAPIError("Rebecca returned an invalid admin response") from exc
        admin = admin.get("admin", admin) if isinstance(admin, dict) else {}
        return self.verify_admin(admin, username, telegram_id, data_limit=data_limit,
                                 expire=expire, users_limit=users_limit, services=services)

    async def find_admin(self, username: str) -> Optional[Dict[str, Any]]:
        """Find an exact username through Rebecca's documented admin list."""
        try:
            async with httpx.AsyncClient(timeout=config.API_TIMEOUT) as client:
                response = await client.get(
                    f"{self.base_url}/api/admins", headers=self._headers(), params={"username": username}
                )
        except httpx.HTTPError as exc:
            raise RebeccaAPIError("Rebecca admin lookup request failed") from exc
        if response.status_code != 200:
            raise RebeccaAPIError("Rebecca admin lookup failed", status_code=response.status_code)
        data = response.json()
        admins = data.get("admins", data) if isinstance(data, dict) else data
        for admin in admins if isinstance(admins, list) else []:
            if isinstance(admin, dict) and admin.get("username") == username:
                return admin
        return None

    async def health_check(self) -> bool:
        """Read-only provider health check; never creates panel data."""
        try:
            async with httpx.AsyncClient(timeout=config.API_TIMEOUT) as client:
                response = await client.get(f"{self.base_url}/api/admin", headers=self._headers())
            return response.status_code == 200
        except httpx.HTTPError:
            return False

rebecca_api = RebeccaAPI()
