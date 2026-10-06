# Bot Flora Fauna Jawa 🌿🐾

Sistem otomasi publikasi konten edukasi keanekaragaman hayati (flora dan fauna endemik/terancam punah) di Pulau Jawa. Bot ini beroperasi penuh di latar belakang menggunakan server **GitHub Actions**, meriset data satwa secara mandiri, lalu menyebarkannya ke Instagram, Facebook Page, dan Telegram tanpa intervensi manusia.

## ⚙️ Fitur Utama

* **Otomasi Jadwal (*Cron Scheduling*)**  
  Menggunakan pengatur waktu *cron* yang didistribusikan ke jam-jam unik (menghindari antrean server global) untuk mempublikasikan 6 konten harian secara teratur.
* **Generator Infografis (Playwright & Jinja2)**  
  Menyuntikkan hasil riset ke dalam *template* HTML khusus, lalu dipotret menjadi gambar PNG siap tayang menggunakan **Headless Browser** (*peramban web tanpa antarmuka grafis yang beroperasi senyap di dalam mesin server*).
* **Produksi Reels Sinematik (FFmpeg)**  
  Memproses gambar statis menjadi video vertikal 30 detik. FFmpeg merakit lapisan kanvas buram (*boxblur*), efek pergerakan perlahan (*zoompan*), dan menempelkan teks tertutup secara permanen (*hardsub*) langsung ke dalam piksel video.
* **Narasi AI & Suara Natural (Groq & Edge-TTS)**  
  Memanfaatkan Groq API (LLM) untuk meracik naskah pendek berbahasa Indonesia dan Inggris. Teks kemudian disuarakan oleh Edge-TTS dengan intonasi natural berstandar dokumenter alam.
* **Anti-Duplikasi (State Persistence)**  
  Memiliki sistem *Ledger* (buku catatan) internal (`posted.txt` & `history_reels.json`) untuk merekam jejak spesies yang sudah tayang, mencegah akun memposting satwa yang sama berulang kali.

## 📂 Struktur Repositori

```text
├── .github/workflows/
│   ├── daily_post.yml     # Pemicu bot infografis lokal (3x sehari)
│   └── daily_reels.yml    # Pemicu bot video Reels internasional (3x sehari)
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

Kurasi Gambar: Foto observasi asli ditarik langsung dari iNaturalist atau Wikimedia Commons, melewati filter lisensi ketat untuk menghindari pelanggaran hak cipta.

Perakitan Media:

Jalur Feed: Playwright mencetak tata letak HTML ke gambar resolusi tinggi.

Jalur Video: FFmpeg merender gabungan foto, audio, dan subtitle waktu nyata.

Distribusi Publik: Menggunakan jalur Meta Graph API (metode Resumable Upload untuk file besar) guna menerbitkan konten serentak ke Instagram dan Facebook, ditutup dengan pengiriman arsip bukti tayang ke Telegram.

🛠️ Persyaratan Pemasangan (Setup)
Agar bot ini berjalan di hasil forking (salinan repositori), beberapa variabel wajib ditanamkan ke dalam brankas keamanan GitHub Secrets:

GROQ_API_KEY: Kunci akses AI Groq.

FB_PAGE_ID: ID numerik Halaman Facebook.

FB_PAGE_ACCESS_TOKEN: Token statis dari Meta Developer (dengan izin publikasi konten video & gambar).

TELEGRAM_BOT_TOKEN: Kunci bot dari BotFather Telegram.

TELEGRAM_CHAT_ID: ID grup/akun personal Telegram untuk menerima notifikasi.
