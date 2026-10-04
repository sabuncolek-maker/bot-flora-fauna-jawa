import os
import json
import requests
from jinja2 import Template
from playwright.sync_api import sync_playwright
from google import genai
from google.genai import types

# 1. Mengambil kunci rahasia dari brankas GitHub Secrets
GEMINI_KEY = os.environ.get("GEMINI_API_KEY")
FB_PAGE_ID = os.environ.get("FB_PAGE_ID")
FB_ACCESS_TOKEN = os.environ.get("FB_PAGE_ACCESS_TOKEN")

if not all([GEMINI_KEY, FB_PAGE_ID, FB_ACCESS_TOKEN]):
    raise ValueError("Error: Kunci rahasia (Secrets) belum lengkap diatur di GitHub!")

# 2. Inisialisasi Koneksi ke Gemini API
client = genai.Client(api_key=GEMINI_KEY)

prompt = """
Kamu adalah edukator biologi ahli flora & fauna endemik/khas Pulau Jawa.
Pilih SATU flora atau fauna khas/endemik Pulau Jawa secara acak dan unik.
Berikan data fakta menarik dalam format JSON murni dengan struktur persis seperti ini:
{
  "category": "FLORA atau FAUNA",
  "name": "Nama Indonesia/Umum",
  "latin_name": "Nama Ilmiah Latin",
  "habitat": "Lokasi spesifik di Jawa (contoh: Taman Nasional Baluran, Ujung Kulon, Gunung Slamet)",
  "facts": [
    {"title": "Judul Fakta 1", "desc": "Penjelasan padat maksimal 20 kata"},
    {"title": "Judul Fakta 2", "desc": "Penjelasan padat maksimal 20 kata"},
    {"title": "Judul Fakta 3", "desc": "Penjelasan padat maksimal 20 kata"}
  ],
  "fb_caption": "Teks naskah caption Facebook lengkap dan menarik, ada emotikon, fakta unik, edukasi pelestarian, dan hashtag relevan."
}
Pastikan hanya mengembalikan teks JSON yang valid tanpa tanda petik markdown.
"""

print("[1/3] Menghubungi Gemini untuk riset flora/fauna Jawa...")
response = client.models.generate_content(
    model="gemini-3.8-flash",
    contents=prompt,
    config=types.GenerateContentConfig(
        response_mime_type="application/json"
    )
)

data = json.loads(response.text)
print(f"-> Berhasil riset: {data['name']} ({data['latin_name']})")

# 3. Merender HTML menjadi Gambar PNG menggunakan Headless Browser
print("[2/3] Merender gambar infografis...")
with open("template.html", "r", encoding="utf-8") as f:
    template_str = f.read()

jinja_template = Template(template_str)
rendered_html = jinja_template.render(**data)

with open("output.html", "w", encoding="utf-8") as f:
    f.write(rendered_html)

image_path = "post_image.png"
with sync_playwright() as p:
    browser = p.chromium.launch()
    page = browser.new_page(viewport={"width": 1080, "height": 1080})
    page.goto(f"file://{os.path.abspath('output.html')}")
    page.screenshot(path=image_path)
    browser.close()

print("-> Gambar berhasil dibuat (post_image.png)")

# 4. Mengunggah Gambar + Caption ke Facebook Page
print("[3/3] Mengunggah postingan ke Halaman Facebook...")
url = f"https://graph.facebook.com/v21.0/{FB_PAGE_ID}/photos"

with open(image_path, "rb") as img_file:
    payload = {
        "caption": data["fb_caption"],
        "access_token": FB_ACCESS_TOKEN
    }
    files = {
        "source": img_file
    }
    res = requests.post(url, data=payload, files=files)

res_json = res.json()
if "id" in res_json:
    print(f"SUKSES BESAR! Postingan berhasil tayang. ID Post: {res_json['id']}")
else:
    print(f"GAGAL UPLOAD KE FACEBOOK: {res_json}")
    exit(1)
