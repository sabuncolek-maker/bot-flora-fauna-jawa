import os
import json
import time
import random
import requests
from jinja2 import Template
from playwright.sync_api import sync_playwright
from groq import Groq

# ==========================================
# 1. Cek Kunci Rahasia (Environment Variables)
# ==========================================
# Pakai .strip() untuk membuang spasi atau enter yang tidak sengaja terbawa
GROQ_KEY = str(os.environ.get("GROQ_API_KEY") or "").strip()
FB_PAGE_ID = str(os.environ.get("FB_PAGE_ID") or "").strip()
FB_ACCESS_TOKEN = str(os.environ.get("FB_PAGE_ACCESS_TOKEN") or "").strip()

if not GROQ_KEY or not FB_PAGE_ID or not FB_ACCESS_TOKEN:
    raise ValueError("Error: Kunci rahasia (GROQ_API_KEY, FB_PAGE_ID, FB_PAGE_ACCESS_TOKEN) belum lengkap diatur di GitHub Secrets!")

# ==========================================
# 2. Atur Riwayat & Pilih Spesies
# ==========================================
SPECIES_FILE = "species_list.json"
HISTORY_FILE = "posted.txt"

with open(SPECIES_FILE, "r", encoding="utf-8") as f:
    all_species = json.load(f)

posted_species = []
if os.path.exists(HISTORY_FILE):
    with open(HISTORY_FILE, "r", encoding="utf-8") as f:
        posted_species = [line.strip() for line in f if line.strip()]

# Jika semua spesies sudah diposting, ulang dari awal
remaining_species = [s for s in all_species if s not in posted_species]
if not remaining_species:
    print("Semua spesies sudah pernah diunggah. Mengulang putaran daftar dari awal...")
    posted_species = []
    remaining_species = all_species[:]

selected_latin = random.choice(remaining_species)
print(f"Target spesies hari ini: {selected_latin}")

# 3. Pengambilan Citra Wikipedia dengan Filter Peta
def fetch_wikipedia_image(latin_name):
    headers = {"User-Agent": "FaunaBot/1.0 (contact@indobizarre.local)"}
    # Gambar cadangan bernuansa alam jika Wikipedia tidak punya foto asli
    cadangan = "https://images.unsplash.com/photo-1518709268805-4e9042af9f23?w=1080"
    
    try:
        url = f"https://en.wikipedia.org/api/rest_v1/page/summary/{requests.utils.quote(latin_name)}"
        r = requests.get(url, headers=headers, timeout=10)
        if r.status_code == 200:
            res_data = r.json()
            img_url = ""
            
            if "originalimage" in res_data:
                img_url = res_data["originalimage"]["source"]
            elif "thumbnail" in res_data:
                img_url = res_data["thumbnail"]["source"]

            # Filter: Cek apakah gambar yang didapat adalah peta atau file vektor
            if img_url:
                url_kecil = img_url.lower()
                kata_terlarang = ["map", "range", "distribution", "sebaran", ".svg"]
                
                # Jika ada indikasi peta, buang dan gunakan cadangan
                if any(kata in url_kecil for kata in kata_terlarang):
                    print(f"Gambar Wikipedia terdeteksi peta/diagram, beralih ke cadangan.")
                    return cadangan
                
                return img_url
    except Exception as e:
        print(f"Gagal mengambil gambar: {e}")
        
    return cadangan

# ==========================================
# 4. Riset Konten Lewat Groq
# ==========================================
client = Groq(api_key=GROQ_KEY)

system_prompt = (
    "Kamu adalah pencerita alam liar Pulau Jawa yang seru dan bersahabat. "
    "Gunakan bahasa Indonesia yang sederhana, membumi, mengalir santai, dan tidak kaku seperti buku teks formal. "
    "Hindari istilah biologi rumit yang bikin bingung orang awam. Jika ada istilah baru, jelaskan artinya secara singkat dan alami. "
    "Wajib berikan balasan HANYA dalam format JSON valid tanpa tanda kutip markdown (```json)."
)

user_prompt = f"""
Riset spesies ini: '{selected_latin}'.
Tentukan apakah ini FLORA (tumbuhan) atau FAUNA (hewan), sebutkan status konservasinya, nama lokal/populernya di Indonesia, lokasi alamnya di Jawa, serta 3 fakta serunya.

Kaidah isi:
1. 'iucn_status': Isi status kelangkaan dalam bahasa Indonesia yang ringkas, contoh: 'Kritis (CR)', 'Genting (EN)', 'Rentan (VU)', 'Risiko Rendah (LC)', atau 'Belum Dievaluasi'.
2. 'facts': Buat 3 fakta unik yang ceritanya enak dibaca orang biasa. Penjelasannya padat, maksimal 20 kata per fakta.
3. 'fb_caption': Tulis naskah postingan Facebook yang ramah, santai, sisipkan fakta status kelangkaannya, ajakan menjaga alam, emotikon, dan hashtag relevan.

Ikuti format JSON persis seperti ini:
{{
  "category": "FLORA atau FAUNA",
  "iucn_status": "Contoh: Genting (EN)",
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
    raise RuntimeError("Gagal mengambil data dari Groq setelah 3 percobaan. Kemungkinan server Groq sedang penuh.")

print(f"-> Terpilih: {data['name']} ({data['latin_name']})")

# ==========================================
# 5. Pasang Data ke Desain HTML lalu Ubah ke Gambar PNG
# ==========================================
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

# ==========================================
# 6. Kirim Postingan ke Facebook & Catat Riwayat
# ==========================================
print("[4/4] Mengunggah ke Facebook...")

# Memecah teks link agar kebal dari format otomatis
bagian_1 = "https://"
bagian_2 = "graph.facebook.com/v21.0/"
fb_url = bagian_1 + bagian_2 + FB_PAGE_ID + "/photos"

with open(image_path, "rb") as img_file:
    payload = {
        "caption": data["fb_caption"], 
        "access_token": FB_ACCESS_TOKEN
    }
    # Kirim foto dan teksnya ke server Facebook
    res = requests.post(fb_url, data=payload, files={"source": img_file})

res_json = res.json()

if "id" in res_json:
    print(f"SUKSES TAYANG! Post ID: {res_json['id']}")
    # Catat nama spesies ini ke daftar agar tidak di-post ulang besok
    posted_species.append(selected_latin)
    with open(HISTORY_FILE, "w", encoding="utf-8") as f:
        for s in posted_species:
            f.write(f"{s}\n")
else:
    print(f"GAGAL UPLOAD: {res_json}")
    exit(1)
