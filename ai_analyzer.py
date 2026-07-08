import base64
import json
import re
import time
from pathlib import Path

import requests
from PIL import Image
from google import genai
from google.genai import types
from openai import OpenAI


GEMINI_MODELS = [
    "gemini-2.5-flash-lite",
    "gemini-2.0-flash",
]

OPENROUTER_MODELS = [
    "qwen/qwen2.5-vl-32b-instruct:free",
    "meta-llama/llama-3.2-11b-vision-instruct:free",
    "openrouter/auto",
]


def fetch_website_context(url: str) -> str:
    if not url:
        return ""

    try:
        response = requests.get(
            url,
            headers={"User-Agent": "Mozilla/5.0"},
            timeout=10,
        )
        html = response.text

        title = re.search(r"<title>(.*?)</title>", html, re.I | re.S)
        desc = re.search(
            r'<meta name="description" content="(.*?)"',
            html,
            re.I | re.S,
        )

        clean_text = re.sub(r"<script.*?</script>", " ", html, flags=re.I | re.S)
        clean_text = re.sub(r"<style.*?</style>", " ", clean_text, flags=re.I | re.S)
        clean_text = re.sub(r"<[^>]+>", " ", clean_text)
        clean_text = re.sub(r"\s+", " ", clean_text).strip()

        result = ""
        if title:
            result += "Website title: " + title.group(1).strip() + "\n"
        if desc:
            result += "Website meta description: " + desc.group(1).strip() + "\n"
        if clean_text:
            result += "Website visible text summary: " + clean_text[:2000]

        return result

    except Exception:
        return ""


def make_small_copy(image_path):
    image_path = Path(image_path)
    temp_path = image_path.parent / f"_ai_temp_{image_path.stem}.jpg"

    img = Image.open(image_path).convert("RGB")
    img.thumbnail((512, 512))
    img.save(temp_path, "JPEG", quality=65, optimize=True)

    return temp_path


def image_to_base64_data_url(image_path):
    img = Image.open(image_path).convert("RGB")
    img.thumbnail((512, 512))

    temp_path = Path(image_path).parent / f"_or_temp_{Path(image_path).stem}.jpg"
    img.save(temp_path, "JPEG", quality=65, optimize=True)

    data = temp_path.read_bytes()
    temp_path.unlink(missing_ok=True)

    encoded = base64.b64encode(data).decode("utf-8")
    return f"data:image/jpeg;base64,{encoded}"


def clean_json(text):
    text = text.strip()
    text = text.replace("```json", "").replace("```", "").strip()

    try:
        return json.loads(text)
    except Exception:
        match = re.search(r"\[.*\]", text, re.S)
        if match:
            return json.loads(match.group(0))
        raise ValueError("AI returned invalid JSON")


def normalize_results(results, total_images):
    final = []

    if not isinstance(results, list):
        results = []

    for i in range(total_images):
        item = results[i] if i < len(results) and isinstance(results[i], dict) else {}

        final.append({
            "filename_topic": str(item.get("filename_topic", "")).strip(),
            "alt": str(item.get("alt", "")).strip(),
            "title": str(item.get("title", "")).strip(),
            "caption": str(item.get("caption", "")).strip(),
            "description": str(item.get("description", "")).strip(),
        })

    return final


def build_prompt(primary_keyword, secondary_keywords="", website_context=""):
    secondary_note = (
        f"Optional secondary keywords:\n{secondary_keywords}"
        if secondary_keywords.strip()
        else "No secondary keywords provided. Infer specific topics from images yourself."
    )

    website_note = (
        f"Website context:\n{website_context}"
        if website_context
        else "No website context provided."
    )

    return f"""
You are a senior Image SEO specialist for WordPress and Google Images.

Primary keyword:
{primary_keyword}

{secondary_note}

{website_note}

Analyze every uploaded image in the exact same order.

Rules:
1. Read visible text inside each image.
2. Understand the actual image content.
3. Create specific metadata, not generic metadata.
4. filename_topic must NOT include the primary keyword.
5. filename_topic must be unique for every image.
6. Do not use generic words like image, topic, screenshot, or diagram alone.
7. Alt text must be natural and under 125 characters.
8. Title should be concise and human-readable.
9. Caption should be helpful.
10. Description should be one strong WordPress media description sentence.
11. Return metadata for every uploaded image.
12. Avoid keyword stuffing.

Return ONLY valid JSON array:

[
  {{
    "filename_topic": "specific unique image topic",
    "alt": "alt text",
    "title": "title",
    "caption": "caption",
    "description": "description"
  }}
]
"""


def analyze_with_gemini(api_key, image_paths, prompt):
    client = genai.Client(api_key=api_key)

    temp_files = []
    contents = [prompt]

    try:
        for path in image_paths:
            small = make_small_copy(path)
            temp_files.append(small)
            contents.append(client.files.upload(file=str(small)))

        last_error = None

        for model_name in GEMINI_MODELS:
            for _ in range(2):
                try:
                    response = client.models.generate_content(
                        model=model_name,
                        contents=contents,
                        config=types.GenerateContentConfig(
                            response_mime_type="application/json",
                            temperature=0.1,
                        ),
                    )
                    return clean_json(response.text)
                except Exception as e:
                    last_error = e
                    time.sleep(3)

        raise last_error

    finally:
        for f in temp_files:
            try:
                f.unlink(missing_ok=True)
            except Exception:
                pass


def analyze_with_openrouter(api_key, image_paths, prompt, preferred_model=""):
    client = OpenAI(
        base_url="https://openrouter.ai/api/v1",
        api_key=api_key,
    )

    models = [preferred_model] if preferred_model else OPENROUTER_MODELS
    models = [m for m in models if m]

    content = [{"type": "text", "text": prompt}]

    for path in image_paths:
        content.append({
            "type": "image_url",
            "image_url": {
                "url": image_to_base64_data_url(path)
            },
        })

    last_error = None

    for model_name in models:
        for _ in range(2):
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

                text = response.choices[0].message.content
                return clean_json(text)

            except Exception as e:
                last_error = e
                time.sleep(3)

    raise last_error


def analyze_images_batch(
    api_key,
    image_paths,
    primary_keyword,
    secondary_keywords="",
    website_url="",
    openrouter_api_key="",
    openrouter_model="",
):
    website_context = fetch_website_context(website_url)
    prompt = build_prompt(primary_keyword, secondary_keywords, website_context)

    errors = []

    if api_key:
        try:
            results = analyze_with_gemini(api_key, image_paths, prompt)
            return normalize_results(results, len(image_paths))
        except Exception as e:
            errors.append(f"Gemini failed: {e}")

    if openrouter_api_key:
        try:
            results = analyze_with_openrouter(
                openrouter_api_key,
                image_paths,
                prompt,
                openrouter_model,
            )
            return normalize_results(results, len(image_paths))
        except Exception as e:
            errors.append(f"OpenRouter failed: {e}")

    raise RuntimeError(
        "AI metadata failed. " + " | ".join(errors)
    )