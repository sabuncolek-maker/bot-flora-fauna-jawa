import os
import json
import random
import requests
from jinja2 import Template
from playwright.sync_api import sync_playwright
from google import genai
from google.genai import types

# 1. Validasi Environment Variables
GEMINI_KEY = os.environ.get("GEMINI_API_KEY")
FB_PAGE_ID = os.environ.get("FB_PAGE_ID")
FB_ACCESS_TOKEN = os.environ.get("FB_PAGE_ACCESS_TOKEN")

if not all([GEMINI_KEY, FB_PAGE_ID, FB_ACCESS_TOKEN]):
    raise ValueError("Error: Secrets belum lengkap diatur di GitHub!")

# 2. Manajemen Riwayat & Rotasi Spesies
SPECIES_FILE = "species_list.json"
HISTORY_FILE = "posted.txt"

with open(SPECIES_FILE, "r", encoding="utf-8") as f:
    all_species = json.load(f)

posted_species = []
if os.path.exists(HISTORY_FILE):
    with open(HISTORY_FILE, "r", encoding="utf-8") as f:
        posted_species = [line.strip() for line in f if line.strip()]

# Reset riwayat jika seluruh database sudah pernah diunggah
remaining_species = [s for s in all_species if s not in posted_species]
if not remaining_species:
    print("Semua spesies sudah terunggah. Mereset siklus riwayat...")
    posted_species = []
    remaining_species = all_species[:]

selected_latin = random.choice(remaining_species)
print(f"Target spesies hari ini: {selected_latin}")

# 3. Pengambilan Citra Wikipedia
def fetch_wikipedia_image(latin_name):
    headers = {"User-Agent": "FaunaBot/1.0 (contact@indobizarre.local)"}
    try:
        url = f"https://en.wikipedia.org/api/rest_v1/page/summary/{requests.utils.quote(latin_name)}"
        r = requests.get(url, headers=headers, timeout=10)
        if r.status_code == 200:
            res_data = r.json()
            if "originalimage" in res_data:
                return res_data["originalimage"]["source"]
            elif "thumbnail" in res_data:
                return res_data["thumbnail"]["source"]
    except Exception as e:
        print(f"Gagal mengambil gambar: {e}")
    return "https://images.unsplash.com/photo-1518709268805-4e9042af9f23?w=1080"

import time

# 4. Riset via Gemini API dengan Retry Otomatis
client = genai.Client(api_key=GEMINI_KEY)

prompt = f"""
Kamu adalah edukator biologi ahli flora & fauna Pulau Jawa.
Fokuskan riset kamu pada spesies ini: '{selected_latin}'.
Tentukan apakah ini FLORA atau FAUNA, cari nama umumnya di Indonesia, lokasi habitat spesifik di Jawa, dan 3 fakta uniknya.

Kembalikan format JSON murni tanpa markdown:
{{
  "category": "FLORA atau FAUNA",
  "name": "Nama Indonesia/Umum",
  "latin_name": "{selected_latin}",
  "habitat": "Lokasi spesifik di Jawa (contoh: TN Ujung Kulon, Gunung Gede Pangrango, Alas Purwo)",
  "facts": [
    {{"title": "Judul Fakta 1", "desc": "Penjelasan padat maksimal 20 kata"}},
    {{"title": "Judul Fakta 2", "desc": "Penjelasan padat maksimal 20 kata"}},
    {{"title": "Judul Fakta 3", "desc": "Penjelasan padat maksimal 20 kata"}}
  ],
  "fb_caption": "Teks naskah caption Facebook lengkap dan menarik, ada emotikon, fakta unik, edukasi pelestarian, dan hashtag relevan."
}}
"""

print("[1/4] Meriset konten via Gemini...")

# Coba hingga 3 kali jika server Google sedang sibuk (503)
response = None
for attempt in range(3):
    try:
        response = client.models.generate_content(
            model="gemini-3.8-flash",
            contents=prompt,
            config=types.GenerateContentConfig(
                response_mime_type="application/json"
            )
        )
        break
    except Exception as e:
        print(f"Server Google sibuk (percobaan {attempt+1}/3): {e}")
        time.sleep(5 * (attempt + 1))  # Beri jeda 5, 10 detik

if not response:
    raise RuntimeError("Gagal menghubungi Gemini setelah 3 percobaan.")

data = json.loads(response.text)
print(f"-> Terpilih: {data['name']} ({data['latin_name']})")

# 5. Rendering HTML ke Grafik Raster via Playwright
print("[2/4] Mengambil foto Wikipedia...")
data['image_url'] = fetch_wikipedia_image(selected_latin)

print("[3/4] Merender gambar infografis...")
with open("template.html", "r", encoding="utf-8") as f:
    template_str = f.read()

rendered_html = Template(template_str).render(**data)
with open("output.html", "w", encoding="utf-8") as f:
    f.write(rendered_html)

image_path = "post_image.png"
with sync_playwright() as p:
    browser = p.chromium.launch()
    page = browser.new_page(viewport={"width": 1080, "height": 1080})
    page.goto(f"file://{os.path.abspath('output.html')}", wait_until="networkidle")
    page.screenshot(path=image_path)
    browser.close()

# 6. Dispatch ke Graph API Facebook & Update Log
print("[4/4] Mengunggah ke Facebook...")
url = f"https://graph.facebook.com/v21.0/{FB_PAGE_ID}/photos"
with open(image_path, "rb") as img_file:
    payload = {"caption": data["fb_caption"], "access_token": FB_ACCESS_TOKEN}
    res = requests.post(url, data=payload, files={"source": img_file})

res_json = res.json()
if "id" in res_json:
    print(f"SUKSES TAYANG! Post ID: {res_json['id']}")
    posted_species.append(selected_latin)
    with open(HISTORY_FILE, "w", encoding="utf-8") as f:
        for s in posted_species:
            f.write(f"{s}\n")
else:
    print(f"GAGAL UPLOAD: {res_json}")
    exit(1)
