import json
import logging
import os
from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path
from urllib.parse import urljoin

import requests
from bs4 import BeautifulSoup
from dotenv import load_dotenv

load_dotenv()

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger(__name__)

STATE_FILE = Path(os.environ.get("STATE_FILE", "seen_reageer_links.json"))
EVENT_URL = os.environ.get(
    "EVENT_URL",
    "https://platform.inschrijven.nl/2026092751189",
)

LISTING_TYPE_FILTER = "Halve Marathon - Bootstart"


@dataclass
class Listing:
    id: str
    listing_type: str
    seller: str
    price: Decimal
    url: str

    def __str__(self) -> str:
        return f"{self.listing_type} • {self.seller} • € {self.price} • {self.url}"

    def to_dict(self) -> dict[str, str]:
        return {
            "id": self.id,
            "listing_type": self.listing_type,
            "seller": self.seller,
            "price": str(self.price),
            "url": self.url,
        }

    @classmethod
    def from_dict(cls, data: dict[str, str]) -> "Listing":
        return cls(
            id=data["id"],
            listing_type=data["listing_type"],
            seller=data["seller"],
            price=Decimal(data["price"]),
            url=data["url"],
        )


def load_seen_listings() -> list[Listing]:
    if not STATE_FILE.exists():
        logger.info("No state file found at %s, starting fresh", STATE_FILE)
        return []

    try:
        raw: list[dict[str, str]] = json.loads(STATE_FILE.read_text(encoding="utf-8"))
        listings = [Listing.from_dict(item) for item in raw]
        logger.info("Loaded %d seen listing(s) from %s", len(listings), STATE_FILE)
        return listings
    except (json.JSONDecodeError, KeyError, ValueError) as e:
        logger.warning("Failed to parse state file: %s", e)
        return []


def fetch_listings() -> list[Listing]:
    logger.info("Fetching listings from %s", EVENT_URL)
    response = requests.get(EVENT_URL)
    response.raise_for_status()

    soup = BeautifulSoup(response.text, "html.parser")

    header = soup.find(
        "div",
        class_="blok-kop",
        string=lambda s: s and "beschikbare startnummers" in s.lower(),
    )
    if not header:
        logger.warning("Could not find 'beschikbare startnummers' block on page")
        return []
    blok = header.parent

    listings: list[Listing] = []
    for row in blok.select("tbody.ho"):
        b_tag = row.select_one('td[colspan="2"] b')
        if not b_tag:
            continue
        raw = b_tag.get_text()

        listing_type, price_raw = [part.strip() for part in raw.split("•")]
        price = Decimal(price_raw.replace("€", "").replace(",", ".").strip())

        seller_td = row.select("tr")[1].select("td")[0]
        seller = str(seller_td.find(string=True, recursive=False)).strip()

        a_tag = row.select_one("a.btn")
        if not a_tag:
            continue
        url_raw = str(a_tag["href"])
        url_hash = url_raw.removeprefix("?koop=")
        url = urljoin(EVENT_URL, url_raw)

        listings.append(
            Listing(
                id=url_hash,
                listing_type=listing_type,
                seller=seller,
                price=price,
                url=url,
            )
        )

    logger.info("Found %d listing(s) on page", len(listings))
    return listings


def get_new_listings(
    seen_listings: list[Listing], current_listings: list[Listing]
) -> tuple[list[Listing], list[Listing]]:
    # index by id for O(1) lookup
    current: dict[str, Listing] = {listing.id: listing for listing in current_listings}
    seen: dict[str, Listing] = {listing.id: listing for listing in seen_listings}

    new_listings: list[Listing] = [
        listing for lid, listing in current.items() if lid not in seen
    ]
    removed_listings: list[Listing] = [
        listing for lid, listing in seen.items() if lid not in current
    ]

    logger.info("%d new listing(s) detected", len(new_listings))
    logger.info("%d removed listing(s) detected", len(removed_listings))

    return new_listings, removed_listings


def format_telegram_message(listing: Listing, submitted: bool | None) -> str:
    if submitted is None:
        status = "⏭️ Auto-submit skipped (wrong type)"
    elif submitted:
        status = "✅ Form auto-submitted!"
    else:
        status = "⚠️ Auto-submit failed, reply manually"

    return "".join(
        [
            "🎟️ New ticket available!\n\n",
            f"🏃 {listing.listing_type}\n",
            f"👤 {listing.seller}\n",
            f"💶 €{listing.price}\n",
            f"{status}\n",
        ]
    )


def send_telegram_notification(listing: Listing, submitted: bool | None) -> None:
    bot_token = os.environ.get("TELEGRAM_BOT_TOKEN")
    chat_id = os.environ.get("TELEGRAM_BOT_CHAT_ID")

    if not bot_token or not chat_id:
        logger.warning("Telegram secrets not configured, printing to stdout instead")
        print(listing)
        return

    message = format_telegram_message(listing, submitted)
    logger.info("Sending Telegram notification for listing: %s", listing.id)

    response = requests.post(
        f"https://api.telegram.org/bot{bot_token}/sendMessage",
        json={
            "chat_id": chat_id,
            "text": message,
            "parse_mode": "HTML",
            "reply_markup": {
                "inline_keyboard": [
                    [
                        {"text": "📯 Reply to seller", "url": listing.url},
                    ]
                ]
            },
        },
        timeout=20,
    )
    response.raise_for_status()
    logger.info("Notification sent for listing: %s", listing.id)


def save_seen_listings(listings: list[Listing]) -> None:
    try:
        data = [listing.to_dict() for listing in listings]
        STATE_FILE.write_text(json.dumps(data, indent=2), encoding="utf-8")
        logger.info("Saved %d listing(s) to %s", len(listings), STATE_FILE)
    except OSError as e:
        logger.error("Failed to save seen listings: %s", e)


def submit_reply_form(listing_id: str, listing_url: str) -> bool:
    try:
        name = os.environ.get("NAME")
        email = os.environ.get("EMAIL")
        phone_no = os.environ.get("PHONE_NO")

        response = requests.post(
            listing_url,
            data={
                "actie": "opslaan",
                "naam": name,
                "emai": email,
                "telf": phone_no,
            },
            timeout=20,
        )
        response.raise_for_status()

        soup = BeautifulSoup(response.text, "html.parser")
        error = soup.find("div", class_="fouthint")

        if error:
            logger.warning(
                "Form rejected for listing %s: %s", listing_id, error.get_text().strip()
            )
            return False

        logger.info("Form submitted for listing: %s", listing_id)
        return True
    except requests.RequestException as e:
        logger.error("Failed to submit form for listing %s: %s", listing_id, e)
        return False


def auto_submit_reply_form(listing: Listing) -> bool | None:
    if listing.listing_type != LISTING_TYPE_FILTER:
        logger.info("Skipping auto-submit for listing type: %s", listing.listing_type)
        return None

    submitted = submit_reply_form(listing.id, listing.url)

    if submitted:
        logger.info("Auto-submitted form for: %s", listing)
    else:
        logger.warning("Auto-submit failed for: %s", listing)

    return submitted


def main():
    logger.info("Starting to look for new ticket listings")
    seen_listings = load_seen_listings()
    current_listings = fetch_listings()
    new_listings, removed_listings = get_new_listings(seen_listings, current_listings)

    if not new_listings and not removed_listings:
        logger.info("No changes detected, skipping save")
        logger.info("Done")
        return

    if removed_listings:
        logger.info("%d listing(s) removed from page", len(removed_listings))

    for listing in new_listings:
        submitted = auto_submit_reply_form(listing)
        send_telegram_notification(listing, submitted)

    save_seen_listings(current_listings)
    logger.info("Done")


if __name__ == "__main__":
    main()
