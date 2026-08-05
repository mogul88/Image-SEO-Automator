from __future__ import annotations

import mimetypes
import re
from pathlib import Path
from typing import Any

import requests
from requests.auth import HTTPBasicAuth


class WordPressUploadError(RuntimeError):
    """Safe, user-readable WordPress upload error."""


def _clean(value: Any) -> str:
    return str(value or "").strip()


def _plain_text(value: str, limit: int = 600) -> str:
    value = re.sub(r"<script\b[^>]*>.*?</script>", " ", value, flags=re.I | re.S)
    value = re.sub(r"<style\b[^>]*>.*?</style>", " ", value, flags=re.I | re.S)
    value = re.sub(r"<[^>]+>", " ", value)
    value = re.sub(r"\s+", " ", value).strip()
    return value[:limit]


def _response_error(response: requests.Response) -> str:
    try:
        payload = response.json()

        if isinstance(payload, dict):
            code = _clean(payload.get("code"))
            message = _plain_text(_clean(payload.get("message")))

            data = payload.get("data")
            wp_status = ""
            if isinstance(data, dict):
                wp_status = _clean(data.get("status"))

            parts = []
            if code:
                parts.append(f"Code: {code}")
            if message:
                parts.append(f"Message: {message}")
            if wp_status:
                parts.append(f"Status: {wp_status}")

            if parts:
                return " | ".join(parts)

    except Exception:
        pass

    body = _plain_text(response.text)
    return body or f"HTTP {response.status_code}"


def _raise_for_wp_error(
    response: requests.Response,
    action: str,
) -> None:
    if response.ok:
        return

    details = _response_error(response)

    hints = {
        400: "WordPress ne upload data reject kiya.",
        401: (
            "Username ya Application Password incorrect hai. "
            "Normal WordPress login password use na karein."
        ),
        403: (
            "Security plugin, firewall, Cloudflare, ya user permission "
            "REST upload ko block kar rahi hai."
        ),
        404: (
            "WordPress REST endpoint nahi mila. Site URL aur permalinks "
            "check karein."
        ),
        413: "File hosting ki upload-size limit se bari hai.",
        415: "Server ne image MIME type accept nahi ki.",
        429: "Server rate limit hit ho gayi. Kuch der baad retry karein.",
        500: "WordPress/hosting par internal server error aaya.",
        502: "Hosting gateway ne invalid response di.",
        503: "WordPress/hosting temporary unavailable hai.",
        504: "Hosting gateway timeout hua.",
    }

    hint = hints.get(
        response.status_code,
        "WordPress REST API ne request reject kar di.",
    )

    raise WordPressUploadError(
        f"{action} failed — HTTP {response.status_code}. "
        f"{details}. {hint}"
    )


def _connection_error_message(exc: Exception) -> str:
    """
    Return a safe useful connection message without credentials.
    """
    name = exc.__class__.__name__
    message = _plain_text(str(exc), limit=450)

    if isinstance(exc, requests.exceptions.ConnectTimeout):
        advice = (
            "WordPress server se connection waqt par establish nahi hua. "
            "Hosting/firewall ya temporary network issue ho sakta hai."
        )
    elif isinstance(exc, requests.exceptions.ReadTimeout):
        advice = (
            "WordPress ne upload ka response waqt par nahi diya. "
            "Hosting slow hai ya image processing request timeout hui."
        )
    elif isinstance(exc, requests.exceptions.SSLError):
        advice = (
            "Website SSL certificate/HTTPS connection verify nahi hui."
        )
    elif isinstance(exc, requests.exceptions.ConnectionError):
        advice = (
            "WordPress server ne connection close/refuse kiya. "
            "Firewall, security plugin, Cloudflare, hosting rule, ya "
            "temporary server issue check karein."
        )
    else:
        advice = "WordPress se network request complete nahi hui."

    return f"{name}: {message}. {advice}"


def test_wordpress_connection(
    site_url: str,
    username: str,
    app_password: str,
    timeout: int = 45,
) -> dict[str, Any]:
    """
    Test WordPress REST authentication and upload capability.
    """
    site_url = _clean(site_url).rstrip("/")
    username = _clean(username)
    app_password = _clean(app_password).replace(" ", "")

    if not site_url:
        raise ValueError("WordPress URL required hai.")
    if not username:
        raise ValueError("WordPress username required hai.")
    if not app_password:
        raise ValueError("WordPress Application Password required hai.")

    endpoint = f"{site_url}/wp-json/wp/v2/users/me"

    try:
        response = requests.get(
            endpoint,
            auth=HTTPBasicAuth(username, app_password),
            headers={
                "Accept": "application/json",
                "User-Agent": "ImageSEOAutomator/2.0",
            },
            timeout=(15, timeout),
            allow_redirects=True,
        )
    except requests.RequestException as exc:
        raise WordPressUploadError(
            "WordPress connection test failed — "
            + _connection_error_message(exc)
        ) from exc

    _raise_for_wp_error(response, "WordPress authentication test")

    try:
        return response.json()
    except ValueError as exc:
        raise WordPressUploadError(
            "WordPress authentication response valid JSON nahi thi."
        ) from exc


def upload_to_wordpress(
    site_url: str,
    username: str,
    app_password: str,
    image_path: str | Path,
    metadata: dict[str, Any],
    timeout: int = 180,
) -> tuple[int, str]:
    """
    Upload one image and its metadata in one multipart request.

    Multipart upload is generally more compatible with hosting firewalls
    than sending the file as a raw request body.

    Returns:
        (media_id, source_url)
    """
    site_url = _clean(site_url).rstrip("/")
    username = _clean(username)
    app_password = _clean(app_password).replace(" ", "")
    image_path = Path(image_path)

    if not site_url:
        raise ValueError("WordPress URL required hai.")
    if not username:
        raise ValueError("WordPress username required hai.")
    if not app_password:
        raise ValueError("WordPress Application Password required hai.")
    if not image_path.exists():
        raise FileNotFoundError(f"Upload image not found: {image_path}")

    mime_type, _ = mimetypes.guess_type(image_path.name)
    if image_path.suffix.lower() == ".webp":
        mime_type = "image/webp"
    mime_type = mime_type or "application/octet-stream"

    endpoint = f"{site_url}/wp-json/wp/v2/media"

    form_data = {
        "title": _clean(metadata.get("title")),
        "alt_text": _clean(metadata.get("alt")),
        "caption": _clean(metadata.get("caption")),
        "description": _clean(metadata.get("description")),
        "slug": image_path.stem,
        "status": "inherit",
    }

    try:
        with image_path.open("rb") as image_file:
            response = requests.post(
                endpoint,
                auth=HTTPBasicAuth(username, app_password),
                headers={
                    "Accept": "application/json",
                    "User-Agent": "ImageSEOAutomator/2.0",
                },
                files={
                    "file": (
                        image_path.name,
                        image_file,
                        mime_type,
                    )
                },
                data=form_data,
                timeout=(20, timeout),
                allow_redirects=True,
            )
    except requests.RequestException as exc:
        raise WordPressUploadError(
            f"Image upload connection failed for {image_path.name} — "
            + _connection_error_message(exc)
        ) from exc

    _raise_for_wp_error(
        response,
        f"WordPress upload for {image_path.name}",
    )

    try:
        media = response.json()
    except ValueError as exc:
        raise WordPressUploadError(
            "WordPress upload response valid JSON nahi thi."
        ) from exc

    media_id = media.get("id")
    source_url = _clean(media.get("source_url"))

    if not media_id:
        raise WordPressUploadError(
            "Upload response mein WordPress media ID nahi mila."
        )

    if not source_url:
        # Retrieve the created item once in case source_url was filtered.
        try:
            retrieve_response = requests.get(
                f"{endpoint}/{media_id}",
                auth=HTTPBasicAuth(username, app_password),
                headers={
                    "Accept": "application/json",
                    "User-Agent": "ImageSEOAutomator/2.0",
                },
                timeout=(15, 60),
            )
            if retrieve_response.ok:
                source_url = _clean(
                    retrieve_response.json().get("source_url")
                )
        except Exception:
            pass

    return int(media_id), source_url
