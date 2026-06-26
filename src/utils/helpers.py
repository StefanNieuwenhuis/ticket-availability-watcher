import re
from datetime import datetime, timezone


def now_utc_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")

def clean_text(value: str) -> str:
    return " ".join(value.split()).strip()

def normalize_for_match(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", value.lower()).strip()


