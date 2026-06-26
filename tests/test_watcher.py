import json

import responses

from ticket_watcher import watcher


FAKE_EVENT_URL = "https://example.test/event"


HTML_WITH_TICKET = """
<html>
  <body>
    <div class="ticket">
      <span>1/8e Marathon - Den Hoorn</span>
      <span>€ 20,00</span>
      <a href="/event?koop=abc123">Reageer</a>
    </div>
  </body>
</html>
"""


HTML_WITHOUT_TICKET = """
<html>
  <body>
    <p>Geen startbewijzen beschikbaar.</p>
  </body>
</html>
"""


@responses.activate
def test_fetch_reageer_links_finds_ticket(monkeypatch):
    monkeypatch.setattr(watcher, "EVENT_URL", FAKE_EVENT_URL)

    responses.add(
        responses.GET,
        FAKE_EVENT_URL,
        body=HTML_WITH_TICKET,
        status=200,
    )

    items = watcher.fetch_reageer_links()

    assert len(items) == 1

    item = items[0]

    assert item["koop_id"] == "abc123"
    assert item["url"] == "https://example.test/event?koop=abc123"
    assert item["text"] == "Reageer"

    assert "reageer" not in item["context"].lower()
    assert "1/8e marathon" in item["context"].lower()

    assert item["event_type"] == "1/8e Marathon - Den Hoorn"
    assert item["price"] == "€ 20,00"
    assert item["seller"] is None


@responses.activate
def test_fetch_reageer_links_no_ticket(monkeypatch):
    monkeypatch.setattr(watcher, "EVENT_URL", FAKE_EVENT_URL)

    responses.add(
        responses.GET,
        FAKE_EVENT_URL,
        body=HTML_WITHOUT_TICKET,
        status=200,
    )

    items = watcher.fetch_reageer_links()

    assert items == []


@responses.activate
def test_send_telegram_posts_message(monkeypatch):
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "fake-token")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "123456789")

    responses.add(
        responses.POST,
        "https://api.telegram.org/botfake-token/sendMessage",
        json={"ok": True, "result": {"message_id": 1}},
        status=200,
    )

    watcher.send_telegram(
        [
            {
                "koop_id": "abc123",
                "url": "https://example.test/event?koop=abc123",
                "context": "1/8e Marathon - Den Hoorn € 20,00",
                "text": "Reageer",
                "event_type": "1/8e Marathon - Den Hoorn",
                "price": "€ 20,00",
                "seller": None,
            }
        ]
    )

    assert len(responses.calls) == 1

    payload = responses.calls[0].request.body
    payload = json.loads(payload)

    assert payload["chat_id"] == "123456789"
    assert "New ticket available" in payload["text"]
    assert "Event: 1/8e Marathon - Den Hoorn" in payload["text"]
    assert "Price: € 20,00" in payload["text"]
    assert "https://example.test/event?koop=abc123" in payload["text"]


@responses.activate
def test_main_sends_only_new_links(monkeypatch, tmp_path):
    state_file = tmp_path / "seen.json"

    monkeypatch.setattr(watcher, "EVENT_URL", FAKE_EVENT_URL)
    monkeypatch.setattr(watcher, "STATE_FILE", state_file)
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "fake-token")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "123456789")

    responses.add(
        responses.GET,
        FAKE_EVENT_URL,
        body=HTML_WITH_TICKET,
        status=200,
    )

    responses.add(
        responses.POST,
        "https://api.telegram.org/botfake-token/sendMessage",
        json={"ok": True},
        status=200,
    )

    watcher.main()

    assert state_file.exists()

    seen = json.loads(state_file.read_text())

    assert set(seen.keys()) == {"abc123"}
    assert seen["abc123"]["koop_id"] == "abc123"
    assert seen["abc123"]["url"] == "https://example.test/event?koop=abc123"
    assert seen["abc123"]["currently_visible"] is True
    assert seen["abc123"]["seen_count"] == 1
    assert seen["abc123"]["last_missing_at"] is None
    assert seen["abc123"]["event_type"] == "1/8e Marathon - Den Hoorn"
    assert seen["abc123"]["price"] == "€ 20,00"
    assert seen["abc123"]["seller"] is None

    post_calls = [
        call for call in responses.calls
        if call.request.method == "POST"
    ]
    assert len(post_calls) == 1


@responses.activate
def test_main_does_not_send_for_already_seen_link(monkeypatch, tmp_path):
    state_file = tmp_path / "seen.json"
    state_file.write_text(
        json.dumps(
            {
                "abc123": {
                    "koop_id": "abc123",
                    "url": "https://example.test/event?koop=abc123",
                    "first_seen_at": "2026-06-26T20:00:00Z",
                    "last_seen_at": "2026-06-26T20:00:00Z",
                    "last_missing_at": None,
                    "currently_visible": True,
                    "seen_count": 1,
                    "text": "Reageer",
                    "context": "1/8e Marathon - Den Hoorn € 20,00",
                    "seller": None,
                    "event_type": "1/8e Marathon - Den Hoorn",
                    "price": "€ 20,00",
                }
            }
        )
    )

    monkeypatch.setattr(watcher, "EVENT_URL", FAKE_EVENT_URL)
    monkeypatch.setattr(watcher, "STATE_FILE", state_file)
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "fake-token")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "123456789")

    responses.add(
        responses.GET,
        FAKE_EVENT_URL,
        body=HTML_WITH_TICKET,
        status=200,
    )

    watcher.main()

    post_calls = [
        call for call in responses.calls
        if call.request.method == "POST"
    ]
    assert len(post_calls) == 0

    seen = json.loads(state_file.read_text())
    assert seen["abc123"]["seen_count"] == 2
    assert seen["abc123"]["currently_visible"] is True


@responses.activate
def test_main_marks_missing_link(monkeypatch, tmp_path):
    state_file = tmp_path / "seen.json"
    state_file.write_text(
        json.dumps(
            {
                "abc123": {
                    "koop_id": "abc123",
                    "url": "https://example.test/event?koop=abc123",
                    "first_seen_at": "2026-06-26T20:00:00Z",
                    "last_seen_at": "2026-06-26T20:00:00Z",
                    "last_missing_at": None,
                    "currently_visible": True,
                    "seen_count": 1,
                    "text": "Reageer",
                    "context": "1/8e Marathon - Den Hoorn € 20,00",
                    "seller": None,
                    "event_type": "1/8e Marathon - Den Hoorn",
                    "price": "€ 20,00",
                }
            }
        )
    )

    monkeypatch.setattr(watcher, "EVENT_URL", FAKE_EVENT_URL)
    monkeypatch.setattr(watcher, "STATE_FILE", state_file)

    responses.add(
        responses.GET,
        FAKE_EVENT_URL,
        body=HTML_WITHOUT_TICKET,
        status=200,
    )

    watcher.main()

    seen = json.loads(state_file.read_text())

    assert seen["abc123"]["currently_visible"] is False
    assert seen["abc123"]["last_missing_at"] is not None
    assert seen["abc123"]["seen_count"] == 1