from __future__ import annotations

import base64
import io
import json
import re
import time
from html import unescape
from pathlib import Path
from typing import Any, Iterable

import requests
from PIL import Image, ImageOps
from google import genai
from google.genai import types
from openai import OpenAI


# =========================================================
# MODEL CONFIGURATION
# =========================================================

# Do not add gemini-2.0-flash or gemini-1.5-flash again.
#
# Models are attempted from top to bottom.
# You can change this list later without changing any function.
GEMINI_MODELS = [
    "gemini-3.1-flash-lite-preview",
    "gemini-3.5-flash",
    "gemini-2.5-flash-lite",
]

# OpenRouter's free router dynamically chooses a currently
# available free model that supports the request requirements.
DEFAULT_OPENROUTER_MODEL = "openrouter/free"

MAX_IMAGE_SIDE = 1100
JPEG_QUALITY = 78
WEBSITE_TIMEOUT_SECONDS = 12
AI_TIMEOUT_SECONDS = 120

# Retry only server-side/transient failures.
TRANSIENT_ERROR_CODES = {500, 502, 503, 504}

# Do not repeatedly retry these errors.
NON_RETRYABLE_ERROR_CODES = {400, 401, 402, 403, 404, 429}


# =========================================================
# CUSTOM ERROR
# =========================================================

class AIAnalysisError(RuntimeError):
    """Raised when all configured AI providers fail."""


# =========================================================
# GENERAL TEXT HELPERS
# =========================================================

def collapse_spaces(value: str) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip()


def truncate_text(value: str, max_length: int) -> str:
    value = collapse_spaces(value)

    if len(value) <= max_length:
        return value

    shortened = value[:max_length].rsplit(" ", 1)[0].strip()
    return shortened or value[:max_length].strip()


def remove_html_tags(html: str) -> str:
    html = re.sub(
        r"<script\b[^>]*>.*?</script>",
        " ",
        html,
        flags=re.I | re.S,
    )
    html = re.sub(
        r"<style\b[^>]*>.*?</style>",
        " ",
        html,
        flags=re.I | re.S,
    )
    html = re.sub(
        r"<noscript\b[^>]*>.*?</noscript>",
        " ",
        html,
        flags=re.I | re.S,
    )
    html = re.sub(r"<!--.*?-->", " ", html, flags=re.S)
    html = re.sub(r"<[^>]+>", " ", html)

    return collapse_spaces(unescape(html))


def extract_meta_description(html: str) -> str:
    patterns = [
        (
            r'<meta[^>]+name=["\']description["\'][^>]+'
            r'content=["\'](.*?)["\']'
        ),
        (
            r'<meta[^>]+content=["\'](.*?)["\'][^>]+'
            r'name=["\']description["\']'
        ),
        (
            r'<meta[^>]+property=["\']og:description["\'][^>]+'
            r'content=["\'](.*?)["\']'
        ),
    ]

    for pattern in patterns:
        match = re.search(pattern, html, re.I | re.S)
        if match:
            return collapse_spaces(unescape(match.group(1)))

    return ""


# =========================================================
# WEBSITE CONTEXT
# =========================================================

def fetch_website_context(url: str) -> str:
    """
    Fetch a compact website summary for AI context.

    Failure is intentionally non-fatal because website context
    is an optional feature.
    """
    url = collapse_spaces(url)

    if not url:
        return ""

    if not re.match(r"^https?://", url, flags=re.I):
        url = f"https://{url}"

    try:
        response = requests.get(
            url,
            headers={
                "User-Agent": (
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                    "AppleWebKit/537.36 Chrome/126 Safari/537.36"
                ),
                "Accept": "text/html,application/xhtml+xml",
            },
            timeout=WEBSITE_TIMEOUT_SECONDS,
            allow_redirects=True,
        )
        response.raise_for_status()

        content_type = response.headers.get("content-type", "").lower()

        if "html" not in content_type:
            return ""

        html = response.text[:1_000_000]

        title_match = re.search(
            r"<title\b[^>]*>(.*?)</title>",
            html,
            re.I | re.S,
        )

        title = (
            collapse_spaces(unescape(title_match.group(1)))
            if title_match
            else ""
        )

        meta_description = extract_meta_description(html)
        visible_text = remove_html_tags(html)

        sections: list[str] = []

        if title:
            sections.append(f"Website title: {title}")

        if meta_description:
            sections.append(
                f"Website meta description: {meta_description}"
            )

        if visible_text:
            sections.append(
                "Website visible content summary: "
                + visible_text[:3000]
            )

        return "\n".join(sections)

    except requests.RequestException:
        return ""
    except Exception:
        return ""


# =========================================================
# IMAGE PREPARATION
# =========================================================

def prepare_image_bytes(
    image_path: str | Path,
    max_side: int = MAX_IMAGE_SIDE,
    quality: int = JPEG_QUALITY,
) -> bytes:
    """
    Convert an image into a reasonably small JPEG for AI analysis.

    EXIF orientation is corrected before resizing.
    """
    path = Path(image_path)

    if not path.exists():
        raise FileNotFoundError(f"Image does not exist: {path}")

    with Image.open(path) as source:
        image = ImageOps.exif_transpose(source).convert("RGB")
        image.thumbnail(
            (max_side, max_side),
            Image.Resampling.LANCZOS,
        )

        buffer = io.BytesIO()
        image.save(
            buffer,
            format="JPEG",
            quality=quality,
            optimize=True,
            progressive=True,
        )

    return buffer.getvalue()


def image_to_base64_data_url(image_path: str | Path) -> str:
    image_bytes = prepare_image_bytes(image_path)
    encoded = base64.b64encode(image_bytes).decode("utf-8")

    return f"data:image/jpeg;base64,{encoded}"


# =========================================================
# JSON PARSING
# =========================================================

def strip_code_fences(text: str) -> str:
    text = str(text or "").strip()

    text = re.sub(
        r"^```(?:json)?\s*",
        "",
        text,
        flags=re.I,
    )
    text = re.sub(r"\s*```$", "", text)

    return text.strip()


def extract_json_candidate(text: str) -> str:
    """
    Extract the first likely JSON array or object.
    """
    text = strip_code_fences(text)

    array_start = text.find("[")
    array_end = text.rfind("]")

    if array_start != -1 and array_end > array_start:
        return text[array_start:array_end + 1]

    object_start = text.find("{")
    object_end = text.rfind("}")

    if object_start != -1 and object_end > object_start:
        return text[object_start:object_end + 1]

    return text


def clean_json(text: str) -> list[dict[str, Any]]:
    candidate = extract_json_candidate(text)

    try:
        parsed = json.loads(candidate)
    except json.JSONDecodeError as exc:
        # Remove occasional trailing commas before } or ].
        repaired = re.sub(r",\s*([}\]])", r"\1", candidate)

        try:
            parsed = json.loads(repaired)
        except json.JSONDecodeError as second_exc:
            raise ValueError(
                "AI returned invalid JSON. "
                f"Original parsing error: {exc}"
            ) from second_exc

    # Accept {"images": [...]} and similar wrapper responses.
    if isinstance(parsed, dict):
        possible_keys = [
            "images",
            "results",
            "metadata",
            "items",
            "data",
        ]

        extracted = None

        for key in possible_keys:
            value = parsed.get(key)
            if isinstance(value, list):
                extracted = value
                break

        if extracted is None:
            # One-image response may be returned as one object.
            if any(
                key in parsed
                for key in (
                    "filename_topic",
                    "alt",
                    "title",
                    "caption",
                    "description",
                )
            ):
                extracted = [parsed]
            else:
                raise ValueError(
                    "AI JSON did not contain a metadata array."
                )

        parsed = extracted

    if not isinstance(parsed, list):
        raise ValueError("AI JSON response is not an array.")

    return [
        item
        for item in parsed
        if isinstance(item, dict)
    ]


# =========================================================
# METADATA NORMALIZATION
# =========================================================

def normalize_topic_text(value: str) -> str:
    value = collapse_spaces(value)
    value = re.sub(r"[_/\\|]+", " ", value)
    value = re.sub(r"[^\w\s&()+.'-]", "", value, flags=re.UNICODE)
    value = collapse_spaces(value)
    value = value.strip(" .-_")

    return value


def remove_primary_keyword_from_topic(
    topic: str,
    primary_keyword: str,
) -> str:
    topic = normalize_topic_text(topic)
    primary_keyword = collapse_spaces(primary_keyword)

    if not primary_keyword:
        return topic

    # Remove the full primary-keyword phrase, case-insensitively.
    topic = re.sub(
        re.escape(primary_keyword),
        " ",
        topic,
        flags=re.I,
    )

    return normalize_topic_text(topic)


def make_unique_topic(
    topic: str,
    used_topics: set[str],
    index: int,
) -> str:
    topic = topic or f"specific-view-{index + 1}"
    base = topic
    candidate = base
    suffix = 2

    while candidate.casefold() in used_topics:
        candidate = f"{base} {suffix}"
        suffix += 1

    used_topics.add(candidate.casefold())
    return candidate


def normalize_results(
    results: Iterable[dict[str, Any]],
    total_images: int,
    primary_keyword: str,
) -> list[dict[str, str]]:
    raw_results = list(results)
    final: list[dict[str, str]] = []
    used_topics: set[str] = set()

    for index in range(total_images):
        item = (
            raw_results[index]
            if index < len(raw_results)
            and isinstance(raw_results[index], dict)
            else {}
        )

        raw_topic = (
            item.get("filename_topic")
            or item.get("topic")
            or item.get("filename")
            or ""
        )

        topic = remove_primary_keyword_from_topic(
            str(raw_topic),
            primary_keyword,
        )
        topic = make_unique_topic(topic, used_topics, index)

        alt = truncate_text(str(item.get("alt", "")), 125)
        title = truncate_text(str(item.get("title", "")), 100)
        caption = truncate_text(str(item.get("caption", "")), 180)
        description = truncate_text(
            str(item.get("description", "")),
            350,
        )

        if not alt:
            alt = truncate_text(
                f"{primary_keyword} showing {topic}",
                125,
            )

        if not title:
            title = truncate_text(
                f"{primary_keyword} – {topic}",
                100,
            )

        if not caption:
            caption = truncate_text(
                f"{topic} for {primary_keyword}.",
                180,
            )

        if not description:
            description = truncate_text(
                (
                    f"This visual shows {topic} in the context of "
                    f"{primary_keyword}, with image-specific details "
                    "for WordPress media documentation."
                ),
                350,
            )

        final.append(
            {
                "filename_topic": topic,
                "alt": alt,
                "title": title,
                "caption": caption,
                "description": description,
            }
        )

    return final


def validate_results(
    results: list[dict[str, str]],
    total_images: int,
) -> None:
    if len(results) != total_images:
        raise ValueError(
            f"AI returned {len(results)} rows for "
            f"{total_images} image(s)."
        )

    required_fields = {
        "filename_topic",
        "alt",
        "title",
        "caption",
        "description",
    }

    for index, item in enumerate(results, start=1):
        missing = [
            field
            for field in required_fields
            if not collapse_spaces(item.get(field, ""))
        ]

        if missing:
            raise ValueError(
                f"Image {index} metadata is missing: "
                + ", ".join(sorted(missing))
            )


# =========================================================
# PROMPT
# =========================================================

def build_prompt(
    primary_keyword: str,
    secondary_keywords: str = "",
    website_context: str = "",
    image_count: int = 0,
) -> str:
    primary_keyword = collapse_spaces(primary_keyword)
    secondary_keywords = secondary_keywords.strip()
    website_context = website_context.strip()

    secondary_note = (
        secondary_keywords
        if secondary_keywords
        else (
            "No secondary keywords were provided. Infer accurate "
            "supporting terms only from each image's visible content."
        )
    )

    website_note = (
        website_context
        if website_context
        else (
            "No website context was provided. Base your work on the "
            "primary keyword and actual image content."
        )
    )

    return f"""
You are a senior WordPress Image SEO specialist and visual-content analyst.

TASK
Analyze exactly {image_count} uploaded image(s), in the exact order supplied.
Create one metadata object for every image.

PRIMARY KEYWORD
{primary_keyword}

SECONDARY KEYWORDS OR GUIDANCE
{secondary_note}

OPTIONAL WEBSITE CONTEXT
{website_note}

IMAGE-ANALYSIS REQUIREMENTS
1. Carefully inspect every image rather than relying on its old filename.
2. Read meaningful visible text, labels, headings, diagram actors, values,
   product names, UI controls, roof details, measurements, and annotations.
3. Identify what makes each image different from the other images.
4. Match each metadata object to the same image position in the request.
5. Do not invent details that are not reasonably visible or supported.
6. Use the website context only when it is genuinely relevant.

FILENAME_TOPIC RULES
1. Return filename_topic as a short descriptive phrase.
2. Do not include the primary keyword phrase inside filename_topic.
3. Make every filename_topic unique.
4. Do not use meaningless phrases such as:
   image topic, generic image, SEO image, uploaded file, visual asset.
5. Do not use only the word screenshot, chart, photo, image, or diagram.
6. Prefer the specific subject, workflow, calculation, roof feature,
   architectural detail, user action, comparison, or visual purpose.
7. Do not add .jpg, .png, .webp, numbers, or URL slugs.

ALT-TEXT RULES
1. Maximum 125 characters.
2. Describe the image naturally for accessibility.
3. Mention the primary keyword only when natural.
4. Do not begin with "Image of", "Picture of", or "Screenshot of".
5. Do not keyword-stuff.

TITLE RULES
1. Human-readable WordPress media title.
2. Concise and image-specific.
3. Avoid repeating words unnecessarily.

CAPTION RULES
1. One useful, natural sentence or phrase.
2. Explain why the visual is helpful.
3. Do not merely repeat the title.

DESCRIPTION RULES
1. One strong WordPress media-library description sentence.
2. Explain the content and its relevance.
3. Keep it factual and useful.
4. Avoid promotional exaggeration and keyword stuffing.

OUTPUT REQUIREMENTS
Return ONLY valid JSON.
Return exactly {image_count} objects.
Do not include markdown, commentary, code fences, or explanations.

Required structure:

[
  {{
    "filename_topic": "specific unique subject without the full primary keyword",
    "alt": "natural image-specific alt text under 125 characters",
    "title": "concise WordPress media title",
    "caption": "useful human-readable caption",
    "description": "one factual WordPress media description sentence"
  }}
]
""".strip()


# =========================================================
# ERROR HELPERS
# =========================================================

def extract_http_status(error: Exception) -> int | None:
    for attribute in ("status_code", "code"):
        value = getattr(error, attribute, None)

        try:
            if value is not None:
                return int(value)
        except (TypeError, ValueError):
            pass

    match = re.search(
        r"\b(400|401|402|403|404|429|500|502|503|504)\b",
        str(error),
    )

    return int(match.group(1)) if match else None


def summarize_error(error: Exception, max_length: int = 500) -> str:
    message = collapse_spaces(str(error))

    # Avoid dumping a huge SDK traceback/error payload into Streamlit.
    return truncate_text(message, max_length)


def sleep_before_retry(attempt: int) -> None:
    # 2s, 4s, 8s...
    delay = min(2 ** attempt, 10)
    time.sleep(delay)


# =========================================================
# GEMINI
# =========================================================

def analyze_with_gemini(
    api_key: str,
    image_paths: list[str | Path],
    prompt: str,
) -> tuple[list[dict[str, Any]], str]:
    api_key = collapse_spaces(api_key)

    if not api_key:
        raise AIAnalysisError("Gemini API key is empty.")

    client = genai.Client(api_key=api_key)

    contents: list[Any] = [prompt]

    for image_path in image_paths:
        image_bytes = prepare_image_bytes(image_path)

        contents.append(
            types.Part.from_bytes(
                data=image_bytes,
                mime_type="image/jpeg",
            )
        )

    model_errors: list[str] = []

    for model_name in GEMINI_MODELS:
        # Two attempts are enough. Do not hammer quota-limited APIs.
        for attempt in range(2):
            try:
                response = client.models.generate_content(
                    model=model_name,
                    contents=contents,
                    config=types.GenerateContentConfig(
                        response_mime_type="application/json",
                        temperature=0.1,
                    ),
                )

                response_text = getattr(response, "text", "") or ""

                if not response_text.strip():
                    raise ValueError(
                        f"{model_name} returned an empty response."
                    )

                return clean_json(response_text), model_name

            except Exception as exc:
                status = extract_http_status(exc)
                short_error = summarize_error(exc)

                # Quota/auth/not-found errors will not improve after waiting.
                if status in NON_RETRYABLE_ERROR_CODES:
                    model_errors.append(
                        f"{model_name}: HTTP {status} — {short_error}"
                    )
                    break

                # Retry transient server errors once.
                if (
                    status in TRANSIENT_ERROR_CODES
                    and attempt == 0
                ):
                    sleep_before_retry(attempt + 1)
                    continue

                model_errors.append(
                    f"{model_name}: {short_error}"
                )
                break

    raise AIAnalysisError(
        "All Gemini models failed. "
        + " | ".join(model_errors)
    )


# =========================================================
# OPENROUTER
# =========================================================

def analyze_with_openrouter(
    api_key: str,
    image_paths: list[str | Path],
    prompt: str,
    preferred_model: str = "",
) -> tuple[list[dict[str, Any]], str]:
    api_key = collapse_spaces(api_key)

    if not api_key:
        raise AIAnalysisError("OpenRouter API key is empty.")

    model_name = (
        collapse_spaces(preferred_model)
        or DEFAULT_OPENROUTER_MODEL
    )

    client = OpenAI(
        base_url="https://openrouter.ai/api/v1",
        api_key=api_key,
        timeout=AI_TIMEOUT_SECONDS,
        max_retries=0,
        default_headers={
            "HTTP-Referer": "https://roofpitchcalculators.com",
            "X-Title": "Image SEO Automator Pro",
        },
    )

    content: list[dict[str, Any]] = [
        {
            "type": "text",
            "text": prompt,
        }
    ]

    for image_path in image_paths:
        content.append(
            {
                "type": "image_url",
                "image_url": {
                    "url": image_to_base64_data_url(image_path),
                },
            }
        )

    errors: list[str] = []

    for attempt in range(2):
        try:
            response = client.chat.completions.create(
                model=model_name,
                messages=[
                    {
                        "role": "user",
                        "content": content,
                    }
                ],
                temperature=0.1,
            )

            if not response.choices:
                raise ValueError(
                    "OpenRouter returned no response choices."
                )

            response_text = (
                response.choices[0].message.content or ""
            )

            if isinstance(response_text, list):
                response_text = "".join(
                    str(part.get("text", ""))
                    if isinstance(part, dict)
                    else str(part)
                    for part in response_text
                )

            if not str(response_text).strip():
                raise ValueError(
                    "OpenRouter returned an empty response."
                )

            return clean_json(str(response_text)), model_name

        except Exception as exc:
            status = extract_http_status(exc)
            short_error = summarize_error(exc)

            if status in NON_RETRYABLE_ERROR_CODES:
                errors.append(
                    f"{model_name}: HTTP {status} — {short_error}"
                )
                break

            if (
                status in TRANSIENT_ERROR_CODES
                and attempt == 0
            ):
                sleep_before_retry(attempt + 1)
                continue

            errors.append(f"{model_name}: {short_error}")
            break

    raise AIAnalysisError(
        "OpenRouter failed. " + " | ".join(errors)
    )


# =========================================================
# PUBLIC FUNCTION USED BY STREAMLIT
# =========================================================

def analyze_images_batch(
    api_key: str,
    image_paths: list[str | Path],
    primary_keyword: str,
    secondary_keywords: str = "",
    website_url: str = "",
    openrouter_api_key: str = "",
    openrouter_model: str = "",
) -> list[dict[str, str]]:
    """
    Provider order:

    1. Gemini
    2. OpenRouter
    3. Raise a clean error so Streamlit can use manual topics
    """

    image_paths = [Path(path) for path in image_paths]
    primary_keyword = collapse_spaces(primary_keyword)

    if not image_paths:
        raise ValueError("No images were supplied for analysis.")

    if not primary_keyword:
        raise ValueError("Primary keyword is required.")

    for path in image_paths:
        if not path.exists():
            raise FileNotFoundError(
                f"Selected image no longer exists: {path}"
            )

    website_context = fetch_website_context(website_url)

    prompt = build_prompt(
        primary_keyword=primary_keyword,
        secondary_keywords=secondary_keywords,
        website_context=website_context,
        image_count=len(image_paths),
    )

    provider_errors: list[str] = []

    # Gemini first.
    if collapse_spaces(api_key):
        try:
            raw_results, model_used = analyze_with_gemini(
                api_key=api_key,
                image_paths=image_paths,
                prompt=prompt,
            )

            results = normalize_results(
                raw_results,
                total_images=len(image_paths),
                primary_keyword=primary_keyword,
            )
            validate_results(results, len(image_paths))

            return results

        except Exception as exc:
            provider_errors.append(
                "Gemini: " + summarize_error(exc, 900)
            )

    # OpenRouter backup.
    if collapse_spaces(openrouter_api_key):
        try:
            raw_results, model_used = analyze_with_openrouter(
                api_key=openrouter_api_key,
                image_paths=image_paths,
                prompt=prompt,
                preferred_model=openrouter_model,
            )

            results = normalize_results(
                raw_results,
                total_images=len(image_paths),
                primary_keyword=primary_keyword,
            )
            validate_results(results, len(image_paths))

            return results

        except Exception as exc:
            provider_errors.append(
                "OpenRouter: " + summarize_error(exc, 900)
            )

    if not collapse_spaces(api_key):
        provider_errors.append("Gemini API key was not supplied.")

    if not collapse_spaces(openrouter_api_key):
        provider_errors.append(
            "OpenRouter API key was not supplied."
        )

    raise AIAnalysisError(
        "AI metadata analysis failed. "
        + " | ".join(provider_errors)
    )
