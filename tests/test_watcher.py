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
    assert items[0]["url"] == "https://example.test/event?koop=abc123"
    assert "1/8e Marathon" in items[0]["context"]
    assert "Reageer" in items[0]["context"]


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

    watcher.send_telegram([
        {
            "url": "https://example.test/event?koop=abc123",
            "context": "1/8e Marathon - € 20,00 Reageer",
            "text": "Reageer",
        }
    ])

    assert len(responses.calls) == 1

    payload = responses.calls[0].request.body
    payload = json.loads(payload)

    assert payload["chat_id"] == "123456789"
    assert "New ticket available" in payload["text"]
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
    assert seen == ["https://example.test/event?koop=abc123"]

    post_calls = [
        call for call in responses.calls
        if call.request.method == "POST"
    ]
    assert len(post_calls) == 1


@responses.activate
def test_main_does_not_send_for_already_seen_link(monkeypatch, tmp_path):
    state_file = tmp_path / "seen.json"
    state_file.write_text(
        json.dumps(["https://example.test/event?koop=abc123"])
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