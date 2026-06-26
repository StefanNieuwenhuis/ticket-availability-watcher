import re
from urllib.parse import urlparse, parse_qs

from utils.helpers import clean_text, normalize_for_match

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
    normalized_context = normalize_for_match(context)

    for event_type in KNOWN_EVENT_TYPES:
        normalized_event_type = normalize_for_match(event_type)

        if normalized_event_type in normalized_context:
            return event_type

    return None

def remove_metadata_from_context(
    context: str,
    event_type: str | None,
    price: str | None,
) -> str:
    result = context

    if event_type:
        # Remove both the canonical format and the separator-free version.
        result = re.sub(
            re.escape(event_type),
            "",
            result,
            flags=re.IGNORECASE,
        )

        separator_free_event_type = event_type.replace(" - ", " ")
        result = re.sub(
            re.escape(separator_free_event_type),
            "",
            result,
            flags=re.IGNORECASE,
        )

    if price:
        result = re.sub(
            re.escape(price),
            "",
            result,
            flags=re.IGNORECASE,
        )

    result = remove_reageer(result)
    result = result.replace(" - ", " ")
    result = result.replace(" | ", " ")
    result = result.replace("•", " ")

    return clean_text(result)

def extract_seller(
    context: str,
    event_type: str | None,
    price: str | None,
) -> str | None:
    seller = remove_metadata_from_context(
        context=context,
        event_type=event_type,
        price=price,
    )

    return seller or None