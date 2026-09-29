-- ตารางของบอท LINE ใน Neon
--
-- แยกไว้ใน schema "line_bot" เพื่อไม่ให้ปนกับตารางเดิม (เช่น "Shop") และให้เครื่องมือจัดการ schema
-- ของระบบเดิมที่ดูแลเฉพาะ schema public (เช่น Prisma, Drizzle) ไม่มองเห็นหรือพยายามลบตารางเหล่านี้
--
-- รันซ้ำได้ ไม่ลบหรือแก้ข้อมูลเดิม: วางทั้งไฟล์ใน SQL Editor ของ Neon หรือรัน `python -m bot init-db`

CREATE SCHEMA IF NOT EXISTS line_bot;

-- สถานะการทักของแต่ละร้าน (1 ร้าน / 1 เบอร์ ทักครั้งเดียว)
CREATE TABLE IF NOT EXISTS line_bot.outreach (
    id               BIGSERIAL    PRIMARY KEY,
    code             TEXT         GENERATED ALWAYS AS ('C' || id::text) STORED,  -- ชื่อเพื่อนใน LINE ขึ้นต้นด้วยรหัสนี้
    place_id         TEXT         NOT NULL UNIQUE,  -- "Shop"."placeId" (ไม่ใส่ foreign key เพื่อไม่ผูกกับระบบเดิม)
    shop_name        TEXT         NOT NULL,
    age_days         INTEGER,                       -- "Shop"."calculatedAgeDays" ตอนนำเข้า
    scan_id          TEXT,                          -- "Shop"."lastScanId" ตอนนำเข้า
    phone_raw        TEXT,                          -- เบอร์ตามที่อยู่ใน "Shop"."phoneNumber"
    phone            TEXT         UNIQUE,           -- normalize แล้ว เช่น 0812345678 (NULL ถ้าใช้ไม่ได้)
    invalid_reason   TEXT,                          -- no_phone | landline | bad_format | duplicate
    status           TEXT         NOT NULL DEFAULT 'PENDING',
    shop_state       TEXT,                          -- OPEN | NOT_OPEN_YET | CLOSED | UNKNOWN
    reply_group      TEXT,                          -- กลุ่มคำตอบแรกของร้าน (config/reply_rules.yaml)
    opener_variant   TEXT,                          -- ข้อความแรกที่ใช้ (Q1 / Q2)
    device_id        TEXT,                          -- ADB serial ของมือถือที่ทัก
    line_name        TEXT,                          -- ชื่อโปรไฟล์ LINE ตอนค้นเจอ
    needs_human      BOOLEAN      NOT NULL DEFAULT false,
    do_not_contact   BOOLEAN      NOT NULL DEFAULT false,
    attempts         SMALLINT     NOT NULL DEFAULT 0,
    last_error       TEXT,
    imported_at      TIMESTAMPTZ  NOT NULL DEFAULT now(),
    added_at         TIMESTAMPTZ,
    messaged_at      TIMESTAMPTZ,
    first_reply_at   TIMESTAMPTZ,
    pitched_at       TIMESTAMPTZ,
    last_reply_at    TIMESTAMPTZ,
    last_reply_text  TEXT,
    recontact_on     DATE,                          -- วันที่พนักงานนัดทักใหม่
    notes            TEXT,                          -- โน้ตของพนักงาน
    updated_at       TIMESTAMPTZ  NOT NULL DEFAULT now(),
    CONSTRAINT outreach_status_chk CHECK (status IN (
        'PENDING', 'INVALID', 'NOT_FOUND', 'ADDED', 'MESSAGED', 'REPLIED', 'PITCHED',
        'INTERESTED', 'DECLINED', 'CLOSED', 'NO_REPLY', 'CUSTOMER', 'ERROR')),
    CONSTRAINT outreach_shop_state_chk CHECK (
        shop_state IS NULL OR shop_state IN ('OPEN', 'NOT_OPEN_YET', 'CLOSED', 'UNKNOWN'))
);

-- คิวงาน: ร้าน PENDING ที่เปิดใหม่ล่าสุดก่อน
CREATE INDEX IF NOT EXISTS outreach_queue_idx ON line_bot.outreach (status, age_days, id);

-- ประวัติข้อความเข้า/ออก
CREATE TABLE IF NOT EXISTS line_bot.messages (
    id           BIGSERIAL    PRIMARY KEY,
    outreach_id  BIGINT       NOT NULL REFERENCES line_bot.outreach(id),
    direction    TEXT         NOT NULL CHECK (direction IN ('in', 'out')),
    sender       TEXT         NOT NULL CHECK (sender IN ('shop', 'bot', 'staff')),
    text         TEXT,
    source       TEXT         NOT NULL,             -- notification | chat_scan | chat_read | bot
    created_at   TIMESTAMPTZ  NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS messages_outreach_idx ON line_bot.messages (outreach_id, created_at);

-- มือถือ/บัญชี LINE ที่บอทใช้
CREATE TABLE IF NOT EXISTS line_bot.devices (
    device_id     TEXT         PRIMARY KEY,         -- ADB serial
    line_account  TEXT,
    daily_quota   SMALLINT     NOT NULL DEFAULT 15,
    status        TEXT         NOT NULL DEFAULT 'ACTIVE' CHECK (status IN ('ACTIVE', 'PAUSED', 'RESTRICTED')),
    paused_until  TIMESTAMPTZ,
    last_seen_at  TIMESTAMPTZ
);
