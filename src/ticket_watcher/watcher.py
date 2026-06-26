import json
import os
from pathlib import Path
from urllib.parse import urljoin

import requests
from bs4 import BeautifulSoup

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

def load_seen_links() -> set[str]:
    if not STATE_FILE.exists():
        return set()

    try:
        return set(json.loads(STATE_FILE.read_text(encoding="utf-8")))
    except json.JSONDecodeError:
        return set()

def save_seen_links(links: set[str]) -> None:
    STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    STATE_FILE.write_text(
        json.dumps(sorted(links), indent=2),
        encoding="utf-8",
    )

def fetch_reageer_links() -> list[dict]:
    response = requests.get(EVENT_URL, headers=HEADERS, timeout=DEFAULT_TIMEOUT_MS)
    response.raise_for_status()

    soup = BeautifulSoup(response.text, 'html.parser')
    items = []

    for a in soup.find_all('a'):
        href = a.get('href')
        text = a.get_text(' ', strip=True)

        if not href:
            continue

        if 'koop=' not in href.lower() and 'reageer' not in text.lower():
            continue

        container = a.find_parent(["tr", "li", "div", "section", "article"])
        context = container.get_text(" ", strip=True) if container else text

        items.append(
            {
                "url": urljoin(EVENT_URL, href),
                "text": text,
                "context": context,
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
        lines.append(item["context"] or "Ticket available")
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
    dry_run = os.environ.get("DRY_RUN") == "1"
    seen = load_seen_links()
    items = fetch_reageer_links()

    current_links = {item["url"] for item in items}
    new_items = [item for item in items if item["url"] not in seen]

    print(f"Found {len(current_links)} Reageer link(s).")
    print(f"Previously seen: {len(seen)}.")

    if new_items:
        print("New Reageer links:")
        for item in new_items:
            print(item["url"])

        if dry_run:
            print("DRY_RUN=1, not sending Telegram message.")
        else:
            send_telegram(new_items)
    else:
        print("No new links.")

    seen.update(current_links)
    save_seen_links(seen)


if __name__ == "__main__":
    main()
