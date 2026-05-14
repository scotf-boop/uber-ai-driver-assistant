from flask import Flask, render_template, request, redirect, url_for, send_from_directory
from dataclasses import dataclass
from pathlib import Path
import os
from werkzeug.utils import secure_filename
import sqlite3
import re
import uuid
from datetime import datetime, date

app = Flask(__name__)
# SAFE_DEFAULT_US_MARKET_PATCH
DEFAULT_US_MARKET = globals().get("DEFAULT_US_MARKET", {
    "name": "US Default Market",
    "min_accept": 0.90,
    "normal": 1.20,
    "good": 1.80,
    "excellent": 2.80,
    "min_hour": 20,
    "good_hour": 30,
    "excellent_hour": 40,
    "adjust": 0,
    "note": "未匹配到具体城市，使用美国通用 UberX 评估标准。"
})

BASE_DIR = Path(__file__).parent
UPLOAD_DIR = BASE_DIR / "uploads"
DATA_DIR = BASE_DIR / "data"
DB_PATH = DATA_DIR / "orders.db"
UPLOAD_DIR.mkdir(exist_ok=True)
DATA_DIR.mkdir(exist_ok=True)

UBERX_RULES = {
    "trash": "< $1/mi",
    "normal": "$1.2-$1.6/mi",
    "good": "$1.8-$2.5/mi",
    "excellent": "$3+/mi",
}

@dataclass
class Analysis:
    income: float
    miles: float
    minutes: float
    gas_price: float
    mpg: float
    wear_rate: float
    fuel_cost: float
    wear_cost: float
    net_profit: float
    gross_per_mile: float
    net_per_mile: float
    gross_per_hour: float
    net_per_hour: float
    uberx_level: str
    grade: str
    advice: str
    css_class: str
    score: int
    risk: str
    action: str
    pro_reason: str
    pro_warning: str
    plan: str
    ocr_text: str = ""

def money(x):
    try:
        return f"${x:,.2f}"
    except Exception:
        return "$0.00"

def init_db():
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    cur.execute("""
    CREATE TABLE IF NOT EXISTS app_feedback (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        created_at TEXT,
        feedback_type TEXT,
        rating TEXT,
        message TEXT
    )
    """)
    cur.execute("""
    CREATE TABLE IF NOT EXISTS orders (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        created_at TEXT,
        plan TEXT,
        image_path TEXT,
        ocr_text TEXT,
        income REAL,
        miles REAL,
        minutes REAL,
        gas_price REAL,
        mpg REAL,
        wear_rate REAL,
        fuel_cost REAL,
        wear_cost REAL,
        net_profit REAL,
        gross_per_mile REAL,
        net_per_mile REAL,
        gross_per_hour REAL,
        net_per_hour REAL,
        score REAL,
        risk TEXT,
        action TEXT,
        grade TEXT,
        advice TEXT,
        user_feedback TEXT,
        user_note TEXT,
        consent_share INTEGER DEFAULT 0
    )
    """)
    conn.commit()
    # Add columns for older DB compatibility
    cols = [r[1] for r in cur.execute("PRAGMA table_info(orders)").fetchall()]
    for col, typ in {
        "plan": "TEXT", "gross_per_hour": "REAL", "score": "REAL",
        "risk": "TEXT", "action": "TEXT"
    }.items():
        if col not in cols:
            cur.execute(f"ALTER TABLE orders ADD COLUMN {col} {typ}")
    conn.commit()
    conn.close()

init_db()

def run_ocr(image_path: Path) -> str:
    try:
        from rapidocr_onnxruntime import RapidOCR
        engine = RapidOCR()
        result, _ = engine(str(image_path))
        if not result:
            return ""
        lines = []
        for item in result:
            if len(item) >= 2:
                lines.append(str(item[1]))
        return "\n".join(lines)
    except Exception as e:
        return f"OCR_ERROR: {e}"

def normalize_text(text: str) -> str:
    text = text or ""
    repl = {
        "Us$": "US$", "us$": "US$", "US $": "US$", "U S$": "US$",
        "＄": "$", "﹩": "$", "英哩": "英里", "哩": "英里",
        "MILES": "miles", "Miles": "miles", "Mile": "mile",
        "O.": "0.", "o.": "0.", "，": ".", ",": ".",
        "–": "-", "—": "-", "：": ":"
    }
    for k, v in repl.items():
        text = text.replace(k, v)
    return text

def number_list(text):
    return [float(x) for x in re.findall(r"[0-9]+(?:\.[0-9]+)?", text or "")]

def extract_from_text(text):
    clean = normalize_text(text)
    if not clean.strip():
        return None, None, None

    income = None
    miles = None
    minutes = None
    lines = [ln.strip() for ln in clean.splitlines() if ln.strip()]

    # 1. 收入：逐行找 $ 金额，取最大合理值
    dollar_values = []
    for line in lines:
        if "$" in line:
            nums = number_list(line)
            for n in nums:
                if 2 <= n <= 500:
                    dollar_values.append(n)
    if dollar_values:
        income = max(dollar_values)

    # 2. 英里：逐行找 miles / mi / 英里 / 距离
    mile_values = []
    for line in lines:
        lower = line.lower()
        if ("mile" in lower) or (" mi" in lower) or ("英里" in line) or ("距离" in line) or ("里程" in line):
            nums = number_list(line)
            for n in nums:
                if 0.1 <= n <= 300:
                    mile_values.append(n)
    if mile_values:
        miles = mile_values[-1]

    def parse_time_from_text(chunk):
        chunk = chunk or ""
        lower = chunk.lower()
        nums = number_list(chunk)
        if not nums:
            return None

        # 2小时28分钟 / 2 hr 28 min
        if ("小时" in chunk) or ("hour" in lower) or ("hr" in lower):
            h = nums[0]
            m = nums[1] if len(nums) > 1 else 0
            total = h * 60 + m
            if 1 <= total <= 360:
                return total

        # 148分钟 / 148 min
        if ("分钟" in chunk) or (" min" in lower) or lower.endswith("min") or ("minute" in lower):
            total = nums[0]
            if 1 <= total <= 360:
                return total
        return None

    # 3. 时间：先强制优先“行程时间”附近，避免绿色提示里的“延误5分钟”
    for idx, line in enumerate(lines):
        lower = line.lower()
        if ("行程时间" in line) or ("行程时长" in line) or ("trip time" in lower) or ("duration" in lower):
            # 先看本行 + 后面最多4行
            nearby = " ".join(lines[idx:idx+5])
            t = parse_time_from_text(nearby)
            if t is not None:
                minutes = t
                break

            # 如果 OCR 把“2小时28分钟”拆成单独的下一行，也逐行看
            for nxt in lines[idx+1:idx+6]:
                t = parse_time_from_text(nxt)
                if t is not None:
                    minutes = t
                    break
            if minutes is not None:
                break

    # 4. 如果没找到“行程时间”，再找所有“小时”格式。优先小时格式，忽略单独5分钟延误。
    if minutes is None:
        hour_candidates = []
        for line in lines:
            t = parse_time_from_text(line)
            lower = line.lower()
            if t is not None and (("小时" in line) or ("hour" in lower) or ("hr" in lower)):
                hour_candidates.append(t)
        if hour_candidates:
            minutes = hour_candidates[-1]

    # 5. 最后才找“分钟/min”格式；这一步可能抓到延误5分钟，所以放最后。
    if minutes is None:
        minute_candidates = []
        for line in lines:
            # 排除明显不是行程时间的提示文字
            if ("延误" in line) or ("增加" in line) or ("等待" in line) or ("delay" in line.lower()):
                continue
            t = parse_time_from_text(line)
            if t is not None:
                minute_candidates.append(t)
        if minute_candidates:
            minutes = minute_candidates[-1]

    nums_all = number_list(clean)

    # fallback 收入
    if income is None:
        candidates = [n for n in nums_all if 2 <= n <= 500]
        income = max(candidates) if candidates else None

    # fallback 英里
    if miles is None:
        candidates = [n for n in nums_all if 0.1 <= n <= 300 and n != income]
        decimals = [n for n in candidates if abs(n - int(n)) > 0.001]
        miles = decimals[-1] if decimals else (candidates[-1] if candidates else None)

    if minutes is not None and not (1 <= minutes <= 360):
        minutes = None

    return income, miles, minutes

def uberx_level(gpm):
    market = US_MARKET_ZONES[1]  # Los Angeles default for backward compatibility
    label, _ = market_level_by_gpm(gpm, market)
    return label

def ai_score(gpm, nph, net, miles, minutes, market=None):
    market = market or DEFAULT_US_MARKET
    score = 0

    # 每英里收入：根据当地标准打分
    if gpm >= market["excellent"]:
        score += 45
    elif gpm >= market["good"]:
        score += 36
    elif gpm >= market["normal"]:
        score += 25
    elif gpm >= market["min_accept"]:
        score += 12
    else:
        score -= 20

    # 时薪：根据当地城市标准打分
    if nph > 0:
        if nph >= market["excellent_hour"]:
            score += 40
        elif nph >= market["good_hour"]:
            score += 32
        elif nph >= market["min_hour"]:
            score += 22
        else:
            score += 5
    else:
        score += 6

    # 净利润补充
    if net >= 30:
        score += 15
    elif net >= 18:
        score += 11
    elif net >= 8:
        score += 7
    elif net >= 4:
        score += 3

    # 长途/超时惩罚
    if miles > 30:
        score -= 10
    elif miles > 20:
        score -= 5
    if minutes and minutes > 90:
        score -= 10

    score += market.get("adjust", 0)
    return max(0, min(100, int(score)))


# SAFE_ORDER_TYPE_MARKET_PATCH
def order_type_ai(order_type):
    try:
        return UBER_ORDER_TYPES.get(order_type or "uberx", UBER_ORDER_TYPES["uberx"])
    except Exception:
        return {
            "name": "UberX",
            "min_multiplier": 1.00,
            "good_multiplier": 1.00,
            "hour_multiplier": 1.00,
            "risk_adjust": 0,
            "note": "UberX 基础标准。"
        }

def apply_order_type_market(market, order_type):
    if not market:
        market = DEFAULT_US_MARKET
    rule = order_type_ai(order_type)
    m = dict(market)
    m["order_type_name"] = rule.get("name", "UberX")
    m["min_accept"] = market.get("min_accept", 0.90) * rule.get("min_multiplier", 1.0)
    m["normal"] = market.get("normal", 1.20) * rule.get("min_multiplier", 1.0)
    m["good"] = market.get("good", 1.80) * rule.get("good_multiplier", 1.0)
    m["excellent"] = market.get("excellent", 2.80) * rule.get("good_multiplier", 1.0)
    m["min_hour"] = market.get("min_hour", 20) * rule.get("hour_multiplier", 1.0)
    m["good_hour"] = market.get("good_hour", 30) * rule.get("hour_multiplier", 1.0)
    m["excellent_hour"] = market.get("excellent_hour", 40) * rule.get("hour_multiplier", 1.0)
    m["adjust"] = market.get("adjust", 0) + rule.get("risk_adjust", 0)
    m["note"] = market.get("note", "") + " 订单类型：" + rule.get("note", "")
    return m


# ===== SAFE MARKET + ORDER TYPE PATCH V5.3 =====
DEFAULT_US_MARKET = {
    "name": "US Default Market",
    "min_accept": 0.90,
    "normal": 1.20,
    "good": 1.80,
    "excellent": 2.80,
    "min_hour": 20,
    "good_hour": 30,
    "excellent_hour": 40,
    "adjust": 0,
    "note": "未匹配到具体城市，使用美国通用 UberX 评估标准。"
}

def gps_market_ai(lat, lon):
    try:
        lat = float(lat)
        lon = float(lon)
    except Exception:
        return DEFAULT_US_MARKET

    try:
        zones = US_MARKET_ZONES
    except Exception:
        zones = []

    for z in zones:
        try:
            if z["lat_min"] <= lat <= z["lat_max"] and z["lon_min"] <= lon <= z["lon_max"]:
                return z
        except Exception:
            continue
    return DEFAULT_US_MARKET

def market_level_by_gpm(gpm, market=None):
    market = market or DEFAULT_US_MARKET
    min_accept = float(market.get("min_accept", 0.90))
    normal = float(market.get("normal", 1.20))
    good = float(market.get("good", 1.80))
    excellent = float(market.get("excellent", 2.80))

    if gpm < min_accept:
        return f"垃圾单（低于当地最低可接 ${min_accept:.2f}/mi）", "trash"
    if gpm < normal:
        return f"偏低单（低于当地普通线 ${normal:.2f}/mi）", "low"
    if gpm < good:
        return f"普通单（当地普通 ${normal:.2f}+/mi）", "normal"
    if gpm < excellent:
        return f"好单（当地好单 ${good:.2f}+/mi）", "good"
    return f"神单（当地神单 ${excellent:.2f}+/mi）", "excellent"

def hourly_level(nph, market=None):
    market = market or DEFAULT_US_MARKET
    min_hour = float(market.get("min_hour", 20))
    good_hour = float(market.get("good_hour", 30))
    excellent_hour = float(market.get("excellent_hour", 40))

    if nph <= 0:
        return "未识别时薪", "unknown"
    if nph < min_hour:
        return f"时薪偏低（低于当地最低 ${min_hour:.0f}/h）", "low"
    if nph < good_hour:
        return f"普通时薪（当地最低 ${min_hour:.0f}/h+）", "normal"
    if nph < excellent_hour:
        return f"好时薪（当地好时薪 ${good_hour:.0f}/h+）", "good"
    return f"优秀时薪（当地优秀 ${excellent_hour:.0f}/h+）", "excellent"

UBER_ORDER_TYPES = globals().get("UBER_ORDER_TYPES", {
    "uberx": {"name":"UberX","min_multiplier":1.0,"good_multiplier":1.0,"hour_multiplier":1.0,"risk_adjust":0,"note":"UberX 基础标准。"},
    "comfort": {"name":"Comfort","min_multiplier":1.15,"good_multiplier":1.15,"hour_multiplier":1.10,"risk_adjust":3,"note":"Comfort 需要更高单价。"},
    "comfort_electric": {"name":"Comfort Electric","min_multiplier":1.25,"good_multiplier":1.25,"hour_multiplier":1.15,"risk_adjust":4,"note":"Comfort Electric 需要更高溢价。"},
    "uberxl": {"name":"UberXL","min_multiplier":1.35,"good_multiplier":1.35,"hour_multiplier":1.20,"risk_adjust":4,"note":"XL车辆成本更高。"},
    "black": {"name":"Uber Black","min_multiplier":1.80,"good_multiplier":1.80,"hour_multiplier":1.50,"risk_adjust":6,"note":"Black必须高单价高时薪。"},
    "airport": {"name":"Airport","min_multiplier":1.20,"good_multiplier":1.20,"hour_multiplier":1.15,"risk_adjust":-6,"note":"机场单要考虑等待和回程。"},
    "reservation": {"name":"Reservation","min_multiplier":1.25,"good_multiplier":1.25,"hour_multiplier":1.20,"risk_adjust":2,"note":"预约单要考虑提前到达等待。"},
    "shared": {"name":"Uber Share","min_multiplier":0.95,"good_multiplier":1.0,"hour_multiplier":1.0,"risk_adjust":-8,"note":"Share可能绕路和等待。"},
    "eats": {"name":"Uber Eats","min_multiplier":0.85,"good_multiplier":0.90,"hour_multiplier":0.90,"risk_adjust":-5,"note":"Eats考虑等餐停车小费风险。"}
})

def order_type_ai(order_type):
    return UBER_ORDER_TYPES.get(order_type or "uberx", UBER_ORDER_TYPES["uberx"])

def apply_order_type_market(market, order_type):
    market = market or DEFAULT_US_MARKET
    rule = order_type_ai(order_type)
    m = dict(market)
    m["order_type_name"] = rule.get("name", "UberX")
    m["min_accept"] = float(market.get("min_accept", 0.90)) * float(rule.get("min_multiplier", 1.0))
    m["normal"] = float(market.get("normal", 1.20)) * float(rule.get("min_multiplier", 1.0))
    m["good"] = float(market.get("good", 1.80)) * float(rule.get("good_multiplier", 1.0))
    m["excellent"] = float(market.get("excellent", 2.80)) * float(rule.get("good_multiplier", 1.0))
    m["min_hour"] = float(market.get("min_hour", 20)) * float(rule.get("hour_multiplier", 1.0))
    m["good_hour"] = float(market.get("good_hour", 30)) * float(rule.get("hour_multiplier", 1.0))
    m["excellent_hour"] = float(market.get("excellent_hour", 40)) * float(rule.get("hour_multiplier", 1.0))
    m["adjust"] = float(market.get("adjust", 0)) + float(rule.get("risk_adjust", 0))
    m["note"] = market.get("note", "") + " 订单类型：" + rule.get("note", "")
    return m
# ===== END SAFE PATCH =====


# ===== SAFE AREA + RUSH PATCH V5.4 =====
def rush_hour_ai():
    try:
        now = datetime.now()
        hour = now.hour + now.minute / 60
        weekday = now.weekday()
        if weekday >= 5:
            return {"name": "周末/非典型通勤高峰", "adjust": 0, "message": "当前不是典型工作日通勤高峰。"}
        if 7 <= hour <= 9.5:
            return {"name": "早高峰", "adjust": -7, "message": "当前接近早高峰，实际行程时间可能被低估。"}
        if 16 <= hour <= 19:
            return {"name": "晚高峰", "adjust": -10, "message": "当前是晚高峰，堵车严重，实际时薪可能下降。"}
        if 11 <= hour <= 14:
            return {"name": "午间稳定期", "adjust": 2, "message": "当前接近午间稳定期，路况相对可控。"}
        if 21 <= hour or hour <= 5:
            return {"name": "夜间低堵车", "adjust": 3, "message": "当前夜间堵车较少，但要注意安全和回程单。"}
        return {"name": "普通时段", "adjust": 0, "message": "当前不是明显高峰期。"}
    except Exception:
        return {"name": "普通时段", "adjust": 0, "message": "高峰AI暂不可用。"}

def gps_area_ai(lat, lon):
    try:
        lat = float(lat)
        lon = float(lon)
    except Exception:
        return None

    zones = [
        {"name": "Downtown LA", "lat_min": 34.030, "lat_max": 34.065, "lon_min": -118.270, "lon_max": -118.230, "adjust": -8, "risk": "高堵车风险", "message": "接近 Downtown LA，红灯和堵车严重。"},
        {"name": "LAX", "lat_min": 33.930, "lat_max": 33.960, "lon_min": -118.430, "lon_max": -118.380, "adjust": -12, "risk": "机场等待风险", "message": "接近 LAX，注意机场等待和回程空驶。"},
        {"name": "Santa Monica", "lat_min": 34.000, "lat_max": 34.040, "lon_min": -118.520, "lon_max": -118.460, "adjust": -6, "risk": "海边高峰堵车", "message": "接近 Santa Monica，高峰期容易堵车。"},
        {"name": "Pasadena", "lat_min": 34.130, "lat_max": 34.180, "lon_min": -118.180, "lon_max": -118.100, "adjust": 6, "risk": "优质区域", "message": "接近 Pasadena，短单稳定。"},
        {"name": "Alhambra / Monterey Park", "lat_min": 34.050, "lat_max": 34.110, "lon_min": -118.170, "lon_max": -118.080, "adjust": 5, "risk": "华人区加成", "message": "接近 Alhambra / Monterey Park，短单密度较好。"},
        {"name": "Arcadia", "lat_min": 34.110, "lat_max": 34.160, "lon_min": -118.070, "lon_max": -117.990, "adjust": 5, "risk": "华人区加成", "message": "接近 Arcadia，区域相对稳定。"},
        {"name": "Irvine", "lat_min": 33.620, "lat_max": 33.760, "lon_min": -117.900, "lon_max": -117.700, "adjust": 4, "risk": "优质长单区域", "message": "接近 Irvine，路况较稳定，但从LA过去要考虑回程。"},
        {"name": "Hollywood", "lat_min": 34.080, "lat_max": 34.120, "lon_min": -118.370, "lon_max": -118.290, "adjust": -5, "risk": "游客区堵车", "message": "接近 Hollywood，游客区堵车和上下客风险较高。"},
        {"name": "Beverly Hills", "lat_min": 34.060, "lat_max": 34.100, "lon_min": -118.430, "lon_max": -118.370, "adjust": 4, "risk": "高价值区域", "message": "接近 Beverly Hills，高价值订单概率较好。"},
    ]

    for z in zones:
        if z["lat_min"] <= lat <= z["lat_max"] and z["lon_min"] <= lon <= z["lon_max"]:
            return z
    return None

def analyze_area_ai(ocr_text, base_score, lat=None, lon=None):
    text = (ocr_text or "").lower()
    score = base_score
    notices = []

    gps_zone = gps_area_ai(lat, lon)
    if gps_zone:
        score += gps_zone.get("adjust", 0)
        notices.append(gps_zone.get("message", ""))
        area = gps_zone.get("name", "GPS区域")
        risk = gps_zone.get("risk", "普通")
        source = "GPS定位"
    else:
        area = "未识别区域"
        risk = "普通"
        source = "未定位"

    # OCR文字补充识别
    keyword_rules = [
        ("Downtown LA", ["downtown", "dtla"], -8, "高堵车风险", "截图文字检测到 Downtown LA。"),
        ("LAX", ["lax", "airport"], -12, "机场等待风险", "截图文字检测到 LAX/airport。"),
        ("Santa Monica", ["santa monica"], -6, "海边高峰堵车", "截图文字检测到 Santa Monica。"),
        ("Pasadena", ["pasadena"], 6, "优质区域", "截图文字检测到 Pasadena。"),
        ("Alhambra / Monterey Park", ["alhambra", "monterey park", "arcadia"], 5, "华人区加成", "截图文字检测到华人区。"),
        ("Irvine", ["irvine"], 4, "优质长单区域", "截图文字检测到 Irvine。"),
        ("Hollywood", ["hollywood"], -5, "游客区堵车", "截图文字检测到 Hollywood。"),
        ("Beverly Hills", ["beverly hills"], 4, "高价值区域", "截图文字检测到 Beverly Hills。"),
    ]
    for name, kws, adj, rsk, msg in keyword_rules:
        if any(k in text for k in kws):
            score += adj
            if area == "未识别区域":
                area, risk, source = name, rsk, "截图文字"
            notices.append(msg)
            break

    rush = rush_hour_ai()
    score += rush.get("adjust", 0)
    notices.append(rush.get("message", ""))

    high = area in ["Downtown LA", "LAX", "Santa Monica"]
    if rush.get("name") in ["早高峰", "晚高峰"] and high:
        score -= 5
        notices.append("高峰期叠加高风险区域，额外降低评分。")

    score = max(0, min(100, int(score)))
    return {
        "score": score,
        "area": area,
        "risk": risk,
        "rush": rush.get("name", "普通时段"),
        "source": source,
        "message": " ".join([n for n in notices if n]),
    }
# ===== END SAFE AREA PATCH =====



# ===== NATIONWIDE REGION AI DATABASE V6.0 =====
US_REGION_ZONES = [
    # Los Angeles / Southern California
    {"name":"Arcadia / Temple City / San Gabriel","lat_min":34.080,"lat_max":34.160,"lon_min":-118.090,"lon_max":-117.960,"adjust":7,"risk":"华人区优质短单","message":"GPS显示在 Arcadia / Temple City / San Gabriel 附近，短单密度较好，夜间相对稳定，适合连续短单。"},
    {"name":"Rosemead / El Monte","lat_min":34.030,"lat_max":34.110,"lon_min":-118.120,"lon_max":-117.900,"adjust":5,"risk":"华人区稳定单","message":"GPS显示在 Rosemead / El Monte 附近，短单和回单概率较好，但要注意部分路段红灯较多。"},
    {"name":"Monterey Park / Alhambra","lat_min":34.040,"lat_max":34.110,"lon_min":-118.170,"lon_max":-118.080,"adjust":6,"risk":"华人区加成","message":"GPS显示在 Monterey Park / Alhambra 附近，短单密度较高，适合做连续订单。"},
    {"name":"Pasadena","lat_min":34.120,"lat_max":34.190,"lon_min":-118.210,"lon_max":-118.060,"adjust":6,"risk":"优质区域","message":"GPS显示在 Pasadena 附近，区域稳定、短单质量较好。"},
    {"name":"Glendale / Burbank","lat_min":34.130,"lat_max":34.240,"lon_min":-118.380,"lon_max":-118.230,"adjust":4,"risk":"稳定商务区","message":"GPS显示在 Glendale / Burbank 附近，商务和住宅订单较稳定，机场/影视区方向要看回程。"},
    {"name":"Koreatown / Mid-Wilshire","lat_min":34.040,"lat_max":34.080,"lon_min":-118.330,"lon_max":-118.270,"adjust":-3,"risk":"高密度堵车","message":"GPS显示在 Koreatown / Mid-Wilshire，订单多但堵车、停车、上下客风险较高，重点看时薪。"},
    {"name":"Downtown LA","lat_min":34.030,"lat_max":34.065,"lon_min":-118.270,"lon_max":-118.230,"adjust":-8,"risk":"高堵车风险","message":"GPS显示在 Downtown LA，红灯、堵车、上下客和停车风险高，实际时薪可能下降。"},
    {"name":"Hollywood / West Hollywood","lat_min":34.080,"lat_max":34.120,"lon_min":-118.390,"lon_max":-118.290,"adjust":-5,"risk":"游客区堵车","message":"GPS显示在 Hollywood / West Hollywood，游客区订单多但堵车和上下客风险高。"},
    {"name":"Beverly Hills / West LA","lat_min":34.050,"lat_max":34.110,"lon_min":-118.480,"lon_max":-118.360,"adjust":5,"risk":"高价值区域","message":"GPS显示在 Beverly Hills / West LA，高价值订单概率较好，但停车和高峰堵车要注意。"},
    {"name":"Santa Monica","lat_min":34.000,"lat_max":34.050,"lon_min":-118.530,"lon_max":-118.450,"adjust":-6,"risk":"海边高峰堵车","message":"GPS显示在 Santa Monica，海边和晚高峰堵车明显，实际时薪可能下降。"},
    {"name":"Culver City / Marina Del Rey","lat_min":33.960,"lat_max":34.040,"lon_min":-118.470,"lon_max":-118.360,"adjust":2,"risk":"中高价值混合区","message":"GPS显示在 Culver City / Marina Del Rey，订单质量不错，但高峰期东西向交通慢。"},
    {"name":"LAX / Inglewood","lat_min":33.930,"lat_max":34.020,"lon_min":-118.430,"lon_max":-118.300,"adjust":-10,"risk":"机场等待/回程风险","message":"GPS显示接近 LAX / Inglewood，要考虑机场等待、上下客规则和回程空驶。"},
    {"name":"South Bay / Torrance","lat_min":33.790,"lat_max":33.900,"lon_min":-118.400,"lon_max":-118.250,"adjust":3,"risk":"稳定住宅商务区","message":"GPS显示在 South Bay / Torrance，区域较稳定，长单要看回程。"},
    {"name":"Long Beach","lat_min":33.720,"lat_max":33.850,"lon_min":-118.250,"lon_max":-118.080,"adjust":0,"risk":"港口/城市混合区","message":"GPS显示在 Long Beach，订单多但区域差异大，注意港口和低速路段。"},
    {"name":"Orange County / Anaheim","lat_min":33.780,"lat_max":33.930,"lon_min":-118.070,"lon_max":-117.800,"adjust":3,"risk":"旅游住宅混合区","message":"GPS显示在 Anaheim / Orange County 北部，Disney周边单多但等待和停车要考虑。"},
    {"name":"Irvine / Costa Mesa","lat_min":33.620,"lat_max":33.760,"lon_min":-117.950,"lon_max":-117.700,"adjust":5,"risk":"优质稳定区","message":"GPS显示在 Irvine / Costa Mesa，路况较稳定、订单质量较好，但从LA过去要考虑回程。"},
    {"name":"San Diego","lat_min":32.650,"lat_max":32.930,"lon_min":-117.300,"lon_max":-116.950,"adjust":2,"risk":"稳定城市区","message":"GPS显示在 San Diego，旅游和市区订单较稳定，海边停车和高峰要注意。"},

    # NYC / Northeast
    {"name":"Manhattan","lat_min":40.700,"lat_max":40.880,"lon_min":-74.030,"lon_max":-73.920,"adjust":-10,"risk":"超高堵车/停车风险","message":"GPS显示在 Manhattan，单价高但堵车、停车、上下客风险极高，必须优先看时薪。"},
    {"name":"Brooklyn / Queens","lat_min":40.560,"lat_max":40.790,"lon_min":-74.050,"lon_max":-73.700,"adjust":-5,"risk":"高密度城市区","message":"GPS显示在 Brooklyn / Queens，订单密度高但低速和绕路风险明显。"},
    {"name":"JFK Airport","lat_min":40.620,"lat_max":40.670,"lon_min":-73.830,"lon_max":-73.740,"adjust":-10,"risk":"机场等待风险","message":"GPS显示接近 JFK，注意机场排队、等待和回程方向。"},
    {"name":"LaGuardia Airport","lat_min":40.750,"lat_max":40.790,"lon_min":-73.900,"lon_max":-73.850,"adjust":-8,"risk":"机场等待风险","message":"GPS显示接近 LaGuardia，机场单要考虑等待和堵车。"},
    {"name":"Newark / EWR","lat_min":40.650,"lat_max":40.730,"lon_min":-74.220,"lon_max":-74.120,"adjust":-8,"risk":"跨州机场风险","message":"GPS显示接近 Newark/EWR，注意跨州、收费路和回程单。"},
    {"name":"Boston Core","lat_min":42.300,"lat_max":42.410,"lon_min":-71.150,"lon_max":-70.980,"adjust":-6,"risk":"复杂道路/停车风险","message":"GPS显示在 Boston核心区，道路复杂、停车和堵车会拖低效率。"},
    {"name":"Philadelphia Core","lat_min":39.900,"lat_max":40.050,"lon_min":-75.250,"lon_max":-75.100,"adjust":-3,"risk":"城市道路风险","message":"GPS显示在 Philadelphia核心区，停车和市区低速影响效率。"},
    {"name":"Washington DC Core","lat_min":38.830,"lat_max":39.000,"lon_min":-77.120,"lon_max":-76.900,"adjust":-5,"risk":"市区限停/堵车风险","message":"GPS显示在 Washington DC核心区，限停、堵车和绕路风险较高。"},

    # Bay Area / West
    {"name":"San Francisco Core","lat_min":37.700,"lat_max":37.820,"lon_min":-122.520,"lon_max":-122.350,"adjust":-8,"risk":"高成本高堵车","message":"GPS显示在 San Francisco核心区，单价高但停车、坡路、堵车和乘客等待风险也高。"},
    {"name":"SFO Airport","lat_min":37.600,"lat_max":37.640,"lon_min":-122.410,"lon_max":-122.360,"adjust":-8,"risk":"机场等待风险","message":"GPS显示接近 SFO，注意机场等待和回程方向。"},
    {"name":"San Jose / Silicon Valley","lat_min":37.200,"lat_max":37.450,"lon_min":-122.100,"lon_max":-121.750,"adjust":3,"risk":"科技商务区","message":"GPS显示在 San Jose / Silicon Valley，商务单较好，长距离要看回程。"},
    {"name":"Seattle Core","lat_min":47.550,"lat_max":47.720,"lon_min":-122.420,"lon_max":-122.250,"adjust":-4,"risk":"高成本堵车","message":"GPS显示在 Seattle核心区，堵车和高成本会压低真实利润。"},
    {"name":"Las Vegas Strip","lat_min":36.090,"lat_max":36.150,"lon_min":-115.190,"lon_max":-115.140,"adjust":-4,"risk":"旅游区上下客风险","message":"GPS显示在 Las Vegas Strip，短单多但堵车、上下客点和游客等待要注意。"},
    {"name":"Phoenix Metro","lat_min":33.300,"lat_max":33.650,"lon_min":-112.250,"lon_max":-111.850,"adjust":2,"risk":"低密度长距离","message":"GPS显示在 Phoenix，大范围城市容易长距离，注意油耗和空驶。"},
    {"name":"Denver Core","lat_min":39.650,"lat_max":39.850,"lon_min":-105.100,"lon_max":-104.850,"adjust":1,"risk":"普通稳定市场","message":"GPS显示在 Denver，市区和机场方向要看回程与天气。"},

    # Texas / South
    {"name":"Dallas Core","lat_min":32.700,"lat_max":32.900,"lon_min":-97.000,"lon_max":-96.650,"adjust":2,"risk":"分散市场","message":"GPS显示在 Dallas核心区，单价可接受但城市分散，注意回程。"},
    {"name":"DFW Airport","lat_min":32.850,"lat_max":32.930,"lon_min":-97.100,"lon_max":-96.950,"adjust":-6,"risk":"机场等待风险","message":"GPS显示接近 DFW机场，要考虑机场等待和回程。"},
    {"name":"Houston Core","lat_min":29.650,"lat_max":29.900,"lon_min":-95.500,"lon_max":-95.250,"adjust":1,"risk":"大城市长距离","message":"GPS显示在 Houston，城市大，长途低单价容易拖低收益。"},
    {"name":"Austin Core","lat_min":30.200,"lat_max":30.380,"lon_min":-97.850,"lon_max":-97.650,"adjust":2,"risk":"活动/科技城市","message":"GPS显示在 Austin，活动区订单波动大，夜间和市中心停车要注意。"},
    {"name":"San Antonio Core","lat_min":29.350,"lat_max":29.550,"lon_min":-98.600,"lon_max":-98.400,"adjust":1,"risk":"普通稳定市场","message":"GPS显示在 San Antonio，旅游区和市区要看时间。"},
    {"name":"Miami / South Beach","lat_min":25.700,"lat_max":25.900,"lon_min":-80.250,"lon_max":-80.100,"adjust":-4,"risk":"旅游区停车堵车","message":"GPS显示在 Miami / South Beach，旅游区停车和堵车明显。"},
    {"name":"Orlando / Disney Area","lat_min":28.300,"lat_max":28.550,"lon_min":-81.650,"lon_max":-81.250,"adjust":-2,"risk":"旅游等待风险","message":"GPS显示在 Orlando/Disney区域，游客单多但等待和停车要考虑。"},
    {"name":"Atlanta Core","lat_min":33.650,"lat_max":33.900,"lon_min":-84.520,"lon_max":-84.250,"adjust":-2,"risk":"通勤堵车","message":"GPS显示在 Atlanta核心区，高峰堵车明显，长单要看方向。"},
    {"name":"Charlotte Core","lat_min":35.150,"lat_max":35.300,"lon_min":-80.950,"lon_max":-80.750,"adjust":1,"risk":"普通增长市场","message":"GPS显示在 Charlotte，市场较稳定，机场和市中心要看时薪。"},
    {"name":"Nashville Core","lat_min":36.080,"lat_max":36.230,"lon_min":-86.900,"lon_max":-86.650,"adjust":0,"risk":"旅游娱乐区","message":"GPS显示在 Nashville，娱乐区单多但上下客和停车要注意。"},
    {"name":"New Orleans Core","lat_min":29.880,"lat_max":30.050,"lon_min":-90.150,"lon_max":-89.950,"adjust":-2,"risk":"旅游区道路复杂","message":"GPS显示在 New Orleans，旅游区和老城区道路复杂，注意时薪。"},

    # Midwest
    {"name":"Chicago Core","lat_min":41.780,"lat_max":41.980,"lon_min":-87.760,"lon_max":-87.550,"adjust":-5,"risk":"市区堵车/停车","message":"GPS显示在 Chicago核心区，堵车、停车和天气风险会影响效率。"},
    {"name":"ORD Airport","lat_min":41.960,"lat_max":42.020,"lon_min":-87.950,"lon_max":-87.860,"adjust":-7,"risk":"机场等待风险","message":"GPS显示接近 O'Hare机场，要考虑等待和回程。"},
    {"name":"Detroit Core","lat_min":42.250,"lat_max":42.450,"lon_min":-83.200,"lon_max":-82.950,"adjust":0,"risk":"普通市场","message":"GPS显示在 Detroit，夜间和区域安全要注意。"},
    {"name":"Minneapolis Core","lat_min":44.880,"lat_max":45.050,"lon_min":-93.350,"lon_max":-93.150,"adjust":0,"risk":"天气影响市场","message":"GPS显示在 Minneapolis，冬季天气会影响速度和风险。"},
    {"name":"Kansas City Core","lat_min":38.950,"lat_max":39.180,"lon_min":-94.700,"lon_max":-94.450,"adjust":1,"risk":"普通市场","message":"GPS显示在 Kansas City，城市分散，注意回程。"},
]

def nationwide_region_ai(lat, lon):
    try:
        lat = float(lat)
        lon = float(lon)
    except Exception:
        return None

    for z in US_REGION_ZONES:
        if z["lat_min"] <= lat <= z["lat_max"] and z["lon_min"] <= lon <= z["lon_max"]:
            return z
    return None

# Override area AI with nationwide database
def gps_area_ai(lat, lon):
    return nationwide_region_ai(lat, lon)

def analyze_area_ai(ocr_text, base_score, lat=None, lon=None):
    text = (ocr_text or "").lower()
    score = base_score
    notices = []

    zone = nationwide_region_ai(lat, lon)
    if zone:
        score += zone.get("adjust", 0)
        area = zone.get("name", "GPS区域")
        risk = zone.get("risk", "普通")
        source = "GPS定位"
        notices.append(zone.get("message", ""))
    else:
        area = "未识别区域"
        risk = "普通"
        source = "未定位"

    # OCR文字辅助识别，防止GPS没开时完全没有区域判断
    text_rules = [
        ("LAX", ["lax"], -10, "机场等待风险", "截图文字检测到 LAX。"),
        ("JFK Airport", ["jfk"], -10, "机场等待风险", "截图文字检测到 JFK。"),
        ("SFO Airport", ["sfo"], -8, "机场等待风险", "截图文字检测到 SFO。"),
        ("Downtown LA", ["downtown la", "dtla"], -8, "高堵车风险", "截图文字检测到 Downtown LA。"),
        ("Manhattan", ["manhattan"], -10, "超高堵车/停车风险", "截图文字检测到 Manhattan。"),
        ("Irvine", ["irvine"], 5, "优质稳定区", "截图文字检测到 Irvine。"),
        ("Arcadia / Temple City / San Gabriel", ["arcadia", "temple city", "san gabriel"], 7, "华人区优质短单", "截图文字检测到 Arcadia/Temple City/San Gabriel。"),
        ("Pasadena", ["pasadena"], 6, "优质区域", "截图文字检测到 Pasadena。"),
    ]
    for name, kws, adj, rsk, msg in text_rules:
        if any(k in text for k in kws):
            score += adj
            if area == "未识别区域":
                area, risk, source = name, rsk, "截图文字"
            notices.append(msg)
            break

    rush = rush_hour_ai()
    score += rush.get("adjust", 0)
    notices.append(rush.get("message", ""))

    high_risk_keywords = ["Downtown", "LAX", "JFK", "SFO", "Manhattan", "Santa Monica", "Hollywood", "Airport"]
    if rush.get("name") in ["早高峰", "晚高峰"] and any(k in area for k in high_risk_keywords):
        score -= 5
        notices.append("高峰期叠加高风险区域，额外降低评分。")

    score = max(0, min(100, int(score)))
    return {
        "score": score,
        "area": area,
        "risk": risk,
        "rush": rush.get("name", "普通时段"),
        "source": source,
        "message": " ".join([n for n in notices if n]),
    }
# ===== END NATIONWIDE REGION AI DATABASE =====



# ===== ORDER ROUTE AI V6.1 =====
ROUTE_PLACE_RULES = [
    # LA / SoCal
    ("Downtown LA", ["downtown la", "dtla"], -8, "高堵车起点/终点"),
    ("Los Angeles", ["los angeles", "hillside ave"], -4, "洛杉矶城区"),
    ("Irvine", ["irvine", "pacific coast hwy huntington beach", "huntington beach", "orange county", "costa mesa"], -7, "OC长途/回程风险"),
    ("LAX", ["lax", "los angeles international airport"], -12, "机场等待风险"),
    ("Santa Monica", ["santa monica"], -6, "海边高峰堵车"),
    ("Hollywood", ["hollywood", "west hollywood"], -5, "游客区堵车"),
    ("Pasadena", ["pasadena"], 4, "稳定区域"),
    ("Arcadia / Temple City / San Gabriel", ["arcadia", "temple city", "san gabriel"], 6, "华人区短单加成"),
    ("Monterey Park / Alhambra", ["monterey park", "alhambra"], 5, "华人区短单加成"),
    ("Long Beach", ["long beach"], -2, "港口/长途风险"),
    ("Anaheim", ["anaheim", "disneyland"], -2, "旅游区等待风险"),
    ("Beverly Hills", ["beverly hills"], 4, "高价值区域"),
    ("Burbank / Glendale", ["burbank", "glendale"], 3, "稳定商务住宅区"),

    # Other major US
    ("Manhattan", ["manhattan"], -10, "超高堵车停车"),
    ("JFK Airport", ["jfk"], -10, "机场等待风险"),
    ("SFO Airport", ["sfo"], -8, "机场等待风险"),
    ("San Francisco", ["san francisco"], -7, "高成本堵车"),
    ("Dallas", ["dallas"], 1, "分散市场"),
    ("Houston", ["houston"], 1, "大城市长途风险"),
    ("Chicago", ["chicago"], -4, "市区堵车"),
    ("Miami", ["miami", "south beach"], -3, "旅游区停车堵车"),
    ("Las Vegas Strip", ["las vegas", "strip"], -3, "旅游区上下客风险"),
]

def detect_route_places(ocr_text):
    text = (ocr_text or "").lower()
    found = []
    for name, keywords, adjust, risk in ROUTE_PLACE_RULES:
        for kw in keywords:
            if kw in text:
                found.append({"name": name, "adjust": adjust, "risk": risk, "keyword": kw})
                break

    # 去重，保持顺序
    unique = []
    seen = set()
    for x in found:
        if x["name"] not in seen:
            unique.append(x)
            seen.add(x["name"])
    return unique

def analyze_route_ai(ocr_text, miles, minutes, gross_per_mile, net_per_hour):
    places = detect_route_places(ocr_text)
    score_adjust = 0
    warnings = []
    route_name = "未识别路线"

    if places:
        route_name = " → ".join([p["name"] for p in places[:3]])
        for p in places:
            score_adjust += p["adjust"]
            warnings.append(f"{p['name']}：{p['risk']}。")

    # 长途路线风险
    if miles >= 40:
        score_adjust -= 18
        warnings.append("长途订单：40英里以上，必须考虑空车回程和真实时薪。")
    elif miles >= 25:
        score_adjust -= 10
        warnings.append("中长途订单：注意回程单概率。")

    # 耗时风险
    if minutes >= 120:
        score_adjust -= 15
        warnings.append("耗时超过2小时，表面收入容易误导，实际时薪风险高。")
    elif minutes >= 75:
        score_adjust -= 8
        warnings.append("耗时较长，注意堵车和等待。")

    # 低单价长途组合
    if miles >= 25 and gross_per_mile < 1.5:
        score_adjust -= 15
        warnings.append("长途低每英里收入组合，通常不适合接。")

    # 低时薪组合
    if minutes and net_per_hour < 20:
        score_adjust -= 12
        warnings.append("预计每小时净利低于$20，时薪偏低。")

    # LA -> Irvine / OC 特殊路线
    text = (ocr_text or "").lower()
    la_hit = any(k in text for k in ["los angeles", "hillside ave", "downtown la", "dtla"])
    oc_hit = any(k in text for k in ["irvine", "huntington beach", "orange county", "costa mesa", "pacific coast hwy"])
    if la_hit and oc_hit:
        route_name = "Los Angeles → Orange County / Irvine"
        score_adjust -= 12
        warnings.append("LA → OC/Irvine 长途路线：回程空驶概率高，高峰期实际时薪容易下降。")

    # 优质短单补偿
    if miles <= 3 and gross_per_mile >= 3 and net_per_hour >= 25:
        score_adjust += 10
        warnings.append("短距离高单价，属于优质短单。")

    return {
        "route": route_name,
        "adjust": score_adjust,
        "message": " ".join(warnings) if warnings else "未识别到明显路线风险。",
        "source": "订单截图路线文字"
    }
# ===== END ORDER ROUTE AI V6.1 =====



# ===== ADDRESS ROUTE AI V6.3 =====
def extract_order_addresses(ocr_text):
    """
    从OCR文字里抓订单底部地址：
    重点不是看地图猜，而是读取类似：
    Hillside Ave, Los Angeles, CA, US
    Pacific Coast Hwy, Huntington Beach, CA, US
    """
    text = ocr_text or ""
    lines = [ln.strip() for ln in text.splitlines() if ln.strip()]

    address_lines = []
    street_keywords = [
        " ave", " avenue", " st", " street", " blvd", " boulevard", " rd", " road",
        " dr", " drive", " hwy", " highway", " pkwy", " parkway", " way", " ln", " lane",
        " ct", " court", " cir", " circle", " pl", " place"
    ]
    city_keywords = [
        "los angeles", "huntington beach", "irvine", "long beach", "santa monica",
        "pasadena", "arcadia", "temple city", "san gabriel", "alhambra", "monterey park",
        "rosemead", "el monte", "burbank", "glendale", "hollywood", "beverly hills",
        "torrance", "anaheim", "orange", "costa mesa", "new york", "manhattan",
        "brooklyn", "queens", "san francisco", "chicago", "dallas", "houston",
        "miami", "las vegas", "phoenix", "seattle", "boston", "washington", "atlanta"
    ]

    for line in lines:
        low = line.lower()
        has_street = any(k in low for k in street_keywords)
        has_city = any(k in low for k in city_keywords)
        has_state = (", ca" in low or ", ny" in low or ", tx" in low or ", fl" in low or
                     ", il" in low or ", nv" in low or ", az" in low or ", wa" in low or
                     ", ma" in low or ", dc" in low or ", ga" in low or ", us" in low)
        # 排除明显不是地址的行
        bad = any(x in low for x in ["us$", "$", "行程时间", "行程距离", "miles", "分钟", "小时", "已赚取", "积分"])
        if not bad and (has_street or (has_city and has_state)):
            address_lines.append(line)

    # 常见情况：最后两条地址就是起点和终点
    pickup = address_lines[0] if len(address_lines) >= 1 else ""
    dropoff = address_lines[1] if len(address_lines) >= 2 else ""

    return {
        "pickup": pickup,
        "dropoff": dropoff,
        "addresses": address_lines
    }

def city_from_address(addr):
    low = (addr or "").lower()
    city_map = [
        ("Los Angeles", ["los angeles"]),
        ("Huntington Beach", ["huntington beach"]),
        ("Irvine", ["irvine"]),
        ("Long Beach", ["long beach"]),
        ("Santa Monica", ["santa monica"]),
        ("Pasadena", ["pasadena"]),
        ("Arcadia", ["arcadia"]),
        ("Temple City", ["temple city"]),
        ("San Gabriel", ["san gabriel"]),
        ("Alhambra", ["alhambra"]),
        ("Monterey Park", ["monterey park"]),
        ("Rosemead", ["rosemead"]),
        ("El Monte", ["el monte"]),
        ("Burbank", ["burbank"]),
        ("Glendale", ["glendale"]),
        ("Hollywood", ["hollywood"]),
        ("Beverly Hills", ["beverly hills"]),
        ("Torrance", ["torrance"]),
        ("Anaheim", ["anaheim"]),
        ("Costa Mesa", ["costa mesa"]),
        ("Orange County", ["orange county", "orange, ca"]),
        ("New York", ["new york"]),
        ("Manhattan", ["manhattan"]),
        ("Brooklyn", ["brooklyn"]),
        ("Queens", ["queens"]),
        ("San Francisco", ["san francisco"]),
        ("Chicago", ["chicago"]),
        ("Dallas", ["dallas"]),
        ("Houston", ["houston"]),
        ("Miami", ["miami"]),
        ("Las Vegas", ["las vegas"]),
        ("Phoenix", ["phoenix"]),
        ("Seattle", ["seattle"]),
        ("Boston", ["boston"]),
        ("Washington DC", ["washington", "dc"]),
        ("Atlanta", ["atlanta"]),
    ]
    for city, keys in city_map:
        if any(k in low for k in keys):
            return city
    return ""

def analyze_address_route_ai(ocr_text, miles, minutes, gross_per_mile, net_per_hour):
    data = extract_order_addresses(ocr_text)
    pickup = data["pickup"]
    dropoff = data["dropoff"]
    pickup_city = city_from_address(pickup)
    dropoff_city = city_from_address(dropoff)

    adjust = 0
    messages = []
    route_name = "未识别地址路线"

    if pickup or dropoff:
        route_name = f"{pickup_city or pickup or '起点未知'} → {dropoff_city or dropoff or '终点未知'}"
        messages.append(f"起点：{pickup or '未识别'}。终点：{dropoff or '未识别'}。")

    # LA / OC 路线：Huntington Beach / Irvine / Costa Mesa / Anaheim 属于OC方向
    pickup_low = pickup.lower()
    drop_low = dropoff.lower()
    text_low = (ocr_text or "").lower()

    la_start = ("los angeles" in pickup_low) or ("los angeles" in text_low and not pickup_city)
    oc_end = any(x in drop_low for x in ["huntington beach", "irvine", "costa mesa", "anaheim", "orange county"]) or \
             any(x in text_low for x in ["huntington beach", "irvine", "costa mesa", "anaheim", "orange county", "pacific coast hwy"])

    if la_start and oc_end:
        route_name = f"{pickup_city or 'Los Angeles'} → {dropoff_city or 'Orange County'}"
        adjust -= 18
        messages.append("地址路线识别为 LA → Orange County 方向，长途回程空驶风险高。")

    # 终点 Huntington Beach / PCH 海边区域
    if "huntington beach" in drop_low or "pacific coast hwy" in drop_low:
        adjust -= 6
        messages.append("终点接近 Huntington Beach / PCH 海边区域，晚高峰和回LA回程风险较高。")

    # 长途/耗时/低时薪
    if miles >= 40:
        adjust -= 18
        messages.append("地址路线为长途单：40英里以上，空车回程风险明显。")
    elif miles >= 25:
        adjust -= 10
        messages.append("地址路线为中长途单，需要考虑回程单概率。")

    if minutes >= 120:
        adjust -= 15
        messages.append("订单耗时超过2小时，实际时薪风险高。")
    elif minutes >= 75:
        adjust -= 8
        messages.append("订单耗时较长，容易拖低真实时薪。")

    if miles >= 25 and gross_per_mile < 1.5:
        adjust -= 15
        messages.append("长途低每英里收入组合，不适合接。")

    if net_per_hour and net_per_hour < 20:
        adjust -= 12
        messages.append("预计每小时净利低于$20，低于多数城市最低可接受时薪。")

    return {
        "pickup": pickup,
        "dropoff": dropoff,
        "pickup_city": pickup_city,
        "dropoff_city": dropoff_city,
        "route": route_name,
        "adjust": adjust,
        "message": " ".join(messages) if messages else "未从订单底部地址识别到明确路线。",
        "source": "订单底部地址OCR"
    }
# ===== END ADDRESS ROUTE AI V6.3 =====


def analyze(income, miles, minutes, gas_price, mpg, wear_rate, ocr_text="", plan="pro", lat=None, lon=None, order_type="uberx"):
    fuel_cost = miles / mpg * gas_price
    wear_cost = miles * wear_rate
    net_profit = income - fuel_cost - wear_cost
    gross_per_mile = income / miles
    net_per_mile = net_profit / miles
    gross_per_hour = income / minutes * 60 if minutes and minutes > 0 else 0
    net_per_hour = net_profit / minutes * 60 if minutes and minutes > 0 else 0

    try:
        base_market = gps_market_ai(lat, lon)
    except NameError:
        base_market = DEFAULT_US_MARKET
        try:
            lat_f = float(lat)
            lon_f = float(lon)
            for z in US_MARKET_ZONES:
                if z["lat_min"] <= lat_f <= z["lat_max"] and z["lon_min"] <= lon_f <= z["lon_max"]:
                    base_market = z
                    break
        except Exception:
            pass
    try:
        market = apply_order_type_market(base_market, order_type)
    except NameError:
        market = dict(base_market or DEFAULT_US_MARKET)
        market["order_type_name"] = order_type or "UberX"
    level, market_tier = market_level_by_gpm(gross_per_mile, market)
    score = ai_score(gross_per_mile, net_per_hour, net_profit, miles, minutes, market)

    area_ai = analyze_area_ai(ocr_text, score, lat, lon)
    route_ai = analyze_route_ai(ocr_text, miles, minutes or 0, gross_per_mile, net_per_hour)
    address_route_ai = analyze_address_route_ai(ocr_text, miles, minutes or 0, gross_per_mile, net_per_hour)
    score = max(0, min(100, int(area_ai["score"] + route_ai["adjust"] + address_route_ai["adjust"])))

    # 新版 UberX 洛杉矶司机逻辑
    # 全国市场动态判断：每个城市有独立每英里线 + 独立时薪线
    hour_label, hour_tier = hourly_level(net_per_hour, market)

    if market_tier == "trash":
        grade, risk, action, cls = "不建议：低于当地最低可接线", "高风险", "拒绝", "bad"

    elif market_tier == "excellent":
        grade, risk, action, cls = "强烈建议：当地神单", "低风险", "马上接", "good"

    elif market_tier == "good":
        if hour_tier in ["low"]:
            grade, risk, action, cls = "好单但耗时偏长", "中等风险", "看情况", "ok"
        else:
            grade, risk, action, cls = "建议：当地好单", "低风险", "建议接单", "good"

    elif market_tier == "normal":
        if hour_tier in ["good", "excellent", "normal"]:
            grade, risk, action, cls = "普通可接单", "中低风险", "可接", "ok"
        else:
            grade, risk, action, cls = "普通但时薪偏低", "中等风险", "看情况", "ok"

    else:
        if hour_tier in ["good", "excellent"]:
            grade, risk, action, cls = "单价偏低但时薪可接", "中等风险", "可接", "ok"
        else:
            grade, risk, action, cls = "不建议：低于当地普通线", "高风险", "建议拒绝", "bad"

    # 地址路线AI强制修正：长途低时薪/低单价时，覆盖过于乐观的建议
    if miles >= 25 and (net_per_hour < market.get("min_hour", 20) or gross_per_mile < market.get("normal", 1.2)):
        grade, risk, action, cls = "不建议：长途低收益路线", "高风险", "建议拒绝", "bad"
    elif miles >= 40 and net_per_hour < market.get("good_hour", 30):
        grade, risk, action, cls = "不建议：长途回程风险高", "高风险", "建议拒绝", "bad"
    elif address_route_ai.get("adjust", 0) <= -30:
        grade, risk, action, cls = "不建议：地址路线风险高", "高风险", "建议拒绝", "bad"

    warnings = []
    if miles > 25:
        warnings.append("长距离订单，注意空车回程风险。")
    if minutes and minutes > 60:
        warnings.append("耗时较长，注意真实时薪。")
    if minutes == 0:
        warnings.append("未识别行程时间，时薪判断不完整。")
    if miles <= 3 and gross_per_mile >= 3:
        warnings.append("短单高单价，重点看取客等待时间。")
    if net_profit < 5:
        warnings.append("总净利润偏低。")

    advice = f"当前市场：{market['name']}；订单类型：{market['order_type_name']}。每英里收入 {money(gross_per_mile)}，属于 {level}。{hour_label}。净利润 {money(net_profit)}。"
    if minutes:
        advice += f" 预计每小时净利润 {money(net_per_hour)}。"
    pro_reason = f"AI评分 {score}/100；风险：{risk}；动作：{action}。当地市场：{market['name']}；类型：{market['order_type_name']}；最低可接 ${market['min_accept']:.2f}/mi，好单 ${market['good']:.2f}/mi，神单 ${market['excellent']:.2f}/mi；最低时薪 ${market['min_hour']:.0f}/h，好时薪 ${market['good_hour']:.0f}/h。"
    pro_reason += f" 区域AI：{area_ai['area']} · {area_ai['risk']} · {area_ai['source']}。地址路线AI：{address_route_ai['route']} · {address_route_ai['source']}。路线AI：{route_ai['route']} · {route_ai['source']}。高峰AI：{area_ai['rush']}。"
    pro_warning = " ".join(warnings) if warnings else "暂无明显风险。"
    if area_ai["message"]:
        pro_warning += " " + area_ai["message"]
    pro_warning += " " + market.get("note", "")
    pro_warning += " " + address_route_ai.get("message", "")
    pro_warning += " " + route_ai.get("message", "")

    return Analysis(income, miles, minutes or 0, gas_price, mpg, wear_rate, fuel_cost, wear_cost,
                    net_profit, gross_per_mile, net_per_mile, gross_per_hour, net_per_hour,
                    level, grade, advice, cls, score, risk, action, pro_reason, pro_warning, plan, ocr_text)

def save_order(result, image_path="", consent_share=0):
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    cur.execute("""
    INSERT INTO orders (
        created_at, plan, image_path, ocr_text, income, miles, minutes, gas_price, mpg, wear_rate,
        fuel_cost, wear_cost, net_profit, gross_per_mile, net_per_mile, gross_per_hour, net_per_hour,
        score, risk, action, grade, advice, consent_share
    ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
    """, (
        datetime.now().strftime("%Y-%m-%d %H:%M:%S"), result.plan, image_path, result.ocr_text,
        result.income, result.miles, result.minutes, result.gas_price, result.mpg, result.wear_rate,
        result.fuel_cost, result.wear_cost, result.net_profit, result.gross_per_mile,
        result.net_per_mile, result.gross_per_hour, result.net_per_hour, result.score,
        result.risk, result.action, result.grade, result.advice, consent_share
    ))
    order_id = cur.lastrowid
    conn.commit()
    conn.close()
    return order_id

def stats():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()
    cur.execute("""
        SELECT COUNT(*) c,
        COALESCE(SUM(income),0) income,
        COALESCE(SUM(net_profit),0) net,
        COALESCE(AVG(gross_per_hour),0) avg_gph,
        COALESCE(AVG(net_per_hour),0) avg_nph,
        COALESCE(AVG(score),0) avg_score
        FROM orders
    """)
    summary = cur.fetchone()
    today = date.today().strftime("%Y-%m-%d")
    cur.execute("""
        SELECT COUNT(*) c, COALESCE(SUM(income),0) income, COALESCE(SUM(net_profit),0) net,
        COALESCE(AVG(net_per_hour),0) avg_nph
        FROM orders WHERE created_at LIKE ?
    """, (today + "%",))
    today_summary = cur.fetchone()
    cur.execute("SELECT * FROM orders ORDER BY id DESC LIMIT 30")
    rows = cur.fetchall()
    conn.close()
    return summary, today_summary, rows

def parse_batch(text):
    blocks = [b.strip() for b in re.split(r"\n\s*\n|---+", text or "") if b.strip()]
    out = []
    for b in blocks:
        inc, mi, mins = extract_from_text(b)
        if inc and mi:
            out.append((b, inc, mi, mins or 0))
    return out


@app.route("/manifest.json")
def manifest_json():
    return send_from_directory(BASE_DIR / "static", "manifest.json")

@app.route("/sw.js")
def service_worker():
    return send_from_directory(BASE_DIR / "static", "sw.js")

@app.route("/", methods=["GET", "POST"])
def index():
    result = None
    error = None
    order_id = None
    form = {
        "ocr": "", "income": "", "miles": "", "minutes": "",
        "gas_price": "5.5", "mpg": "38", "wear_rate": "0.25",
        "plan": "pro", "order_type": "uberx", "consent_share": "", "lat": "", "lon": ""
    }

    if request.method == "POST":
        form.update(request.form.to_dict())
        try:
            plan = form.get("plan", "free")
            ocr_text = form.get("ocr", "")
            uploaded = False
            image_path_saved = ""

            file = request.files.get("screenshot")
            if file and file.filename:
                uploaded = True
                ext = Path(file.filename).suffix.lower()
                if ext not in [".png", ".jpg", ".jpeg", ".webp", ".bmp"]:
                    raise ValueError("请上传图片文件：png / jpg / jpeg / webp / bmp")
                filename = secure_filename(f"{uuid.uuid4().hex}{ext}")
                image_path = UPLOAD_DIR / filename
                file.save(image_path)
                image_path_saved = str(image_path)
                detected = run_ocr(image_path)
                if detected.startswith("OCR_ERROR"):
                    raise ValueError("截图 OCR 失败：" + detected)
                ocr_text = detected
                form["ocr"] = detected

            auto_income, auto_miles, auto_minutes = extract_from_text(ocr_text) if ocr_text else (None, None, None)

            if uploaded:
                income = auto_income or 0
                miles = auto_miles or 0
                minutes = auto_minutes or 0
                form["income"] = f"{income:.2f}" if income else ""
                form["miles"] = f"{miles:.2f}" if miles else ""
                form["minutes"] = f"{minutes:.0f}" if minutes else ""
            else:
                income = float(form.get("income") or 0)
                miles = float(form.get("miles") or 0)
                minutes = float(form.get("minutes") or 0)
                if income <= 0 and auto_income:
                    income = auto_income; form["income"] = f"{income:.2f}"
                if miles <= 0 and auto_miles:
                    miles = auto_miles; form["miles"] = f"{miles:.2f}"
                if minutes <= 0 and auto_minutes:
                    minutes = auto_minutes; form["minutes"] = f"{minutes:.0f}"

            gas_price = float(form.get("gas_price") or 5.5)
            mpg = float(form.get("mpg") or 38)
            wear_rate = float(form.get("wear_rate") or 0.25)

            if income <= 0 or miles <= 0:
                raise ValueError("没有识别到收入或英里。请手动补上后再次分析。")

            lat = form.get("lat")
            lon = form.get("lon")
            order_type = form.get("order_type", "uberx")
            result = analyze(income, miles, minutes, gas_price, mpg, wear_rate, ocr_text, plan, lat, lon, order_type)
            consent = 1 if form.get("consent_share") == "on" else 0
            order_id = save_order(result, image_path_saved, consent)
        except Exception as e:
            error = str(e)

    summary, today_summary, rows = stats()
    return render_template("index.html", result=result, error=error, form=form, order_id=order_id,
                           summary=summary, today=today_summary, rows=rows, money=money)

@app.route("/batch", methods=["POST"])
def batch():
    gas_price = float(request.form.get("gas_price", "5.5") or 5.5)
    mpg = float(request.form.get("mpg", "38") or 38)
    wear_rate = float(request.form.get("wear_rate", "0.25") or 0.25)
    order_type = request.form.get("order_type", "uberx")

    items = []

    # 1. 支持多张截图上传
    files = request.files.getlist("screenshots")
    for file in files:
        if not file or not file.filename:
            continue
        ext = Path(file.filename).suffix.lower()
        if ext not in [".png", ".jpg", ".jpeg", ".webp", ".bmp"]:
            continue

        filename = secure_filename(f"{uuid.uuid4().hex}{ext}")
        image_path = UPLOAD_DIR / filename
        file.save(image_path)

        ocr_text = run_ocr(image_path)
        if ocr_text.startswith("OCR_ERROR"):
            items.append({
                "error": "OCR失败",
                "ocr_text": ocr_text,
                "analysis": None,
                "filename": file.filename,
            })
            continue

        income, miles, minutes = extract_from_text(ocr_text)
        if income and miles:
            analysis = analyze(income, miles, minutes or 0, gas_price, mpg, wear_rate, ocr_text=ocr_text, plan="pro", order_type=order_type)
            items.append({
                "error": None,
                "ocr_text": ocr_text,
                "analysis": analysis,
                "filename": file.filename,
            })
        else:
            items.append({
                "error": "未识别到收入或英里",
                "ocr_text": ocr_text,
                "analysis": None,
                "filename": file.filename,
            })

    # 2. 兼容文字粘贴对比
    batch_text = request.form.get("batch_text", "")
    if batch_text.strip():
        for block, inc, mi, mins in parse_batch(batch_text):
            analysis = analyze(inc, mi, mins, gas_price, mpg, wear_rate, ocr_text=block, plan="pro", order_type=order_type)
            items.append({
                "error": None,
                "ocr_text": block,
                "analysis": analysis,
                "filename": "粘贴订单",
            })

    # 按 AI 评分排序，无法识别的放最后
    items.sort(key=lambda x: x["analysis"].score if x.get("analysis") else -1, reverse=True)

    return render_template("batch.html", items=items, money=money)

@app.route("/feedback/<int:order_id>", methods=["POST"])
def feedback(order_id):
    fb = request.form.get("feedback", "")
    note = request.form.get("note", "")
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    cur.execute("UPDATE orders SET user_feedback=?, user_note=? WHERE id=?", (fb, note, order_id))
    conn.commit()
    conn.close()
    return redirect(url_for("index"))


@app.route("/app_feedback", methods=["POST"])
def app_feedback():
    feedback_type = request.form.get("feedback_type", "")
    rating = request.form.get("rating", "")
    message = request.form.get("message", "")
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    cur.execute(
        "INSERT INTO app_feedback (created_at, feedback_type, rating, message) VALUES (?,?,?,?)",
        (datetime.now().strftime("%Y-%m-%d %H:%M:%S"), feedback_type, rating, message)
    )
    conn.commit()
    conn.close()
    return redirect(url_for("index"))

@app.route("/export")
def export():
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    rows = cur.execute("SELECT * FROM orders ORDER BY id DESC").fetchall()
    headers = [d[0] for d in cur.description]
    conn.close()
    import csv, io
    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(headers)
    writer.writerows(rows)
    return app.response_class(output.getvalue(), mimetype="text/csv",
        headers={"Content-Disposition": "attachment;filename=uber_ai_orders_export.csv"})

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port, debug=False)
