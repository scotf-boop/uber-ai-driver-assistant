from flask import Flask, render_template, request, jsonify
from PIL import Image, ImageEnhance, ImageFilter
import re, os, tempfile, math

try:
    import pytesseract
except Exception:
    pytesseract = None

app = Flask(__name__)
APP_VERSION = "V15.2 OCR Fix"

RATE_RULES = [
    (1.0, "垃圾单", "不建议接", "低于 $1/mile，长期开会拉低收入。"),
    (1.2, "偏低单", "谨慎接", "接近保本，除非顺路或回家方向。"),
    (1.6, "普通单", "谨慎接", "$1.2-$1.6/mile，普通水平。"),
    (2.5, "好单", "建议接", "$1.8-$2.5/mile，收入质量不错。"),
    (999, "优秀单", "强烈建议接", "$3+/mile 级别优先接。"),
]

def clean_text(text: str) -> str:
    text = text or ""
    text = text.replace("Ｓ", "$ ").replace("US$", "$").replace("USD", "$")
    text = text.replace("英哩", "英里").replace("哩", "英里")
    return text


def _to_float(raw: str):
    try:
        return float((raw or "").replace(',', '.'))
    except Exception:
        return None


def extract_values(text: str):
    """Robust Uber trip OCR extraction.

    V15.2 fixes:
    - Do not treat the number after 行程距离 as 1 mile when the nearby text is actually time.
    - Prefer direct unit matches like 0.40 英里 / 48.15 英里.
    - Parse 1分钟46秒 as 2 minutes for hourly-income calculation.
    - Prefer the real total income line over 一口价 / tip lines by choosing the largest valid US$ amount.
    """
    t = clean_text(text)
    compact = re.sub(r"\s+", " ", t)
    income = None
    miles = None
    minutes = None

    # Income: choose the largest valid US$/$ amount.
    # Uber detail pages often contain total income, one-price, and tip. The total is normally the largest.
    money_vals = []
    for m in re.finditer(r"(?:US\$|\$)\s*([0-9]{1,4}(?:[\.,][0-9]{1,2})?)", compact, re.I):
        v = _to_float(m.group(1))
        if v is not None and 2 <= v <= 2000:
            money_vals.append(v)
    if money_vals:
        income = max(money_vals)

    # Distance: first preference is explicit mile unit. Do NOT use max(), because OCR may put
    # "行程时间 行程距离 1分钟46秒 0.40英里" and a loose distance pattern can capture the time number 1.
    unit_miles = []
    for m in re.finditer(r"([0-9]{1,4}(?:[\.,][0-9]{1,2})?)\s*(?:英\s*里|mile|miles|mi\b)", compact, re.I):
        v = _to_float(m.group(1))
        if v is not None and 0.05 <= v <= 1000:
            unit_miles.append(v)
    if unit_miles:
        # last explicit mile value is usually the clean trip-distance field after the label
        miles = unit_miles[-1]
    else:
        # fallback: inspect lines around 行程距离/距离 and prefer decimal-like numbers
        fallback = []
        lines = [ln.strip() for ln in t.splitlines() if ln.strip()]
        for i, line in enumerate(lines):
            low = line.lower()
            if "行程距离" in line or "距离" in line or "mile" in low:
                window = " ".join(lines[i:i+3])
                for n in re.findall(r"[0-9]{1,4}(?:[\.,][0-9]{1,2})?", window):
                    v = _to_float(n)
                    if v is not None and 0.05 <= v <= 1000:
                        fallback.append(v)
        if fallback:
            # Prefer decimal distances; otherwise use the last candidate.
            decimal_vals = [v for v in fallback if abs(v - int(v)) > 1e-9]
            miles = decimal_vals[-1] if decimal_vals else fallback[-1]

    # Time: parse hours/minutes/seconds. 1分钟46秒 is rounded up to 2 minutes.
    time_candidates = []

    for m in re.finditer(
        r"([0-9]{1,2})\s*(?:小\s*时|hour|hours|hr|hrs)\s*([0-9]{1,2})?\s*(?:分\s*钟|minute|minutes|min|mins)?",
        compact,
        re.I,
    ):
        h = int(m.group(1)); mm = int(m.group(2) or 0)
        if 0 <= h <= 20 and 0 <= mm < 60:
            time_candidates.append(h * 60 + mm)

    for m in re.finditer(r"([0-9]{1,4})\s*分\s*钟\s*([0-9]{1,2})\s*秒", compact):
        mm = int(m.group(1)); ss = int(m.group(2))
        if 0 <= mm <= 2000 and 0 <= ss < 60:
            time_candidates.append(mm + (1 if ss > 0 else 0))

    for m in re.finditer(r"([0-9]{1,4})\s*(?:minute|minutes|min|mins)\s*([0-9]{1,2})?\s*(?:sec|secs|second|seconds)?", compact, re.I):
        mm = int(m.group(1)); ss = int(m.group(2) or 0)
        if 0 <= mm <= 2000 and 0 <= ss < 60:
            time_candidates.append(mm + (1 if ss > 0 else 0))

    # Chinese minutes without seconds, including OCR-spaced 分 钟.
    for m in re.finditer(r"([0-9]{1,4})\s*分\s*钟(?!\s*[0-9]{1,2}\s*秒)", compact):
        mm = int(m.group(1))
        if 1 <= mm <= 2000:
            time_candidates.append(mm)

    if time_candidates:
        minutes = max(time_candidates)

    return income, miles, minutes

def analyze(income, miles, minutes, pickup_miles=0.0, fuel_cost_per_mile=0.25):
    if not income or not miles or not minutes:
        return {"ok": False, "error": "收入、英里或时间缺失，无法完整分析。"}
    total_miles = miles + max(0.0, pickup_miles or 0.0)
    dollar_per_mile = income / miles if miles else 0
    real_dollar_per_mile = income / total_miles if total_miles else 0
    dollar_per_hour = income / (minutes / 60) if minutes else 0
    fuel_cost = total_miles * fuel_cost_per_mile
    net_income = income - fuel_cost
    net_hour = net_income / (minutes / 60) if minutes else 0

    if dollar_per_mile < 1.0:
        level, action, reason = RATE_RULES[0][1:]
    elif dollar_per_mile < 1.2:
        level, action, reason = RATE_RULES[1][1:]
    elif dollar_per_mile <= 1.6:
        level, action, reason = RATE_RULES[2][1:]
    elif dollar_per_mile <= 2.5:
        level, action, reason = RATE_RULES[3][1:]
    else:
        level, action, reason = RATE_RULES[4][1:]

    if real_dollar_per_mile < 1.0:
        action = "不建议接"
        reason += " 加上接客距离后真实$/mile偏低。"

    return {
        "ok": True,
        "income": round(income, 2),
        "miles": round(miles, 2),
        "minutes": int(minutes),
        "hours_text": f"{minutes//60}小时{minutes%60}分钟" if minutes >= 60 else f"{minutes}分钟",
        "pickup_miles": round(pickup_miles, 2),
        "total_miles": round(total_miles, 2),
        "dollar_per_mile": round(dollar_per_mile, 2),
        "real_dollar_per_mile": round(real_dollar_per_mile, 2),
        "dollar_per_hour": round(dollar_per_hour, 2),
        "fuel_cost": round(fuel_cost, 2),
        "net_income": round(net_income, 2),
        "net_hour": round(net_hour, 2),
        "level": level,
        "action": action,
        "reason": reason,
    }


def ocr_image(path):
    if pytesseract is None:
        return ""
    img = Image.open(path).convert('L')
    img = ImageEnhance.Contrast(img).enhance(2.2)
    img = img.filter(ImageFilter.SHARPEN)
    w, h = img.size
    if w < 1200:
        img = img.resize((w*2, h*2))
    configs = ["--psm 6", "--psm 11", "--psm 4"]
    texts = []
    for cfg in configs:
        try:
            texts.append(pytesseract.image_to_string(img, lang="eng+chi_sim", config=cfg))
        except Exception:
            try:
                texts.append(pytesseract.image_to_string(img, lang="eng", config=cfg))
            except Exception:
                pass
    return "\n".join(texts)

@app.route('/')
def index():
    return render_template('index.html', version=APP_VERSION)

@app.route('/analyze_text', methods=['POST'])
def analyze_text():
    data = request.get_json(force=True)
    text = data.get('text','')
    pickup = float(data.get('pickup_miles') or 0)
    cost = float(data.get('fuel_cost_per_mile') or 0.25)
    income, miles, minutes = extract_values(text)
    return jsonify({"extracted": {"income": income, "miles": miles, "minutes": minutes}, "analysis": analyze(income, miles, minutes, pickup, cost), "ocr_text": text})

@app.route('/analyze_image', methods=['POST'])
def analyze_image():
    f = request.files.get('file')
    pickup = float(request.form.get('pickup_miles') or 0)
    cost = float(request.form.get('fuel_cost_per_mile') or 0.25)
    if not f:
        return jsonify({"error":"没有收到图片"}), 400
    with tempfile.NamedTemporaryFile(delete=False, suffix='.png') as tmp:
        f.save(tmp.name)
        path = tmp.name
    try:
        text = ocr_image(path)
    finally:
        try: os.remove(path)
        except Exception: pass
    income, miles, minutes = extract_values(text)
    return jsonify({"extracted": {"income": income, "miles": miles, "minutes": minutes}, "analysis": analyze(income, miles, minutes, pickup, cost), "ocr_text": text})

if __name__ == '__main__':
    app.run(host='127.0.0.1', port=5000, debug=True)
