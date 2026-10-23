"""Command line entry point: python -m bot <command> (run from the project folder)."""

import argparse
import sys

from .config import database_url, device_serial, load_settings, load_yaml, telegram, test_phones
from .importer import plan_import, rows_to_store, summarize
from .phone import normalize_phone
from .safety import blocked_reason, test_mode
from .templates import TemplateError, friend_label, load_messages, render, shop_display_name

_OUTCOME_LABELS = {
    "PENDING": "พร้อมทัก",
    "INVALID:no_phone": "ไม่มีเบอร์ (ไม่บันทึก — รอบหน้าถ้ามีเบอร์จะดึงใหม่)",
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
        added = db.insert_outreach(conn, rows_to_store(rows))
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
    reason = blocked_reason(phone.phone, settings, test_phones())
    if reason:
        print(reason)
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
        app.open_chat()
        if args.rename:
            app.rename_friend(friend_label(args.code, args.shop, settings["device"]["friend_label_max_len"]))
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


def cmd_test_add(args: argparse.Namespace) -> int:
    from . import db
    from .control import add_test_shop

    with db.connect(database_url()) as conn:
        try:
            code = add_test_shop(conn, args.phone, args.name, test_phones())
        except ValueError as exc:
            print(exc)
            return 1
    print(f"เพิ่มร้านทดสอบ {code} \"{args.name}\" เบอร์ {normalize_phone(args.phone).phone} เข้าคิวแล้ว (สถานะ PENDING)")
    return 0


def cmd_test_clear(args: argparse.Namespace) -> int:
    from . import db

    with db.connect(database_url()) as conn:
        removed = db.clear_test_shops(conn)
    print(f"ลบร้านทดสอบออกจากคิวแล้ว {removed} ร้าน")
    return 0


def _worker_log():
    from datetime import datetime

    from .device import LOG_DIR

    LOG_DIR.mkdir(parents=True, exist_ok=True)
    path = LOG_DIR / "worker.log"

    def log(message: str) -> None:
        line = f"{datetime.now():%Y-%m-%d %H:%M:%S}  {message}"
        print(line)
        with open(path, "a", encoding="utf-8") as f:
            f.write(line + "\n")

    return log


def cmd_run(args: argparse.Namespace) -> int:
    from . import db
    from .device import LOG_DIR, PhoneConnectionError, connect, device_errors, dump_screen
    from .line_app import LineApp
    from .worker import Worker, WorkerSettings

    settings = load_settings()
    allowed = None
    if test_mode(settings):
        allowed = test_phones()
        if not allowed:
            print("โหมดทดสอบเปิดอยู่แต่ยังไม่มี TEST_PHONES ในไฟล์ .env — บอทจะไม่ทักใครเลย")
            return 1
    config = WorkerSettings.from_config(settings)
    messages = load_messages()
    business = settings.get("business", {})
    try:
        render(messages[config.opener_id], shop="ร้านทดสอบ", factory=business.get("factory_name"),
               persona=business.get("persona_name"), source=business.get("source_text"))
    except TemplateError as exc:
        print(f"ข้อความ {config.opener_id}: {exc} (ใส่ค่าใน config/settings.yaml หัวข้อ business)")
        return 1

    auto_reply = bool(settings["outreach"].get("auto_pitch"))
    if auto_reply:
        problem = _reply_messages_problem(messages, business)
        if problem:
            print(f"เปิด auto_pitch ไว้ แต่{problem} (ใส่ค่าใน config/settings.yaml หัวข้อ business หรือปิด auto_pitch)")
            return 1

    from .status import StatusBoard, clear_stop_request, read_status, running, stop_requested

    current = read_status()
    if running(current):
        print(f"บอทอีกตัวกำลังทำงานอยู่ (เริ่มเมื่อ {current['started_at'][:16].replace('T', ' ')}) — หยุดตัวเดิมก่อน")
        return 1
    clear_stop_request()
    log = _worker_log()
    board = StatusBoard(mode="ทดสอบ" if allowed is not None else "ทักร้านจริง", once=args.once,
                        quota_rule=config.quota.describe(), outreach_hours=settings["outreach"]["outreach_hours"],
                        ignore_hours=args.now, auto_reply=auto_reply)
    reason = "หยุดกะทันหัน"
    try:
        d = connect(device_serial())
        device_id = device_serial() or d.serial
        board.update(device=device_id)
        app = LineApp(d, load_yaml("selectors.yaml"), settings["device"]["line_package"])
        with db.AutoReconnect(database_url()) as conn:
            db.apply_schema(conn)  # creates or updates the bot's tables; safe to run every start
            watcher = _reply_watcher(conn, app, d, settings, messages, allowed, log)
            worker = Worker(conn, app, config, messages, business, device_id, settings, allowed, log=log,
                            on_error=lambda item: dump_screen(d, f"worker-{item.code}", LOG_DIR), replies=watcher,
                            status=board, should_stop=stop_requested)
            log(f"เริ่มทำงาน: มือถือ {device_id}, {config.quota.describe()}"
                + (f", โหมดทดสอบ ({len(allowed)} เบอร์)" if allowed is not None else ", ทักร้านจริง")
                + (", ตอบกลับอัตโนมัติ" if auto_reply else ", ไม่ตอบอัตโนมัติ (แจ้งพนักงานอย่างเดียว)"))
            reason = worker.run(once=args.once, ignore_hours=args.now, max_shops=args.max)
    except PhoneConnectionError as exc:
        reason = "ต่อมือถือไม่ได้"
        log(f"หยุด: {exc}")
        return 1
    except device_errors() as exc:
        reason = f"มือถือมีปัญหา ({exc})"
        log(f"หยุด: {reason} — กดตรวจการเชื่อมต่อในหน้าเว็บ หรือรัน python -m bot doctor")
        return 1
    except KeyboardInterrupt:
        reason = "หยุดด้วย Ctrl+C"
        raise
    except Exception as exc:  # a crash: the dashboard must not keep showing a live bot
        reason = f"หยุดเพราะเกิดข้อผิดพลาด: {exc}"
        log(reason)
        raise
    finally:
        board.stopped(reason)
        clear_stop_request()
    log(f"หยุด: {reason}")
    return 0


def cmd_status(args: argparse.Namespace) -> int:
    from datetime import datetime

    from . import db
    from .quota import QuotaRule, describe_plan, today_plan
    from .reports import STATUS_LABELS

    settings = load_settings()
    allowed = test_phones() if test_mode(settings) else None
    rule = QuotaRule.from_config(settings["outreach"])
    with db.connect(database_url()) as conn:
        summary = db.queue_summary(conn)
        plan = today_plan(conn, rule, allowed, datetime.now().astimezone())
    if not summary:
        print("คิวว่าง")
        return 0
    for status, count in sorted(summary.items(), key=lambda item: -item[1]):
        print(f"  {STATUS_LABELS.get(status, status)} ({status}): {count}")
    print(describe_plan(plan, rule) + (" [โหมดทดสอบ: นับเฉพาะเบอร์ใน TEST_PHONES]" if allowed is not None else ""))
    return 0


def _reply_messages_problem(messages, business):
    for reply_id in ("B1", "B2", "B3", "B4", "B5", "B6"):
        try:
            render(messages[reply_id], shop="ร้านทดสอบ", factory=business.get("factory_name"),
                   persona=business.get("persona_name"), source=business.get("source_text"))
        except TemplateError as exc:
            return f"ข้อความ {reply_id}: {exc}"
    return None


def _reply_watcher(conn, app, device, settings, messages, allowed, log):
    from datetime import datetime

    from .classifier import ReplyClassifier
    from .alerts import make_alert
    from .replies import ReplyHandler, ReplyWatcher
    from .schedule import parse_window

    outreach = settings["outreach"]
    now = lambda: datetime.now().astimezone()  # noqa: E731
    handler = ReplyHandler(conn, ReplyClassifier.from_config(), bool(outreach.get("auto_pitch")),
                           make_alert(log, *telegram()), log, now)
    return ReplyWatcher(handler, app, device, messages, settings.get("business", {}), allowed,
                        parse_window(outreach["reply_hours"]), float(outreach.get("chat_scan_minutes", 20)), log, now,
                        int(outreach.get("no_reply_days", 7)))


def cmd_check_replies(args: argparse.Namespace) -> int:
    """One round of reply checking right now, ignoring reply hours: notifications, chat list, pending replies."""
    from . import db
    from .device import connect, device_errors
    from .line_app import LineApp

    from .status import read_status, running

    if running(read_status()):
        print("บอทกำลังทำงานอยู่และตรวจคำตอบเองอยู่แล้ว — ไม่ต้องสั่งซ้ำ (มือถือใช้ได้ทีละงาน)")
        return 1
    settings = load_settings()
    allowed = test_phones() if test_mode(settings) else None
    log = _worker_log()
    d = connect(device_serial())
    app = LineApp(d, load_yaml("selectors.yaml"), settings["device"]["line_package"])
    with db.AutoReconnect(database_url()) as conn:
        watcher = _reply_watcher(conn, app, d, settings, load_messages(), allowed, log)
        try:
            found = watcher.poll_notifications()
            log(f"จากการแจ้งเตือน: คำตอบใหม่ {found} ข้อความ")
            found = watcher.scan_chat_list()
            log(f"จากหน้ารายการแชท: คำตอบใหม่ {found} ข้อความ")
            if watcher.handler.auto_reply:
                log(f"บอทตอบกลับไป {watcher.send_pending()} ร้าน")
            app.leave()
        except device_errors() as exc:
            log(f"หยุด: มือถือมีปัญหา ({exc})")
            return 1
    return 0


def cmd_replies(args: argparse.Namespace) -> int:
    """Shops whose replies are waiting for a person."""
    from . import db

    with db.connect(database_url()) as conn:
        rows = db.waiting_for_staff(conn)
    if not rows:
        print("ไม่มีร้านที่รอพนักงานตอบ")
        return 0
    for code, name, status, text, at in rows:
        when = f"{at.astimezone():%d/%m %H:%M}" if at else "-"
        print(f"  {code:<6} {name}  [{status}]  {when}  \"{text or ''}\"")
    return 0


def cmd_dashboard(args: argparse.Namespace) -> int:
    import socket
    import subprocess

    from .config import ROOT, dashboard_password

    database_url()  # fail early with the usual message if .env is missing
    address = "127.0.0.1"
    if args.lan:
        if not dashboard_password():
            print("เปิดให้เครื่องอื่นเข้าได้ต้องตั้ง DASHBOARD_PASSWORD ในไฟล์ .env ก่อน")
            return 1
        address = "0.0.0.0"
        try:
            with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
                s.connect(("8.8.8.8", 80))  # no packet is sent; this only picks the LAN address
                print(f"เครื่องอื่นในวง Wi-Fi เดียวกันเปิดได้ที่ http://{s.getsockname()[0]}:{args.port}")
        except OSError:
            print("เครื่องอื่นในวง Wi-Fi เดียวกันเปิดได้ที่ http://<IP ของเครื่องนี้>:" + str(args.port))
    print(f"เปิด dashboard ที่ http://localhost:{args.port} (กด Ctrl+C เพื่อปิด)")
    command = [sys.executable, "-m", "streamlit", "run", str(ROOT / "dashboard" / "app.py"),
               "--server.address", address, "--server.port", str(args.port),
               "--browser.gatherUsageStats", "false", "--client.toolbarMode", "minimal"]
    return subprocess.call(command, cwd=ROOT)


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

    p = sub.add_parser("test-add", help="เพิ่มร้านทดสอบ (เบอร์ทีมใน TEST_PHONES) เข้าคิว")
    p.add_argument("--phone", required=True, help="เบอร์ทีมที่อยู่ใน TEST_PHONES")
    p.add_argument("--name", default="ร้านทดสอบ", help="ชื่อร้านสมมติ")
    p.set_defaults(func=cmd_test_add)

    sub.add_parser("test-clear", help="ลบร้านทดสอบทั้งหมดออกจากคิว").set_defaults(func=cmd_test_clear)

    p = sub.add_parser("run", help="ทักร้านจากคิวตามโควตาและเวลาทำการ (ทำงานต่อเนื่อง)")
    p.add_argument("--once", action="store_true", help="ทำแค่ 1 ร้านแล้วหยุด")
    p.add_argument("--now", action="store_true", help="ไม่สนช่วงเวลาทักร้าน (ใช้ตอนทดสอบ)")
    p.add_argument("--max", type=int, help="หยุดเมื่อทำครบกี่ร้าน")
    p.set_defaults(func=cmd_run)

    sub.add_parser("status", help="ดูจำนวนร้านในคิวแยกตามสถานะ").set_defaults(func=cmd_status)
    sub.add_parser("replies", help="ดูร้านที่ตอบแล้วและรอพนักงานคุยต่อ").set_defaults(func=cmd_replies)
    sub.add_parser("check-replies", help="ตรวจคำตอบของร้านตอนนี้เลย 1 รอบ").set_defaults(func=cmd_check_replies)

    p = sub.add_parser("dashboard", help="เปิดหน้า dashboard สำหรับพนักงานในเบราว์เซอร์")
    p.add_argument("--port", type=int, default=8501)
    p.add_argument("--lan", action="store_true", help="ให้เครื่องอื่นในวง Wi-Fi เปิดได้ (ต้องตั้ง DASHBOARD_PASSWORD)")
    p.set_defaults(func=cmd_dashboard)

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
    if test_mode(load_settings()):
        print("[โหมดทดสอบ] ทักได้เฉพาะเบอร์ใน TEST_PHONES")
    from .device import PhoneConnectionError

    try:
        return args.func(args)
    except PhoneConnectionError as exc:
        print(exc)
        return 1
    except ModuleNotFoundError as exc:
        print(f"ไม่พบไลบรารี {exc.name} — ยังไม่ได้เปิด .venv หรือยังไม่ได้ติดตั้ง:\n"
              "  .venv\\Scripts\\activate   (Windows)   หรือ   source .venv/bin/activate   (macOS)\n"
              "  pip install -r requirements.txt")
        return 1
    except KeyboardInterrupt:
        print("\nหยุดแล้ว")
        return 130


if __name__ == "__main__":
    sys.exit(main())
