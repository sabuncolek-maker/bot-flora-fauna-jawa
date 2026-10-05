import os
import json
import time
import random
import requests
from jinja2 import Template
from playwright.sync_api import sync_playwright
from groq import Groq

# 1. Cek Kunci Rahasia (Environment Variables)
GROQ_KEY = os.environ.get("GROQ_API_KEY")
FB_PAGE_ID = os.environ.get("FB_PAGE_ID")
FB_ACCESS_TOKEN = os.environ.get("FB_PAGE_ACCESS_TOKEN")

if not all([GROQ_KEY, FB_PAGE_ID, FB_ACCESS_TOKEN]):
    raise ValueError("Error: Kunci rahasia (GROQ_API_KEY, FB_PAGE_ID, FB_PAGE_ACCESS_TOKEN) belum lengkap diatur di GitHub Secrets!")

# 2. Atur Riwayat & Pilih Spesies yang Belum Pernah Diposting
SPECIES_FILE = "species_list.json"
HISTORY_FILE = "posted.txt"

with open(SPECIES_FILE, "r", encoding="utf-8") as f:
    all_species = json.load(f)

posted_species = []
if os.path.exists(HISTORY_FILE):
    with open(HISTORY_FILE, "r", encoding="utf-8") as f:
        posted_species = [line.strip() for line in f if line.strip()]

# Jika semua spesies dalam daftar sudah pernah diposting, ulang dari awal
remaining_species = [s for s in all_species if s not in posted_species]
if not remaining_species:
    print("Semua spesies sudah pernah diunggah. Mengulang putaran daftar dari awal...")
    posted_species = []
    remaining_species = all_species[:]

selected_latin = random.choice(remaining_species)
print(f"Target spesies hari ini: {selected_latin}")

# 3. Ambil Foto dari Wikipedia
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
    # Gambar cadangan jika di Wikipedia tidak tersedia
    return "https://images.unsplash.com/photo-1518709268805-4e9042af9f23?w=1080"

# 4. Riset Konten Lewat Groq
client = Groq(api_key=GROQ_KEY)

# Instruksi peran AI: Menjelaskan dengan bahasa santai, lugas, dan mudah dicerna
system_prompt = (
    "Kamu adalah pencerita alam liar Pulau Jawa yang seru dan bersahabat. "
    "Gunakan bahasa Indonesia yang sederhana, membumi, mengalir santai, dan tidak kaku seperti buku teks formal. "
    "Hindari istilah biologi rumit yang bikin bingung orang awam, atau jika ada istilah baru, jelaskan artinya secara singkat dan alami. "
    "Wajib berikan balasan HANYA dalam format JSON valid tanpa tanda kutip markdown (```json)."
)

user_prompt = f"""
Riset spesies ini: '{selected_latin}'.
Tentukan apakah ini FLORA (tumbuhan) atau FAUNA (hewan), sebutkan nama lokal/populernya di Indonesia, lokasi alamnya di Jawa, serta 3 fakta serunya.

Kaidah isi:
1. 'facts': Buat 3 fakta unik yang ceritanya enak dibaca orang biasa. Penjelasannya padat, maksimal 20 kata per fakta.
2. 'fb_caption': Tulis naskah postingan Facebook yang ramah, enak dibaca seperti teman sedang bercerita, sisipkan emotikon yang pas, ajakan menjaga alam, dan beberapa tagar (#) yang relevan.

Ikuti format JSON persis seperti ini:
{{
  "category": "FLORA atau FAUNA",
  "name": "Nama Indonesia/Umum yang Akrab Didengar",
  "latin_name": "{selected_latin}",
  "habitat": "Contoh: Hutan Lindung Gunung Slamet, TN Ujung Kulon, dll",
  "facts": [
    {{"title": "Judul Fakta 1", "desc": "Penjelasan ringkas dan asyik maksimal 20 kata"}},
    {{"title": "Judul Fakta 2", "desc": "Penjelasan ringkas dan asyik maksimal 20 kata"}},
    {{"title": "Judul Fakta 3", "desc": "Penjelasan ringkas dan asyik maksimal 20 kata"}}
  ],
  "fb_caption": "Naskah lengkap caption Facebook santai dan membumi"
}}
"""

print("[1/4] Meriset konten via Groq...")

data = None
for attempt in range(3):
    try:
        chat_completion = client.chat.completions.create(
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt}
            ],
            model="openai/gpt-oss-120b",
            temperature=0.6,
            response_format={"type": "json_object"}
        )
        raw_text = chat_completion.choices[0].message.content
        data = json.loads(raw_text)
        break
    except Exception as e:
        print(f"Kendala menghubungi Groq (percobaan {attempt+1}/3): {e}")
        time.sleep(3 * (attempt + 1))

if not data:
    raise RuntimeError("Gagal mengambil data dari Groq setelah 3 percobaan.")

print(f"-> Terpilih: {data['name']} ({data['latin_name']})")

# 5. Pasang Data ke Desain HTML lalu Ubah ke Gambar PNG
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

# 6. Kirim Postingan ke Facebook & Catat di File posted.txt
print("[4/4] Mengunggah ke Facebook...")
url = f"[https://graph.facebook.com/v21.0/](https://graph.facebook.com/v21.0/){FB_PAGE_ID}/photos"
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
