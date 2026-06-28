import email.message
import imaplib
import logging
import os
import time

import requests
from bs4 import BeautifulSoup
from bs4.element import AttributeValueList
from dotenv import load_dotenv

from email_confirmer.contants import (
    IMAP_HOST,
    MAX_RETRIES,
    RETRY_INTERVAL_SECONDS,
    SENDER_FILTER,
    SUBJECT_FILTER,
)

load_dotenv()

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger(__name__)

GMAIL_ADDRESS = os.environ.get("GMAIL_ADDRESS", "")
GMAIL_APP_PASSWORD = os.environ.get("GMAIL_APP_PASSWORD", "")


def connect_to_inbox() -> imaplib.IMAP4_SSL:
    logger.info("Connecting to Gmail IMAP")
    mail = imaplib.IMAP4_SSL(IMAP_HOST)
    mail.login(GMAIL_ADDRESS, GMAIL_APP_PASSWORD)
    mail.select("inbox")

    return mail


def search_confirmation_emails(mail: imaplib.IMAP4_SSL) -> list[bytes]:
    _, uids = mail.search(
        None,
        f'(UNSEEN FROM "{SENDER_FILTER}" SUBJECT "{SUBJECT_FILTER}")',
    )
    return uids[0].split()


def extract_body(msg: email.message.Message) -> str:
    if msg.is_multipart():
        return "".join(
            part.get_payload(decode=True).decode("utf-8", errors="ignore")
            for part in msg.walk()
            if part.get_content_type() in ("text/html", "text/plain")
        )
    return msg.get_payload(decode=True).decode("utf-8", errors="ignore")


def extract_verify_url(body: str) -> str | AttributeValueList | None:
    soup = BeautifulSoup(body, "html.parser")
    anchor = soup.find(
        "a",
        href=lambda href: (
            href and "platform.inschrijven.nl" in href and "verify=" in href
        ),
    )
    if anchor:
        return anchor["href"]
    return None


def mark_as_read(mail: imaplib.IMAP4_SSL, uid: bytes) -> None:
    mail.store(uid, "+FLAGS", "\\Seen")


def fetch_verify_url() -> str | None:
    with connect_to_inbox() as mail:
        uids = search_confirmation_emails(mail)

        for uid in uids:
            _, msg_data = mail.fetch(uid, "(RFC822)")
            msg = email.message_from_bytes(msg_data[0][1])
            body = extract_body(msg)
            verify_url = extract_verify_url(body)

            if verify_url:
                logger.info("Found verify URL in email UID %s", uid.decode())
                mark_as_read(mail, uid)
                return verify_url

            logger.warning(
                "Matching email found but no verify URL in body (UID %s)", uid.decode()
            )

    logger.info("No unread confirmation emails found")
    return None


def check_confirmation_response(html: str) -> tuple[bool, str | None]:
    soup = BeautifulSoup(html, "html.parser")
    error = soup.find("div", style=lambda s: s and "background:#f7d9d9" in s)
    if error:
        return False, error.get_text().strip()
    return True, None


def confirm_registration(verify_url: str) -> tuple[bool, str | None]:
    try:
        logger.info("GETting verify URL: %s", verify_url)
        response = requests.get(verify_url, timeout=10)
        response.raise_for_status()

        confirmed, error_msg = check_confirmation_response(response.text)
        if not confirmed:
            logger.warning("Confirmation rejected by server: %s", error_msg)
            return False, error_msg

        logger.info("Confirmation successful: HTTP %d", response.status_code)
        return True, None
    except requests.RequestException as e:
        logger.error("Failed to confirm registration: %s", e)
        return False, None


def build_telegram_message(
    confirmed: bool, verify_url: str, error_msg: str | None = None
) -> str:
    if confirmed:
        return (
            "✅ Email address confirmed!\n\n"
            "Your registration for the <b>Texel Halve Marathon</b>"
            " has been confirmed."
        )
    if error_msg:
        return f"⚠️ Confirmation failed!\n\n<i>{error_msg}</i>"
    return "⚠️ Confirmation failed!"


def send_telegram_notification(
    confirmed: bool, verify_url: str, error_msg: str | None = None
) -> None:
    bot_token = os.environ.get("TELEGRAM_BOT_TOKEN")
    chat_id = os.environ.get("TELEGRAM_BOT_CHAT_ID")

    if not bot_token or not chat_id:
        logger.warning("Telegram secrets not configured, printing to stdout instead")
        print(f"Confirmed: {confirmed} — {verify_url}")
        return

    message = build_telegram_message(confirmed, verify_url, error_msg)
    response = requests.post(
        f"https://api.telegram.org/bot{bot_token}/sendMessage",
        json={
            "chat_id": chat_id,
            "text": message,
            "parse_mode": "HTML",
            "reply_markup": {
                "inline_keyboard": [
                    [
                        {"text": "☑️ Confirm manually", "url": verify_url},
                    ]
                ]
            },
        },
        timeout=20,
    )
    response.raise_for_status()
    logger.info("Telegram notification sent (confirmed=%s)", confirmed)


def wait_and_confirm() -> None:
    logger.info(
        "Waiting for confirmation email (max %d retries, %ds interval)",
        MAX_RETRIES,
        RETRY_INTERVAL_SECONDS,
    )

    for attempt in range(1, MAX_RETRIES + 1):
        logger.info(
            "Attempt %d/%d: checking for confirmation email", attempt, MAX_RETRIES
        )

        verify_url = fetch_verify_url()
        if verify_url:
            confirmed, error_msg = confirm_registration(verify_url)
            send_telegram_notification(confirmed, verify_url, error_msg)
            return

        if attempt < MAX_RETRIES:
            logger.info("No email yet, retrying in %ds", RETRY_INTERVAL_SECONDS)
            time.sleep(RETRY_INTERVAL_SECONDS)

    logger.error("Confirmation email not received after %d attempts", MAX_RETRIES)
    send_telegram_notification(
        confirmed=False,
        verify_url="",
        error_msg=(
            f"Confirmation email not received after {MAX_RETRIES} attempts."
            f" Please confirm manually on inschrijven.nl."
        ),
    )


def main() -> None:
    logger.info("Starting email confirmer")

    verify_url = fetch_verify_url()
    if not verify_url:
        logger.info("Nothing to confirm, exiting")
        return

    confirmed, error_msg = confirm_registration(verify_url)
    send_telegram_notification(confirmed, verify_url, error_msg)
    logger.info("Done")


if __name__ == "__main__":
    main()
