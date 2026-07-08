from slugify import slugify
from config import OUTPUT_FOLDER


def clean_topic(keyword, topic):
    keyword_slug = slugify(keyword)
    topic_slug = slugify(topic)

    # Agar topic ke start/end par primary keyword aa raha ho to remove
    if topic_slug.startswith(keyword_slug + "-"):
        topic_slug = topic_slug[len(keyword_slug) + 1:]

    if topic_slug.endswith("-" + keyword_slug):
        topic_slug = topic_slug[:-(len(keyword_slug) + 1)]

    if topic_slug == keyword_slug:
        topic_slug = "overview"

    return topic_slug or "image"


def make_seo_filename(keyword, topic, used_names):
    keyword_slug = slugify(keyword) or "image"
    topic_slug = clean_topic(keyword, topic)

    base = f"{keyword_slug}-{topic_slug}"
    filename = f"{base}.webp"
    counter = 2

    while filename in used_names or (OUTPUT_FOLDER / filename).exists():
        filename = f"{base}-{counter}.webp"
        counter += 1

    used_names.add(filename)
    return filename


def make_metadata(keyword, topic):
    clean_keyword = keyword.strip()
    clean_topic = topic.strip()

    return {
        "alt": f"{clean_keyword} {clean_topic} showing the main visual details",
        "title": f"{clean_keyword} {clean_topic}".title(),
        "caption": f"{clean_keyword} {clean_topic} visual guide.",
        "description": f"This image explains {clean_keyword} {clean_topic} with clear visual details for users and search engines."
    }