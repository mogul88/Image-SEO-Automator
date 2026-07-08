import requests
from pathlib import Path


def upload_to_wordpress(site_url, username, app_password, image_path, metadata):
    site_url = site_url.rstrip("/")
    media_url = f"{site_url}/wp-json/wp/v2/media"

    image_path = Path(image_path)

    headers = {
        "Content-Disposition": f'attachment; filename="{image_path.name}"',
        "Content-Type": "image/webp"
    }

    with open(image_path, "rb") as img:
        response = requests.post(
            media_url,
            headers=headers,
            data=img,
            auth=(username, app_password)
        )

    response.raise_for_status()
    media = response.json()
    media_id = media["id"]

    update_url = f"{site_url}/wp-json/wp/v2/media/{media_id}"

    update_data = {
        "title": metadata["title"],
        "alt_text": metadata["alt"],
        "caption": metadata["caption"],
        "description": metadata["description"]
    }

    update_response = requests.post(
        update_url,
        json=update_data,
        auth=(username, app_password)
    )

    update_response.raise_for_status()

    return media_id, media.get("source_url")