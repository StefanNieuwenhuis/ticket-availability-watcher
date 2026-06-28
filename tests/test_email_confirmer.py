import email.message
import email.mime.base
import email.mime.multipart
from unittest.mock import MagicMock, patch

import pytest
import requests.exceptions
import responses as responses_lib

from email_confirmer.email_confirmer import (
    build_telegram_message,
    check_confirmation_response,
    confirm_registration,
    connect_to_inbox,
    extract_body,
    extract_verify_url,
    fetch_verify_url,
    main,
    mark_as_read,
    search_confirmation_emails,
    send_telegram_notification,
    wait_and_confirm,
)

# ---------------------------------------------------------------------------
# connect_to_inbox
# ---------------------------------------------------------------------------


class TestConnectToInbox:
    def test_returns_imap_connection(self):
        with patch("email_confirmer.email_confirmer.imaplib.IMAP4_SSL") as mock_cls:
            mock_mail = MagicMock()
            mock_cls.return_value = mock_mail

            result = connect_to_inbox()

            mock_cls.assert_called_once_with("imap.gmail.com")
            mock_mail.login.assert_called_once()
            mock_mail.select.assert_called_once_with("inbox")
            assert result is mock_mail

    def test_uses_env_credentials(self):
        with (
            patch("email_confirmer.email_confirmer.imaplib.IMAP4_SSL") as mock_cls,
            patch("email_confirmer.email_confirmer.GMAIL_ADDRESS", "user@gmail.com"),
            patch("email_confirmer.email_confirmer.GMAIL_APP_PASSWORD", "secret"),
        ):
            mock_mail = MagicMock()
            mock_cls.return_value = mock_mail

            connect_to_inbox()

            mock_mail.login.assert_called_once_with("user@gmail.com", "secret")


# ---------------------------------------------------------------------------
# search_confirmation_emails
# ---------------------------------------------------------------------------


class TestSearchConfirmationEmails:
    def test_returns_uid_list(self):
        mail = MagicMock()
        mail.search.return_value = (None, [b"1 2 3"])

        result = search_confirmation_emails(mail)

        assert result == [b"1", b"2", b"3"]

    def test_returns_empty_list_when_no_emails(self):
        mail = MagicMock()
        mail.search.return_value = (None, [b""])

        result = search_confirmation_emails(mail)

        # b"".split() returns [] — no UIDs present
        assert result == []

    def test_search_uses_correct_filters(self):
        mail = MagicMock()
        mail.search.return_value = (None, [b""])

        search_confirmation_emails(mail)

        args = mail.search.call_args[0]
        query = args[1]
        assert "UNSEEN" in query
        assert "no-reply@inschrijven.nl" in query
        assert "Texel Halve Marathon" in query


# ---------------------------------------------------------------------------
# extract_body
# ---------------------------------------------------------------------------


class TestExtractBody:
    def test_single_part_plain_text(self):
        msg = email.message.Message()
        msg.set_payload("Hello world", charset="utf-8")
        msg["Content-Type"] = "text/plain"

        result = extract_body(msg)

        assert "Hello world" in result

    def test_multipart_concatenates_html_and_plain(self):
        import email.mime.text

        msg = email.mime.multipart.MIMEMultipart("alternative")

        plain = email.mime.text.MIMEText("plain text", "plain", "utf-8")
        html = email.mime.text.MIMEText("<p>html text</p>", "html", "utf-8")

        msg.attach(plain)
        msg.attach(html)

        result = extract_body(msg)

        assert "plain text" in result
        assert "html text" in result

    def test_multipart_skips_non_text_parts(self):
        import email.mime.text

        msg = email.mime.multipart.MIMEMultipart("mixed")

        image = email.mime.base.MIMEBase("image", "png")
        image.set_payload(b"\x89PNG")
        msg.attach(image)

        plain = email.mime.text.MIMEText("some text", "plain", "utf-8")
        msg.attach(plain)

        result = extract_body(msg)
        assert "some text" in result
        assert isinstance(result, str)


# ---------------------------------------------------------------------------
# extract_verify_url
# ---------------------------------------------------------------------------


class TestExtractVerifyUrl:
    def test_extracts_verify_url(self):
        html = '<a href="https://platform.inschrijven.nl/confirm?verify=abc123">Bevestig</a>'
        result = extract_verify_url(html)
        assert result == "https://platform.inschrijven.nl/confirm?verify=abc123"

    def test_returns_none_when_no_anchor(self):
        html = "<p>No links here</p>"
        result = extract_verify_url(html)
        assert result is None

    def test_returns_none_when_anchor_missing_verify_param(self):
        html = '<a href="https://platform.inschrijven.nl/dashboard">Dashboard</a>'
        result = extract_verify_url(html)
        assert result is None

    def test_returns_none_when_anchor_wrong_domain(self):
        html = '<a href="https://example.com/verify=abc123">Click</a>'
        result = extract_verify_url(html)
        assert result is None

    def test_returns_first_matching_url_when_multiple(self):
        html = (
            '<a href="https://platform.inschrijven.nl/confirm?verify=first">First</a>'
            '<a href="https://platform.inschrijven.nl/confirm?verify=second">Second</a>'
        )
        result = extract_verify_url(html)
        assert result == "https://platform.inschrijven.nl/confirm?verify=first"


# ---------------------------------------------------------------------------
# mark_as_read
# ---------------------------------------------------------------------------


class TestMarkAsRead:
    def test_sets_seen_flag(self):
        mail = MagicMock()
        mark_as_read(mail, b"42")
        mail.store.assert_called_once_with(b"42", "+FLAGS", "\\Seen")


# ---------------------------------------------------------------------------
# check_confirmation_response
# ---------------------------------------------------------------------------


class TestCheckConfirmationResponse:
    def test_returns_true_when_no_error_div(self):
        html = "<html><body><p>Success!</p></body></html>"
        confirmed, error_msg = check_confirmation_response(html)
        assert confirmed is True
        assert error_msg is None

    def test_returns_false_with_error_message(self):
        html = '<div style="background:#f7d9d9">Aanmelding mislukt</div>'
        confirmed, error_msg = check_confirmation_response(html)
        assert confirmed is False
        assert error_msg == "Aanmelding mislukt"

    def test_error_message_is_stripped(self):
        html = '<div style="background:#f7d9d9">  Spaties  </div>'
        _, error_msg = check_confirmation_response(html)
        assert error_msg == "Spaties"

    def test_other_background_color_is_not_treated_as_error(self):
        html = '<div style="background:#d9f7d9">Success</div>'
        confirmed, _ = check_confirmation_response(html)
        assert confirmed is True


# ---------------------------------------------------------------------------
# confirm_registration
# ---------------------------------------------------------------------------


class TestConfirmRegistration:
    @responses_lib.activate
    def test_happy_path_returns_true(self):
        responses_lib.add(
            responses_lib.GET,
            "https://platform.inschrijven.nl/confirm?verify=abc",
            body="<html><body>OK</body></html>",
            status=200,
        )
        confirmed, error_msg = confirm_registration(
            "https://platform.inschrijven.nl/confirm?verify=abc"
        )
        assert confirmed is True
        assert error_msg is None

    @responses_lib.activate
    def test_server_returns_error_div(self):
        responses_lib.add(
            responses_lib.GET,
            "https://platform.inschrijven.nl/confirm?verify=abc",
            body='<div style="background:#f7d9d9">Al bevestigd</div>',
            status=200,
        )
        confirmed, error_msg = confirm_registration(
            "https://platform.inschrijven.nl/confirm?verify=abc"
        )
        assert confirmed is False
        assert error_msg == "Al bevestigd"

    @responses_lib.activate
    def test_http_error_returns_false(self):
        responses_lib.add(
            responses_lib.GET,
            "https://platform.inschrijven.nl/confirm?verify=abc",
            status=500,
        )
        confirmed, error_msg = confirm_registration(
            "https://platform.inschrijven.nl/confirm?verify=abc"
        )
        assert confirmed is False
        assert error_msg is None

    @responses_lib.activate
    def test_connection_error_returns_false(self):
        responses_lib.add(
            responses_lib.GET,
            "https://platform.inschrijven.nl/confirm?verify=abc",
            body=requests.exceptions.ConnectionError("Connection refused"),
        )
        confirmed, error_msg = confirm_registration(
            "https://platform.inschrijven.nl/confirm?verify=abc"
        )
        assert confirmed is False
        assert error_msg is None


# ---------------------------------------------------------------------------
# fetch_verify_url
# ---------------------------------------------------------------------------


class TestFetchVerifyUrl:
    def _make_raw_email(self, body_html: str) -> bytes:
        msg = email.message.EmailMessage()
        msg["From"] = "no-reply@inschrijven.nl"
        msg["Subject"] = "Bevestig"
        msg.set_content(body_html, subtype="html")
        return msg.as_bytes()

    def test_returns_verify_url_from_email(self):
        verify_url = "https://platform.inschrijven.nl/confirm?verify=tok1"
        raw = self._make_raw_email(f'<a href="{verify_url}">confirm</a>')

        mock_mail = MagicMock()
        mock_mail.__enter__ = MagicMock(return_value=mock_mail)
        mock_mail.__exit__ = MagicMock(return_value=False)
        mock_mail.search.return_value = (None, [b"1"])
        mock_mail.fetch.return_value = (None, [(None, raw)])

        with patch(
            "email_confirmer.email_confirmer.connect_to_inbox", return_value=mock_mail
        ):
            result = fetch_verify_url()

        assert result == verify_url

    def test_marks_email_as_read_after_finding_url(self):
        verify_url = "https://platform.inschrijven.nl/confirm?verify=tok1"
        raw = self._make_raw_email(f'<a href="{verify_url}">confirm</a>')

        mock_mail = MagicMock()
        mock_mail.__enter__ = MagicMock(return_value=mock_mail)
        mock_mail.__exit__ = MagicMock(return_value=False)
        mock_mail.search.return_value = (None, [b"1"])
        mock_mail.fetch.return_value = (None, [(None, raw)])

        with patch(
            "email_confirmer.email_confirmer.connect_to_inbox", return_value=mock_mail
        ):
            fetch_verify_url()

        mock_mail.store.assert_called_once_with(b"1", "+FLAGS", "\\Seen")

    def test_returns_none_when_no_emails(self):
        mock_mail = MagicMock()
        mock_mail.__enter__ = MagicMock(return_value=mock_mail)
        mock_mail.__exit__ = MagicMock(return_value=False)
        mock_mail.search.return_value = (None, [b""])

        with patch(
            "email_confirmer.email_confirmer.connect_to_inbox", return_value=mock_mail
        ):
            result = fetch_verify_url()

        assert result is None

    def test_returns_none_when_email_has_no_verify_url(self):
        raw = self._make_raw_email("<p>No links here</p>")

        mock_mail = MagicMock()
        mock_mail.__enter__ = MagicMock(return_value=mock_mail)
        mock_mail.__exit__ = MagicMock(return_value=False)
        mock_mail.search.return_value = (None, [b"1"])
        mock_mail.fetch.return_value = (None, [(None, raw)])

        with patch(
            "email_confirmer.email_confirmer.connect_to_inbox", return_value=mock_mail
        ):
            result = fetch_verify_url()

        assert result is None

    def test_skips_email_without_url_and_checks_next(self):
        verify_url = "https://platform.inschrijven.nl/confirm?verify=tok2"
        raw_no_url = self._make_raw_email("<p>No link</p>")
        raw_with_url = self._make_raw_email(f'<a href="{verify_url}">confirm</a>')

        mock_mail = MagicMock()
        mock_mail.__enter__ = MagicMock(return_value=mock_mail)
        mock_mail.__exit__ = MagicMock(return_value=False)
        mock_mail.search.return_value = (None, [b"1 2"])
        mock_mail.fetch.side_effect = [
            (None, [(None, raw_no_url)]),
            (None, [(None, raw_with_url)]),
        ]

        with patch(
            "email_confirmer.email_confirmer.connect_to_inbox", return_value=mock_mail
        ):
            result = fetch_verify_url()

        assert result == verify_url


# ---------------------------------------------------------------------------
# build_telegram_message
# ---------------------------------------------------------------------------


class TestBuildTelegramMessage:
    def test_confirmed_message(self):
        msg = build_telegram_message(True, "https://example.com/verify=x")
        assert "confirmed" in msg.lower()
        assert "✅" in msg

    def test_failed_with_error_message(self):
        msg = build_telegram_message(
            False, "https://example.com/verify=x", "Server error"
        )
        assert "Server error" in msg
        assert "⚠️" in msg

    def test_failed_without_error_message(self):
        msg = build_telegram_message(False, "https://example.com/verify=x", None)
        assert "⚠️" in msg


# ---------------------------------------------------------------------------
# send_telegram_notification
# ---------------------------------------------------------------------------


class TestSendTelegramNotification:
    @responses_lib.activate
    def test_sends_post_when_secrets_configured(self):
        responses_lib.add(
            responses_lib.POST,
            "https://api.telegram.org/botTOKEN/sendMessage",
            json={"ok": True},
            status=200,
        )

        with (
            patch("email_confirmer.email_confirmer.os.environ.get") as mock_env,
        ):
            mock_env.side_effect = lambda key, *_: {
                "TELEGRAM_BOT_TOKEN": "TOKEN",
                "TELEGRAM_BOT_CHAT_ID": "123",
            }.get(key)
            send_telegram_notification(
                True, "https://platform.inschrijven.nl/confirm?verify=x"
            )

        assert len(responses_lib.calls) == 1
        assert "sendMessage" in responses_lib.calls[0].request.url

    def test_prints_to_stdout_when_secrets_missing(self, capsys):
        with patch("email_confirmer.email_confirmer.os.environ.get", return_value=None):
            send_telegram_notification(
                True, "https://platform.inschrijven.nl/confirm?verify=x"
            )

        captured = capsys.readouterr()
        assert "https://platform.inschrijven.nl/confirm?verify=x" in captured.out

    @responses_lib.activate
    def test_raises_on_telegram_http_error(self):
        responses_lib.add(
            responses_lib.POST,
            "https://api.telegram.org/botTOKEN/sendMessage",
            status=403,
        )

        with (
            patch("email_confirmer.email_confirmer.os.environ.get") as mock_env,
            pytest.raises(Exception),
        ):
            mock_env.side_effect = lambda key, *_: {
                "TELEGRAM_BOT_TOKEN": "TOKEN",
                "TELEGRAM_BOT_CHAT_ID": "123",
            }.get(key)
            send_telegram_notification(
                True, "https://platform.inschrijven.nl/confirm?verify=x"
            )


# ---------------------------------------------------------------------------
# wait_and_confirm
# ---------------------------------------------------------------------------


class TestWaitAndConfirm:
    def test_happy_path_confirms_on_first_attempt(self):
        verify_url = "https://platform.inschrijven.nl/confirm?verify=tok"

        with (
            patch(
                "email_confirmer.email_confirmer.fetch_verify_url",
                return_value=verify_url,
            ),
            patch(
                "email_confirmer.email_confirmer.confirm_registration",
                return_value=(True, None),
            ) as mock_confirm,
            patch(
                "email_confirmer.email_confirmer.send_telegram_notification"
            ) as mock_notify,
        ):
            wait_and_confirm()

        mock_confirm.assert_called_once_with(verify_url)
        mock_notify.assert_called_once_with(True, verify_url, None)

    def test_retries_until_email_found(self):
        verify_url = "https://platform.inschrijven.nl/confirm?verify=tok"

        with (
            patch(
                "email_confirmer.email_confirmer.fetch_verify_url",
                side_effect=[None, None, verify_url],
            ),
            patch(
                "email_confirmer.email_confirmer.confirm_registration",
                return_value=(True, None),
            ),
            patch("email_confirmer.email_confirmer.send_telegram_notification"),
            patch("email_confirmer.email_confirmer.time.sleep") as mock_sleep,
        ):
            wait_and_confirm()

        assert mock_sleep.call_count == 2

    def test_sends_failure_notification_after_max_retries(self):
        with (
            patch(
                "email_confirmer.email_confirmer.fetch_verify_url", return_value=None
            ),
            patch(
                "email_confirmer.email_confirmer.send_telegram_notification"
            ) as mock_notify,
            patch("email_confirmer.email_confirmer.time.sleep"),
            patch("email_confirmer.email_confirmer.MAX_RETRIES", 2),
        ):
            wait_and_confirm()

        mock_notify.assert_called_once()
        call_kwargs = mock_notify.call_args
        assert call_kwargs[1]["confirmed"] is False or call_kwargs[0][0] is False

    def test_no_sleep_after_last_attempt(self):
        with (
            patch(
                "email_confirmer.email_confirmer.fetch_verify_url", return_value=None
            ),
            patch("email_confirmer.email_confirmer.send_telegram_notification"),
            patch("email_confirmer.email_confirmer.time.sleep") as mock_sleep,
            patch("email_confirmer.email_confirmer.MAX_RETRIES", 3),
            patch("email_confirmer.email_confirmer.RETRY_INTERVAL_SECONDS", 5),
        ):
            wait_and_confirm()

        # Should sleep MAX_RETRIES - 1 times (not after the last attempt)
        assert mock_sleep.call_count == 2


# ---------------------------------------------------------------------------
# main
# ---------------------------------------------------------------------------


class TestMain:
    def test_exits_early_when_no_verify_url(self):
        with (
            patch(
                "email_confirmer.email_confirmer.fetch_verify_url", return_value=None
            ),
            patch(
                "email_confirmer.email_confirmer.confirm_registration"
            ) as mock_confirm,
        ):
            main()

        mock_confirm.assert_not_called()

    def test_confirms_and_notifies_on_happy_path(self):
        verify_url = "https://platform.inschrijven.nl/confirm?verify=tok"

        with (
            patch(
                "email_confirmer.email_confirmer.fetch_verify_url",
                return_value=verify_url,
            ),
            patch(
                "email_confirmer.email_confirmer.confirm_registration",
                return_value=(True, None),
            ) as mock_confirm,
            patch(
                "email_confirmer.email_confirmer.send_telegram_notification"
            ) as mock_notify,
        ):
            main()

        mock_confirm.assert_called_once_with(verify_url)
        mock_notify.assert_called_once_with(True, verify_url, None)

    def test_sends_failure_notification_when_confirmation_fails(self):
        verify_url = "https://platform.inschrijven.nl/confirm?verify=tok"

        with (
            patch(
                "email_confirmer.email_confirmer.fetch_verify_url",
                return_value=verify_url,
            ),
            patch(
                "email_confirmer.email_confirmer.confirm_registration",
                return_value=(False, "Server error"),
            ),
            patch(
                "email_confirmer.email_confirmer.send_telegram_notification"
            ) as mock_notify,
        ):
            main()

        mock_notify.assert_called_once_with(False, verify_url, "Server error")
