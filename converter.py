from PIL import Image
from config import OUTPUT_FOLDER, START_QUALITY, MIN_QUALITY, MAX_WIDTH


def resize_image(img):
    width, height = img.size

    if width > MAX_WIDTH:
        new_height = int((MAX_WIDTH / width) * height)
        img = img.resize((MAX_WIDTH, new_height), Image.LANCZOS)

    return img


def save_under_limit(img, output_path, max_size_kb):
    img = resize_image(img)
    quality = START_QUALITY

    while True:
        while quality >= MIN_QUALITY:
            img.save(output_path, "WEBP", quality=quality, method=6, optimize=True)
            size_kb = output_path.stat().st_size / 1024

            if size_kb <= max_size_kb:
                return size_kb, quality, img.size

            quality -= 5

        width, height = img.size

        if width <= 500:
            return size_kb, quality, img.size

        img = img.resize((int(width * 0.85), int(height * 0.85)), Image.LANCZOS)
        quality = 70


def convert_image(image_path, output_name, max_size_kb=50):
    img = Image.open(image_path).convert("RGB")
    output_path = OUTPUT_FOLDER / output_name

    size_kb, quality, dimensions = save_under_limit(img, output_path, max_size_kb)

    return output_name, size_kb, quality, dimensions