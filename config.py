import os

BOT_TOKEN = os.getenv("BOT_TOKEN", "").strip()
ADMIN_IDS_RAW = os.getenv("ADMIN_IDS", "").strip()
DATABASE_PATH = os.getenv("DATABASE_PATH", "shangdi_users.db")


def parse_admin_ids(raw_value: str) -> set[int]:
    result = set()
    for item in raw_value.split(","):
        item = item.strip()
        if item:
            result.add(int(item))
    return result


ADMIN_IDS = parse_admin_ids(ADMIN_IDS_RAW)


def validate_config() -> None:
    if not BOT_TOKEN:
        raise RuntimeError("BOT_TOKEN is missing from Railway Variables.")
    if not ADMIN_IDS:
        raise RuntimeError("ADMIN_IDS is missing from Railway Variables.")
