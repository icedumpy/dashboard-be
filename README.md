# Practical FastAPI (QC)

## เตรียมค่าแวดล้อม

cp .env.example .env

## สตาร์ทระบบ

# จะ build image, รอฐานข้อมูลพร้อม, รัน alembic upgrade head (init + seed) แล้วเปิด API

docker compose up -d --build

# ตรวจสอบสุขภาพ

curl http://localhost:8000/health

## ทดสอบดึงข้อมูลตัวอย่าง

curl "http://localhost:8000/counters?limit=10"

## คำสั่ง migration เพิ่มเติม

# สร้างไฟล์ migration ใหม่

docker compose exec api alembic revision -m "add something"

# ใช้งาน migration

docker compose exec api alembic upgrade head

# ย้อน migration

docker compose exec api alembic downgrade -1

## Reset (ลบ data volume)

docker compose down -v
