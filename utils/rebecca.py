"""Small, provider-specific helpers with no network side effects."""
from html import escape


def parse_service_ids(value: str) -> list[int]:
    """Parse a non-empty comma-separated list of unique positive integers."""
    if not isinstance(value, str) or not value.strip():
        raise ValueError("Rebecca service IDs are required")
    result: list[int] = []
    for raw in value.split(","):
        part = raw.strip()
        if not part or not part.isascii() or not part.isdecimal():
            raise ValueError("Rebecca service IDs must be positive integers")
        service_id = int(part)
        if service_id <= 0:
            raise ValueError("Rebecca service IDs must be positive integers")
        if service_id not in result:
            result.append(service_id)
    return result


def credential_message(username: str, password: str, login_url: str, plan_name: str) -> str:
    """Build one consistently HTML-formatted customer credential message."""
    return (
        "🎉 پنل شما صادر شد!\n\n"
        f"نام کاربری:\n<code>{escape(str(username))}</code>\n\n"
        f"رمز عبور:\n<code>{escape(str(password))}</code>\n\n"
        f"🌐 آدرس ورود:\n<a href=\"{escape(str(login_url), quote=True)}\">{escape(str(login_url))}</a>\n\n"
        f"📦 پلن:\n{escape(str(plan_name))}"
    )
