from __future__ import annotations
import os, re, sqlite3, hashlib, secrets
from datetime import datetime
from pathlib import Path
from flask import Flask, render_template, request, redirect, url_for, session, flash, send_from_directory
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
app.config["MAX_CONTENT_LENGTH"] = 12 * 1024 * 1024

# V10 Stable：关闭云端OCR模型，避免 Render 502 / 内存爆掉。
# 截图仍可上传保存；请粘贴订单文字或手动输入收入、英里、时间。

def db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn

def init_db():
    with db() as conn:
        conn.executescript('''
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
            feedback TEXT
        );
        CREATE TABLE IF NOT EXISTS feedback(
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER,
            order_id INTEGER,
            created_at TEXT NOT NULL,
            type TEXT,
            content TEXT
        );
        CREATE TABLE IF NOT EXISTS subscriptions(
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            provider TEXT DEFAULT 'manual',
            status TEXT DEFAULT 'inactive',
            plan TEXT DEFAULT 'free',
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        );
        ''')

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

def extract_income(text):
    vals = []
    for line in (text or "").splitlines():
        if "$" in line or "US$" in line:
            vals += [n for n in nums(line) if 2 <= n <= 500]
    return max(vals) if vals else None

def extract_miles(text):
    vals = []
    for line in (text or "").splitlines():
        low = line.lower()
        if any(k in low for k in ["mile", " mi", "英里", "距离", "里程"]):
            vals += [n for n in nums(line) if 0.1 <= n <= 400]
    return vals[-1] if vals else None

def extract_minutes(text):
    t = text or ""
    m = re.search(r"([0-9]+)\s*小时\s*([0-9]+)\s*分钟", t)
    if m:
        return int(m.group(1)) * 60 + int(m.group(2))
    m = re.search(r"([0-9]+)\s*hr[s]?\s*([0-9]+)\s*min", t, re.I)
    if m:
        return int(m.group(1)) * 60 + int(m.group(2))
    vals = []
    for line in t.splitlines():
        low = line.lower()
        if any(k in low for k in ["延误", "增加", "等待", "delay"]):
            continue
        if any(k in low for k in ["分钟", " min", "minute", "行程时间", "duration"]):
            v = nums(line)
            if v and 1 <= v[0] <= 600:
                vals.append(int(v[0]))
    return max(vals) if vals else None

STREET_WORDS = [" ave", " avenue", " st", " street", " blvd", " boulevard", " rd", " road", " dr", " drive", " hwy", " highway", " way", " lane"]
def extract_addresses(text):
    found = []
    for line in (text or "").splitlines():
        low = line.strip().lower()
        if not low or any(x in low for x in ["us$", "$", "miles", "英里", "分钟", "小时", "积分"]):
            continue
        if any(w in low for w in STREET_WORDS) or any(c in low for c in ["los angeles", "huntington beach", "irvine", "arcadia", "pasadena"]):
            found.append(line.strip())
    return found[0] if len(found)>0 else "", found[1] if len(found)>1 else ""

def market_by_text(text):
    low = (text or "").lower()
    if any(x in low for x in ["los angeles", "irvine", "huntington beach", "arcadia", "pasadena"]):
        return dict(name="Los Angeles / SoCal", min_accept=1.0, normal=1.2, good=1.8, excellent=3.0, min_hour=20, good_hour=30, excellent_hour=40)
    if any(x in low for x in ["new york", "manhattan", "brooklyn", "queens"]):
        return dict(name="New York City", min_accept=1.3, normal=1.8, good=2.5, excellent=3.6, min_hour=25, good_hour=35, excellent_hour=45)
    return dict(name="US Default Market", min_accept=.9, normal=1.2, good=1.8, excellent=2.8, min_hour=20, good_hour=30, excellent_hour=40)

ORDER_TYPES = {"uberx":("UberX",1,1),"comfort":("Comfort",1.15,1.1),"uberxl":("UberXL",1.35,1.2),"black":("Uber Black",1.8,1.5),"airport":("Airport",1.2,1.15),"reservation":("Reservation",1.25,1.2),"shared":("Uber Share",.95,1),"eats":("Uber Eats",.85,.9)}
def apply_order_type(m, order_type):
    name, mm, hm = ORDER_TYPES.get(order_type, ORDER_TYPES["uberx"])
    m = dict(m); m["order_type_name"] = name
    for k in ["min_accept","normal","good","excellent"]: m[k] *= mm
    for k in ["min_hour","good_hour","excellent_hour"]: m[k] *= hm
    return m

def analyze_order(income, miles, minutes, gas_price, mpg, wear_rate, text, order_type):
    income, miles, minutes = float(income), float(miles), int(float(minutes or 0))
    gas_price, mpg, wear_rate = float(gas_price or 5.5), float(mpg or 38), float(wear_rate or .25)
    fuel = miles / mpg * gas_price if mpg else 0
    wear = miles * wear_rate
    net = income - fuel - wear
    gpm = income / miles if miles else 0
    nph = net / (minutes/60) if minutes else 0
    m = apply_order_type(market_by_text(text), order_type)
    pickup, dropoff = extract_addresses(text)
    score = 50
    if gpm >= m["excellent"]: score += 35; tier = "神单"
    elif gpm >= m["good"]: score += 22; tier = "好单"
    elif gpm >= m["normal"]: score += 8; tier = "普通单"
    elif gpm >= m["min_accept"]: score -= 8; tier = "偏低单"
    else: score -= 25; tier = "垃圾单"
    if nph >= m["excellent_hour"]: score += 25
    elif nph >= m["good_hour"]: score += 15
    elif nph >= m["min_hour"]: score += 5
    else: score -= 18
    warnings=[]
    low=(text or "").lower()
    if miles >= 40: score -= 18; warnings.append("40英里以上长途单，回程风险高。")
    elif miles >= 25: score -= 10; warnings.append("中长途订单，注意回程单概率。")
    if minutes >= 120: score -= 15; warnings.append("耗时超过2小时，真实时薪风险高。")
    if ("los angeles" in low or "hillside" in low) and any(x in low for x in ["irvine","huntington beach","orange county","pacific coast hwy"]):
        score -= 25; route="Los Angeles → Orange County / Irvine"; warnings.append("LA到OC方向，空车回程风险高。")
    else:
        route=(pickup or "起点未知") + " → " + (dropoff or "终点未知")
    score=max(0,min(100,int(score)))
    if score>=80: grade,risk,action,cls="强烈建议：当地神单","低风险","马上接","good"
    elif score>=62: grade,risk,action,cls="建议：当地好单","低风险","建议接单","good"
    elif score>=45: grade,risk,action,cls="普通可接单","中等风险","看情况","ok"
    else: grade,risk,action,cls="不建议：收益或路线风险高","高风险","建议拒绝","bad"
    if miles >= 25 and (nph < m["min_hour"] or gpm < m["normal"]):
        grade,risk,action,cls="不建议：长途低收益路线","高风险","建议拒绝","bad"
    reason=f"当前市场：{m['name']}；订单类型：{m['order_type_name']}。每英里收入 ${gpm:.2f}，属于 {tier}。净利润 ${net:.2f}；预计每小时净利 ${nph:.2f}。地址路线AI：{route}。{' '.join(warnings) if warnings else '暂无明显路线风险。'}"
    return dict(income=income,miles=miles,minutes=minutes,fuel_cost=fuel,vehicle_cost=wear,net_profit=net,gross_per_mile=gpm,net_per_hour=nph,score=score,grade=grade,risk=risk,action=action,cls=cls,reason=reason,pickup=pickup,dropoff=dropoff,route=route)

def user_stats(user_id):
    if not user_id: return dict(total=0,income=0,net=0,avg_score=0)
    with db() as conn: rows=conn.execute("SELECT * FROM orders WHERE user_id=? ORDER BY id DESC",(user_id,)).fetchall()
    if not rows: return dict(total=0,income=0,net=0,avg_score=0)
    return dict(total=len(rows), income=sum(float(r["income"] or 0) for r in rows), net=sum(float(r["net_profit"] or 0) for r in rows), avg_score=sum(int(r["score"] or 0) for r in rows)/len(rows))

@app.route('/manifest.json')
def manifest_json(): return send_from_directory(STATIC_DIR, 'manifest.json')
@app.route('/sw.js')
def sw(): return send_from_directory(STATIC_DIR, 'sw.js')

@app.route('/register', methods=['GET','POST'])
def register():
    if request.method=='POST':
        email=request.form.get('email','').strip().lower(); password=request.form.get('password','')
        if not email or len(password)<6: flash('邮箱和至少6位密码必填'); return redirect(url_for('register'))
        try:
            with db() as conn: conn.execute('INSERT INTO users(email,password_hash,plan,created_at) VALUES (?,?,?,?)',(email,hash_password(password),'free',datetime.utcnow().isoformat()))
            flash('注册成功，请登录'); return redirect(url_for('login'))
        except sqlite3.IntegrityError: flash('这个邮箱已经注册')
    return render_template('auth.html', mode='register')

@app.route('/login', methods=['GET','POST'])
def login():
    if request.method=='POST':
        email=request.form.get('email','').strip().lower(); password=request.form.get('password','')
        with db() as conn:
            u=conn.execute('SELECT * FROM users WHERE email=?',(email,)).fetchone()
            if u and verify_password(password,u['password_hash']): session['user_id']=u['id']; return redirect(url_for('index'))
        flash('邮箱或密码错误')
    return render_template('auth.html', mode='login')

@app.route('/logout')
def logout(): session.clear(); return redirect(url_for('index'))
@app.route('/pro')
def pro(): return render_template('pro.html', user=current_user())

@app.route('/', methods=['GET','POST'])
def index():
    init_db(); user=current_user(); result=None; order_id=None; ocr_text=request.form.get('ocr_text','') if request.method=='POST' else ''
    form={k:request.form.get(k,default) for k,default in {'income':'','miles':'','minutes':'','gas_price':'5.5','mpg':'38','wear_rate':'0.25','order_type':'uberx'}.items()}
    if request.method=='POST':
        file=request.files.get('screenshot'); screenshot_file=''
        if file and file.filename:
            filename=datetime.utcnow().strftime('%Y%m%d%H%M%S_')+secure_filename(file.filename); file.save(UPLOAD_DIR/filename); screenshot_file=filename
            if not ocr_text.strip(): flash('截图已上传。V10稳定版关闭云端OCR，请粘贴订单文字，或手动输入收入/英里/时间。')
        income=float(form['income']) if form['income'] else extract_income(ocr_text)
        miles=float(form['miles']) if form['miles'] else extract_miles(ocr_text)
        minutes=int(float(form['minutes'])) if form['minutes'] else extract_minutes(ocr_text)
        if income: form['income']=f'{income:.2f}'
        if miles: form['miles']=f'{miles:.2f}'
        if minutes: form['minutes']=str(minutes)
        if income and miles:
            result=analyze_order(income,miles,minutes or 0,form['gas_price'],form['mpg'],form['wear_rate'],ocr_text,form['order_type'])
            if user:
                with db() as conn:
                    cur=conn.execute('''INSERT INTO orders(user_id,created_at,order_type,income,miles,minutes,gross_per_mile,net_profit,net_per_hour,score,grade,action,risk,pickup_address,dropoff_address,route_name,ocr_text,screenshot_file) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)''',(user['id'],datetime.utcnow().isoformat(),form['order_type'],result['income'],result['miles'],result['minutes'],result['gross_per_mile'],result['net_profit'],result['net_per_hour'],result['score'],result['grade'],result['action'],result['risk'],result['pickup'],result['dropoff'],result['route'],ocr_text,screenshot_file))
                    order_id=cur.lastrowid
        else: flash('没有识别到收入/英里。请手动输入，或粘贴订单文字。')
    recent=[]
    if user:
        with db() as conn: recent=conn.execute('SELECT * FROM orders WHERE user_id=? ORDER BY id DESC LIMIT 10',(user['id'],)).fetchall()
    return render_template('index.html', user=user, result=result, order_id=order_id, ocr_text=ocr_text, form=form, stats=user_stats(user['id'] if user else None), recent_orders=recent)

init_db()
if __name__ == '__main__':
    app.run(host='0.0.0.0', port=int(os.environ.get('PORT',5000)), debug=False)
