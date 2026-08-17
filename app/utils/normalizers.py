import re
from urllib.parse import urlparse, parse_qs, urlunparse

import phonenumbers


def normalize_email(email: str) -> str:
    """Trim and lowercase email."""
    return email.strip().lower()


def normalize_phone(phone: str) -> str:
    """Normalize phone to E.164 format."""
    try:
        parsed = phonenumbers.parse(phone, None)
        return phonenumbers.format_number(parsed, phonenumbers.PhoneNumberFormat.E164)
    except phonenumbers.NumberParseException:
        # Return cleaned version if parsing fails
        cleaned = re.sub(r"[^\d+]", "", phone)
        if not cleaned.startswith("+"):
            cleaned = "+" + cleaned
        return cleaned


def normalize_telegram_id(telegram_id: str | int) -> str:
    """Normalize Telegram user ID to string."""
    return str(telegram_id).strip()


def normalize_avito_url(url: str) -> tuple[str, str, str | None]:
    """
    Normalize Avito profile URL.
    Returns: (original_url, normalized_url, avito_id_or_None)
    """
    original = url.strip()

    parsed = urlparse(original)

    # Remove tracking params
    tracking_params = {"utm_source", "utm_medium", "utm_campaign", "utm_content", "utm_term", "ref", "from"}
    query_params = parse_qs(parsed.query, keep_blank_values=True)
    clean_query = {k: v for k, v in query_params.items() if k.lower() not in tracking_params}

    # Rebuild query string
    clean_query_string = "&".join(f"{k}={v[0]}" for k, v in clean_query.items())

    # Rebuild URL without tracking params and fragment
    normalized_parsed = parsed._replace(query=clean_query_string, fragment="")
    normalized = urlunparse(normalized_parsed)

    # Try to extract Avito ID from URL
    avito_id = _extract_avito_id(original)

    return original, normalized, avito_id


def _extract_avito_id(url: str) -> str | None:
    """Extract Avito user ID from profile URL."""
    # Pattern: avito.ru/user/XXXXX or avito.ru/moskva/predlozheniya_uslug/...
    # Profile URLs typically look like: avito.ru/user/abc123/profile
    match = re.search(r"avito\.ru/user/([a-zA-Z0-9_-]+)", url, re.IGNORECASE)
    if match:
        return match.group(1)

    # Also try to find numeric ID patterns
    match = re.search(r"avito\.ru/.*?(\d{6,})", url, re.IGNORECASE)
    if match:
        return match.group(1)

    return None