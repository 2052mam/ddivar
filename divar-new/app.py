from flask import Flask, render_template, request, jsonify, session, redirect, url_for, send_from_directory
from functools import wraps
from concurrent.futures import ThreadPoolExecutor, as_completed
from werkzeug.security import generate_password_hash, check_password_hash
from werkzeug.exceptions import HTTPException
import requests
import re
import time
import sqlite3
import json
import smtplib
import threading
import statistics
import os
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from datetime import datetime, timedelta
from typing import Any, List, Dict, Optional, Tuple
from html import unescape

from config import (
    DATABASE_PATH,
    TELEGRAM_BOT_TOKEN,
    TELEGRAM_CHAT_ID,
    NOTIFY_EMAIL,
    SMTP_HOST,
    SMTP_PORT,
    SMTP_USER,
    SMTP_PASS,
    SMTP_USE_SSL,
    ADMIN_USERNAME,
    ADMIN_PASSWORD,
    TRIAL_DAYS,
    TRIAL_HOURS,
    SEARCH_COOLDOWN_SECONDS,
    MAX_PARALLEL_DIVAR_REQUESTS,
    MAX_NOTIFY_PER_RUN,
    REQUEST_DELAY,
)

app = Flask(__name__)
app.secret_key = "divar-monitor-secret-key-change-this"
app.permanent_session_lifetime = timedelta(days=60)  # نشست پایدار تا ۶۰ روز
app.config["SESSION_COOKIE_SAMESITE"] = "Lax"
app.config["SESSION_COOKIE_HTTPONLY"] = True
# اگر روی HTTPS هستید این را True کنید:
# app.config["SESSION_COOKIE_SECURE"] = True

@app.errorhandler(500)
def _err_500(e):
    try:
        print("[ERROR] 500:", e)
    except Exception:
        pass
    return "خطای موقت سرور. چند لحظه بعد دوباره تلاش کنید.", 500

@app.errorhandler(Exception)
def _err_any(e):
    # Redirect / HTTPException ها را دست نزن
    if isinstance(e, HTTPException):
        return e
    try:
        print("[ERROR] unhandled:", type(e).__name__, e)
    except Exception:
        pass
    try:
        if request.path.startswith("/api/"):
            return jsonify({"success": False, "error": "server_error"}), 500
    except Exception:
        pass
    return "خطای موقت سرور. لطفاً صفحه را رفرش کنید.", 500


MAX_WORKERS = max(6, MAX_PARALLEL_DIVAR_REQUESTS * 2)
SEARCH_SEMAPHORE = threading.Semaphore(MAX_PARALLEL_DIVAR_REQUESTS)

# جزئیات هر آگهی (توضیحات، امکانات و مشخصات) در هر جستجو چندبار لازم می‌شود.
# یک کش مشترک جلوی درخواست‌های تکراری را می‌گیرد؛ مقدار کش فقط داده‌ی عمومی آگهی است.
POST_DETAILS_CACHE: Dict[str, Tuple[Dict[str, Any], float]] = {}
POST_DETAILS_CACHE_LOCK = threading.Lock()
POST_DETAILS_CACHE_TTL = 900
# پاسخ تهی معمولاً ناشی از محدودسازی نرخ/خطای موقت است؛ آن را طولانی‌مدت
# cache نکنیم تا بازیابی توضیحات در جستجوی بعدی متوقف نشود.
POST_DETAILS_FAILURE_CACHE_TTL = 60

# برای سازگاری با نسخه‌های قبلی که فقط نام کش توضیحات را می‌شناختند.
DESC_CACHE = POST_DETAILS_CACHE
DESC_CACHE_LOCK = POST_DETAILS_CACHE_LOCK
DESC_CACHE_TTL = POST_DETAILS_CACHE_TTL

# همه‌ی درخواست‌های جزئیات هم همان سقف هم‌زمانی API دیوار را رعایت می‌کنند.
DETAIL_FETCH_WORKERS = max(1, min(5, MAX_PARALLEL_DIVAR_REQUESTS))
ALL_CITY_VALUES = {"all", "all_cities", "all-cities", "allcities", "*", "0"}

LAST_SEARCH: Dict[int, float] = {}
LAST_SEARCH_LOCK = threading.Lock()

# ====================== مدل‌ها ======================
MOBILE_MODELS = {
    "samsung": [
        "samsung galaxy a57", "samsung galaxy s25 ultra", "samsung galaxy s26", "samsung galaxy s26 ultra",
        "samsung galaxy s25 plus", "samsung galaxy s25 edge", "samsung galaxy s25 fe", "samsung galaxy s25",
        "samsung galaxy z fold6", "samsung galaxy s24 ultra", "samsung galaxy s24", "samsung galaxy s24 fe",
        "samsung galaxy z fold5", "samsung galaxy z flip5", "samsung galaxy z fold4", "samsung galaxy z flip4",
        "samsung galaxy s23 ultra", "samsung galaxy s23 plus", "samsung galaxy s23 5g", "samsung galaxy s23 fe",
        "samsung galaxy z fold3 5g", "samsung galaxy z flip3 5g", "samsung galaxy z flip 5g", "samsung galaxy z flip",
        "samsung galaxy z fold2 5g", "samsung galaxy s22 ultra 5g", "samsung galaxy s22+ 5g", "samsung galaxy s22 5g",
        "samsung galaxy s21 ultra 5g", "samsung galaxy s21+ 5g", "samsung galaxy s21 5g", "samsung galaxy s21 fe 5g",
        "samsung galaxy s21", "samsung galaxy s20 ultra 5g", "samsung galaxy s20 ultra", "samsung galaxy s20+ 5g",
        "samsung galaxy s20 5g", "samsung galaxy s20 fe 5g", "samsung galaxy s20 fe", "samsung galaxy s20",
        "samsung galaxy note20 ultra 5g", "samsung galaxy note20 ultra", "samsung galaxy note20 5g", "samsung galaxy note20",
        "samsung galaxy note10+ 5g", "samsung galaxy note10 5g", "samsung galaxy note10", "samsung galaxy note9",
        "samsung galaxy note8", "samsung galaxy s10 5g", "samsung galaxy s10+", "samsung galaxy s10e", "samsung galaxy s10",
        "samsung galaxy s10 lite", "samsung galaxy s9+", "samsung galaxy s9", "samsung galaxy s8+", "samsung galaxy s8",
        "samsung galaxy s7 edge", "samsung galaxy s7", "samsung galaxy s6 edge+", "samsung galaxy s6 edge", "samsung galaxy s6",
        "samsung galaxy a73 5g", "samsung galaxy a72", "samsung galaxy a71", "samsung galaxy a70", "samsung galaxy a55",
        "samsung galaxy a54", "samsung galaxy a53 5g", "samsung galaxy a52s 5g", "samsung galaxy a52 5g", "samsung galaxy a52",
        "samsung galaxy a51", "samsung galaxy a50", "samsung galaxy a35", "samsung galaxy a34", "samsung galaxy a33 5g",
        "samsung galaxy a32", "samsung galaxy a25", "samsung galaxy a24", "samsung galaxy a23", "samsung galaxy a22",
        "samsung galaxy a21s", "samsung galaxy a16", "samsung galaxy a15", "samsung galaxy a14 5g", "samsung galaxy a13",
        "samsung galaxy a12", "samsung galaxy a06", "samsung galaxy a05s", "samsung galaxy a05", "samsung galaxy a04s",
        "samsung galaxy a04", "samsung galaxy a03", "samsung galaxy a02s", "samsung galaxy a02", "samsung galaxy a01",
        "samsung galaxy m55", "samsung galaxy m54", "samsung galaxy m53", "samsung galaxy m34 5g", "samsung galaxy m33",
        "samsung galaxy m32", "samsung galaxy m31", "samsung galaxy m14", "samsung galaxy m13", "samsung galaxy m12",
        "samsung galaxy f54", "samsung galaxy f34", "samsung galaxy f23", "samsung galaxy f14", "samsung galaxy f13"
    ],
    "apple": [
        "apple iphone 17 pro max", "apple iphone 17 pro", "apple iphone 17", "apple iphone air",
        "apple iphone 16 pro max", "apple iphone 16 pro", "apple iphone 16 plus", "apple iphone 16",
        "apple iphone 15 pro max", "apple iphone 15 pro", "apple iphone 15 plus", "apple iphone 15",
        "apple iphone 14 pro max", "apple iphone 14 pro", "apple iphone 14 plus", "apple iphone 14",
        "apple iphone 13 pro max", "apple iphone 13 pro", "apple iphone 13", "apple iphone 13 mini",
        "apple iphone 12 pro max", "apple iphone 12 pro", "apple iphone 12", "apple iphone 12 mini",
        "apple iphone se (2022)", "apple iphone 11 pro max", "apple iphone 11 pro", "apple iphone 11",
        "apple iphone se (2020)", "apple iphone xs max", "apple iphone xs", "apple iphone xr",
        "apple iphone x", "apple iphone 8 plus", "apple iphone 8", "apple iphone 7 plus", "apple iphone 7",
        "apple iphone 6s plus", "apple iphone 6s", "apple iphone se", "apple iphone 6 plus", "apple iphone 6"
    ],
    "xiaomi": [
        "xiaomi 15t pro", "xiaomi 15 ultra", "xiaomi 15t", "xiaomi 14t pro", "xiaomi 14t", "xiaomi 14",
        "xiaomi 13t pro", "xiaomi 13t", "xiaomi 13 pro", "xiaomi 13", "xiaomi 12t pro", "xiaomi 12t",
        "xiaomi 12 pro", "xiaomi 12", "xiaomi 11t pro", "xiaomi 11t", "xiaomi mi 11 ultra", "xiaomi mi 11",
        "xiaomi poco f7 pro", "xiaomi poco f7", "xiaomi poco f6 pro", "xiaomi poco f6", "xiaomi poco f5 pro",
        "xiaomi poco f5", "xiaomi poco x7 pro", "xiaomi poco x7", "xiaomi poco x6 pro", "xiaomi poco x6",
        "xiaomi poco x5 pro", "xiaomi poco x5", "xiaomi poco m6 pro", "xiaomi poco m6", "xiaomi poco m5",
        "xiaomi redmi note 14 pro+", "xiaomi redmi note 14 pro", "xiaomi redmi note 14",
        "xiaomi redmi note 13 pro+", "xiaomi redmi note 13 pro", "xiaomi redmi note 13",
        "xiaomi redmi note 12 pro+", "xiaomi redmi note 12 pro", "xiaomi redmi note 12",
        "xiaomi redmi note 11 pro+", "xiaomi redmi note 11 pro", "xiaomi redmi note 11",
        "xiaomi redmi note 10 pro", "xiaomi redmi note 10", "xiaomi redmi 14c", "xiaomi redmi 13c",
        "xiaomi redmi 13", "xiaomi redmi 12", "xiaomi redmi 12c", "xiaomi redmi a3", "xiaomi redmi a2"
    ],
    "huawei": [
        "huawei pura 70", "huawei p60 pro", "huawei p60", "huawei mate 50 pro", "huawei mate 50",
        "huawei mate 40 pro", "huawei mate 40", "huawei p50 pro", "huawei p50", "huawei p40 pro",
        "huawei p40", "huawei p30 pro", "huawei p30", "huawei nova 12", "huawei nova 11",
        "huawei nova 10", "huawei nova 9", "huawei nova 8", "huawei nova 7", "huawei y9a",
        "huawei y7a", "huawei y6p", "huawei y5p", "huawei p smart", "huawei enjoy 20"
    ]
}

CAR_MODELS = {
    "pride": [
        'Pride 111', 'Pride 131', 'Pride 132',
        'Pride 141', 'Pride Automatic', 'Pride Station',
        'Pride Sedan', 'Pride Pickup Plus', 'Pride Pickup 151 Bi-fuel',
        'Pride Pickup 151 GX', 'Pride Pickup 151 SE', 'Pride Hatchback',
        'Pride Saba GLXI',
    ],
    "peugeot": [
        'Peugeot 2008', 'Peugeot 204', 'Peugeot 205',
        'Peugeot 206', 'Peugeot 207i', 'Peugeot 301',
        'Peugeot 304', 'Peugeot 306', 'Peugeot 307',
        'Peugeot 308', 'Peugeot 403', 'Peugeot 404',
        'Peugeot 405', 'Peugeot 406', 'Peugeot 407',
        'Peugeot 504', 'Peugeot 505', 'Peugeot 508',
        'Peugeot 605', 'Peugeot 607', 'Peugeot 806',
        'Peugeot RD', 'Peugeot RDI', 'Peugeot Partner',
        'Peugeot Pars', 'Peugeot Limousine', 'Peugeot Roa Petrol',
        'Peugeot Roa Bi-fuel', 'Peugeot Roa Sal Bi-fuel', 'Peugeot RCZ',
    ],
    "saipa": [
        'Saipa 421P', 'Saipa 441P', 'Saipa Arya',
        'Saipa Atlas E-normal', 'Saipa Atlas E Plus', 'Saipa Atlas G',
        'Saipa Atlas GL', 'Saipa Atlas L', 'Saipa Atlas S',
        'Saipa Sahand E', 'Saipa Sahand G', 'Saipa Sahand G CNG',
        'Saipa Sahand S', 'Saipa Karvan Saipa',
    ],
    "samand": [
        'Samand Sarir', 'Samand Soren', 'Samand EL',
        'Samand LX', 'Samand SE', 'Samand X7 petrol',
    ],
    "tiba": [
        'Tiba Hatchback', 'Tiba Sedan Plus', 'Tiba Sedan EX Bi-fuel',
        'Tiba Sedan LX-normal', 'Tiba Sedan LX Bi-fuel', 'Tiba Sedan SL',
        'Tiba Sedan SX-normal', 'Tiba Sedan SX Bi-fuel',
    ],
    "tara": [
        'Tara Automatic', 'Tara tara EV', 'Tara Manual',
        'Tara V3', 'Tara V1 plus', 'Tara v4 LX',
    ],
    "dena": [
        'Dena plus', 'Dena basic 1700cc',
    ],
    "runna": [
        'Runna Plus P', 'Runna Plus-normal', 'Runna Plus TU5P',
        'Runna EL',
    ],
    "shahin": [
        'Shahin Plus', 'Shahin G-normal', 'Shahin G CVT',
    ],
    "quick": [
        'Quick Automatic', 'Quick manual', 'Quick GX',
        'Quick GXH', 'Quick RS',
    ],
    "paykan": [
        'Paykan Bi-fuel(CNG)', 'Paykan Bi-fuel(LPG)', 'Paykan Petrol',
        'Paykan Pickup Petrol', 'Paykan Pickup CNG',
    ],
    "iran_khodro": [
        'Iran Khodro RIRA 1.7L Turbo', 'Iran Khodro RIRA EV',
    ],
    "mvm": [
        'MVM Arrizo 5ev', 'MVM Arizo 5 sport FL', 'MVM M5 Pro',
        'MVM X77', 'MVM 110', 'MVM 110S',
        'MVM 315 Sedan', 'MVM 315 hatchback', 'MVM 315 Hatchback Plus',
        'MVM 530', 'MVM 550', 'MVM X22',
        'MVM X22pro', 'MVM X33', 'MVM X33S',
        'MVM X55 Excellent-normal', 'MVM X55 Excellent-sport', 'MVM X55 Pro Li',
        'MVM X55 Pro Excellent-normal', 'MVM X55 Pro Excellent-sport', 'MVM X55 Pro IE-normal',
    ],
    "fownix": [
        'Fownix Arrizo 6 pro', 'Fownix ARIZO 6 GT', 'Fownix ARIZO 8',
        'Fownix FX EV', 'Fownix Tiggo 7 pro-normal', 'Fownix Tiggo 7 pro E plus Plugin hybrid',
        'Fownix Tiggo 7 pro Premium', 'Fownix Tiggo 7 pro Max-normal', 'Fownix Tiggo 7 pro Max 1.6L Turbo awd',
        'Fownix Tiggo 8 pro-normal', 'Fownix تیگو 8 پرو E plus', 'Fownix Tiggo 8 pro Max IE',
        'Fownix Tiggo 8 Promax', 'Fownix Tiggo 8 pro Max E plus Plugin hybrid', 'Fownix Tiggo 9',
        'Fownix Fx Excellent', 'Fownix Fx Premium-normal',
    ],
    "chery": [
        'Chery Arrizo 5', 'Chery Arizo 5 sport FL', 'Chery Arrizo 5IE New',
        'Chery Arrizo 5TE', 'Chery Arrizo 6', 'Chery Tiggo 5',
        'Chery Tiggo 7 excellent', 'Chery Queen', 'Chery Viana A15',
    ],
    "kmc": [
        'KMC Eagle 1.5L Petrol', 'KMC EJ7 PLUS', 'KMC SR3',
        'KMC SR6 1.5L Turbo', 'KMC A5', 'KMC X5',
        'KMC T9 pick-up', 'KMC EJ7-normal', 'KMC JS6',
        'KMC J7', 'KMC K7', 'KMC T8',
    ],
    "jac": [
        'JAC J3 Sedan', 'JAC J3 Hatchback', 'JAC J4',
        'JAC J5', 'JAC S3', 'JAC S5',
        'JAC Van Refine',
    ],
    "toyota": [
        'Toyota Aurion', 'Toyota FJ Cruiser', 'Toyota Echo',
        'Toyota BZ3X', 'Toyota BZ3', 'Toyota Prado 2door',
        'Toyota Prado 4door', 'Toyota Prius', 'Toyota Highlander Hybrid',
        'Toyota Rav4 2.0L Fwd', 'Toyota Rav4 2.0L Awd', 'Toyota Rav4 2.5L Fwd',
        'Toyota Rav4-normal', 'Toyota Rav4 Hybrid-normal', 'Toyota Rav4 Hybrid Awd',
        'Toyota Rav4 Adventure Plus', 'Toyota Celica', 'Toyota Supra',
        'Toyota Solara', 'Toyota Frontlander Hybrid', 'Toyota 4runner',
        'Toyota Furtuner', 'Toyota Carina', 'Toyota Crown',
        'Toyota Corolla', 'Toyota Corona', 'Toyota Cressida',
        'Toyota Camry', 'Toyota Landcruiser 2door', 'Toyota Landcruiser 4door',
        'Toyota Levin-Turbo 1200CC', 'Toyota Levin-Hybrid 1800CC-normal', 'Toyota Levin-Hybrid 1800CC Sport Edition',
        'Toyota Venza-normal', 'Toyota Hilux single cabin', 'Toyota Hilux double cabin',
        'Toyota Yaris Sedan', 'Toyota Yaris Hatchback', 'Toyota Avalon Hybrid 2.5L',
        'Toyota bZ4x-normal', 'Toyota bZ4x TURBO', 'Toyota C-HR',
        'Toyota GT86 automatic', 'Toyota Venza 2.0L luxury plus', 'Toyota Venza 2.0L premium',
        'Toyota Wildlander 2.0L Fwd', 'Toyota Wildlander Luxury Plus', 'Toyota Pickup',
        'Toyota Van',
    ],
    "hyundai": [
        'Hyundai Van', 'Hyundai Azera Grandeur', 'Hyundai Avante',
        'Hyundai Scoupe', 'Hyundai Excel', 'Hyundai Accent',
        'Hyundai Accent-ir', 'Hyundai Elantra', 'Hyundai Elantra-ir',
        'Hyundai Hyundai Elantra 1600', 'Hyundai Hyundai Hyundai Elantra 1800', 'Hyundai Palisade',
        'Hyundai Trajet', 'Hyundai Tucson 1.5L Turbo', 'Hyundai Tucson-ix35',
        'Hyundai Genesis', 'Hyundai Santafe ix45', 'Hyundai Centennial',
        'Hyundai Sonata 1.5L Turbo', 'Hyundai Sonata-LF-normal', 'Hyundai Sonata-LF-hybrid',
        'Hyundai Sonata-NF', 'Hyundai Sonata-YF', 'Hyundai hyundai creta',
        'Hyundai Kona', 'Hyundai Veracruz-ix55', 'Hyundai Verna',
        'Hyundai Veloster', 'Hyundai FX Coupe', 'Hyundai i10-ir',
        'Hyundai i20-normal', 'Hyundai i20-ir optional', 'Hyundai i30',
        'Hyundai i40-normal', 'Hyundai i40 Station',
    ],
    "kia": [
        'Kia Optima', 'Kia Optima-hybrid', 'Kia Opirus',
        'Kia Sportage', 'Kia Stonic', 'Kia Pegas',
        'Kia Picanto', 'Kia Rio', 'Kia Rio-ir',
        'Kia Cerato', 'Kia Cerato-ir', 'Kia Seltos 1.5L',
        'Kia Seltos-normal', 'Kia Sorento', 'Kia soul',
        'Kia Sonet', "Kia Cee'd", 'Kia cadenza',
        'Kia carnival', 'Kia carens', 'Kia K3-normal',
        'Kia K3 GT line', 'Kia K5-normal', 'Kia mohave',
        'Kia EV5 Type 530', 'Kia K5 TURBO', 'Kia K8',
        'Kia Q3 Sportback', 'Kia Seltos Turbo', 'Kia Van',
    ],
    "nissan": [
        'Nissan Armada', 'Nissan Altima', 'Nissan X-Terra',
        'Nissan X-Trail', 'Nissan X trail', 'Nissan Patrol 2 door',
        'Nissan Patrol 4 door', 'Nissan Pathfinder', 'Nissan Terra Exclusive',
        'Nissan Terra Pickup', 'Nissan Terra', 'Nissan Terra Leo mx8 2.0L Turbo',
        'Nissan Teana-ir', 'Nissan Teana-im', 'Nissan Tiida',
        'Nissan Juke', 'Nissan Roniz', 'Nissan Sunny',
        'Nissan Seranza', 'Nissan Sylphy E power', 'Nissan Sylphy-normal',
        'Nissan Sylphy Enjoy Edition', 'Nissan Sylphy Joyful', 'Nissan Qashqai',
        'Nissan Qashqai-ir', 'Nissan KICKS-normal', 'Nissan Maxima-ir',
        'Nissan Maxima-im', 'Nissan Murano', 'Nissan Navara',
        'Nissan KICKS 1.6L', 'Nissan N6 PHEV', 'Nissan N7',
        'Nissan Pickup Double-Cabin', 'Nissan Pickup Patrol',
    ],
    "bmw": [
        'BMW 225i', 'BMW 116i', 'BMW 2002',
        'BMW 218i Grand Coupe', 'BMW 220i Grand Coupe', 'BMW 318i',
        'BMW X2', 'BMW 1 Series Convertible', 'BMW 1 Series Coupe',
        'BMW 1 Series Hatchback', 'BMW 2 Series Activetourer', 'BMW 2 Series Convertible',
        'BMW 2 Series Coupe', 'BMW 2 Series Grancoupe', 'BMW 3 Series Sedan',
        'BMW 3 Series Convertible', 'BMW 3 Series Coupe', 'BMW 3 Series GT',
        'BMW 4 Series Convertible', 'BMW 4 Series Coupe', 'BMW 4 Series Grancoupe',
        'BMW 5 Series Sedan', 'BMW 5 Series GT', 'BMW 6 Series Convertible',
        'BMW 6 Series Coupe', 'BMW 6 Series Grancoupe', 'BMW 7 Series',
        'BMW Classic', 'BMW i5', 'BMW i8',
        'BMW iX1', 'BMW iX3', 'BMW X1',
        'BMW X3', 'BMW X4', 'BMW X5',
        'BMW X6', 'BMW Z3', 'BMW Z4 20i',
        'BMW Z4 23i', 'BMW Z4 28i', 'BMW Z4 30i',
    ],
    "mercedes_benz": [
        'Mercedes-Benz GLB 180', 'Mercedes-Benz CLA 180', 'Mercedes-Benz AMG EQE SUV',
        'Mercedes-Benz A Class', 'Mercedes-Benz B Class', 'Mercedes-Benz C Class',
        'Mercedes-Benz CE Class', 'Mercedes-Benz CL Class', 'Mercedes-Benz CLA Class',
        'Mercedes-Benz CLK Class convertible', 'Mercedes-Benz CLK Class Coupe', 'Mercedes-Benz CLS Class',
        'Mercedes-Benz E Class', 'Mercedes-Benz E Class-ir', 'Mercedes-Benz G Class',
        'Mercedes-Benz GL Class', 'Mercedes-Benz GLA Class', 'Mercedes-Benz GLB Class',
        'Mercedes-Benz GLC Class', 'Mercedes-Benz GLE Class', 'Mercedes-Benz GLK Class',
        'Mercedes-Benz GLS Class', 'Mercedes-Benz ML Class', 'Mercedes-Benz S Class',
        'Mercedes-Benz SL Class', 'Mercedes-Benz SLC Class', 'Mercedes-Benz SLK Class',
        'Mercedes-Benz SLR Class', 'Mercedes-Benz SLS Class', 'Mercedes-Benz Classic',
        'Mercedes-Benz Viano', 'Mercedes-Benz Unimog', 'Mercedes-Benz A180',
        'Mercedes-Benz EQA 260', 'Mercedes-Benz EQB 260', 'Mercedes-Benz EQE 500 SUV',
        'Mercedes-Benz GTS', 'Mercedes-Benz GLB 200', 'Mercedes-Benz Van Vito',
    ],
    "audi": [
        'Audi Q4 e-tron', 'Audi Q5 e-tron', 'Audi A3 Sedan',
        'Audi A3L 1.5L Turbo', 'Audi A4', 'Audi Q5-normal',
        'Audi TT convertible',
    ],
    "mazda": [
        'Mazda 2-ir', 'Mazda 3', 'Mazda 3-ir',
        'Mazda 323', 'Mazda 3N Sedan-ir', 'Mazda 3N Hatchback-ir',
        'Mazda 6-normal', 'Mazda 626', 'Mazda EZ60',
        'Mazda CX-30', 'Mazda CX-50-normal', 'Mazda CX-50 Hybrid',
        'Mazda CX5', 'Mazda CX9', 'Mazda EZ6 Hybrid EREV',
        'Mazda EZ6-normal', 'Mazda Pickup 1000', 'Mazda Pickup 1600',
        'Mazda Pickup injector 2000-single cabin petrol', 'Mazda Pickup injector 2000-single cabin Bi-fuel', 'Mazda Pickup injector 2000-double cabin petrol',
        'Mazda Pickup injector 2000-double cabin Bi-fuel', 'Mazda Pickup 2600', 'Mazda Pickup Cara-single cabin 1700cc',
        'Mazda Pickup Cara-double cabin 1700cc', 'Mazda Pickup Cara-double cabin 2000cc',
    ],
    "renault": [
        'Renault 21', 'Renault 5-normal', 'Renault 5-ir',
        'Renault 9 GTL', 'Renault Arkana XM3', 'Renault Scala',
        'Renault Pars Tondar', 'Renault PK', 'Renault Talisman',
        'Renault Tondar 90', 'Renault Duster', 'Renault Sandero',
        'Renault Sandero-Stepway', 'Renault Sepand', 'Renault Safrane',
        'Renault Symbol', 'Renault Fluence', 'Renault Captur',
        'Renault Clio', 'Renault Koleos', 'Renault Laguna',
        'Renault Latitude', 'Renault Modus', 'Renault Megan',
        'Renault Megan-ir', 'Renault Pickup',
    ],
    "volkswagen": [
        'Volkswagen ID4-normal', 'Volkswagen Atlas', 'Volkswagen Beetle',
        'Volkswagen Passat', 'Volkswagen Tayron L', 'Volkswagen T-ROC',
        'Volkswagen Tiguan-normal', 'Volkswagen Tiguan L Pro', 'Volkswagen jetta-normal',
        'Volkswagen Jetta VS5', 'Volkswagen jetta VS7', 'Volkswagen Caddy',
        'Volkswagen Gol', 'Volkswagen Golf', 'Volkswagen ID Unyx Pure',
        'Volkswagen ID Unyx-normal', 'Volkswagen ID4 Pro', 'Volkswagen ID4 Pure Plus',
        'Volkswagen Pickup', 'Volkswagen Van Transporter', 'Volkswagen Van Caddy',
        'Volkswagen Van T2-normal', 'Volkswagen Van T2 Camper',
    ],
    "suzuki": [
        'Suzuki S-PRESSO', 'Suzuki BALENO', 'Suzuki Jimny',
        'Suzuki Jimny-normal', 'Suzuki Dzire', 'Suzuki Celerio',
        'Suzuki Swift', 'Suzuki Ciaz', 'Suzuki Fronx GLX',
        'Suzuki Kizashi', 'Suzuki Grand-Vitara', 'Suzuki Grand-Vitara-ir automatic-2000cc',
        'Suzuki Grand-Vitara-ir automatic-2400cc', 'Suzuki Grand-Vitara-ir manual-2000cc',
    ],
    "alfa_romeo": [
        'Alfa Romeo 4C', 'Alfa Romeo Giulietta',
    ],
    "amg": [
        'Amg Artan', 'Amg Arka',
    ],
    "amico": [
        'Amico Araz', 'Amico Asna petrol', 'Amico Asna Bi-fuel',
    ],
    "ario": [
        'Ario automatic-1600cc', 'Ario manual-1500cc',
    ],
    "arisan": [
        'Arisan Arisan',
    ],
    "avatr": [
        'Avatr 07 Erev', 'Avatr 11',
    ],
    "aventra": [
        'AVENTRA',
    ],
    "bac": [
        'BAC',
    ],
    "baic": [
        'BAIC EU5', 'BAIC BJ30e', 'BAIC Beijing X55',
        'BAIC U5 PLUS', 'BAIC Sabrina', 'BAIC Sabrina-ir',
        'BAIC Senova', 'BAIC Beijing X7', 'BAIC X25 Automatic',
    ],
    "baw": [
        'BAW',
    ],
    "bestune": [
        'Bestune Nat', 'Bestune T77',
    ],
    "besturn": [
        'Besturn B30', 'Besturn B50',
    ],
    "bisu": [
        'BISU T3',
    ],
    "borgward": [
        'Borgward BX6', 'Borgward BX5', 'Borgward BX7TS',
    ],
    "brilliance": [
        'Brilliance C3', 'Brilliance H220', 'Brilliance H230',
        'Brilliance H320', 'Brilliance H330', 'Brilliance V5',
        'Brilliance V6',
    ],
    "buick": [
        'Buick B2', 'Buick B3-normal',
    ],
    "byd": [
        'BYD Qin-L', 'BYD Qin Plus', 'BYD Dolphin',
        'BYD Destroyer 05 DMi', 'BYD SEALION 06 DMi', 'BYD Seagull',
        'BYD SEAL 05 DMi', 'BYD HAN', 'BYD SONG PLUS',
        'BYD F3', 'BYD S6', 'BYD S7',
    ],
    "capra": [
        'Capra Capra 2', 'Capra Single-cabin', 'Capra Double Cabin singledifferential',
        'Capra B-normal', 'Capra B Double-cabin', 'Capra U double cabin 2.4L AT',
        'Capra U-normal',
    ],
    "changan": [
        'Changan Hunter Plus pick-up', 'Changan Cs 35 2', 'Changan CS 35 3',
        'Changan CS 55', 'Changan CS35-normal', 'Changan CS35-ir',
        'Changan EADO-normal', 'Changan EADO EV460', 'Changan NEVO A05',
        'Changan X5 Plus',
    ],
    "changhe": [
        'Changhe',
    ],
    "citroen": [
        'Citroen Xantia', 'Citroen Xantia2', 'Citroen Jian',
        'Citroen Sialk', 'Citroen C3 wo-warmer', 'Citroen C3 XR',
        'Citroen C5', 'Citroen New C5',
    ],
    "dacia": [
        'Dacia Duster Hybrid',
    ],
    "daewoo": [
        'Daewoo Espero', 'Daewoo Racer', 'Daewoo Cielo sedan',
        'Daewoo Matiz',
    ],
    "daihatsu": [
        'Daihatsu',
    ],
    "datsun": [
        'Datsun Car',
    ],
    "dayun": [
        'Dayun DX', 'Dayun Y5-normal', 'Dayun Y5 Plus',
    ],
    "deer": [
        'Deer',
    ],
    "delica": [
        'Delica Van Taxi',
    ],
    "dignity": [
        'Dignity Prime',
    ],
    "dodge": [
        'Dodge Challenger', 'Dodge Coronet',
    ],
    "domy": [
        'Domy',
    ],
    "dongfeng": [
        'Dongfeng Shine Max-normal', 'Dongfeng Shine Max Hybrid', 'Dongfeng E5',
        'Dongfeng E70', 'Dongfeng H30',
    ],
    "ds": [
        'DS 3', 'DS 5-normal', 'DS 5LS',
        'DS 6', 'DS Crossback-4', 'DS crossback-7 opera',
    ],
    "envoy": [
        'Envoy',
    ],
    "eres": [
        'Eres',
    ],
    "fiat": [
        'Fiat 500', 'Fiat 500x', 'Fiat R500',
        'Fiat TIPO SEDAN 1600', 'Fiat TIPO CROSS 1600',
    ],
    "fidelity": [
        'Fidelity XB1', 'Fidelity Prime', 'Fidelity FIDELITY PRESTIGE',
        'Fidelity Prestige 7 seater', 'Fidelity Elite 5 seater',
    ],
    "ford": [
        'Ford Edge', 'Ford Taurus', 'Ford Fusion',
        'Ford Kuga', 'Ford Mustang',
    ],
    "gac": [
        'Gac EMPOW', 'Gac Empow Hybrid', 'Gac EMKOO',
        'Gac Emkoo Hybrid', 'Gac GS8', 'Gac Es9',
        'Gac GS3', 'Gac E9 Plugin Hybrid 2.0L Turbo',
    ],
    "gac_gonow": [
        'GAC Gonow G5', 'GAC Gonow GA3S', 'GAC Gonow GS5',
    ],
    "geely": [
        'Geely AZKARRA', 'Geely BINRUI', 'Geely PREFACE',
        'Geely GX3 PRO', 'Geely GEOMETRY C', 'Geely COOLRAY PLUS',
        'Geely MONJARO', 'Geely Emgrand 7', 'Geely Emgrand 7_RV',
        'Geely Emgrand X7', 'Geely GC6 excellent',
    ],
    "great_wall": [
        'Great-Wall Tank', 'Great-Wall Pickup Wingle 5-single cabin', 'Great-Wall Pickup Wingle 5-double cabin',
        'Great-Wall C30', 'Great-Wall Pickup Wingle 3', 'Great-Wall Van 13 Seater',
    ],
    "haima": [
        'Haima X7', 'Haima S8', 'Haima S5',
        'Haima S7 automatic-2000cc', 'Haima S7 Plus', 'Haima S7 automatic-turbo-1800cc',
    ],
    "hyosow": [
        'Hyosow',
    ],
    "im": [
        'IM',
    ],
    "infiniti": [
        'infiniti FX35', 'infiniti g35 coupe', 'infiniti g37 coupe',
        'infiniti q50',
    ],
    "inroads": [
        'inroads',
    ],
    "isuzu": [
        'Isuzu',
    ],
    "iveco": [
        'Iveco',
    ],
    "jaguar": [
        'Jaguar',
    ],
    "jeep": [
        'Jeep Ahoo', 'Jeep Cherokee', 'Jeep Wrangler',
        'Jeep Renegade', 'Jeep Shahbaz', 'Jeep Sahra',
        'Jeep Mute', 'Jeep Wagoneer', 'Jeep KM',
    ],
    "jetour": [
        'Jetour',
    ],
    "jmc": [
        'JMC',
    ],
    "joylong": [
        'Joylong',
    ],
    "kg_mobility": [
        'KG Mobility Actyon', 'KG Mobility Torres', 'KG Mobility Tivoli',
        'KG Mobility Rexton',
    ],
    "lada": [
        'Lada Granta', 'Lada Niva Travel', 'Lada Niva-normal',
        'Lada Vesta Sport',
    ],
    "lamari": [
        'Lamari ECHO 1.5 Turbo', 'Lamari Eama-normal', 'Lamari NEO 1.5 Turbo',
        'Lamari HIRCANI 1.6 Turbo',
    ],
    "land_rover": [
        'Land Rover Discovery', 'Land Rover Defender', 'Land Rover Range Rover-normal',
        'Land Rover Range Rover-Evoque', 'Land Rover ir',
    ],
    "lexus": [
        'Lexus CT', 'Lexus ES', 'Lexus GS',
        'Lexus IS', 'Lexus LS', 'Lexus LX',
        'Lexus NX 200t', 'Lexus NX 300 H', 'Lexus NX 350',
        'Lexus RX 200t-luxury', 'Lexus RX 200T-F-sport', 'Lexus RX 350-normal',
        'Lexus RX 350-F-sport',
    ],
    "lifan": [
        'Lifan 520', 'Lifan 520i', 'Lifan 620',
        'Lifan 820', 'Lifan X50', 'Lifan X60 automatic',
        'Lifan X70',
    ],
    "mg": [
        'MG 3-normal', 'MG 350', 'MG 360 automatic-normal',
        'MG 360 automatic-turbo', 'MG 4 EV XPOWER', 'MG MG 5',
        'MG 550', 'MG 6', 'MG MG 7',
        'MG TORRES', 'MG Roewe D7', 'MG Cyberster',
        'MG GS', 'MG GT', 'MG RX5',
    ],
    "oldsmobile": [
        'Oldsmobile Regency',
    ],
    "opel": [
        'Opel Astra', 'Opel Omega', 'Opel Insignia',
        'Opel Calibra', 'Opel CROSS LAND', 'Opel Corsa Turbo',
        'Opel Corsa automatic', 'Opel Corsa new', 'Opel Mokka 1.2L Turbo',
        'Opel Mokka', 'Opel Vectra',
    ],
    "oshan": [
        'Oshan E2 Pro',
    ],
    "pars_khodro": [
        'PARS KHODRO P90',
    ],
    "pazhan": [
        'Pazhan 2 door', 'Pazhan 4 door 4 cylinder-4 door', 'Pazhan Herour',
    ],
    "pontiac": [
        'Pontiac',
    ],
    "porsche": [
        'Porsche 911', 'Porsche Boxster', 'Porsche Panamera',
        'Porsche Cayenne', 'Porsche Macan',
    ],
    "proton": [
        'Proton Impian', 'Proton Gen-2', 'Proton Viera automatic',
    ],
    "qingling": [
        'Qingling',
    ],
    "rayen": [
        'Rayen R3', 'Rayen V5 automatic',
    ],
    "reera": [
        'Reera',
    ],
    "respect": [
        'Respect Prime',
    ],
    "rich": [
        'Rich',
    ],
    "rigan": [
        'Rigan Coupa exclusive', 'Rigan Coupa royal',
    ],
    "roewe": [
        'Roewe',
    ],
    "rollsroyce": [
        'Rollsroyce',
    ],
    "rover": [
        'Rover',
    ],
    "seat": [
        'Seat',
    ],
    "sinogold": [
        'Sinogold',
    ],
    "skoda": [
        'Škoda Octavia', 'Škoda Superb',
    ],
    "skywell": [
        'SKYWELL',
    ],
    "smart": [
        'Smart',
    ],
    "soueast": [
        'Soueast',
    ],
    "ssangyong": [
        'SsangYong Actyon', 'SsangYong Tivoli', 'SsangYong Tivoli-IR',
        'SsangYong Chairman', 'SsangYong Khan', 'SsangYong Rexton',
        'SsangYong Rexton-ir', 'SsangYong Rodius', 'SsangYong Kyron',
        'SsangYong Korando', 'SsangYong Musso', 'SsangYong new actyon',
        'SsangYong new-korando Optimum', 'SsangYong new-korando Premium-normal',
    ],
    "subaru": [
        'Subaru Forester', 'Subaru Legacy', 'Subaru Way Wave',
        'Subaru X7',
    ],
    "swm": [
        'SWM',
    ],
    "tigard": [
        'Tigard Tissan S05 1.5L Turbo', 'Tigard L7 PLUS', 'Tigard X35 PRIME',
    ],
    "xpeng": [
        'XPENG G6', 'XPENG G9', 'XPENG G9 AWD',
    ],
    "xtrim": [
        'XTRIM Xtrim (EXEED) LX', 'XTRIM Xtrim (EXEED) TXL',
    ],
    "zamyad": [
        'Zamyad EX', 'Zamyad Padra-normal', 'Zamyad Padra Plus',
        'Zamyad Dorka', 'Zamyad Zagros', 'Zamyad Shooka',
        'Zamyad Karoon', 'Zamyad Z28', 'Zamyad Z 24 Optional',
        'Zamyad Z 24 Petrol-normal', 'Zamyad Z 24 Petrol Optional', 'Zamyad Z 24 Bi-fuel Optional',
        'Zamyad Z 24 diesel', 'Zamyad Z 28',
    ],
    "zotye": [
        'Zotye DL5',
    ],
    "zx_auto": [
        'ZX_AUTO',
    ],
}

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "fa-IR,fa;q=0.9,en-US;q=0.8,en;q=0.7",
    "Content-Type": "application/json",
    "Origin": "https://divar.ir",
    "Referer": "https://divar.ir/",
    "Sec-Fetch-Dest": "empty",
    "Sec-Fetch-Mode": "cors",
    "Sec-Fetch-Site": "same-site",
    # پاسخ detail جدید دیوار با این headerها همان ساختار کامل وب را می‌دهد.
    "X-Render-Type": "CSR",
    "X-Standard-Divar-Error": "true",
}

# ====================== دیتابیس ======================
def get_db():
    """اتصال به SQLite — پوشه ساخته می‌شود؛ بدون WAL (امن برای هاست اشتراکی)."""
    db_path = DATABASE_PATH
    parent = os.path.dirname(os.path.abspath(db_path))
    if parent and not os.path.isdir(parent):
        try:
            os.makedirs(parent, exist_ok=True)
            print(f"[DB] created directory: {parent}")
        except Exception as e:
            print(f"[DB] cannot create directory {parent}: {e}")
            fallback = os.path.join(os.path.dirname(os.path.abspath(__file__)), "divar.db")
            print(f"[DB] falling back to {fallback}")
            db_path = fallback
    # timeout جلوگیری از قفل ابدی؛ check_same_thread برای Flask
    conn = sqlite3.connect(db_path, timeout=30, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    try:
        # DELETE امن‌تر از WAL روی cPanel / NFS / shared hosting است
        conn.execute("PRAGMA journal_mode=DELETE")
        conn.execute("PRAGMA busy_timeout=30000")
        conn.execute("PRAGMA foreign_keys=ON")
        conn.execute("PRAGMA synchronous=NORMAL")
    except Exception:
        pass
    return conn


def ensure_admin(conn):
    row = conn.execute("SELECT 1 FROM users WHERE role='admin' LIMIT 1").fetchone()
    if not row:
        conn.execute(
            "INSERT INTO users (username, password_hash, role, is_active, plan, subscription_expires_at, max_monitors, created_at) VALUES (?,?,?,?,?,?,?,?)",
            (ADMIN_USERNAME, generate_password_hash(ADMIN_PASSWORD), "admin", 1, "vip", None, 9999, datetime.now().isoformat()),
        )
        conn.commit()
        print(f"[INIT] Admin account created -> username: {ADMIN_USERNAME} password: {ADMIN_PASSWORD} (CHANGE THIS)")


def init_db():
    """ایجاد کامل جداول، مایگریشن، ادمین و داده‌های پیش‌فرض — از صفر هم کار می‌کند."""
    try:
        conn = get_db()
    except Exception as e:
        print("[INIT] FATAL cannot open database:", e)
        return

    try:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS users (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                username TEXT UNIQUE NOT NULL,
                password_hash TEXT NOT NULL,
                role TEXT DEFAULT 'user',
                is_active INTEGER DEFAULT 1,
                plan TEXT DEFAULT 'free',
                subscription_expires_at TEXT,
                max_monitors INTEGER DEFAULT 1,
                telegram_chat_id TEXT,
                notify_email TEXT,
                created_at TEXT,
                last_login TEXT
            );
            CREATE TABLE IF NOT EXISTS monitors (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER,
                name TEXT NOT NULL,
                category TEXT NOT NULL,
                settings_json TEXT NOT NULL,
                is_active INTEGER DEFAULT 1,
                track_price INTEGER DEFAULT 0,
                created_at TEXT
            );
            CREATE TABLE IF NOT EXISTS seen_ads (
                token TEXT PRIMARY KEY,
                monitor_id INTEGER,
                title TEXT,
                price TEXT,
                price_num INTEGER,
                link TEXT,
                found_at TEXT,
                last_checked TEXT
            );
            CREATE TABLE IF NOT EXISTS history (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                monitor_id INTEGER,
                monitor_name TEXT,
                token TEXT,
                title TEXT,
                price TEXT,
                link TEXT,
                matched_keywords TEXT,
                value_score REAL,
                found_at TEXT
            );
            CREATE TABLE IF NOT EXISTS price_changes (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                token TEXT,
                monitor_id INTEGER,
                title TEXT,
                old_price TEXT,
                new_price TEXT,
                link TEXT,
                changed_at TEXT
            );
            CREATE TABLE IF NOT EXISTS meta (
                key TEXT PRIMARY KEY,
                value TEXT
            );
            CREATE TABLE IF NOT EXISTS payment_requests (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                plan_id TEXT NOT NULL,
                plan_name TEXT,
                amount INTEGER,
                days INTEGER,
                max_monitors INTEGER,
                phone TEXT,
                receipt_path TEXT,
                status TEXT DEFAULT 'pending',
                admin_note TEXT,
                created_at TEXT,
                reviewed_at TEXT,
                reviewed_by INTEGER
            );
            CREATE TABLE IF NOT EXISTS support_messages (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                sender TEXT NOT NULL,
                message TEXT NOT NULL,
                is_read INTEGER DEFAULT 0,
                created_at TEXT
            );
            CREATE TABLE IF NOT EXISTS site_content (
                key TEXT PRIMARY KEY,
                title TEXT,
                body TEXT,
                updated_at TEXT
            );
            CREATE TABLE IF NOT EXISTS push_subscriptions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                endpoint TEXT NOT NULL UNIQUE,
                p256dh TEXT,
                auth TEXT,
                user_agent TEXT,
                created_at TEXT,
                last_seen TEXT
            );
            CREATE TABLE IF NOT EXISTS notifications (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                title TEXT,
                body TEXT,
                link TEXT,
                monitor_id INTEGER,
                is_read INTEGER DEFAULT 0,
                created_at TEXT
            );
            """
        )
        conn.commit()
    except Exception as e:
        print("[INIT] schema create error:", e)
        try:
            conn.close()
        except Exception:
            pass
        return

    # migrations (برای دیتابیس‌های قدیمی)
    try:
        cols = [r[1] for r in conn.execute("PRAGMA table_info(monitors)").fetchall()]
        if "track_price" not in cols:
            conn.execute("ALTER TABLE monitors ADD COLUMN track_price INTEGER DEFAULT 0")
        if "user_id" not in cols:
            conn.execute("ALTER TABLE monitors ADD COLUMN user_id INTEGER")

        cols2 = [r[1] for r in conn.execute("PRAGMA table_info(seen_ads)").fetchall()]
        if "price_num" not in cols2:
            conn.execute("ALTER TABLE seen_ads ADD COLUMN price_num INTEGER")
        if "last_checked" not in cols2:
            conn.execute("ALTER TABLE seen_ads ADD COLUMN last_checked TEXT")

        history_cols = [r[1] for r in conn.execute("PRAGMA table_info(history)").fetchall()]
        if "value_score" not in history_cols:
            conn.execute("ALTER TABLE history ADD COLUMN value_score REAL")

        cols3 = [r[1] for r in conn.execute("PRAGMA table_info(users)").fetchall()]
        if "telegram_chat_id" not in cols3:
            conn.execute("ALTER TABLE users ADD COLUMN telegram_chat_id TEXT")
        if "notify_email" not in cols3:
            conn.execute("ALTER TABLE users ADD COLUMN notify_email TEXT")
        if "last_login" not in cols3:
            conn.execute("ALTER TABLE users ADD COLUMN last_login TEXT")
        conn.commit()
    except Exception as e:
        print("[INIT] db migrate:", e)

    try:
        ensure_admin(conn)
    except Exception as e:
        print("[INIT] ensure_admin:", e)

    try:
        seed_site_defaults(conn)
        conn.commit()
    except Exception as e:
        print("[INIT] seed_site_defaults:", e)

    try:
        ensure_vapid_keys(conn)
    except Exception as _v:
        print("[INIT] vapid init:", _v)

    # assign orphan monitors (created before user system) to admin
    try:
        admin_row = conn.execute("SELECT id FROM users WHERE role='admin' LIMIT 1").fetchone()
        if admin_row:
            conn.execute("UPDATE monitors SET user_id=? WHERE user_id IS NULL", (admin_row["id"],))
            conn.commit()
    except Exception as e:
        print("[INIT] orphan monitor assign error:", e)

    try:
        conn.commit()
    except Exception:
        pass
    try:
        conn.close()
    except Exception:
        pass
    print("[INIT] database ready")


def get_meta(conn, key, default=None):
    row = conn.execute("SELECT value FROM meta WHERE key=?", (key,)).fetchone()
    return row["value"] if row else default


def set_meta(conn, key, value):
    conn.execute(
        "INSERT INTO meta (key, value) VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
        (key, value),
    )


def seed_site_defaults(conn):
    """تنظیمات پیش‌فرض سایت، پلن‌ها و صفحات محتوا"""
    defaults_meta = {
        "payment_card_number": "6037-XXXX-XXXX-XXXX",
        "payment_card_owner": "نام صاحب کارت",
        "payment_bank_name": "بانک",
        "support_phone": "",
        "plans_json": json.dumps([
            {"id": "monthly", "name": "ماهانه", "days": 30, "price": 890000, "max_monitors": 3, "desc": "مناسب شروع"},
            {"id": "quarterly", "name": "سه‌ماهه", "days": 90, "price": 2290000, "max_monitors": 5, "desc": "پیشنهادی — ۲۰٪ تخفیف"},
            {"id": "yearly", "name": "سالانه", "days": 365, "price": 6990000, "max_monitors": 10, "desc": "به‌صرفه‌ترین"},
        ], ensure_ascii=False),
    }
    for k, v in defaults_meta.items():
        if get_meta(conn, k) is None:
            set_meta(conn, k, v)

    contents = {
        "about": (
            "درباره ما",
            """دیده‌بان دیوار یک سرویس رصد هوشمند آگهی‌های دیوار است.

ما کمک می‌کنیم فرصت‌های خوب خرید موبایل، خودرو و املاک را زودتر از بقیه ببینید — بدون اینکه ساعت‌ها صفحه دیوار را رفرش کنید.

چه کار می‌کنیم؟
• جستجوی پیشرفته با فیلترهای دقیق و کلمات کلیدی
• مانیتور خودکار ۲۴ ساعته روی شرایط دلخواه شما
• اعلان از طریق ایمیل، تلگرام و نوتیفیکیشن مرورگر
• امتیازدهی هوشمند برای تشخیص آگهی‌های باارزش‌تر

تیم ما روی سرعت، دقت و تجربه کاربری تمرکز دارد تا شما فقط روی تصمیم خرید تمرکز کنید.

برای ارتباط با ما از بخش پشتیبانی داخل پنل استفاده کنید."""
        ),
        "terms": (
            "قوانین و مقررات",
            """با ثبت‌نام و استفاده از دیده‌بان دیوار، این شرایط را می‌پذیرید:

۱. استفاده قانونی
سرویس فقط برای اهداف قانونی و شخصی مجاز است. هرگونه سوءاستفاده، اسپم، یا تلاش برای اختلال در سامانه ممنوع است.

۲. مسئولیت اطلاعات آگهی‌ها
آگهی‌ها از بستر دیوار جمع‌آوری می‌شوند. صحت قیمت، مشخصات و موجود بودن کالا بر عهده آگهی‌دهنده و سایت دیوار است. دیده‌بان دیوار تضمینی روی معامله نمی‌دهد.

۳. اشتراک و پرداخت
اشتراک پس از بررسی و تأیید رسید پرداخت توسط پشتیبانی فعال می‌شود. زمان بررسی معمولاً کوتاه است اما ممکن است متغیر باشد.

۴. حساب کاربری
حفظ امنیت نام کاربری و رمز عبور بر عهده شماست. حساب‌های مشکوک یا سوءاستفاده‌کننده مسدود می‌شوند.

۵. محدودیت‌ها
تعداد مانیتور و امکانات بر اساس پلن اشتراک شما تعیین می‌شود. دوره آزمایشی محدودیت‌های خاص خود را دارد.

۶. تغییرات سرویس
ممکن است امکانات، قیمت‌ها یا قوانین به‌روزرسانی شوند. ادامه استفاده به معنای پذیرش نسخه به‌روز قوانین است.

۷. حریم خصوصی
اطلاعات حساب و تنظیمات اعلان برای ارائه سرویس نگهداری می‌شود و به اشخاص ثالث فروخته نمی‌شود."""
        ),
        "guide": (
            "راهنمای استفاده",
            """شروع سریع با دیده‌بان دیوار:

۱) ورود به داشبورد
پس از ثبت‌نام یا ورود، صفحه اصلی با سه بخش در دسترس است: جستجو، مانیتورها و تاریخچه.

۲) انتخاب دسته
یکی از دسته‌های موبایل، خودرو، فروش مسکونی یا اجاره مسکونی را انتخاب کنید.

۳) انتخاب مدل‌ها
از لیست برندها، مدل‌های موردنظر را علامت بزنید. می‌توانید «انتخاب همه» را برای هر برند بزنید.

۴) فیلترها
بازه قیمت، شهر، کلمات کلیدی مثبت و کلمات منفی (حذف قطعی) را تنظیم کنید.

۵) جستجوی هوشمند
با فعال کردن جستجوی هوشمند، آگهی‌ها بر اساس قیمت بازار، تازگی، کیفیت متن و ریسک امتیاز می‌گیرند. حالت‌های محافظه‌کار / متعادل / باز را امتحان کنید.

۶) ذخیره مانیتور
بعد از تنظیم فیلترها، «ذخیره به‌عنوان مانیتور» را بزنید. سیستم در پس‌زمینه جستجو می‌کند و آگهی‌های جدید را اعلام می‌کند.

۷) اعلان‌ها
از منوی اعلان‌ها، همه پیام‌های ارسال‌شده را ببینید. روی هر مورد کلیک کنید تا به آگهی بروید.
در حساب کاربری می‌توانید ایمیل، تلگرام و نوتیف مرورگر را تنظیم کنید.

۸) تاریخچه
آگهی‌هایی که مانیتور پیدا کرده در بخش تاریخچه ثبت می‌شوند تا بعداً مرور کنید.

نکته: برای کار کردن مانیتور و اعلان‌ها، اشتراک باید فعال باشد."""
        ),
        "buy_guide": (
            "راهنمای خرید اشتراک",
            """فعال‌سازی اشتراک در چند قدم:

۱) انتخاب پلن
از صفحه «خرید اشتراک» پلن ماهانه، سه‌ماهه یا سالانه را انتخاب کنید. سقف تعداد مانیتور هر پلن متفاوت است.

۲) واریز وجه
مبلغ پلن را دقیقاً به شماره کارت نمایش‌داده‌شده واریز کنید. بهتر است مبلغ را بدون گرد کردن بفرستید تا تطبیق رسید سریع‌تر شود.

۳) ارسال رسید
شماره موبایل خود را وارد کنید و تصویر (یا PDF) رسید را آپلود کنید، سپس درخواست را ارسال کنید.

۴) تأیید پشتیبانی
تیم پشتیبانی رسید را بررسی می‌کند. پس از تأیید، اشتراک و سقف مانیتور به‌صورت خودکار فعال می‌شود.

۵) پیگیری وضعیت
همان صفحه خرید، لیست درخواست‌های قبلی و وضعیت آن‌ها (در انتظار / تأیید / رد) را نشان می‌دهد.

۶) تمدید
قبل از انقضا می‌توانید دوباره همان مسیر را طی کنید. در صورت رد شدن درخواست، از پشتیبانی دلیل را بپرسید و در صورت نیاز دوباره ارسال کنید.

اگر در واریز یا آپلود رسید مشکل داشتید، از بخش چت پشتیبانی پیام بگذارید."""
        ),
    }
    for key, (title, body) in contents.items():
        row = conn.execute("SELECT body FROM site_content WHERE key=?", (key,)).fetchone()
        if not row:
            conn.execute(
                "INSERT INTO site_content (key, title, body, updated_at) VALUES (?,?,?,?)",
                (key, title, body, datetime.now().isoformat()),
            )
        else:
            # ارتقای محتوای کوتاه/قدیمی فقط اگر خیلی کوتاه باشد
            old_body = (row["body"] or "").strip()
            if len(old_body) < 120:
                conn.execute(
                    "UPDATE site_content SET title=?, body=?, updated_at=? WHERE key=?",
                    (title, body, datetime.now().isoformat(), key),
                )
    conn.commit()


def get_plans(conn=None):
    close = False
    if conn is None:
        conn = get_db()
        close = True
    try:
        raw = get_meta(conn, "plans_json") or "[]"
        plans = json.loads(raw)
        return plans if isinstance(plans, list) else []
    except Exception:
        return []
    finally:
        if close:
            conn.close()


def get_payment_info(conn=None):
    close = False
    if conn is None:
        conn = get_db()
        close = True
    try:
        return {
            "card_number": get_meta(conn, "payment_card_number") or "",
            "card_owner": get_meta(conn, "payment_card_owner") or "",
            "bank_name": get_meta(conn, "payment_bank_name") or "",
            "support_phone": get_meta(conn, "support_phone") or "",
        }
    finally:
        if close:
            conn.close()


def get_site_content(key: str):
    conn = get_db()
    row = conn.execute("SELECT * FROM site_content WHERE key=?", (key,)).fetchone()
    conn.close()
    if not row:
        return {"key": key, "title": key, "body": ""}
    return dict(row)


# ====================== auth helpers ======================
def get_current_user():
    try:
        uid = session.get("user_id")
        if not uid:
            return None
        conn = get_db()
        try:
            row = conn.execute("SELECT * FROM users WHERE id=?", (uid,)).fetchone()
        finally:
            conn.close()
        return row
    except Exception as e:
        print("[AUTH] get_current_user error:", e)
        return None


def is_sub_active(user) -> bool:
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


def login_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        user = get_current_user()
        if not user or not user["is_active"]:
            session.clear()
            return redirect(url_for("login"))
        return f(*args, **kwargs)
    return decorated


def api_login_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        user = get_current_user()
        if not user or not user["is_active"]:
            return jsonify({"success": False, "error": "unauthorized"}), 401
        return f(*args, **kwargs)
    return decorated


def admin_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        user = get_current_user()
        if not user or user["role"] != "admin":
            return redirect(url_for("login"))
        return f(*args, **kwargs)
    return decorated


def api_admin_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        user = get_current_user()
        if not user or user["role"] != "admin":
            return jsonify({"success": False, "error": "forbidden"}), 403
        return f(*args, **kwargs)
    return decorated


def subscription_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        user = get_current_user()
        if not is_sub_active(user):
            return jsonify({
                "success": False,
                "error": "subscription_expired",
                "message": "اشتراک شما فعال نیست یا منقضی شده. برای تمدید به صفحه خرید اشتراک بروید.",
                "redirect": "/subscribe",
            }), 402
        return f(*args, **kwargs)
    return decorated


# ====================== توابع کمکی ======================
PERSIAN_DIGITS = "۰۱۲۳۴۵۶۷۸۹"
ARABIC_DIGITS = "٠١٢٣٤٥٦٧٨٩"
ASCII_DIGITS = "0123456789"


def normalize_digits(text: str) -> str:
    if not text:
        return text
    trans = {}
    for i, ch in enumerate(PERSIAN_DIGITS):
        trans[ch] = ASCII_DIGITS[i]
    for i, ch in enumerate(ARABIC_DIGITS):
        trans[ch] = ASCII_DIGITS[i]
    return "".join(trans.get(c, c) for c in text)


def parse_money_amount(value: Any) -> Optional[int]:
    """عدد پول را از متن‌های دیوار با رقم/واحدهای فارسی استخراج می‌کند.

    نمونه‌های پشتیبانی‌شده: ``۲٬۳۵۰٬۰۰۰٬۰۰۰``، ``۳۰۰ میلیون`` و
    ``1.5 میلیارد``. برخلاف نسخه‌ی قبلی، چند عددِ یک متن (مثل ودیعه و
    اجاره) به اشتباه به هم نمی‌چسبند.
    """
    if value is None or value == "":
        return None
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        try:
            return int(round(float(value)))
        except (TypeError, ValueError, OverflowError):
            return None

    text = normalize_digits(str(value)).replace("٬", ",")
    # جداکننده‌ی گروهی و عدد اعشاری هر دو در آگهی‌ها دیده می‌شوند.
    match = re.search(
        r"(?P<number>\d+(?:,\d{3})*(?:\.\d+)?)\s*"
        r"(?P<unit>هزار|میلیون|میلیارد|هزارگان|thousand|million|billion)?",
        text,
        flags=re.IGNORECASE,
    )
    if not match:
        return None
    try:
        number = float(match.group("number").replace(",", ""))
    except (TypeError, ValueError):
        return None

    unit = (match.group("unit") or "").lower()
    multiplier = {
        "هزار": 1_000,
        "هزارگان": 1_000,
        "thousand": 1_000,
        "میلیون": 1_000_000,
        "million": 1_000_000,
        "میلیارد": 1_000_000_000,
        "billion": 1_000_000_000,
    }.get(unit, 1)
    try:
        result = int(round(number * multiplier))
    except (TypeError, ValueError, OverflowError):
        return None
    return result if result >= 0 else None


def parse_price(price_str: str) -> Optional[int]:
    """نام سازگار قدیمی برای استخراج مبلغ؛ برای متن چندرقمی، مبلغ اول را می‌خواند."""
    return parse_money_amount(price_str)


def format_grouped_number(value: Any) -> str:
    """نمایش خوانای اعداد با جداکننده‌ی هزارگان (بدون گرد کردن مبلغ)."""
    try:
        if value is None or value == "":
            return ""
        return f"{int(round(float(value))):,}"
    except (TypeError, ValueError, OverflowError):
        return ""


def format_toman(value: Any) -> str:
    grouped = format_grouped_number(value)
    return f"{grouped} تومان" if grouped else ""


def normalize_city_ids(value: Any, default_city: str = "5") -> List[str]:
    """ورودی شهرِ فرم و مانیتور را به فهرست شناسه‌های معتبر تبدیل می‌کند.

    فهرست خالی عمداً به معنی «همه‌ی شهرها» است و در payload بدون محدودیت
    شهری ارسال می‌شود. این تابع تنظیمات مانیتورهای قدیمی را نیز سازگار نگه
    می‌دارد.
    """
    if isinstance(value, (list, tuple, set)):
        raw_values = list(value)
    elif value is None:
        raw_values = [default_city]
    else:
        raw_values = str(value).replace("،", ",").split(",")

    cleaned = []
    for raw in raw_values:
        item = str(raw or "").strip()
        if not item:
            continue
        if item.lower() in ALL_CITY_VALUES:
            return []
        if item.isdigit():
            cleaned.append(item)

    # مقدار خالیِ صریح از کلاینت («all») به همه‌ی شهرها اشاره دارد؛ ولی برای
    # مانیتورهای قدیمی که اصلاً city ندارند، شهر پیش‌فرض قبلی حفظ می‌شود.
    if cleaned:
        return list(dict.fromkeys(cleaned))
    if value in ("", [], (), set()):
        return []
    return [str(default_city)]


def _normalize_fa_digits(text: str) -> str:
    """تبدیل ارقام فارسی/عربی به انگلیسی + نرمال‌سازی فاصله و نیم‌فاصله"""
    if not text:
        return ""
    fa = "۰۱۲۳۴۵۶۷۸۹٠١٢٣٤٥٦٧٨٩"
    en = "01234567890123456789"
    trans = str.maketrans(fa, en)
    text = text.translate(trans)
    text = text.lower()
    # نیم‌فاصله/کنترل‌های جهت‌دار و اعراب در توضیحات کپی‌شده رایج‌اند و نباید
    # تطبیق عبارت کاربر را بشکنند.
    text = re.sub(r"[\u200b-\u200f\u202a-\u202e]", " ", text)
    text = re.sub(r"[\u064b-\u065f\u0670]", "", text)
    text = text.replace("ـ", " ").replace("-", " ").replace("_", " ")
    text = text.replace("ي", "ی").replace("ك", "ک").replace("ة", "ه").replace("ۀ", "ه")
    return re.sub(r"\s+", " ", text).strip()


# قرارداد رایج اجاره: هر ۱۰۰ میلیون تومان ودیعه، معادل ۳ میلیون تومان اجاره‌ی ماهانه است.
DEPOSIT_STEP_TOMAN = 100_000_000
RENT_PER_DEPOSIT_STEP_TOMAN = 3_000_000


def rental_equivalents(credit_num: Optional[int], rent_num: Optional[int]) -> Dict[str, Optional[int]]:
    """ودیعه/اجاره را با نرخ استاندارد به دو نمایش قابل‌مقایسه تبدیل می‌کند."""
    credit = int(credit_num or 0) if credit_num is not None else 0
    rent = int(rent_num or 0) if rent_num is not None else 0
    if credit <= 0 and rent <= 0:
        return {
            "rent_equivalent_num": None,
            "deposit_equivalent_num": None,
            "conversion_rule": "",
            "rental_equivalent_display": "",
            "deposit_equivalent_display": "",
        }

    # معادل اجاره‌ی ماهانه = اجاره‌ی اعلام‌شده + اجاره‌ی حاصل از ودیعه.
    rent_equivalent = rent + round(credit * RENT_PER_DEPOSIT_STEP_TOMAN / DEPOSIT_STEP_TOMAN)
    # معادل ودیعه نیز برای مقایسه‌ی رتبه‌بندی استفاده می‌شود.
    deposit_equivalent = credit + round(rent * DEPOSIT_STEP_TOMAN / RENT_PER_DEPOSIT_STEP_TOMAN)
    rule = (
        f"نرخ تبدیل: هر {format_toman(DEPOSIT_STEP_TOMAN)} ودیعه ≈ "
        f"{format_toman(RENT_PER_DEPOSIT_STEP_TOMAN)} اجاره ماهانه"
    )
    return {
        "rent_equivalent_num": int(rent_equivalent),
        "deposit_equivalent_num": int(deposit_equivalent),
        "conversion_rule": rule,
        "rental_equivalent_display": f"معادل اجاره ماهانه: {format_toman(rent_equivalent)}",
        "deposit_equivalent_display": f"معادل ودیعه: {format_toman(deposit_equivalent)}",
    }


def _extract_labeled_money(text: str, label_pattern: str) -> Optional[int]:
    """مبلغ قبل یا بعد از یک برچسب مانند «ودیعه» و «اجاره» را می‌خواند."""
    if not text:
        return None
    normalized = normalize_digits(str(text)).replace("٬", ",")
    amount = r"(\d+(?:,\d{3})*(?:\.\d+)?\s*(?:هزار|میلیون|میلیارد)?)"
    # «ودیعه: ۳۰۰ میلیون» و «۳۰۰ میلیون ودیعه» هر دو در دیوار رایج‌اند.
    patterns = [
        rf"(?:{label_pattern})\s*(?:ماهانه)?\s*(?:تا|از)?\s*[:：\-،]?\s*{amount}",
        rf"{amount}\s*(?:تومان)?\s*(?:{label_pattern})",
    ]
    for pattern in patterns:
        match = re.search(pattern, normalized, flags=re.IGNORECASE)
        if match:
            # گروه آخر، مبلغ است؛ در الگوی معکوس هم گروه اول همین مقدار است.
            for group in reversed(match.groups()):
                parsed = parse_money_amount(group)
                if parsed is not None:
                    return parsed
    return None


def format_real_estate_price(credit_num: Optional[int], rent_num: Optional[int], fallback: str = "") -> str:
    """قیمت اجاره را همیشه با رقم‌های کامل و جداشده نمایش می‌دهد."""
    parts = []
    if credit_num is not None and int(credit_num) >= 0:
        parts.append(f"ودیعه {format_toman(credit_num)}")
    if rent_num is not None and int(rent_num) >= 0:
        parts.append(f"اجاره {format_toman(rent_num)}")
    if parts:
        return " · ".join(parts)
    return fallback or "توافقی"


def is_model_match(title: str, model: str) -> bool:
    """
    تطبیق دقیق مدل با عنوان آگهی.
    جلوگیری از قاطی شدن مدل‌های هم‌خانواده
    (مثلاً iphone 11 وقتی iphone 14 pro max انتخاب شده).
    """
    if not title or not model:
        return False
    title_n = _normalize_fa_digits(title)
    model_n = _normalize_fa_digits(model)
    title_compact = title_n.replace(" ", "")
    model_compact = model_n.replace(" ", "")

    # ---- شماره/کد مدل ----
    model_nums = re.findall(r"\d{2,4}", model_n)
    model_nums = sorted(set(model_nums), key=len, reverse=True)
    if model_nums:
        main_num = model_nums[0]
        title_nums = set(re.findall(r"\d{1,4}", title_n))
        if main_num not in title_nums and main_num not in title_compact:
            return False

    def _has_any(text, forms):
        tc = text.replace(" ", "")
        for f in forms:
            if f.replace(" ", "") in tc or f in text:
                return True
        return False

    pro_max_forms = ["pro max", "promax", "پرومکس", "پرو مکس"]
    plus_forms = ["plus", "پلاس"]
    mini_forms = ["mini", "مینی"]
    ultra_forms = ["ultra", "اولترا"]

    model_pro_max = _has_any(model_n, pro_max_forms)
    model_pro = (not model_pro_max) and (
        bool(re.search(r"\bpro\b", model_n)) or ("پرو" in model_n and "مکس" not in model_n)
    )
    model_plus = _has_any(model_n, plus_forms)
    model_mini = _has_any(model_n, mini_forms)
    model_ultra = _has_any(model_n, ultra_forms)

    title_pro_max = _has_any(title_n, pro_max_forms)
    title_pro = (not title_pro_max) and (
        bool(re.search(r"\bpro\b", title_n)) or ("پرو" in title_n and "مکس" not in title_n and "پرومکس" not in title_compact)
    )
    title_plus = _has_any(title_n, plus_forms)
    title_mini = _has_any(title_n, mini_forms)
    title_ultra = _has_any(title_n, ultra_forms)

    if model_pro_max and not title_pro_max:
        return False
    if model_pro and not title_pro:
        return False
    if model_plus and not title_plus:
        return False
    if model_mini and not title_mini:
        return False
    if model_ultra and not title_ultra:
        return False

    model_has_variant = model_pro_max or model_pro or model_plus or model_mini or model_ultra
    if not model_has_variant:
        if title_pro_max or title_pro or title_plus or title_mini or title_ultra:
            return False
    if model_pro and title_pro_max:
        return False

    brand_hints = {
        "iphone": ["iphone", "آیفون", "ایفون"],
        "galaxy": ["galaxy", "گلکسی", "سامسونگ", "samsung"],
        "samsung": ["samsung", "سامسونگ", "galaxy", "گلکسی"],
        "xiaomi": ["xiaomi", "شیائومی", "شیاومی", "redmi", "poco", "ردمی"],
        "redmi": ["redmi", "ردمی", "شیائومی"],
        "poco": ["poco", "پوکو"],
        "pride": ["pride", "پراید"],
        "peugeot": ["peugeot", "پژو"],
        "samand": ["samand", "سمند"],
        "tiba": ["tiba", "تیبا"],
        "saina": ["saina", "ساینا"],
        "shahin": ["shahin", "شاهین"],
        "dena": ["dena", "دنا"],
    }
    for brand, hints in brand_hints.items():
        if brand in model_n:
            if not any(h in title_n for h in hints):
                if not model_nums:
                    return False
            break

    return True



def _clean_public_text(value: Any) -> str:
    """متن عمومی API را بدون HTML و فاصله‌های مزاحم برمی‌گرداند."""
    if not isinstance(value, str):
        return ""
    text = unescape(value).replace("<br>", "\n").replace("<br/>", "\n").replace("<br />", "\n")
    text = re.sub(r"<[^>]+>", " ", text)
    text = text.replace("\r", "\n").replace("\u200c", " ")
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n\s*\n+", "\n", text)
    return text.strip()


def _path_value(obj: Any, *path: str) -> Any:
    current = obj
    for key in path:
        if not isinstance(current, dict):
            return None
        current = current.get(key)
    return current


def _text_from_description_widget(widget: Dict[str, Any]) -> List[str]:
    """همه‌ی شکل‌های متداول ویجت توضیحات در API وب دیوار را می‌خواند."""
    out: List[str] = []

    def walk(node: Any, depth: int = 0) -> None:
        if depth > 5 or node is None:
            return
        if isinstance(node, str):
            cleaned = _clean_public_text(node)
            if cleaned and not cleaned.startswith("http"):
                out.append(cleaned)
            return
        if isinstance(node, list):
            for child in node:
                walk(child, depth + 1)
            return
        if not isinstance(node, dict):
            return
        # اول کلیدهای معنا‌دار را بخوان تا متن اصلی قبل از label/metadata بیاید.
        for key in ("description", "description_text", "text", "content", "body", "value"):
            if key in node:
                walk(node.get(key), depth + 1)
        for key, child in node.items():
            if key not in {"description", "description_text", "text", "content", "body", "value"}:
                walk(child, depth + 1)

    walk(widget.get("data") if isinstance(widget, dict) else widget)
    return out


_DESCRIPTION_CONTEXT_MARKERS = (
    "description", "description_text", "post_description", "ad_description",
    "توضیحات", "توضیح", "شرح آگهی", "متن آگهی", "شرح",
)
_GENERIC_DESCRIPTION_LABELS = {
    "description", "description text", "توضیحات", "توضیحات آگهی",
    "شرح", "شرح آگهی", "متن آگهی", "توضیح",
}
_GENERIC_DESCRIPTION_LABELS_NORMALIZED = frozenset(
    _normalize_fa_digits(item) for item in _GENERIC_DESCRIPTION_LABELS
)


def _is_description_context(value: Any) -> bool:
    normalized = _normalize_fa_digits(str(value or ""))
    return bool(normalized) and any(
        _normalize_fa_digits(marker) in normalized
        for marker in _DESCRIPTION_CONTEXT_MARKERS
    )


def _is_generic_description_label(value: str) -> bool:
    return _normalize_fa_digits(value) in _GENERIC_DESCRIPTION_LABELS_NORMALIZED


def _append_description_candidate(candidates: List[Tuple[int, str]], value: Any, priority: int) -> None:
    """یک متن/ساختار احتمالی توضیح را بدون واردکردن URL یا label ثبت می‌کند."""
    if isinstance(value, str):
        cleaned = _clean_public_text(value)
        if (
            cleaned
            and not cleaned.lower().startswith(("http://", "https://"))
            and not _is_generic_description_label(cleaned)
            # شناسه‌های machine-readable مانند DESCRIPTION_ROW متن آگهی نیستند.
            and not re.fullmatch(r"[A-Z][A-Z0-9_]{2,}", cleaned)
        ):
            candidates.append((priority, cleaned))
        return
    if isinstance(value, (dict, list)):
        for item in _text_from_description_widget({"data": value}):
            _append_description_candidate(candidates, item, priority)


def _rank_description_candidates(candidates: List[Tuple[int, str]]) -> List[str]:
    """نسخه‌های تکراری/labelها را حذف و متن اصلی را پیش از metadata قرار می‌دهد."""
    by_text: Dict[str, Tuple[int, str]] = {}
    for priority, text in candidates:
        cleaned = _clean_public_text(text)
        normalized = _normalize_fa_digits(cleaned)
        if not normalized or _is_generic_description_label(cleaned):
            continue
        previous = by_text.get(normalized)
        if previous is None or priority > previous[0] or (priority == previous[0] and len(cleaned) > len(previous[1])):
            by_text[normalized] = (priority, cleaned)

    # در یک منبع هم‌اولویت، احتمال متن توضیح برای متن کامل‌تر از یک caption
    # بسیار بیشتر است. اولویت منبع (direct > description widget > SEO) حفظ می‌شود.
    ranked = sorted(
        by_text.values(),
        key=lambda item: (item[0], min(len(item[1]), 2500), item[1].count(" ")),
        reverse=True,
    )
    return [text for _, text in ranked]


def extract_post_description_candidates(payload: Dict[str, Any]) -> List[str]:
    """همه‌ی متن‌های قابل اتکای توضیحات را از نسخه‌های مختلف detail API می‌خواند.

    دیوار گاهی در یک widget هم ``title: توضیحات`` و هم متن واقعی را می‌فرستد.
    نسخه‌ی قبلی اولین string را برمی‌گرداند و در نتیجه همان caption به‌جای متن
    ذخیره می‌شد. این تابع متن‌ها را رتبه‌بندی می‌کند و علاوه بر متن نمایشی،
    متن‌های هم‌خانواده را برای تطبیق کلیدواژه نگه می‌دارد.
    """
    if not isinstance(payload, dict):
        return []

    candidates: List[Tuple[int, str]] = []
    direct_paths = (
        ("description",),
        ("data", "description"),
        ("post", "description"),
        ("post", "data", "description"),
        ("widgets", "description"),
        ("data", "widgets", "description"),
    )
    for path in direct_paths:
        _append_description_candidate(candidates, _path_value(payload, *path), 100)

    # بخش/ویجت‌های شناخته‌شده‌ی endpoint جدید و قدیمی.
    for sections_path in (("sections",), ("data", "sections")):
        sections = _path_value(payload, *sections_path)
        if not isinstance(sections, list):
            continue
        for section in sections:
            if not isinstance(section, dict):
                continue
            section_name = str(section.get("section_name") or section.get("name") or "")
            section_context = _is_description_context(
                section_name or section.get("title")
            )
            widgets = section.get("widgets") or section.get("widget_list") or section.get("items") or []
            if not isinstance(widgets, list):
                widgets = [widgets]
            for widget in widgets:
                if not isinstance(widget, dict):
                    continue
                widget_type = str(widget.get("widget_type") or widget.get("type") or "")
                # شکل canonical فعلی API: DESCRIPTION / DESCRIPTION_ROW / data.text.
                # آن را با اولویت بالاتر از captionهای همان section نگه می‌داریم.
                if (
                    _normalize_fa_digits(section_name) == "description"
                    and _normalize_fa_digits(widget_type) == "description row"
                ):
                    _append_description_candidate(candidates, (widget.get("data") or {}).get("text"), 110)
                widget_context = section_context or _is_description_context(
                    widget_type or widget.get("title") or widget.get("label")
                )
                if widget_context:
                    _append_description_candidate(candidates, widget, 90)

    # بعضی releaseها section را حذف می‌کنند و widget را مستقیم می‌فرستند.
    for widgets_path in (("widget_list",), ("widgets", "widget_list"), ("data", "widget_list"), ("widgets",)):
        widgets = _path_value(payload, *widgets_path)
        if isinstance(widgets, dict):
            widgets = widgets.get("widgets") or widgets.get("items") or []
        if not isinstance(widgets, list):
            continue
        for widget in widgets:
            if not isinstance(widget, dict):
                continue
            if _is_description_context(
                widget.get("widget_type") or widget.get("type") or widget.get("title") or widget.get("label")
            ):
                _append_description_candidate(candidates, widget, 90)

    # آخرین محافظ در برابر تغییر نام section/widget: اگر کلید یا caption به
    # «توضیحات» اشاره کند، value/text همان node را بخوان. این scan عمداً تنها
    # در context توضیحات متن جمع می‌کند، نه همه‌ی متن‌های payload.
    def walk(node: Any, in_description_context: bool = False, priority: int = 65, depth: int = 0) -> None:
        if depth > 10 or node is None:
            return
        if isinstance(node, list):
            for child in node:
                walk(child, in_description_context, priority, depth + 1)
            return
        if not isinstance(node, dict):
            return

        node_context = in_description_context or any(
            _is_description_context(node.get(key))
            for key in ("section_name", "widget_type", "type", "title", "label", "name", "key")
        )
        for key, value in node.items():
            key_normalized = _normalize_fa_digits(str(key))
            child_priority = 15 if "seo" in key_normalized else priority
            child_context = node_context or _is_description_context(key)
            if isinstance(value, str):
                if child_context:
                    _append_description_candidate(candidates, value, child_priority)
            elif isinstance(value, (dict, list)):
                walk(value, child_context, child_priority, depth + 1)

    walk(payload)

    # SEO only remains a low-priority last fallback.
    for seo_path in (
        ("seo", "description"), ("data", "seo", "description"),
        ("share", "description"), ("data", "share", "description"),
    ):
        _append_description_candidate(candidates, _path_value(payload, *seo_path), 10)

    return _rank_description_candidates(candidates)


def extract_post_description(payload: Dict[str, Any]) -> str:
    """بهترین متن توضیح برای نمایش را برمی‌گرداند."""
    candidates = extract_post_description_candidates(payload)
    return candidates[0] if candidates else ""


def _iter_detail_rows(payload: Dict[str, Any]):
    """ردیف‌های جدول مشخصات را از شکل‌های متفاوت پاسخ جزئیات استخراج می‌کند."""
    seen = set()

    def walk(node: Any, depth: int = 0):
        if depth > 9 or node is None:
            return
        if isinstance(node, list):
            for item in node:
                yield from walk(item, depth + 1)
            return
        if not isinstance(node, dict):
            return

        label = node.get("title") or node.get("label") or node.get("name") or node.get("key")
        value = (
            node.get("value")
            if "value" in node else node.get("text")
            if "text" in node else node.get("subtitle")
            if "subtitle" in node else node.get("display_value")
        )
        available = node.get("available") if isinstance(node.get("available"), bool) else None
        if isinstance(label, str) and (value is not None or available is not None):
            clean_label = _clean_public_text(label)
            clean_value = _clean_public_text(value) if isinstance(value, str) else value
            marker = (clean_label, str(clean_value), available)
            if clean_label and marker not in seen:
                seen.add(marker)
                yield {"label": clean_label, "value": clean_value, "available": available}

        for child in node.values():
            if isinstance(child, (dict, list)):
                yield from walk(child, depth + 1)

    yield from walk(payload)


def _detail_bool(row: Dict[str, Any]) -> Optional[bool]:
    if row.get("available") is not None:
        return bool(row["available"])
    source = _normalize_fa_digits(f"{row.get('label') or ''} {row.get('value') or ''}")
    if any(word in source for word in ("ندارد", "بدون", "خیر", "false", "not available")):
        return False
    if any(word in source for word in ("دارد", "هست", "بله", "true", "available")):
        return True
    return None


def extract_post_details(payload: Dict[str, Any]) -> Dict[str, Any]:
    """داده‌ی قابل استفاده‌ی یک آگهی را از پاسخ جزئیات استخراج می‌کند.

    این خروجی هم برای متن توضیحات، هم امکانات املاک و هم رنگ/بدنه/شاسی خودرو
    استفاده می‌شود تا همه‌ی قابلیت‌ها به یک منبع یکسان و cache‌شده تکیه کنند.
    """
    if not isinstance(payload, dict):
        return {}

    description_candidates = extract_post_description_candidates(payload)
    attrs: Dict[str, Any] = {
        "description": description_candidates[0] if description_candidates else "",
        # تمام candidateهای معتبر در متن جست‌وجو باقی می‌مانند تا یک caption
        # کوتاه یا تغییر جزئی schema باعث گم‌شدن کلمه‌ی داخل توضیحات نشود.
        "description_search_text": "\n".join(description_candidates)[:8000],
        "description_source": "api" if description_candidates else "",
        "detail_text": "",
        "features": {"parking": None, "elevator": None, "warehouse": None, "balcony": None},
        "car_color": "",
        "body_status_detail": "",
        "chassis_status_detail": "",
        # قیمت جزئیات یک fallback برای کارت‌هایی است که API جست‌وجو در آن‌ها
        # middle_description_text را خالی می‌فرستد (به‌ویژه برخی خودرو/موبایل‌ها).
        "price_num": None,
        "credit_num": None,
        "rent_num": None,
    }
    detail_parts: List[str] = []

    for row in _iter_detail_rows(payload):
        label = row.get("label") or ""
        value = row.get("value")
        value_text = str(value or "")
        normalized_label = _normalize_fa_digits(label)
        normalized_pair = _normalize_fa_digits(f"{label} {value_text}")
        if value_text:
            detail_parts.append(f"{label}: {value_text}")
        else:
            detail_parts.append(label)

        feature_key = None
        if "آسانسور" in normalized_pair or "elevator" in normalized_pair:
            feature_key = "elevator"
        elif "پارک" in normalized_pair or "parking" in normalized_pair:
            feature_key = "parking"
        elif "انباری" in normalized_pair or "انبار" in normalized_pair or "warehouse" in normalized_pair:
            feature_key = "warehouse"
        elif "بالکن" in normalized_pair or "تراس" in normalized_pair or "balcony" in normalized_pair:
            feature_key = "balcony"
        if feature_key:
            known = _detail_bool(row)
            if known is None and not any(word in normalized_pair for word in ("ندارد", "بدون")):
                # ردیف‌های feature در API گاهی فقط نام موردِ موجود را دارند.
                known = True
            if known is not None:
                attrs["features"][feature_key] = known

        if ("رنگ" == normalized_label or normalized_label.endswith(" رنگ") or "color" in normalized_label) and value_text:
            attrs["car_color"] = value_text
        elif "وضعیت بدنه" in normalized_label or normalized_label in {"بدنه", "body status"}:
            attrs["body_status_detail"] = value_text or label
        elif "شاسی" in normalized_label or "chassis" in normalized_label:
            attrs["chassis_status_detail"] = value_text or label

        if "ودیعه" in normalized_label or normalized_label.startswith("رهن"):
            amount = parse_money_amount(value_text)
            if amount is not None:
                attrs["credit_num"] = amount
        elif "اجاره" in normalized_label or "rent" in normalized_label:
            amount = parse_money_amount(value_text)
            if amount is not None:
                attrs["rent_num"] = amount
        elif (
            attrs["price_num"] is None
            and (normalized_label.startswith("قیمت") or "price" in normalized_label)
            and "هر متر" not in normalized_label
            and "متری" not in normalized_label
        ):
            amount = parse_money_amount(value_text)
            if amount is not None:
                attrs["price_num"] = amount

    # پاسخ‌های قدیمی، مبلغ اجاره و قیمت کالا را در webengage قرار می‌دهند.
    for path in (("data", "webengage"), ("webengage",), ("data",)):
        source = _path_value(payload, *path)
        if not isinstance(source, dict):
            continue
        if attrs["credit_num"] is None:
            for key in ("credit", "deposit", "mortgage"):
                amount = parse_money_amount(source.get(key))
                if amount is not None:
                    attrs["credit_num"] = amount
                    break
        if attrs["rent_num"] is None:
            for key in ("rent", "monthly_rent"):
                amount = parse_money_amount(source.get(key))
                if amount is not None:
                    attrs["rent_num"] = amount
                    break
        if attrs["price_num"] is None:
            for key in ("price", "price_value", "amount", "total_price"):
                amount = parse_money_amount(_price_text_from_value(source.get(key)))
                if amount is not None:
                    attrs["price_num"] = amount
                    break
        if not attrs["car_color"]:
            for key in ("color", "car_color"):
                if source.get(key):
                    attrs["car_color"] = str(source[key])
                    break
        if not attrs["body_status_detail"] and source.get("body_status"):
            attrs["body_status_detail"] = str(source["body_status"])
        if not attrs["chassis_status_detail"] and source.get("chassis_status"):
            attrs["chassis_status_detail"] = str(source["chassis_status"])

    attrs["detail_text"] = " · ".join(dict.fromkeys(part for part in detail_parts if part))[:4000]
    return attrs


def _has_detail_description(details: Dict[str, Any]) -> bool:
    return bool(_clean_public_text(details.get("description_search_text") or details.get("description")))


def _join_unique_texts(values: List[Any], limit: int = 8000) -> str:
    """متن‌های توضیح/detail را بدون تکرار برای جست‌وجو با هم ترکیب می‌کند."""
    seen = set()
    output = []
    for value in values:
        cleaned = _clean_public_text(value)
        normalized = _normalize_fa_digits(cleaned)
        if cleaned and normalized and normalized not in seen:
            seen.add(normalized)
            output.append(cleaned)
    return "\n".join(output)[:limit]


def _merge_detail_results(base: Dict[str, Any], extra: Dict[str, Any]) -> Dict[str, Any]:
    """دو پاسخ detail را طوری ادغام می‌کند که fallback اطلاعات قبلی را پاک نکند."""
    if not base:
        return dict(extra or {})
    if not extra:
        return dict(base)
    merged = dict(base)
    for key in ("car_color", "body_status_detail", "chassis_status_detail", "price_num", "credit_num", "rent_num"):
        if merged.get(key) in (None, "") and extra.get(key) not in (None, ""):
            merged[key] = extra[key]
    features = dict(base.get("features") or {})
    for key, value in (extra.get("features") or {}).items():
        if features.get(key) is None and value is not None:
            features[key] = value
    if features:
        merged["features"] = features

    merged["detail_text"] = _join_unique_texts(
        [base.get("detail_text"), extra.get("detail_text")], limit=5000
    )
    search_text = _join_unique_texts(
        [
            base.get("description_search_text"), base.get("description"),
            extra.get("description_search_text"), extra.get("description"),
        ]
    )
    if search_text:
        merged["description_search_text"] = search_text
    if not merged.get("description") and extra.get("description"):
        merged["description"] = extra["description"]
        merged["description_source"] = extra.get("description_source") or "api"
    return merged


def extract_post_description_from_html(html: str) -> str:
    """Fallback محافظه‌کارانه برای صفحه‌ی عمومی آگهی وقتی API متن را ندهد.

    معمولاً API وب کافی است؛ اما دیوار گاهی schema جزئیات را تغییر می‌دهد یا
    پاسخ خلاصه برمی‌گرداند. صفحه‌ی عمومی در آن وضعیت description را در meta یا
    JSON-LD دارد. این parser فقط فیلدهای description را می‌خواند تا متن نامرتبط
    صفحه به‌عنوان آگهی وارد جست‌وجو نشود.
    """
    if not isinstance(html, str) or not html:
        return ""
    candidates: List[Tuple[int, str]] = []

    for tag in re.findall(r"<meta\b[^>]*>", html, flags=re.IGNORECASE):
        attrs = {
            key.lower(): unescape(value)
            for key, _, value in re.findall(
                r"([\w:-]+)\s*=\s*(['\"])(.*?)\2", tag, flags=re.IGNORECASE | re.DOTALL
            )
        }
        marker = (attrs.get("name") or attrs.get("property") or attrs.get("itemprop") or "").lower()
        if marker in {"description", "og:description", "twitter:description"}:
            _append_description_candidate(candidates, attrs.get("content"), 45)

    def collect_description_values(node: Any, depth: int = 0) -> None:
        if depth > 10 or node is None:
            return
        if isinstance(node, list):
            for child in node:
                collect_description_values(child, depth + 1)
            return
        if not isinstance(node, dict):
            return
        for key, value in node.items():
            if _is_description_context(key):
                _append_description_candidate(candidates, value, 55)
            if isinstance(value, (dict, list)):
                collect_description_values(value, depth + 1)

    for script in re.findall(
        r"<script\b[^>]*type\s*=\s*(['\"])(?:application/ld\+json|application/json)\1[^>]*>(.*?)</script>",
        html,
        flags=re.IGNORECASE | re.DOTALL,
    ):
        try:
            collect_description_values(json.loads(unescape(script[1])))
        except (TypeError, ValueError, json.JSONDecodeError):
            pass

    # برخی نسخه‌های صفحه state را به‌صورت JSON escape‌شده inline قرار می‌دهند.
    for match in re.finditer(
        r'(?i)["\'](?:description|description_text|post_description)["\']\s*:\s*("(?:\\.|[^"\\])*")',
        html,
    ):
        try:
            _append_description_candidate(candidates, json.loads(match.group(1)), 50)
        except (TypeError, ValueError, json.JSONDecodeError):
            pass

    ranked = _rank_description_candidates(candidates)
    return ranked[0] if ranked else ""


def get_post_details(token: str) -> Dict[str, Any]:
    """جزئیات آگهی را با API جدید، API قدیمی و fallback صفحه‌ی عمومی می‌خواند.

    مهم: پاسخ detail ممکن است مشخصات خودرو/ملک را داشته باشد ولی توضیحات را
    نداشته باشد. در آن حالت نباید همان پاسخ زودتر return شود، چون کلمات کلیدی
    داخل توضیحات هرگز بررسی نمی‌شوند؛ fallbackها تا رسیدن به متن ادامه دارند.
    """
    token = str(token or "").strip()
    if not token or not re.fullmatch(r"[A-Za-z0-9_-]{4,160}", token):
        return {}

    headers = dict(HEADERS)
    headers.update({
        # دیوار برای detail کامل در برخی مسیرها media type پرشده را برمی‌گرداند؛
        # بدون آن ممکن است sections وجود داشته باشد اما DESCRIPTION_ROW تهی بماند.
        "Accept": "application/json-filled, application/json, text/plain, */*",
        "X-Render-Type": "CSR",
        "X-Standard-Divar-Error": "true",
        "X-Requested-With": "XMLHttpRequest",
    })
    api_urls = (
        f"https://api.divar.ir/v8/posts-v2/web/{token}",
        f"https://api.divar.ir/v8/posts/{token}",
    )
    best_details: Dict[str, Any] = {}
    for url in api_urls:
        for attempt in range(2):
            try:
                response = requests.get(url, headers=headers, timeout=8)
                if response.status_code == 200:
                    payload = response.json()
                    if isinstance(payload, dict):
                        details = extract_post_details(payload)
                        best_details = _merge_detail_results(best_details, details)
                        if _has_detail_description(best_details):
                            return best_details
                    # API می‌تواند یک پاسخ ۲۰۰ ولی schema خلاصه بدهد؛ endpoint
                    # بعدی/صفحه‌ی عمومی هنوز ممکن است متن واقعی را داشته باشد.
                    break
                if response.status_code == 429 or response.status_code >= 500:
                    time.sleep(0.6 * (attempt + 1))
                    continue
                break
            except Exception as exc:
                if attempt == 1:
                    print(f"[divar] detail fetch failed for {token}: {type(exc).__name__}")
                time.sleep(0.25 * (attempt + 1))

    if not _has_detail_description(best_details):
        page_headers = dict(headers)
        page_headers["Accept"] = "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8"
        page_headers.pop("Content-Type", None)
        try:
            response = requests.get(
                f"https://divar.ir/v/{token}", headers=page_headers, timeout=8
            )
            if response.status_code == 200:
                page_description = extract_post_description_from_html(response.text)
                if page_description:
                    fallback = {
                        "description": page_description,
                        "description_search_text": page_description,
                        "description_source": "public-page",
                    }
                    best_details = _merge_detail_results(best_details, fallback)
        except Exception as exc:
            print(f"[divar] public-page description fallback failed for {token}: {type(exc).__name__}")

    return best_details


def get_post_details_cached(token: str) -> Dict[str, Any]:
    token = str(token or "").strip()
    if not token:
        return {}
    now = time.time()
    with POST_DETAILS_CACHE_LOCK:
        cached = POST_DETAILS_CACHE.get(token)
        if cached:
            cached_details, cached_at = cached
            # مشخصات بدون description ممکن است پاسخ ناقص یا محدودشده باشد؛ آن
            # را کوتاه نگه می‌داریم تا کلیدواژه‌ی توضیحات تا ۱۵ دقیقه از کار
            # نیفتد، حتی اگر همان پاسخ چند مشخصه‌ی دیگر داشته باشد.
            ttl = POST_DETAILS_CACHE_TTL if _has_detail_description(cached_details) else POST_DETAILS_FAILURE_CACHE_TTL
            if now - cached_at < ttl:
                return cached_details
    with SEARCH_SEMAPHORE:
        details = get_post_details(token)
    with POST_DETAILS_CACHE_LOCK:
        # timestamp after the network call, so a slow request still receives a
        # full cache lifetime.
        POST_DETAILS_CACHE[token] = (details, time.time())
    return details


def get_post_description(token: str) -> str:
    return (get_post_details(token).get("description") or "")


def get_post_description_cached(token: str) -> str:
    return (get_post_details_cached(token).get("description") or "")


def _merge_post_details(ad: Dict[str, Any], details: Dict[str, Any]) -> Dict[str, Any]:
    if not details:
        ad.setdefault("description", "")
        return ad
    description = details.get("description") or ""
    if description:
        ad["description"] = description
    else:
        ad.setdefault("description", "")
    description_search_text = details.get("description_search_text") or description
    if description_search_text:
        ad["description_search_text"] = description_search_text
    else:
        ad.setdefault("description_search_text", ad.get("description") or "")
    if details.get("description_source"):
        ad["description_source"] = details["description_source"]
    ad["detail_text"] = details.get("detail_text") or ad.get("detail_text") or ""
    ad["features"] = details.get("features") or ad.get("features") or {}
    for key in ("parking", "elevator", "warehouse", "balcony"):
        value = (details.get("features") or {}).get(key)
        if value is not None:
            ad[key] = value
    for key in ("car_color", "body_status_detail", "chassis_status_detail"):
        if details.get(key):
            ad[key] = details[key]

    if ad.get("category") == "apartment-rent":
        # فقط کارت‌های اجاره باید اعداد ودیعه/اجاره و معادل‌سازی را دریافت کنند.
        credit = details.get("credit_num")
        rent = details.get("rent_num")
        if credit is not None or rent is not None:
            ad["credit_num"] = credit if credit is not None else ad.get("credit_num")
            ad["rent_num"] = rent if rent is not None else ad.get("rent_num")
            equivalents = rental_equivalents(ad.get("credit_num"), ad.get("rent_num"))
            ad.update(equivalents)
            if equivalents.get("deposit_equivalent_num") is not None:
                ad["price_num"] = equivalents["deposit_equivalent_num"]
            display = format_real_estate_price(ad.get("credit_num"), ad.get("rent_num"), ad.get("price") or "")
            ad["price"] = display
            ad["price_display"] = display
    else:
        # دفاع در عمق برای cacheها/نتایج قدیمی: حتی اگر پاسخ detail حاوی rent
        # باشد، کارت فروش، خودرو یا موبایل نباید آن را به کاربر نشان دهد.
        for key in (
            "credit_num", "rent_num", "rent_equivalent_num", "deposit_equivalent_num",
            "conversion_rule", "rental_equivalent_display", "deposit_equivalent_display",
        ):
            ad.pop(key, None)

        # برخی کارت‌های کالا و خودرو قیمت را فقط در detail/webengage دارند. فقط
        # وقتی قیمت کارت واقعاً موجود نیست از این مقدار استفاده می‌کنیم تا قیمت
        # جست‌وجو (منبع اصلی) بی‌دلیل تغییر نکند.
        detail_price = details.get("price_num")
        if ad.get("price_num") is None and detail_price is not None:
            ad["price_num"] = detail_price
            display = format_toman(detail_price)
            if display:
                ad["price"] = display
                ad["price_display"] = display
    ad["details_loaded"] = True
    return ad


def hydrate_ads_with_details(ads: List[Dict]) -> List[Dict]:
    """همه‌ی آگهی‌های خروجی را با توضیحات و مشخصات detail کامل می‌کند.

    این کار عمداً سقفِ تعداد ندارد: کلمات کلیدی/منفی و امتیازدهی باید برای
    تمام نتیجه‌های نمایش‌داده‌شده یکسان عمل کنند، نه فقط ۲۰ یا ۲۵ مورد اول.
    هم‌زمانی و cache از فشار غیرضروری به API جلوگیری می‌کنند.
    """
    if not ads:
        return ads
    by_token: Dict[str, List[Dict]] = {}
    for ad in ads:
        token = str(ad.get("token") or "").strip()
        if token:
            by_token.setdefault(token, []).append(ad)
        else:
            ad.setdefault("description", "")

    def load(token: str):
        return token, get_post_details_cached(token)

    workers = min(DETAIL_FETCH_WORKERS, len(by_token))
    if workers <= 1:
        results = [load(token) for token in by_token]
    else:
        results = []
        with ThreadPoolExecutor(max_workers=workers) as executor:
            futures = [executor.submit(load, token) for token in by_token]
            for future in as_completed(futures):
                try:
                    results.append(future.result())
                except Exception as exc:
                    print("[divar] detail worker failed:", type(exc).__name__)

    for token, details in results:
        for ad in by_token.get(token, []):
            _merge_post_details(ad, details)
    return ads


def hydrate_ads_with_missing_prices(ads: List[Dict]) -> List[Dict]:
    """فقط کارت‌های بی‌قیمت را از detail کامل می‌کند.

    بعضی POST_ROWهای موبایل/خودرو قیمت را در payload جست‌وجو ندارند اما آن را
    در webengage detail دارند. این fetch محدود، قبل از میانگین‌گیری/فیلتر
    هوشمند انجام می‌شود تا آن کارت‌ها حذف یا با قیمت خالی نمایش داده نشوند.
    """
    missing = [ad for ad in ads if ad.get("price_num") is None and ad.get("token")]
    if missing:
        hydrate_ads_with_details(missing)
    return ads


def _ad_search_text(ad: Dict[str, Any]) -> str:
    return " ".join(
        str(ad.get(key) or "")
        for key in (
            "title", "status", "description", "description_search_text", "detail_text",
            "body_status_detail", "chassis_status_detail",
        )
    )


def find_matched_keywords(text: str, keywords: List[str]) -> List[str]:
    if not text or not keywords:
        return []
    normalized_text = _normalize_fa_digits(text)
    return [
        keyword for keyword in keywords
        if (normalized_keyword := _normalize_fa_digits(keyword)) and normalized_keyword in normalized_text
    ]


def keyword_match_sources(ad: Dict[str, Any], keywords: List[str]) -> Dict[str, List[str]]:
    """برای هر کلیدواژه محل تطبیق را برمی‌گرداند؛ مخصوصاً برای مشاهده‌پذیری متن توضیح."""
    source_texts = {
        "title": " ".join(str(ad.get(key) or "") for key in ("title", "status")),
        "description": " ".join(
            str(ad.get(key) or "") for key in ("description", "description_search_text")
        ),
        "details": " ".join(
            str(ad.get(key) or "")
            for key in ("detail_text", "body_status_detail", "chassis_status_detail")
        ),
    }
    normalized_sources = {
        source: _normalize_fa_digits(text) for source, text in source_texts.items() if text
    }
    matches: Dict[str, List[str]] = {}
    for keyword in keywords or []:
        display_keyword = str(keyword).strip()
        normalized_keyword = _normalize_fa_digits(display_keyword)
        if not normalized_keyword:
            continue
        locations = [
            source for source, text in normalized_sources.items() if normalized_keyword in text
        ]
        if locations:
            matches[display_keyword] = locations
    return matches


def has_negative_keyword(text: str, negative_keywords: List[str]) -> bool:
    if not text or not negative_keywords:
        return False
    normalized_text = _normalize_fa_digits(text)
    return any(
        (normalized_keyword := _normalize_fa_digits(keyword)) and normalized_keyword in normalized_text
        for keyword in negative_keywords
        if str(keyword).strip()
    )


def filter_negative_keywords(ads: List[Dict], negative_keywords: List[str], max_fetch: Optional[int] = None) -> List[Dict]:
    """فیلتر منفی روی عنوان *و توضیحات کامل* تمام آگهی‌ها اعمال می‌شود.

    ``max_fetch`` فقط برای سازگاری امضای قدیمی نگه داشته شده و عمداً نادیده
    گرفته می‌شود؛ محدود کردن آن علت اصلی رد نشدن برخی آگهی‌ها بود.
    """
    negative_keywords = [str(keyword).strip() for keyword in (negative_keywords or []) if str(keyword).strip()]
    if not negative_keywords:
        return ads
    hydrate_ads_with_details(ads)
    return [ad for ad in ads if not has_negative_keyword(_ad_search_text(ad), negative_keywords)]


def enrich_ads_with_keywords(ads: List[Dict], keywords: List[str], max_fetch: Optional[int] = None) -> List[Dict]:
    """کلمات کلیدی را روی عنوان و توضیحات تمام آگهی‌های نتیجه تطبیق می‌دهد."""
    keywords = [str(keyword).strip() for keyword in (keywords or []) if str(keyword).strip()]
    if keywords:
        hydrate_ads_with_details(ads)
    for ad in ads:
        sources = keyword_match_sources(ad, keywords)
        # کلیدها به همان ترتیب ورودی کاربر نگه داشته می‌شوند تا UI/مانیتور پایدار بماند.
        matched = [keyword for keyword in keywords if keyword in sources]
        ad["matched_keywords"] = matched
        ad["matched_keyword_sources"] = sources
        ad["has_keyword"] = bool(matched)
    ads.sort(key=lambda item: (not item.get("has_keyword", False), -(item.get("value_score") or 0)))
    return ads

# ====================== امتیازدهی هوشمند حرفه‌ای ======================
# پروفایل‌ها: conservative / balanced / open
SMART_PROFILES = {
    # مقیاس امتیاز ≈ ۰–۱۰۰  (معمولی ۳۰–۴۵ | خوب ۵۵–۷۰ | عالی ۷۰+)
    "conservative": {
        "min_score": 55.0,          # فقط فرصت‌های قوی
        "max_price_ratio": 0.90,    # حداکثر ۹۰٪ میانگین
        "min_price_ratio": 0.45,    # خیلی ارزان = مشکوک
        "deep_fetch": 45,
        "max_results": 30,
    },
    "balanced": {
        "min_score": 42.0,
        "max_price_ratio": 0.97,
        "min_price_ratio": 0.38,
        "deep_fetch": 55,
        "max_results": 50,
    },
    "open": {
        "min_score": 30.0,
        "max_price_ratio": 1.02,
        "min_price_ratio": 0.32,
        "deep_fetch": 65,
        "max_results": 70,
    },
}

# سیگنال‌های منفی پیش‌فرض (کسر امتیاز، نه حذف قطعی)
MOBILE_NEG_SIGNALS = [
    "طرح", "کپی", "مشابه اصل", "های کپی", "high copy", "تعمیری", "آب خورده", "آب‌خورده",
    "تاچ خراب", "باتری ضعیف", "بدون گارانتی", "رجیستری ندارد", "اقساط", "معاوضه",
    "قسطی", "نیاز به تعمیر", "lcd خراب", "فریم خراب", "جعبه ندارد",
]
CAR_NEG_SIGNALS = [
    "تصادفی", "چپی", "اتاق تعویض", "موتور تعویض", "تعویض موتور", "نیاز به تعمیر",
    "مدارک ناقص", "معاوضه", "اقساط", "قسطی", "بدون بیمه", "پایان کار", "ساندویچی",
]
# افت رنگ و شاسی جداگانه و با درصدهای مشخص محاسبه می‌شود؛ بنابراین این موارد
# اینجا تکرار نشده‌اند تا یک ایراد بدنه دو بار جریمه نشود.
# رنگ‌های پرتقاضای سفید و مشکی اثر مثبت دارند؛ سفید معمولاً نقدشوندگی
# بیشتری دارد، بنابراین اثر آن کمی بالاتر لحاظ شده است.
CAR_COLOR_VALUE_BONUSES = {"سفید": 2.5, "مشکی": 1.5}
CAR_PAINT_VALUE_DEDUCTIONS = {
    "تمام‌رنگ": 10.0,
    "سقف رنگ": 9.0,
    "شاسی رنگ": 9.0,
    "کاپوت رنگ": 6.0,
    "صندوق رنگ": 5.0,
    "ستون رنگ": 4.0,
    "درب رنگ": 4.0,
    "گلگیر رنگ": 3.0,
}
CAR_CHASSIS_DAMAGE_DEDUCTION = 3.0
MOBILE_POS_SIGNALS = [
    "پلمپ", "اکبند", "در حد نو", "بدون خط", "گارانتی", "بیمه", "جعبه", "شارژر",
    "سالم سالم", "کار نکرده", "نو هست",
]
CAR_POS_SIGNALS = [
    "بدون رنگ", "رنگ نشده", "موتور سالم", "گیربکس سالم", "سالم سالم", "صفر",
    "وکالتی نیست", "آچار به آچار", "بیمه دارد", "سرویس منظم",
]
RE_NEG_SIGNALS = [
    "معاوضه", "اقساط", "قسطی", "وکالتی", "قولنامه‌ای", "قولنامه ای", "نیمه کاره",
    "نیمه‌کاره", "نیاز به بازسازی", "فرسوده", "نم دارد", "رطوبت", "پایان کار ندارد",
]
RE_POS_SIGNALS = [
    "سند تک برگ", "سندتکبرگ", "نوساز", "کلید نخورده", "بازسازی شده", "فول امکانات",
    "نورگیر", "جنوبی", "لوکس",
]


def parse_freshness_hours(time_text: str) -> Optional[float]:
    """برآورد ساعت سپری‌شده از متن دیوار مثل «۲ ساعت پیش»"""
    if not time_text:
        return None
    t = _normalize_fa_digits(time_text)
    if "لحظ" in t or "همین الان" in t:
        return 0.1
    m = re.search(r"(\d+)\s*دقیقه", t)
    if m:
        return max(0.05, int(m.group(1)) / 60.0)
    m = re.search(r"(\d+)\s*ساعت", t)
    if m:
        return float(m.group(1))
    m = re.search(r"(\d+)\s*روز", t)
    if m:
        return float(m.group(1)) * 24
    if "دیروز" in t:
        return 24.0
    if "هفته" in t:
        m = re.search(r"(\d+)\s*هفته", t)
        return float(m.group(1) if m else 1) * 24 * 7
    return None


def market_baseline(prices: List[int]) -> float:
    """میانه + حذف پرت برای میانگین پایدار بازار.

    - نمونه خیلی کوچک (<3): میانه
    - حذف قیمت‌های پرت نسبت به میانه
    - میانگین روی هسته میانی (تقریباً ۵۰٪ وسط)
    """
    if not prices:
        return 0.0
    if len(prices) == 1:
        return float(prices[0])
    sorted_p = sorted(int(p) for p in prices if p and p > 0)
    if not sorted_p:
        return 0.0
    if len(sorted_p) == 1:
        return float(sorted_p[0])
    if len(sorted_p) == 2:
        return float(sum(sorted_p) / 2)

    med = statistics.median(sorted_p)
    if med <= 0:
        return float(sum(sorted_p) / len(sorted_p))

    # حذف پرت‌های خیلی دور از میانه
    filtered = [p for p in sorted_p if med * 0.50 <= p <= med * 1.85]
    if len(filtered) < max(3, len(sorted_p) // 3):
        filtered = sorted_p

    filtered = sorted(filtered)
    n = len(filtered)
    if n >= 8:
        lo = n // 5
        hi = n - n // 5
        core = filtered[lo:hi] or filtered
    elif n >= 5:
        lo = n // 5
        hi = n - n // 5
        core = filtered[lo:hi] or filtered
    else:
        core = filtered
    return float(sum(core) / len(core))


def mobile_status_score(status_text: str) -> float:
    if not status_text:
        return 1.5
    s = _normalize_fa_digits(status_text)
    if "در حد نو" in s:
        return 3.5
    if s.strip() == "نو" or s.startswith("نو ") or " نو" in f" {s} ":
        return 4.2
    if "کارکرده" in s:
        return 1.8
    if "تعمیر" in s or "نیاز" in s:
        return 0.4
    return 1.8


def mobile_storage_score(title: str) -> float:
    t = _normalize_fa_digits(title or "")
    if any(x in t for x in ["1tb", "1024"]):
        return 3.5
    if "512" in t:
        return 3.0
    if "256" in t:
        return 2.5
    if "128" in t:
        return 2.0
    if "64" in t:
        return 1.2
    return 0.7


def mobile_ram_score(title: str) -> float:
    t = _normalize_fa_digits(title or "")
    if any(x in t for x in ["16gb", "18gb", "12gb", "16 گیگ", "12 گیگ"]):
        return 3.0
    if "8gb" in t or "8 گیگ" in t:
        return 2.5
    if "6gb" in t or "6 گیگ" in t:
        return 2.0
    if "4gb" in t or "4 گیگ" in t:
        return 1.4
    return 0.8


def car_body_score(title: str, status: str = "") -> float:
    text = _normalize_fa_digits((title or "") + " " + (status or ""))
    if any(x in text for x in ["بدون رنگ", "رنگ نشده", "صفر کیلومتر", "صفر"]):
        return 4.5
    if any(x in text for x in ["سالم", "تمیز"]):
        return 3.5
    if any(x in text for x in ["لکه", "خش"]):
        return 2.8
    if any(x in text for x in ["رنگ شده", "رنگخوردگی", "رنگ درب", "رنگ کاپوت"]):
        return 1.6
    if any(x in text for x in ["تصادف", "چپی"]):
        return 0.6
    return 2.4


def car_motor_score(title: str, desc: str = "") -> float:
    t = _normalize_fa_digits((title or "") + " " + (desc or ""))
    if any(x in t for x in ["موتور سالم", "موتور عالی", "گیربکس سالم"]):
        return 3.2
    if any(x in t for x in ["تعویض موتور", "موتور تعویض"]):
        return 1.2
    if any(x in t for x in ["نیاز به تعمیر", "موتور خراب"]):
        return 0.4
    return 2.0


def _signal_hits(text: str, signals: List[str]) -> List[str]:
    if not text:
        return []
    t = _normalize_fa_digits(text)
    hits = []
    for s in signals:
        sn = _normalize_fa_digits(s)
        if sn and sn in t:
            hits.append(s)
    return hits


def _normalize_car_color(value: Any) -> str:
    text = _normalize_fa_digits(str(value or ""))
    if any(word in text for word in ("سفید", "white")):
        return "سفید"
    if any(word in text for word in ("مشکی", "سیاه", "black")):
        return "مشکی"
    if any(word in text for word in ("نقره", "silver")):
        return "نقره‌ای"
    if any(word in text for word in ("خاکست", "طوسی", "gray", "grey")):
        return "خاکستری"
    return ""


def _has_any_phrase(text: str, phrases: List[str]) -> bool:
    normalized = _normalize_fa_digits(text)
    return any(_normalize_fa_digits(phrase) in normalized for phrase in phrases)


def evaluate_car_condition(ad: Dict, desc: str = "") -> Dict[str, Any]:
    """اثر رنگ، بدنه و شاسی را بر ارزش خودرو محاسبه می‌کند.

    درصدهای مورد استفاده همان بازه‌های درخواستی هستند: سقف/شاسی رنگ ۹٪،
    کاپوت ۶٪، صندوق ۵٪، ستون/درب ۴٪، گلگیر ۳٪، تمام‌رنگ ۱۰٪ و ضربه شاسی
    ۳٪ برای هر سمتِ اعلام‌شده. مجموع برای جلوگیری از نتیجه‌ی غیرواقعی سقف
    ۳۰٪ دارد.
    """
    structured_body = str(ad.get("body_status_detail") or ad.get("body_status") or "")
    structured_chassis = str(ad.get("chassis_status_detail") or ad.get("chassis_status") or "")
    source = " ".join(
        str(part or "") for part in (
            ad.get("title"), ad.get("status"), desc, ad.get("description"), ad.get("detail_text"),
            structured_body, structured_chassis,
        )
    )
    normalized = _normalize_fa_digits(source)
    body_code = _normalize_fa_digits(structured_body)
    chassis_code = _normalize_fa_digits(structured_chassis)

    pristine = _has_any_phrase(source, ["بدون رنگ", "بی رنگ", "رنگ نشده", "فابریک", "no paint"])
    paint_deduction = 0.0
    chassis_paint_deduction = 0.0
    notes: List[str] = []

    full_paint = (
        "full paint" in body_code
        or "full-paint" in body_code
        or _has_any_phrase(source, ["تمام رنگ", "تمام‌رنگ", "کامل رنگ", "دور رنگ", "فول رنگ"])
    )
    if full_paint:
        paint_deduction = CAR_PAINT_VALUE_DEDUCTIONS["تمام‌رنگ"]
        notes.append("تمام‌رنگ −۱۰٪")
    else:
        paint_patterns = (
            ("سقف رنگ", ["سقف رنگ", "رنگ سقف", "roof paint"]),
            ("کاپوت رنگ", ["کاپوت رنگ", "رنگ کاپوت", "hood paint"]),
            ("صندوق رنگ", ["صندوق رنگ", "رنگ صندوق", "درب صندوق رنگ", "trunk paint"]),
            ("ستون رنگ", ["ستون رنگ", "رنگ ستون", "pillar paint"]),
            ("درب رنگ", ["درب رنگ", "رنگ درب", "در رنگ", "door paint"]),
            ("گلگیر رنگ", ["گلگیر رنگ", "رنگ گلگیر", "fender paint"]),
        )
        for name, patterns in paint_patterns:
            if _has_any_phrase(source, patterns):
                deduction = CAR_PAINT_VALUE_DEDUCTIONS[name]
                paint_deduction += deduction
                notes.append(f"{name} −{int(deduction)}٪")
        # وقتی دیوار تنها یکی/چند لکه را مشخص کرده و محل در متن نیست.
        if not paint_deduction and not pristine:
            if "one spot paint" in body_code or "one-spot-paint" in body_code or _has_any_phrase(source, ["یک لکه رنگ", "تک لکه رنگ"]):
                paint_deduction = CAR_PAINT_VALUE_DEDUCTIONS["گلگیر رنگ"]
                notes.append("یک لکه رنگ −۳٪")
            elif "some paint" in body_code or "some-paint" in body_code or _has_any_phrase(source, ["چند لکه رنگ", "چند تکه رنگ"]):
                paint_deduction = 6.0
                notes.append("چند لکه رنگ −۶٪")

    # «شاسی رنگ» با «شاسی ضربه» متفاوت است و هر کدام قواعد خود را دارد.
    if _has_any_phrase(source, ["شاسی رنگ", "رنگ شاسی", "chassis paint"]):
        chassis_paint_deduction = CAR_PAINT_VALUE_DEDUCTIONS["شاسی رنگ"]
        notes.append("شاسی رنگ −۹٪")

    chassis_damage_count = 0
    both_damage = (
        "both-damage" in chassis_code
        or "both damage" in chassis_code
        or _has_any_phrase(source, ["هر دو شاسی ضربه", "دو شاسی ضربه", "شاسی جلو و عقب", "both chassis damage"])
    )
    if both_damage:
        chassis_damage_count = 2
    elif (
        "front-damage" in chassis_code
        or "back-damage" in chassis_code
        or "front damage" in chassis_code
        or "back damage" in chassis_code
        or "جلو ضربه" in chassis_code
        or "عقب ضربه" in chassis_code
        or _has_any_phrase(source, ["شاسی ضربه", "ضربه شاسی", "شاسی جلو", "شاسی عقب", "جلو ضربه", "عقب ضربه", "chassis damage"])
    ):
        chassis_damage_count = 1
    chassis_damage_deduction = chassis_damage_count * CAR_CHASSIS_DAMAGE_DEDUCTION
    if chassis_damage_deduction:
        notes.append(f"ضربه شاسی −{int(chassis_damage_deduction)}٪")

    color = _normalize_car_color(ad.get("car_color") or ad.get("color") or source)
    color_bonus = CAR_COLOR_VALUE_BONUSES.get(color, 0.0)
    if color_bonus:
        notes.append(f"رنگ {color} +{color_bonus:g}٪")

    total_deduction = min(30.0, paint_deduction + chassis_paint_deduction + chassis_damage_deduction)
    # ضریب ارزش منصفانه نسبت به نسخه‌ی بدون‌رنگ/شاسی‌سالم همان مدل.
    valuation_factor = max(0.65, (1.0 - total_deduction / 100.0) * (1.0 + color_bonus / 100.0))
    return {
        "color": color,
        "color_bonus_percent": color_bonus,
        "paint_deduction_percent": round(paint_deduction + chassis_paint_deduction, 1),
        "chassis_deduction_percent": round(chassis_damage_deduction, 1),
        "total_deduction_percent": round(total_deduction, 1),
        "valuation_factor": round(valuation_factor, 5),
        "notes": notes,
    }


def evaluate_real_estate_features(ad: Dict, desc: str = "") -> Dict[str, Any]:
    """امکانات کلیدی املاک را از داده‌ی detail و در صورت نیاز متن آگهی امتیاز می‌دهد."""
    text = _normalize_fa_digits(
        " ".join(str(part or "") for part in (ad.get("title"), ad.get("status"), desc, ad.get("description"), ad.get("detail_text")))
    )
    aliases = {
        "elevator": ("آسانسور", "elevator"),
        "parking": ("پارکینگ", "parking"),
        "warehouse": ("انباری", "انبار", "warehouse"),
        "balcony": ("بالکن", "تراس", "balcony"),
    }
    points = {"elevator": 3.2, "parking": 3.2, "warehouse": 2.6, "balcony": 1.0}
    labels = {"elevator": "آسانسور", "parking": "پارکینگ", "warehouse": "انباری", "balcony": "بالکن"}
    values: Dict[str, Optional[bool]] = {}
    score = 0.0
    present: List[str] = []
    absent: List[str] = []

    for key, words in aliases.items():
        explicit = ad.get(key)
        if explicit is None:
            explicit = (ad.get("features") or {}).get(key)
        if isinstance(explicit, bool):
            value: Optional[bool] = explicit
        else:
            has_word = any(_normalize_fa_digits(word) in text for word in words)
            no_pattern = any(
                phrase in text
                for phrase in (f"بدون {words[0]}", f"{words[0]} ندارد", f"فاقد {words[0]}")
            )
            value = False if no_pattern else True if has_word else None
        values[key] = value
        if value is True:
            score += points[key]
            present.append(labels[key])
        elif value is False:
            # نبودِ صریحِ امکانات مهم در امتیاز مؤثر است، اما نبودِ داده جریمه نمی‌شود.
            score -= min(1.4, points[key] * 0.45)
            absent.append(labels[key])

    display_parts = []
    if present:
        display_parts.append(" · ".join(f"✓ {name}" for name in present))
    if absent:
        display_parts.append(" · ".join(f"✕ {name}" for name in absent))
    return {
        "features": values,
        "score": round(max(-5.0, min(10.0, score)), 2),
        "present": present,
        "absent": absent,
        "display": " | ".join(display_parts),
    }


def score_ad_smart(
    ad: Dict,
    avg: float,
    category: str,
    profile: str = "balanced",
    desc: str = "",
    strict_price: bool = True,
) -> Dict:
    """امتیاز قابل توضیح ۰ تا ۱۰۰ برای همه‌ی دسته‌ها.

    علاوه بر قیمت/تازگی/کیفیت، این نسخه امکانات املاک و ارزش واقعی رنگ و
    بدنه‌ی خودرو را محاسبه می‌کند. ``strict_price`` تنها برای فیلتر جست‌وجوی
    هوشمند است؛ در نمایش عادی False است تا حتی آگهیِ قیمت‌نامشخص هم یک امتیاز
    مشخص داشته باشد.
    """
    cfg = SMART_PROFILES.get(profile) or SMART_PROFILES["balanced"]
    try:
        price = int(ad.get("price_num")) if ad.get("price_num") not in (None, "") else None
    except (TypeError, ValueError):
        price = None
    try:
        baseline = float(avg or 0)
    except (TypeError, ValueError):
        baseline = 0.0

    description = desc or ad.get("description") or ""
    title = ad.get("title") or ""
    status = ad.get("status") or ""
    full_text = " ".join(
        str(part or "")
        for part in (
            title, status, description, ad.get("description_search_text"), ad.get("detail_text"),
            ad.get("body_status_detail"), ad.get("chassis_status_detail"),
        )
    )
    reasons: List[str] = []
    breakdown = {
        "price": 0.0,
        "freshness": 0.0,
        "quality": 0.0,
        "seller": 0.0,
        "negative": 0.0,
        "positive": 0.0,
        "facilities": 0.0,
        "condition": 0.0,
        "completeness": 0.0,
    }
    car_valuation: Dict[str, Any] = {}
    property_features: Dict[str, Any] = {}

    # خودروهای رنگ‌شده ظاهراً ارزان‌ترند؛ برای مقایسه‌ی معامله، قیمت‌شان به
    # ارزش معادل بدنه‌ی سالم نرمال می‌شود تا «رنگ‌دار بودن» اشتباهاً فرصت ویژه
    # تشخیص داده نشود.
    comparison_price = price
    if category == "car":
        car_valuation = evaluate_car_condition(ad, description)
        factor = float(car_valuation.get("valuation_factor") or 1.0)
        if price and factor > 0:
            comparison_price = price / factor
        if baseline > 0:
            fair_value = int(round(baseline * factor))
            car_valuation["fair_market_value"] = fair_value
            if price:
                car_valuation["market_difference"] = int(price - fair_value)
        if price and comparison_price:
            car_valuation["condition_normalized_price"] = int(round(comparison_price))
        notes = car_valuation.get("notes") or []
        if notes:
            reasons.append("ارزش بدنه: " + "، ".join(notes[:3]))

    if (not price or baseline <= 0) and strict_price and category not in ("apartment-sell", "apartment-rent"):
        return {
            "value_score": 0.0,
            "score_breakdown": breakdown,
            "score_reason": "قیمت نامشخص یا میانگین بازار در دسترس نیست",
            "pass_filter": False,
            "neg_hits": [],
            "pos_hits": [],
            "car_valuation": car_valuation,
            "property_features": property_features,
        }

    # ---- قیمت (۰ تا ۴۰) ----
    price_score = 0.0
    ratio = 1.0
    priced = bool(comparison_price and baseline > 0)
    if priced:
        ratio = float(comparison_price) / baseline
        min_r = cfg["min_price_ratio"]
        max_r = cfg["max_price_ratio"]
        if category in ("apartment-sell", "apartment-rent"):
            min_r = min(min_r, 0.35)
            max_r = max(max_r, 1.8)
        if ratio < min_r or ratio > max_r:
            if strict_price:
                return {
                    "value_score": 0.0,
                    "score_breakdown": breakdown,
                    "score_reason": f"خارج از بازه قیمت معقول نسبت به بازار (نسبت {ratio:.2f})",
                    "pass_filter": False,
                    "neg_hits": [],
                    "pos_hits": [],
                    "car_valuation": car_valuation,
                    "property_features": property_features,
                }
            # برای کارت عادی صفر کردن امتیاز درست نیست؛ فقط سهم قیمت صفر می‌شود.
            reasons.append("قیمت خارج از بازه معمول بازار")
        else:
            discount = max(0.0, (baseline - float(comparison_price)) / baseline)
            price_score = min(40.0, discount * 120.0)
            if ratio <= 1.0:
                # نزدیکِ میانگین یک امتیاز پایه دارد؛ با ارزان‌تر شدن، منحنی
                # تخفیف اصلی سهم بزرگ‌تر را می‌گیرد.
                near = max(0.0, 8.0 * (1.0 - max(0.0, 1.0 - ratio) / 0.12))
                price_score = max(price_score, near)
            else:
                over = min(12.0, (ratio - 1.0) * 40.0)
                price_score = max(0.0, 6.0 - over)
            if discount >= 0.15:
                reasons.append(f"قیمت {int(discount * 100)}٪ زیر میانگین بازار")
            elif discount >= 0.07:
                reasons.append(f"قیمت حدود {int(discount * 100)}٪ زیر میانگین")
            elif ratio <= 1.02:
                reasons.append("قیمت نزدیک میانگین بازار")
            else:
                reasons.append("قیمت بالاتر از میانگین")
    else:
        # املاک اجاره ممکن است یکی از ودیعه/اجاره را نداشته باشد؛ بدون جایزه‌ی
        # مصنوعی فقط پیام روشن ارائه می‌شود.
        reasons.append("قیمت/میانگین کامل در دسترس نیست")
    breakdown["price"] = round(price_score, 2)

    # ---- تازگی (۰ تا ۱۵) ----
    hours = parse_freshness_hours(ad.get("time_text") or "")
    if hours is None:
        fresh = 4.0
    elif hours <= 1:
        fresh = 15.0
        reasons.append("کمتر از ۱ ساعت پیش")
    elif hours <= 3:
        fresh = 13.0
        reasons.append("زیر ۳ ساعت")
    elif hours <= 12:
        fresh = 10.0
        reasons.append("آگهی امروز")
    elif hours <= 24:
        fresh = 7.0
    elif hours <= 48:
        fresh = 4.0
    elif hours <= 72:
        fresh = 2.0
    else:
        fresh = 0.5
    breakdown["freshness"] = fresh

    # ---- کیفیت متن / وضعیت ----
    if category == "mobile":
        q = mobile_status_score(status) * 1.2
        q += mobile_storage_score(title) * 0.7
        q += mobile_ram_score(title) * 0.55
        q = min(15.0, q)
        if mobile_status_score(status) >= 3.5:
            reasons.append("وضعیت ظاهری خوب")
    elif category in ("apartment-sell", "apartment-rent"):
        q = 5.0
        normalized_text = _normalize_fa_digits(full_text)
        for keyword, bonus in (("نوساز", 3.5), ("فول", 1.5), ("لوکس", 1.5), ("بازسازی", 1.5), ("سند تک", 2.5), ("کلید نخورده", 2.5)):
            if _normalize_fa_digits(keyword) in normalized_text:
                q += bonus
        q = min(15.0, q)
        property_features = evaluate_real_estate_features(ad, description)
        breakdown["facilities"] = property_features["score"]
        ad["facility_display"] = property_features.get("display") or ""
        ad["property_features"] = property_features.get("features") or {}
        if property_features.get("present"):
            reasons.append("امکانات: " + "، ".join(property_features["present"]))
        if q >= 10:
            reasons.append("متن املاک مثبت")
    else:
        q = car_body_score(full_text) * 1.6 + car_motor_score(full_text, description) * 1.2
        if car_valuation.get("color_bonus_percent"):
            q += 1.2
        q = min(15.0, q)
        # اثر ارزشی واقعی رنگ و شاسی به امتیاز هم منتقل می‌شود.
        condition_score = (1.4 if car_valuation.get("color_bonus_percent") else 0.0) - min(
            10.0, float(car_valuation.get("total_deduction_percent") or 0.0) * 0.55
        )
        breakdown["condition"] = round(condition_score, 2)
        ad["car_valuation"] = car_valuation
        valuation_parts = list(car_valuation.get("notes") or [])
        if car_valuation.get("fair_market_value"):
            valuation_parts.append(f"ارزش منصفانه ≈ {format_toman(car_valuation['fair_market_value'])}")
        if valuation_parts:
            ad["car_valuation_display"] = " | ".join(valuation_parts)
        if car_body_score(full_text) >= 3.5:
            reasons.append("وضعیت بدنه/متن مثبت")
    breakdown["quality"] = round(q, 2)

    # ---- نوع فروشنده (۰ تا ۸) ----
    seller = 2.5 if ad.get("is_shop") else 6.5
    reasons.append("فروشگاه/آژانس" if ad.get("is_shop") else "آگهی شخصی")
    if ad.get("is_nardeban"):
        seller -= 1.5
    breakdown["seller"] = round(max(0.0, min(8.0, seller)), 2)

    # ---- سیگنال‌های متن توضیحات نیز به اندازه‌ی عنوان بررسی می‌شوند ----
    if category == "mobile":
        neg_list, pos_list = MOBILE_NEG_SIGNALS, MOBILE_POS_SIGNALS
    elif category in ("apartment-sell", "apartment-rent"):
        neg_list, pos_list = RE_NEG_SIGNALS, RE_POS_SIGNALS
    else:
        neg_list, pos_list = CAR_NEG_SIGNALS, CAR_POS_SIGNALS
    neg_hits = _signal_hits(full_text, neg_list)
    hard = {"طرح", "کپی", "مشابه اصل", "high copy", "تصادفی", "چپی", "اتاق تعویض", "تعویض موتور", "قولنامه‌ای"}
    neg_penalty = min(30.0, sum(10.0 if hit in hard else 4.5 for hit in neg_hits))
    breakdown["negative"] = round(-neg_penalty, 2)
    if neg_hits:
        reasons.append("ریسک: " + "، ".join(neg_hits[:3]))

    pos_hits = _signal_hits(full_text, pos_list)
    pos_bonus = min(8.0, len(pos_hits) * 2.0)
    breakdown["positive"] = round(pos_bonus, 2)
    if pos_hits and not neg_hits:
        reasons.append("نشانه‌های مثبت در متن")

    # ---- کامل بودن ----
    comp = 0.0
    if ad.get("image_url"):
        comp += 2.0
    image_count = ad.get("image_count") or 0
    if image_count >= 3:
        comp += 2.5
    elif image_count >= 1:
        comp += 1.0
    if price:
        comp += 1.5
    if description:
        comp += 0.8
    breakdown["completeness"] = round(min(6.0, comp), 2)

    total = sum(float(value or 0) for value in breakdown.values())
    total = round(max(0.0, min(100.0, total)), 1)
    hard_hit = any(hit in hard for hit in neg_hits)
    pass_filter = total >= cfg["min_score"] and breakdown["negative"] > -18 and not hard_hit
    reason = " · ".join(dict.fromkeys(reasons[:5])) if reasons else "امتیاز ترکیبی قیمت و کیفیت"

    return {
        "value_score": total,
        "score_breakdown": breakdown,
        "score_reason": reason,
        "pass_filter": pass_filter,
        "neg_hits": neg_hits,
        "pos_hits": pos_hits,
        "car_valuation": car_valuation,
        "property_features": property_features,
    }


def ensure_ads_rated(ads: List[Dict], category: str, profile: str = "balanced", force: bool = False) -> List[Dict]:
    """تضمین می‌کند هیچ کارت نتیجه‌ای بدون امتیاز باقی نماند."""
    prices = []
    for ad in ads:
        try:
            value = int(ad.get("price_num"))
            if value > 0:
                prices.append(value)
        except (TypeError, ValueError):
            pass
    global_avg = clean_avg(prices) if prices else 0.0
    for ad in ads:
        if not force and ad.get("value_score") is not None:
            continue
        try:
            local_avg = float(ad.get("model_avg") or ad.get("market_avg") or global_avg or 0)
        except (TypeError, ValueError):
            local_avg = global_avg
        result = score_ad_smart(
            ad,
            local_avg,
            category,
            profile,
            desc=ad.get("description") or "",
            strict_price=False,
        )
        ad.update(result)
        if local_avg > 0:
            ad.setdefault("market_avg", int(local_avg))
    return ads

# سازگاری با کدهای قدیمی که هنوز این نام‌ها را صدا می‌زنند
def mobile_value_score(ad: Dict, avg: float) -> float:
    return score_ad_smart(ad, avg, "mobile", "balanced").get("value_score", 0)


def car_value_score(ad: Dict, avg: float) -> float:
    return score_ad_smart(ad, avg, "car", "balanced").get("value_score", 0)


# ====================== Divar API ======================
MAX_PAGES_PER_SEARCH = 20          # حداکثر صفحات (هر صفحه معمولاً ~20-24 آگهی)
MAX_ADS_PER_MODEL = 300            # سقف آگهی برای هر مدل

def search_divar_once(payload: Dict, retries: int = 3) -> Dict:
    """یک درخواست به API دیوار با retry و backoff"""
    last_err = None
    for attempt in range(max(1, retries)):
        try:
            r = requests.post(
                "https://api.divar.ir/v8/postlist/w/search",
                headers=HEADERS,
                json=payload,
                timeout=28,
            )
            if r.status_code == 429:
                time.sleep(1.2 * (attempt + 1))
                last_err = "rate_limited"
                continue
            if r.status_code >= 500:
                time.sleep(0.8 * (attempt + 1))
                last_err = f"server_{r.status_code}"
                continue
            r.raise_for_status()
            data = r.json()
            if not isinstance(data, dict):
                return {}
            return data
        except Exception as e:
            last_err = e
            time.sleep(0.6 * (attempt + 1))
    print(f"[divar] search error after {retries} tries:", last_err)
    return {}


def _extract_pagination(raw: Dict):
    """استخراج اطلاعات pagination از پاسخ (ساختارهای مختلف API)"""
    # حالت ۱: pagination.data
    pag = raw.get("pagination")
    if isinstance(pag, dict):
        data = pag.get("data")
        if data:
            return data
        # گاهی خود pagination همان data است
        if pag.get("@type") or pag.get("last_post_date") or pag.get("page"):
            return pag

    # حالت ۲: داخل action_log
    try:
        info = (
            raw.get("action_log", {})
            .get("server_side_info", {})
            .get("info", {})
        )
        if info.get("pagination_data"):
            return info["pagination_data"]
    except Exception:
        pass

    return None


def _has_next_page(raw: Dict, new_count: int) -> bool:
    """تشخیص وجود صفحه بعدی با چند روش مختلف"""
    # روش رسمی
    try:
        flag = (
            raw.get("action_log", {})
            .get("server_side_info", {})
            .get("info", {})
            .get("has_next_page")
        )
        if flag is True:
            return True
        if flag is False:
            return False
    except Exception:
        pass

    # اگر صفحه پر بوده (حدود ۲۰+ آگهی POST_ROW) احتمالاً صفحه بعد وجود دارد
    if new_count >= 18:
        return True

    return False


def search_divar(payload: Dict, max_pages: int = MAX_PAGES_PER_SEARCH, max_ads: int = MAX_ADS_PER_MODEL) -> Dict:
    """
    جستجوی چندصفحه‌ای دیوار.
    از pagination_data برای گرفتن صفحات بعدی استفاده می‌کند.
    خروجی نهایی مثل پاسخ تک‌صفحه‌ای است ولی list_widgets شامل همه آگهی‌هاست.
    """
    all_widgets = []
    first_response = None
    current_payload = dict(payload)
    seen_tokens = set()

    for page in range(1, max_pages + 1):
        raw = search_divar_once(current_payload)
        if not raw:
            break
        if first_response is None:
            first_response = raw

        widgets = raw.get("list_widgets") or []
        new_count = 0
        for w in widgets:
            if w.get("widget_type") != "POST_ROW":
                # بعضی وقت‌ها ویجت‌های دیگر هم آگهی دارند
                if w.get("widget_type") not in ("POST_ROW", "LEGACY_POST_ROW", "POST"):
                    continue
            token = (w.get("data") or {}).get("token")
            if not token or token in seen_tokens:
                continue
            seen_tokens.add(token)
            all_widgets.append(w)
            new_count += 1

        pagination = _extract_pagination(raw)
        has_next = _has_next_page(raw, new_count)

        if not has_next or not pagination or new_count == 0 or len(all_widgets) >= max_ads:
            break

        # آماده‌سازی payload صفحه بعد
        current_payload = dict(payload)
        current_payload["pagination_data"] = pagination
        # کمی فاصله برای جلوگیری از rate-limit
        time.sleep(0.4)

    if first_response is None:
        return {}

    result = dict(first_response)
    result["list_widgets"] = all_widgets
    return result


def _parse_re_price_fields(price_str: str) -> Dict[str, Any]:
    """استخراج فروش/ودیعه/اجاره و نرخ تبدیل استاندارد از متن کارت دیوار."""
    out: Dict[str, Any] = {
        "price_num": None,
        "rent_num": None,
        "credit_num": None,
        "rent_equivalent_num": None,
        "deposit_equivalent_num": None,
        "conversion_rule": "",
        "rental_equivalent_display": "",
        "deposit_equivalent_display": "",
        "price_display": price_str or "",
    }
    if not price_str:
        return out

    source = normalize_digits(str(price_str)).replace("٬", ",")
    rent_label = r"اجاره(?:[ٔی])?(?:\s*ماهانه)?|rent"
    credit_label = r"ودیعه|رهن(?:\s*کامل)?|deposit|mortgage"
    has_rental_label = bool(re.search(rf"(?:{rent_label}|{credit_label})", source, flags=re.IGNORECASE))
    credit = _extract_labeled_money(source, credit_label)
    rent = _extract_labeled_money(source, rent_label)

    # بعضی کارت‌ها برچسب را حذف کرده و صرفاً دو مبلغ پشت سر هم می‌فرستند.
    amounts: List[int] = []
    amount_pattern = r"\d+(?:,\d{3})*(?:\.\d+)?\s*(?:هزار|میلیون|میلیارد)?"
    for match in re.finditer(amount_pattern, source, flags=re.IGNORECASE):
        parsed = parse_money_amount(match.group(0))
        if parsed is not None:
            amounts.append(parsed)
    if has_rental_label and (credit is None or rent is None) and len(amounts) >= 2:
        biggest, smallest = max(amounts[:2]), min(amounts[:2])
        credit = credit if credit is not None else biggest
        rent = rent if rent is not None else smallest
    elif has_rental_label and credit is None and amounts:
        credit = max(amounts)

    if has_rental_label or credit is not None or rent is not None:
        out["credit_num"] = credit
        out["rent_num"] = rent
        equivalents = rental_equivalents(credit, rent)
        out.update(equivalents)
        # معادل ودیعه، معیار مقایسه‌ی یکنواختِ آگهی‌های اجاره است.
        out["price_num"] = equivalents.get("deposit_equivalent_num")
        out["price_display"] = format_real_estate_price(credit, rent, str(price_str))
    else:
        amount = parse_money_amount(source)
        out["price_num"] = amount
        out["price_display"] = format_toman(amount) if amount is not None else str(price_str)
    return out


def _image_url_from_post_data(data: Dict[str, Any]) -> str:
    image = data.get("image_url") or data.get("image") or ""
    if isinstance(image, str):
        return image
    if isinstance(image, dict):
        return str(image.get("url") or image.get("src") or image.get("thumbnail_url") or "")
    if isinstance(image, list) and image:
        first = image[0]
        if isinstance(first, dict):
            return str(first.get("url") or first.get("src") or first.get("thumbnail_url") or "")
        return str(first or "")
    return ""


def _price_text_from_value(value: Any, depth: int = 0) -> str:
    """یک مقدار قیمت (از جمله شکل‌های objectی API) را به متن قابل نمایش تبدیل می‌کند."""
    if value is None or isinstance(value, bool) or depth > 4:
        return ""
    if isinstance(value, str):
        return value.strip()
    if isinstance(value, (int, float)):
        return str(value)
    if isinstance(value, dict):
        # قیمت در نسخه‌های مختلف API گاهی به صورت {value: ...} یا
        # {display_value: ...} برمی‌گردد؛ str(dict) هم ناپایدار است و هم در
        # کارت نمایش مناسبی ندارد.
        for key in (
            "display_value", "formatted", "formatted_value", "text", "label",
            "price", "price_value", "amount", "value",
        ):
            text = _price_text_from_value(value.get(key), depth + 1)
            if text:
                return text
        return ""
    if isinstance(value, (list, tuple)):
        for item in value:
            text = _price_text_from_value(item, depth + 1)
            if text:
                return text
    return ""


def _extract_listing_price_text(post: Dict[str, Any]) -> str:
    """قیمت کارت را با fallbackهای schemaهای فعلی دیوار پیدا می‌کند.

    ``middle_description_text`` شکل رایج کارت‌های جست‌وجو است، اما برای بعضی
    آگهی‌های خودرو و موبایل خالی است و عدد واقعی در ``price`` یا ``webengage``
    می‌آید. این fallback مانع خالی‌ماندن قیمت آن کارت‌ها می‌شود.
    """
    if not isinstance(post, dict):
        return ""

    for key in ("middle_description_text", "price", "price_text", "price_display", "price_value"):
        text = _price_text_from_value(post.get(key))
        if text:
            return text

    nested_sources: List[Any] = [
        post.get("webengage"),
        post.get("metadata"),
        post.get("action"),
        post.get("payload"),
    ]
    action = post.get("action")
    if isinstance(action, dict):
        nested_sources.extend((action.get("payload"), action.get("data")))

    for source in nested_sources:
        if not isinstance(source, dict):
            continue
        for key in ("price", "price_text", "price_display", "price_value", "amount"):
            text = _price_text_from_value(source.get(key))
            if text:
                return text
    return ""


def _standard_price_fields(raw_price: Any) -> Dict[str, Any]:
    """فیلدهای قیمت برای دسته‌های غیر اجاره (موبایل، خودرو و فروش ملک)."""
    raw_text = _price_text_from_value(raw_price)
    price_num = parse_money_amount(raw_text)
    display = format_toman(price_num) if price_num is not None else raw_text
    return {
        "price_num": price_num,
        "price_display": display or "توافقی",
        "price_raw": raw_text,
    }


def extract_ads(data: Dict, category: str = "") -> List[Dict]:
    """کارت‌های جست‌وجو را به ساختار داخلی با قیمت قابل اتکا تبدیل می‌کند.

    جزئیات ودیعه/اجاره فقط برای ``apartment-rent`` استخراج می‌شوند. این جداسازی
    عمدی است: متن یا metadata یک آگهی فروش، خودرو یا موبایل هرگز نباید باعث شود
    کارت آن مانند کارت اجاره نمایش داده شود.
    """
    ads: List[Dict] = []
    is_rental_category = category == "apartment-rent"
    for widget in data.get("list_widgets", []):
        if not isinstance(widget, dict):
            continue
        widget_type = widget.get("widget_type")
        if widget_type not in ("POST_ROW", "LEGACY_POST_ROW", "POST"):
            continue
        post = widget.get("data", {}) or {}
        if not isinstance(post, dict):
            continue
        token = post.get("token")
        if not token:
            continue
        raw_price = _extract_listing_price_text(post)
        red_text = str(post.get("red_text") or "")
        top_text = str(post.get("top_description_text") or "")
        bottom_text = str(post.get("bottom_description_text") or "")
        time_text = bottom_text
        if any(word in bottom_text for word in ("املاک", "آژانس", "مشاور", "بنگاه")):
            time_text = top_text if any(word in top_text for word in ("پیش", "ساعت", "دقیقه", "روز")) else ""

        if is_rental_category:
            price_fields = _parse_re_price_fields(raw_price)
            price_num = price_fields.get("price_num")
            if price_num is None:
                price_num = parse_money_amount(raw_price)
            display = price_fields.get("price_display") or (
                format_toman(price_num) if price_num is not None else raw_price
            )
        else:
            price_fields = _standard_price_fields(raw_price)
            price_num = price_fields.get("price_num")
            display = price_fields.get("price_display")

        is_agency = any(word in (red_text + bottom_text + top_text) for word in ("املاک", "آژانس", "مشاورین", "فروشگاه"))
        ad = {
            "token": token,
            "category": category,
            "title": post.get("title") or "",
            "price": display or "توافقی",
            "price_display": display or "توافقی",
            "price_raw": price_fields.get("price_raw", raw_price),
            "price_num": price_num,
            "status": top_text,
            "red_text": red_text,
            "time_text": time_text,
            "seller_text": bottom_text,
            "image_url": _image_url_from_post_data(post),
            "image_count": post.get("image_count") or 0,
            "is_nardeban": "نردبان" in red_text,
            "is_shop": is_agency or ("فروشگاه" in red_text) or ("فروشگاه" in top_text),
            "car_color": post.get("color") or post.get("car_color") or "",
            "body_status_detail": post.get("body_status") or "",
            "chassis_status_detail": post.get("chassis_status") or "",
            "description": "",
            "description_search_text": "",
            "link": f"https://divar.ir/v/{token}",
        }
        if is_rental_category:
            ad.update({
                "rent_num": price_fields.get("rent_num"),
                "credit_num": price_fields.get("credit_num"),
                "rent_equivalent_num": price_fields.get("rent_equivalent_num"),
                "deposit_equivalent_num": price_fields.get("deposit_equivalent_num"),
                "conversion_rule": price_fields.get("conversion_rule") or "",
                "rental_equivalent_display": price_fields.get("rental_equivalent_display") or "",
                "deposit_equivalent_display": price_fields.get("deposit_equivalent_display") or "",
            })
        ads.append(ad)
    return ads

def _is_wide_price(min_p, max_p, is_mobile=True):
    """آیا بازه قیمت تقریباً نامحدود است؟"""
    try:
        mn = int(str(min_p).replace(",", "") or 0)
        mx = int(str(max_p).replace(",", "") or 0)
    except Exception:
        return True
    if is_mobile:
        return mn <= 1_000_000 and mx >= 500_000_000
    return mn <= 50_000_000 and mx >= 2_000_000_000


def build_mobile_payload(
    city_ids, brand_models, min_price="1000000", max_price="900000000",
    recent_ads=None, sort="sort_date", has_photo=None, has_installment=False,
    business_type="ALL", status=None, internal_storage=None, ram_memory=None,
    sim_card_slot=None, base_color=None,
):
    """
    payload مینیمال‌تر: فقط فیلترهایی که واقعاً لازم هستند اضافه می‌شوند.
    این باعث می‌شود تعداد آگهی‌ها خیلی نزدیک به سایت دیوار باشد.
    """
    city_ids = normalize_city_ids(city_ids)
    if not brand_models:
        brand_models = [m for lst in MOBILE_MODELS.values() for m in lst]

    form = {
        "category": {"str": {"value": "mobile-phones"}},
        "brand_model": {"repeated_string": {"value": brand_models}},
    }

    # فقط اگر کاربر بازه قیمت محدود کرده باشد
    if not _is_wide_price(min_price, max_price, is_mobile=True):
        form["price"] = {"number_range": {"minimum": str(min_price), "maximum": str(max_price)}}

    # recent_ads فقط اگر مقدار داشته باشد (None یا "" یعنی همه آگهی‌ها)
    if recent_ads and str(recent_ads).strip() and str(recent_ads) not in ("all", "0", "none"):
        form["recent_ads"] = {"str": {"value": str(recent_ads)}}

    if has_photo is True:
        form["has-photo"] = {"boolean": {"value": True}}
    # اگر False یا None باشد اصلاً کلید را نمی‌فرستیم تا آگهی‌های بدون عکس هم بیاید

    if has_installment:
        form["has_installment_sale"] = {"boolean": {"value": True}}

    if business_type == "personal":
        form["goods-business-type"] = {"repeated_string": {"value": ["personal"]}}
    elif business_type == "marketplace":
        form["goods-business-type"] = {"repeated_string": {"value": ["marketplace"]}}

    # فیلترهای جزئی فقط اگر کاربر انتخاب کرده باشد (نه ALL)
    def _add_if_specific(key, values):
        if values and values != ["ALL_POSSIBLE_OPTIONS"] and "ALL_POSSIBLE_OPTIONS" not in values:
            form[key] = {"repeated_string": {"value": values}}

    _add_if_specific("status", status)
    _add_if_specific("internal_storage", internal_storage)
    _add_if_specific("ram_memory", ram_memory)
    _add_if_specific("sim_card_slot", sim_card_slot)
    _add_if_specific("base_color", base_color)

    return {
        "city_ids": city_ids,
        "source_view": "FILTER",
        "disable_recommendation": False,
        "map_state": {"camera_info": {"bbox": {}}},
        "search_data": {
            "form_data": {"data": form},
            "server_payload": {
                "@type": "type.googleapis.com/widgets.SearchData.ServerPayload",
                "additional_form_data": {"data": {"sort": {"str": {"value": sort or "sort_date"}}}},
            },
        },
        "previous_place_ids": [],
    }


def build_real_estate_payload(
    city_ids,
    category="apartment-sell",
    recent_ads=None,
    sort="sort_date",
    has_photo=None,
    min_price=None,
    max_price=None,
    min_rent=None,
    max_rent=None,
    min_credit=None,
    max_credit=None,
    size_min=None,
    size_max=None,
    rooms=None,
    building_age_min=None,
    building_age_max=None,
    floor_min=None,
    floor_max=None,
    parking=None,
    elevator=None,
    warehouse=None,
    balcony=None,
    deed_type=None,
    business_type=None,
):
    """
    payload املاک مسکونی:
    - apartment-sell  → فروش آپارتمان
    - apartment-rent  → اجاره آپارتمان
    فقط فیلترهای انتخاب‌شده کاربر ارسال می‌شود.
    """
    if category not in ("apartment-sell", "apartment-rent"):
        category = "apartment-sell"
    city_ids = normalize_city_ids(city_ids)

    form = {
        "category": {"str": {"value": category}},
    }

    if recent_ads and str(recent_ads).strip() and str(recent_ads) not in ("all", "0", "none"):
        form["recent_ads"] = {"str": {"value": str(recent_ads)}}

    if has_photo is True:
        form["has-photo"] = {"boolean": {"value": True}}

    # متراژ
    try:
        smin = int(size_min) if size_min not in (None, "", "0") else None
        smax = int(size_max) if size_max not in (None, "") else None
    except Exception:
        smin = smax = None
    if smin is not None or smax is not None:
        rng = {}
        if smin is not None:
            rng["minimum"] = str(smin)
        if smax is not None:
            rng["maximum"] = str(smax)
        form["size"] = {"number_range": rng}

    # اتاق
    if rooms and isinstance(rooms, list) and rooms:
        form["rooms"] = {"repeated_string": {"value": rooms}}

    # سن بنا
    try:
        amin = int(building_age_min) if building_age_min not in (None, "") else None
        amax = int(building_age_max) if building_age_max not in (None, "") else None
    except Exception:
        amin = amax = None
    if amin is not None or amax is not None:
        rng = {}
        if amin is not None:
            rng["minimum"] = str(amin)
        if amax is not None:
            rng["maximum"] = str(amax)
        form["building-age"] = {"number_range": rng}

    # طبقه
    try:
        fmin = int(floor_min) if floor_min not in (None, "") else None
        fmax = int(floor_max) if floor_max not in (None, "") else None
    except Exception:
        fmin = fmax = None
    if fmin is not None or fmax is not None:
        rng = {}
        if fmin is not None:
            rng["minimum"] = str(fmin)
        if fmax is not None:
            rng["maximum"] = str(fmax)
        form["floor"] = {"number_range": rng}

    # امکانات
    if parking is True:
        form["parking"] = {"boolean": {"value": True}}
    if elevator is True:
        form["elevator"] = {"boolean": {"value": True}}
    if warehouse is True:
        form["warehouse"] = {"boolean": {"value": True}}
    if balcony is True:
        form["balcony"] = {"boolean": {"value": True}}

    if deed_type and isinstance(deed_type, list) and deed_type:
        form["deed_type"] = {"repeated_string": {"value": deed_type}}

    if business_type == "personal":
        form["business-type"] = {"repeated_string": {"value": ["personal"]}}
    elif business_type == "business":
        form["business-type"] = {"repeated_string": {"value": ["business"]}}

    # قیمت فروش
    if category == "apartment-sell":
        try:
            pmin = int(min_price) if min_price not in (None, "", "0") else None
            pmax = int(max_price) if max_price not in (None, "") else None
        except Exception:
            pmin = pmax = None
        if pmin is not None or pmax is not None:
            # فقط اگر بازه خیلی باز نباشد
            if not (pmin in (None, 0) and (pmax is None or pmax >= 500_000_000_000)):
                rng = {}
                if pmin is not None:
                    rng["minimum"] = str(pmin)
                if pmax is not None:
                    rng["maximum"] = str(pmax)
                form["price"] = {"number_range": rng}

    # اجاره و ودیعه
    if category == "apartment-rent":
        try:
            rmin = int(min_rent) if min_rent not in (None, "", "0") else None
            rmax = int(max_rent) if max_rent not in (None, "") else None
        except Exception:
            rmin = rmax = None
        if rmin is not None or rmax is not None:
            rng = {}
            if rmin is not None:
                rng["minimum"] = str(rmin)
            if rmax is not None:
                rng["maximum"] = str(rmax)
            form["rent"] = {"number_range": rng}
        try:
            cmin = int(min_credit) if min_credit not in (None, "", "0") else None
            cmax = int(max_credit) if max_credit not in (None, "") else None
        except Exception:
            cmin = cmax = None
        if cmin is not None or cmax is not None:
            rng = {}
            if cmin is not None:
                rng["minimum"] = str(cmin)
            if cmax is not None:
                rng["maximum"] = str(cmax)
            form["credit"] = {"number_range": rng}

    return {
        "city_ids": city_ids,
        "source_view": "FILTER",
        "disable_recommendation": False,
        "map_state": {"camera_info": {"bbox": {}}},
        "search_data": {
            "form_data": {"data": form},
            "server_payload": {
                "@type": "type.googleapis.com/widgets.SearchData.ServerPayload",
                "additional_form_data": {"data": {"sort": {"str": {"value": sort or "sort_date"}}}},
            },
        },
        "previous_place_ids": [],
    }


def build_car_payload(
    city_ids, brand_models, min_price="100000000", max_price="5000000000",
    recent_ads=None, sort="sort_date", has_photo=None, has_installment=False,
    has_video=False, year_min=None, year_max=None, usage_min=None, usage_max=None,
    body_status=None, chassis_status=None, motor_status=None, gearbox=None,
    fuel_type=None, color=None, origin=None,
):
    """
    payload مینیمال برای خودرو.
    فیلترهای اجباری قبلی (سال، کارکرد، شاسی، موتور، ...) فقط در صورت انتخاب کاربر اعمال می‌شوند.
    """
    city_ids = normalize_city_ids(city_ids)
    if not brand_models:
        brand_models = [m for lst in CAR_MODELS.values() for m in lst]

    form = {
        "category": {"str": {"value": "light"}},
        "brand_model": {"repeated_string": {"value": brand_models}},
    }

    if not _is_wide_price(min_price, max_price, is_mobile=False):
        form["price"] = {"number_range": {"minimum": str(min_price), "maximum": str(max_price)}}

    if recent_ads and str(recent_ads).strip() and str(recent_ads) not in ("all", "0", "none"):
        form["recent_ads"] = {"str": {"value": str(recent_ads)}}

    if has_photo is True:
        form["has-photo"] = {"boolean": {"value": True}}

    if has_installment:
        form["has_installment_sale"] = {"boolean": {"value": True}}

    if has_video:
        form["has-video"] = {"boolean": {"value": True}}

    # سال ساخت فقط اگر واقعاً محدود شده باشد (پیش‌فرض‌های خیلی باز = بدون فیلتر)
    try:
        y_min = int(year_min) if year_min not in (None, "", "0") else None
        y_max = int(year_max) if year_max not in (None, "", "0") else None
    except Exception:
        y_min = y_max = None
    # اگر بازه تقریباً کل سال‌های ممکن باشد، فیلتر نفرست
    if y_min is not None or y_max is not None:
        if not ( (y_min is None or y_min <= 1360) and (y_max is None or y_max >= 1404) ):
            form["production-year"] = {
                "number_range": {
                    "minimum": str(y_min if y_min is not None else 1300),
                    "maximum": str(y_max if y_max is not None else 1405),
                }
            }

    # کارکرد — فقط اگر محدودیت معنادار باشد
    try:
        u_min = int(usage_min) if usage_min not in (None, "") else None
        u_max = int(usage_max) if usage_max not in (None, "") else None
    except Exception:
        u_min = u_max = None
    if (u_min is not None and u_min > 0) or (u_max is not None and u_max < 800000):
        form["usage"] = {
            "number_range": {
                "minimum": str(u_min if u_min is not None else 0),
                "maximum": str(u_max if u_max is not None else 10000000),
            }
        }

    def _add_if_specific(key, values, as_str=False):
        if not values:
            return
        if isinstance(values, list):
            if values == ["ALL_POSSIBLE_OPTIONS"] or "ALL_POSSIBLE_OPTIONS" in values:
                return
            if as_str:
                form[key] = {"str": {"value": values[0]}}
            else:
                form[key] = {"repeated_string": {"value": values}}
        else:
            form[key] = {"str": {"value": values}}

    _add_if_specific("body_status", body_status)
    _add_if_specific("brand_model_manufacturer_origin", origin)
    _add_if_specific("color", color)
    _add_if_specific("fuel_type", fuel_type)

    if chassis_status and chassis_status != ["ALL_POSSIBLE_OPTIONS"] and "ALL_POSSIBLE_OPTIONS" not in (chassis_status or []):
        form["chassis_status"] = {"str": {"value": chassis_status[0] if isinstance(chassis_status, list) else chassis_status}}

    if motor_status and motor_status != ["ALL_POSSIBLE_OPTIONS"] and "ALL_POSSIBLE_OPTIONS" not in (motor_status or []):
        form["motor_status"] = {"str": {"value": motor_status[0] if isinstance(motor_status, list) else motor_status}}

    if gearbox and gearbox != ["ALL_POSSIBLE_OPTIONS"] and "ALL_POSSIBLE_OPTIONS" not in (gearbox or []):
        form["gearbox"] = {"str": {"value": gearbox[0] if isinstance(gearbox, list) else gearbox}}

    return {
        "city_ids": city_ids,
        "source_view": "FILTER",
        "disable_recommendation": False,
        "map_state": {"camera_info": {"bbox": {}}},
        "search_data": {
            "form_data": {"data": form},
            "server_payload": {
                "@type": "type.googleapis.com/widgets.SearchData.ServerPayload",
                "additional_form_data": {"data": {"sort": {"str": {"value": sort or "sort_date"}}}},
            },
        },
        "previous_place_ids": [],
    }


def clean_avg(prices: List[int]) -> float:
    """میانگین بازار پایدار (حذف پرت + هسته میانی)"""
    return market_baseline(prices)


def finalize_ads(
    ads: List[Dict],
    keywords: List[str],
    negative_keywords: List[str],
    category: str = "mobile",
    smart_profile: str = "balanced",
) -> List[Dict]:
    """مرحله‌ی مشترک همه‌ی دسته‌ها: detail، امتیاز و سپس کلیدواژه‌ها.

    ترتیب مهم است: ابتدا توضیحات واقعی و امکانات detail خوانده می‌شوند، سپس
    امتیاز به‌روز می‌شود و در پایان کلمات منفی/مثبت روی همان متن کامل اعمال
    می‌گردند.
    """
    hydrate_ads_with_details(ads)
    ensure_ads_rated(ads, category, smart_profile, force=True)
    ads = filter_negative_keywords(ads, negative_keywords)
    ads = enrich_ads_with_keywords(ads, keywords)
    return ads


# ====================== worker functions (parallel per-model) ======================
def process_model_smart(
    model,
    category,
    city_ids,
    recent_ads,
    business_type,
    min_smart_score,
    smart_profile="balanced",
    require_details=False,
):
    """
    جستجوی هوشمند دو مرحله‌ای:
    ۱) گرفتن حداکثر آگهی‌ها + امتیاز سریع (عنوان/قیمت/تازگی)
    ۲) خواندن توضیحات فقط برای برترین‌ها و امتیاز نهایی
    """
    try:
        profile = smart_profile if smart_profile in SMART_PROFILES else "balanced"
        cfg = SMART_PROFILES[profile]
        # آستانه = حداکثرِ (حداقل پروفایل، مقدار کاربر)
        # یعنی کاربر با عدد بالاتر می‌تواند سخت‌گیرانه‌تر کند؛ با عدد پایین‌تر هم نمی‌تواند از کف پروفایل رد شود
        # مگر اینکه صریحاً پروفایل open باشد و کاربر عدد کم بگذارد
        user_min = float(min_smart_score) if min_smart_score not in (None, "") else cfg["min_score"]
        try:
            user_min = float(user_min)
        except Exception:
            user_min = cfg["min_score"]
        effective_min = max(cfg["min_score"], user_min)

        if category == "mobile":
            payload = build_mobile_payload(
                city_ids, [model],
                recent_ads=recent_ads,
                has_photo=None,
                has_installment=False,
                business_type=business_type or "ALL",
            )
        else:
            payload = build_car_payload(
                city_ids, [model],
                recent_ads=recent_ads,
                has_photo=None,
                has_installment=False,
            )

        with SEARCH_SEMAPHORE:
            raw = search_divar(payload)
        ads = extract_ads(raw, category)
        # فیلتر دقیق مدل — جلوگیری از قاطی شدن مدل‌های دیگر
        ads = [a for a in ads if is_model_match(a.get("title") or "", model)]
        # وقتی کلمه‌ی کلیدی/منفی داریم، توضیحات باید قبل از رتبه‌بندی تمام
        # کاندیداها خوانده شود؛ محدود کردن به top-N باعث خطای قبلی می‌شد.
        if require_details:
            hydrate_ads_with_details(ads)
        else:
            hydrate_ads_with_missing_prices(ads)
        prices = [a["price_num"] for a in ads if a.get("price_num")]
        if not prices:
            return model, None, []

        avg = clean_avg(prices)
        if avg <= 0:
            return model, None, []

        # مرحله ۱: امتیاز سریع بدون توضیحات
        phase1 = []
        for ad in ads:
            if not ad.get("price_num"):
                continue
            result = score_ad_smart(ad, avg, category, profile, desc=ad.get("description") or "")
            ad.update(result)
            ad["model_avg"] = int(avg)
            ad["smart_profile"] = profile
            phase1.append(ad)

        phase1.sort(key=lambda x: x.get("value_score") or 0, reverse=True)

        # مرحله ۲: عمیق‌سازی روی برترین‌ها
        deep_n = min(cfg["deep_fetch"], len(phase1))
        for ad in phase1[:deep_n]:
            try:
                details = get_post_details_cached(ad["token"])
                _merge_post_details(ad, details)
            except Exception:
                pass
            desc = ad.get("description") or ""
            if desc or ad.get("detail_text"):
                ad["_desc"] = desc[:500]
                result = score_ad_smart(ad, avg, category, profile, desc=desc)
                ad.update(result)

        phase1.sort(key=lambda x: x.get("value_score") or 0, reverse=True)

        # فیلتر نهایی بر اساس آستانه
        best = [a for a in phase1 if (a.get("value_score") or 0) >= effective_min]
        best = best[: cfg["max_results"]]

        # fallback محدود: فقط اگر نتیجه خیلی کم بود و هنوز زیر میانگین واقعی‌اند
        if len(best) < 3:
            extra = [
                a for a in phase1
                if a not in best
                and a.get("price_num")
                and a["price_num"] <= avg * 0.95
                and (a.get("value_score") or 0) >= max(3.0, effective_min * 0.6)
            ]
            for a in extra[: max(4, 6 - len(best))]:
                if not a.get("score_reason"):
                    a["score_reason"] = "نزدیک میانگین / فرصت قابل بررسی"
                best.append(a)

        # پاکسازی فیلد داخلی
        for a in best:
            a.pop("_desc", None)

        if best:
            return model, {
                "avg": int(avg),
                "count": len(ads),
                "scored": len(best),
                "profile": profile,
            }, best
        return model, None, []
    except Exception as e:
        print(f"smart model error {model}:", e)
        return model, None, []


def process_model_avg(model, category, city_ids, recent_ads, data):
    try:
        has_photo = data.get("has_photo")
        if has_photo is False:
            has_photo = None  # فیلتر عکس اعمال نشود

        if category == "mobile":
            payload = build_mobile_payload(
                city_ids, [model],
                recent_ads=recent_ads,
                has_photo=has_photo,
                has_installment=data.get("has_installment", False),
                business_type=data.get("business_type", "ALL"),
                min_price=str(data.get("min_price", "1000000")),
                max_price=str(data.get("max_price", "900000000")),
                sort=data.get("sort", "sort_date"),
                status=data.get("status"),
                internal_storage=data.get("internal_storage"),
                ram_memory=data.get("ram_memory"),
                sim_card_slot=data.get("sim_card_slot"),
                base_color=data.get("base_color"),
            )
        else:
            payload = build_car_payload(
                city_ids, [model],
                recent_ads=recent_ads,
                has_photo=has_photo,
                has_installment=data.get("has_installment", False),
                has_video=data.get("has_video", False),
                min_price=str(data.get("min_price", "100000000")),
                max_price=str(data.get("max_price", "5000000000")),
                year_min=data.get("year_min"),
                year_max=data.get("year_max"),
                usage_min=data.get("usage_min"),
                usage_max=data.get("usage_max"),
                body_status=data.get("body_status"),
                chassis_status=data.get("chassis_status"),
                motor_status=data.get("motor_status"),
                gearbox=data.get("gearbox"),
                fuel_type=data.get("fuel_type"),
                color=data.get("color"),
                origin=data.get("origin"),
                sort=data.get("sort", "sort_date"),
            )
        with SEARCH_SEMAPHORE:
            raw = search_divar(payload)
        ads = extract_ads(raw, category)
        ads = [a for a in ads if is_model_match(a.get("title") or "", model)]
        hydrate_ads_with_missing_prices(ads)
        prices = [a["price_num"] for a in ads if a.get("price_num")]
        if not prices:
            return model, None, []
        avg = clean_avg(prices)
        if avg <= 0:
            return model, None, []
        picked = []
        for a in ads:
            pn = a.get("price_num")
            if not pn:
                continue
            # فقط زیر میانگین و نه مشکوک‌خیلی‌ارزان
            if (avg * 0.35) <= pn < avg:
                a["model_avg"] = int(avg)
                disc = (avg - pn) / avg
                a["value_score"] = round(min(90.0, 40.0 + disc * 100.0), 1)
                a["score_reason"] = f"قیمت {int(disc*100)}٪ زیر میانگین بازار ({int(avg):,})"
                picked.append(a)
        picked.sort(key=lambda x: x.get("price_num") or 0)
        return model, int(avg), picked
    except Exception as e:
        print(f"avg model error {model}:", e)
        return model, None, []


def process_model_normal(model, category, city_ids, recent_ads, data):
    try:
        has_photo = data.get("has_photo")
        if has_photo is False:
            has_photo = None

        if category == "mobile":
            payload = build_mobile_payload(
                city_ids, [model],
                recent_ads=recent_ads,
                has_photo=has_photo,
                has_installment=data.get("has_installment", False),
                business_type=data.get("business_type", "ALL"),
                min_price=str(data.get("min_price", "1000000")),
                max_price=str(data.get("max_price", "900000000")),
                sort=data.get("sort", "sort_date"),
                status=data.get("status"),
                internal_storage=data.get("internal_storage"),
                ram_memory=data.get("ram_memory"),
                sim_card_slot=data.get("sim_card_slot"),
                base_color=data.get("base_color"),
            )
        else:
            payload = build_car_payload(
                city_ids, [model],
                recent_ads=recent_ads,
                has_photo=has_photo,
                has_installment=data.get("has_installment", False),
                has_video=data.get("has_video", False),
                min_price=str(data.get("min_price", "100000000")),
                max_price=str(data.get("max_price", "5000000000")),
                year_min=data.get("year_min"),
                year_max=data.get("year_max"),
                usage_min=data.get("usage_min"),
                usage_max=data.get("usage_max"),
                body_status=data.get("body_status"),
                chassis_status=data.get("chassis_status"),
                motor_status=data.get("motor_status"),
                gearbox=data.get("gearbox"),
                fuel_type=data.get("fuel_type"),
                color=data.get("color"),
                origin=data.get("origin"),
                sort=data.get("sort", "sort_date"),
            )
        with SEARCH_SEMAPHORE:
            raw = search_divar(payload)
        ads = extract_ads(raw, category)
        # فیلتر دقیق مدل تا مدل‌های هم‌خانواده قاطی نشوند
        ads = [a for a in ads if is_model_match(a.get("title") or "", model)]
        hydrate_ads_with_missing_prices(ads)
        prices = [a.get("price_num") for a in ads if a.get("price_num")]
        avg = clean_avg(prices) if prices else 0.0
        # حتی جست‌وجوی عادی هم امتیاز هر آگهی را برمی‌گرداند؛ جزئیات کامل‌تر
        # بعداً در finalize_ads همین امتیاز را با توضیحات/رنگ/امکانات بازنگری می‌کند.
        for ad in ads:
            result = score_ad_smart(ad, avg, category, "balanced", strict_price=False)
            ad.update(result)
            if avg > 0:
                ad["model_avg"] = int(avg)
        return model, int(avg) if avg > 0 else None, ads
    except Exception as e:
        print(f"normal model error {model}:", e)
        return model, None, []


# ====================== منطق جستجو ======================
def _re_filters_from_data(data: dict) -> dict:
    return dict(
        min_price=data.get("min_price"),
        max_price=data.get("max_price"),
        min_rent=data.get("min_rent"),
        max_rent=data.get("max_rent"),
        min_credit=data.get("min_credit"),
        max_credit=data.get("max_credit"),
        size_min=data.get("size_min"),
        size_max=data.get("size_max"),
        rooms=data.get("rooms") or [],
        building_age_min=data.get("building_age_min"),
        building_age_max=data.get("building_age_max"),
        floor_min=data.get("floor_min"),
        floor_max=data.get("floor_max"),
        parking=bool(data.get("parking")),
        elevator=bool(data.get("elevator")),
        warehouse=bool(data.get("warehouse")),
        balcony=bool(data.get("balcony")),
        deed_type=data.get("deed_type") or [],
        business_type=data.get("business_type"),
        has_photo=True if data.get("has_photo") is True else None,
        sort=data.get("sort", "sort_date"),
    )


def process_real_estate(
    category,
    city_ids,
    recent_ads,
    data,
    smart=False,
    min_smart_score=55,
    smart_profile="balanced",
    require_details=False,
):
    """جستجوی یک‌مرحله‌ای املاک (بدون مدل)."""
    try:
        filters = _re_filters_from_data(data)
        payload = build_real_estate_payload(
            city_ids=city_ids,
            category=category,
            recent_ads=recent_ads,
            **filters,
        )
        with SEARCH_SEMAPHORE:
            raw = search_divar(payload, max_pages=15, max_ads=250)
        ads = extract_ads(raw, category)
        if require_details:
            hydrate_ads_with_details(ads)
        else:
            hydrate_ads_with_missing_prices(ads)

        prices = [a["price_num"] for a in ads if a.get("price_num")]
        avg_for_all = clean_avg(prices) if prices else 0.0
        if not smart:
            for ad in ads:
                result = score_ad_smart(
                    ad,
                    avg_for_all,
                    category,
                    smart_profile,
                    desc=ad.get("description") or "",
                    strict_price=False,
                )
                ad.update(result)
                if avg_for_all > 0:
                    ad["market_avg"] = int(avg_for_all)
            return {"count": len(ads), "avg": int(avg_for_all) if avg_for_all else None}, ads

        prices = [a["price_num"] for a in ads if a.get("price_num")]
        avg = clean_avg(prices) if prices else 0
        if avg <= 0:
            # بدون میانگین: همه آگهی‌ها را با امتیاز پایه برگردان (نه لیست خالی)
            for a in ads:
                a["value_score"] = 50.0
                a["score_reason"] = "امتیاز پایه (نمونه قیمت ناکافی)"
            return {"count": len(ads), "avg": None}, ads[:60]

        phase1 = []
        for ad in ads:
            result = score_ad_smart(ad, avg, category, smart_profile, desc=ad.get("description") or "")
            if not result:
                continue
            # ادغام فیلدهای امتیاز روی خود آگهی
            merged = dict(ad)
            merged.update(result)
            merged["market_avg"] = int(avg)
            if result.get("pass_filter") is False and (result.get("value_score") or 0) <= 0:
                continue
            phase1.append(merged)

        phase1.sort(key=lambda x: -(x.get("value_score") or 0))
        # آستانه نرم‌تر اگر تعداد کم است
        effective_min = min_smart_score
        if len(phase1) < 12:
            effective_min = max(35.0, min_smart_score - 15)

        deep_n = min(35, len(phase1))
        out = []
        for ad in phase1[:deep_n]:
            try:
                if ad.get("token"):
                    _merge_post_details(ad, get_post_details_cached(ad["token"]))
            except Exception:
                pass
            desc = ad.get("description") or ""
            rescored = score_ad_smart(ad, avg, category, smart_profile, desc=desc)
            if not rescored:
                continue
            merged = dict(ad)
            merged.update(rescored)
            merged["market_avg"] = int(avg)
            if (merged.get("value_score") or 0) >= effective_min:
                out.append(merged)
        for ad in phase1[deep_n:]:
            if (ad.get("value_score") or 0) >= effective_min:
                out.append(ad)
        # اگر هنوز خیلی کم بود، top بدون آستانه سخت
        if len(out) < 5 and phase1:
            out = phase1[: min(25, len(phase1))]
        out.sort(key=lambda x: -(x.get("value_score") or 0))
        return {"avg": int(avg), "count": len(ads)}, out[:80]
    except Exception as e:
        print(f"real-estate error {category}:", e)
        return None, []


def run_search_logic(data: dict) -> dict:
    category = data.get("category", "mobile")
    # [] در API دیوار به معنی نبود محدودیت شهری است؛ گزینه «همه شهرها» همین
    # مقدار را می‌فرستد و تنظیمات قدیمیِ بدون city همچنان تبریز را حفظ می‌کنند.
    city_ids = normalize_city_ids(data.get("city", "5"))
    # اگر کاربر "همه" یا مقدار خالی فرستاده، فیلتر زمانی اعمال نشود
    raw_recent = data.get("recent_ads")
    if raw_recent in (None, "", "all", "0", "none", "ALL"):
        recent_ads = None
    else:
        recent_ads = raw_recent
    selected_models = data.get("models") or []
    smart_search = data.get("smart_search", False)
    smart_avg = data.get("smart_avg", False)
    smart_days = int(data.get("smart_days", 3) or 3)
    smart_profile = (data.get("smart_profile") or "balanced").strip().lower()
    if smart_profile not in SMART_PROFILES:
        smart_profile = "balanced"
    keywords = [k.strip() for k in (data.get("keywords") or "").replace("،", ",").split(",") if k.strip()]
    negative_keywords = [k.strip() for k in (data.get("negative_keywords") or "").replace("،", ",").split(",") if k.strip()]
    try:
        min_smart_score = float(data.get("min_smart_score") or SMART_PROFILES[smart_profile]["min_score"])
    except Exception:
        min_smart_score = SMART_PROFILES[smart_profile]["min_score"]

    if smart_search or smart_avg:
        # برای نمونه آماری بهتر در هوشمند:
        # 1–2 روز → همان | 3–7 روز → 7d | بیشتر → بدون محدودیت
        days = max(int(smart_days or 3), 1)
        if days <= 1:
            recent_ads = "1d"
        elif days == 2:
            recent_ads = "2d"
        elif days <= 7:
            recent_ads = "7d"
        else:
            recent_ads = None

    # ---------- املاک ----------
    if category in ("apartment-sell", "apartment-rent"):
        info, ads = process_real_estate(
            category, city_ids, recent_ads, data,
            smart=bool(smart_search),
            min_smart_score=min_smart_score,
            smart_profile=smart_profile,
            require_details=bool(keywords or negative_keywords),
        )
        ads = finalize_ads(ads, keywords, negative_keywords, category, smart_profile)
        if smart_search:
            try:
                gmin = float(min_smart_score) if min_smart_score not in (None, "") else SMART_PROFILES.get(smart_profile, {}).get("min_score", 42)
            except Exception:
                gmin = SMART_PROFILES.get(smart_profile, {}).get("min_score", 42)
            floor = SMART_PROFILES.get(smart_profile, SMART_PROFILES["balanced"])["min_score"]
            final_min = max(float(floor), float(gmin))
            before = len(ads)
            ads = [a for a in ads if (a.get("value_score") or 0) >= final_min]
            ads.sort(key=lambda x: (-(x.get("value_score") or 0), not x.get("has_keyword", False)))
            return {
                "success": True, "count": len(ads), "ads": ads,
                "smart_search_enabled": True, "smart_avg_enabled": False,
                "model_info": {"املاک": info} if info else {},
                "min_smart_score": final_min,
                "smart_profile": smart_profile,
                "smart_filter": {"profile": smart_profile, "min_score": final_min, "before": before, "after": len(ads)},
            }
        return {
            "success": True, "count": len(ads), "ads": ads,
            "smart_search_enabled": False, "smart_avg_enabled": False,
        }

    models_dict = MOBILE_MODELS if category == "mobile" else CAR_MODELS
    models_to_search = selected_models if selected_models else [m for lst in models_dict.values() for m in lst]

    seen = set()
    all_ads = []

    if smart_search:
        model_info = {}
        with ThreadPoolExecutor(max_workers=MAX_WORKERS) as ex:
            futures = [
                ex.submit(
                    process_model_smart, m, category, city_ids, recent_ads,
                    data.get("business_type", "ALL"), min_smart_score, smart_profile,
                    bool(keywords or negative_keywords),
                )
                for m in models_to_search
            ]
            for fut in as_completed(futures):
                model, info, best = fut.result()
                if info:
                    model_info[model] = info
                for ad in best:
                    if ad["token"] not in seen:
                        seen.add(ad["token"])
                        all_ads.append(ad)

        all_ads = finalize_ads(all_ads, keywords, negative_keywords, category, smart_profile)
        # فیلتر سراسری نهایی — تضمین می‌کند پروفایل/حداقل امتیاز واقعاً اعمال شود
        try:
            global_min = float(min_smart_score) if min_smart_score not in (None, "") else SMART_PROFILES.get(smart_profile, {}).get("min_score", 42)
        except Exception:
            global_min = SMART_PROFILES.get(smart_profile, {}).get("min_score", 42)
        profile_floor = SMART_PROFILES.get(smart_profile, SMART_PROFILES["balanced"])["min_score"]
        final_min = max(float(profile_floor), float(global_min))
        before_cnt = len(all_ads)
        all_ads = [a for a in all_ads if (a.get("value_score") or 0) >= final_min]
        # مرتب‌سازی: امتیاز هوشمند اول، بعد کلمات کلیدی
        all_ads.sort(key=lambda x: (-(x.get("value_score") or 0), not x.get("has_keyword", False)))
        return {
            "success": True, "count": len(all_ads), "ads": all_ads,
            "smart_search_enabled": True, "smart_avg_enabled": False,
            "model_info": model_info, "min_smart_score": final_min,
            "smart_filter": {"profile": smart_profile, "min_score": final_min, "before": before_cnt, "after": len(all_ads)},
            "smart_profile": smart_profile,
        }

    elif smart_avg:
        model_averages = {}
        with ThreadPoolExecutor(max_workers=MAX_WORKERS) as ex:
            futures = [ex.submit(process_model_avg, m, category, city_ids, recent_ads, data) for m in models_to_search]
            for fut in as_completed(futures):
                model, avg, picked = fut.result()
                if avg is not None:
                    model_averages[model] = avg
                for ad in picked:
                    if ad["token"] not in seen:
                        seen.add(ad["token"])
                        all_ads.append(ad)

        all_ads = finalize_ads(all_ads, keywords, negative_keywords, category, smart_profile)
        return {
            "success": True, "count": len(all_ads), "ads": all_ads,
            "smart_search_enabled": False, "smart_avg_enabled": True,
            "model_averages": model_averages,
        }

    else:
        with ThreadPoolExecutor(max_workers=MAX_WORKERS) as ex:
            futures = [ex.submit(process_model_normal, m, category, city_ids, recent_ads, data) for m in models_to_search]
            for fut in as_completed(futures):
                model, _, ads = fut.result()
                for ad in ads:
                    if ad["token"] not in seen:
                        seen.add(ad["token"])
                        all_ads.append(ad)

        all_ads = finalize_ads(all_ads, keywords, negative_keywords, category, smart_profile)
        return {
            "success": True, "count": len(all_ads), "ads": all_ads,
            "smart_search_enabled": False, "smart_avg_enabled": False,
        }


# ====================== نوتیف ======================
def send_email(subject: str, body: str, to_email: Optional[str] = None):
    target = to_email or NOTIFY_EMAIL
    if not target or not SMTP_USER:
        print("Email not configured")
        return
    try:
        msg = MIMEMultipart()
        msg["From"] = SMTP_USER
        msg["To"] = target
        msg["Subject"] = subject
        msg.attach(MIMEText(body, "plain", "utf-8"))
        if SMTP_USE_SSL:
            server = smtplib.SMTP_SSL(SMTP_HOST, int(SMTP_PORT), timeout=20)
        else:
            server = smtplib.SMTP(SMTP_HOST, int(SMTP_PORT), timeout=20)
            server.starttls()
        server.login(SMTP_USER, SMTP_PASS)
        server.sendmail(SMTP_USER, target, msg.as_string())
        server.quit()
        print("Email sent")
    except Exception as e:
        print("Email error:", e)


def send_telegram(text: str, chat_id: Optional[str] = None):
    target = chat_id or TELEGRAM_CHAT_ID
    if not TELEGRAM_BOT_TOKEN or not target:
        return
    try:
        requests.post(
            f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage",
            json={"chat_id": target, "text": text, "parse_mode": "HTML"},
            timeout=10,
        )
    except Exception as e:
        print("Telegram error:", e)




# ====================== PWA / Web Push ======================
def _b64url(data: bytes) -> str:
    import base64
    return base64.urlsafe_b64encode(data).decode("utf-8").rstrip("=")


def ensure_vapid_keys(conn=None):
    close = False
    if conn is None:
        conn = get_db()
        close = True
    pub = get_meta(conn, "vapid_public")
    priv = get_meta(conn, "vapid_private")
    if pub and priv:
        if close:
            conn.close()
        return pub, priv
    try:
        from cryptography.hazmat.primitives.asymmetric import ec
        from cryptography.hazmat.backends import default_backend
        key = ec.generate_private_key(ec.SECP256R1(), default_backend())
        priv_bytes = key.private_numbers().private_value.to_bytes(32, "big")
        pub_point = key.public_key().public_numbers()
        pub_bytes = b"\x04" + pub_point.x.to_bytes(32, "big") + pub_point.y.to_bytes(32, "big")
        priv_s = _b64url(priv_bytes)
        pub_s = _b64url(pub_bytes)
        set_meta(conn, "vapid_public", pub_s)
        set_meta(conn, "vapid_private", priv_s)
        set_meta(conn, "vapid_subject", "mailto:admin@divar-monitor.local")
        conn.commit()
        print("[PWA] VAPID keys generated")
        if close:
            conn.close()
        return pub_s, priv_s
    except Exception as e:
        print("[PWA] VAPID generate error:", e)
        if close:
            conn.close()
        return None, None


def send_web_push_to_user(user_id: int, title: str, body: str, url: str = "/"):
    conn = get_db()
    ensure_vapid_keys(conn)
    pub = get_meta(conn, "vapid_public")
    priv = get_meta(conn, "vapid_private")
    subject = get_meta(conn, "vapid_subject") or "mailto:admin@divar-monitor.local"
    rows = conn.execute(
        "SELECT * FROM push_subscriptions WHERE user_id=?", (user_id,)
    ).fetchall()
    conn.close()
    if not rows or not priv:
        return 0
    try:
        from pywebpush import webpush
    except Exception:
        print("[PWA] pywebpush not installed — on server: pip install pywebpush")
        return 0
    payload = json.dumps({"title": title, "body": body, "url": url}, ensure_ascii=False)
    sent = 0
    for row in rows:
        sub = {
            "endpoint": row["endpoint"],
            "keys": {"p256dh": row["p256dh"], "auth": row["auth"]},
        }
        try:
            webpush(
                subscription_info=sub,
                data=payload,
                vapid_private_key=priv,
                vapid_claims={"sub": subject},
            )
            sent += 1
        except Exception as e:
            print("[PWA] push fail:", e)
            if "410" in str(e) or "404" in str(e):
                try:
                    c2 = get_db()
                    c2.execute("DELETE FROM push_subscriptions WHERE endpoint=?", (row["endpoint"],))
                    c2.commit()
                    c2.close()
                except Exception:
                    pass
    return sent

def save_notification(
    user_id: Optional[int],
    title: str,
    body: str = "",
    link: Optional[str] = None,
    monitor_id: Optional[int] = None,
    conn=None,
) -> Optional[int]:
    """ذخیره یک اعلان در دیتابیس برای نمایش در صفحه اعلان‌ها"""
    if not user_id:
        return None
    close = False
    if conn is None:
        conn = get_db()
        close = True
    try:
        cur = conn.execute(
            """
            INSERT INTO notifications (user_id, title, body, link, monitor_id, is_read, created_at)
            VALUES (?, ?, ?, ?, ?, 0, ?)
            """,
            (
                int(user_id),
                (title or "")[:300],
                (body or "")[:2000],
                (link or None),
                monitor_id,
                datetime.now().isoformat(),
            ),
        )
        if close:
            conn.commit()
        return cur.lastrowid
    except Exception as e:
        print("save_notification error:", e)
        return None
    finally:
        if close:
            try:
                conn.close()
            except Exception:
                pass


def notify(
    text: str,
    subject: str = "آگهی جدید دیوار",
    telegram_chat_id: Optional[str] = None,
    email: Optional[str] = None,
    user_id: Optional[int] = None,
    link: Optional[str] = None,
    monitor_id: Optional[int] = None,
    save: bool = True,
):
    """ارسال ایمیل / تلگرام / وب‌پوش و در صورت نیاز ذخیره در جدول اعلان‌ها"""
    push_url = link or "/notifications"
    if user_id:
        try:
            send_web_push_to_user(
                user_id,
                subject or "دیده‌بان دیوار",
                (text or "")[:180].replace("\n", " "),
                push_url,
            )
        except Exception as _pe:
            print("push notify error:", _pe)
        if save:
            try:
                body_preview = (text or "").replace("<b>", "").replace("</b>", "").replace("<br>", "\n")
                save_notification(user_id, subject or "آگهی جدید", body_preview[:500], link=link, monitor_id=monitor_id)
            except Exception as _se:
                print("notify save error:", _se)
    clean = text.replace("<b>", "").replace("</b>", "").replace("<br>", "\n")
    send_email(subject, clean, to_email=email)
    send_telegram(text, chat_id=telegram_chat_id)


# ====================== Auth Routes ======================
@app.route("/register", methods=["GET", "POST"])
def register():
    error = None
    if request.method == "POST":
        username = (request.form.get("username") or "").strip()
        password = request.form.get("password") or ""
        password2 = request.form.get("password2") or ""
        if len(username) < 3:
            error = "نام کاربری باید حداقل ۳ کاراکتر باشد"
        elif len(password) < 6:
            error = "رمز عبور باید حداقل ۶ کاراکتر باشد"
        elif password != password2:
            error = "رمز عبور و تکرار آن یکسان نیستند"
        else:
            conn = get_db()
            exists = conn.execute("SELECT 1 FROM users WHERE username=?", (username,)).fetchone()
            if exists:
                error = "این نام کاربری قبلا ثبت شده است"
                conn.close()
            else:
                # اشتراک رایگان ۳ ساعته برای جلوگیری از سوءاستفاده
                trial_end = (datetime.now() + timedelta(hours=TRIAL_HOURS)).isoformat()
                conn.execute(
                    "INSERT INTO users (username, password_hash, role, is_active, plan, subscription_expires_at, max_monitors, created_at) VALUES (?,?,?,?,?,?,?,?)",
                    (username, generate_password_hash(password), "user", 1, "trial", trial_end, 1, datetime.now().isoformat()),
                )
                conn.commit()
                uid = conn.execute("SELECT id FROM users WHERE username=?", (username,)).fetchone()["id"]
                conn.close()
                session.clear()
                session.permanent = True
                session["user_id"] = uid
                return redirect(url_for("index"))
    if get_current_user():
        return redirect(url_for("index"))
    return render_template("register.html", error=error)


@app.route("/login", methods=["GET", "POST"])
def login():
    error = None
    try:
        if request.method == "POST":
            username = (request.form.get("username") or "").strip()
            password = request.form.get("password") or ""
            if not username or not password:
                error = "نام کاربری و رمز عبور را وارد کنید"
            else:
                conn = None
                try:
                    conn = get_db()
                    user = conn.execute(
                        "SELECT * FROM users WHERE username=?", (username,)
                    ).fetchone()
                    if user and check_password_hash(user["password_hash"], password):
                        if not user["is_active"]:
                            error = "حساب شما غیرفعال شده است"
                        else:
                            try:
                                conn.execute(
                                    "UPDATE users SET last_login=? WHERE id=?",
                                    (datetime.now().isoformat(), user["id"]),
                                )
                                conn.commit()
                            except Exception as e:
                                print("[LOGIN] last_login update:", e)
                            # session را قبل از بستن conn تنظیم می‌کنیم
                            try:
                                session.clear()
                            except Exception:
                                pass
                            session.permanent = True
                            session["user_id"] = int(user["id"])
                            role = user["role"]
                            if conn:
                                try:
                                    conn.close()
                                except Exception:
                                    pass
                                conn = None
                            if role == "admin":
                                return redirect(url_for("admin_page"))
                            return redirect(url_for("index"))
                    else:
                        error = "نام کاربری یا رمز عبور اشتباه است"
                except Exception as e:
                    print("[LOGIN] error:", e)
                    error = "خطای موقت در ورود. چند ثانیه بعد دوباره تلاش کنید."
                finally:
                    if conn is not None:
                        try:
                            conn.close()
                        except Exception:
                            pass

        # اگر قبلاً وارد شده
        u = get_current_user()
        if u:
            if u["role"] == "admin":
                return redirect(url_for("admin_page"))
            return redirect(url_for("index"))
        return render_template("login.html", error=error)
    except Exception as e:
        print("[LOGIN] fatal:", e)
        try:
            return render_template("login.html", error="خطای سرور. لطفاً بعداً تلاش کنید."), 500
        except Exception:
            return "Login error", 500


@app.route("/logout")
def logout():
    session.clear()
    return redirect(url_for("login"))

@app.route("/admin/login", methods=["GET", "POST"])
def admin_login():
    """ادمین از همان صفحه ورود عادی استفاده می‌کند — این مسیر فقط ریدایرکت می‌کند."""
    return redirect(url_for("login"))


@app.route("/account", methods=["GET", "POST"])
@login_required
def account():
    user = get_current_user()
    msg = None
    if request.method == "POST":
        action = request.form.get("action", "password")
        conn = get_db()
        if action == "password":
            cur = request.form.get("current_password") or ""
            new = request.form.get("new_password") or ""
            if check_password_hash(user["password_hash"], cur) and len(new) >= 6:
                conn.execute("UPDATE users SET password_hash=? WHERE id=?", (generate_password_hash(new), user["id"]))
                conn.commit()
                msg = "رمز عبور با موفقیت تغییر کرد"
            else:
                msg = "رمز فعلی اشتباه است یا رمز جدید خیلی کوتاه است"
        elif action == "notify":
            tg = (request.form.get("telegram_chat_id") or "").strip()
            em = (request.form.get("notify_email") or "").strip()
            conn.execute("UPDATE users SET telegram_chat_id=?, notify_email=? WHERE id=?", (tg or None, em or None, user["id"]))
            conn.commit()
            msg = "تنظیمات اعلان ذخیره شد"
        conn.close()
        user = get_current_user()
    return render_template("account.html", user=user, msg=msg, sub_active=is_sub_active(user))


# ====================== Admin Routes ======================
@app.route("/admin")
@admin_required
def admin_page():
    return render_template("admin.html")


def _admin_user_error(error: str, status: int = 400):
    """یک پاسخ یکنواخت برای خطاهای ویرایش کاربر در پنل مدیریت."""
    return jsonify({"success": False, "error": error}), status


def _admin_bool(value: Any, default: bool = False) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    if isinstance(value, str):
        lowered = value.strip().lower()
        if lowered in {"1", "true", "yes", "on"}:
            return True
        if lowered in {"0", "false", "no", "off", ""}:
            return False
    raise ValueError("invalid_boolean")


def _admin_non_negative_int(value: Any, field: str, maximum: int = 9999) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError, OverflowError):
        raise ValueError(field)
    if parsed < 0 or parsed > maximum:
        raise ValueError(field)
    return parsed


def _admin_optional_text(value: Any, field: str, maximum: int = 255) -> Optional[str]:
    if value is None:
        return None
    if not isinstance(value, str):
        raise ValueError(field)
    cleaned = value.strip()
    if len(cleaned) > maximum or "\x00" in cleaned:
        raise ValueError(field)
    return cleaned or None


def _admin_subscription_expiry(value: Any) -> Optional[str]:
    """ورودی datetime-local پنل را به تاریخ قابل استفاده‌ی SQLite تبدیل می‌کند."""
    if value is None or value == "":
        return None
    if not isinstance(value, str):
        raise ValueError("subscription_expires_at")
    try:
        parsed = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
    except (TypeError, ValueError):
        raise ValueError("subscription_expires_at")
    # is_sub_active با datetime بدون timezone مقایسه می‌کند؛ مقدار aware باعث
    # TypeError و اشتراک ظاهراً غیرفعال می‌شد، پس آن را یکدست نگه می‌داریم.
    if parsed.tzinfo is not None:
        parsed = parsed.replace(tzinfo=None)
    return parsed.isoformat()


def _admin_username(value: Any) -> str:
    if not isinstance(value, str):
        raise ValueError("username")
    username = value.strip()
    if len(username) < 3 or len(username) > 80 or "\x00" in username:
        raise ValueError("username")
    return username


@app.route("/api/admin/users", methods=["GET"])
@api_admin_required
def admin_list_users():
    conn = get_db()
    rows = conn.execute(
        "SELECT id, username, role, is_active, plan, subscription_expires_at, max_monitors, "
        "telegram_chat_id, notify_email, created_at, last_login FROM users ORDER BY id DESC"
    ).fetchall()
    conn.close()
    return jsonify([dict(r) for r in rows])


@app.route("/api/admin/users", methods=["POST"])
@api_admin_required
def admin_create_user():
    data = request.get_json(silent=True) or {}
    if not isinstance(data, dict):
        return _admin_user_error("invalid")
    try:
        username = _admin_username(data.get("username"))
        password = data.get("password") or ""
        if not isinstance(password, str) or len(password) < 6:
            raise ValueError("password")
        role = str(data.get("role", "user") or "user").strip().lower()
        if role not in {"user", "admin"}:
            raise ValueError("role")
        plan = _admin_optional_text(data.get("plan", "free"), "plan", 80)
        if not plan:
            raise ValueError("plan")
        days = _admin_non_negative_int(data.get("days", 30), "days", maximum=36500)
        max_monitors = _admin_non_negative_int(data.get("max_monitors", 3), "max_monitors")
    except ValueError as exc:
        return _admin_user_error(str(exc))

    expires = None if role == "admin" else (datetime.now() + timedelta(days=days)).isoformat()
    conn = get_db()
    try:
        if conn.execute("SELECT 1 FROM users WHERE username=?", (username,)).fetchone():
            return _admin_user_error("exists", 409)
        conn.execute(
            "INSERT INTO users (username, password_hash, role, is_active, plan, subscription_expires_at, max_monitors, created_at) VALUES (?,?,?,?,?,?,?,?)",
            (username, generate_password_hash(password), role, 1, plan, expires, max_monitors, datetime.now().isoformat()),
        )
        conn.commit()
    finally:
        conn.close()
    return jsonify({"success": True})


@app.route("/api/admin/users/<int:uid>", methods=["PUT"])
@api_admin_required
def admin_update_user(uid):
    data = request.get_json(silent=True) or {}
    if not isinstance(data, dict):
        return _admin_user_error("invalid")

    conn = get_db()
    try:
        user = conn.execute("SELECT * FROM users WHERE id=?", (uid,)).fetchone()
        if not user:
            return _admin_user_error("not_found", 404)

        try:
            username = _admin_username(data["username"]) if "username" in data else user["username"]
            role = str(data.get("role", user["role"]) or "").strip().lower()
            if role not in {"user", "admin"}:
                raise ValueError("role")
            plan = (
                _admin_optional_text(data["plan"], "plan", 80)
                if "plan" in data else (user["plan"] or "free")
            )
            if not plan:
                raise ValueError("plan")
            active_value = (
                data["is_active"] if "is_active" in data
                else (user["is_active"] if user["is_active"] is not None else 1)
            )
            is_active = 1 if _admin_bool(active_value) else 0
            max_value = (
                data["max_monitors"] if "max_monitors" in data
                else (user["max_monitors"] if user["max_monitors"] is not None else 1)
            )
            max_monitors = _admin_non_negative_int(max_value, "max_monitors")
            expiry_value = (
                data["subscription_expires_at"] if "subscription_expires_at" in data
                else user["subscription_expires_at"]
            )
            expires = None if role == "admin" else _admin_subscription_expiry(expiry_value)
            telegram_chat_id = (
                _admin_optional_text(data["telegram_chat_id"], "telegram_chat_id")
                if "telegram_chat_id" in data else user["telegram_chat_id"]
            )
            notify_email = (
                _admin_optional_text(data["notify_email"], "notify_email")
                if "notify_email" in data else user["notify_email"]
            )
            new_password = data.get("password")
            if new_password not in (None, ""):
                if not isinstance(new_password, str) or len(new_password) < 6:
                    raise ValueError("password")
        except ValueError as exc:
            return _admin_user_error(str(exc))

        if username != user["username"]:
            duplicate = conn.execute("SELECT 1 FROM users WHERE username=? AND id<>?", (username, uid)).fetchone()
            if duplicate:
                return _admin_user_error("exists", 409)

        # اجازه نده آخرین مدیر فعال با یک ویرایش تصادفی غیرفعال یا تنزل نقش شود.
        if user["role"] == "admin" and (role != "admin" or not is_active):
            remaining_admin = conn.execute(
                "SELECT 1 FROM users WHERE role='admin' AND is_active=1 AND id<>? LIMIT 1", (uid,)
            ).fetchone()
            if not remaining_admin:
                return _admin_user_error("last_active_admin")

        if new_password not in (None, ""):
            conn.execute("UPDATE users SET password_hash=? WHERE id=?", (generate_password_hash(new_password), uid))
        conn.execute(
            "UPDATE users SET username=?, plan=?, is_active=?, max_monitors=?, role=?, "
            "subscription_expires_at=?, telegram_chat_id=?, notify_email=? WHERE id=?",
            (username, plan, is_active, max_monitors, role, expires, telegram_chat_id, notify_email, uid),
        )
        conn.commit()
    finally:
        conn.close()
    return jsonify({"success": True})


@app.route("/api/admin/users/<int:uid>/extend", methods=["POST"])
@api_admin_required
def admin_extend_user(uid):
    data = request.json or {}
    days = int(data.get("days", 30) or 30)
    conn = get_db()
    user = conn.execute("SELECT * FROM users WHERE id=?", (uid,)).fetchone()
    if not user:
        conn.close()
        return jsonify({"success": False}), 404
    base = datetime.now()
    if user["subscription_expires_at"]:
        try:
            cur_exp = datetime.fromisoformat(user["subscription_expires_at"])
            if cur_exp > base:
                base = cur_exp
        except Exception:
            pass
    new_exp = (base + timedelta(days=days)).isoformat()
    conn.execute("UPDATE users SET subscription_expires_at=?, is_active=1 WHERE id=?", (new_exp, uid))
    conn.commit()
    conn.close()
    return jsonify({"success": True, "subscription_expires_at": new_exp})


@app.route("/api/admin/users/<int:uid>", methods=["DELETE"])
@api_admin_required
def admin_delete_user(uid):
    conn = get_db()
    conn.execute("DELETE FROM monitors WHERE user_id=?", (uid,))
    conn.execute("DELETE FROM users WHERE id=? AND role != 'admin'", (uid,))
    conn.commit()
    conn.close()
    return jsonify({"success": True})


# ====================== Main Routes ======================
@app.route("/")
@login_required
def index():
    """داشبورد — مدل‌ها و وضعیت کاربر؛ خطا نباید worker را بکشد."""
    try:
        user = get_current_user()
        if not user:
            return redirect(url_for("login"))
        # sqlite3.Row → dict برای سازگاری مطمئن با Jinja
        user_data = dict(user)
        return render_template(
            "index.html",
            mobile_models=MOBILE_MODELS,
            car_models=CAR_MODELS,
            user=user_data,
            sub_active=is_sub_active(user),
        )
    except Exception as e:
        print("[INDEX] error:", type(e).__name__, e)
        import traceback
        traceback.print_exc()
        # به جای down شدن، پیام قابل‌فهم
        try:
            return (
                "<!doctype html><meta charset=utf-8><body style='font-family:Tahoma;padding:40px;direction:rtl'>"
                "<h2>خطا در بارگذاری داشبورد</h2>"
                "<p>لطفاً صفحه را رفرش کنید. اگر ادامه داشت از منوی بالا به بخش‌های دیگر بروید.</p>"
                "<p><a href='/notifications'>اعلان‌ها</a> · <a href='/account'>حساب</a> · <a href='/logout'>خروج</a></p>"
                "</body>",
                500,
            )
        except Exception:
            return "Dashboard error", 500


@app.route("/search", methods=["POST"])
@api_login_required
@subscription_required
def search():
    user = get_current_user()
    now_ts = time.time()
    with LAST_SEARCH_LOCK:
        last = LAST_SEARCH.get(user["id"], 0)
        if now_ts - last < SEARCH_COOLDOWN_SECONDS:
            wait = round(SEARCH_COOLDOWN_SECONDS - (now_ts - last), 1)
            return jsonify({"success": False, "error": "cooldown", "message": f"لطفا {wait} ثانیه صبر کن و دوباره امتحان کن"}), 429
        LAST_SEARCH[user["id"]] = now_ts
    data = request.json or {}
    return jsonify(run_search_logic(data))


@app.route("/api/monitors", methods=["GET"])
@api_login_required
def list_monitors():
    user = get_current_user()
    conn = get_db()
    if user["role"] == "admin":
        rows = conn.execute("SELECT * FROM monitors ORDER BY id DESC").fetchall()
    else:
        rows = conn.execute("SELECT * FROM monitors WHERE user_id=? ORDER BY id DESC", (user["id"],)).fetchall()
    conn.close()
    return jsonify([dict(r) for r in rows])


@app.route("/api/monitors", methods=["POST"])
@api_login_required
@subscription_required
def create_monitor():
    user = get_current_user()
    conn = get_db()
    count = conn.execute("SELECT COUNT(*) c FROM monitors WHERE user_id=?", (user["id"],)).fetchone()["c"]
    if user["role"] != "admin" and count >= (user["max_monitors"] or 1):
        conn.close()
        return jsonify({
            "success": False, "error": "limit_reached",
            "message": f"شما فقط مجاز به ساخت {user['max_monitors']} مانیتور هستید."
        }), 403

    data = request.json or {}
    name = data.get("name") or "مانیتور بدون نام"
    category = data.get("category", "mobile")
    settings = data.get("settings") or {}
    track_price = 1 if data.get("track_price") else 0
    conn.execute(
        "INSERT INTO monitors (user_id, name, category, settings_json, is_active, track_price, created_at) VALUES (?,?,?,?,?,?,?)",
        (user["id"], name, category, json.dumps(settings, ensure_ascii=False), 1, track_price, datetime.now().isoformat()),
    )
    conn.commit()
    mid = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
    conn.close()
    return jsonify({"success": True, "id": mid})


def _own_monitor_or_admin(conn, user, mid):
    row = conn.execute("SELECT * FROM monitors WHERE id=?", (mid,)).fetchone()
    if not row:
        return None
    if user["role"] != "admin" and row["user_id"] != user["id"]:
        return None
    return row


@app.route("/api/monitors/<int:mid>/toggle", methods=["POST"])
@api_login_required
def toggle_monitor(mid):
    user = get_current_user()
    conn = get_db()
    row = _own_monitor_or_admin(conn, user, mid)
    if not row:
        conn.close()
        return jsonify({"success": False}), 404
    new_val = 0 if row["is_active"] else 1
    conn.execute("UPDATE monitors SET is_active=? WHERE id=?", (new_val, mid))
    conn.commit()
    conn.close()
    return jsonify({"success": True, "is_active": new_val})


@app.route("/api/monitors/<int:mid>", methods=["DELETE"])
@api_login_required
def delete_monitor(mid):
    user = get_current_user()
    conn = get_db()
    row = _own_monitor_or_admin(conn, user, mid)
    if not row:
        conn.close()
        return jsonify({"success": False}), 404
    conn.execute("DELETE FROM monitors WHERE id=?", (mid,))
    conn.commit()
    conn.close()
    return jsonify({"success": True})


@app.route("/api/history")
@api_login_required
def history():
    user = get_current_user()
    limit = int(request.args.get("limit", 100))
    conn = get_db()
    if user["role"] == "admin":
        rows = conn.execute("SELECT * FROM history ORDER BY found_at DESC LIMIT ?", (limit,)).fetchall()
    else:
        rows = conn.execute(
            """
            SELECT h.* FROM history h
            JOIN monitors m ON m.id = h.monitor_id
            WHERE m.user_id=?
            ORDER BY h.found_at DESC LIMIT ?
            """,
            (user["id"], limit),
        ).fetchall()
    conn.close()
    return jsonify([dict(r) for r in rows])


# ====================== اعلان‌ها ======================
@app.route("/notifications")
@login_required
def notifications_page():
    user = get_current_user()
    return render_template(
        "notifications.html",
        user=user,
        sub_active=is_sub_active(user),
    )


@app.route("/api/notifications")
@api_login_required
def api_notifications():
    user = get_current_user()
    limit = min(int(request.args.get("limit", 100)), 300)
    only_unread = request.args.get("unread") in ("1", "true", "yes")
    conn = get_db()
    if only_unread:
        rows = conn.execute(
            """
            SELECT * FROM notifications
            WHERE user_id=? AND is_read=0
            ORDER BY created_at DESC, id DESC LIMIT ?
            """,
            (user["id"], limit),
        ).fetchall()
    else:
        rows = conn.execute(
            """
            SELECT * FROM notifications
            WHERE user_id=?
            ORDER BY created_at DESC, id DESC LIMIT ?
            """,
            (user["id"], limit),
        ).fetchall()
    conn.close()
    return jsonify([dict(r) for r in rows])


@app.route("/api/notifications/unread-count")
@api_login_required
def api_notifications_unread_count():
    user = get_current_user()
    conn = get_db()
    row = conn.execute(
        "SELECT COUNT(*) AS c FROM notifications WHERE user_id=? AND is_read=0",
        (user["id"],),
    ).fetchone()
    conn.close()
    return jsonify({"count": int(row["c"] if row else 0)})


@app.route("/api/notifications/<int:nid>/read", methods=["POST"])
@api_login_required
def api_notification_read(nid):
    user = get_current_user()
    conn = get_db()
    conn.execute(
        "UPDATE notifications SET is_read=1 WHERE id=? AND user_id=?",
        (nid, user["id"]),
    )
    conn.commit()
    conn.close()
    return jsonify({"success": True})


@app.route("/api/notifications/read-all", methods=["POST"])
@api_login_required
def api_notifications_read_all():
    user = get_current_user()
    conn = get_db()
    conn.execute(
        "UPDATE notifications SET is_read=1 WHERE user_id=? AND is_read=0",
        (user["id"],),
    )
    conn.commit()
    conn.close()
    return jsonify({"success": True})


@app.route("/api/notifications/clear", methods=["POST"])
@api_login_required
def api_notifications_clear():
    """پاکسازی کامل اعلان‌های کاربر"""
    user = get_current_user()
    conn = get_db()
    conn.execute("DELETE FROM notifications WHERE user_id=?", (user["id"],))
    conn.commit()
    conn.close()
    return jsonify({"success": True})


@app.route("/api/history/clear", methods=["POST"])
@api_login_required
def api_history_clear():
    """پاکسازی تاریخچه آگهی‌های پیدا شده برای کاربر"""
    user = get_current_user()
    conn = get_db()
    if user["role"] == "admin":
        conn.execute("DELETE FROM history")
    else:
        conn.execute(
            """
            DELETE FROM history WHERE monitor_id IN (
                SELECT id FROM monitors WHERE user_id=?
            )
            """,
            (user["id"],),
        )
    conn.commit()
    conn.close()
    return jsonify({"success": True})


@app.route("/api/test-telegram", methods=["POST"])
@api_login_required
def test_telegram():
    user = get_current_user()
    send_telegram("✅ تست نوتیفیکیشن دیوار مانیتور", chat_id=user["telegram_chat_id"])
    return jsonify({"success": True})


@app.route("/api/test-email", methods=["POST"])
@api_login_required
def test_email():
    user = get_current_user()
    notify("این یک پیام تست از مانیتور دیوار است.", subject="تست نوتیف دیوار", email=user["notify_email"])
    return jsonify({"success": True})


# ====================== اشتراک / پرداخت / پشتیبانی / محتوا ======================
UPLOAD_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "uploads", "receipts")
os.makedirs(UPLOAD_DIR, exist_ok=True)


@app.route("/subscribe")
@login_required
def subscribe_page():
    user = get_current_user()
    conn = get_db()
    plans = get_plans(conn)
    pay = get_payment_info(conn)
    reqs = conn.execute(
        "SELECT * FROM payment_requests WHERE user_id=? ORDER BY id DESC LIMIT 20",
        (user["id"],),
    ).fetchall()
    conn.close()
    return render_template(
        "subscribe.html",
        user=user,
        sub_active=is_sub_active(user),
        plans=plans,
        payment=pay,
        requests=[dict(r) for r in reqs],
    )


@app.route("/api/subscribe/request", methods=["POST"])
@api_login_required
def subscribe_request():
    user = get_current_user()
    plan_id = (request.form.get("plan_id") or "").strip()
    phone = (request.form.get("phone") or "").strip()
    if not plan_id or not phone:
        return jsonify({"success": False, "error": "پلن و شماره موبایل الزامی است"}), 400
    phone_digits = re.sub(r"\D", "", normalize_digits(phone))
    if len(phone_digits) < 10:
        return jsonify({"success": False, "error": "شماره موبایل معتبر نیست"}), 400

    plans = {p["id"]: p for p in get_plans()}
    plan = plans.get(plan_id)
    if not plan:
        return jsonify({"success": False, "error": "پلن نامعتبر"}), 400

    file = request.files.get("receipt")
    if not file or not file.filename:
        return jsonify({"success": False, "error": "تصویر رسید الزامی است"}), 400
    ext = os.path.splitext(file.filename)[1].lower()
    if ext not in (".jpg", ".jpeg", ".png", ".webp", ".gif", ".pdf"):
        return jsonify({"success": False, "error": "فرمت فایل مجاز نیست"}), 400

    # فقط یک درخواست در انتظار
    conn = get_db()
    pending = conn.execute(
        "SELECT id FROM payment_requests WHERE user_id=? AND status='pending'",
        (user["id"],),
    ).fetchone()
    if pending:
        conn.close()
        return jsonify({"success": False, "error": "یک درخواست در انتظار بررسی دارید"}), 400

    fname = f"u{user['id']}_{int(time.time())}{ext}"
    fpath = os.path.join(UPLOAD_DIR, fname)
    file.save(fpath)
    rel = f"receipts/{fname}"

    conn.execute(
        """INSERT INTO payment_requests
           (user_id, plan_id, plan_name, amount, days, max_monitors, phone, receipt_path, status, created_at)
           VALUES (?,?,?,?,?,?,?,?,?,?)""",
        (
            user["id"], plan_id, plan.get("name"), int(plan.get("price") or 0),
            int(plan.get("days") or 30), int(plan.get("max_monitors") or 3),
            phone_digits, rel, "pending", datetime.now().isoformat(),
        ),
    )
    conn.commit()
    conn.close()
    return jsonify({"success": True, "message": "درخواست ثبت شد و پس از بررسی فعال می‌شود"})


@app.route("/uploads/<path:filename>")
@login_required
def serve_upload(filename):
    user = get_current_user()
    base = os.path.join(os.path.dirname(os.path.abspath(__file__)), "uploads")
    # ادمین همه، کاربر فقط فایل خودش
    if user["role"] != "admin":
        if not filename.startswith("receipts/") or f"u{user['id']}_" not in os.path.basename(filename):
            return "Forbidden", 403
    return send_from_directory(base, filename)


@app.route("/support")
@login_required
def support_page():
    return render_template("support.html", user=get_current_user(), sub_active=is_sub_active(get_current_user()))


@app.route("/api/support/messages", methods=["GET"])
@api_login_required
def support_list():
    user = get_current_user()
    conn = get_db()
    rows = conn.execute(
        "SELECT * FROM support_messages WHERE user_id=? ORDER BY id ASC LIMIT 200",
        (user["id"],),
    ).fetchall()
    if user["role"] != "admin":
        conn.execute(
            "UPDATE support_messages SET is_read=1 WHERE user_id=? AND sender='admin'",
            (user["id"],),
        )
        conn.commit()
    conn.close()
    return jsonify({"success": True, "messages": [dict(r) for r in rows]})


@app.route("/api/support/messages", methods=["POST"])
@api_login_required
def support_send():
    user = get_current_user()
    data = request.get_json(silent=True) or {}
    msg = (data.get("message") or "").strip()
    if not msg or len(msg) > 2000:
        return jsonify({"success": False, "error": "پیام نامعتبر"}), 400
    conn = get_db()
    conn.execute(
        "INSERT INTO support_messages (user_id, sender, message, is_read, created_at) VALUES (?,?,?,?,?)",
        (user["id"], "user", msg, 0, datetime.now().isoformat()),
    )
    conn.commit()
    conn.close()
    return jsonify({"success": True})


@app.route("/page/<key>")
@login_required
def cms_page(key):
    if key not in ("about", "terms", "guide", "buy_guide"):
        return "Not found", 404
    content = get_site_content(key)
    return render_template(
        "cms_page.html",
        user=get_current_user(),
        sub_active=is_sub_active(get_current_user()),
        content=content,
    )


@app.route("/api/me")
@api_login_required
def api_me():
    user = get_current_user()
    return jsonify({
        "success": True,
        "user": {
            "id": user["id"],
            "username": user["username"],
            "role": user["role"],
            "plan": user["plan"],
            "subscription_expires_at": user["subscription_expires_at"],
            "max_monitors": user["max_monitors"],
            "sub_active": is_sub_active(user),
        },
    })


# ---- Admin: payments, settings, support, content ----
@app.route("/api/admin/payments", methods=["GET"])
@api_admin_required
def admin_payments():
    status = request.args.get("status", "pending")
    conn = get_db()
    if status == "all":
        rows = conn.execute(
            """SELECT p.*, u.username FROM payment_requests p
               JOIN users u ON u.id=p.user_id ORDER BY p.id DESC LIMIT 100"""
        ).fetchall()
    else:
        rows = conn.execute(
            """SELECT p.*, u.username FROM payment_requests p
               JOIN users u ON u.id=p.user_id WHERE p.status=? ORDER BY p.id DESC LIMIT 100""",
            (status,),
        ).fetchall()
    conn.close()
    return jsonify([dict(r) for r in rows])


@app.route("/api/admin/payments/<int:pid>/review", methods=["POST"])
@api_admin_required
def admin_review_payment(pid):
    admin = get_current_user()
    data = request.get_json(silent=True) or {}
    action = data.get("action")  # approve | reject
    note = (data.get("note") or "").strip()
    conn = get_db()
    row = conn.execute("SELECT * FROM payment_requests WHERE id=?", (pid,)).fetchone()
    if not row:
        conn.close()
        return jsonify({"success": False, "error": "not found"}), 404
    if row["status"] != "pending":
        conn.close()
        return jsonify({"success": False, "error": "already reviewed"}), 400

    if action == "approve":
        user = conn.execute("SELECT * FROM users WHERE id=?", (row["user_id"],)).fetchone()
        base = datetime.now()
        if user and user["subscription_expires_at"]:
            try:
                cur = datetime.fromisoformat(user["subscription_expires_at"])
                if cur > base:
                    base = cur
            except Exception:
                pass
        new_exp = (base + timedelta(days=int(row["days"] or 30))).isoformat()
        conn.execute(
            "UPDATE users SET subscription_expires_at=?, is_active=1, plan=?, max_monitors=? WHERE id=?",
            (new_exp, row["plan_name"] or row["plan_id"], int(row["max_monitors"] or 3), row["user_id"]),
        )
        conn.execute(
            "UPDATE payment_requests SET status='approved', admin_note=?, reviewed_at=?, reviewed_by=? WHERE id=?",
            (note, datetime.now().isoformat(), admin["id"], pid),
        )
        conn.execute(
            "INSERT INTO support_messages (user_id, sender, message, is_read, created_at) VALUES (?,?,?,?,?)",
            (row["user_id"], "admin", f"✅ اشتراک شما تأیید و تا {new_exp[:10]} فعال شد.", 0, datetime.now().isoformat()),
        )
    elif action == "reject":
        conn.execute(
            "UPDATE payment_requests SET status='rejected', admin_note=?, reviewed_at=?, reviewed_by=? WHERE id=?",
            (note or "رد شد", datetime.now().isoformat(), admin["id"], pid),
        )
        conn.execute(
            "INSERT INTO support_messages (user_id, sender, message, is_read, created_at) VALUES (?,?,?,?,?)",
            (row["user_id"], "admin", f"❌ درخواست پرداخت رد شد. {note}".strip(), 0, datetime.now().isoformat()),
        )
    else:
        conn.close()
        return jsonify({"success": False, "error": "action invalid"}), 400
    conn.commit()
    conn.close()
    return jsonify({"success": True})


@app.route("/api/admin/settings", methods=["GET", "POST"])
@api_admin_required
def admin_settings():
    conn = get_db()
    if request.method == "GET":
        data = {
            **get_payment_info(conn),
            "plans": get_plans(conn),
        }
        conn.close()
        return jsonify(data)
    data = request.get_json(silent=True) or {}
    if "card_number" in data:
        set_meta(conn, "payment_card_number", str(data["card_number"]))
    if "card_owner" in data:
        set_meta(conn, "payment_card_owner", str(data["card_owner"]))
    if "bank_name" in data:
        set_meta(conn, "payment_bank_name", str(data["bank_name"]))
    if "support_phone" in data:
        set_meta(conn, "support_phone", str(data["support_phone"]))
    if "plans" in data and isinstance(data["plans"], list):
        set_meta(conn, "plans_json", json.dumps(data["plans"], ensure_ascii=False))
    conn.commit()
    conn.close()
    return jsonify({"success": True})


@app.route("/api/admin/content", methods=["GET", "POST"])
@api_admin_required
def admin_content():
    conn = get_db()
    if request.method == "GET":
        rows = conn.execute("SELECT * FROM site_content").fetchall()
        conn.close()
        return jsonify([dict(r) for r in rows])
    data = request.get_json(silent=True) or {}
    key = data.get("key")
    if key not in ("about", "terms", "guide", "buy_guide"):
        conn.close()
        return jsonify({"success": False, "error": "key invalid"}), 400
    conn.execute(
        """INSERT INTO site_content (key, title, body, updated_at) VALUES (?,?,?,?)
           ON CONFLICT(key) DO UPDATE SET title=excluded.title, body=excluded.body, updated_at=excluded.updated_at""",
        (key, data.get("title") or key, data.get("body") or "", datetime.now().isoformat()),
    )
    conn.commit()
    conn.close()
    return jsonify({"success": True})


@app.route("/api/admin/support/threads", methods=["GET"])
@api_admin_required
def admin_support_threads():
    conn = get_db()
    rows = conn.execute(
        """SELECT m.user_id, u.username,
                  MAX(m.created_at) as last_at,
                  SUM(CASE WHEN m.sender='user' AND m.is_read=0 THEN 1 ELSE 0 END) as unread
           FROM support_messages m JOIN users u ON u.id=m.user_id
           GROUP BY m.user_id ORDER BY last_at DESC LIMIT 100"""
    ).fetchall()
    conn.close()
    return jsonify([dict(r) for r in rows])


@app.route("/api/admin/support/<int:uid>/messages", methods=["GET", "POST"])
@api_admin_required
def admin_support_user(uid):
    conn = get_db()
    if request.method == "GET":
        rows = conn.execute(
            "SELECT * FROM support_messages WHERE user_id=? ORDER BY id ASC", (uid,)
        ).fetchall()
        conn.execute(
            "UPDATE support_messages SET is_read=1 WHERE user_id=? AND sender='user'", (uid,)
        )
        conn.commit()
        conn.close()
        return jsonify({"success": True, "messages": [dict(r) for r in rows]})
    data = request.get_json(silent=True) or {}
    msg = (data.get("message") or "").strip()
    if not msg:
        conn.close()
        return jsonify({"success": False, "error": "empty"}), 400
    conn.execute(
        "INSERT INTO support_messages (user_id, sender, message, is_read, created_at) VALUES (?,?,?,?,?)",
        (uid, "admin", msg, 0, datetime.now().isoformat()),
    )
    conn.commit()
    conn.close()
    return jsonify({"success": True})



@app.route("/manifest.webmanifest")
def web_manifest():
    return send_from_directory(
        os.path.join(os.path.dirname(os.path.abspath(__file__)), "static"),
        "manifest.webmanifest",
        mimetype="application/manifest+json",
    )


@app.route("/sw.js")
def service_worker():
    resp = send_from_directory(
        os.path.join(os.path.dirname(os.path.abspath(__file__)), "static"),
        "sw.js",
        mimetype="application/javascript",
    )
    resp.headers["Service-Worker-Allowed"] = "/"
    resp.headers["Cache-Control"] = "no-cache"
    return resp


@app.route("/static/<path:filename>")
def static_files(filename):
    return send_from_directory(
        os.path.join(os.path.dirname(os.path.abspath(__file__)), "static"),
        filename,
    )


@app.route("/api/push/vapid-public")
@api_login_required
def push_vapid_public():
    conn = get_db()
    pub, _priv = ensure_vapid_keys(conn)
    conn.close()
    if not pub:
        return jsonify({"success": False, "error": "vapid_unavailable"}), 500
    return jsonify({"success": True, "publicKey": pub})


@app.route("/api/push/subscribe", methods=["POST"])
@api_login_required
def push_subscribe():
    user = get_current_user()
    data = request.get_json(silent=True) or {}
    endpoint = data.get("endpoint")
    keys = data.get("keys") or {}
    if not endpoint or not keys.get("p256dh") or not keys.get("auth"):
        return jsonify({"success": False, "error": "invalid subscription"}), 400
    conn = get_db()
    ensure_vapid_keys(conn)
    now = datetime.now().isoformat()
    conn.execute(
        "INSERT INTO push_subscriptions (user_id, endpoint, p256dh, auth, user_agent, created_at, last_seen) VALUES (?,?,?,?,?,?,?) "
        "ON CONFLICT(endpoint) DO UPDATE SET user_id=excluded.user_id, p256dh=excluded.p256dh, auth=excluded.auth, last_seen=excluded.last_seen",
        (user["id"], endpoint, keys.get("p256dh"), keys.get("auth"), (request.headers.get("User-Agent") or "")[:250], now, now),
    )
    conn.commit()
    conn.close()
    return jsonify({"success": True})


@app.route("/api/push/test", methods=["POST"])
@api_login_required
def push_test():
    user = get_current_user()
    n = send_web_push_to_user(user["id"], "تست دیده‌بان دیوار", "اگر این پیام را می‌بینید نوتیفیکیشن فعال است", "/")
    return jsonify({"success": True, "sent": n, "message": ("%d نوتیف ارسال شد" % n) if n else "subscription نیست یا pywebpush نصب نشده"})


@app.route("/api/push/reset", methods=["POST"])
@api_login_required
def push_reset():
    """حذف همه سابسکریپشن‌های پوش کاربر تا بتواند دوباره اجازه بگیرد و ثبت کند"""
    user = get_current_user()
    conn = get_db()
    conn.execute("DELETE FROM push_subscriptions WHERE user_id=?", (user["id"],))
    conn.commit()
    conn.close()
    return jsonify({"success": True, "message": "سابسکریپشن‌ها پاک شد. صفحه را رفرش کنید و دوباره اجازه نوتیفیکیشن بدهید."})


if __name__ == "__main__":
    app.run(debug=True, port=5000)

# DB init بعد از تعریف همه توابع — خطا نباید کل اپ را پایین بکشد
try:
    init_db()
except Exception as _init_err:
    print("[INIT] non-fatal init_db error:", _init_err)
