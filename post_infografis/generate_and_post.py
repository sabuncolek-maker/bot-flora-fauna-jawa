import os
import json
import time
import random
import requests
from jinja2 import Template
from playwright.sync_api import sync_playwright
from groq import Groq
import sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from post_shared.quality import request_json, request_bytes, is_image_bytes, parse_json_object, validate_infographic_payload

# ==========================================
# 1. Cek Kunci Rahasia (Environment Variables)
# ==========================================
GROQ_KEY = str(os.environ.get("GROQ_API_KEY") or "").strip()
FB_PAGE_ID = str(os.environ.get("FB_PAGE_ID") or "").strip()
FB_ACCESS_TOKEN = str(os.environ.get("FB_PAGE_ACCESS_TOKEN") or "").strip()
TELEGRAM_BOT_TOKEN = str(os.environ.get("TELEGRAM_BOT_TOKEN") or "").strip()
TELEGRAM_CHAT_ID = str(os.environ.get("TELEGRAM_CHAT_ID") or "").strip()

# Jeda acak alami (1 sampai 8 menit)
jeda_detik = random.randint(60, 480)
print(f"Menunggu jeda alami selama {jeda_detik} detik...")
time.sleep(jeda_detik)

if not GROQ_KEY or not FB_PAGE_ID or not FB_ACCESS_TOKEN:
    raise ValueError("Error: Kunci rahasia (GROQ_API_KEY, FB_PAGE_ID, FB_PAGE_ACCESS_TOKEN) belum lengkap diatur di GitHub Secrets!")

# ==========================================
# 2. Fungsi Koleksi Spesies Otomatis dari GBIF API
# ==========================================
def get_species_from_gbif(posted_list):
    """
    Mengambil nama ilmiah flora/fauna liar di koordinat Pulau Jawa
    dengan status terancam punah (CR, EN, VU) langsung dari basis data GBIF.
    """
    try:
        # Poligon area batas daratan Pulau Jawa dalam format WKT
        polygon_jawa = "POLYGON((105.1 -5.8, 114.6 -5.8, 114.6 -8.8, 105.1 -8.8, 105.1 -5.8))"
        offset_acak = random.randint(0, 150)
        
        url_gbif = "https://api.gbif.org/v1/occurrence/search"
        params = {
            "country": "ID",
            "geometry": polygon_jawa,
            "iucnRedListCategory": ["CR", "EN", "VU"],
            "hasCoordinate": "true",
            "limit": 50,
            "offset": offset_acak
        }
        
        data = request_json("GET", url_gbif, params=params, timeout=20)
        results = data.get("results", [])
        
        kandidat = []
        for item in results:
            nama = item.get("species")
            if nama and (nama not in posted_list) and (nama not in kandidat):
                kandidat.append(nama)
                
        if kandidat:
            terpilih = random.choice(kandidat)
            print(f"[GBIF] Spesies liar Jawa ditemukan otomatis: {terpilih}")
            return terpilih
        else:
            print("[GBIF] Tidak ada spesies baru di halaman ini, beralih ke cadangan lokal.")
            return None

    except Exception as e:
        print(f"[GBIF] Kendala koneksi ke server GBIF: {e}")
        return None

# ==========================================
# 3. Penentuan Spesies Target (GBIF -> Cadangan JSON)
# ==========================================
HISTORY_FILE = "posted.txt"

# Baca riwayat postingan
posted_species = []
if os.path.exists(HISTORY_FILE):
    with open(HISTORY_FILE, "r", encoding="utf-8") as f:
        posted_species = [line.strip() for line in f if line.strip()]

# Tahap 1: Coba ambil spesies otomatis dari server GBIF
selected_latin = get_species_from_gbif(posted_species)

# Tahap 2: Fallback (cadangan otomatis jika GBIF bermasalah atau kosong)
if not selected_latin:
    raise RuntimeError("GBIF tidak memberikan kandidat spesies Jawa yang tervalidasi; job dihentikan agar tidak menerbitkan data yang belum terverifikasi.")

print(f"Target spesies hari ini: {selected_latin}")

# ==========================================
# 4. Fungsi Pembantu (Foto, Notifikasi, Instagram)
# ==========================================
def fetch_inaturalist_image(latin_name):
    """Mencari foto observasi spesies dari iNaturalist."""
    try:
        url = f"https://api.inaturalist.org/v1/taxa?q={requests.utils.quote(latin_name)}&locale=id&license=cc0,cc-by"
        res_data = request_json("GET", url, timeout=15)
        for result in res_data.get("results", []):
            photo = result.get("default_photo") or {}
            img_url = photo.get("large_url") or photo.get("medium_url")
            if img_url:
                print("-> Foto berhasil diambil dari iNaturalist!")
                return img_url
    except Exception as e:
        print(f"Gagal mengambil dari iNaturalist: {e}")
    return None


def fetch_species_image(latin_name):
    headers = {"User-Agent": "FaunaBot/1.0"}
    try:
        url = f"https://en.wikipedia.org/api/rest_v1/page/summary/{requests.utils.quote(latin_name)}"
        res_data = request_json("GET", url, headers=headers, timeout=15)
        img_url = (res_data.get("originalimage") or res_data.get("thumbnail") or {}).get("source", "")
        if img_url:
            lower = img_url.lower()
            if not any(word in lower for word in ["map", "range", "distribution", "sebaran", ".svg"]):
                print("-> Foto berhasil diambil dari Wikipedia!")
                return img_url, "Wikimedia Commons"
    except Exception as e:
        print(f"Gagal memproses Wikipedia: {e}")

    inat_img = fetch_inaturalist_image(latin_name)
    if inat_img:
        return inat_img, "iNaturalist"

    raise RuntimeError(f"Tidak ditemukan foto spesifik yang layak untuk {latin_name}; job dihentikan.")
    
def send_telegram_alert(pesan):
    """Mengirim notifikasi status ke Telegram"""
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        return
    try:
        url_tg = "https://api.telegram.org/bot" + TELEGRAM_BOT_TOKEN + "/sendMessage"
        payload = {
            "chat_id": TELEGRAM_CHAT_ID,
            "text": pesan,
            "parse_mode": "Markdown"
        }
        requests.post(url_tg, json=payload, timeout=10)
    except Exception as e:
        print(f"Gagal mengirim notifikasi Telegram: {e}")

def post_to_instagram(fb_photo_id, caption):
    """Mengunggah foto ke feed Instagram via Two-Step Publish"""
    try:
        url_page = f"https://graph.facebook.com/v21.0/{FB_PAGE_ID}?fields=instagram_business_account&access_token={FB_ACCESS_TOKEN}"
        r_page = requests.get(url_page, timeout=10).json()
        ig_account = r_page.get("instagram_business_account")
        
        if not ig_account:
            print("Peringatan: Tidak ditemukan akun Instagram Bisnis yang tertaut.")
            return None
        
        ig_user_id = ig_account["id"]

        url_photo = f"https://graph.facebook.com/v21.0/{fb_photo_id}?fields=images&access_token={FB_ACCESS_TOKEN}"
        r_photo = requests.get(url_photo, timeout=10).json()
        images = r_photo.get("images", [])
        if not images:
            print("Gagal mengambil tautan gambar dari Facebook.")
            return None
        
        image_url_public = images[0]["source"]

        # Tahap 1: Wadah Media
        url_container = f"https://graph.facebook.com/v21.0/{ig_user_id}/media"
        payload_container = {
            "image_url": image_url_public,
            "caption": caption,
            "access_token": FB_ACCESS_TOKEN
        }
        res_container = requests.post(url_container, data=payload_container, timeout=15).json()
        creation_id = res_container.get("id")

        if not creation_id:
            print(f"Gagal membuat container Instagram: {res_container}")
            return None

        print("Menunggu sinkronisasi media Instagram...")
        time.sleep(5)

        # Tahap 2: Publikasi
        url_publish = f"https://graph.facebook.com/v21.0/{ig_user_id}/media_publish"
        payload_publish = {
            "creation_id": creation_id,
            "access_token": FB_ACCESS_TOKEN
        }
        res_publish = requests.post(url_publish, data=payload_publish, timeout=15).json()
        ig_post_id = res_publish.get("id")

        if ig_post_id:
            print(f"SUKSES TAYANG DI INSTAGRAM! ID: {ig_post_id}")
            return ig_post_id
        else:
            print(f"Gagal menerbitkan di Instagram: {res_publish}")
            return None

    except Exception as e:
        print(f"Kendala saat proses kirim ke Instagram: {e}")
        return None

# ==========================================
# 5. Riset Konten Lewat Groq
# ==========================================
client = Groq(api_key=GROQ_KEY)

system_prompt = (
    "Kamu adalah pencerita alam liar Pulau Jawa yang seru dan bersahabat. "
    "Gunakan bahasa Indonesia yang sederhana, membumi, mengalir santai, dan tidak kaku seperti buku teks formal. "
    "Hindari istilah biologi rumit yang bikin bingung orang awam. Jika ada istilah baru, jelaskan artinya secara singkat dan alami. "
    "Wajib berikan balasan HANYA dalam format JSON valid tanpa tanda kutip markdown (```json)."
)

user_prompt = f"""
Lakukan riset biologis yang SANGAT AKURAT untuk spesies ini: '{selected_latin}'.
PENTING: Jangan berhalusinasi. Pastikan nama lokal/Indonesia yang kamu berikan benar-benar pasangan yang sah dari nama latin tersebut (Misal: jika latinnya Leucopsar rothschildi, maka namanya harus Jalak Bali, BUKAN Elang Jawa).

Tentukan apakah ini FLORA (tumbuhan) atau FAUNA (hewan), sebutkan status konservasinya, nama lokalnya yang paling valid, lokasi alamnya di Jawa/Indonesia, serta 3 fakta serunya.

Kaidah isi:
1. 'iucn_status': Isi status kelangkaan dalam bahasa Indonesia yang ringkas, contoh: 'Kritis (CR)', 'Genting (EN)'.
2. 'facts': Buat 3 fakta unik yang ceritanya enak dibaca orang biasa. Penjelasannya padat, maksimal 20 kata per fakta. Pastikan faktanya relevan dengan wujud asli spesies tersebut! ATURAN KERAS ANTI-TAUTOLOGI: 'desc' DILARANG mengulang 'title' dengan kata lain — 'desc' WAJIB menambah informasi baru berupa fungsi, angka, perbandingan, sebab-akibat, atau perilaku. Contoh SALAH: title 'Bulu Hitam di Sayap' + desc 'Sayapnya berwarna hitam pekat'. Contoh BENAR: desc menjelaskan GUNA atau DAMPAK dari ciri tersebut, bukan sekadar mendeskripsikan ulang.
3. 'fb_caption': Tulis naskah postingan Facebook yang ramah, santai, sisipkan fakta kelangkaan dan hashtag relevan.

Ikuti format JSON persis seperti ini:
{{
  "category": "FLORA atau FAUNA",
  "iucn_status": "Contoh: Genting (EN)",
  "name": "Nama Indonesia Valid (Jangan Mengarang)",
  "latin_name": "{selected_latin}",
  "habitat": "Contoh: Hutan Lindung Gunung Slamet, TN Ujung Kulon, dll",
  "facts": [
    {{"title": "Judul Fakta 1", "desc": "Penjelasan ringkas maksimal 20 kata"}},
    {{"title": "Judul Fakta 2", "desc": "Penjelasan ringkas maksimal 20 kata"}},
    {{"title": "Judul Fakta 3", "desc": "Penjelasan ringkas maksimal 20 kata"}}
  ],
  "fb_caption": "Naskah lengkap caption Facebook"
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

# ==========================================
# 6. Pasang Data ke Desain HTML lalu Render Gambar PNG
# ==========================================
print("[2/4] Mengambil foto...")
img_url, sumber_foto = fetch_species_image(selected_latin)
data['image_url'] = img_url
data['image_source'] = sumber_foto

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
# 7. Unggah Postingan ke Facebook & Catat Riwayat
# ==========================================
print("[4/4] Mengunggah ke Facebook...")

fb_url = f"https://graph.facebook.com/v21.0/{FB_PAGE_ID}/photos"

with open(image_path, "rb") as img_file:
    payload = {
        "caption": data["fb_caption"], 
        "access_token": FB_ACCESS_TOKEN
    }
    res = requests.post(fb_url, data=payload, files={"source": img_file}, timeout=30)
if not res.ok:
    raise RuntimeError(f"Facebook upload gagal HTTP {res.status_code}: {res.text[:500]}")

res_json = res.json()

if "id" in res_json:
    post_id = res_json['id']
    print(f"SUKSES TAYANG DI FACEBOOK! Post ID: {post_id}")
    
    print("Mengirim konten ke Instagram...")
    ig_post_id = post_to_instagram(post_id, data["fb_caption"])
    
    posted_species.append(selected_latin)
    with open(HISTORY_FILE, "w", encoding="utf-8") as f:
        for s in posted_species:
            f.write(f"{s}\n")
            
    status_ig_text = f"✅ `{ig_post_id}`" if ig_post_id else "⚠️ Lewat / Gagal"
    laporan = (
        f"✅ *Konten Berhasil Dipublikasikan!*\n\n"
        f"📌 *Spesies:* {data['name']} (`{selected_latin}`)\n"
        f"🛡️ *Status:* {data.get('iucn_status', '-')}\n"
        f"📘 *Facebook:* `{post_id}`\n"
        f"📸 *Instagram:* {status_ig_text}"
    )
    send_telegram_alert(laporan)
else:
    print(f"GAGAL UPLOAD: {res_json}")
    laporan_gagal = f"❌ *Postingan Facebook Gagal Diunggah!*\n\nTarget: `{selected_latin}`\nError: `{res_json}`"
    send_telegram_alert(laporan_gagal)
    exit(1)
