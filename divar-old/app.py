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
from typing import List, Dict, Optional

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

DESC_CACHE: Dict[str, tuple] = {}
DESC_CACHE_LOCK = threading.Lock()
DESC_CACHE_TTL = 600

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


def parse_price(price_str: str) -> Optional[int]:
    if not price_str:
        return None
    s = normalize_digits(str(price_str)).replace(",", "").replace("٬", "")
    numbers = re.findall(r"\d+", s)
    if numbers:
        try:
            return int("".join(numbers))
        except Exception:
            return None
    return None


def _normalize_fa_digits(text: str) -> str:
    """تبدیل ارقام فارسی/عربی به انگلیسی + نرمال‌سازی فاصله و نیم‌فاصله"""
    if not text:
        return ""
    fa = "۰۱۲۳۴۵۶۷۸۹٠١٢٣٤٥٦٧٨٩"
    en = "01234567890123456789"
    trans = str.maketrans(fa, en)
    text = text.translate(trans)
    text = text.lower()
    text = text.replace("‌", " ").replace("ـ", " ").replace("-", " ").replace("_", " ")
    text = text.replace("ي", "ی").replace("ك", "ک")
    # فاصله‌های اضافه
    while "  " in text:
        text = text.replace("  ", " ")
    return text.strip()


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


def get_post_description(token: str) -> str:
    for url in [
        f"https://api.divar.ir/v8/posts/{token}",
        f"https://api.divar.ir/v8/posts-v2/web/{token}",
    ]:
        try:
            r = requests.get(url, headers=HEADERS, timeout=12)
            if r.status_code != 200:
                continue
            data = r.json()
            if isinstance(data.get("description"), str) and len(data["description"]) > 5:
                return data["description"]
            if isinstance(data.get("data"), dict) and isinstance(data["data"].get("description"), str):
                return data["data"]["description"]

            def dig(obj, depth=0):
                if depth > 6:
                    return ""
                if isinstance(obj, dict):
                    for k in ["description", "text", "value", "content"]:
                        v = obj.get(k)
                        if isinstance(v, str) and len(v) > 20 and not v.startswith("http"):
                            return v
                    for v in obj.values():
                        f = dig(v, depth + 1)
                        if f:
                            return f
                elif isinstance(obj, list):
                    for i in obj:
                        f = dig(i, depth + 1)
                        if f:
                            return f
                return ""

            found = dig(data)
            if found:
                return found
        except Exception:
            pass
    return ""


def get_post_description_cached(token: str) -> str:
    now = time.time()
    with DESC_CACHE_LOCK:
        cached = DESC_CACHE.get(token)
        if cached and now - cached[1] < DESC_CACHE_TTL:
            return cached[0]
    with SEARCH_SEMAPHORE:
        desc = get_post_description(token)
    with DESC_CACHE_LOCK:
        DESC_CACHE[token] = (desc, now)
    return desc


def find_matched_keywords(text: str, keywords: List[str]) -> List[str]:
    if not text or not keywords:
        return []
    t = normalize_digits(text.lower())
    return [k for k in keywords if normalize_digits(k.lower()) in t]


def has_negative_keyword(text: str, negative_keywords: List[str]) -> bool:
    if not text or not negative_keywords:
        return False
    t = normalize_digits(text.lower())
    return any(normalize_digits(k.lower()) in t for k in negative_keywords if k.strip())


def filter_negative_keywords(ads: List[Dict], negative_keywords: List[str], max_fetch: int = 25) -> List[Dict]:
    if not negative_keywords:
        return ads
    negative_keywords = [k.strip() for k in negative_keywords if k.strip()]
    if not negative_keywords:
        return ads
    kept = []
    fetch = 0
    for ad in ads:
        title = ad.get("title") or ""
        if has_negative_keyword(title, negative_keywords):
            continue
        blocked = False
        if fetch < max_fetch:
            desc = get_post_description_cached(ad["token"])
            fetch += 1
            if has_negative_keyword(desc, negative_keywords):
                blocked = True
        if not blocked:
            kept.append(ad)
    return kept


def enrich_ads_with_keywords(ads: List[Dict], keywords: List[str], max_fetch: int = 20) -> List[Dict]:
    if not keywords:
        for ad in ads:
            ad["matched_keywords"] = []
            ad["has_keyword"] = False
        return ads
    keywords = [k.strip() for k in keywords if k.strip()]
    fetch = 0
    for ad in ads:
        matched = find_matched_keywords(ad.get("title") or "", keywords)
        if not matched and fetch < max_fetch:
            desc = get_post_description_cached(ad["token"])
            fetch += 1
            matched = find_matched_keywords(desc, keywords)
        ad["matched_keywords"] = matched
        ad["has_keyword"] = bool(matched)
    ads.sort(key=lambda x: (not x.get("has_keyword", False), -(x.get("value_score") or 0)))
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
    "مدارک ناقص", "معاوضه", "اقساط", "قسطی", "رنگ درب", "رنگ کاپوت", "رنگ شده کامل",
    "بدون بیمه", "پایان کار", "ساندویچی",
]
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
    "پارکینگ", "انباری", "آسانسور", "بالکن", "نورگیر", "جنوبی", "لوکس",
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


def score_ad_smart(
    ad: Dict,
    avg: float,
    category: str,
    profile: str = "balanced",
    desc: str = "",
) -> Dict:
    """
    امتیاز چندبعدی کالیبره‌شده روی مقیاس تقریبی ۰ تا ۱۰۰.

    کالیبراسیون هدف:
    - آگهی معمولی نزدیک میانگین بازار: حدود ۳۵–۵۰
    - فرصت خوب (قیمت مناسب + متن تمیز): حدود ۵۵–۷۰
    - فرصت عالی (تخفیف واضح + تازگی + کیفیت): حدود ۷۰–۸۵
    - امتیاز بالای ۹۰ نادر است

    اجزا:
      قیمت ۰–۴۰ | تازگی ۰–۱۵ | کیفیت ۰–۱۵ | فروشنده ۰–۸
      مثبت ۰–۸ | کامل بودن ۰–۶ | منفی تا −۳۰
    """
    cfg = SMART_PROFILES.get(profile) or SMART_PROFILES["balanced"]
    price = ad.get("price_num")
    reasons = []
    breakdown = {
        "price": 0.0,
        "freshness": 0.0,
        "quality": 0.0,
        "seller": 0.0,
        "negative": 0.0,
        "completeness": 0.0,
        "positive": 0.0,
    }

    if not price or not avg or avg <= 0:
        if category in ("apartment-sell", "apartment-rent"):
            price = None
        else:
            return {
                "value_score": 0.0,
                "score_breakdown": breakdown,
                "score_reason": "قیمت نامشخص یا میانگین بازار در دسترس نیست",
                "pass_filter": False,
            }

    # ---- قیمت (۰ تا ۴۰) — اصلی‌ترین عامل ----
    price_score = 0.0
    ratio = 1.0
    if price and avg and avg > 0:
        ratio = price / avg
        min_r = cfg["min_price_ratio"]
        max_r = cfg["max_price_ratio"]
        if category in ("apartment-sell", "apartment-rent"):
            min_r = min(min_r, 0.35)
            max_r = max(max_r, 1.8)
        # خیلی ارزان مشکوک یا خیلی گران → رد
        if ratio < min_r or ratio > max_r:
            return {
                "value_score": 0.0,
                "score_breakdown": breakdown,
                "score_reason": f"خارج از بازه قیمت معقول نسبت به بازار (نسبت {ratio:.2f})",
                "pass_filter": False,
            }
        # منحنی تخفیف: هر ۱٪ زیر میانگین ≈ ۱.۲ امتیاز، سقف ۴۰
        # ۲۰٪ زیر میانگین ≈ ۲۴ | ۳۰٪ ≈ ۳۶ | ۳۳٪+ ≈ ۴۰
        discount = max(0.0, (avg - price) / avg)
        price_score = min(40.0, discount * 120.0)
        if ratio <= 1.0:
            # نزدیک میانگین هم کمی امتیاز پایه دارد (نه خیلی)
            near = max(0.0, 8.0 * (1.0 - max(0.0, ratio - 0.88) / 0.12))
            price_score = max(price_score, near)
        else:
            # بالای میانگین: جریمه ملایم
            over = min(12.0, (ratio - 1.0) * 40.0)
            price_score = max(0.0, 6.0 - over)
        breakdown["price"] = round(price_score, 2)
        if discount >= 0.15:
            reasons.append(f"قیمت {int(discount * 100)}٪ زیر میانگین بازار")
        elif discount >= 0.07:
            reasons.append(f"قیمت حدود {int(discount * 100)}٪ زیر میانگین")
        elif ratio <= 1.02:
            reasons.append("قیمت نزدیک میانگین بازار")
        else:
            reasons.append("قیمت بالاتر از میانگین")
    else:
        price_score = 8.0 if category in ("apartment-sell", "apartment-rent") else 0.0
        breakdown["price"] = price_score
        if category in ("apartment-sell", "apartment-rent"):
            reasons.append("قیمت کامل استخراج نشد")

    # ---- تازگی (۰ تا ۱۵) — بدون امتیاز رایگان برای نامشخص ----
    hours = parse_freshness_hours(ad.get("time_text") or "")
    if hours is None:
        fresh = 4.0  # نامشخص = خنثی، نه جایزه
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

    # ---- کیفیت متن/وضعیت (۰ تا ۱۵) ----
    title = ad.get("title") or ""
    status = ad.get("status") or ""
    full_text = f"{title} {status} {desc or ''}"

    if category == "mobile":
        q = mobile_status_score(status) * 1.2
        q += mobile_storage_score(title) * 0.7
        q += mobile_ram_score(title) * 0.55
        q = min(15.0, q)
        if mobile_status_score(status) >= 3.5:
            reasons.append("وضعیت ظاهری خوب")
    elif category in ("apartment-sell", "apartment-rent"):
        q = 5.0
        for kw, bonus in (("نوساز", 3.5), ("فول", 1.5), ("لوکس", 1.5), ("بازسازی", 1.5), ("سند تک", 2.5), ("کلید نخورده", 2.5)):
            if kw in full_text:
                q += bonus
        q = min(15.0, q)
        if q >= 10:
            reasons.append("متن املاک مثبت")
    else:
        q = car_body_score(title, status) * 1.6 + car_motor_score(title, desc) * 1.2
        q = min(15.0, q)
        if car_body_score(title, status) >= 3.5:
            reasons.append("وضعیت بدنه/متن مثبت")
    breakdown["quality"] = round(q, 2)

    # ---- نوع فروشنده (۰ تا ۸) ----
    seller = 4.0  # پایه خنثی
    if ad.get("is_shop"):
        seller = 2.5
        reasons.append("فروشگاه/آژانس")
    else:
        seller = 6.5
        reasons.append("آگهی شخصی")
    if ad.get("is_nardeban"):
        seller -= 1.5
    breakdown["seller"] = round(max(0.0, min(8.0, seller)), 2)

    # ---- سیگنال منفی (کسر تا −۳۰) ----
    if category == "mobile":
        neg_list = MOBILE_NEG_SIGNALS
    elif category in ("apartment-sell", "apartment-rent"):
        neg_list = RE_NEG_SIGNALS
    else:
        neg_list = CAR_NEG_SIGNALS
    neg_hits = _signal_hits(full_text, neg_list)
    hard = {"طرح", "کپی", "مشابه اصل", "high copy", "تصادفی", "چپی", "اتاق تعویض", "تعویض موتور", "قولنامه‌ای"}
    neg_penalty = 0.0
    for h in neg_hits:
        neg_penalty += 10.0 if h in hard else 4.5
    neg_penalty = min(30.0, neg_penalty)
    breakdown["negative"] = round(-neg_penalty, 2)
    if neg_hits:
        reasons.append("ریسک: " + "، ".join(neg_hits[:3]))

    # ---- سیگنال مثبت (۰ تا ۸) ----
    if category == "mobile":
        pos_list = MOBILE_POS_SIGNALS
    elif category in ("apartment-sell", "apartment-rent"):
        pos_list = RE_POS_SIGNALS
    else:
        pos_list = CAR_POS_SIGNALS
    pos_hits = _signal_hits(full_text, pos_list)
    pos_bonus = min(8.0, len(pos_hits) * 2.0)
    breakdown["positive"] = round(pos_bonus, 2)
    if pos_hits and not neg_hits:
        reasons.append("نشانه‌های مثبت در متن")

    # ---- کامل بودن (۰ تا ۶) ----
    comp = 0.0
    if ad.get("image_url"):
        comp += 2.0
    ic = ad.get("image_count") or 0
    if ic >= 3:
        comp += 2.5
    elif ic >= 1:
        comp += 1.0
    if price:
        comp += 1.5
    breakdown["completeness"] = round(min(6.0, comp), 2)

    total = (
        breakdown["price"]
        + breakdown["freshness"]
        + breakdown["quality"]
        + breakdown["seller"]
        + breakdown["negative"]
        + breakdown["positive"]
        + breakdown["completeness"]
    )
    total = round(max(0.0, min(100.0, total)), 1)

    min_score = cfg["min_score"]
    hard_hit = any(h in hard for h in neg_hits)
    pass_filter = total >= min_score and breakdown["negative"] > -18 and not hard_hit

    reason = " · ".join(reasons[:4]) if reasons else "امتیاز ترکیبی قیمت و کیفیت"
    return {
        "value_score": total,
        "score_breakdown": breakdown,
        "score_reason": reason,
        "pass_filter": pass_filter,
        "neg_hits": neg_hits,
        "pos_hits": pos_hits,
    }


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


def _parse_re_price_fields(price_str: str) -> Dict:
    """استخراج اجاره/ودیعه/قیمت از متن وسط کارت دیوار."""
    out = {"price_num": None, "rent_num": None, "credit_num": None, "price_display": price_str}
    if not price_str:
        return out
    s = normalize_digits(str(price_str))
    # الگوهای رایج اجاره: «ودیعه X | اجاره Y» یا چند عدد
    nums = []
    for m in re.finditer(r"(\d[\d,]*)", s.replace("٬", ",")):
        try:
            nums.append(int(m.group(1).replace(",", "")))
        except Exception:
            pass
    low = s.replace(" ", "")
    if "اجاره" in s or "رهن" in s or "ودیعه" in s:
        # سعی در نسبت دادن
        credit = None
        rent = None
        if "ودیعه" in s or "رهن" in s:
            m = re.search(r"(?:ودیعه|رهن)\s*:?\s*([\d,]+)", s)
            if m:
                try:
                    credit = int(m.group(1).replace(",", ""))
                except Exception:
                    pass
        if "اجاره" in s:
            m = re.search(r"اجاره\s*:?\s*([\d,]+)", s)
            if m:
                try:
                    rent = int(m.group(1).replace(",", ""))
                except Exception:
                    pass
        if credit is None and rent is None and len(nums) >= 2:
            # معمولاً عدد بزرگ‌تر ودیعه است
            a, b = nums[0], nums[1]
            credit, rent = (a, b) if a >= b else (b, a)
        elif credit is None and nums:
            credit = max(nums)
        out["credit_num"] = credit
        out["rent_num"] = rent
        # برای امتیازدهی: ترکیب تقریبی ارزش (ودیعه + اجاره*12/3) یا فقط ودیعه
        if credit and rent:
            out["price_num"] = int(credit + rent * 20)
        elif credit:
            out["price_num"] = credit
        elif rent:
            out["price_num"] = rent * 30
        else:
            out["price_num"] = parse_price(price_str)
    else:
        out["price_num"] = parse_price(price_str)
    return out


def extract_ads(data: Dict) -> List[Dict]:
    ads = []
    for w in data.get("list_widgets", []):
        wt = w.get("widget_type")
        if wt not in ("POST_ROW", "LEGACY_POST_ROW", "POST"):
            continue
        p = w.get("data", {})
        token = p.get("token")
        if not token:
            continue
        price_str = p.get("middle_description_text")
        red_text = p.get("red_text") or ""
        top_text = p.get("top_description_text") or ""
        bottom = p.get("bottom_description_text") or ""
        # در املاک bottom اغلب نام آژانس است؛ time ممکن است جدا باشد
        time_text = bottom
        if any(x in bottom for x in ("املاک", "آژانس", "مشاور", "بنگاه")):
            time_text = top_text if any(y in top_text for y in ("پیش", "ساعت", "دقیقه", "روز")) else ""
        re_fields = _parse_re_price_fields(price_str or "")
        price_num = re_fields.get("price_num")
        if price_num is None:
            price_num = parse_price(price_str)
        is_agency = any(x in (red_text + bottom + top_text) for x in ("املاک", "آژانس", "مشاورین", "فروشگاه"))
        ads.append({
            "token": token,
            "title": p.get("title"),
            "price": price_str,
            "price_num": price_num,
            "rent_num": re_fields.get("rent_num"),
            "credit_num": re_fields.get("credit_num"),
            "status": top_text,
            "red_text": red_text,
            "time_text": time_text,
            "seller_text": bottom,
            "image_url": p.get("image_url"),
            "image_count": p.get("image_count") or 0,
            "is_nardeban": "نردبان" in red_text,
            "is_shop": is_agency or ("فروشگاه" in red_text) or ("فروشگاه" in top_text),
            "link": f"https://divar.ir/v/{token}",
        })
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


def finalize_ads(ads: List[Dict], keywords: List[str], negative_keywords: List[str]) -> List[Dict]:
    ads = filter_negative_keywords(ads, negative_keywords)
    ads = enrich_ads_with_keywords(ads, keywords)
    return ads


# ====================== worker functions (parallel per-model) ======================
def process_model_smart(model, category, city_ids, recent_ads, business_type, min_smart_score, smart_profile="balanced"):
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
        ads = extract_ads(raw)
        # فیلتر دقیق مدل — جلوگیری از قاطی شدن مدل‌های دیگر
        ads = [a for a in ads if is_model_match(a.get("title") or "", model)]
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
            result = score_ad_smart(ad, avg, category, profile, desc="")
            ad["value_score"] = result["value_score"]
            ad["score_reason"] = result["score_reason"]
            ad["score_breakdown"] = result["score_breakdown"]
            ad["model_avg"] = int(avg)
            ad["smart_profile"] = profile
            phase1.append(ad)

        phase1.sort(key=lambda x: x.get("value_score") or 0, reverse=True)

        # مرحله ۲: عمیق‌سازی روی برترین‌ها
        deep_n = min(cfg["deep_fetch"], len(phase1))
        for ad in phase1[:deep_n]:
            try:
                desc = get_post_description_cached(ad["token"])
            except Exception:
                desc = ""
            if desc:
                ad["_desc"] = desc[:500]
                result = score_ad_smart(ad, avg, category, profile, desc=desc)
                ad["value_score"] = result["value_score"]
                ad["score_reason"] = result["score_reason"]
                ad["score_breakdown"] = result["score_breakdown"]

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
        ads = extract_ads(raw)
        ads = [a for a in ads if is_model_match(a.get("title") or "", model)]
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
        ads = extract_ads(raw)
        # فیلتر دقیق مدل تا مدل‌های هم‌خانواده قاطی نشوند
        ads = [a for a in ads if is_model_match(a.get("title") or "", model)]
        return model, None, ads
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


def process_real_estate(category, city_ids, recent_ads, data, smart=False, min_smart_score=55, smart_profile="balanced"):
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
        ads = extract_ads(raw)
        if not smart:
            return {"count": len(ads), "avg": None}, ads

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
            result = score_ad_smart(ad, avg, category, smart_profile, desc="")
            if not result:
                continue
            # ادغام فیلدهای امتیاز روی خود آگهی
            merged = dict(ad)
            merged.update(result)
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
            desc = ""
            try:
                if ad.get("token"):
                    desc = get_post_description(ad["token"]) or ""
                    time.sleep(0.15)
            except Exception:
                desc = ""
            rescored = score_ad_smart(ad, avg, category, smart_profile, desc=desc)
            if not rescored:
                continue
            merged = dict(ad)
            merged.update(rescored)
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
    city_ids = [data.get("city", "5")]
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
        )
        ads = finalize_ads(ads, keywords, negative_keywords)
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

        all_ads = finalize_ads(all_ads, keywords, negative_keywords)
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

        all_ads = finalize_ads(all_ads, keywords, negative_keywords)
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

        all_ads = finalize_ads(all_ads, keywords, negative_keywords)
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


@app.route("/api/admin/users", methods=["GET"])
@api_admin_required
def admin_list_users():
    conn = get_db()
    rows = conn.execute(
        "SELECT id, username, role, is_active, plan, subscription_expires_at, max_monitors, created_at, last_login FROM users ORDER BY id DESC"
    ).fetchall()
    conn.close()
    return jsonify([dict(r) for r in rows])


@app.route("/api/admin/users", methods=["POST"])
@api_admin_required
def admin_create_user():
    data = request.json or {}
    username = (data.get("username") or "").strip()
    password = data.get("password") or ""
    if len(username) < 3 or len(password) < 6:
        return jsonify({"success": False, "error": "invalid"}), 400
    role = data.get("role", "user")
    plan = data.get("plan", "free")
    days = int(data.get("days", 30) or 30)
    max_monitors = int(data.get("max_monitors", 3) or 3)
    expires = None if role == "admin" else (datetime.now() + timedelta(days=days)).isoformat()
    conn = get_db()
    if conn.execute("SELECT 1 FROM users WHERE username=?", (username,)).fetchone():
        conn.close()
        return jsonify({"success": False, "error": "exists"}), 409
    conn.execute(
        "INSERT INTO users (username, password_hash, role, is_active, plan, subscription_expires_at, max_monitors, created_at) VALUES (?,?,?,?,?,?,?,?)",
        (username, generate_password_hash(password), role, 1, plan, expires, max_monitors, datetime.now().isoformat()),
    )
    conn.commit()
    conn.close()
    return jsonify({"success": True})


@app.route("/api/admin/users/<int:uid>", methods=["PUT"])
@api_admin_required
def admin_update_user(uid):
    data = request.json or {}
    conn = get_db()
    user = conn.execute("SELECT * FROM users WHERE id=?", (uid,)).fetchone()
    if not user:
        conn.close()
        return jsonify({"success": False}), 404
    plan = data.get("plan", user["plan"])
    is_active = 1 if data.get("is_active", user["is_active"]) else 0
    max_monitors = int(data.get("max_monitors", user["max_monitors"]))
    role = data.get("role", user["role"])
    expires = data.get("subscription_expires_at", user["subscription_expires_at"])
    new_password = data.get("password")
    if new_password and len(new_password) >= 6:
        conn.execute("UPDATE users SET password_hash=? WHERE id=?", (generate_password_hash(new_password), uid))
    conn.execute(
        "UPDATE users SET plan=?, is_active=?, max_monitors=?, role=?, subscription_expires_at=? WHERE id=?",
        (plan, is_active, max_monitors, role, expires, uid),
    )
    conn.commit()
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
