"""Shared authorization; owners are immutable, staff are restored from the database."""
import config
OWNER_IDS = tuple(dict.fromkeys(int(x) for x in config.SUDO_ADMINS))


def is_owner(user_id):
    return int(user_id) in OWNER_IDS


def is_staff(user_id):
    return int(user_id) in config.SUDO_ADMINS
