"""
اسکریپت مستقل مانیتور — توسط Cron هر ۵ دقیقه اجرا می‌شود.
قابل اجرا دستی: python monitor.py

ویژگی‌ها:
- قفل پردازشی غیرمسدودکننده (fcntl.flock) برای جلوگیری از تداخل اجرای همزمان Cron
- ایزوله‌سازی seen_ads به تفکیک هر مانیتور (monitor_id, token)
- اتصال کوتاه‌مدت به SQLite (عدم نگه‌داشتن قفل دیتابیس حین درخواست‌های شبکه)
- استخراج واقعی قیمت در پیگیری تغییر قیمت (check_price_changes)
- ثبت کامل اعلان‌های درون‌برنامه‌ای + ارسال Web Push / تلگرام / ایمیل
"""
from datetime import datetime, timedelta
import json
import os
import sys
import time
from typing import Optional

try:
    import fcntl
except ImportError:
    fcntl = None

# اطمینان از اینکه مسیر پروژه در sys.path هست (برای اجرای از cron با هر working directory)
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
if BASE_DIR not in sys.path:
    sys.path.insert(0, BASE_DIR)

from app import (
    get_db,
    get_post_details_cached,
    init_db,
    notify,
    parse_price,
    rental_equivalents,
    run_search_logic,
    save_notification,
)

# سقف بررسی تغییر قیمت در هر اجرا (جلوگیری از کند شدن و بلاک شدن توسط دیوار)
PRICE_CHECK_BATCH = 20
PRICE_CHECK_MAX_AGE_DAYS = 7
PRICE_CHECK_MIN_INTERVAL_MIN = 30
MAX_NOTIFY_PER_RUN = 12       # حداکثر آگهی در هر پیام ایمیل/تلگرام
MAX_SAVE_NOTIF_PER_RUN = 50   # حداکثر اعلان ذخیره‌شده در پنل در هر اجرا
RETENTION_DAYS = 14           # نگهداری تاریخچه و آگهی‌های دیده‌شده
CLEANUP_INTERVAL_HOURS = 6    # فاصله پاکسازی خودکار

LOCK_FILE_PATH = os.path.join(BASE_DIR, "monitor.lock")


def acquire_monitor_lock():
    """قفل انحصاری غیرمسدودکننده برای جلوگیری از اجرای همزمان چند پروسه‌ی monitor.py."""
    if fcntl is None:
        return None
    try:
        fh = open(LOCK_FILE_PATH, "w")
        fcntl.flock(fh.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        fh.write(f"{os.getpid()} {datetime.now().isoformat()}\n")
        fh.flush()
        return fh
    except (BlockingIOError, IOError, OSError):
        try:
            fh.close()
        except Exception:
            pass
        return False


def release_monitor_lock(lock_fh) -> None:
    if not lock_fh or isinstance(lock_fh, bool):
        return
    try:
        if fcntl is not None:
            fcntl.flock(lock_fh.fileno(), fcntl.LOCK_UN)
    except Exception:
        pass
    try:
        lock_fh.close()
    except Exception:
        pass


def is_user_sub_active(user_row) -> bool:
    if not user_row:
        return False
    if not user_row["is_active"]:
        return False
    if user_row["role"] == "admin":
        return True
    exp = user_row["subscription_expires_at"]
    if not exp:
        return False
    try:
        return datetime.fromisoformat(exp) > datetime.now()
    except Exception:
        return False


def extract_price_from_post(token: str, category: str = "") -> Optional[int]:
    """استخراج قیمت فعلی یک آگهی از جزئیات دیوار برای تشخیص تغییر قیمت."""
    token = str(token or "").strip()
    if not token:
        return None
    try:
        details = get_post_details_cached(token)
        if not details:
            return None
        if category == "apartment-rent":
            credit = details.get("credit_num")
            rent = details.get("rent_num")
            if credit is not None or rent is not None:
                eq = rental_equivalents(credit, rent)
                if eq.get("deposit_equivalent_num") is not None:
                    return int(eq["deposit_equivalent_num"])
        price_num = details.get("price_num")
        if price_num is not None and int(price_num) > 0:
            return int(price_num)
    except Exception as e:
        print(f"extract_price_from_post error ({token}):", e)
    return None


def check_price_changes(conn=None):
    """بررسی تغییر قیمت فقط برای آگهی‌های جدیدِ مانیتورهایی که track_price روشن دارند."""
    now = datetime.now()
    cutoff_found = (now - timedelta(days=PRICE_CHECK_MAX_AGE_DAYS)).isoformat()
    cutoff_checked = (now - timedelta(minutes=PRICE_CHECK_MIN_INTERVAL_MIN)).isoformat()

    own_conn = conn is None
    db = conn if conn is not None else get_db()
    try:
        rows = db.execute(
            """
            SELECT s.*, m.name AS monitor_name, m.category AS monitor_category,
                   m.user_id, u.telegram_chat_id, u.notify_email, u.role, u.is_active AS u_active, u.subscription_expires_at
            FROM seen_ads s
            JOIN monitors m ON m.id = s.monitor_id
            LEFT JOIN users u ON u.id = m.user_id
            WHERE m.is_active = 1
              AND m.track_price = 1
              AND s.price_num IS NOT NULL
              AND (s.found_at IS NULL OR s.found_at >= ?)
              AND (s.last_checked IS NULL OR s.last_checked <= ?)
            ORDER BY COALESCE(s.last_checked, '1970-01-01') ASC
            LIMIT ?
            """,
            (cutoff_found, cutoff_checked, PRICE_CHECK_BATCH),
        ).fetchall()
        candidates = [dict(r) for r in rows]
    finally:
        if own_conn:
            db.close()

    for r in candidates:
        u_Check = {
            "is_active": r["u_active"],
            "role": r["role"],
            "subscription_expires_at": r["subscription_expires_at"],
        }
        if r["user_id"] and not is_user_sub_active(u_Check):
            continue

        new_num = extract_price_from_post(r["token"], category=r.get("monitor_category") or "")
        now_iso = datetime.now().isoformat()

        db_write = conn if conn is not None else get_db()
        try:
            if new_num is None:
                db_write.execute(
                    "UPDATE seen_ads SET last_checked=? WHERE token=? AND monitor_id=?",
                    (now_iso, r["token"], r["monitor_id"]),
                )
                db_write.commit()
                time.sleep(0.2)
                continue

            if new_num != r["price_num"]:
                old_str = f"{r['price_num']:,} تومان"
                new_str = f"{new_num:,} تومان"
                diff = new_num - r["price_num"]
                arrow = "📉 کاهش" if diff < 0 else "📈 افزایش"
                pct = abs(diff) / r["price_num"] * 100 if r["price_num"] else 0
                db_write.execute(
                    "INSERT INTO price_changes (token, monitor_id, title, old_price, new_price, link, changed_at) VALUES (?,?,?,?,?,?,?)",
                    (r["token"], r["monitor_id"], r["title"], old_str, new_str, r["link"], now_iso),
                )
                db_write.execute(
                    "UPDATE seen_ads SET price=?, price_num=?, last_checked=? WHERE token=? AND monitor_id=?",
                    (new_str, new_num, now_iso, r["token"], r["monitor_id"]),
                )
                db_write.commit()
                msg = (
                    f"<b>{arrow} قیمت ({pct:.1f}%) — {r['monitor_name']}</b>\n"
                    f"{r['title']}\n"
                    f"قبلی: {old_str}\n"
                    f"جدید: <b>{new_str}</b>\n"
                    f"🔗 {r['link']}"
                )
                notify(
                    msg,
                    subject=f"تغییر قیمت: {r['title'][:40]}",
                    telegram_chat_id=r["telegram_chat_id"],
                    email=r["notify_email"],
                    user_id=r["user_id"],
                    link=r["link"],
                    monitor_id=r["monitor_id"],
                )
            else:
                db_write.execute(
                    "UPDATE seen_ads SET last_checked=? WHERE token=? AND monitor_id=?",
                    (now_iso, r["token"], r["monitor_id"]),
                )
                db_write.commit()
        finally:
            if own_conn:
                db_write.close()
        time.sleep(0.2)


def cleanup_old_records(conn):
    """پاکسازی دوره‌ای داده‌های قدیمی تا دیتابیس سبک بماند."""
    try:
        row = conn.execute("SELECT value FROM meta WHERE key='last_cleanup'").fetchone()
        if row and row["value"]:
            try:
                last_dt = datetime.fromisoformat(row["value"])
                if datetime.now() - last_dt < timedelta(hours=CLEANUP_INTERVAL_HOURS):
                    return
            except Exception:
                pass

        cutoff = (datetime.now() - timedelta(days=RETENTION_DAYS)).isoformat()
        c1 = conn.execute("DELETE FROM history WHERE found_at < ?", (cutoff,)).rowcount
        c2 = conn.execute("DELETE FROM price_changes WHERE changed_at < ?", (cutoff,)).rowcount
        c3 = conn.execute(
            """
            DELETE FROM seen_ads
            WHERE found_at < ?
              AND monitor_id NOT IN (SELECT id FROM monitors WHERE track_price=1 AND is_active=1)
            """,
            (cutoff,),
        ).rowcount
        seen_cutoff_tracked = (datetime.now() - timedelta(days=RETENTION_DAYS * 2)).isoformat()
        c4 = conn.execute("DELETE FROM seen_ads WHERE found_at < ?", (seen_cutoff_tracked,)).rowcount
        c5 = conn.execute("DELETE FROM notifications WHERE created_at < ?", (cutoff,)).rowcount

        conn.execute(
            "INSERT INTO meta (key, value) VALUES ('last_cleanup', ?) "
            "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
            (datetime.now().isoformat(),),
        )
        conn.commit()
        try:
            conn.execute("PRAGMA optimize")
        except Exception:
            pass
        print(f"[cleanup] removed history={c1}, price_changes={c2}, seen={c3+c4}, notifications={c5}")
    except Exception as e:
        print("cleanup error:", e)


def run_all_monitors():
    t0 = time.time()
    stats = {"monitors": 0, "ads_checked": 0, "new_ads": 0, "errors": 0, "skipped_locked": False}

    lock_fh = acquire_monitor_lock()
    if lock_fh is False:
        print("[monitor] another monitor.py process is already running — skipping overlapping run.")
        stats["skipped_locked"] = True
        return stats

    try:
        init_db()
        conn = get_db()
        try:
            monitors = [
                dict(m)
                for m in conn.execute("SELECT * FROM monitors WHERE is_active=1").fetchall()
            ]
        finally:
            conn.close()

        for m in monitors:
            conn_u = get_db()
            try:
                user = (
                    dict(conn_u.execute("SELECT * FROM users WHERE id=?", (m["user_id"],)).fetchone() or {})
                    if m["user_id"] else None
                )
            finally:
                conn_u.close()

            if m["user_id"] and not is_user_sub_active(user):
                print(f"[skip] monitor #{m['id']} ({m['name']}): user #{m['user_id']} subscription inactive")
                continue

            stats["monitors"] += 1
            try:
                settings = json.loads(m["settings_json"])
                settings["category"] = m["category"]
                res = run_search_logic(settings)
                ads = res.get("ads", [])
                stats["ads_checked"] += len(ads)
                new_ads = []
                now_iso = datetime.now().isoformat()

                conn_w = get_db()
                try:
                    for ad in ads:
                        token = str(ad.get("token") or "").strip()
                        if not token:
                            continue
                        exists = conn_w.execute(
                            "SELECT 1 FROM seen_ads WHERE token=? AND monitor_id=?",
                            (token, m["id"]),
                        ).fetchone()
                        if exists:
                            continue
                        pnum = ad.get("price_num") or parse_price(ad.get("price", ""))
                        conn_w.execute(
                            "INSERT OR REPLACE INTO seen_ads (token, monitor_id, title, price, price_num, link, found_at, last_checked) VALUES (?,?,?,?,?,?,?,?)",
                            (token, m["id"], ad.get("title"), ad.get("price"), pnum, ad.get("link"), now_iso, now_iso),
                        )
                        conn_w.execute(
                            "INSERT INTO history (monitor_id, monitor_name, token, title, price, link, matched_keywords, value_score, found_at) VALUES (?,?,?,?,?,?,?,?,?)",
                            (
                                m["id"],
                                m["name"],
                                token,
                                ad.get("title"),
                                ad.get("price"),
                                ad.get("link"),
                                ",".join(ad.get("matched_keywords", [])),
                                ad.get("value_score"),
                                now_iso,
                            ),
                        )
                        new_ads.append(ad)

                    if new_ads and user:
                        for ad in new_ads[:MAX_SAVE_NOTIF_PER_RUN]:
                            score_txt = f" · امتیاز {int(round(ad['value_score']))}/100" if ad.get("value_score") else ""
                            kw_txt = f" · 🎯 {','.join(ad['matched_keywords'])}" if ad.get("matched_keywords") else ""
                            save_notification(
                                user_id=user["id"],
                                title=f"🔔 {m['name']}: {(ad.get('title') or '')[:90]}",
                                body=f"💰 {ad.get('price') or ''}{score_txt}{kw_txt}",
                                link=ad.get("link"),
                                monitor_id=m["id"],
                                conn=conn_w,
                            )
                    conn_w.commit()
                finally:
                    conn_w.close()

                stats["new_ads"] += len(new_ads)
                if new_ads:
                    batch = new_ads[:MAX_NOTIFY_PER_RUN]
                    extra = len(new_ads) - len(batch)
                    lines = [f"<b>🔔 مانیتور «{m['name']}» — {len(new_ads)} آگهی جدید</b>\n"]
                    for ad in batch:
                        kw = f" 🎯[{','.join(ad['matched_keywords'])}]" if ad.get("matched_keywords") else ""
                        vs = f" ⭐{int(round(ad['value_score']))}/100" if ad.get("value_score") else ""
                        lines.append(f"• {ad['title']}{kw}{vs}\n💰 {ad['price']}\n🔗 {ad['link']}\n")
                    if extra > 0:
                        lines.append(f"\n... و {extra} آگهی دیگر (در پنل اعلان‌ها و تاریخچه ثبت شد)")

                    first_link = batch[0].get("link") if len(batch) == 1 else "/notifications"
                    notify(
                        "\n".join(lines),
                        subject=f"دیوار [{m['name']}] - {len(new_ads)} آگهی جدید",
                        telegram_chat_id=user["telegram_chat_id"] if user else None,
                        email=user["notify_email"] if user else None,
                        user_id=user["id"] if user else None,
                        link=first_link,
                        monitor_id=m["id"],
                        save=False,
                    )
            except Exception as e:
                stats["errors"] += 1
                print(f"Monitor {m['id']} error:", e)

        try:
            check_price_changes()
        except Exception as e:
            print("Price check error:", e)

        conn_end = get_db()
        try:
            cleanup_old_records(conn_end)
            conn_end.execute(
                "INSERT INTO meta (key, value) VALUES ('last_monitor_run', ?) "
                "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                (datetime.now().isoformat(),),
            )
            conn_end.commit()
        finally:
            conn_end.close()

        dt = time.time() - t0
        print(
            f"[{datetime.now().isoformat(timespec='seconds')}] monitor done in {dt:.1f}s — "
            f"monitors={stats['monitors']} checked={stats['ads_checked']} new={stats['new_ads']} errors={stats['errors']}"
        )
        return stats
    finally:
        release_monitor_lock(lock_fh)


if __name__ == "__main__":
    run_all_monitors()
