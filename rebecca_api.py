"""Adapter for Rebecca's documented administrator HTTP API."""
from typing import Any, Dict, Optional
from urllib.parse import quote

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

    async def _request(self, method: str, path: str, **kwargs: Any) -> Any:
        """Issue a Rebecca request without ever including secrets in errors."""
        try:
            async with httpx.AsyncClient(timeout=config.API_TIMEOUT) as client:
                response = await client.request(
                    method, f"{self.base_url}{path}", headers=self._headers(), **kwargs
                )
        except httpx.HTTPError as exc:
            raise RebeccaAPIError("Rebecca API request failed") from exc
        if response.status_code < 200 or response.status_code >= 300:
            raise RebeccaAPIError(
                f"Rebecca API request failed (HTTP {response.status_code})",
                status_code=response.status_code,
            )
        if response.status_code == 204 or not response.content:
            return {}
        try:
            return response.json()
        except ValueError as exc:
            raise RebeccaAPIError("Rebecca returned an invalid response") from exc

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
        data = await self._request("GET", "/api/admins", params={"username": username})
        admins = data.get("admins", data) if isinstance(data, dict) else data
        for admin in admins if isinstance(admins, list) else []:
            if isinstance(admin, dict) and admin.get("username") == username:
                return admin
        return None

    async def get_admin_usage(self, username: str) -> Dict[str, Any]:
        data = await self._request("GET", f"/api/admin/usage/{quote(username, safe='')}")
        return data.get("usage", data) if isinstance(data, dict) else {}

    async def disable_admin(self, username: str, reason: str) -> Dict[str, Any]:
        data = await self._request(
            "POST", f"/api/admin/{quote(username, safe='')}/disable", json={"reason": reason}
        )
        return data if isinstance(data, dict) else {}

    async def enable_admin(self, username: str) -> Dict[str, Any]:
        data = await self._request("POST", f"/api/admin/{quote(username, safe='')}/enable")
        return data if isinstance(data, dict) else {}

    async def delete_admin(self, username: str) -> bool:
        await self._request("DELETE", f"/api/admin/{quote(username, safe='')}")
        return True

    async def health_check(self) -> bool:
        """Read-only provider health check; never creates panel data."""
        try:
            async with httpx.AsyncClient(timeout=config.API_TIMEOUT) as client:
                response = await client.get(f"{self.base_url}/api/admin", headers=self._headers())
            return response.status_code == 200
        except httpx.HTTPError:
            return False

    async def list_admins(self) -> list[Dict[str, Any]]:
        """Return admins through Rebecca's official read-only list route."""
        try:
            async with httpx.AsyncClient(timeout=config.API_TIMEOUT) as client:
                response = await client.get(f"{self.base_url}/api/admins", headers=self._headers())
        except httpx.HTTPError as exc:
            raise RebeccaAPIError("Rebecca admin list request failed") from exc
        if response.status_code != 200:
            raise RebeccaAPIError("Rebecca admin list failed", status_code=response.status_code)
        data = response.json()
        admins = data.get("admins", data) if isinstance(data, dict) else data
        return [item for item in admins if isinstance(item, dict)] if isinstance(admins, list) else []

rebecca_api = RebeccaAPI()
