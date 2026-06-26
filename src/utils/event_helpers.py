import re
from urllib.parse import urlparse, parse_qs

from utils.helpers import clean_text

PRICE_PATTERN = re.compile(r"€\s?\d+(?:[,.]\d{2})?")


KNOWN_EVENT_TYPES = [
    "Hele Marathon - Bootstart",
    "Hele Marathon - Eilandstart",
    "Halve Marathon - Bootstart",
    "Halve Marathon - Eilandstart",
    "1/4 Marathon",
    "1/8e Marathon - Den Hoorn",
]

def extract_koop_id(url: str) -> str | None:
    parsed = urlparse(url)
    query = parse_qs(parsed.query)
    values = query.get("koop")
    return values[0] if values else None

def remove_reageer(value: str) -> str:
    return clean_text(value.lower().replace("reageer", ""))


def extract_price(context: str) -> str | None:
    match = PRICE_PATTERN.search(context)
    return clean_text(match.group(0)) if match else None


def extract_event_type(context: str) -> str | None:
    for event_type in KNOWN_EVENT_TYPES:
        if event_type.lower() in context.lower():
            return event_type

    return None


def extract_seller(
    context: str,
    event_type: str | None,
    price: str | None,
) -> str | None:
    seller = context

    if event_type:
        seller = seller.replace(event_type, "")

    if price:
        seller = seller.replace(price, "")

    seller = remove_reageer(seller)

    # Remove common separators that may remain after stripping metadata.
    seller = seller.replace(" - ", " ")
    seller = seller.replace(" | ", " ")
    seller = seller.replace("•", " ")

    seller = clean_text(seller)

    return seller or None