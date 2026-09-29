"""Command line entry point: python -m bot <command> (run from the project folder)."""

import argparse
import sys

from .config import database_url, device_serial, load_settings, load_yaml
from .importer import plan_import, summarize
from .phone import normalize_phone
from .templates import TemplateError, friend_label, load_messages, render, shop_display_name

_OUTCOME_LABELS = {
    "PENDING": "พร้อมทัก",
    "INVALID:no_phone": "ไม่มีเบอร์",
    "INVALID:landline": "เบอร์บ้าน/สำนักงาน (ค้นใน LINE ไม่ได้)",
    "INVALID:bad_format": "เบอร์ผิดรูปแบบ",
    "INVALID:duplicate": "เบอร์ซ้ำกับร้านที่มีอยู่แล้ว",
}


def cmd_doctor(args: argparse.Namespace) -> int:
    from .device import run_doctor

    settings = load_settings()
    checks = run_doctor(device_serial(), settings["device"]["line_package"])
    for check in checks:
        mark = "OK" if check.ok else ("!!" if check.required else "--")
        print(f"[{mark}] {check.label}" + (f" — {check.detail}" if check.detail else ""))
    return 0 if all(c.ok for c in checks if c.required) else 1


def cmd_dump(args: argparse.Namespace) -> int:
    from .device import connect, device_errors, dump_screen

    try:
        base = dump_screen(connect(device_serial()), args.label)
    except device_errors() as exc:
        print(f"บันทึกหน้าจอไม่ได้: {exc}")
        return 1
    print(f"บันทึกแล้ว: {base}.png / .xml / .txt")
    print("ส่งไฟล์ .txt ให้ผู้พัฒนาเพื่อเติม selector (ไฟล์อาจมีชื่อหรือข้อความที่อยู่บนหน้าจอ)")
    return 0


def cmd_init_db(args: argparse.Namespace) -> int:
    from . import db

    url = database_url()
    with db.connect(url) as conn:
        db.apply_schema(conn)
    print(f"สร้าง/ตรวจตารางใน schema line_bot เรียบร้อย ({db.describe(url)})")
    return 0


def cmd_import(args: argparse.Namespace) -> int:
    from . import db

    url = database_url()
    with db.connect(url) as conn:
        if not db.schema_exists(conn):
            print("ยังไม่มีตารางของบอท — รัน python -m bot init-db ก่อน")
            return 1
        shops = db.fetch_new_shops(conn, load_settings().get("import", {}))
        rows = plan_import(shops, db.fetch_existing_phones(conn))

        print(f"ฐานข้อมูล: {db.describe(url)}")
        print(f"ร้านที่ยังไม่เคยนำเข้า: {len(rows)} ร้าน")
        if not rows:
            return 0
        for outcome, count in sorted(summarize(rows).items(), key=lambda item: (item[0] != "PENDING", item[0])):
            print(f"  {_OUTCOME_LABELS.get(outcome, outcome)}: {count}")
        for row in rows[:15]:
            print(f"  {row.status:<8} {row.phone or '-':<11} {row.shop_name}")
        if len(rows) > 15:
            print(f"  ... และอีก {len(rows) - 15} ร้าน")

        if not args.commit:
            conn.rollback()
            print("ยังไม่ได้บันทึก — ถ้าถูกต้องแล้วให้รันอีกครั้งพร้อม --commit")
            return 0
        added = db.insert_outreach(conn, rows)
        conn.commit()
    print(f"บันทึกเข้าคิวแล้ว {added} ร้าน")
    return 0


def cmd_poc(args: argparse.Namespace) -> int:
    from .device import LOG_DIR, connect, device_errors, dump_screen
    from .line_app import LineApp, LineUIError, SearchResult

    settings = load_settings()
    phone = normalize_phone(args.phone)
    if phone.phone is None:
        print(f"เบอร์ {args.phone!r} ใช้ค้นใน LINE ไม่ได้ ({phone.reason})")
        return 1

    business = settings.get("business", {})
    opener_id = settings["outreach"]["opener"]
    try:
        text = render(load_messages()[opener_id], shop=shop_display_name(args.shop),
                      factory=business.get("factory_name"), persona=business.get("persona_name"),
                      source=business.get("source_text"))
    except TemplateError as exc:
        print(f"ข้อความ {opener_id}: {exc} (ใส่ค่าใน config/settings.yaml หัวข้อ business)")
        return 1
    print(f"ข้อความที่จะใช้ ({opener_id}):\n{text}\n")

    if args.step:
        def before_action(step: str) -> None:
            input(f"-> {step}  [Enter = ทำต่อ, Ctrl+C = หยุด] ")
    else:
        def before_action(step: str) -> None:
            print(f"-> {step}")

    d = connect(device_serial())
    app = LineApp(d, load_yaml("selectors.yaml"), settings["device"]["line_package"], before_action=before_action)
    try:
        app.go_home()
        result = app.search_phone(phone.phone)
        print(f"ผลการค้นหา: {result.value}")
        if result == SearchResult.LIMITED:
            print("LINE จำกัดการค้นหาชั่วคราว — หยุดค้นหาจนถึงวันพรุ่งนี้")
            return 1
        if result in (SearchResult.NOT_FOUND, SearchResult.UNKNOWN):
            base = dump_screen(d, f"poc-{result.value.lower()}", LOG_DIR)
            print(f"เก็บหน้าจอไว้ที่ {base}.png / .txt")
            return 0 if result == SearchResult.NOT_FOUND else 1
        if result == SearchResult.CAN_ADD:
            app.add_friend()
        if args.rename:
            app.rename_friend(friend_label(args.code, args.shop, settings["device"]["friend_label_max_len"]))
        app.open_chat()
        if app.send_message(text, send=args.send):
            print("ส่งข้อความแล้ว")
        elif args.send:
            print("กดส่งแล้ว แต่ยืนยันไม่ได้ว่าข้อความออกไป — ตรวจที่หน้าจอ")
        else:
            print("พิมพ์ข้อความค้างไว้ในช่องแชท (ยังไม่ส่ง) — ใส่ --send เพื่อส่งจริง")
        return 0
    except LineUIError as exc:
        base = dump_screen(d, "poc-error", LOG_DIR)
        print(f"หยุด: {exc}\nเก็บหน้าจอไว้ที่ {base}.png / .txt")
        return 1
    except device_errors() as exc:
        print(f"มือถือมีปัญหาระหว่างทำงาน: {exc} — รัน python -m bot doctor เพื่อตรวจ")
        return 1


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="python -m bot", description="บอททักร้านทาง LINE")
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("doctor", help="ตรวจการเชื่อมต่อมือถือและแอป LINE").set_defaults(func=cmd_doctor)

    p = sub.add_parser("dump", help="บันทึกภาพและโครงสร้างหน้าจอปัจจุบัน (ใช้หา selector)")
    p.add_argument("label", help="ชื่อหน้าจอ เช่น home, add-friend, search, result-found")
    p.set_defaults(func=cmd_dump)

    sub.add_parser("init-db", help="สร้างตารางของบอทใน Neon (รันซ้ำได้)").set_defaults(func=cmd_init_db)

    p = sub.add_parser("import", help="ดึงร้านใหม่จากตาราง Shop เข้าคิว")
    p.add_argument("--commit", action="store_true", help="บันทึกจริง (ถ้าไม่ใส่ จะแสดงตัวอย่างเท่านั้น)")
    p.set_defaults(func=cmd_import)

    p = sub.add_parser("poc", help="ทดลองกับเบอร์ทดสอบ 1 เบอร์: ค้นหา → เพิ่มเพื่อน → พิมพ์ข้อความแรก")
    p.add_argument("--phone", required=True, help="เบอร์ทดสอบ (ใช้เบอร์ของทีมเท่านั้น)")
    p.add_argument("--shop", default="ร้านทดสอบ", help="ชื่อร้านที่ใช้ในข้อความ")
    p.add_argument("--code", default="C0", help="รหัสที่ใช้ตั้งชื่อเพื่อน")
    p.add_argument("--rename", action="store_true", help="ลองเปลี่ยนชื่อเพื่อนเป็นรหัส + ชื่อร้าน")
    p.add_argument("--send", action="store_true", help="กดส่งจริง (ถ้าไม่ใส่ จะพิมพ์ค้างไว้เฉย ๆ)")
    p.add_argument("--step", action="store_true", help="หยุดรอกด Enter ก่อนทุกขั้น")
    p.set_defaults(func=cmd_poc)
    return parser


def main(argv=None) -> int:
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(errors="replace")  # never crash on consoles that cannot show Thai
    args = build_parser().parse_args(argv)
    from .device import PhoneConnectionError

    try:
        return args.func(args)
    except PhoneConnectionError as exc:
        print(exc)
        return 1
    except KeyboardInterrupt:
        print("\nหยุดแล้ว")
        return 130


if __name__ == "__main__":
    sys.exit(main())
