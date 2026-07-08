import customtkinter as ctk
from tkinter import messagebox, filedialog
from pathlib import Path
from PIL import Image
import time

from config import OUTPUT_FOLDER, METADATA_FOLDER
from converter import convert_image
from seo import make_seo_filename, make_metadata
from utils import save_metadata_csv, open_folder

try:
    from ai_analyzer import analyze_images_batch
except Exception:
    analyze_images_batch = None

try:
    from wordpress_uploader import upload_to_wordpress
except Exception:
    upload_to_wordpress = None


ctk.set_appearance_mode("dark")
ctk.set_default_color_theme("blue")

app = ctk.CTk()
app.title("Image SEO Automator V11")
app.geometry("1150x850")
app.resizable(True, True)
app.state("zoomed")

selected_images = []
preview_images = []


def log(message):
    log_box.insert("end", message + "\n")
    log_box.see("end")
    app.update()


def clear_preview():
    for widget in preview_grid.winfo_children():
        widget.destroy()
    preview_images.clear()


def show_previews():
    clear_preview()

    for i, image_path in enumerate(selected_images[:24], start=1):
        try:
            img = Image.open(image_path).convert("RGB")
            img.thumbnail((95, 95))

            ctk_img = ctk.CTkImage(light_image=img, dark_image=img, size=(95, 95))
            preview_images.append(ctk_img)

            card = ctk.CTkFrame(preview_grid, width=140, height=175)
            card.grid(row=(i - 1) // 6, column=(i - 1) % 6, padx=8, pady=8)

            img_label = ctk.CTkLabel(card, image=ctk_img, text="")
            img_label.pack(pady=5)

            name_label = ctk.CTkLabel(
                card,
                text=image_path.name[:18],
                font=("Segoe UI", 10)
            )
            name_label.pack(padx=4)

            remove_btn = ctk.CTkButton(
                card,
                text="Remove",
                width=90,
                height=26,
                fg_color="#7f1d1d",
                hover_color="#991b1b",
                command=lambda p=image_path: remove_single_image(p)
            )
            remove_btn.pack(pady=6)

        except Exception as e:
            log(f"Preview error: {image_path.name} - {e}")


def select_images():
    global selected_images

    files = filedialog.askopenfilenames(
        title="Select Images",
        filetypes=[
            ("Image Files", "*.jpg *.jpeg *.png *.webp"),
            ("All Files", "*.*")
        ]
    )

    if not files:
        return

    # new selected files ko old list ke sath add karega, replace nahi karega
    for file in files:
        path = Path(file)
        if path not in selected_images:
            selected_images.append(path)

    selected_label.configure(text=f"{len(selected_images)} image(s) selected")

    log_box.delete("1.0", "end")
    for img in selected_images:
        log(f"Selected: {img.name}")

    show_previews()


def clear_selected_images():
    global selected_images

    selected_images = []
    selected_label.configure(text="No images selected")
    clear_preview()
    log_box.delete("1.0", "end")
    log("Selected images cleared.")

def remove_single_image(image_path):
    global selected_images

    selected_images = [img for img in selected_images if img != image_path]
    selected_label.configure(text=f"{len(selected_images)} image(s) selected")

    log_box.delete("1.0", "end")
    for img in selected_images:
        log(f"Selected: {img.name}")

    show_previews()

def get_topics(total):
    raw_topics = topics_box.get("1.0", "end").strip()
    topics = [line.strip() for line in raw_topics.splitlines() if line.strip()]

    while len(topics) < total:
        topics.append(f"image topic {len(topics) + 1}")

    return topics[:total]


def clear_output_folder():
    if clear_output_var.get():
        for file in OUTPUT_FOLDER.glob("*.webp"):
            try:
                file.unlink()
            except Exception:
                pass
        log("Old output images cleared.\n")


def start_conversion():
    start_btn.configure(text="PROCESSING...", state="disabled")
    app.update()

    try:
        log_box.delete("1.0", "end")
        progress_bar.set(0)
        progress_label.configure(text="Progress: 0% | Done: 0 | Remaining: 0")

        keyword = keyword_entry.get().strip() or "image seo"
        website_url = website_entry.get().strip()
        gemini_key = gemini_key_entry.get().strip()
        secondary_keywords = secondary_keywords_box.get("1.0", "end").strip()
        max_size_kb = int(size_option.get())
        dry_run = dry_run_var.get()

        wp_url = wp_url_entry.get().strip()
        wp_user = wp_user_entry.get().strip()
        wp_pass = wp_pass_entry.get().strip()
        use_wp = bool(wp_url and wp_user and wp_pass)

        if not selected_images:
            messagebox.showwarning("No Images", "Please select images first.")
            return

        if gemini_key and analyze_images_batch is None:
            messagebox.showerror("Gemini Error", "ai_analyzer.py not found or has error.")
            return

        if use_wp and upload_to_wordpress is None:
            messagebox.showerror("WordPress Error", "wordpress_uploader.py not found or has error.")
            return

        clear_output_folder()

        rows = []
        used_names = set()
        total = len(selected_images)
        topics = get_topics(total)
        ai_results = []
        start_time = time.time()

        log(f"Found {total} selected image(s)\n")

        if dry_run:
            log("🟢 Dry Run Enabled - WordPress upload will be skipped.\n")
        else:
            log("🌍 WordPress Upload Mode Enabled.\n")

        if gemini_key:
            try:
                log("Gemini API key detected.")
                log("Analyzing all images with Gemini in one batch...")

                ai_results = analyze_images_batch(
                    gemini_key,
                    selected_images,
                    keyword,
                    secondary_keywords,
                    website_url
                )

                if not ai_results:
                    messagebox.showerror("Gemini Failed", "Gemini returned no metadata.")
                    return

                log(f"Gemini completed. Metadata received for {len(ai_results)} image(s).\n")

            except Exception as e:
                messagebox.showerror("Gemini Error", str(e))
                log(f"Gemini failed: {e}")
                return
        else:
            log("Gemini API key is empty. Manual topics mode is being used.\n")

        for index, image_path in enumerate(selected_images):
            try:
                topic = topics[index]

                if gemini_key and index < len(ai_results):
                    ai_meta = ai_results[index]
                    topic = ai_meta.get("filename_topic") or ai_meta.get("topic") or topic

                    meta = {
                        "alt": ai_meta.get("alt") or f"{keyword} {topic}",
                        "title": ai_meta.get("title") or f"{keyword} {topic}".title(),
                        "caption": ai_meta.get("caption") or f"{keyword} {topic} visual guide.",
                        "description": ai_meta.get("description") or f"Image showing {keyword} {topic}."
                    }
                else:
                    meta = make_metadata(keyword, topic)

                output_name = make_seo_filename(keyword, topic, used_names)

                output_name, size_kb, quality, dimensions = convert_image(
                    image_path,
                    output_name,
                    max_size_kb
                )

                image_file_path = OUTPUT_FOLDER / output_name
                wp_media_url = ""

                if use_wp:
                    if dry_run:
                        log(f"Dry Run: Skipped WordPress upload for {output_name}")
                    else:
                        log(f"Uploading to WordPress: {output_name}")

                        media_id, wp_media_url = upload_to_wordpress(
                            wp_url,
                            wp_user,
                            wp_pass,
                            image_file_path,
                            meta
                        )

                        log(f"Uploaded: {wp_media_url}")

                rows.append({
                    "filename": output_name,
                    "alt": meta["alt"],
                    "title": meta["title"],
                    "caption": meta["caption"],
                    "description": meta["description"],
                    "wordpress_url": wp_media_url
                })

                done = index + 1
                percent = int((done / total) * 100)
                remaining = total - done
                elapsed = max(1, int(time.time() - start_time))
                avg_per_image = elapsed / done
                eta = int(avg_per_image * remaining)

                progress_bar.set(done / total)
                progress_label.configure(
                    text=f"Progress: {percent}% | Done: {done}/{total} | Remaining: {remaining} | ETA: {eta}s"
                )

                log(
                    f"Converted: {output_name} | {size_kb:.1f} KB | "
                    f"Quality {quality} | {dimensions[0]}x{dimensions[1]}"
                )

            except Exception as e:
                log(f"Error: {image_path.name} - {e}")

        csv_path = save_metadata_csv(rows)
        elapsed_total = int(time.time() - start_time)

        log(f"\nMetadata CSV created: {csv_path}")
        log(f"Completed in {elapsed_total} seconds")
        log("✅ Conversion completed successfully!")

    finally:
        start_btn.configure(text="START CONVERSION", state="normal")
        app.update()


def open_output():
    open_folder(OUTPUT_FOLDER)


def open_metadata():
    open_folder(METADATA_FOLDER)


main_scroll = ctk.CTkScrollableFrame(app, width=1080, height=800)
main_scroll.pack(fill="both", expand=True, padx=20, pady=20)

title = ctk.CTkLabel(main_scroll, text="IMAGE SEO AUTOMATOR", font=("Segoe UI", 34, "bold"))
title.pack(pady=12)

subtitle = ctk.CTkLabel(
    main_scroll,
    text="Upload • Preview • Gemini Batch Analyze • Compress • SEO Metadata • WordPress Upload",
    font=("Segoe UI", 15)
)
subtitle.pack(pady=4)

select_btn = ctk.CTkButton(
    main_scroll,
    text="SELECT IMAGES",
    width=280,
    height=45,
    command=select_images
)
select_btn.pack(pady=8)

clear_images_btn = ctk.CTkButton(
    main_scroll,
    text="CLEAR SELECTED IMAGES",
    width=280,
    height=40,
    fg_color="#7f1d1d",
    command=clear_selected_images
)
clear_images_btn.pack(pady=5)

selected_label = ctk.CTkLabel(main_scroll, text="No images selected", font=("Segoe UI", 13))
selected_label.pack(pady=3)

preview_frame = ctk.CTkFrame(main_scroll)
preview_frame.pack(fill="x", padx=20, pady=10)

preview_title = ctk.CTkLabel(preview_frame, text="Image Preview", font=("Segoe UI", 15, "bold"))
preview_title.pack(pady=8)

preview_grid = ctk.CTkFrame(preview_frame)
preview_grid.pack(pady=6)

website_label = ctk.CTkLabel(main_scroll, text="Website URL Optional", font=("Segoe UI", 14, "bold"))
website_label.pack(pady=3)

website_entry = ctk.CTkEntry(
    main_scroll,
    width=620,
    height=42,
    placeholder_text="Example: https://yourwebsite.com/page-url"
)
website_entry.pack(pady=5)

keyword_label = ctk.CTkLabel(main_scroll, text="Primary Keyword", font=("Segoe UI", 14, "bold"))
keyword_label.pack(pady=3)

keyword_entry = ctk.CTkEntry(
    main_scroll,
    width=620,
    height=42,
    placeholder_text="Example: 5/12 Roof Pitch"
)
keyword_entry.pack(pady=5)

gemini_label = ctk.CTkLabel(main_scroll, text="Gemini API Key Optional", font=("Segoe UI", 14, "bold"))
gemini_label.pack(pady=3)

gemini_key_entry = ctk.CTkEntry(
    main_scroll,
    width=620,
    height=42,
    placeholder_text="Paste Gemini API key here",
    show="*"
)
gemini_key_entry.pack(pady=5)

secondary_label = ctk.CTkLabel(
    main_scroll,
    text="Secondary Keywords Optional",
    font=("Segoe UI", 14, "bold")
)
secondary_label.pack(pady=3)

secondary_keywords_box = ctk.CTkTextbox(main_scroll, width=620, height=95, font=("Segoe UI", 13))
secondary_keywords_box.pack(pady=5)

size_label = ctk.CTkLabel(main_scroll, text="Target Max Image Size KB", font=("Segoe UI", 14, "bold"))
size_label.pack(pady=4)

size_option = ctk.CTkOptionMenu(
    main_scroll,
    values=["25", "50", "75", "100", "150", "200"],
    width=160
)
size_option.set("50")
size_option.pack(pady=5)

clear_output_var = ctk.BooleanVar(value=True)
clear_output_checkbox = ctk.CTkCheckBox(
    main_scroll,
    text="Clear output folder before conversion",
    variable=clear_output_var
)
clear_output_checkbox.pack(pady=8)

dry_run_var = ctk.BooleanVar(value=True)
dry_run_checkbox = ctk.CTkCheckBox(
    main_scroll,
    text="Dry Run (Skip WordPress Upload)",
    variable=dry_run_var
)
dry_run_checkbox.pack(pady=6)

topics_label = ctk.CTkLabel(
    main_scroll,
    text="Manual Image Topics Fallback Optional",
    font=("Segoe UI", 14, "bold")
)
topics_label.pack(pady=4)

topics_box = ctk.CTkTextbox(main_scroll, width=620, height=115, font=("Segoe UI", 13))
topics_box.pack(pady=5)

wp_section = ctk.CTkFrame(main_scroll)
wp_section.pack(pady=12, padx=20)

wp_title = ctk.CTkLabel(wp_section, text="WordPress Upload Optional", font=("Segoe UI", 16, "bold"))
wp_title.grid(row=0, column=0, columnspan=2, pady=8)

wp_url_entry = ctk.CTkEntry(
    wp_section,
    width=620,
    height=38,
    placeholder_text="WordPress Site URL e.g. https://roofpitchcalculators.com"
)
wp_url_entry.grid(row=1, column=0, columnspan=2, padx=10, pady=5)

wp_user_entry = ctk.CTkEntry(
    wp_section,
    width=300,
    height=38,
    placeholder_text="WordPress Username"
)
wp_user_entry.grid(row=2, column=0, padx=10, pady=5)

wp_pass_entry = ctk.CTkEntry(
    wp_section,
    width=300,
    height=38,
    placeholder_text="Application Password",
    show="*"
)
wp_pass_entry.grid(row=2, column=1, padx=10, pady=5)

start_btn = ctk.CTkButton(
    main_scroll,
    text="START CONVERSION",
    width=280,
    height=52,
    command=start_conversion
)
start_btn.pack(pady=15)

progress_bar = ctk.CTkProgressBar(main_scroll, width=780, height=18)
progress_bar.pack(pady=6)
progress_bar.set(0)

progress_label = ctk.CTkLabel(
    main_scroll,
    text="Progress: 0% | Done: 0 | Remaining: 0",
    font=("Segoe UI", 13, "bold")
)
progress_label.pack(pady=4)

button_frame = ctk.CTkFrame(main_scroll)
button_frame.pack(pady=12)

output_btn = ctk.CTkButton(button_frame, text="Open Output Folder", width=200, command=open_output)
output_btn.grid(row=0, column=0, padx=12, pady=10)

metadata_btn = ctk.CTkButton(button_frame, text="Open Metadata Folder", width=200, command=open_metadata)
metadata_btn.grid(row=0, column=1, padx=12, pady=10)

log_label = ctk.CTkLabel(main_scroll, text="Process Log", font=("Segoe UI", 15, "bold"))
log_label.pack(pady=5)

log_box = ctk.CTkTextbox(main_scroll, width=940, height=240, font=("Consolas", 13))
log_box.pack(pady=10)

footer = ctk.CTkLabel(
    main_scroll,
    text="Dry Run ON = no WordPress upload | Gemini blank = manual topics mode | WordPress blank = local export only",
    font=("Segoe UI", 12)
)
footer.pack(pady=10)

app.mainloop()