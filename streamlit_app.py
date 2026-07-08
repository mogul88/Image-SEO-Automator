import streamlit as st
from pathlib import Path
import tempfile
import zipfile
import csv
import io
import time

from ai_analyzer import analyze_images_batch
from seo import make_seo_filename
from converter import convert_image
from wordpress_uploader import upload_to_wordpress


def safe_secret(key, default=""):
    try:
        return st.secrets.get(key, default)
    except Exception:
        return default


st.set_page_config(
    page_title="Image SEO Automator Pro",
    page_icon="🖼️",
    layout="wide",
    initial_sidebar_state="expanded",
)

st.markdown("""
<style>
.stApp {
    background: #f6f8fb;
    color: #0f172a;
}
.block-container {
    max-width: 1220px;
    padding-top: 1.4rem;
}
[data-testid="stSidebar"] {
    background: #0b1220;
}
[data-testid="stSidebar"] label,
[data-testid="stSidebar"] h1,
[data-testid="stSidebar"] h2,
[data-testid="stSidebar"] h3,
[data-testid="stSidebar"] p,
[data-testid="stSidebar"] span {
    color: #f8fafc !important;
}
[data-testid="stSidebar"] input,
[data-testid="stSidebar"] textarea {
    background: #ffffff !important;
    color: #0f172a !important;
    border: 1px solid #94a3b8 !important;
}
.hero {
    background: linear-gradient(135deg, #08111f 0%, #12375a 55%, #08111f 100%);
    color: white;
    border-radius: 26px;
    padding: 34px;
    box-shadow: 0 22px 60px rgba(15,23,42,.18);
    margin-bottom: 22px;
}
.hero h1 {
    color: white;
    font-size: 46px;
    margin: 8px 0;
}
.hero p {
    color: #dbeafe;
    font-size: 17px;
    max-width: 850px;
}
.badge {
    display:inline-block;
    padding: 7px 13px;
    border-radius: 999px;
    background: rgba(34,197,94,.16);
    border: 1px solid rgba(34,197,94,.45);
    color: #bbf7d0;
    font-weight: 700;
    font-size: 13px;
}
.stat {
    background: white;
    border: 1px solid #e2e8f0;
    border-radius: 20px;
    padding: 20px;
    box-shadow: 0 12px 35px rgba(15,23,42,.08);
    min-height: 115px;
}
.stat-label {
    font-size: 12px;
    color: #64748b;
    font-weight: 800;
    text-transform: uppercase;
    letter-spacing: .08em;
}
.stat-value {
    font-size: 26px;
    font-weight: 900;
    color: #0f172a;
    margin-top: 8px;
}
.stButton > button {
    background: linear-gradient(90deg,#2563eb,#0891b2) !important;
    color: white !important;
    border: 0 !important;
    border-radius: 16px !important;
    height: 52px !important;
    font-weight: 900 !important;
}
.stDownloadButton > button {
    background: linear-gradient(90deg,#16a34a,#22c55e) !important;
    color: white !important;
    border: 0 !important;
    border-radius: 16px !important;
    height: 52px !important;
    font-weight: 900 !important;
}
[data-testid="stFileUploader"] {
    background: white;
    border: 2px dashed #60a5fa;
    border-radius: 18px;
    padding: 16px;
}
input, textarea {
    background: white !important;
    color: #0f172a !important;
}
</style>
""", unsafe_allow_html=True)


with st.sidebar:
    st.markdown("## ⚙️ Control Panel")

    gemini_api_key = st.text_input(
        "Gemini API Key",
        type="password",
        value=safe_secret("GEMINI_API_KEY", ""),
    )

    openrouter_api_key = st.text_input(
        "OpenRouter API Key Backup",
        type="password",
        value=safe_secret("OPENROUTER_API_KEY", ""),
    )

    openrouter_model = st.text_input(
        "OpenRouter Model Optional",
        value=safe_secret("OPENROUTER_MODEL", ""),
        placeholder="qwen/qwen2.5-vl-32b-instruct:free",
    )

    st.divider()

    dry_run = st.toggle("Dry Run only", value=True)
    target_size = st.selectbox(
        "Target Max Size KB",
        [25, 50, 75, 100, 150, 200],
        index=1,
    )

    st.divider()

    st.markdown("## 🌍 WordPress")
    wp_site_url = st.text_input("WordPress URL", value=safe_secret("WP_SITE_URL", ""))
    wp_username = st.text_input("Username", value=safe_secret("WP_USERNAME", ""))
    wp_app_password = st.text_input(
        "Application Password",
        type="password",
        value=safe_secret("WP_APP_PASSWORD", ""),
    )

    st.caption("Dry Run ON = WordPress upload skip.")


st.markdown("""
<div class="hero">
  <span class="badge">AI Image SEO Suite</span>
  <h1>Image SEO Automator Pro</h1>
  <p>Gemini first, OpenRouter backup, smart filenames, alt text, title, caption, description, WebP compression, ZIP export, and optional WordPress upload.</p>
</div>
""", unsafe_allow_html=True)

s1, s2, s3, s4 = st.columns(4)
s1.markdown(
    f'<div class="stat"><div class="stat-label">Mode</div><div class="stat-value">{"Dry Run" if dry_run else "Live Upload"}</div></div>',
    unsafe_allow_html=True,
)
s2.markdown(
    f'<div class="stat"><div class="stat-label">Target Size</div><div class="stat-value">{target_size} KB</div></div>',
    unsafe_allow_html=True,
)
s3.markdown(
    f'<div class="stat"><div class="stat-label">Gemini</div><div class="stat-value">{"Ready" if gemini_api_key else "Missing"}</div></div>',
    unsafe_allow_html=True,
)
s4.markdown(
    f'<div class="stat"><div class="stat-label">Backup AI</div><div class="stat-value">{"Ready" if openrouter_api_key else "Missing"}</div></div>',
    unsafe_allow_html=True,
)

st.write("")

left, right = st.columns([1.2, 0.8], gap="large")

with left:
    st.subheader("📤 Upload Images")
    uploaded_files = st.file_uploader(
        "Upload JPG, PNG, WEBP images",
        type=["jpg", "jpeg", "png", "webp"],
        accept_multiple_files=True,
    )

    st.subheader("🎯 SEO Inputs")
    website_url = st.text_input(
        "Website URL Optional",
        placeholder="https://yourwebsite.com/page-url",
    )
    primary_keyword = st.text_input(
        "Primary Keyword",
        placeholder="Example: 8/12 Roof Pitch",
    )
    secondary_keywords = st.text_area(
        "Secondary Keywords Optional / Manual Backup Topics",
        placeholder="angle diagram\nroof slope chart\nrafter length\ncost calculator",
    )

with right:
    st.subheader("✅ Output Includes")
    st.write("• WebP compressed images")
    st.write("• Smart SEO filenames")
    st.write("• Alt text under 125 chars")
    st.write("• WordPress title")
    st.write("• Caption + description")
    st.write("• Metadata CSV")
    st.write("• ZIP package")

    st.info("Best test: 1 image + Dry Run ON + Gemini key. If Gemini busy, OpenRouter backup will run.")

if uploaded_files:
    st.markdown("## 🖼️ Preview Gallery")
    cols = st.columns(5)
    for i, file in enumerate(uploaded_files):
        with cols[i % 5]:
            st.image(file, caption=file.name, use_container_width=True)

st.markdown("---")
start = st.button("🚀 Start Conversion", type="primary", use_container_width=True)

if start:
    if not uploaded_files:
        st.error("Please upload images first.")
        st.stop()

    if not primary_keyword.strip():
        st.error("Primary keyword required hai.")
        st.stop()

    if not gemini_api_key.strip() and not openrouter_api_key.strip():
        st.error("Gemini ya OpenRouter mein se kam az kam ek API key required hai.")
        st.stop()

    progress = st.progress(0)
    status = st.empty()
    log_box = st.empty()
    logs = []

    def log(msg):
        logs.append(msg)
        log_box.code("\n".join(logs))

    with tempfile.TemporaryDirectory() as temp_dir:
        temp_path = Path(temp_dir)
        input_dir = temp_path / "input"
        output_dir = temp_path / "output"
        metadata_dir = temp_path / "metadata"

        input_dir.mkdir()
        output_dir.mkdir()
        metadata_dir.mkdir()

        image_paths = []

        for file in uploaded_files:
            img_path = input_dir / file.name
            img_path.write_bytes(file.getvalue())
            image_paths.append(img_path)

        log(f"Found {len(image_paths)} image(s)")
        log("AI metadata analysis started...")

        try:
            ai_results = analyze_images_batch(
                gemini_api_key,
                image_paths,
                primary_keyword,
                secondary_keywords,
                website_url,
                openrouter_api_key,
                openrouter_model,
            )
        except Exception as e:
            manual_topics = [
                line.strip()
                for line in secondary_keywords.splitlines()
                if line.strip()
            ]

            if len(manual_topics) >= len(image_paths):
                st.warning("AI failed. Manual backup topics se metadata ban raha hai.")
                ai_results = []

                for i, topic in enumerate(manual_topics[:len(image_paths)]):
                    ai_results.append({
                        "filename_topic": topic,
                        "alt": f"{primary_keyword} {topic}"[:120],
                        "title": f"{primary_keyword} {topic}".title(),
                        "caption": f"{primary_keyword} {topic} visual reference.",
                        "description": f"This image shows {primary_keyword} related to {topic}.",
                    })
            else:
                st.error(f"AI failed: {e}")
                st.info(
                    f"Manual backup ke liye har image ka 1 topic do. Images: {len(image_paths)} | Topics: {len(manual_topics)}"
                )
                st.stop()

        log(f"Metadata received for {len(ai_results)} image(s).")

        import config
        import converter
        import seo

        config.OUTPUT_FOLDER = output_dir
        converter.OUTPUT_FOLDER = output_dir
        seo.OUTPUT_FOLDER = output_dir

        rows = []
        used_names = set()
        start_time = time.time()

        for index, image_path in enumerate(image_paths):
            ai_meta = ai_results[index] if index < len(ai_results) else {}
            topic = ai_meta.get("filename_topic") or f"topic-{index + 1}"

            meta = {
                "alt": ai_meta.get("alt") or f"{primary_keyword} {topic}",
                "title": ai_meta.get("title") or f"{primary_keyword} {topic}".title(),
                "caption": ai_meta.get("caption") or f"{primary_keyword} {topic} visual reference.",
                "description": ai_meta.get("description") or f"Image showing {primary_keyword} {topic}.",
            }

            output_name = make_seo_filename(primary_keyword, topic, used_names)

            output_name, size_kb, quality, dimensions = convert_image(
                image_path,
                output_name,
                target_size,
            )

            output_file = output_dir / output_name
            wp_url = ""

            if not dry_run and wp_site_url and wp_username and wp_app_password:
                log(f"Uploading to WordPress: {output_name}")
                _, wp_url = upload_to_wordpress(
                    wp_site_url,
                    wp_username,
                    wp_app_password,
                    output_file,
                    meta,
                )
                log(f"Uploaded: {wp_url}")
            else:
                log(f"Dry Run / Local Export: {output_name}")

            rows.append({
                "filename": output_name,
                "alt": meta["alt"],
                "title": meta["title"],
                "caption": meta["caption"],
                "description": meta["description"],
                "wordpress_url": wp_url,
            })

            done = index + 1
            percent = done / len(image_paths)
            elapsed = max(1, int(time.time() - start_time))
            remaining = len(image_paths) - done
            eta = int((elapsed / done) * remaining)

            progress.progress(percent)
            status.success(
                f"Progress: {int(percent * 100)}% | Done: {done}/{len(image_paths)} | ETA: {eta}s"
            )

            log(f"Converted: {output_name} | {size_kb:.1f} KB | Quality {quality}")

        csv_path = metadata_dir / "metadata.csv"

        with open(csv_path, "w", newline="", encoding="utf-8") as f:
            fieldnames = ["filename", "alt", "title", "caption", "description", "wordpress_url"]
            writer = csv.DictWriter(f, fieldnames=fieldnames)
            writer.writeheader()
            writer.writerows(rows)

        zip_buffer = io.BytesIO()

        with zipfile.ZipFile(zip_buffer, "w", zipfile.ZIP_DEFLATED) as zipf:
            for img_file in output_dir.glob("*.webp"):
                zipf.write(img_file, arcname=f"images/{img_file.name}")
            zipf.write(csv_path, arcname="metadata.csv")

        zip_buffer.seek(0)

        st.success("✅ Done! ZIP package ready.")

        st.download_button(
            label="⬇️ Download ZIP Package",
            data=zip_buffer,
            file_name=f"{primary_keyword.lower().replace(' ', '-')}-image-seo-package.zip",
            mime="application/zip",
            use_container_width=True,
        )

        st.markdown("## 📋 Metadata Preview")
        st.dataframe(rows, use_container_width=True)