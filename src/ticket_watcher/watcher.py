import json
import os
from pathlib import Path
from urllib.parse import urljoin

import requests
from bs4 import BeautifulSoup

from utils.event_helpers import extract_koop_id, remove_reageer, extract_event_type, extract_price, extract_seller
from utils.helpers import now_utc_iso, clean_text

DEFAULT_TIMEOUT_MS=20
EVENT_URL = os.environ.get(
    "EVENT_URL",
    "https://platform.inschrijven.nl/2026092751189",
)
STATE_FILE = Path(os.environ.get("STATE_FILE", "seen_reageer_links.json"))

HEADERS = {
    'User-Agent': (
        'Mozilla/5.0 ticket watcher; checks every n minutes via GitHub Actions'
    )
}

def load_seen_links() -> dict:
    if not STATE_FILE.exists():
        return {}

    try:
        data = json.loads(STATE_FILE.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {}

    # Very old format: ["https://...?koop=abc"]
    if isinstance(data, list):
        migrated = {}

        for url in data:
            koop_id = extract_koop_id(url)

            if not koop_id:
                continue

            migrated[koop_id] = {
                "koop_id": koop_id,
                "url": url,
                "first_seen_at": None,
                "last_seen_at": None,
                "last_missing_at": None,
                "currently_visible": False,
                "seen_count": 1,
                "text": "",
                "context": "",
                "seller": None,
                "event_type": None,
                "price": None,
            }

        return migrated

    if not isinstance(data, dict):
        return {}

    migrated = {}

    for key, metadata in data.items():
        if not isinstance(metadata, dict):
            continue

        # Old format used URL as key.
        url = metadata.get("url") or key
        koop_id = metadata.get("koop_id") or extract_koop_id(url)

        if not koop_id:
            continue

        context = remove_reageer(metadata.get("context", ""))
        event_type = metadata.get("event_type") or extract_event_type(context)
        price = metadata.get("price") or extract_price(context)
        seller = metadata.get("seller") or extract_seller(
            context,
            event_type=event_type,
            price=price,
        )

        migrated[koop_id] = {
            **metadata,
            "koop_id": koop_id,
            "url": url,
            "context": context,
            "seller": seller,
            "event_type": event_type,
            "price": price,
        }

    return migrated


def save_seen_links(seen: dict) -> None:
    STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    STATE_FILE.write_text(
        json.dumps(seen, indent=2, sort_keys=True, ensure_ascii=False),
        encoding="utf-8",
    )

def fetch_reageer_links() -> list[dict]:
    response = requests.get(EVENT_URL, headers=HEADERS, timeout=20)
    response.raise_for_status()

    soup = BeautifulSoup(response.text, "html.parser")
    items = []

    for a in soup.find_all("a"):
        href = a.get("href")
        text = clean_text(a.get_text(" ", strip=True))

        if not href:
            continue

        if "koop=" not in href.lower() and "reageer" not in text.lower():
            continue

        url = urljoin(EVENT_URL, href)
        koop_id = extract_koop_id(url)

        container = a.find_parent(["tr", "li", "div", "section", "article"])
        raw_context = clean_text(container.get_text(" ", strip=True)) if container else text
        context = remove_reageer(raw_context)

        event_type = extract_event_type(context)
        price = extract_price(context)
        seller = extract_seller(context, event_type=event_type, price=price)

        items.append(
            {
                "koop_id": koop_id,
                "url": url,
                "text": text,
                "context": context,
                "seller": seller,
                "event_type": event_type,
                "price": price,
            }
        )

    return items

def send_telegram(new_items: list[dict]) -> None:
    bot_token = os.environ.get("TELEGRAM_BOT_TOKEN")
    chat_id = os.environ.get("TELEGRAM_CHAT_ID")

    if not bot_token or not chat_id:
        print("Telegram secrets not configured. New items:")
        for item in new_items:
            print(item["url"])
        return

    lines = [
        "🎟️ New ticket available",
        "",
    ]

    for item in new_items:
        if item.get("event_type"):
            lines.append(f"Event: {item['event_type']}")

        if item.get("price"):
            lines.append(f"Price: {item['price']}")

        if item.get("seller"):
            lines.append(f"Seller: {item['seller']}")

        lines.append(item["url"])
        lines.append("")

    message = "\n".join(lines).strip()

    response = requests.post(
        f"https://api.telegram.org/bot{bot_token}/sendMessage",
        json={
            "chat_id": chat_id,
            "text": message,
            "disable_web_page_preview": False,
        },
        timeout=20,
    )
    response.raise_for_status()

def main() -> None:
    seen = load_seen_links()
    items = fetch_reageer_links()

    current_ids = {
        item["koop_id"]
        for item in items
        if item.get("koop_id")
    }

    new_items = [
        item
        for item in items
        if item.get("koop_id") and item["koop_id"] not in seen
    ]

    timestamp = now_utc_iso()

    print(f"Found {len(current_ids)} Reageer link(s).")
    print(f"Previously seen: {len(seen)}.")

    if new_items:
        print("New Reageer links:")
        for item in new_items:
            label = item.get("event_type") or item.get("seller") or item["koop_id"]
            price = f" ({item['price']})" if item.get("price") else ""
            print(f"- {label}{price}: {item['url']}")

        dry_run = os.environ.get("DRY_RUN") == "1"

        if dry_run:
            print("DRY_RUN=1, not sending Telegram message.")
        else:
            send_telegram(new_items)
    else:
        print("No new links.")

    # Mark previously visible links as missing when they are no longer visible.
    for koop_id, metadata in seen.items():
        if koop_id not in current_ids and metadata.get("currently_visible") is True:
            metadata["last_missing_at"] = timestamp
            metadata["currently_visible"] = False

    # Add or update all currently visible links.
    for item in items:
        koop_id = item.get("koop_id")

        if not koop_id:
            continue

        existing = seen.get(koop_id, {})

        first_seen_at = existing.get("first_seen_at") or timestamp
        seen_count = int(existing.get("seen_count", 0)) + 1

        seen[koop_id] = {
            **existing,
            "koop_id": koop_id,
            "url": item.get("url"),
            "first_seen_at": first_seen_at,
            "last_seen_at": timestamp,
            "last_missing_at": existing.get("last_missing_at"),
            "currently_visible": True,
            "seen_count": seen_count,
            "text": item.get("text", ""),
            "context": item.get("context", ""),
            "seller": item.get("seller"),
            "event_type": item.get("event_type"),
            "price": item.get("price"),
        }

    save_seen_links(seen)


if __name__ == "__main__":
    main()
