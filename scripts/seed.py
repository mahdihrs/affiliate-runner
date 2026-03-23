"""One-time seed script for niches and adlibs."""

import os
import sys
from dotenv import load_dotenv
from supabase import create_client

load_dotenv()

SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_KEY = os.getenv("SUPABASE_KEY")

if not SUPABASE_URL or not SUPABASE_KEY:
    print("SUPABASE_URL and SUPABASE_KEY must be set")
    sys.exit(1)

supabase = create_client(SUPABASE_URL, SUPABASE_KEY)

NICHES = [
    {
        "name": "rumah_tangga",
        "display_name": "Barang Rumah Tangga",
        "shopee_category_id": "",
        "keywords": ["peralatan rumah tangga", "alat dapur", "organizer rumah", "peralatan masak"],
    },
    {
        "name": "beauty",
        "display_name": "Beauty & Skincare",
        "shopee_category_id": "",
        "keywords": ["skincare", "beauty", "perawatan wajah", "kosmetik"],
    },
    {
        "name": "bayi_anak",
        "display_name": "Perlengkapan Bayi & Anak",
        "shopee_category_id": "",
        "keywords": ["perlengkapan bayi", "mainan anak", "botol susu", "baju bayi"],
    },
    {
        "name": "makanan_minuman",
        "display_name": "Makanan & Minuman",
        "shopee_category_id": "",
        "keywords": ["camilan", "makanan sehat", "minuman", "snack"],
    },
]

ADLIBS = {
    "rumah_tangga": [
        ("Cocok buat dapur kecil yang butuh alat multifungsi tanpa makan banyak tempat", "benefit"),
        ("Buat yang sering males beberes karena alatnya nggak praktis — ini lebih simpel", "pain_point"),
        ("Kalau kamu sering lupa matiin kompor, timer ini lumayan bantu", "pain_point"),
        ("Cocok digunakan untuk rumah yang sering lembab — bahan anti jamurnya lumayan tahan", "benefit"),
        ("Buat yang capek beli produk murahan yang cepat rusak, ini build quality-nya lebih solid", "pain_point"),
        ("Kalau kamu tinggal sendiri dan masak porsi kecil, ukurannya pas banget", "benefit"),
        ("Cocok buat yang mau dapur tetap rapi tanpa beli banyak organizer berbeda", "benefit"),
        ("Buat yang nggak mau ribet setup — langsung bisa dipakai out of the box", "benefit"),
        ("Kalau bau kulkas jadi masalah rutin di rumah kamu, ini worth dicoba dulu", "pain_point"),
        ("Cocok dipakai juga buat kos atau kontrakan, nggak makan tempat", "benefit"),
    ],
    "beauty": [
        ("Cocok buat kamu yang lagi nyari skincare harian tanpa banyak langkah", "benefit"),
        ("Buat kulit yang gampang breakout, formula ringan ini worth dicoba", "pain_point"),
        ("Kalau kamu tipe yang males ribet, satu produk ini bisa gantiin beberapa step", "benefit"),
        ("Pas buat yang baru mau mulai skincare tapi bingung mulai dari mana", "pain_point"),
        ("Buat yang sering skip sunscreen karena lengket — ini teksturnya beda", "pain_point"),
        ("Kalau kulit kamu cenderung kering di AC seharian, ini worth dicoba", "pain_point"),
        ("Cocok dipakai pagi sebelum makeup, nggak bikin pilling", "benefit"),
        ("Buat yang nggak mau keluar banyak tapi tetap mau rawat kulit", "benefit"),
        ("Formulanya cukup mild, cocok buat yang kulitnya sensitif", "benefit"),
        ("Kalau kamu sering lupa pakai skincare karena packagingnya ribet, yang ini simpel", "pain_point"),
    ],
    "bayi_anak": [
        ("Cocok buat bayi yang aktif gerak, bahannya nggak bikin gerah", "benefit"),
        ("Buat mama yang nggak mau ribet tiap mau nyusuin di luar rumah", "pain_point"),
        ("Kalau si kecil susah tidur, produk ini lumayan bantu bikin tidurnya lebih nyenyak", "pain_point"),
        ("Cocok digunakan untuk anak yang lagi fase oral — materialnya food grade", "benefit"),
        ("Buat yang sering panik kalau barang bayi ketinggalan waktu pergi — ini compact", "pain_point"),
        ("Kalau kamu capek cuci botol berkali-kali, desain wide neck ini lebih gampang dibersihin", "pain_point"),
        ("Cocok buat anak yang lagi belajar jalan, solnya cukup grip di lantai rumah", "benefit"),
        ("Buat yang mau stimulasi motorik anak tanpa harus keluar rumah", "benefit"),
        ("Kalau kamu khawatir soal bahan kimia, produk ini sudah certified bebas BPA", "pain_point"),
        ("Cocok dipakai dari newborn sampai usia toddler, jadi nggak cepat ganti", "benefit"),
    ],
    "makanan_minuman": [
        ("Cocok buat yang sering skip sarapan karena nggak sempat masak", "pain_point"),
        ("Buat yang lagi cari camilan yang nggak bikin terlalu guilty", "pain_point"),
        ("Kalau kamu sering ngidam sesuatu manis tapi mau tetap kontrol porsi", "pain_point"),
        ("Cocok digunakan untuk bekal kerja atau sekolah, nggak ribet dibawa", "benefit"),
        ("Buat yang bosan minum air putih polos seharian di kantor", "pain_point"),
        ("Kalau kamu susah makan sayur, ini salah satu cara yang lebih gampang", "pain_point"),
        ("Cocok buat yang lagi coba pola makan lebih teratur tanpa harus masak dari nol", "benefit"),
        ("Buat yang sering kehabisan stok di rumah — lebih hemat beli bundling", "benefit"),
        ("Kalau kamu butuh camilan yang bisa disimpan lama di laci kantor", "benefit"),
        ("Cocok buat anak-anak yang susah makan, rasanya nggak aneh-aneh", "benefit"),
    ],
}


def seed() -> None:
    """Seed niches and adlibs. Idempotent via upsert on niche name."""
    for niche_data in NICHES:
        # Upsert niche
        result = supabase.table("niches").upsert(
            niche_data, on_conflict="name"
        ).execute()
        niche_id = result.data[0]["id"]
        niche_name = niche_data["name"]
        print(f"Upserted niche: {niche_name} ({niche_id})")

        # Seed adlibs for this niche
        adlibs = ADLIBS.get(niche_name, [])
        for phrase, angle in adlibs:
            # Check if adlib already exists
            existing = (
                supabase.table("niche_adlibs")
                .select("id")
                .eq("niche_id", niche_id)
                .eq("phrase", phrase)
                .execute()
            )
            if existing.data:
                print(f"  Adlib already exists: {phrase[:50]}...")
                continue

            supabase.table("niche_adlibs").insert(
                {"niche_id": niche_id, "phrase": phrase, "angle": angle}
            ).execute()
            print(f"  Inserted adlib: {phrase[:50]}...")

    print("\nSeed complete: 4 niches + 40 adlibs")


if __name__ == "__main__":
    seed()
