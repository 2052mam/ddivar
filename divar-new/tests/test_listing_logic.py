import os
import tempfile
import unittest
from unittest.mock import patch

import app


class ListingLogicTests(unittest.TestCase):
    def test_current_sections_description_is_read(self):
        payload = {
            "sections": [
                {
                    "section_name": "DESCRIPTION",
                    "widgets": [
                        {
                            "widget_type": "DESCRIPTION_ROW",
                            # بعضی پاسخ‌های دیوار caption را قبل از متن می‌فرستند؛
                            # parser نباید خود «توضیحات» را به‌جای متن برگرداند.
                            "data": {
                                "title": "توضیحات",
                                "subtitle": "توضیح دقیق آگهی با کلمه کلیدی ویژه",
                            },
                        }
                    ],
                }
            ]
        }
        self.assertEqual(
            app.extract_post_details(payload)["description"],
            "توضیح دقیق آگهی با کلمه کلیدی ویژه",
        )
        canonical_payload = {
            "sections": [{
                "section_name": "DESCRIPTION",
                "widgets": [{
                    "widget_type": "DESCRIPTION_ROW",
                    "data": {"text": "متن canonical فقط در توضیحات"},
                }],
            }]
        }
        self.assertEqual(
            app.extract_post_details(canonical_payload)["description"],
            "متن canonical فقط در توضیحات",
        )
        # Legacy detail payloads still appear for some posts and must remain a
        # reliable fallback when the modern sections response is unavailable.
        legacy_payload = {"data": {"widgets": {"description": {"text": "توضیح قدیمی آگهی"}}}}
        self.assertEqual(app.extract_post_details(legacy_payload)["description"], "توضیح قدیمی آگهی")

    def test_detail_fallback_continues_when_first_response_has_only_specs(self):
        class FakeResponse:
            def __init__(self, payload, status_code=200):
                self._payload = payload
                self.status_code = status_code
                self.text = ""

            def json(self):
                return self._payload

        modern_specs_only = {
            "sections": [{
                "section_name": "LIST_DATA",
                "widgets": [{"data": {"title": "مدل", "subtitle": "۱۴۰۰"}}],
            }]
        }
        legacy_with_description = {
            "data": {"description": "کلمه فقط داخل توضیحات واقعی آگهی است"}
        }
        with patch("app.requests.get", side_effect=[
            FakeResponse(modern_specs_only), FakeResponse(legacy_with_description),
        ]) as get:
            details = app.get_post_details("Abcd_1234")

        self.assertEqual(details["description"], "کلمه فقط داخل توضیحات واقعی آگهی است")
        self.assertIn("کلمه فقط", details["description_search_text"])
        # پاسخ اول مشخصات داشت، اما نباید مانع درخواست fallback برای description شود.
        self.assertEqual(get.call_count, 2)
        self.assertIn("application/json-filled", get.call_args_list[0].kwargs["headers"]["Accept"])

    def test_public_page_description_is_last_resort_fallback(self):
        class FakeResponse:
            def __init__(self, payload=None, status_code=200, text=""):
                self._payload = payload
                self.status_code = status_code
                self.text = text

            def json(self):
                return self._payload

        specs_only = {"sections": [{"section_name": "LIST_DATA", "widgets": []}]}
        page = '<meta property="og:description" content="عبارت ویژه فقط در توضیح صفحه است">'
        with patch("app.requests.get", side_effect=[
            FakeResponse(specs_only), FakeResponse(status_code=404), FakeResponse(text=page),
        ]):
            details = app.get_post_details("Abcd_1234")

        self.assertEqual(details["description"], "عبارت ویژه فقط در توضیح صفحه است")
        self.assertEqual(details["description_source"], "public-page")

    def test_keywords_and_negative_keywords_use_description(self):
        details = {
            "description": "موتور سالم است اما خودرو تعمیری نیست",
            "detail_text": "",
            "features": {},
            "credit_num": None,
            "rent_num": None,
        }
        ad = {
            "token": "Abcd_1234",
            "title": "خودرو",
            "price_num": 500_000_000,
            "price": "500,000,000 تومان",
            "time_text": "۱ ساعت پیش",
        }
        with patch("app.get_post_details_cached", return_value=details):
            enriched = app.finalize_ads([ad], ["موتور سالم"], [], "car")
        self.assertEqual(enriched[0]["matched_keywords"], ["موتور سالم"])
        self.assertIn(
            "description", enriched[0]["matched_keyword_sources"]["موتور سالم"],
        )
        self.assertTrue(enriched[0]["has_keyword"])

        blocked = {
            "token": "Abcd_5678",
            "title": "خودرو",
            "price_num": 500_000_000,
            "price": "500,000,000 تومان",
            "time_text": "۱ ساعت پیش",
        }
        details["description"] = "این خودرو نیاز به تعمیر دارد"
        with patch("app.get_post_details_cached", return_value=details):
            filtered = app.finalize_ads([blocked], [], ["نیاز به تعمیر"], "car")
        self.assertEqual(filtered, [])

    def test_description_keyword_is_matched_in_every_category(self):
        cases = {
            "mobile": "باتری تعویض نشده و کلمه موبایل داخل توضیحات است",
            "car": "کلمه خودرو فقط در توضیحات آگهی است",
            "apartment-sell": "کلمه ملک فقط در توضیحات واحد است",
            "apartment-rent": "کلمه اجاره فقط در توضیحات واحد است",
        }
        for category, description in cases.items():
            keyword = description.split("کلمه ", 1)[1].split(" ", 1)[0]
            ad = {
                "token": f"Abcd_{len(category)}_{category[:3]}",
                "title": "عنوان بدون کلیدواژه",
                "price_num": 500_000_000,
                "time_text": "امروز",
            }
            details = {
                "description": description,
                "description_search_text": description,
                "detail_text": "",
                "features": {},
            }
            with self.subTest(category=category), patch(
                "app.get_post_details_cached", return_value=details
            ):
                result = app.finalize_ads([ad], [keyword], [], category)
            self.assertEqual(result[0]["matched_keywords"], [keyword])
            self.assertIn("description", result[0]["matched_keyword_sources"][keyword])
        self.assertEqual(
            app.find_matched_keywords("بدون\u200cرنگ و کاملاً سالم", ["بدون رنگ"]),
            ["بدون رنگ"],
        )

    def test_rental_conversion_is_exact_and_grouped(self):
        fields = app._parse_re_price_fields(
            "ودیعه ۳۰۰ میلیون تومان | اجارهٔ ماهانه ۹ میلیون تومان"
        )
        self.assertEqual(fields["credit_num"], 300_000_000)
        self.assertEqual(fields["rent_num"], 9_000_000)
        self.assertEqual(fields["rent_equivalent_num"], 18_000_000)
        self.assertEqual(fields["deposit_equivalent_num"], 600_000_000)
        self.assertIn("300,000,000", fields["price_display"])
        self.assertIn("100,000,000", fields["conversion_rule"])

    def test_detail_rows_supply_rental_and_property_features(self):
        payload = {
            "sections": [
                {
                    "section_name": "LIST_DATA",
                    "widgets": [
                        {"data": {"title": "آسانسور", "subtitle": "دارد"}},
                        {"data": {"title": "پارکینگ", "subtitle": "دارد"}},
                        {"data": {"title": "انباری", "subtitle": "ندارد"}},
                        {"data": {"title": "ودیعه", "subtitle": "۱۰۰ میلیون تومان"}},
                        {"data": {"title": "اجاره", "subtitle": "۳ میلیون تومان"}},
                    ],
                }
            ]
        }
        details = app.extract_post_details(payload)
        self.assertEqual(details["features"], {
            "parking": True, "elevator": True, "warehouse": False, "balcony": None,
        })
        self.assertEqual(details["credit_num"], 100_000_000)
        self.assertEqual(details["rent_num"], 3_000_000)

    def test_real_estate_facilities_change_rating(self):
        common = {
            "title": "آپارتمان",
            "price_num": 2_000_000_000,
            "time_text": "۱ ساعت پیش",
            "image_url": "image",
            "image_count": 3,
        }
        equipped = dict(
            common,
            features={"elevator": True, "parking": True, "warehouse": True, "balcony": False},
        )
        basic = dict(
            common,
            features={"elevator": False, "parking": False, "warehouse": False, "balcony": False},
        )
        equipped_score = app.score_ad_smart(
            equipped, 2_000_000_000, "apartment-sell", strict_price=False
        )
        basic_score = app.score_ad_smart(
            basic, 2_000_000_000, "apartment-sell", strict_price=False
        )
        self.assertGreater(
            equipped_score["score_breakdown"]["facilities"],
            basic_score["score_breakdown"]["facilities"],
        )
        self.assertGreater(equipped_score["value_score"], basic_score["value_score"])

    def test_car_damage_and_preferred_color_valuation(self):
        ad = {
            "title": "خودرو",
            "car_color": "سفید",
            "body_status_detail": "تمام‌رنگ",
            "chassis_status_detail": "جلو ضربه",
            "description": "بدون توضیح",
        }
        valuation = app.evaluate_car_condition(ad)
        self.assertEqual(valuation["paint_deduction_percent"], 10.0)
        self.assertEqual(valuation["chassis_deduction_percent"], 3.0)
        self.assertEqual(valuation["total_deduction_percent"], 13.0)
        self.assertEqual(valuation["color_bonus_percent"], 2.5)
        self.assertEqual(
            app.evaluate_car_condition({"title": "خودرو", "car_color": "مشکی"})["color_bonus_percent"],
            1.5,
        )

    def test_each_requested_paint_factor_is_applied(self):
        factors = {
            "سقف رنگ": 9.0,
            "کاپوت رنگ": 6.0,
            "صندوق رنگ": 5.0,
            "ستون رنگ": 4.0,
            "درب رنگ": 4.0,
            "گلگیر رنگ": 3.0,
        }
        for body_status, expected in factors.items():
            with self.subTest(body_status=body_status):
                valuation = app.evaluate_car_condition({
                    "title": "خودرو", "body_status_detail": body_status,
                })
                self.assertEqual(valuation["paint_deduction_percent"], expected)

        chassis_paint = app.evaluate_car_condition({
            "title": "خودرو", "chassis_status_detail": "شاسی رنگ",
        })
        self.assertEqual(chassis_paint["paint_deduction_percent"], 9.0)

    def test_rental_details_are_extracted_only_for_residential_rentals(self):
        listing = {
            "list_widgets": [{
                "widget_type": "POST_ROW",
                "data": {
                    "token": "Rental_1234",
                    "title": "آپارتمان نمونه",
                    "middle_description_text": "ودیعه ۳۰۰ میلیون تومان | اجاره ماهانه ۹ میلیون تومان",
                },
            }],
        }
        rental = app.extract_ads(listing, "apartment-rent")[0]
        self.assertEqual(rental["credit_num"], 300_000_000)
        self.assertEqual(rental["rent_num"], 9_000_000)
        self.assertTrue(rental["rental_equivalent_display"])

        for category in ("apartment-sell", "car", "mobile"):
            with self.subTest(category=category):
                card = app.extract_ads(listing, category)[0]
                self.assertEqual(card["category"], category)
                self.assertNotIn("credit_num", card)
                self.assertNotIn("rent_num", card)
                self.assertNotIn("rental_equivalent_display", card)

    def test_mobile_and_car_price_fallbacks_are_displayable(self):
        data = {
            "list_widgets": [
                {
                    "widget_type": "POST_ROW",
                    "data": {
                        "token": "Phone_1234",
                        "title": "موبایل",
                        # یک شکل objectی که در برخی پاسخ‌های کارت دیده می‌شود.
                        "price": {"value": 12_500_000},
                    },
                },
                {
                    "widget_type": "POST_ROW",
                    "data": {
                        "token": "Car_12345",
                        "title": "خودرو",
                        "middle_description_text": "",
                        "webengage": {"price": 875_000_000},
                    },
                },
            ],
        }
        mobile = app.extract_ads(data, "mobile")[0]
        car = app.extract_ads(data, "car")[1]
        self.assertEqual(mobile["price_num"], 12_500_000)
        self.assertIn("12,500,000", mobile["price_display"])
        self.assertEqual(car["price_num"], 875_000_000)
        self.assertIn("875,000,000", car["price_display"])

        # حتی اگر جست‌وجو قیمت را نفرستد، detail برای کارت غیر اجاره fallback است.
        incomplete = {"category": "car", "price": "توافقی", "price_display": "توافقی", "price_num": None}
        app._merge_post_details(incomplete, {"price_num": 640_000_000, "features": {}})
        self.assertEqual(incomplete["price_num"], 640_000_000)
        self.assertIn("640,000,000", incomplete["price_display"])

    def test_non_rental_detail_never_adds_rental_fields(self):
        ad = {
            "category": "mobile",
            "price_num": 10_000_000,
            # داده‌ی مانده از پاسخ/cache قدیمی هم باید پاک شود.
            "rental_equivalent_display": "نباید دیده شود",
            "credit_num": 100_000_000,
        }
        app._merge_post_details(ad, {
            "credit_num": 200_000_000,
            "rent_num": 5_000_000,
            "features": {},
        })
        for key in ("credit_num", "rent_num", "rental_equivalent_display", "conversion_rule"):
            self.assertNotIn(key, ad)

    def test_result_template_has_rental_category_guard_and_opaque_badges(self):
        from pathlib import Path

        template = Path(app.__file__).with_name("templates").joinpath("index.html").read_text()
        css = Path(app.__file__).with_name("static").joinpath("css", "app.css").read_text()
        self.assertIn("ad.category === 'apartment-rent'", template)
        self.assertIn("backdrop-filter:none", css)
        self.assertIn(".badge-smart{background:#075d45", css)

    def test_admin_user_editor_api_updates_account_and_keeps_last_admin_safe(self):
        """ویرایشگر پنل باید همه‌ی فیلدهای حساب را ذخیره و lockout را منع کند."""
        fd, db_path = tempfile.mkstemp(prefix="divar-admin-test-", suffix=".db")
        os.close(fd)
        original_db_path = app.DATABASE_PATH
        try:
            app.DATABASE_PATH = db_path
            conn = app.get_db()
            conn.execute(
                """CREATE TABLE users (
                    id INTEGER PRIMARY KEY AUTOINCREMENT, username TEXT UNIQUE NOT NULL,
                    password_hash TEXT NOT NULL, role TEXT DEFAULT 'user', is_active INTEGER DEFAULT 1,
                    plan TEXT DEFAULT 'free', subscription_expires_at TEXT, max_monitors INTEGER DEFAULT 1,
                    telegram_chat_id TEXT, notify_email TEXT, created_at TEXT, last_login TEXT
                )"""
            )
            conn.execute(
                "INSERT INTO users (username,password_hash,role,is_active,plan,max_monitors) VALUES (?,?,?,?,?,?)",
                ("admin-test", app.generate_password_hash("admin-pass"), "admin", 1, "vip", 9999),
            )
            conn.execute(
                "INSERT INTO users (username,password_hash,role,is_active,plan,max_monitors) VALUES (?,?,?,?,?,?)",
                ("target-user", app.generate_password_hash("old-password"), "user", 1, "free", 1),
            )
            conn.commit()
            admin_id = conn.execute("SELECT id FROM users WHERE username='admin-test'").fetchone()["id"]
            target_id = conn.execute("SELECT id FROM users WHERE username='target-user'").fetchone()["id"]
            conn.close()

            client = app.app.test_client()
            with client.session_transaction() as session:
                session["user_id"] = admin_id

            panel = client.get("/admin")
            self.assertEqual(panel.status_code, 200)
            self.assertIn(b'id="userEditor"', panel.data)

            updated = client.put(f"/api/admin/users/{target_id}", json={
                "username": "renamed-user", "role": "user", "plan": "pro",
                "is_active": True, "max_monitors": 8,
                "subscription_expires_at": "2031-02-03T04:05",
                "notify_email": "user@example.test", "telegram_chat_id": "123456",
                "password": "new-password",
            })
            self.assertEqual(updated.status_code, 200)
            self.assertTrue(updated.get_json()["success"])

            conn = app.get_db()
            row = conn.execute("SELECT * FROM users WHERE id=?", (target_id,)).fetchone()
            conn.close()
            self.assertEqual(row["username"], "renamed-user")
            self.assertEqual(row["plan"], "pro")
            self.assertEqual(row["max_monitors"], 8)
            self.assertEqual(row["notify_email"], "user@example.test")
            self.assertEqual(row["telegram_chat_id"], "123456")
            self.assertTrue(row["subscription_expires_at"].startswith("2031-02-03T04:05"))
            self.assertTrue(app.check_password_hash(row["password_hash"], "new-password"))

            locked_out = client.put(f"/api/admin/users/{admin_id}", json={"is_active": False})
            self.assertEqual(locked_out.status_code, 400)
            self.assertEqual(locked_out.get_json()["error"], "last_active_admin")
        finally:
            app.DATABASE_PATH = original_db_path
            try:
                os.remove(db_path)
            except FileNotFoundError:
                pass

    def test_all_cities_is_unrestricted_and_every_ad_is_rated(self):
        self.assertEqual(app.normalize_city_ids("all"), [])
        self.assertEqual(app.build_car_payload("all", ["Pride 111"])["city_ids"], [])
        ads = [{"token": "Abcd_9876", "title": "کالای بدون قیمت", "time_text": "امروز"}]
        app.ensure_ads_rated(ads, "mobile")
        self.assertIn("value_score", ads[0])
        self.assertIsNotNone(ads[0]["value_score"])


if __name__ == "__main__":
    unittest.main()
