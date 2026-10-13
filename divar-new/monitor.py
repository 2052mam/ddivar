# monitor.py
# اجرای دوره‌ای مانیتورها (cron / systemd timer)
# مثال:
# */10 * * * * cd /path/to/app && python monitor.py >> monitor.log 2>&1

import json
import time
import traceback
from datetime import datetime

from app import (
    init_db,
    get_db,
    run_search_logic,
    notify,
    save_notification,
    parse_price,
)
from config import REQUEST_DELAY, MAX_NOTIFY_PER_RUN

# فاصله بین مانیتورها (ثانیه) — جلوگیری از rate-limit دیوار
MONITOR_GAP_SEC = max(1.5, float(REQUEST_DELAY or 1.0) + 0.5)


def get_meta(conn, key, default=None):
    row = conn.execute("SELECT value FROM meta WHERE key=?", (key,)).fetchone()
    return row["value"] if row else default


def set_meta(conn, key, value):
    conn.execute(
        "INSERT INTO meta (key, value) VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
        (key, value),
    )


def should_check_prices_today(conn) -> bool:
    last = get_meta(conn, "last_price_check")
    if not last:
        return True
    try:
        last_dt = datetime.fromisoformat(last)
        return (datetime.now() - last_dt).total_seconds() > 20 * 3600
    except Exception:
        return True


def owner_is_eligible(conn, user_id) -> bool:
    if not user_id:
        return True  # legacy
    user = conn.execute("SELECT * FROM users WHERE id=?", (user_id,)).fetchone()
    if not user:
        return False
    if user["role"] == "admin":
        return True
    if not user["is_active"]:
        return False
    exp = user["subscription_expires_at"]
    if not exp:
        return False
    try:
        return datetime.fromisoformat(exp) > datetime.now()
    except Exception:
        return False


def get_owner_notify_targets(conn, user_id):
    if not user_id:
        return None, None
    user = conn.execute(
        "SELECT telegram_chat_id, notify_email FROM users WHERE id=?", (user_id,)
    ).fetchone()
    if not user:
        return None, None
    return user["telegram_chat_id"], user["notify_email"]


def extract_price_from_post(token: str):
    """دریافت قیمت به‌روز از صفحه آگهی — best effort"""
    try:
        from app import get_post_description, parse_price as _pp
        # get_post_description فقط متن است؛ برای قیمت از search استفاده نمی‌کنیم
        # در صورت نیاز می‌توان API جزئیات پست را اضافه کرد
        return None
    except Exception:
        return None


def check_price_changes(conn, mon, tg_chat_id, notify_email, max_check: int = 40):
    rows = conn.execute(
        """
        SELECT token, title, price, price_num, link FROM seen_ads
        WHERE monitor_id=?
        ORDER BY found_at DESC LIMIT ?
        """,
        (mon["id"], max_check),
    ).fetchall()
    changes = 0
    lines = [f"تغییر قیمت — {mon['name']}\n"]
    for row in rows:
        token = row["token"]
        old_num = row["price_num"]
        if old_num is None:
            continue
        new_num = extract_price_from_post(token)
        if new_num is None:
            continue
        if int(new_num) == int(old_num):
            continue
        now = datetime.now().isoformat()
        conn.execute(
            """
            INSERT INTO price_changes
            (token, monitor_id, title, old_price, new_price, link, changed_at)
            VALUES (?,?,?,?,?,?,?)
            """,
            (
                token,
                mon["id"],
                row["title"],
                row["price"],
                str(new_num),
                row["link"],
                now,
            ),
        )
        conn.execute(
            "UPDATE seen_ads SET price=?, price_num=?, last_checked=? WHERE token=?",
            (str(new_num), int(new_num), now, token),
        )
        changes += 1
        lines.append(
            f"• {row['title']}\nقبل: {row['price']} → بعد: {new_num}\n{row['link']}\n"
        )
    if changes:
        # ذخیره هر تغییر قیمت به صورت جدا در صفحه اعلان‌ها
        for row in rows:
            # فقط آیتم‌هایی که واقعاً تغییر کرده‌اند در lines آمده‌اند؛ برای سادگی از price_changes تازه استفاده می‌کنیم
            pass
        try:
            recent = conn.execute(
                """
                SELECT title, old_price, new_price, link FROM price_changes
                WHERE monitor_id=? ORDER BY id DESC LIMIT ?
                """,
                (mon["id"], changes),
            ).fetchall()
            for r in recent:
                body = f"قبل: {r['old_price']} → بعد: {r['new_price']}"
                save_notification(
                    mon["user_id"],
                    f"تغییر قیمت: {r['title'] or mon['name']}",
                    body,
                    link=r["link"],
                    monitor_id=mon["id"],
                    conn=conn,
                )
        except Exception as e:
            print("save price notifications error:", e)
        notify(
            "\n".join(lines),
            subject=f"تغییر قیمت: {mon['name']}",
            telegram_chat_id=tg_chat_id,
            email=notify_email,
            user_id=mon["user_id"],
            monitor_id=mon["id"],
            save=False,
        )
    return changes


def run():
    init_db()
    conn = get_db()
    monitors = conn.execute(
        "SELECT * FROM monitors WHERE is_active=1 ORDER BY id ASC"
    ).fetchall()
    do_price_check = should_check_prices_today(conn)

    stats = {"ok": 0, "skip": 0, "err": 0, "new_ads": 0}
    print(f"[{datetime.now().isoformat()}] monitor start — {len(monitors)} active")

    for mon in monitors:
        try:
            if not owner_is_eligible(conn, mon["user_id"]):
                print(f"  skip monitor {mon['id']}: owner inactive/expired")
                stats["skip"] += 1
                continue

            tg_chat_id, notify_email = get_owner_notify_targets(conn, mon["user_id"])

            try:
                settings = json.loads(mon["settings_json"] or "{}")
            except Exception:
                settings = {}
            settings["category"] = mon["category"]

            result = run_search_logic(settings)
            if not result.get("success"):
                print(f"  monitor {mon['id']} search failed: {result}")
                stats["err"] += 1
                time.sleep(MONITOR_GAP_SEC)
                continue

            ads = result.get("ads") or []
            new_ads = []
            for ad in ads:
                token = ad.get("token")
                if not token:
                    continue
                exists = conn.execute(
                    "SELECT 1 FROM seen_ads WHERE token=?", (token,)
                ).fetchone()
                if exists:
                    continue

                now = datetime.now().isoformat()
                price_num = ad.get("price_num")
                if price_num is None:
                    price_num = parse_price(ad.get("price"))

                conn.execute(
                    """
                    INSERT INTO seen_ads
                    (token, monitor_id, title, price, price_num, link, found_at, last_checked)
                    VALUES (?,?,?,?,?,?,?,?)
                    """,
                    (
                        token,
                        mon["id"],
                        ad.get("title"),
                        ad.get("price"),
                        price_num,
                        ad.get("link"),
                        now,
                        now,
                    ),
                )
                conn.execute(
                    """
                    INSERT INTO history
                    (monitor_id, monitor_name, token, title, price, link, matched_keywords, value_score, found_at)
                    VALUES (?,?,?,?,?,?,?,?,?)
                    """,
                    (
                        mon["id"],
                        mon["name"],
                        token,
                        ad.get("title"),
                        ad.get("price"),
                        ad.get("link"),
                        ",".join(ad.get("matched_keywords") or []),
                        ad.get("value_score"),
                        now,
                    ),
                )
                new_ads.append(ad)

            conn.commit()
            stats["ok"] += 1
            stats["new_ads"] += len(new_ads)

            if new_ads:
                lines = [f"آگهی جدید - {mon['name']}\n"]
                for ad in new_ads[:MAX_NOTIFY_PER_RUN]:
                    score = ad.get("value_score")
                    score_txt = f" | امتیاز {score}" if score is not None else ""
                    lines.append(
                        f"• {ad.get('title')}{score_txt}\n{ad.get('price')}\n{ad.get('link')}\n"
                    )
                    # ذخیره هر آگهی به صورت جدا در صفحه اعلان‌ها
                    body_parts = []
                    if ad.get("price"):
                        body_parts.append(str(ad.get("price")))
                    if score is not None:
                        body_parts.append(f"امتیاز {score}")
                    if ad.get("rental_equivalent_display"):
                        body_parts.append(str(ad.get("rental_equivalent_display")))
                    if ad.get("matched_keywords"):
                        body_parts.append("کلمات: " + ",".join(ad.get("matched_keywords") or []))
                    try:
                        save_notification(
                            mon["user_id"],
                            ad.get("title") or f"آگهی جدید — {mon['name']}",
                            " | ".join(body_parts),
                            link=ad.get("link"),
                            monitor_id=mon["id"],
                            conn=conn,
                        )
                    except Exception as e:
                        print("save ad notification error:", e)
                conn.commit()
                notify(
                    "\n".join(lines),
                    subject=f"دیوار: {mon['name']}",
                    telegram_chat_id=tg_chat_id,
                    email=notify_email,
                    user_id=mon["user_id"],
                    monitor_id=mon["id"],
                    save=False,
                )
                print(f"  monitor {mon['id']}: {len(new_ads)} new ads notified")

            track_price = 0
            try:
                track_price = int(mon["track_price"] or 0)
            except Exception:
                track_price = 0

            if track_price and do_price_check:
                print(f"  price-check monitor {mon['id']} ...")
                n = check_price_changes(conn, mon, tg_chat_id, notify_email)
                conn.commit()
                print(f"  price changes: {n}")

            time.sleep(MONITOR_GAP_SEC)

        except Exception as e:
            stats["err"] += 1
            print(f"  Monitor {mon['id']} error: {e}")
            traceback.print_exc()
            try:
                conn.rollback()
            except Exception:
                pass
            time.sleep(MONITOR_GAP_SEC)

    if do_price_check:
        set_meta(conn, "last_price_check", datetime.now().isoformat())
        conn.commit()

    set_meta(
        conn,
        "last_monitor_run",
        json.dumps(
            {"at": datetime.now().isoformat(), **stats},
            ensure_ascii=False,
        ),
    )
    conn.commit()
    conn.close()
    print(
        f"[{datetime.now().isoformat()}] monitor finished — "
        f"ok={stats['ok']} skip={stats['skip']} err={stats['err']} new={stats['new_ads']}"
    )


if __name__ == "__main__":
    run()
