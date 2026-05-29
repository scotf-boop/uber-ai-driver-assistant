
from __future__ import annotations
import os, re, sqlite3, hashlib, secrets, csv, io, math
from datetime import datetime, date
from pathlib import Path
from flask import Flask, render_template, request, redirect, url_for, session, flash, send_from_directory, Response, jsonify
from werkzeug.utils import secure_filename

BASE_DIR = Path(__file__).resolve().parent
UPLOAD_DIR = BASE_DIR / "uploads"
DATA_DIR = BASE_DIR / "data"
STATIC_DIR = BASE_DIR / "static"
DB_PATH = DATA_DIR / "uber_ai.db"

for d in (UPLOAD_DIR, DATA_DIR, STATIC_DIR):
    d.mkdir(exist_ok=True)

app = Flask(__name__)
app.secret_key = os.environ.get("SECRET_KEY", "dev-secret-" + secrets.token_hex(16))
app.config["MAX_CONTENT_LENGTH"] = 16 * 1024 * 1024

GOOGLE_MAPS_API_KEY = os.environ.get("GOOGLE_MAPS_API_KEY", "").strip()

def db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn

def init_db():
    with db() as conn:
        conn.executescript("""
        CREATE TABLE IF NOT EXISTS users(
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            email TEXT UNIQUE NOT NULL,
            password_hash TEXT NOT NULL,
            plan TEXT NOT NULL DEFAULT 'free',
            created_at TEXT NOT NULL,
            last_login TEXT
        );
        CREATE TABLE IF NOT EXISTS orders(
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER,
            created_at TEXT NOT NULL,
            order_type TEXT,
            income REAL,
            miles REAL,
            minutes REAL,
            gross_per_mile REAL,
            net_profit REAL,
            net_per_hour REAL,
            score INTEGER,
            grade TEXT,
            action TEXT,
            risk TEXT,
            pickup_address TEXT,
            dropoff_address TEXT,
            route_name TEXT,
            ocr_text TEXT,
            screenshot_file TEXT,
            accepted INTEGER,
            accurate INTEGER,
            feedback TEXT,
            user_feedback TEXT,
            lat TEXT,
            lon TEXT
        );
        CREATE TABLE IF NOT EXISTS feedback(
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER,
            order_id INTEGER,
            created_at TEXT NOT NULL,
            type TEXT,
            content TEXT
        );
        CREATE TABLE IF NOT EXISTS app_feedback(
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            created_at TEXT NOT NULL,
            feedback_type TEXT,
            rating TEXT,
            impact TEXT,
            message TEXT
        );
        """)

def money(x):
    try:
        return f"${float(x):,.2f}"
    except Exception:
        return "$0.00"

def hash_password(password, salt=None):
    salt = salt or secrets.token_hex(16)
    h = hashlib.pbkdf2_hmac("sha256", password.encode(), salt.encode(), 120000)
    return salt + ":" + h.hex()

def verify_password(password, stored):
    try:
        salt, h = stored.split(":", 1)
        return hash_password(password, salt).split(":", 1)[1] == h
    except Exception:
        return False

def current_user():
    uid = session.get("user_id")
    if not uid:
        return None
    with db() as conn:
        return conn.execute("SELECT * FROM users WHERE id=?", (uid,)).fetchone()

def nums(text):
    return [float(x) for x in re.findall(r"[0-9]+(?:\.[0-9]+)?", text or "")]

def normalize_text(text):
    text = text or ""
    repl = {
        "＄":"$","﹩":"$","US $":"$","USD":"$","usd":"$",
        "Miles":"miles","MILES":"miles","Mile":"mile","MI":"mi",
        "英哩":"英里","哩":"英里","，":".","：":":","—":"-","–":"-",
        "O.":"0.","o.":"0.","$ ":"$"
    }
    for a,b in repl.items():
        text = text.replace(a,b)
    return text

def extract_income(text):
    """
    V12.1 修复：
    1. 优先识别带 $ / US$ 的金额，例如 US$6.30。
    2. 不再把地址门牌号、年份、订单编号当收入。
    3. Uber截图常见金额范围优先取 2~250 美元。
    """
    text = normalize_text(text)
    dollar_vals = []
    for m in re.finditer(r'(?:US\s*)?\$\s*([0-9]{1,3}(?:\.[0-9]{1,2})?)', text, re.I):
        try:
            v = float(m.group(1))
            if 2 <= v <= 250:
                dollar_vals.append(v)
        except Exception:
            pass
    if dollar_vals:
        return max(dollar_vals)

    vals = []
    for line in text.splitlines():
        low = line.lower()
        if any(k in low for k in ["fare","earn","earning","pay","total","trip fare","offer","offr","收入","金额"]):
            for n in nums(line):
                if 2 <= n <= 250:
                    vals.append(n)
    return max(vals) if vals else None

def extract_miles(text):
    """
    V12.3 Uber区域识别：
    支持：行程距离 3.21 英里 / 3.21英里 / 3.21 mi。
    """
    text = normalize_text(text)
    vals = []

    for m in re.finditer(r'(?:行程距离|距离|里程)?\s*([0-9]{1,3}(?:\.[0-9]+)?)\s*英里', text):
        try:
            v = float(m.group(1))
            if 0.1 <= v <= 300:
                vals.append(v)
        except Exception:
            pass
    if vals:
        return vals[-1]

    text_for_miles = re.sub(r'([0-9])\s+([0-9])\s*(mi|mile|miles)\b', r'\1.\2 \3', text, flags=re.I).replace(",", ".")
    for m in re.finditer(r'([0-9]{1,3}(?:\.[0-9]+)?)\s*(?:mi|mile|miles)\b', text_for_miles, re.I):
        try:
            v = float(m.group(1))
            if 0.1 <= v <= 300:
                vals.append(v)
        except Exception:
            pass
    if vals:
        return vals[-1]

    for line in text.splitlines():
        low = line.lower()
        if any(k in low for k in ["mile"," mi","distance","trip distance"]) or any(k in line for k in ["英里","距离","里程","行程距离"]):
            for n in nums(line):
                if 0.1 <= n <= 300:
                    vals.append(n)
    if vals:
        return vals[-1]
    return None

def extract_minutes(text):
    """
    V12.6：优先支持中文长时间：
    2小时28分钟 => 148 分钟
    """
    text = normalize_text(text)

    m = re.search(r'([0-9]{1,2})\s*小时\s*([0-9]{1,2})\s*分钟', text)
    if m:
        total = int(m.group(1)) * 60 + int(m.group(2))
        if 1 <= total <= 600:
            return total

    compact = re.sub(r'\s+', '', text)
    m = re.search(r'([0-9]{1,2})小时([0-9]{1,2})分钟', compact)
    if m:
        total = int(m.group(1)) * 60 + int(m.group(2))
        if 1 <= total <= 600:
            return total

    m = re.search(r'([0-9]{1,3})\s*分钟\s*([0-9]{1,2})?\s*秒?', text)
    if m:
        v = int(m.group(1))
        if 1 <= v <= 600:
            return v

    m = re.search(r'([0-9]{1,2})\s*(?:hr|hrs|hour|hours)\s*([0-9]{1,2})\s*(?:min|mins|minute|minutes)', text, re.I)
    if m:
        total = int(m.group(1)) * 60 + int(m.group(2))
        if 1 <= total <= 600:
            return total

    candidates = []
    for m in re.finditer(r'([0-9]{1,3})\s*(?:min|mins|minute|minutes|mn|m)\b', text, re.I):
        v = int(m.group(1))
        if 1 <= v <= 600:
            candidates.append(v)

    if candidates:
        return max(candidates)

    vals = []
    for line in text.splitlines():
        low = line.lower()
        if any(k in low for k in ["delay","wait","延误","增加","等待"]):
            continue
        if any(k in low for k in ["trip time","estimated time","duration","time"]) or any(k in line for k in ["行程时间","预计时间","时间"]):
            for n in nums(line):
                if 1 <= n <= 600:
                    vals.append(int(n))
    return max(vals) if vals else None

STREET_WORDS = [" ave"," avenue"," st"," street"," blvd"," boulevard"," rd"," road"," dr"," drive"," hwy"," highway"," way"," lane"," pkwy"," freeway"," fwy"]

CITY_COORDS = {
    "arcadia": (34.139729, -118.035344),
    "temple city": (34.107230, -118.057846),
    "san gabriel": (34.096111, -118.105833),
    "alhambra": (34.095287, -118.127014),
    "monterey park": (34.062511, -118.122849),
    "los angeles": (34.052235, -118.243683),
    "la": (34.052235, -118.243683),
    "lax": (33.941589, -118.408530),
    "irvine": (33.684567, -117.826505),
    "orange county": (33.717470, -117.831143),
    "huntington beach": (33.659484, -117.998802),
    "las vegas": (36.169941, -115.139832),
    "pasadena": (34.147785, -118.144516),
    "santa monica": (34.019454, -118.491191),
    "hollywood": (34.092809, -118.328661),
}

def extract_addresses(text):
    found=[]
    for line in (text or "").splitlines():
        raw=line.strip()
        low=raw.lower()
        if not low or any(x in low for x in ["us$","$","miles","英里","分钟","小时","积分"," min","minute"]):
            continue
        if any(w in low for w in STREET_WORDS) or any(c in low for c in CITY_COORDS.keys()):
            found.append(raw)
    return found[0] if len(found)>0 else "", found[1] if len(found)>1 else ""

def find_city_hits(text):
    low=(text or "").lower()
    hits=[]
    for c in CITY_COORDS:
        if c in low:
            hits.append(c)
    return hits

def haversine_miles(a, b):
    lat1, lon1 = a
    lat2, lon2 = b
    R = 3958.8
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp = math.radians(lat2-lat1)
    dl = math.radians(lon2-lon1)
    x = math.sin(dp/2)**2 + math.cos(p1)*math.cos(p2)*math.sin(dl/2)**2
    return 2*R*math.atan2(math.sqrt(x), math.sqrt(1-x))

def infer_route_from_text(text, current_lat=None, current_lon=None):
    hits = find_city_hits(text)
    pickup, dropoff = extract_addresses(text)

    if len(hits) >= 2:
        start, end = hits[0], hits[-1]
        dist = haversine_miles(CITY_COORDS[start], CITY_COORDS[end])
        return {
            "start": start.title(),
            "end": end.title(),
            "route": f"{start.title()} → {end.title()}",
            "estimated_distance": dist,
            "source": "订单文字城市识别"
        }

    if len(hits) == 1:
        end = hits[0]
        if current_lat and current_lon:
            try:
                cur = (float(current_lat), float(current_lon))
                dist = haversine_miles(cur, CITY_COORDS[end])
                return {
                    "start": "GPS当前位置",
                    "end": end.title(),
                    "route": f"GPS当前位置 → {end.title()}",
                    "estimated_distance": dist,
                    "source": "GPS + 订单城市识别"
                }
            except Exception:
                pass
        return {
            "start": pickup or "起点未知",
            "end": end.title(),
            "route": f"{pickup or '起点未知'} → {end.title()}",
            "estimated_distance": None,
            "source": "订单文字城市识别"
        }

    return {
        "start": pickup or "起点未知",
        "end": dropoff or "终点未知",
        "route": f"{pickup or '起点未知'} → {dropoff or '终点未知'}",
        "estimated_distance": None,
        "source": "基础地址识别"
    }

def market_by_text(text, lat=None, lon=None):
    low=(text or "").lower()
    try:
        latf = float(lat) if lat else None
        lonf = float(lon) if lon else None
    except Exception:
        latf=lonf=None
    if any(x in low for x in ["los angeles","irvine","huntington beach","arcadia","pasadena","temple city","san gabriel","orange county","lax"]) or (latf and lonf and 32.5 <= latf <= 35.5 and -119.5 <= lonf <= -116.5):
        return dict(name="Los Angeles / SoCal", min_accept=1.0, normal=1.2, good=1.8, excellent=3.0, min_hour=20, good_hour=30, excellent_hour=40)
    if "las vegas" in low:
        return dict(name="Las Vegas", min_accept=.9, normal=1.2, good=1.7, excellent=2.8, min_hour=18, good_hour=28, excellent_hour=38)
    if any(x in low for x in ["new york","manhattan","brooklyn","queens"]):
        return dict(name="New York City", min_accept=1.3, normal=1.8, good=2.5, excellent=3.6, min_hour=25, good_hour=35, excellent_hour=45)
    return dict(name="US Default Market", min_accept=.9, normal=1.2, good=1.8, excellent=2.8, min_hour=20, good_hour=30, excellent_hour=40)

ORDER_TYPES = {
    "uberx":("UberX",1,1), "comfort":("Comfort",1.15,1.1), "uberxl":("UberXL",1.35,1.2),
    "black":("Uber Black",1.8,1.5), "airport":("Airport",1.2,1.15), "reservation":("Reservation",1.25,1.2),
    "shared":("Uber Share",.95,1), "eats":("Uber Eats",.85,.9), "spark":("Spark",1.05,1),
    "doordash":("DoorDash",.95,.9), "lyft":("Lyft",1,1),
}

def apply_order_type(m, order_type):
    name, mm, hm = ORDER_TYPES.get(order_type, ORDER_TYPES["uberx"])
    m = dict(m); m["order_type_name"] = name
    for k in ["min_accept","normal","good","excellent"]: m[k] *= mm
    for k in ["min_hour","good_hour","excellent_hour"]: m[k] *= hm
    return m

def uberx_level(gpm):
    if gpm < 1.0: return "垃圾单"
    if 1.2 <= gpm <= 1.6: return "普通单"
    if 1.8 <= gpm <= 2.5: return "好单"
    if gpm >= 3.0: return "极好单 / 神单"
    if gpm < 1.2: return "偏低单"
    if gpm < 1.8: return "普通偏上"
    return "好单偏上"

def route_risk_ai(text, route_info, miles, minutes, gpm, lat=None, lon=None):
    low=(text or "").lower()
    score_adj=0
    warnings=[]
    if any(x in low for x in ["arcadia","temple city","san gabriel","alhambra","monterey park"]):
        score_adj += 6
        warnings.append("区域AI：华人区优质短单，短单密度较好，适合连续短单。")
    if any(x in low for x in ["lax","airport"]):
        score_adj -= 8
        warnings.append("LAX/机场AI：注意排队、等待、停车和回程空驶。")
    if any(x in low for x in ["downtown","dtla","hollywood","santa monica"]):
        score_adj -= 5
        warnings.append("城区AI：游客区/市中心停车和堵车风险偏高。")
    if any(x in low for x in ["irvine","orange county","huntington beach","pch"]):
        score_adj -= 10
        warnings.append("路线AI：检测到 OC / Irvine / Huntington Beach / PCH，回 LA 空驶风险偏高。")
    if "los angeles" in low and any(x in low for x in ["irvine","orange county","huntington beach"]):
        score_adj -= 18
        warnings.append("地址路线AI：LA → Orange County 方向，长途回程风险高。")
    est = route_info.get("estimated_distance")
    if est and est >= 35:
        score_adj -= 12
        warnings.append(f"路线AI：估算跨区距离约 {est:.1f} 英里，回程风险高。")
    if miles >= 40:
        score_adj -= 18; warnings.append("长途订单：40英里以上，必须考虑空车回程和真实时薪。")
    elif miles >= 25:
        score_adj -= 10; warnings.append("中长途订单：注意回程单概率。")
    if minutes >= 120:
        score_adj -= 15; warnings.append("耗时超过2小时，真实时薪风险高。")
    elif minutes >= 75:
        score_adj -= 7; warnings.append("订单耗时较长，容易拖低真实时薪。")
    if miles >= 25 and gpm < 1.5:
        score_adj -= 18; warnings.append("长途低每英里收入组合，通常不适合接。")
    if not warnings:
        warnings.append("路线AI：暂无明显路线风险。")
    return score_adj, " ".join(warnings)


def fix_uber_time_ocr_number(raw_minutes, miles=None, text=""):
    try:
        raw_float = float(raw_minutes)
        n = int(raw_float)
    except Exception:
        return raw_minutes
    if 0 < raw_float <= 60 and abs(raw_float - n) > 0.001:
        return round(raw_float, 2)
    try:
        m = float(miles or 0)
    except Exception:
        m = 0

    if 0 < n <= 60:
        return n

    if 100 <= n <= 999:
        if m > 0 and m <= 2:
            min_part = n // 100
            sec_part = n % 100
            if 1 <= min_part <= 60 and 0 <= sec_part <= 59:
                return min_part
            if str(n).startswith("1"):
                return 1
        return n

    if 1000 <= n <= 9999:
        min_part = n // 100
        sec_part = n % 100
        if 1 <= min_part <= 120 and 0 <= sec_part <= 59:
            return min_part

    if m > 0 and m <= 2 and n > 60:
        return 0

    return n

def analyze_order(income, miles, minutes, gas_price, mpg, wear_rate, text, order_type, plan="pro", lat=None, lon=None):
    income, miles, minutes = float(income), float(miles), float(minutes or 0)
    minutes = float(fix_uber_time_ocr_number(minutes, miles, text) or 0)
    gas_price, mpg, wear_rate = float(gas_price or 5.5), float(mpg or 38), float(wear_rate or .25)
    fuel = miles / mpg * gas_price if mpg else 0
    wear = miles * wear_rate
    net = income - fuel - wear
    gpm = income / miles if miles else 0
    npm = net / miles if miles else 0
    nph = net / (minutes/60) if minutes else 0
    gross_per_hour = income / (minutes/60) if minutes else 0
    route_info = infer_route_from_text(text, lat, lon)
    m = apply_order_type(market_by_text(text, lat, lon), order_type)

    score=50
    if gpm >= m["excellent"]: score += 35; tier="神单"
    elif gpm >= m["good"]: score += 22; tier="好单"
    elif gpm >= m["normal"]: score += 8; tier="普通单"
    elif gpm >= m["min_accept"]: score -= 8; tier="偏低单"
    else: score -= 25; tier="垃圾单"

    if minutes:
        if nph >= m["excellent_hour"]: score += 25; hour_level="优秀时薪"
        elif nph >= m["good_hour"]: score += 15; hour_level="好时薪"
        elif nph >= m["min_hour"]: score += 5; hour_level="普通时薪"
        else: score -= 18; hour_level="时薪偏低"
    else:
        hour_level="未提供时间"

    route_adj, route_warning = route_risk_ai(text, route_info, miles, minutes, gpm, lat, lon)
    score += route_adj
    score=max(0,min(100,int(score)))

    if score>=80: grade,risk,action,cls="强烈建议：当地神单","低风险","马上接","good"
    elif score>=62: grade,risk,action,cls="建议：当地好单","低风险","建议接单","good"
    elif score>=45: grade,risk,action,cls="普通可接单","中等风险","看情况","ok"
    else: grade,risk,action,cls="不建议：收益或路线风险高","高风险","建议拒绝","bad"
    if miles>=25 and ((nph and nph < m["min_hour"]) or gpm < m["normal"]):
        grade,risk,action,cls="不建议：长途低收益路线","高风险","建议拒绝","bad"

    pro_reason = f"当前市场：{m['name']}；订单类型：{m['order_type_name']}。每英里收入 ${gpm:.2f}，属于 {tier}。净利润 ${net:.2f}；预计每小时净利 ${nph:.2f}。路线：{route_info['route']}。"
    pro_warning = route_warning
    advice = pro_reason + " " + pro_warning
    return dict(
        income=income,miles=miles,minutes=minutes,fuel_cost=fuel,vehicle_cost=wear,net_profit=net,
        gross_per_mile=gpm,net_per_mile=npm,gross_per_hour=gross_per_hour,net_per_hour=nph,
        score=score,grade=grade,risk=risk,action=action,cls=cls,css_class=cls,reason=advice,advice=advice,
        pro_reason=pro_reason,pro_warning=pro_warning,pickup=route_info["start"],dropoff=route_info["end"],route=route_info["route"],
        route_source=route_info["source"],estimated_route_distance=route_info.get("estimated_distance"),
        plan=plan,uberx_level=uberx_level(gpm),hour_level=hour_level,order_type_name=m["order_type_name"],market_name=m["name"],
        google_maps_enabled=bool(GOOGLE_MAPS_API_KEY)
    )

def today_stats(user_id=None):
    today_str=date.today().isoformat()
    q="SELECT * FROM orders WHERE created_at LIKE ?"; args=[today_str+"%"]
    if user_id: q+=" AND user_id=?"; args.append(user_id)
    with db() as conn: rows=conn.execute(q,args).fetchall()
    return dict(income=sum(float(r["income"] or 0) for r in rows), net=sum(float(r["net_profit"] or 0) for r in rows)) if rows else dict(income=0,net=0)

def summary_stats(user_id=None):
    q="SELECT * FROM orders"; args=[]
    if user_id: q+=" WHERE user_id=?"; args.append(user_id)
    with db() as conn: rows=conn.execute(q,args).fetchall()
    return dict(avg_nph=sum(float(r["net_per_hour"] or 0) for r in rows)/len(rows), avg_score=sum(float(r["score"] or 0) for r in rows)/len(rows)) if rows else dict(avg_nph=0,avg_score=0)

def recent_rows(user_id=None):
    q="SELECT * FROM orders"; args=[]
    if user_id: q+=" WHERE user_id=?"; args.append(user_id)
    q+=" ORDER BY id DESC LIMIT 20"
    with db() as conn: return conn.execute(q,args).fetchall()

def save_order(user, form, result, ocr_text, screenshot_file, lat, lon):
    with db() as conn:
        cur=conn.execute("""INSERT INTO orders(user_id,created_at,order_type,income,miles,minutes,gross_per_mile,net_profit,net_per_hour,score,grade,action,risk,pickup_address,dropoff_address,route_name,ocr_text,screenshot_file,lat,lon)
        VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""", (
            user["id"] if user else None, datetime.now().isoformat(timespec="seconds"), form.get("order_type"),
            result["income"],result["miles"],result["minutes"],result["gross_per_mile"],result["net_profit"],result["net_per_hour"],
            result["score"],result["grade"],result["action"],result["risk"],result["pickup"],result["dropoff"],result["route"],ocr_text,screenshot_file,lat,lon
        ))
        return cur.lastrowid

@app.route("/api/extract", methods=["POST"])
def api_extract():
    text = request.json.get("text","") if request.is_json else request.form.get("text","")
    income, miles, minutes = extract_income(text), extract_miles(text), extract_minutes(text)
    return jsonify({"income": income, "miles": miles, "minutes": minutes, "pickup_dropoff": extract_addresses(text), "cities": find_city_hits(text)})

@app.route("/manifest.json")
def manifest_json(): return send_from_directory(STATIC_DIR, "manifest.json")
@app.route("/sw.js")
def sw(): return send_from_directory(STATIC_DIR, "sw.js")

@app.route("/register", methods=["GET","POST"])
def register():
    if request.method=="POST":
        email=request.form.get("email","").strip().lower(); password=request.form.get("password","")
        if not email or len(password)<6:
            flash("邮箱和至少6位密码必填"); return redirect(url_for("register"))
        try:
            with db() as conn:
                conn.execute("INSERT INTO users(email,password_hash,plan,created_at) VALUES (?,?,?,?)", (email,hash_password(password),"free",datetime.now().isoformat(timespec="seconds")))
            flash("注册成功，请登录"); return redirect(url_for("login"))
        except sqlite3.IntegrityError:
            flash("这个邮箱已经注册")
    return render_template("auth.html", mode="register")

@app.route("/login", methods=["GET","POST"])
def login():
    if request.method=="POST":
        email=request.form.get("email","").strip().lower(); password=request.form.get("password","")
        with db() as conn:
            u=conn.execute("SELECT * FROM users WHERE email=?",(email,)).fetchone()
            if u and verify_password(password,u["password_hash"]):
                session["user_id"]=u["id"]; return redirect(url_for("index"))
        flash("邮箱或密码错误")
    return render_template("auth.html", mode="login")

@app.route("/logout")
def logout():
    session.clear(); return redirect(url_for("index"))

@app.route("/", methods=["GET","POST"])
def index():
    init_db()
    user=current_user()
    result=None; order_id=None; error=None
    ocr_text=request.form.get("ocr","") if request.method=="POST" else ""
    form={k:request.form.get(k,default) for k,default in {
        "plan":"pro","income":"","miles":"","minutes":"","gas_price":"5.5","mpg":"38","wear_rate":"0.25","order_type":"uberx","ocr":""
    }.items()}
    if request.method=="POST":
        screenshot_file=""
        try:
            file=request.files.get("screenshot")
            if file and file.filename:
                ext=Path(file.filename).suffix.lower()
                if ext not in [".png",".jpg",".jpeg",".webp",".bmp"]:
                    raise ValueError("请上传图片文件：png / jpg / jpeg / webp / bmp")
                filename=datetime.now().strftime("%Y%m%d%H%M%S_")+secure_filename(file.filename)
                file.save(UPLOAD_DIR/filename)
                screenshot_file=filename
            income=float(form["income"]) if form["income"] else extract_income(ocr_text)
            miles=float(form["miles"]) if form["miles"] else extract_miles(ocr_text)
            minutes=float(form["minutes"]) if form["minutes"] else extract_minutes(ocr_text)
            if income: form["income"]=f"{income:.2f}"
            if miles: form["miles"]=f"{miles:.2f}"
            if minutes:
                minutes = float(fix_uber_time_ocr_number(minutes, miles, ocr_text) or 0)
                form["minutes"]=str(round(minutes, 2))
            if income and miles:
                result=analyze_order(income,miles,minutes or 0,form["gas_price"],form["mpg"],form["wear_rate"],ocr_text,form["order_type"],form["plan"],request.form.get("lat"),request.form.get("lon"))
                order_id=save_order(user,form,result,ocr_text,screenshot_file,request.form.get("lat"),request.form.get("lon"))
            else:
                flash("没有识别到收入/英里。请等待OCR完成，或手动输入收入/英里/时间。")
        except Exception as e:
            error=str(e)
    return render_template("index.html", user=user,result=result,order_id=order_id,ocr_text=ocr_text,form=form,
        today=today_stats(user["id"] if user else None), summary=summary_stats(user["id"] if user else None),
        rows=recent_rows(user["id"] if user else None), money=money, error=error, google_maps_api_key=GOOGLE_MAPS_API_KEY)

@app.route("/batch", methods=["POST"])
def batch():
    items=[]
    gas_price=request.form.get("gas_price","5.5"); mpg=request.form.get("mpg","38"); wear_rate=request.form.get("wear_rate","0.25"); order_type=request.form.get("order_type","uberx")
    batch_text=request.form.get("batch_text","").strip()
    if batch_text:
        parts=[p.strip() for p in re.split(r"\n\s*\n|---+", batch_text) if p.strip()]
        for i,p in enumerate(parts,1):
            income=extract_income(p); miles=extract_miles(p); minutes=extract_minutes(p) or 0
            if income and miles:
                analysis=analyze_order(income,miles,minutes,gas_price,mpg,wear_rate,p,order_type,"pro")
                items.append(dict(filename=f"文字/OCR订单 {i}", ocr_text=p, analysis=analysis, error=""))
            else:
                items.append(dict(filename=f"文字/OCR订单 {i}", ocr_text=p, analysis=None, error="无法识别收入/英里"))
    items.sort(key=lambda x:x["analysis"]["score"] if x.get("analysis") else -1, reverse=True)
    return render_template("batch.html", items=items, money=money)

@app.route("/feedback/<int:order_id>", methods=["POST"])
def feedback(order_id):
    fb=request.form.get("feedback",""); note=request.form.get("note","")
    with db() as conn:
        conn.execute("UPDATE orders SET user_feedback=?, feedback=? WHERE id=?", (fb,note,order_id))
        conn.execute("INSERT INTO feedback(user_id,order_id,created_at,type,content) VALUES (?,?,?,?,?)", (session.get("user_id"),order_id,datetime.now().isoformat(timespec="seconds"),fb,note))
    flash("反馈已保存"); return redirect(url_for("index"))

@app.route("/app_feedback", methods=["POST"])
def app_feedback():
    with db() as conn:
        conn.execute("INSERT INTO app_feedback(created_at,feedback_type,rating,impact,message) VALUES (?,?,?,?,?)", (
            datetime.now().isoformat(timespec="seconds"),request.form.get("feedback_type",""),request.form.get("rating",""),request.form.get("impact",""),request.form.get("message","")
        ))
    flash("反馈已提交"); return redirect(url_for("index"))

@app.route("/export")
def export_csv():
    output=io.StringIO(); writer=csv.writer(output)
    writer.writerow(["created_at","income","miles","minutes","gross_per_mile","net_profit","net_per_hour","score","grade","action","risk","route"])
    for r in recent_rows(session.get("user_id")):
        writer.writerow([r["created_at"],r["income"],r["miles"],r["minutes"],r["gross_per_mile"],r["net_profit"],r["net_per_hour"],r["score"],r["grade"],r["action"],r["risk"],r["route_name"]])
    return Response(output.getvalue(), mimetype="text/csv", headers={"Content-Disposition":"attachment; filename=uber_ai_orders.csv"})

init_db()
if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT",5000)), debug=False)
