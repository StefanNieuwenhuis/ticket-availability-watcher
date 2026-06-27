import json
from decimal import Decimal
from urllib.parse import parse_qs

import pytest
import requests
import responses

from ticket_watcher import ticket_watcher

HTML_HAPPY_PATH = """
<html>
	<body>
		<div class="blok">
			<div class="blok-kop">Beschikbare startnummers</div>
			<div>
				<div class="blok-kol3">
					<table style="width:100%;border-spacing:0;border-collapse:collapse;">
						<tbody class="ho">
							<tr>
								<td rowspan="2"></td>
								<td colspan="2"><b style="font-size:14pt">1/8e Marathon - Den Hoorn • € 20,00</b></td>
							</tr>
							<tr>
								<td style="vertical-align:top">Test User<br><i></i></td>
								<td style="text-align:right"><a href="?koop=abc123" class="btn">Reageer</a></td>
							</tr>
						</tbody>
					</table>
				</div>
			</div>
		</div>
	</body>
</html>
"""


HTML_NO_ROWS = """
<html>
	<body>
		<section>
			<div class="blok-kop">Beschikbare startnummers</div>
			<table>
				<tbody></tbody>
			</table>
		</section>
	</body>
</html>
"""


HTML_MISSING_HEADER = """
<html>
	<body>
		<section>
			<p>Geen beschikbare startnummers.</p>
		</section>
	</body>
</html>
"""


HTML_INVALID_LISTING = """
<html>
	<body>
		<section>
			<div class="blok-kop">Beschikbare startnummers</div>
			<table>
				<tbody class="ho">
					<tr>
						<td colspan="2"><b>1/8e Marathon - Den Hoorn</b></td>
					</tr>
					<tr>
						<td>Test User</td>
					</tr>
					<tr>
						<td><a class="btn" href="?koop=abc123">Reageer</a></td>
					</tr>
				</tbody>
			</table>
		</section>
	</body>
</html>
"""


class TestLoadSeenListings:
	def test_returns_empty_list_when_file_missing(self, monkeypatch, tmp_path):
		state_file = tmp_path / "missing_state.json"
		monkeypatch.setattr(ticket_watcher, "STATE_FILE", state_file)

		seen = ticket_watcher.load_seen_listings()

		assert seen == []

	def test_returns_list_from_valid_json(self, monkeypatch, tmp_path):
		state_file = tmp_path / "state.json"
		raw = [
			{
				"id": "abc123",
				"listing_type": "1/8e Marathon - Den Hoorn",
				"seller": "Test User",
				"price": "20.00",
				"url": "https://example.test/event?koop=abc123",
			}
		]
		state_file.write_text(json.dumps(raw), encoding="utf-8")
		monkeypatch.setattr(ticket_watcher, "STATE_FILE", state_file)

		seen = ticket_watcher.load_seen_listings()

		assert len(seen) == 1
		assert isinstance(seen[0], ticket_watcher.Listing)
		assert seen[0].id == "abc123"
		assert seen[0].listing_type == "1/8e Marathon - Den Hoorn"
		assert seen[0].seller == "Test User"
		assert seen[0].price == Decimal("20.00")
		assert seen[0].url == "https://example.test/event?koop=abc123"

	def test_returns_empty_list_for_invalid_json(self, monkeypatch, tmp_path):
		state_file = tmp_path / "state.json"
		state_file.write_text("{invalid json", encoding="utf-8")
		monkeypatch.setattr(ticket_watcher, "STATE_FILE", state_file)

		seen = ticket_watcher.load_seen_listings()

		assert seen == []


class TestFetchListings:
	@responses.activate
	def test_happy_path(self, monkeypatch):
		event_url = "https://example.test/event"
		monkeypatch.setattr(ticket_watcher, "EVENT_URL", event_url)

		responses.add(
			responses.GET,
			event_url,
			body=HTML_HAPPY_PATH,
			status=200,
		)

		listings = ticket_watcher.fetch_listings()

		assert len(listings) == 1
		assert isinstance(listings[0], ticket_watcher.Listing)
		assert listings[0].id == "abc123"
		assert listings[0].listing_type == "1/8e Marathon - Den Hoorn"
		assert listings[0].seller == "Test User"
		assert listings[0].price == Decimal("20.00")
		assert listings[0].url == "https://example.test/event?koop=abc123"

	@responses.activate
	def test_returns_empty_list_when_no_listing_rows(self, monkeypatch):
		event_url = "https://example.test/event"
		monkeypatch.setattr(ticket_watcher, "EVENT_URL", event_url)

		responses.add(
			responses.GET,
			event_url,
			body=HTML_NO_ROWS,
			status=200,
		)

		listings = ticket_watcher.fetch_listings()

		assert listings == []

	@responses.activate
	def test_returns_empty_list_when_header_block_is_missing(self, monkeypatch):
		event_url = "https://example.test/event"
		monkeypatch.setattr(ticket_watcher, "EVENT_URL", event_url)

		responses.add(
			responses.GET,
			event_url,
			body=HTML_MISSING_HEADER,
			status=200,
		)

		listings = ticket_watcher.fetch_listings()

		assert listings == []

	@responses.activate
	def test_raises_when_listing_row_format_is_invalid(self, monkeypatch):
		event_url = "https://example.test/event"
		monkeypatch.setattr(ticket_watcher, "EVENT_URL", event_url)

		responses.add(
			responses.GET,
			event_url,
			body=HTML_INVALID_LISTING,
			status=200,
		)

		with pytest.raises(ValueError):
			ticket_watcher.fetch_listings()

	@responses.activate
	def test_raises_for_http_error(self, monkeypatch):
		event_url = "https://example.test/event"
		monkeypatch.setattr(ticket_watcher, "EVENT_URL", event_url)

		responses.add(
			responses.GET,
			event_url,
			body="server error",
			status=500,
		)

		with pytest.raises(requests.HTTPError):
			ticket_watcher.fetch_listings()


class TestGetNewListings:
	@staticmethod
	def _listing(
		listing_id: str,
		listing_type: str = "10K",
		seller: str = "Alice",
		price: str = "25.00",
	) -> ticket_watcher.Listing:
		return ticket_watcher.Listing(
			id=listing_id,
			listing_type=listing_type,
			seller=seller,
			price=Decimal(price),
			url=f"https://example.test/event?koop={listing_id}",
		)

	def test_happy_path_returns_new_and_removed(self):
		seen_listings = [self._listing("old-1")]
		current_listings = [
			self._listing("new-1", listing_type="Half Marathon", seller="Bob", price="45.00")
		]

		new_listings, removed_listings = ticket_watcher.get_new_listings(seen_listings, current_listings)

		assert new_listings == [current_listings[0]]
		assert removed_listings == [seen_listings[0]]

	def test_returns_empty_lists_when_both_inputs_are_empty(self):
		new_listings, removed_listings = ticket_watcher.get_new_listings([], [])

		assert new_listings == []
		assert removed_listings == []

	def test_returns_only_new_when_seen_is_empty(self):
		current_listings = [
			self._listing("new-1", listing_type="Half Marathon", seller="Bob", price="45.00")
		]

		new_listings, removed_listings = ticket_watcher.get_new_listings([], current_listings)

		assert new_listings == current_listings
		assert removed_listings == []

	def test_returns_only_removed_when_current_is_empty(self):
		seen_listings = [self._listing("old-1")]

		new_listings, removed_listings = ticket_watcher.get_new_listings(seen_listings, [])

		assert new_listings == []
		assert removed_listings == seen_listings

	def test_same_id_with_changed_fields_is_not_new_or_removed(self):
		seen_listings = [self._listing("same-1")]
		current_listings = [self._listing("same-1", seller="Alice Updated", price="30.00")]

		new_listings, removed_listings = ticket_watcher.get_new_listings(seen_listings, current_listings)

		assert new_listings == []
		assert removed_listings == []

	def test_duplicate_ids_keep_last_occurrence_by_current_logic(self):
		seen_listings = [
			self._listing("dup-1", seller="Alice", price="25.00"),
			self._listing("dup-1", seller="Alice Second", price="26.00"),
		]
		current_listings = [
			self._listing("dup-1", seller="Bob", price="27.00")
		]

		new_listings, removed_listings = ticket_watcher.get_new_listings(seen_listings, current_listings)

		assert new_listings == []
		assert removed_listings == []


class TestFormatTelegramMessage:
	def test_happy_path_formats_listing_data(self):
		listing = ticket_watcher.Listing(
			id="abc123",
			listing_type="Halve Marathon - Bootstart",
			seller="Ellen Hoekstra",
			price=Decimal("45.00"),
			url="https://example.test/event?koop=abc123",
		)

		message = ticket_watcher.format_telegram_message(listing, submitted=True)

		assert "New ticket available!" in message
		assert "Halve Marathon - Bootstart" in message
		assert "Ellen Hoekstra" in message
		assert "€45.00" in message
		assert "Form auto-submitted" in message

	def test_keeps_expected_line_structure(self):
		listing = ticket_watcher.Listing(
			id="abc123",
			listing_type="10K",
			seller="Alice",
			price=Decimal("25.00"),
			url="https://example.test/event?koop=abc123",
		)

		message = ticket_watcher.format_telegram_message(listing, submitted=False)

		assert message.startswith("🎟️ New ticket available!\n\n")
		assert message.endswith("\n")
		assert "Auto-submit failed" in message


class TestSendTelegramNotification:
	@staticmethod
	def _listing(listing_id: str) -> ticket_watcher.Listing:
		return ticket_watcher.Listing(
			id=listing_id,
			listing_type="Halve Marathon - Bootstart",
			seller="Ellen Hoekstra",
			price=Decimal("45.00"),
			url=f"https://example.test/event?koop={listing_id}",
		)

	@responses.activate
	def test_happy_path_sends_single_message(self, monkeypatch):
		monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "fake-token")
		monkeypatch.setenv("TELEGRAM_BOT_CHAT_ID", "123456")

		responses.add(
			responses.POST,
			"https://api.telegram.org/botfake-token/sendMessage",
			json={"ok": True},
			status=200,
		)

		ticket_watcher.send_telegram_notification(self._listing("abc123"), submitted=True)

		assert len(responses.calls) == 1
		payload = json.loads(responses.calls[0].request.body)
		assert payload["chat_id"] == "123456"
		assert "New ticket available!" in payload["text"]
		assert "Form auto-submitted" in payload["text"]
		assert payload["reply_markup"]["inline_keyboard"][0][0]["url"] == "https://example.test/event?koop=abc123"

	@responses.activate
	def test_sends_multiple_messages_for_multiple_listings(self, monkeypatch):
		monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "fake-token")
		monkeypatch.setenv("TELEGRAM_BOT_CHAT_ID", "123456")

		responses.add(
			responses.POST,
			"https://api.telegram.org/botfake-token/sendMessage",
			json={"ok": True},
			status=200,
		)
		responses.add(
			responses.POST,
			"https://api.telegram.org/botfake-token/sendMessage",
			json={"ok": True},
			status=200,
		)

		ticket_watcher.send_telegram_notification(self._listing("abc123"), submitted=True)
		ticket_watcher.send_telegram_notification(self._listing("def456"), submitted=False)

		assert len(responses.calls) == 2

	@responses.activate
	def test_with_missing_credentials_makes_no_requests(self, monkeypatch):
		monkeypatch.delenv("TELEGRAM_BOT_TOKEN", raising=False)
		monkeypatch.delenv("TELEGRAM_BOT_CHAT_ID", raising=False)

		ticket_watcher.send_telegram_notification(self._listing("abc123"), submitted=True)

		assert len(responses.calls) == 0

	@responses.activate
	def test_missing_credentials_prints_warning_when_new_listings_exist(self, monkeypatch, capsys):
		monkeypatch.delenv("TELEGRAM_BOT_TOKEN", raising=False)
		monkeypatch.delenv("TELEGRAM_BOT_CHAT_ID", raising=False)

		ticket_watcher.send_telegram_notification(self._listing("abc123"), submitted=False)

		captured = capsys.readouterr()
		assert "Halve Marathon - Bootstart" in captured.out
		assert len(responses.calls) == 0


class TestSaveSeenListings:
	@staticmethod
	def _listing(listing_id: str, price: str = "45.00") -> ticket_watcher.Listing:
		return ticket_watcher.Listing(
			id=listing_id,
			listing_type="Halve Marathon - Bootstart",
			seller="Ellen Hoekstra",
			price=Decimal(price),
			url=f"https://example.test/event?koop={listing_id}",
		)

	def test_happy_path_writes_serialized_listings(self, monkeypatch, tmp_path):
		state_file = tmp_path / "seen.json"
		monkeypatch.setattr(ticket_watcher, "STATE_FILE", state_file)

		listings = [self._listing("abc123", price="45.00")]

		ticket_watcher.save_seen_listings(listings)

		assert state_file.exists()
		payload = json.loads(state_file.read_text(encoding="utf-8"))
		assert payload == [
			{
				"id": "abc123",
				"listing_type": "Halve Marathon - Bootstart",
				"seller": "Ellen Hoekstra",
				"price": "45.00",
				"url": "https://example.test/event?koop=abc123",
			}
		]

	def test_writes_empty_array_for_empty_input(self, monkeypatch, tmp_path):
		state_file = tmp_path / "seen.json"
		monkeypatch.setattr(ticket_watcher, "STATE_FILE", state_file)

		ticket_watcher.save_seen_listings([])

		payload = json.loads(state_file.read_text(encoding="utf-8"))
		assert payload == []

	def test_logs_error_when_write_fails(self, monkeypatch, caplog):
		class FailingPath:
			def write_text(self, _content: str, encoding: str) -> None:
				raise OSError("disk full")

		monkeypatch.setattr(ticket_watcher, "STATE_FILE", FailingPath())

		ticket_watcher.save_seen_listings([self._listing("abc123")])

		assert "Failed to save seen listings: disk full" in caplog.text


class TestSubmitReplyForm:
	@responses.activate
	def test_happy_path_posts_form_and_returns_true(self, monkeypatch):
		listing_id = "abc123"
		listing_url = "https://example.test/reply?koop=abc123"

		monkeypatch.setenv("NAME", "Test User")
		monkeypatch.setenv("EMAIL", "test@example.test")
		monkeypatch.setenv("PHONE_NO", "0600000000")

		responses.add(
			responses.POST,
			listing_url,
			status=200,
		)

		result = ticket_watcher.submit_reply_form(listing_id, listing_url)

		assert result is True
		assert len(responses.calls) == 1

		payload = responses.calls[0].request.body
		if isinstance(payload, bytes):
			payload = payload.decode("utf-8")
		form = parse_qs(payload)
		assert form["actie"] == ["opslaan"]
		assert form["naam"] == ["Test User"]
		assert form["emai"] == ["test@example.test"]
		assert form["telf"] == ["0600000000"]

	@responses.activate
	def test_returns_false_when_server_responds_with_http_error(self):
		listing_id = "abc123"
		listing_url = "https://example.test/reply?koop=abc123"

		responses.add(
			responses.POST,
			listing_url,
			status=500,
		)

		result = ticket_watcher.submit_reply_form(listing_id, listing_url)

		assert result is False

	@responses.activate
	def test_returns_false_when_request_raises_exception(self):
		listing_id = "abc123"
		listing_url = "https://example.test/reply?koop=abc123"

		responses.add(
			responses.POST,
			listing_url,
			body=requests.ConnectionError("network down"),
		)

		result = ticket_watcher.submit_reply_form(listing_id, listing_url)

		assert result is False

	@responses.activate
	def test_returns_false_when_response_contains_form_error(self):
		listing_id = "abc123"
		listing_url = "https://example.test/reply?koop=abc123"

		responses.add(
			responses.POST,
			listing_url,
			body="<html><div class='fouthint'>Niet toegestaan</div></html>",
			status=200,
		)

		result = ticket_watcher.submit_reply_form(listing_id, listing_url)

		assert result is False

