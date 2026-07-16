from __future__ import annotations

import mimetypes
from pathlib import Path
from typing import Any

import requests
from requests.auth import HTTPBasicAuth


class WordPressUploadError(RuntimeError):
    """Raised when WordPress authentication, upload, or metadata update fails."""


def _clean(value: Any) -> str:
    return str(value or "").strip()


def _extract_wp_error(response: requests.Response) -> str:
    """
    Return a useful WordPress error message instead of a huge raw traceback.
    """
    try:
        payload = response.json()

        if isinstance(payload, dict):
            code = _clean(payload.get("code"))
            message = _clean(payload.get("message"))

            details = payload.get("data")
            status = ""

            if isinstance(details, dict):
                status = _clean(details.get("status"))

            parts = []

            if code:
                parts.append(f"Code: {code}")

            if message:
                parts.append(f"Message: {message}")

            if status:
                parts.append(f"Status: {status}")

            if parts:
                return " | ".join(parts)

    except Exception:
        pass

    fallback = _clean(response.text)

    if fallback:
        return fallback[:500]

    return f"HTTP {response.status_code}"


def _raise_for_wordpress_error(
    response: requests.Response,
    action: str,
) -> None:
    if response.ok:
        return

    details = _extract_wp_error(response)

    if response.status_code == 401:
        hint = (
            "Username ya Application Password incorrect hai. "
            "WordPress login password nahi, naya Application Password use karo."
        )
    elif response.status_code == 403:
        hint = (
            "User authenticated ho sakta hai lekin media upload permission nahi hai, "
            "ya security plugin/Cloudflare REST request block kar raha hai."
        )
    elif response.status_code == 404:
        hint = (
            "REST API media endpoint nahi mila. Site URL aur /wp-json/ availability check karo."
        )
    elif response.status_code == 413:
        hint = "Image server upload-size limit se bari hai."
    elif response.status_code == 415:
        hint = "Server ne image MIME type accept nahi ki."
    else:
        hint = "WordPress REST API ne request reject kar di."

    raise WordPressUploadError(
        f"{action} failed — HTTP {response.status_code}. "
        f"{details}. {hint}"
    )


def test_wordpress_connection(
    site_url: str,
    username: str,
    app_password: str,
    timeout: int = 30,
) -> dict[str, Any]:
    """
    Test credentials before uploading.

    Returns current authenticated WordPress user data.
    """
    site_url = _clean(site_url).rstrip("/")
    username = _clean(username)
    app_password = _clean(app_password).replace(" ", "")

    if not site_url:
        raise ValueError("WordPress Site URL required hai.")

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
                "User-Agent": "ImageSEOAutomator/1.0",
            },
            timeout=timeout,
        )
    except requests.RequestException as exc:
        raise WordPressUploadError(
            f"WordPress connection failed: {exc}"
        ) from exc

    _raise_for_wordpress_error(
        response,
        "WordPress authentication test",
    )

    return response.json()


def upload_to_wordpress(
    site_url: str,
    username: str,
    app_password: str,
    image_path: str | Path,
    metadata: dict[str, Any],
    timeout: int = 60,
) -> tuple[int, str]:
    """
    Upload one image to WordPress Media Library and update:
    - title
    - alt text
    - caption
    - description

    Returns:
        (media_id, source_url)
    """
    site_url = _clean(site_url).rstrip("/")
    username = _clean(username)

    # WordPress displays Application Passwords with spaces.
    # Removing spaces avoids accidental authentication failure.
    app_password = _clean(app_password).replace(" ", "")

    image_path = Path(image_path)

    if not site_url:
        raise ValueError("WordPress Site URL required hai.")

    if not username:
        raise ValueError("WordPress username required hai.")

    if not app_password:
        raise ValueError("WordPress Application Password required hai.")

    if not image_path.exists():
        raise FileNotFoundError(
            f"Upload image not found: {image_path}"
        )

    title = _clean(metadata.get("title"))
    alt_text = _clean(metadata.get("alt"))
    caption = _clean(metadata.get("caption"))
    description = _clean(metadata.get("description"))

    mime_type, _ = mimetypes.guess_type(image_path.name)

    if image_path.suffix.lower() == ".webp":
        mime_type = "image/webp"

    mime_type = mime_type or "application/octet-stream"

    auth = HTTPBasicAuth(username, app_password)
    media_endpoint = f"{site_url}/wp-json/wp/v2/media"

    upload_headers = {
        "Content-Disposition": (
            f'attachment; filename="{image_path.name}"'
        ),
        "Content-Type": mime_type,
        "Accept": "application/json",
        "User-Agent": "ImageSEOAutomator/1.0",
    }

    try:
        with image_path.open("rb") as image_file:
            upload_response = requests.post(
                media_endpoint,
                headers=upload_headers,
                data=image_file,
                auth=auth,
                timeout=timeout,
            )
    except requests.RequestException as exc:
        raise WordPressUploadError(
            f"Image upload connection failed: {exc}"
        ) from exc

    _raise_for_wordpress_error(
        upload_response,
        f"WordPress upload for {image_path.name}",
    )

    try:
        media = upload_response.json()
    except ValueError as exc:
        raise WordPressUploadError(
            "WordPress upload succeeded but returned invalid JSON."
        ) from exc

    media_id = media.get("id")

    if not media_id:
        raise WordPressUploadError(
            "WordPress response mein media ID nahi mila."
        )

    update_endpoint = (
        f"{site_url}/wp-json/wp/v2/media/{media_id}"
    )

    update_data = {
        "title": title,
        "alt_text": alt_text,
        "caption": caption,
        "description": description,
    }

    try:
        update_response = requests.post(
            update_endpoint,
            json=update_data,
            auth=auth,
            headers={
                "Accept": "application/json",
                "User-Agent": "ImageSEOAutomator/1.0",
            },
            timeout=timeout,
        )
    except requests.RequestException as exc:
        raise WordPressUploadError(
            f"Image uploaded, lekin metadata update connection failed: {exc}"
        ) from exc

    _raise_for_wordpress_error(
        update_response,
        f"Metadata update for media ID {media_id}",
    )

    updated_media = update_response.json()

    source_url = (
        updated_media.get("source_url")
        or media.get("source_url")
        or ""
    )

    return int(media_id), _clean(source_url)
