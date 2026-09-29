# line-controller-bot

บอทสำหรับโรงงานลูกชิ้น ใช้ทักร้านก๋วยเตี๋ยวเปิดใหม่ทาง LINE จากเบอร์โทรในตาราง `"Shop"` บน Neon

- ควบคุมแอป LINE บนมือถือ Android (Honor 400 Pro) ผ่าน ADB + uiautomator2
- ทักด้วยคำถามสั้น ๆ ว่าร้านเปิดขายอยู่ไหม บันทึกว่าร้านไหนตอบแล้ว ตอบกลับอัตโนมัติ 1 ครั้ง แล้วส่งต่อให้พนักงาน
- สถานะทั้งหมดเก็บใน Neon (schema `line_bot`) โดยไม่แตะตารางเดิม

## คำสั่ง

รันในโฟลเดอร์โปรเจกต์ หลังติดตั้งตาม [docs/SETUP.md](docs/SETUP.md)

| คำสั่ง | ใช้ทำอะไร |
|---|---|
| `python -m bot doctor` | ตรวจการเชื่อมต่อคอมพิวเตอร์ → มือถือ → LINE |
| `python -m bot dump <ชื่อหน้าจอ>` | เก็บภาพและโครงสร้างหน้าจอ LINE ไว้หา selector |
| `python -m bot init-db` | สร้างตารางของบอทใน Neon (รันซ้ำได้) |
| `python -m bot import [--commit]` | ดึงร้านใหม่จาก `"Shop"` เข้าคิว (ไม่ใส่ `--commit` = ดูตัวอย่างเท่านั้น) |
| `python -m bot poc --phone 08xxxxxxxx [--step] [--rename] [--send]` | ทดลองค้นเบอร์ เพิ่มเพื่อน และพิมพ์ข้อความแรกกับเบอร์ทดสอบ |

## เอกสาร

- แผนการพัฒนา: [docs/PLAN.md](docs/PLAN.md)
- ติดตั้งและทดลอง: [docs/SETUP.md](docs/SETUP.md)
- ข้อความที่บอทใช้: [docs/MESSAGES.md](docs/MESSAGES.md)
