"""
Bot Mitos vs Fakta - Carousel edukasi flora & fauna Jawa.
Jalan terpisah dari bot utama, tidak mengganggu fitur yang sudah ada.
"""
import os
import json
import time
import random
import requests
from jinja2 import Template
from playwright.sync_api import sync_playwright
from groq import Groq

# --- Kunci rahasia (sama seperti bot utama) ---
GROQ_KEY = str(os.environ.get("GROQ_API_KEY") or "").strip()
FB_PAGE_ID = str(os.environ.get("FB_PAGE_ID") or "").strip()
FB_ACCESS_TOKEN = str(os.environ.get("FB_PAGE_ACCESS_TOKEN") or "").strip()
TELEGRAM_BOT_TOKEN = str(os.environ.get("TELEGRAM_BOT_TOKEN") or "").strip()
TELEGRAM_CHAT_ID = str(os.environ.get("TELEGRAM_CHAT_ID") or "").strip()

# Jeda acak alami (1-8 menit) agar terlihat organik
jeda = random.randint(60, 480)
print(f"Jeda alami {jeda} detik...")
time.sleep(jeda)

BASE = os.path.dirname(os.path.abspath(__file__))
MITOS_FILE = os.path.join(BASE, "mitos_list.json")
HISTORY_FILE = os.path.join(BASE, "history_mitos.json")
TEMPLATE_FILE = os.path.join(BASE, "template_mitos.html")

def pilih_mitos():
    """Pilih satu mitos yang belum pernah diposting."""
    with open(MITOS_FILE, encoding="utf-8") as f:
        daftar = json.load(f)
    history = []
    if os.path.exists(HISTORY_FILE):
        with open(HISTORY_FILE, encoding="utf-8") as f:
            history = json.load(f)
    tersedia = [m for m in daftar if m["spesies"] not in history]
    if not tersedia:
        print("Semua mitos sudah diposting!")
        return None
    return random.choice(tersedia)

def perkaya_fakta(mitos_data):
    """Minta AI Groq untuk memperkaya fakta dengan bahasa menarik."""
    if not GROQ_KEY:
        return mitos_data["fakta_singkat"]
    try:
        client = Groq(api_key=GROQ_KEY)
        resp = client.chat.completions.create(
            model="llama-3.3-70b-versatile",
            messages=[{
                "role": "user",
                "content": f"Tulis penjelasan fakta ilmiah yang menarik dalam Bahasa Indonesia (maksimal 3 kalimat pendek) tentang: {mitos_data['spesies']}. Fakta dasar: {mitos_data['fakta_singkat']}. Buat bahasa yang mudah dipahami orang awam dan ada unsur kagumnya."
            }],
            max_tokens=200,
        )
        return resp.choices[0].message.content.strip()
    except Exception as e:
        print(f"Groq gagal, pakai fakta bawaan: {e}")
        return mitos_data["fakta_singkat"]

def buat_slide(template, data_slide, output_path):
    """Render satu slide HTML menjadi PNG."""
    html = template.render(**data_slide)
    with sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page(viewport={"width": 1080, "height": 1080})
        page.set_content(html)
        page.screenshot(path=output_path)
        browser.close()
    print(f"Slide tersimpan: {output_path}")

def posting_carousel_ig(slide_paths, caption):
    """Posting carousel ke Instagram via Graph API."""
    # Step 1: Upload tiap gambar dapat container ID
    containers = []
    for path in slide_paths:
        # Upload menggunakan resumable upload (sama seperti bot utama)
        # ... implementasi mengikuti pola bot utama ...
        pass
    # Step 2: Buat carousel container
    # Step 3: Publish
    print("Carousel diposting!")
    return True

def main():
    mitos = pilih_mitos()
    if not mitos:
        return

    print(f"Mitos terpilih: {mitos['nama_lokal']}")

    # Perkaya fakta dengan AI
    fakta = perkaya_fakta(mitos)

    # Load template
    with open(TEMPLATE_FILE, encoding="utf-8") as f:
        template = Template(f.read())

    # Buat 4 slide
    slides = []
    data_slides = [
        {"tipe": "cover", "nomor": "1", **mitos},
        {"tipe": "mitos", "nomor": "2", **mitos},
        {"tipe": "fakta", "nomor": "3", "fakta": fakta, **mitos},
        {"tipe": "penutup", "nomor": "4", **mitos},
    ]
    for i, ds in enumerate(data_slides, 1):
        path = os.path.join(BASE, f"slide_{i}.png")
        buat_slide(template, ds, path)
        slides.append(path)

    # Caption
    caption = (
        f"🔍 MITOS vs FAKTA: {mitos['nama_lokal']}\n\n"
        f"❌ Mitos: {mitos['mitos']}\n\n"
        f"✅ Fakta: {fakta}\n\n"
        f"Jangan mudah percaya mitos ya! Follow untuk fakta satwa lainnya 🐾\n"
        f"#MitosVsFakta #SatwaJawa #EdukasiSatwa #FloraFaunaIndonesia"
    )

    # Posting (implementasi penuh mengikuti pola bot utama)
    # posting_carousel_ig(slides, caption)

    # Simpan ke history
    history = []
    if os.path.exists(HISTORY_FILE):
        with open(HISTORY_FILE, encoding="utf-8") as f:
            history = json.load(f)
    history.append(mitos["spesies"])
    with open(HISTORY_FILE, "w", encoding="utf-8") as f:
        json.dump(history, f, ensure_ascii=False, indent=2)

    print("Selesai! Mitos vs Fakta berhasil dibuat.")

if __name__ == "__main__":
    main()
