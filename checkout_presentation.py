"""Customer-facing plan summaries without provider or database identities."""
from html import escape
import math
import config


def plan_summary(plan):
    traffic = getattr(plan, 'traffic_limit_bytes', None)
    seconds = getattr(plan, 'time_limit_seconds', None)
    users = getattr(plan, 'max_users', None)
    return config.MESSAGES['sales_plan_summary'].format(
        plan_name=escape(str(plan.name)),
        traffic='نامحدود' if traffic is None else f'{int(traffic) / 1024**3:g} GB',
        duration='نامحدود' if seconds is None else f'{max(1, math.ceil(int(seconds)/86400))} روز',
        users='نامحدود' if users is None else str(int(users)),
        price=f'{int(plan.price):,}',
    )
