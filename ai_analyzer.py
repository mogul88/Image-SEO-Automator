from __future__ import annotations

import base64
import io
import json
import re
import time
from html import unescape
from pathlib import Path
from typing import Any

import requests
from PIL import Image, ImageOps
from google import genai
from google.genai import types
from openai import OpenAI


GEMINI_PREFERENCES = [
    "gemini-3.1-flash-lite-preview",
    "gemini-3.5-flash",
    "gemini-3-flash-preview",
    "gemini-2.5-flash-lite",
]

DEFAULT_OPENROUTER_MODEL = "openrouter/free"
MAX_IMAGE_SIDE = 1100
JPEG_QUALITY = 78
WEBSITE_TIMEOUT = 12
AI_TIMEOUT = 150
TRANSIENT_CODES = {500, 502, 503, 504}
NON_RETRYABLE_CODES = {400, 401, 402, 403, 404, 429}


class AIAnalysisError(RuntimeError):
    pass


def collapse_spaces(value: Any) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip()


def truncate_text(value: Any, max_length: int) -> str:
    text = collapse_spaces(value)
    if len(text) <= max_length:
        return text
    shortened = text[:max_length].rsplit(" ", 1)[0].strip()
    return shortened or text[:max_length].strip()


def fetch_website_context(url: str) -> str:
    url = collapse_spaces(url)
    if not url:
        return ""
    if not re.match(r"^https?://", url, re.I):
        url = f"https://{url}"

    try:
        response = requests.get(
            url,
            headers={"User-Agent": "Mozilla/5.0"},
            timeout=WEBSITE_TIMEOUT,
            allow_redirects=True,
        )
        response.raise_for_status()
        if "html" not in response.headers.get("content-type", "").lower():
            return ""

        html = response.text[:1_000_000]
        title_match = re.search(r"<title\b[^>]*>(.*?)</title>", html, re.I | re.S)
        title = collapse_spaces(unescape(title_match.group(1))) if title_match else ""

        desc = ""
        for pattern in (
            r'<meta[^>]+name=["\']description["\'][^>]+content=["\'](.*?)["\']',
            r'<meta[^>]+content=["\'](.*?)["\'][^>]+name=["\']description["\']',
            r'<meta[^>]+property=["\']og:description["\'][^>]+content=["\'](.*?)["\']',
        ):
            match = re.search(pattern, html, re.I | re.S)
            if match:
                desc = collapse_spaces(unescape(match.group(1)))
                break

        clean = re.sub(r"<script\b[^>]*>.*?</script>", " ", html, flags=re.I | re.S)
        clean = re.sub(r"<style\b[^>]*>.*?</style>", " ", clean, flags=re.I | re.S)
        clean = re.sub(r"<[^>]+>", " ", clean)
        clean = collapse_spaces(unescape(clean))

        parts = []
        if title:
            parts.append(f"Website title: {title}")
        if desc:
            parts.append(f"Website meta description: {desc}")
        if clean:
            parts.append(f"Website visible content summary: {clean[:3000]}")
        return "\n".join(parts)
    except Exception:
        return ""


def prepare_image_bytes(image_path: str | Path) -> bytes:
    path = Path(image_path)
    if not path.exists():
        raise FileNotFoundError(f"Image does not exist: {path}")

    with Image.open(path) as source:
        image = ImageOps.exif_transpose(source).convert("RGB")
        image.thumbnail((MAX_IMAGE_SIDE, MAX_IMAGE_SIDE), Image.Resampling.LANCZOS)
        buffer = io.BytesIO()
        image.save(
            buffer,
            format="JPEG",
            quality=JPEG_QUALITY,
            optimize=True,
            progressive=True,
        )
        return buffer.getvalue()


def image_to_data_url(image_path: str | Path) -> str:
    encoded = base64.b64encode(prepare_image_bytes(image_path)).decode("utf-8")
    return f"data:image/jpeg;base64,{encoded}"


def clean_json(text: str) -> list[dict[str, Any]]:
    text = str(text or "").strip()
    text = re.sub(r"^```(?:json)?\s*", "", text, flags=re.I)
    text = re.sub(r"\s*```$", "", text)

    array_start, array_end = text.find("["), text.rfind("]")
    if array_start != -1 and array_end > array_start:
        text = text[array_start:array_end + 1]
    else:
        object_start, object_end = text.find("{"), text.rfind("}")
        if object_start != -1 and object_end > object_start:
            text = text[object_start:object_end + 1]

    try:
        parsed = json.loads(text)
    except json.JSONDecodeError:
        parsed = json.loads(re.sub(r",\s*([}\]])", r"\1", text))

    if isinstance(parsed, dict):
        for key in ("images", "results", "metadata", "items", "data"):
            if isinstance(parsed.get(key), list):
                parsed = parsed[key]
                break
        else:
            parsed = [parsed]

    if not isinstance(parsed, list):
        raise ValueError("AI response is not a JSON array.")

    return [item for item in parsed if isinstance(item, dict)]


def normalize_topic(value: Any) -> str:
    text = collapse_spaces(value)
    text = re.sub(r"[_/\\|]+", " ", text)
    text = re.sub(r"[^\w\s&()+.'-]", "", text, flags=re.UNICODE)
    return collapse_spaces(text).strip(" .-_")


def normalize_results(
    results: list[dict[str, Any]],
    total_images: int,
    primary_keyword: str,
) -> list[dict[str, str]]:
    final = []
    used_topics: set[str] = set()

    for index in range(total_images):
        item = results[index] if index < len(results) else {}

        topic = normalize_topic(
            item.get("filename_topic")
            or item.get("topic")
            or item.get("filename")
            or ""
        )
        topic = re.sub(re.escape(primary_keyword), " ", topic, flags=re.I)
        topic = normalize_topic(topic) or f"specific-view-{index + 1}"

        base = topic
        suffix = 2
        while topic.casefold() in used_topics:
            topic = f"{base} {suffix}"
            suffix += 1
        used_topics.add(topic.casefold())

        alt = truncate_text(item.get("alt", ""), 125) or truncate_text(
            f"{primary_keyword} showing {topic}", 125
        )
        title = truncate_text(item.get("title", ""), 100) or truncate_text(
            f"{primary_keyword} – {topic}", 100
        )
        caption = truncate_text(item.get("caption", ""), 180) or truncate_text(
            f"{topic} visual for {primary_keyword}.", 180
        )
        description = truncate_text(item.get("description", ""), 350) or truncate_text(
            f"This visual explains {topic} in relation to {primary_keyword} for WordPress media use.",
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


def validate_results(results: list[dict[str, str]], total_images: int) -> None:
    if len(results) != total_images:
        raise ValueError(
            f"AI returned {len(results)} rows for {total_images} images."
        )

    required = {"filename_topic", "alt", "title", "caption", "description"}
    for index, item in enumerate(results, start=1):
        missing = [name for name in required if not collapse_spaces(item.get(name))]
        if missing:
            raise ValueError(
                f"Image {index} metadata is missing: {', '.join(sorted(missing))}"
            )


def build_prompt(
    primary_keyword: str,
    secondary_keywords: str,
    website_context: str,
    image_count: int,
) -> str:
    secondary_note = secondary_keywords.strip() or (
        "No secondary keywords were supplied. Inspect every image independently, "
        "read visible text and infer the most accurate image-specific SEO topic."
    )
    website_note = website_context.strip() or "No website context was provided."

    return f"""
You are an expert visual analyst, OCR reader and WordPress Image SEO editor.

Analyze exactly {image_count} uploaded images in their supplied order.

PRIMARY KEYWORD
{primary_keyword}

SECONDARY KEYWORDS OR GUIDANCE
{secondary_note}

OPTIONAL WEBSITE CONTEXT
{website_note}

REQUIREMENTS
1. Inspect each image itself. Never rely on its original filename.
2. Read visible headings, labels, text, values, annotations and UI controls.
3. Identify the exact subject and purpose of every image.
4. Distinguish similar images using their visible differences.
5. Return one metadata object per image in exactly the same order.
6. Do not invent unsupported details.
7. Work accurately even when no secondary keywords are provided.

FILENAME_TOPIC
- Short, specific and unique.
- Do not repeat the full primary keyword.
- No extension or numbering.
- Never use generic phrases like image topic, uploaded image, SEO image,
  generic diagram or visual asset.

ALT
- Natural accessibility description under 125 characters.
- Describe what is visible.
- Do not begin with Image of, Picture of or Screenshot of.
- Avoid keyword stuffing.

TITLE
- Concise, natural and suitable for WordPress media.

CAPTION
- Short and helpful.
- Explain what the visual helps the reader understand.

DESCRIPTION
- One factual WordPress media description sentence.
- Explain content and relevance without keyword stuffing.

Return ONLY valid JSON with exactly {image_count} objects:

[
  {{
    "filename_topic": "specific unique subject",
    "alt": "natural alt text under 125 characters",
    "title": "concise WordPress title",
    "caption": "short helpful caption",
    "description": "one factual media description sentence"
  }}
]
""".strip()


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


def summarize_error(error: Exception) -> str:
    return truncate_text(str(error), 700)


def model_name(model: Any) -> str:
    return collapse_spaces(getattr(model, "name", "")).removeprefix("models/")


def supports_generate_content(model: Any) -> bool:
    methods = (
        getattr(model, "supported_actions", None)
        or getattr(model, "supported_generation_methods", None)
        or []
    )
    if not methods:
        return True
    return any("generate" in str(method).lower() for method in methods)


def discover_gemini_models(client: genai.Client) -> list[str]:
    available: list[str] = []

    try:
        for model in client.models.list():
            name = model_name(model)
            if (
                name
                and "gemini" in name.lower()
                and "flash" in name.lower()
                and "image" not in name.lower()
                and supports_generate_content(model)
            ):
                available.append(name)
    except Exception:
        return GEMINI_PREFERENCES.copy()

    if not available:
        return GEMINI_PREFERENCES.copy()

    ordered = [
        preferred
        for preferred in GEMINI_PREFERENCES
        if preferred in available
    ]
    ordered.extend(
        sorted(
            (name for name in available if name not in ordered),
            key=lambda name: (
                "lite" not in name.lower(),
                "preview" in name.lower(),
                name,
            ),
        )
    )
    return ordered


def analyze_with_gemini(
    api_key: str,
    image_paths: list[Path],
    prompt: str,
) -> list[dict[str, Any]]:
    client = genai.Client(api_key=collapse_spaces(api_key))
    contents: list[Any] = [prompt]

    for path in image_paths:
        contents.append(
            types.Part.from_bytes(
                data=prepare_image_bytes(path),
                mime_type="image/jpeg",
            )
        )

    errors = []

    for current_model in discover_gemini_models(client):
        for attempt in range(2):
            try:
                response = client.models.generate_content(
                    model=current_model,
                    contents=contents,
                    config=types.GenerateContentConfig(
                        response_mime_type="application/json",
                        temperature=0.1,
                    ),
                )
                text = getattr(response, "text", "") or ""
                if not text.strip():
                    raise ValueError(f"{current_model} returned an empty response.")
                return clean_json(text)

            except Exception as exc:
                status = extract_http_status(exc)

                if status in NON_RETRYABLE_CODES:
                    errors.append(
                        f"{current_model}: HTTP {status} — {summarize_error(exc)}"
                    )
                    break

                if status in TRANSIENT_CODES and attempt == 0:
                    time.sleep(3)
                    continue

                errors.append(f"{current_model}: {summarize_error(exc)}")
                break

    raise AIAnalysisError(
        "All available Gemini models failed. " + " | ".join(errors)
    )


def analyze_with_openrouter(
    api_key: str,
    image_paths: list[Path],
    prompt: str,
    preferred_model: str = "",
) -> list[dict[str, Any]]:
    selected_model = collapse_spaces(preferred_model) or DEFAULT_OPENROUTER_MODEL

    client = OpenAI(
        base_url="https://openrouter.ai/api/v1",
        api_key=collapse_spaces(api_key),
        timeout=AI_TIMEOUT,
        max_retries=0,
        default_headers={
            "HTTP-Referer": "https://roofpitchcalculators.com",
            "X-Title": "Image SEO Automator Pro",
        },
    )

    content: list[dict[str, Any]] = [{"type": "text", "text": prompt}]
    for path in image_paths:
        content.append(
            {
                "type": "image_url",
                "image_url": {"url": image_to_data_url(path)},
            }
        )

    errors = []

    for attempt in range(2):
        try:
            response = client.chat.completions.create(
                model=selected_model,
                messages=[{"role": "user", "content": content}],
                temperature=0.1,
            )

            if not response.choices:
                raise ValueError("OpenRouter returned no response choices.")

            text = response.choices[0].message.content or ""
            if isinstance(text, list):
                text = "".join(
                    str(part.get("text", ""))
                    if isinstance(part, dict)
                    else str(part)
                    for part in text
                )

            if not str(text).strip():
                raise ValueError("OpenRouter returned an empty response.")

            return clean_json(str(text))

        except Exception as exc:
            status = extract_http_status(exc)

            if status in NON_RETRYABLE_CODES:
                errors.append(
                    f"{selected_model}: HTTP {status} — {summarize_error(exc)}"
                )
                break

            if status in TRANSIENT_CODES and attempt == 0:
                time.sleep(3)
                continue

            errors.append(f"{selected_model}: {summarize_error(exc)}")
            break

    raise AIAnalysisError("OpenRouter failed. " + " | ".join(errors))


def analyze_images_batch(
    api_key: str,
    image_paths: list[str | Path],
    primary_keyword: str,
    secondary_keywords: str = "",
    website_url: str = "",
    openrouter_api_key: str = "",
    openrouter_model: str = "",
) -> list[dict[str, str]]:
    paths = [Path(path) for path in image_paths]
    primary_keyword = collapse_spaces(primary_keyword)

    if not paths:
        raise ValueError("No images were supplied for analysis.")
    if not primary_keyword:
        raise ValueError("Primary keyword is required.")

    for path in paths:
        if not path.exists():
            raise FileNotFoundError(f"Selected image no longer exists: {path}")

    prompt = build_prompt(
        primary_keyword=primary_keyword,
        secondary_keywords=secondary_keywords,
        website_context=fetch_website_context(website_url),
        image_count=len(paths),
    )

    provider_errors = []

    if collapse_spaces(api_key):
        try:
            raw = analyze_with_gemini(api_key, paths, prompt)
            results = normalize_results(raw, len(paths), primary_keyword)
            validate_results(results, len(paths))
            return results
        except Exception as exc:
            provider_errors.append(f"Gemini: {summarize_error(exc)}")

    if collapse_spaces(openrouter_api_key):
        try:
            raw = analyze_with_openrouter(
                openrouter_api_key,
                paths,
                prompt,
                openrouter_model,
            )
            results = normalize_results(raw, len(paths), primary_keyword)
            validate_results(results, len(paths))
            return results
        except Exception as exc:
            provider_errors.append(f"OpenRouter: {summarize_error(exc)}")

    if not collapse_spaces(api_key):
        provider_errors.append("Gemini API key was not supplied.")
    if not collapse_spaces(openrouter_api_key):
        provider_errors.append("OpenRouter API key was not supplied.")

    raise AIAnalysisError(
        "AI metadata analysis failed. " + " | ".join(provider_errors)
    )
