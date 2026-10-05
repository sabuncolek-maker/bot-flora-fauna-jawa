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
TELEGRAM_BOT_TOKEN = str(os.environ.get("TELEGRAM_BOT_TOKEN") or "").strip()
TELEGRAM_CHAT_ID = str(os.environ.get("TELEGRAM_CHAT_ID") or "").strip()

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

# ==========================================
# 3. Ambil Foto (Wikipedia -> iNaturalist -> Cadangan)
# ==========================================
def fetch_inaturalist_image(latin_name):
    """Mencari foto observasi satwa liar asli dari API iNaturalist"""
    try:
        url = f"https://api.inaturalist.org/v1/taxa?q={requests.utils.quote(latin_name)}&locale=id"
        r = requests.get(url, timeout=10)
        if r.status_code == 200:
            res_data = r.json()
            results = res_data.get("results", [])
            if results:
                default_photo = results[0].get("default_photo")
                if default_photo and "medium_url" in default_photo:
                    # Mengambil foto resolusi lebih tajam (large)
                    img_url = default_photo["medium_url"].replace("medium", "large")
                    print(f"-> Foto berhasil diambil dari iNaturalist!")
                    return img_url
    except Exception as e:
        print(f"Gagal mengambil dari iNaturalist: {e}")
    return None

def fetch_species_image(latin_name):
    """Pencarian foto bertingkat: Wikipedia -> iNaturalist -> Unsplash"""
    headers = {"User-Agent": "FaunaBot/1.0 (contact@indobizarre.local)"}
    cadangan = "https://images.unsplash.com/photo-1518709268805-4e9042af9f23?w=1080"
    
    # 1. Coba ambil dari Wikipedia
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

            # Filter deteksi peta / grafik vektor
            if img_url:
                url_kecil = img_url.lower()
                kata_terlarang = ["map", "range", "distribution", "sebaran", ".svg"]
                if not any(kata in url_kecil for kata in kata_terlarang):
                    print(f"-> Foto berhasil diambil dari Wikipedia!")
                    return img_url
                else:
                    print(f"Foto Wikipedia terdeteksi peta, beralih ke iNaturalist...")
    except Exception as e:
        print(f"Gagal memproses Wikipedia: {e}")

    # 2. Jika Wikipedia berupa peta atau kosong, coba cari di iNaturalist
    inat_img = fetch_inaturalist_image(latin_name)
    if inat_img:
        return inat_img

    # 3. Jika keduanya nihil, pakai cadangan alam Unsplash
    print("Foto spesifik tidak ditemukan, memakai foto cadangan alam.")
    return cadangan

def send_telegram_alert(pesan):
    """Mengirim pesan ringkas ke aplikasi Telegram"""
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

# ==========================================
# Fungsi Posting ke Instagram (Two-Step Publish)
# ==========================================
def post_to_instagram(fb_photo_id, caption):
    """
    Mengunggah foto ke Instagram:
    1. Ambil Instagram Account ID yang tertaut di Facebook Page.
    2. Ambil URL publik foto dari Facebook CDN.
    3. Buat Container ID (wadah penampung media sementara).
    4. Terbitkan kontainer ke feed Instagram.
    """
    try:
        # 1. Deteksi otomatis Akun Instagram Bisnis
        url_page = f"https://graph.facebook.com/v21.0/{FB_PAGE_ID}?fields=instagram_business_account&access_token={FB_ACCESS_TOKEN}"
        r_page = requests.get(url_page, timeout=10).json()
        ig_account = r_page.get("instagram_business_account")
        
        if not ig_account:
            print("Peringatan: Tidak ditemukan akun Instagram Bisnis yang tertaut ke Halaman Facebook ini.")
            return None
        
        ig_user_id = ig_account["id"]

        # 2. Ambil link gambar dari Facebook yang baru saja diunggah
        url_photo = f"https://graph.facebook.com/v21.0/{fb_photo_id}?fields=images&access_token={FB_ACCESS_TOKEN}"
        r_photo = requests.get(url_photo, timeout=10).json()
        images = r_photo.get("images", [])
        if not images:
            print("Gagal mengambil tautan gambar dari server Facebook.")
            return None
        
        image_url_public = images[0]["source"]

        # 3. Tahap 1: Buat Media Container
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

        # Beri jeda 5 detik agar server Meta selesai memproses gambar
        print("Menunggu sinkronisasi media Instagram...")
        time.sleep(5)

        # 4. Tahap 2: Publikasikan ke Feed
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
data['image_url'] = fetch_species_image(selected_latin)

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

# Evaluasi respons API: Mengirim laporan ke Telegram berdasarkan status berhasil atau gagal
if "id" in res_json:
    post_id = res_json['id']
    print(f"SUKSES TAYANG DI FACEBOOK! Post ID: {post_id}")
    
    # Kirim otomatis ke Instagram
    print("Mengirim konten ke Instagram...")
    ig_post_id = post_to_instagram(post_id, data["fb_caption"])
    
    # Catat nama spesies ini ke daftar agar tidak di-post ulang besok
    posted_species.append(selected_latin)
    with open(HISTORY_FILE, "w", encoding="utf-8") as f:
        for s in posted_species:
            f.write(f"{s}\n")
            
    # Laporan sukses ke Telegram
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
