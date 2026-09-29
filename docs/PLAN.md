# แผนพัฒนา LINE Controller Bot

> สถานะ: แผนงาน v2 — ปรับตามข้อมูลธุรกิจ (29 ก.ย. 2026) ยังไม่เริ่มเขียนโค้ด
> ข้อความทั้งหมดที่บอทใช้อยู่ใน [MESSAGES.md](MESSAGES.md)

## 1. โจทย์

- เราเป็น **โรงงานลูกชิ้น** ลูกค้าเป้าหมายส่วนใหญ่คือ **ร้านก๋วยเตี๋ยวเปิดใหม่**
- หารายชื่อร้านใหม่ **เดือนละ 1 รอบ** หาเจอเท่าไรทักทั้งหมด
- เบอร์ร้านเก็บอยู่ใน **Neon** (PostgreSQL)
- มีมือถือแล้ว และต้องการใช้ **LINE OA**

สิ่งที่บอทต้องทำ:

1. นำเบอร์ร้านใหม่ของเดือน → เพิ่มเพื่อน LINE ด้วยเบอร์ → ส่งข้อความแนะนำตัว
2. บันทึกว่าร้านไหนตอบแล้ว (สนใจ / ปฏิเสธ / ถามเรื่องอื่น)
3. ตอบกลับอัตโนมัติ และส่งต่อให้ฝ่ายขายเมื่อบอทตอบเองไม่ได้

## 2. ข้อจำกัดสำคัญ

| เรื่อง | รายละเอียด | ผลต่อการออกแบบ |
|---|---|---|
| **LINE OA เพิ่มเพื่อนจากเบอร์โทรไม่ได้** | OA ทักใครก่อนไม่ได้ ต้องให้ลูกค้าเพิ่มเพื่อนหรือทักเข้ามาก่อน การค้นหาด้วยเบอร์ทำได้เฉพาะบัญชี LINE ปกติ | ต้องมีบัญชี LINE ปกติ 1 บัญชีไว้ "ทักครั้งแรก" (ดูข้อ 3) |
| LINE Official Notification | ส่งถึงเบอร์โทรได้โดยไม่ต้องเป็นเพื่อน แต่ห้ามเนื้อหาการตลาด | ใช้ชวนเป็นลูกค้าใหม่ไม่ได้ |
| ร้านต้องเปิด "ให้ผู้อื่นเพิ่มเพื่อน" | ค้นหาด้วยเบอร์จะเจอเฉพาะคนที่เปิดการตั้งค่านี้ | มีสถานะ `NOT_FOUND`; ร้านที่ค้นไม่เจอใช้ SMS แนบลิงก์ OA หรือโทรแทน |
| LINE จำกัดการค้นหา | ค้นเกินจำนวน หรือค้นไม่เจอติดกันหลายครั้ง → ระงับการค้นหาชั่วคราว (LINE ไม่เปิดเผยตัวเลข) | โควตาต่อวัน, คัดเบอร์เสียทิ้งก่อน, พักเครื่องเมื่อค้นไม่เจอติดกัน, หยุดทันทีเมื่อมีข้อความเตือน |
| เพื่อนสูงสุด 5,000 คนต่อบัญชี LINE ปกติ | นับรวมคนที่ซ่อน/บล็อก (OA ไม่มีเพดานนี้) | ย้ายร้านที่สนใจไปคุยต่อใน OA |
| เงื่อนไขการใช้งาน LINE | บัญชีปกติไม่ได้มีไว้ทักลูกค้าจำนวนมาก และ LINE ไม่รองรับการควบคุมแอปด้วยโปรแกรม → อาจถูกจำกัด/ระงับ | ปริมาณต่ำ, ข้อความสุภาพ ระบุตัวตน มีทางปฏิเสธ, ข้อมูลลูกค้าเก็บใน OA + Neon ไม่ผูกกับบัญชีนี้ |
| PDPA | เบอร์ร้านมักเป็นเบอร์ส่วนตัวของเจ้าของร้าน | บอกที่มาของเบอร์และทางปฏิเสธในข้อความแรก, มีรายชื่อห้ามติดต่อ |
| UI ของ LINE เปลี่ยนตามเวอร์ชัน | บอทที่อิงหน้าจออาจพังเมื่อ LINE อัปเดต | ปิด auto-update, เก็บ selector ในไฟล์ config, เก็บ screenshot ทุกครั้งที่ error |

## 3. แนวทาง: ใช้ 2 บัญชีคู่กัน

| | บัญชี LINE ปกติ (บนมือถือ บอทควบคุม) | LINE OA + Messaging API |
|---|---|---|
| หน้าที่ | ทักครั้งแรก: ค้นเบอร์ → เพิ่มเพื่อน → แนะนำตัว → ตอบสั้น ๆ ตามคำตอบ | คุยจริง: ใบราคา, ขอตัวอย่าง, สั่งซื้อ, โปรโมชันรายเดือน |
| ตอบอัตโนมัติ | บอทพิมพ์ตอบบนมือถือ เป็นข้อความสำเร็จรูป ไม่เกิน 2 ครั้งต่อร้าน | ผ่าน API ตอบทันที มีปุ่มเมนูและรูปใบราคา **ข้อความตอบกลับไม่นับโควตา** |
| ติดตามการตอบ | อ่าน notification + สแกนรายการแชท | webhook บันทึกทุกข้อความลง Neon แม่นยำ |
| คนตอบเอง | LINE PC ที่ล็อกอินบัญชีเดียวกัน | LINE OA Manager (เว็บ) หรือแอป LINE OA ได้หลายแอดมิน |
| ความเสี่ยง | อาจถูกจำกัด/ระงับ | ช่องทางทางการ ไม่เสี่ยง |

เหตุผล: บัญชีปกติจำเป็นเฉพาะตอน "ทักครั้งแรก" เพราะ OA ทำไม่ได้ ส่วนการคุยต่อ การตอบอัตโนมัติ และการเก็บลูกค้าระยะยาว
ทำใน OA ดีกว่าทุกด้าน และถ้าบัญชีปกติมีปัญหา ร้านที่ย้ายไป OA แล้วจะไม่หายไปด้วย

> ⚠️ มือถือเครื่องบอท **ห้ามคนใช้ระหว่างบอททำงาน** (หน้าจอเดียวใช้ได้ทีละคน) — ฝ่ายขายใช้ LINE PC / LINE OA Manager บนคอมพิวเตอร์แทน

ทางเลือกอื่น (ไม่แนะนำเป็นหลัก):

- **บัญชีปกติอย่างเดียว** ทำทุกอย่างบนมือถือ — ง่ายสุด แต่บทสนทนาทั้งหมดผูกกับบัญชีที่มีความเสี่ยง และการตอบอัตโนมัติผ่านหน้าจอทำได้จำกัด
- **OA อย่างเดียว + SMS** ส่ง SMS แนบลิงก์ OA — ไม่เสี่ยงเลยและส่งได้ทั้งรอบในวันเดียว แต่คนไทยระวังลิงก์ใน SMS มาก อัตราตอบน่าจะต่ำกว่า
  และมีค่า SMS → ใช้เป็นช่องทางเสริมสำหรับร้านที่ค้นเบอร์ใน LINE ไม่เจอ

## 4. ภาพรวมระบบ

```text
Neon (PostgreSQL) — สถานะของทุกร้านอยู่ที่นี่ที่เดียว
 ├─ Phone bot (Python บน PC ที่ต่อมือถือ)
 │    └─ USB/ADB + uiautomator2 → Android → LINE บัญชีปกติ
 │         ค้นเบอร์ → เพิ่มเพื่อน → เปลี่ยนชื่อเป็นรหัสร้าน → ส่งข้อความ → อ่านคำตอบ → ตอบสั้น ๆ
 ├─ OA webhook service (Python บนคลาวด์)
 │    └─ LINE OA + Messaging API
 │         รับรหัสจากลิงก์ → ผูกร้าน → ส่งใบราคา/ปุ่มเมนู → ส่งต่อแอดมิน
 └─ Dashboard (เว็บ)
      funnel รายเดือน, รายการที่รอฝ่ายขาย, export CSV
```

### เครื่องมือ

| ส่วน | เลือกใช้ | เหตุผล |
|---|---|---|
| ภาษา | Python 3.11+ | ใช้ได้ทั้งบอทมือถือและ webhook |
| ควบคุมมือถือ | [uiautomator2](https://github.com/openatx/uiautomator2) (ทำงานบน ADB) | หาปุ่มจาก resource-id/ข้อความ, ใส่ข้อความภาษาไทยได้, มี watcher ปิด popup |
| หา selector | uiautodev | ดูโครงสร้างหน้าจอ LINE |
| ดูหน้าจอสด | scrcpy | เฝ้าดูมือถือจาก PC |
| ฐานข้อมูล | Neon + SQLAlchemy/psycopg | ใช้ฐานข้อมูลเดิม |
| LINE OA | Messaging API + line-bot-sdk (Python) + FastAPI | webhook และ Reply API |
| Dashboard | Streamlit หรือหน้าเว็บใน FastAPI | ดูสถานะ/ส่งออก CSV |
| แจ้งเตือนฝ่ายขาย | Telegram (ฟรี) หรือให้ OA push เข้ากลุ่ม LINE ของทีม (นับโควตาข้อความ) | LINE Notify ปิดบริการไปแล้วเมื่อ 31 มี.ค. 2025 |

> ห้ามใช้ `adb shell input text` ส่งข้อความ เพราะพิมพ์ภาษาไทยไม่ได้ — ใช้ `set_text()` ของ uiautomator2 (สำรอง: ADBKeyBoard)

## 5. ขั้นตอนการทำงาน

### 5.1 นำเข้ารอบเดือน

1. เมื่อรายชื่อร้านใหม่ของเดือนเข้า Neon แล้ว → สคริปต์สร้างแถว `line_outreach` สถานะ `PENDING` ให้ทุกร้านที่ยังไม่เคยทัก
2. Normalize เบอร์ (ตัด `-`/ช่องว่าง, `+66` → `0`) เก็บเฉพาะมือถือ 10 หลัก (`06`/`08`/`09`) ที่เหลือเป็น `INVALID`
3. เบอร์ที่เคยทักแล้วในเดือนก่อน ๆ หรืออยู่ในรายชื่อห้ามติดต่อ → ข้าม
4. สร้างรหัสประจำร้าน เช่น `C1023`
5. เรียงคิวให้ร้านที่เพิ่งเปิดล่าสุดได้ก่อน

ระยะเวลาทั้งรอบ ≈ จำนวนร้าน ÷ โควตาต่อวัน เช่น โควตา 15 ร้าน/วัน: 100 ร้าน ≈ 7 วัน, 300 ร้าน ≈ 20 วัน
ถ้ารอบไหนร้านเยอะจนทักไม่ทันในเดือน ส่วนที่เหลือส่ง SMS แนบลิงก์ OA แทน

### 5.2 ทักครั้งแรก (บัญชีปกติ, ต่อ 1 ร้าน)

1. กลับหน้า Home ของ LINE
2. เพิ่มเพื่อน → ค้นหา → หมายเลขโทรศัพท์ → ใส่เบอร์ → ค้นหา
3. อ่านผลลัพธ์:
   - พบ → กดเพิ่ม → `ADDED`
   - เป็นเพื่อนอยู่แล้ว → ข้ามไปขั้นทัก
   - ไม่พบ → `NOT_FOUND`
   - ถูกจำกัดการค้นหา → พักเครื่องถึงวันถัดไป + แจ้งเตือน (ร้านนี้กลับเป็น `PENDING`)
4. เปลี่ยนชื่อที่แสดงของเพื่อนเป็น `C1023 ชื่อร้าน` (เห็นเฉพาะฝั่งเรา) — ใช้จับคู่ notification/แชทกับร้านได้แน่นอน
5. ส่งข้อความแรก ([MESSAGES.md](MESSAGES.md) ข้อ A1 สุ่มแบบ A/B) → ตรวจว่าข้อความขึ้นจริง → `MESSAGED`
6. บันทึกทุกขั้นลง Neon ทันที (บอทล่มแล้วทำต่อได้ ไม่ทักซ้ำ) และเว้นระยะสุ่ม 1–3 นาทีก่อนร้านถัดไป
7. ช่วงเวลาทักร้านใหม่: 14:00–16:30 (เลี่ยงช่วงร้านยุ่งตอนเที่ยงและเย็น)

### 5.3 ตรวจคำตอบ (บัญชีปกติ)

| ชั้น | วิธี | ความถี่ |
|---|---|---|
| 1. Notification | อ่านการแจ้งเตือนของ LINE (ชื่อที่มีรหัสร้าน + ข้อความ) ผ่าน `adb shell dumpsys notification --noredact` หรือแอป MacroDroid ส่งเข้า webhook | ทันทีที่มีข้อความ |
| 2. สแกนรายการแชท | เปิดแท็บแชท อ่านชื่อ + ตัวเลขยังไม่อ่าน โดยไม่กดเข้าห้อง — เก็บตกตอน notification ไม่เด้ง | ทุก 15–30 นาที |
| 3. อ่านในห้องแชท | ก่อนตอบทุกครั้ง บอทอ่านข้อความเต็มของร้านในห้องแชท (notification อาจตัดข้อความ) | ทุกครั้งที่ตอบ |

ทุกข้อความที่จับได้ → บันทึกลง `line_messages` และอัปเดต `first_reply_at`, `last_reply_text`

### 5.4 ตอบอัตโนมัติ (บัญชีปกติ)

งานตอบกลับสำคัญกว่างานทักร้านใหม่ บอทจึงทำก่อนเสมอ และตอบเฉพาะในเวลาทำการ

1. เปิดห้องแชทของร้าน → อ่านข้อความล่าสุด
2. จัดกลุ่มตามกฎใน [MESSAGES.md](MESSAGES.md) ส่วน C (ตรวจกลุ่ม "ปฏิเสธ" ก่อนเสมอ เพราะ "ไม่สนใจ" มีคำว่า "สนใจ" อยู่ในตัว)
   - สนใจ / ถามราคา → A2 ส่งลิงก์ OA พร้อมรหัสร้าน → `INTERESTED`
   - ปฏิเสธ → A3 ขอบคุณ → `DECLINED` + ห้ามติดต่อ
   - ถามที่มาของเบอร์ / ไม่พอใจ → A4 ขออภัยและบอกที่มา → `DECLINED` + ห้ามติดต่อ + แจ้งเตือน
   - อื่น ๆ (รวมสติกเกอร์/รูป) → A5 รับเรื่อง → `REPLIED` + `needs_human` + แจ้งเตือนฝ่ายขาย
3. บอทตอบอัตโนมัติไม่เกิน 2 ครั้งต่อร้าน หลังจากนั้นทุกข้อความใหม่ → แจ้งเตือนฝ่ายขายอย่างเดียว
4. ไม่ตอบภายใน 3 วัน → ทักซ้ำ 1 ครั้ง (A6) → ยังไม่ตอบอีก 4 วัน → `NO_REPLY` จบ ไม่ทักอีก

### 5.5 LINE OA (Messaging API)

1. ลิงก์ในข้อความ A2 คือ `https://line.me/R/oaMessage/{LINE ID ของ OA แบบ percent-encode}/?C1023`
   กดแล้วแชท OA จะเปิดขึ้นพร้อมรหัสร้านในช่องพิมพ์ ร้านแค่กดส่ง
2. Webhook รับข้อความ → เจอรหัส → ผูก LINE userId กับร้าน → `JOINED_OA` → ตอบ B1 (ต้อนรับ + รูปใบราคา + ปุ่มตอบด่วน)
3. ปุ่มตอบด่วน: ขอตัวอย่างฟรี (B2) / พื้นที่จัดส่ง (B3) / คุยกับเจ้าหน้าที่ (B4 → หยุดบอทสำหรับร้านนั้น + แจ้งเตือน)
4. ร้านที่เพิ่มเพื่อน OA เองโดยไม่มีรหัส → B5 ขอชื่อร้าน แล้วแอดมินจับคู่ใน dashboard
5. ตรวจลายเซ็น `X-Line-Signature` ทุก request และบันทึกทุกข้อความเข้า/ออกลง `line_messages`
6. ระหว่างที่ webhook ยังไม่เสร็จ ใช้ฟีเจอร์ที่ไม่ต้องเขียนโค้ดของ LINE OA Manager ไปก่อนได้ (ข้อความทักทาย, ตอบกลับตามคำสำคัญ, ริชเมนู)
7. Deploy บนบริการคลาวด์ขนาดเล็ก (เช่น Google Cloud Run หรือ Vercel) ที่มี HTTPS และต่อ Neon ผ่าน pooled connection

### 5.6 Dashboard

- Funnel ต่อรอบเดือน: รายชื่อ → เพิ่มเพื่อนได้ → ส่งแล้ว → ตอบ → สนใจ → เข้า OA → เป็นลูกค้า (ฝ่ายขายกดยืนยันเอง)
- เทียบอัตราการตอบของข้อความแบบ A และ B
- รายการ `needs_human` ที่รอฝ่ายขาย
- สถานะเครื่องและโควตาวันนี้, export CSV

### 5.7 ความปลอดภัยของบัญชีและความทนทาน

- โควตาเริ่มต่ำ (10–20 ร้าน/วัน) แล้วค่อยปรับจากผลจริง, เว้นระยะแบบสุ่ม
- ค้นไม่เจอติดกันหลายครั้ง (ค่าเริ่มต้น 5) → พักเครื่อง 1 ชั่วโมง
- เจอ popup เตือนหรือถูกจำกัด → หยุดเครื่องทันที + แจ้งเตือน
- selector อยู่ใน `config/selectors.yaml`; ลำดับการหา: resource-id → ข้อความ → รูปภาพ/OCR
- error → เก็บ screenshot + UI dump + log; recovery: กด Back จนถึง Home → force-stop แล้วเปิด LINE ใหม่
- health check ก่อนเริ่มงาน: ADB เชื่อมต่อ, จอติด, LINE ล็อกอินอยู่, แบตเตอรี่พอ

## 6. Neon

- Neon คือ PostgreSQL ใช้ schema ในข้อ 7 ได้ทันที — สร้างตารางโดยวาง SQL ใน SQL Editor ของ Neon Console
- Webhook บนคลาวด์ใช้ connection string แบบ pooled (hostname มี `-pooler` และต่อท้าย `sslmode=require`)
- Neon หยุด compute เมื่อว่าง 5 นาที (แพ็กเกจ Free/Launch) การเชื่อมต่อแรกหลังจากนั้นจะช้าขึ้นครึ่งวินาทีถึงไม่กี่วินาที → เปิด `pool_pre_ping` และ retry
- สร้าง branch `dev` ใน Neon ไว้ทดสอบ ไม่แตะข้อมูลจริง
- เก็บ connection string ในตัวแปร `DATABASE_URL` (ไฟล์ `.env` ที่ไม่ commit) — ห้ามใส่ในโค้ดหรือส่งในแชท

## 7. ฐานข้อมูล (ตารางใหม่ใน Neon)

```sql
-- สถานะการทักของแต่ละเบอร์ (1 เบอร์ ทักครั้งเดียว)
CREATE TABLE line_outreach (
    id               BIGSERIAL    PRIMARY KEY,
    prospect_id      BIGINT       NOT NULL,           -- id ในตารางร้านค้าเดิม
    batch_month      DATE         NOT NULL,           -- รอบเดือน เช่น 2026-10-01
    phone            VARCHAR(15)  NOT NULL UNIQUE,    -- normalize แล้ว เช่น 0812345678
    code             VARCHAR(12)  NOT NULL UNIQUE,    -- เช่น C1023 (ชื่อเพื่อน + รหัสใน OA)
    device_id        VARCHAR(64),                     -- เครื่อง/บัญชีที่ทัก
    line_name        VARCHAR(100),                    -- ชื่อโปรไฟล์ LINE ตอนค้นเจอ
    message_variant  VARCHAR(8),                      -- ข้อความแรกแบบ A หรือ B
    status           VARCHAR(20)  NOT NULL DEFAULT 'PENDING',
    auto_replies     SMALLINT     NOT NULL DEFAULT 0, -- จำนวนครั้งที่บอทตอบอัตโนมัติ
    needs_human      BOOLEAN      NOT NULL DEFAULT false,
    do_not_contact   BOOLEAN      NOT NULL DEFAULT false,
    oa_user_id       VARCHAR(64)  UNIQUE,             -- LINE userId เมื่อเข้ามาใน OA
    attempts         SMALLINT     NOT NULL DEFAULT 0,
    last_error       TEXT,
    added_at         TIMESTAMPTZ,
    messaged_at      TIMESTAMPTZ,
    followed_up_at   TIMESTAMPTZ,
    first_reply_at   TIMESTAMPTZ,
    last_reply_at    TIMESTAMPTZ,
    last_reply_text  TEXT,
    joined_oa_at     TIMESTAMPTZ,
    updated_at       TIMESTAMPTZ  NOT NULL DEFAULT now()
);
-- status: PENDING | INVALID | NOT_FOUND | ADDED | MESSAGED | FOLLOWED_UP | REPLIED
--         | INTERESTED | DECLINED | NO_REPLY | JOINED_OA | CUSTOMER | ERROR

-- ประวัติข้อความเข้า/ออกทั้ง 2 ช่องทาง
CREATE TABLE line_messages (
    id           BIGSERIAL    PRIMARY KEY,
    outreach_id  BIGINT       REFERENCES line_outreach(id),  -- NULL ได้ถ้ายังจับคู่ร้านไม่ได้
    channel      VARCHAR(10)  NOT NULL,   -- 'line_app' | 'line_oa'
    direction    VARCHAR(3)   NOT NULL,   -- 'in' | 'out'
    sender       VARCHAR(10)  NOT NULL,   -- 'shop' | 'bot' | 'staff'
    text         TEXT,
    source       VARCHAR(20)  NOT NULL,   -- 'notification' | 'chat_scan' | 'chat_read' | 'webhook' | 'bot'
    oa_user_id   VARCHAR(64),             -- ข้อความ OA ที่ยังไม่ผูกกับร้าน
    created_at   TIMESTAMPTZ  NOT NULL DEFAULT now()
);

-- เครื่อง/บัญชี LINE ที่บอทใช้
CREATE TABLE line_devices (
    device_id     VARCHAR(64)  PRIMARY KEY,   -- ADB serial
    line_account  VARCHAR(100),
    daily_quota   SMALLINT     NOT NULL DEFAULT 15,
    status        VARCHAR(20)  NOT NULL DEFAULT 'ACTIVE',  -- ACTIVE | PAUSED | RESTRICTED
    paused_until  TIMESTAMPTZ,
    last_seen_at  TIMESTAMPTZ
);
```

## 8. โครงสร้างโปรเจกต์ (เสนอ)

```text
line-controller-bot/
├── config/
│   ├── settings.yaml        # เวลาทำงาน, โควตา, ข้อมูลโรงงาน (ชื่อ, LINE ID ของ OA, พื้นที่ส่ง)
│   ├── messages.yaml        # ข้อความทั้งหมด (จาก docs/MESSAGES.md)
│   ├── reply_rules.yaml     # คำสำคัญสำหรับจัดกลุ่มคำตอบ
│   └── selectors.yaml       # selector UI ของ LINE
├── db/
│   └── schema.sql           # ตารางใหม่ใน Neon
├── common/                  # เชื่อมต่อ DB, normalize เบอร์, เติมข้อความ, แจ้งเตือน
├── phone_bot/               # รันบน PC ที่ต่อมือถือ
│   ├── device.py            # เชื่อมต่อ, health check, recovery
│   ├── flows/               # add_friend, rename_friend, send_message, read_chat, scan_chats
│   ├── notifications.py
│   ├── classifier.py        # จัดกลุ่มคำตอบ
│   └── worker.py            # คิว + โควตา + เวลาทำงาน
├── oa_webhook/              # รันบนคลาวด์
│   └── app.py               # FastAPI + line-bot-sdk
├── dashboard/
├── scripts/                 # import_batch.py, dump_screen.py ฯลฯ
└── tests/
```

## 9. ลำดับการพัฒนา

| Phase | งาน | เกณฑ์ผ่าน |
|---|---|---|
| 0. เตรียม (เจ้าของ) | บัญชี LINE ปกติ + LINE OA, ทดลองด้วยมือ, เตรียมข้อมูลสินค้า (ข้อ 10) | checklist ครบ |
| 1. ฐานข้อมูล + PoC มือถือ | ตารางใน Neon, สคริปต์นำเข้ารอบเดือน, ทักเบอร์ทดสอบ 1 เบอร์ครบ flow | ทักเบอร์ของทีมได้จริง |
| 2. คิว + โควตา | worker รายวัน, เวลาทำงาน, retry, screenshot, แจ้งเตือน | รันทั้งวันได้ ไม่ทักซ้ำ |
| 3. ตรวจคำตอบ + ตอบอัตโนมัติบนมือถือ | notification, สแกนแชท, จัดกลุ่มคำตอบ, ข้อความ A2–A6 | ตอบถูกกลุ่มภายในไม่กี่นาที |
| 4. OA webhook (ทำคู่ขนานกับ 1–3 ได้) | รับรหัส, ผูกร้าน, ข้อความ B1–B5, deploy | กดลิงก์ → `JOINED_OA` + ได้ใบราคา |
| 5. Dashboard | funnel รายเดือน, เทียบ A/B, รายการรอฝ่ายขาย | ฝ่ายขายใช้เองได้ |
| 6. เสริม | AI ช่วยจัดกลุ่ม/ตอบคำถามทั่วไป, SMS สำหรับร้านที่ค้นไม่เจอ | — |

ทดสอบกับเบอร์ของทีมก่อนทุกเฟส แล้วค่อยใช้กับร้านจริงทีละน้อย

## 10. Checklist Phase 0

- [ ] ยืนยันว่ามือถือเป็น Android (ยี่ห้อ/รุ่น/เวอร์ชัน Android)
- [ ] สมัคร LINE ปกติด้วยเบอร์ที่ยังไม่ผูกกับ LINE ของใคร ตั้งชื่อ เช่น "ฝ่ายขาย ลูกชิ้น{ชื่อโรงงาน}" + รูปโลโก้/สินค้า
- [ ] สร้าง LINE OA (หรือใช้ที่มีอยู่) → เปิด Messaging API (ได้ Channel secret + Channel access token) → จด LINE ID ของ OA
- [ ] ตั้งค่ามือถือ: USB debugging (Xiaomi เปิด *USB debugging (Security settings)* ด้วย), ไม่ล็อกจอ, Stay awake ขณะชาร์จ,
      ปิด battery optimization และ auto-update ของ LINE, เปิดแจ้งเตือน + แสดงตัวอย่างข้อความ, ปิด "เพิ่มเพื่อนอัตโนมัติ"
- [ ] ทดลองด้วยมือกับเบอร์ของทีม 5–10 เบอร์: รูปแบบเบอร์ที่ช่องค้นหารับ, หน้าจอแต่ละกรณี (เก็บ screenshot),
      เปลี่ยนชื่อเพื่อนได้ยาวสุดกี่ตัวอักษร, notification แสดงชื่อที่เราเปลี่ยนหรือไม่
- [ ] ทดลองลิงก์ `oaMessage` จากมือถือของทีม ว่าเปิดแชท OA พร้อมรหัสได้
- [ ] ถ้าล็อกอิน LINE PC ด้วย notification บนมือถือยังเด้งไหม
- [ ] ข้อมูลสินค้า: รายการลูกชิ้น, รูปใบราคา, ขั้นต่ำการสั่ง, พื้นที่และวันส่ง, เงื่อนไขตัวอย่างฟรี, เวลาทำการของฝ่ายขาย
- [ ] โครงสร้างตารางร้านค้าใน Neon (ชื่อตาราง + คอลัมน์ ไม่ต้องส่งข้อมูลจริง) และแหล่งที่มาของรายชื่อร้าน

## อ้างอิง

- [LINE Help Center — I can't add friends](https://help.line.me/line/smartphone/?contentId=20000372) (การค้นหาด้วยเบอร์และการจำกัดการค้นหา)
- [LINE Help Center — Maximum number of friends](https://help.line.me/line/smartphone?lang=en&contentId=20023716&country=TW)
- [LINE Developers — LINE URL scheme](https://developers.line.biz/en/docs/messaging-api/using-line-url-scheme/) (ลิงก์ `oaMessage`)
- [LINE Developers — Messaging API pricing](https://developers.line.biz/en/docs/messaging-api/pricing/)
- [LINE Developers — LINE notification messages overview](https://developers.line.biz/en/docs/partner-docs/line-notification-messages/overview/)
- [Infobip — Compliance and guidelines for LINE](https://www.infobip.com/docs/line/compliance-guidelines) (LINE Official Notification ในไทย)
- [Neon — Connection pooling](https://neon.com/docs/connect/connection-pooling)
- [openatx/uiautomator2](https://github.com/openatx/uiautomator2)
