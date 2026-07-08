import os
import csv
from config import METADATA_FOLDER


def get_images(folder):
    return [
        file for file in folder.glob("*")
        if file.suffix.lower() in [".jpg", ".jpeg", ".png", ".webp"]
    ]


def save_metadata_csv(rows):
    from config import METADATA_FOLDER

    csv_path = METADATA_FOLDER / "metadata.csv"

    fieldnames = [
        "filename",
        "alt",
        "title",
        "caption",
        "description",
        "wordpress_url"
    ]

    with open(csv_path, "w", newline="", encoding="utf-8") as file:
        writer = csv.DictWriter(file, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)

    return csv_path


def open_folder(folder_path):
    os.startfile(folder_path)