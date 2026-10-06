# Bot Flora Fauna Jawa 🌿🐾

Sistem otomasi publikasi konten edukasi keanekaragaman hayati (flora dan fauna endemik/terancam punah) di Pulau Jawa. Bot ini beroperasi penuh di latar belakang menggunakan server **GitHub Actions**, meriset data satwa secara mandiri, lalu menyebarkannya ke Instagram, Facebook Page, dan Telegram tanpa intervensi manusia.

## ⚙️ Fitur Utama

* **Otomasi Jadwal & Jitter Anti-Spam**  
  Menggunakan pengatur waktu *cron* di menit ganjil, dikombinasikan dengan **Jitter Delay** (jeda eksekusi acak 1–8 menit). Trik ini membuat pola unggahan terlihat organik seperti perilaku manusia asli untuk menghindari *shadowban* dari algoritma keamanan Meta.
* **Penyaring Lisensi Hak Cipta**  
  Bot hanya menarik foto berlisensi terbuka (CC0 dan CC-BY) dari **iNaturalist** dan **Wikimedia Commons**, memastikan seluruh aset visual aman dari cap air (*watermark*) dan klaim hak cipta komersial.
* **Generator Infografis (Playwright & Jinja2)**  
  Menyuntikkan hasil riset ke dalam *template* HTML khusus, lalu dipotret menjadi gambar PNG siap tayang menggunakan peramban web tanpa antarmuka grafis (*headless browser*).
* **Produksi Reels Dinamis (FFmpeg)**  
  Memproses 6 gambar statis menjadi video vertikal 30 detik dengan ritme perpindahan cepat (5 detik per frame) untuk menjaga retensi penonton. FFmpeg merakit lapisan kanvas buram (*boxblur*), efek pergerakan perlahan (*zoompan*), dan menempelkan teks tertutup secara permanen (*hardsub*).
* **Narasi AI & Suara Natural (Groq & Edge-TTS)**  
  Memanfaatkan LLM Groq untuk meracik naskah pendek berbahasa Indonesia dan Inggris. Teks kemudian disuarakan oleh Edge-TTS dengan intonasi natural berstandar dokumenter alam.
* **Memori Anti-Duplikasi**  
  Memiliki sistem rekam jejak internal (`posted.txt` & `history_reels.json`) untuk mencegah bot mempublikasikan spesies yang sama berulang kali.

## 📂 Struktur Repositori

```text
├── .github/workflows/
│   ├── daily_post.yml       # Pemicu bot infografis lokal (3x sehari)
│   └── daily_reels.yml      # Pemicu bot video Reels internasional (3x sehari)
├── post_infografis/
│   ├── generate_and_post.py # Mesin utama perakit gambar HTML
│   ├── template.html        # Desain dasar tata letak infografis
│   └── posted.txt           # Catatan memori satwa untuk infografis
├── post_reels/
│   ├── generate_reels.py    # Mesin utama perakit video & audio
│   └── history_reels.json   # Catatan memori satwa untuk Reels
└── requirements.txt         # Daftar pustaka Python yang dibutuhkan

🚀 Alur Kerja Sistem (Workflow)
Pencarian Target: Bot memanggil GBIF API (basis data hayati global) untuk melacak spesies dengan status CR (Kritis), EN (Genting), atau VU (Rentan) di koordinat poligon Pulau Jawa.

Kurasi Gambar: Foto observasi asli ditarik otomatis, melewati filter lisensi ketat untuk menghindari jepretan fotografer komersial.

Perakitan Media:

Jalur Feed: Playwright mencetak tata letak HTML ke gambar resolusi tinggi.

Jalur Video: FFmpeg merender gabungan 6 foto, audio, dan subtitle waktu nyata.

Distribusi Publik: Menggunakan jalur Meta Graph API (metode Resumable Upload untuk file besar) guna menerbitkan konten serentak ke Instagram dan Facebook, diakhiri dengan pengiriman bukti tayang ke Telegram.

🛠️ Persyaratan Pemasangan (Setup)
Repositori ini disetel sebagai Public agar mendapat kuota menit GitHub Actions gratis tanpa batas (unlimited). Jika kamu melakukan forking (menyalin repositori), wajib menanamkan variabel berikut ke dalam brankas GitHub Secrets:

GROQ_API_KEY: Kunci akses API Groq.

FB_PAGE_ID: ID numerik Halaman Facebook.

FB_PAGE_ACCESS_TOKEN: Token statis dari Meta Developer (dengan izin publikasi konten media).

TELEGRAM_BOT_TOKEN: Kunci bot dari BotFather Telegram.

TELEGRAM_CHAT_ID: ID grup/akun personal Telegram untuk menerima notifikasi otomatis.
