"""
Email over HTTPS, for hosts that block outbound SMTP (Railway's Hobby plan does).

Uses Brevo's transactional email API (https://developers.brevo.com/reference/sendtransacemail).
Turn it on by setting BREVO_API_KEY; DEFAULT_FROM_EMAIL must be a sender address that has been
verified in the Brevo account. Nothing else is needed: no SMTP host, port or password.
"""

import json
import logging
import urllib.error
import urllib.request

from django.conf import settings
from django.core.mail.backends.base import BaseEmailBackend
from django.core.mail.message import sanitize_address

logger = logging.getLogger(__name__)

API_URL = "https://api.brevo.com/v3/smtp/email"


def _parse_address(address):
    """'Name <a@b.org>' or 'a@b.org' -> {"email": ..., "name": ...}"""

    from email.utils import parseaddr

    name, email = parseaddr(sanitize_address(address, "utf-8"))
    parsed = {"email": email}
    if name:
        parsed["name"] = name
    return parsed


class BrevoEmailBackend(BaseEmailBackend):
    def send_messages(self, email_messages):
        key = getattr(settings, "BREVO_API_KEY", "")

        if not key:
            if self.fail_silently:
                return 0
            raise RuntimeError("BREVO_API_KEY is not set, so email cannot be sent.")

        sent = 0

        for message in email_messages:
            payload = {
                "sender": _parse_address(message.from_email or settings.DEFAULT_FROM_EMAIL),
                "to": [_parse_address(a) for a in message.to],
                "subject": message.subject,
            }

            if message.cc:
                payload["cc"] = [_parse_address(a) for a in message.cc]
            if message.bcc:
                payload["bcc"] = [_parse_address(a) for a in message.bcc]

            html = next(
                (content for content, mimetype in getattr(message, "alternatives", []) if mimetype == "text/html"),
                None,
            )
            if html:
                payload["htmlContent"] = html
            payload["textContent"] = message.body

            request = urllib.request.Request(
                API_URL,
                data=json.dumps(payload).encode("utf-8"),
                headers={"api-key": key, "accept": "application/json", "content-type": "application/json"},
                method="POST",
            )

            try:
                with urllib.request.urlopen(request, timeout=15) as response:
                    response.read()
                sent += 1
            except urllib.error.HTTPError as exc:
                detail = exc.read().decode("utf-8", "replace")[:500]
                logger.error("Brevo refused the email (%s): %s", exc.code, detail)
                if not self.fail_silently:
                    raise
            except Exception:
                logger.exception("Brevo email could not be sent")
                if not self.fail_silently:
                    raise

        return sent
