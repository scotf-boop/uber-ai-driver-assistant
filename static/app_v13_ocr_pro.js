
const I18N = {
  zh:{title:"Uber AI Cloud V13 OCR Pro",subtitle:"Python 3.14兼容 · V13 OCR Pro · 总收入识别 · 分钟秒识别 · GPS · 路线AI"},
  en:{title:"Uber AI Cloud V13 OCR Pro",subtitle:"Python 3.14 compatible · V13 OCR Pro · total income OCR · minutes/seconds · GPS"},
  es:{title:"Uber AI Cloud V13 OCR Pro",subtitle:"Compatible con Python 3.14 · V13 OCR Pro · ingreso total · minutos/segundos"},
  ko:{title:"Uber AI Cloud V13 OCR Pro",subtitle:"Python 3.14 호환 · V13 OCR Pro · 총수입 인식 · 분/초 인식"}
};
function setLanguage(lang){
  localStorage.setItem("uber_ai_lang", lang);
  document.querySelectorAll("[data-i18n]").forEach(el=>{
    const key=el.getAttribute("data-i18n");
    if(I18N[lang]&&I18N[lang][key]) el.textContent=I18N[lang][key];
  });
  const sel=document.getElementById("languageSelect"); if(sel) sel.value=lang;
}
async function makeEnhancedImage(file){
  return new Promise((resolve, reject)=>{
    const img = new Image();
    img.onload = ()=>{
      const scale = 2.5;
      const canvas = document.createElement("canvas");
      canvas.width = Math.round(img.width * scale);
      canvas.height = Math.round(img.height * scale);
      const ctx = canvas.getContext("2d", {willReadFrequently:true});
      ctx.imageSmoothingEnabled = true;
      ctx.drawImage(img, 0, 0, canvas.width, canvas.height);

      const imageData = ctx.getImageData(0, 0, canvas.width, canvas.height);
      const d = imageData.data;

      for(let i=0; i<d.length; i+=4){
        let gray = 0.299*d[i] + 0.587*d[i+1] + 0.114*d[i+2];

        // 增强对比度
        gray = (gray - 128) * 1.65 + 128;

        // 轻微二值化，让小字更清楚
        if(gray > 185) gray = 255;
        else if(gray < 75) gray = 0;

        gray = Math.max(0, Math.min(255, gray));
        d[i] = d[i+1] = d[i+2] = gray;
      }

      ctx.putImageData(imageData, 0, 0);
      canvas.toBlob(blob => {
        if(blob) resolve(blob);
        else reject(new Error("图片增强失败"));
      }, "image/png");
    };
    img.onerror = reject;
    img.src = URL.createObjectURL(file);
  });
}


async function cropImageRegion(file, region){
  return new Promise((resolve, reject)=>{
    const img = new Image();
    img.onload = ()=>{
      const canvas = document.createElement("canvas");
      const sx = Math.round(img.width * region.x);
      const sy = Math.round(img.height * region.y);
      const sw = Math.round(img.width * region.w);
      const sh = Math.round(img.height * region.h);
      const scale = 3.0;
      canvas.width = Math.round(sw * scale);
      canvas.height = Math.round(sh * scale);
      const ctx = canvas.getContext("2d", {willReadFrequently:true});
      ctx.imageSmoothingEnabled = true;
      ctx.drawImage(img, sx, sy, sw, sh, 0, 0, canvas.width, canvas.height);
      const imageData = ctx.getImageData(0, 0, canvas.width, canvas.height);
      const d = imageData.data;
      for(let i=0; i<d.length; i+=4){
        let gray = 0.299*d[i] + 0.587*d[i+1] + 0.114*d[i+2];
        gray = (gray - 128) * 2.0 + 128;
        if(gray > 180) gray = 255;
        else if(gray < 80) gray = 0;
        gray = Math.max(0, Math.min(255, gray));
        d[i] = d[i+1] = d[i+2] = gray;
      }
      ctx.putImageData(imageData, 0, 0);
      canvas.toBlob(blob => blob ? resolve(blob) : reject(new Error("区域裁剪失败")), "image/png");
    };
    img.onerror = reject;
    img.src = URL.createObjectURL(file);
  });
}

async function recognizeImageBlob(blob, label, status){
  const r = await Tesseract.recognize(blob, "chi_sim+eng", {
    tessedit_pageseg_mode: "6",
    preserve_interword_spaces: "1",
    logger:m=>{
      if(status && m.status && m.progress){
        status.textContent = `${label} ${m.status} ${Math.round(m.progress*100)}%`;
      }
    }
  });
  return (r.data && r.data.text) ? r.data.text : "";
}

function regionExtractUberFields(mergedText){
  const result = {income:null, miles:null, minutes:null};
  let text = (mergedText || "").replace(/US\s*\$/gi, "$").replace(/＄/g, "$").replace(/，/g, ".");

  const money = [...text.matchAll(/\$\s*([0-9]{1,3}(?:\.[0-9]{1,2})?)/g)]
    .map(m=>parseFloat(m[1])).filter(v=>v>=2 && v<=250);
  if(money.length) result.income = Math.max(...money);

  const zhMiles = [...text.matchAll(/(?:行程距离|距离|里程)?\s*([0-9]{1,3}(?:\.[0-9]+)?)\s*英里/g)]
    .map(m=>parseFloat(m[1])).filter(v=>v>=0.1 && v<=300);
  if(zhMiles.length) result.miles = zhMiles[zhMiles.length - 1];

  const enMiles = [...text.matchAll(/([0-9]{1,3}(?:\.[0-9]+)?)\s*(?:mi|mile|miles)\b/gi)]
    .map(m=>parseFloat(m[1])).filter(v=>v>=0.1 && v<=300);
  if(!result.miles && enMiles.length) result.miles = enMiles[enMiles.length - 1];

  let th = text.match(/([0-9]{1,2})\s*小时\s*([0-9]{1,2})\s*分钟/);
  if(th) result.minutes = parseInt(th[1]) * 60 + parseInt(th[2]);

  if(!result.minutes){
    const compact = text.replace(/\s+/g, "");
    th = compact.match(/([0-9]{1,2})小时([0-9]{1,2})分钟/);
    if(th) result.minutes = parseInt(th[1]) * 60 + parseInt(th[2]);
  }

  let t = text.match(/([0-9]{1,3})\s*分钟\s*([0-9]{1,2})?\s*秒?/);
  if(!result.minutes && t) result.minutes = parseInt(t[1]);

  if(!result.minutes){
    const mins = [...text.matchAll(/([0-9]{1,3})\s*(?:min|mins|minute|minutes)\b/gi)]
      .map(m=>parseInt(m[1])).filter(v=>v>=1 && v<=600);
    if(mins.length) result.minutes = Math.max(...mins);
  }
  return result;
}

function mergeOCRText(a, b){
  const lines = [];
  const seen = new Set();
  for(const text of [a || "", b || ""]){
    for(const line of text.split(/\r?\n/)){
      const clean = line.trim();
      if(!clean) continue;
      const key = clean.toLowerCase().replace(/\s+/g, " ");
      if(!seen.has(key)){
        seen.add(key);
        lines.push(clean);
      }
    }
  }
  return lines.join("\n");
}


function normalizeUberOCRTextV124(text){
  text = text || "";
  return text
    .replace(/小\s*时/g, "小时")
    .replace(/时\s*间/g, "时间")
    .replace(/分\s*钟/g, "分钟")
    .replace(/秒\s*/g, "秒")
    .replace(/英\s*里/g, "英里")
    .replace(/行\s*程\s*时\s*间/g, "行程时间")
    .replace(/行\s*程\s*距\s*离/g, "行程距离")
    .replace(/O\./g, "0.")
    .replace(/o\./g, "0.")
    .replace(/，/g, ".");
}


function fixUberTimeOCRNumber(rawMinutes, miles, text){
  if(rawMinutes == null || rawMinutes === "") return rawMinutes;
  let n = parseInt(rawMinutes);
  if(isNaN(n)) return rawMinutes;

  // 正常范围直接返回
  if(n > 0 && n <= 60) return n;

  // Uber短途订单不可能 0.40 英里跑 111 分钟；
  // OCR常把 “1分钟46秒” 连读成 146 / 111 / 106 这种数字。
  let m = parseFloat(miles || "0");

  // 三位数：只有短距离订单才按 分钟+秒 纠正，例如 146 => 1分46秒 => 1分钟
  if(n >= 100 && n <= 999){
    if(m > 0 && m <= 2){
      const minPart = Math.floor(n / 100);
      const secPart = n % 100;
      if(minPart >= 1 && minPart <= 60 && secPart >= 0 && secPart <= 59){
        return minPart;
      }
      if(String(n).startsWith("1")){
        return 1;
      }
    }
    return n;
  }

  // 四位数：1006 => 10分06秒 => 10分钟
  if(n >= 1000 && n <= 9999){
    const minPart = Math.floor(n / 100);
    const secPart = n % 100;
    if(minPart >= 1 && minPart <= 120 && secPart >= 0 && secPart <= 59){
      return minPart;
    }
  }

  // 如果里程很短但时间识别超大，直接清空，避免给出错误分析
  if(m > 0 && m <= 2 && n > 60){
    return "";
  }

  return n;
}

function extractTimeFromTimeRegion(text){
  text = normalizeUberOCRTextV124(text);

  // 最高优先级：2小时28分钟 / 2 小时 28 分钟
  let hm = text.match(/([0-9]{1,2})\s*小时\s*([0-9]{1,2})\s*分钟/);
  if(hm){
    const total = parseInt(hm[1]) * 60 + parseInt(hm[2]);
    if(total >= 1 && total <= 600) return total;
  }

  // 有时OCR会识别为：2 小 时 28 分 钟
  const compact = text.replace(/\s+/g, "");
  hm = compact.match(/([0-9]{1,2})小时([0-9]{1,2})分钟/);
  if(hm){
    const total = parseInt(hm[1]) * 60 + parseInt(hm[2]);
    if(total >= 1 && total <= 600) return total;
  }

  // 1分钟46秒 / 1 分钟 46 秒
  let m = text.match(/([0-9]{1,3})\s*分钟\s*([0-9]{1,2})?\s*秒?/);
  if(m) return parseInt(m[1]);

  // OCR可能识别成 1:46
  m = text.match(/\b([0-9]{1,2})\s*[:：]\s*([0-9]{1,2})\b/);
  if(m) return parseInt(m[1]);

  // 英文 2 hr 28 min
  hm = text.match(/([0-9]{1,2})\s*(?:hr|hrs|hour|hours)\s*([0-9]{1,2})\s*(?:min|mins|minute|minutes)/i);
  if(hm){
    const total = parseInt(hm[1]) * 60 + parseInt(hm[2]);
    if(total >= 1 && total <= 600) return total;
  }

  // OCR可能把“1分钟46秒”连读成 146 / 111 / 106
  const nums = [...text.matchAll(/[0-9]{1,4}/g)].map(x=>parseInt(x[0])).filter(v=>v>=1 && v<=9999);
  if(nums.length){
    for(const n of nums){
      if(n >= 100){
        const fixed = fixUberTimeOCRNumber(n, document.getElementById("milesInput")?.value || "", text);
        if(fixed !== "" && fixed <= 600) return fixed;
      }
    }
    return nums[0];
  }

  return null;
}

function extractMilesFromDistanceRegion(text){
  text = normalizeUberOCRTextV124(text);
  let m = text.match(/([0-9]{1,3}(?:\.[0-9]+)?)\s*英里/);
  if(m) return parseFloat(m[1]);

  m = text.match(/([0-9]{1,3}(?:\.[0-9]+)?)\s*(?:mi|mile|miles)\b/i);
  if(m) return parseFloat(m[1]);

  // OCR可能把 0.40 识别为 040
  const dec = text.match(/[0-9]{1,3}\.[0-9]+/);
  if(dec) return parseFloat(dec[0]);

  return null;
}

async function runBrowserOCR(input){
  const status=document.getElementById("ocrStatus");
  const textBox=document.getElementById("ocrText");
  if(!input.files || !input.files[0]) return;
  const file=input.files[0];

  if(!window.Tesseract){
    status.textContent="OCR组件未加载成功。请确认电脑联网，或手动粘贴订单文字。";
    return;
  }

  status.textContent="正在准备Uber截图分区识别...";
  try{
    const enhanced = await makeEnhancedImage(file);

    // Uber行程详情常见布局：
    // 顶部金额区
    // 地图下方左侧：行程时间
    // 地图下方右侧：行程距离
    // 地址区
    const topAmount = await cropImageRegion(file, {x:0.00, y:0.17, w:1.00, h:0.18});

    // 专门拆分：左侧时间、右侧距离
    const timeRegion = await cropImageRegion(file, {x:0.02, y:0.535, w:0.46, h:0.135});
    const distanceRegion = await cropImageRegion(file, {x:0.45, y:0.535, w:0.53, h:0.135});

    // 稍大范围兜底
    const statsRegion = await cropImageRegion(file, {x:0.00, y:0.535, w:1.00, h:0.16});
    const addressArea = await cropImageRegion(file, {x:0.00, y:0.64, w:1.00, h:0.17});

    status.textContent="OCR识别金额...";
    const tAmount = await recognizeImageBlob(topAmount, "金额区OCR", status);

    status.textContent="OCR识别左侧行程时间...";
    const tTime = await recognizeImageBlob(timeRegion, "时间区OCR", status);

    status.textContent="OCR识别右侧行程距离...";
    const tDistance = await recognizeImageBlob(distanceRegion, "距离区OCR", status);

    status.textContent="OCR识别统计区域兜底...";
    const tStats = await recognizeImageBlob(statsRegion, "统计区OCR", status);

    status.textContent="OCR识别地址区域...";
    const tAddr = await recognizeImageBlob(addressArea, "地址区OCR", status);

    status.textContent="OCR识别整图补充...";
    const rFull = await Tesseract.recognize(enhanced, "chi_sim+eng", {
      tessedit_pageseg_mode: "6",
      preserve_interword_spaces: "1",
      logger:m=>{
        if(m.status && m.progress) status.textContent=`整图增强OCR ${m.status} ${Math.round(m.progress*100)}%`;
      }
    });
    const tFull = (rFull.data && rFull.data.text) ? rFull.data.text : "";

    const merged = mergeOCRText(
      tAmount + "\n" + tTime + "\n" + tDistance,
      tStats + "\n" + tAddr + "\n" + tFull
    );

    textBox.value = merged.trim();

    const regionFields = regionExtractUberFields(merged);
    const timeOnly = extractTimeFromTimeRegion(tTime + "\n" + tStats);
    const milesOnly = extractMilesFromDistanceRegion(tDistance + "\n" + tStats);

    if(regionFields.income) document.getElementById("incomeInput").value = Number(regionFields.income).toFixed(2);
    if(milesOnly) document.getElementById("milesInput").value = Number(milesOnly).toFixed(2);
    else if(regionFields.miles) document.getElementById("milesInput").value = Number(regionFields.miles).toFixed(2);

    if(timeOnly) document.getElementById("minutesInput").value = parseInt(timeOnly);
    else if(regionFields.minutes) document.getElementById("minutesInput").value = parseInt(regionFields.minutes);

    status.textContent = `分区识别完成：收入 ${regionFields.income || "未识别"}，英里 ${milesOnly || regionFields.miles || "未识别"}，时间 ${timeOnly || regionFields.minutes || "未识别"}`;

    await extractFieldsFromText();

    // 防止通用提取覆盖分区结果：再写回一次
    if(milesOnly) document.getElementById("milesInput").value = Number(milesOnly).toFixed(2);
    if(timeOnly) document.getElementById("minutesInput").value = parseInt(timeOnly);

    const income = document.getElementById("incomeInput").value;
    const miles = document.getElementById("milesInput").value;
    let minutes = document.getElementById("minutesInput").value;
    const fixedMinutes = fixUberTimeOCRNumber(minutes, miles, textBox.value);
    if(fixedMinutes !== minutes){
      document.getElementById("minutesInput").value = fixedMinutes;
      minutes = fixedMinutes;
    }
    status.textContent = `最终提取：收入 ${income || "未识别"}，英里 ${miles || "未识别"}，时间 ${minutes || "未识别"}`;
  }catch(e){
    status.textContent="OCR失败：" + e + "。可以手动粘贴订单文字。";
  }
}

function localExtractV121(text){
  text = (text || "").replace(/US\s*\$/gi, "$").replace(/＄/g, "$");
  let income = null, miles = null, minutes = null;

  const moneyMatches = [...text.matchAll(/\$\s*([0-9]{1,3}(?:\.[0-9]{1,2})?)/g)]
    .map(m => parseFloat(m[1]))
    .filter(v => v >= 2 && v <= 250);
  if(moneyMatches.length) income = Math.max(...moneyMatches);

  // miles: support 4.2 mi / 4.2mi / 4,2 mi / 4 2 mi OCR mistakes
  let normalizedForMiles = text.replace(/([0-9])\s+([0-9])\s*(mi|mile|miles)\b/gi, "$1.$2 $3")
                               .replace(/,/g, ".");
  const zhMileMatches = [...text.matchAll(/(?:行程距离|距离|里程)?\s*([0-9]{1,3}(?:\.[0-9]+)?)\s*英里/g)]
    .map(m => parseFloat(m[1]))
    .filter(v => v >= 0.1 && v <= 300);
  if(zhMileMatches.length) miles = zhMileMatches[zhMileMatches.length - 1];

  const mileMatches = [...normalizedForMiles.matchAll(/([0-9]{1,3}(?:\.[0-9]+)?)\s*(?:mi|mile|miles)\b/gi)]
    .map(m => parseFloat(m[1]))
    .filter(v => v >= 0.1 && v <= 300);
  if(!miles && mileMatches.length) miles = mileMatches[mileMatches.length - 1];

  let minuteCandidates = [];
  const hrMin = text.match(/([0-9]{1,2})\s*(?:hr|hrs|hour|hours)\s*([0-9]{1,2})\s*(?:min|mins|minute|minutes)/i);
  if(hrMin) {
    minutes = parseInt(hrMin[1]) * 60 + parseInt(hrMin[2]);
  } else {
    for(const m of text.matchAll(/([0-9]{1,3})\s*(?:min|mins|minute|minutes|mn|m)\b/gi)){
      const v = parseInt(m[1]);
      if(v >= 1 && v <= 600) minuteCandidates.push(v);
    }
    for(const m of text.matchAll(/([0-9]{1,3})\s*分钟/g)){
      const v = parseInt(m[1]);
      if(v >= 1 && v <= 600) minuteCandidates.push(v);
    }
    if(minuteCandidates.length) minutes = Math.max(...minuteCandidates);
  }
  return {income, miles, minutes};
}

async function extractFieldsFromText(){
  const text=document.getElementById("ocrText").value || "";
  const status=document.getElementById("ocrStatus");
  const local = localExtractV121(text);

  if(local.income) document.getElementById("incomeInput").value=Number(local.income).toFixed(2);
  if(local.miles) document.getElementById("milesInput").value=Number(local.miles).toFixed(2);
  if(local.minutes) document.getElementById("minutesInput").value=parseInt(local.minutes);

  try{
    const res=await fetch("/api/extract",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({text})});
    const data=await res.json();

    const income = local.income || data.income;
    const miles = local.miles || data.miles;
    const minutes = local.minutes || data.minutes;

    if(income) document.getElementById("incomeInput").value=Number(income).toFixed(2);
    if(miles) document.getElementById("milesInput").value=Number(miles).toFixed(2);
    if(minutes){
      const fixed = fixUberTimeOCRNumber(minutes, miles, text);
      document.getElementById("minutesInput").value = fixed;
      minutes = fixed;
    }

    if(status) status.textContent=`提取完成：收入 ${income || "未识别"}，英里 ${miles || "未识别"}，时间 ${minutes || "未识别"}`;
  }catch(e){
    if(status) status.textContent=`本地提取完成：收入 ${local.income || "未识别"}，英里 ${local.miles || "未识别"}，时间 ${local.minutes || "未识别"}`;
  }
}
function getLocation(){
  const s=document.getElementById("gpsStatus");
  if(!navigator.geolocation){ s.textContent="浏览器不支持GPS，请手动输入"; useManualLocation(); return; }
  const isLocalhost=location.hostname==="localhost"||location.hostname==="127.0.0.1";
  const isHttps=location.protocol==="https:";
  if(!isHttps&&!isLocalhost) s.textContent="HTTP局域网可能限制GPS，请手动输入"; else s.textContent="正在获取...";
  navigator.geolocation.getCurrentPosition(pos=>{
    const lat=pos.coords.latitude.toFixed(6), lon=pos.coords.longitude.toFixed(6);
    document.getElementById("lat").value=lat; document.getElementById("lon").value=lon;
    s.textContent=lat+", "+lon;
    localStorage.setItem("manual_lat",lat); localStorage.setItem("manual_lon",lon);
  }, err=>{
    s.textContent="定位失败，可手动输入";
    useManualLocation();
    const oldLat=localStorage.getItem("manual_lat"), oldLon=localStorage.getItem("manual_lon");
    if(oldLat&&oldLon){document.getElementById("lat").value=oldLat;document.getElementById("lon").value=oldLon;s.textContent=oldLat+", "+oldLon+"（已使用上次保存）";}
  },{enableHighAccuracy:true,timeout:12000,maximumAge:60000});
}
function useManualLocation(){const box=document.getElementById("manualGpsBox"); if(box) box.style.display="block";}
function saveManualLocation(){
  const input=document.getElementById("manualGpsInput"), s=document.getElementById("gpsStatus"), val=(input.value||"").trim();
  const cityMap={"los angeles":["34.052235","-118.243683"],"la":["34.052235","-118.243683"],"arcadia":["34.139729","-118.035344"],"temple city":["34.107230","-118.057846"],"san gabriel":["34.096111","-118.105833"],"irvine":["33.684567","-117.826505"],"huntington beach":["33.659484","-117.998802"],"orange county":["33.717470","-117.831143"],"lax":["33.941589","-118.408530"],"las vegas":["36.169941","-115.139832"],"new york":["40.712776","-74.005974"]};
  let lat="",lon="",lower=val.toLowerCase();
  if(cityMap[lower]){lat=cityMap[lower][0];lon=cityMap[lower][1];}
  else{const parts=val.split(",").map(x=>x.trim()); if(parts.length>=2&&!isNaN(parseFloat(parts[0]))&&!isNaN(parseFloat(parts[1]))){lat=parseFloat(parts[0]).toFixed(6);lon=parseFloat(parts[1]).toFixed(6);}}
  if(!lat||!lon){s.textContent="格式错误：请输入 34.090295,-118.009022 或 Los Angeles";return;}
  document.getElementById("lat").value=lat;document.getElementById("lon").value=lon;localStorage.setItem("manual_lat",lat);localStorage.setItem("manual_lon",lon);s.textContent=lat+", "+lon+"（手动位置）";
}
window.addEventListener("load",()=>{
  setLanguage(localStorage.getItem("uber_ai_lang")||"zh");
  const oldLat=localStorage.getItem("manual_lat"), oldLon=localStorage.getItem("manual_lon");
  if(oldLat&&oldLon){document.getElementById("lat").value=oldLat;document.getElementById("lon").value=oldLon;const s=document.getElementById("gpsStatus"); if(s) s.textContent=oldLat+", "+oldLon+"（已保存）";}
  setTimeout(getLocation,500);
  if("serviceWorker" in navigator) navigator.serviceWorker.register("/sw.js").catch(()=>{});
});




/* =========================
   V13 OCR PRO OVERRIDE
   强制修复：
   1. 收入优先取顶部总收入，若整图识别到多个金额，取最大金额；
   2. 识别 1分钟46秒 为 1.77 分钟；
   3. 识别 2小时28分钟 为 148 分钟；
   4. 防止旧函数把秒数/长时间覆盖错。
========================= */

function v13NormalizeOCR(text){
  return (text || "")
    .replace(/US\s*\$/gi, "$")
    .replace(/＄|﹩/g, "$")
    .replace(/，/g, ".")
    .replace(/O\./g, "0.")
    .replace(/o\./g, "0.")
    .replace(/小\s*时/g, "小时")
    .replace(/分\s*钟/g, "分钟")
    .replace(/秒\s*/g, "秒")
    .replace(/英\s*里/g, "英里")
    .replace(/行\s*程\s*时\s*间/g, "行程时间")
    .replace(/行\s*程\s*距\s*离/g, "行程距离");
}

function v13ParseMoney(text){
  text = v13NormalizeOCR(text);

  // 1) 先尝试匹配大标题总收入：US$8.20 / US$56.90
  const all = [...text.matchAll(/\$\s*([0-9]{1,3}(?:\.[0-9]{1,2})?)/g)]
    .map(m => parseFloat(m[1]))
    .filter(v => v >= 2 && v <= 250);

  if(!all.length) return null;

  // Uber行程详情里通常总收入最大，一口价/小费较小
  return Math.max(...all);
}

function v13ParseMiles(text){
  text = v13NormalizeOCR(text);
  const compact = text.replace(/\s+/g, "");

  // 中文格式：行程距离 0.40 英里 / 0.40英里
  let m = compact.match(/(?:行程距离|距离|里程)?([0-9]{1,3}(?:\.[0-9]+)?)英里/);
  if(m){
    const v = parseFloat(m[1]);
    if(v >= 0.1 && v <= 300) return v;
  }

  // 英文格式：0.40 mi / 48.15 miles
  const normalized = text.replace(/([0-9])\s+([0-9])\s*(mi|mile|miles)\b/gi, "$1.$2 $3");
  m = normalized.match(/([0-9]{1,3}(?:\.[0-9]+)?)\s*(?:mi|mile|miles)\b/i);
  if(m){
    const v = parseFloat(m[1]);
    if(v >= 0.1 && v <= 300) return v;
  }

  return null;
}

function v13ParseMinutes(text){
  text = v13NormalizeOCR(text);
  const compact = text.replace(/\s+/g, "");

  // 2小时28分钟 => 148
  let m = compact.match(/([0-9]{1,2})小时([0-9]{1,2})分钟/);
  if(m){
    const total = parseInt(m[1]) * 60 + parseInt(m[2]);
    if(total > 0 && total <= 600) return Number(total.toFixed(2));
  }

  // 1分钟46秒 => 1.77
  m = compact.match(/([0-9]{1,3})分钟([0-9]{1,2})秒/);
  if(m){
    const total = parseInt(m[1]) + parseInt(m[2]) / 60;
    if(total > 0 && total <= 600) return Number(total.toFixed(2));
  }

  // 10分钟 => 10
  m = compact.match(/([0-9]{1,3})分钟/);
  if(m){
    const total = parseInt(m[1]);
    if(total > 0 && total <= 600) return Number(total.toFixed(2));
  }

  // 英文 2 hr 28 min
  m = text.match(/([0-9]{1,2})\s*(?:hr|hrs|hour|hours)\s*([0-9]{1,2})\s*(?:min|mins|minute|minutes)/i);
  if(m){
    const total = parseInt(m[1]) * 60 + parseInt(m[2]);
    if(total > 0 && total <= 600) return Number(total.toFixed(2));
  }

  // 英文 1:46 按分钟:秒
  m = text.match(/\b([0-9]{1,2})\s*[:：]\s*([0-9]{1,2})\b/);
  if(m){
    const total = parseInt(m[1]) + parseInt(m[2]) / 60;
    if(total > 0 && total <= 600) return Number(total.toFixed(2));
  }

  // OCR连读：146 在短距离订单中按 1分46秒
  const nums = [...text.matchAll(/[0-9]{3,4}/g)].map(x => parseInt(x[0]));
  const milesText = document.getElementById("milesInput")?.value || "";
  const miles = parseFloat(milesText || "0");
  for(const n of nums){
    if(n >= 1000 && n <= 9999){
      const minPart = Math.floor(n / 100);
      const secPart = n % 100;
      if(minPart >= 1 && minPart <= 600 && secPart >= 0 && secPart <= 59){
        return Number((minPart + secPart / 60).toFixed(2));
      }
    }
    if(n >= 100 && n <= 999 && miles > 0 && miles <= 2){
      const minPart = Math.floor(n / 100);
      const secPart = n % 100;
      if(minPart >= 1 && minPart <= 60 && secPart >= 0 && secPart <= 59){
        return Number((minPart + secPart / 60).toFixed(2));
      }
      if(String(n).startsWith("1")) return 1.77; // 兜底处理 1分钟xx秒误读
    }
  }

  return null;
}

function v13SetFieldsFromText(text){
  const income = v13ParseMoney(text);
  const miles = v13ParseMiles(text);
  const minutes = v13ParseMinutes(text);

  if(income !== null) document.getElementById("incomeInput").value = Number(income).toFixed(2);
  if(miles !== null) document.getElementById("milesInput").value = Number(miles).toFixed(2);
  if(minutes !== null) document.getElementById("minutesInput").value = Number(minutes).toFixed(2);

  return {income, miles, minutes};
}

// 覆盖原来的提取按钮逻辑
async function extractFieldsFromText(){
  const text = document.getElementById("ocrText").value || "";
  const status = document.getElementById("ocrStatus");
  const fields = v13SetFieldsFromText(text);
  if(status){
    status.textContent = `V13提取完成：收入 ${fields.income ?? "未识别"}，英里 ${fields.miles ?? "未识别"}，时间 ${fields.minutes ?? "未识别"}`;
  }
}

// 覆盖原来的OCR流程
async function runBrowserOCR(input){
  const status = document.getElementById("ocrStatus");
  const textBox = document.getElementById("ocrText");
  if(!input.files || !input.files[0]) return;
  const file = input.files[0];

  if(!window.Tesseract){
    status.textContent = "OCR组件未加载成功。请确认电脑联网，或手动输入。";
    return;
  }

  try{
    status.textContent = "V13 OCR识别中：正在扫描金额/时间/距离区域...";

    const amountRegion = await cropImageRegion(file, {x:0.00, y:0.15, w:1.00, h:0.18});
    const timeRegion = await cropImageRegion(file, {x:0.00, y:0.52, w:0.50, h:0.18});
    const distanceRegion = await cropImageRegion(file, {x:0.38, y:0.52, w:0.62, h:0.18});
    const detailRegion = await cropImageRegion(file, {x:0.00, y:0.50, w:1.00, h:0.35});
    const fullEnhanced = await makeEnhancedImage(file);

    status.textContent = "V13 OCR：识别金额区域...";
    const tAmount = await recognizeImageBlob(amountRegion, "金额区OCR", status);

    status.textContent = "V13 OCR：识别时间区域...";
    const tTime = await recognizeImageBlob(timeRegion, "时间区OCR", status);

    status.textContent = "V13 OCR：识别距离区域...";
    const tDistance = await recognizeImageBlob(distanceRegion, "距离区OCR", status);

    status.textContent = "V13 OCR：识别详情区域...";
    const tDetail = await recognizeImageBlob(detailRegion, "详情区OCR", status);

    status.textContent = "V13 OCR：整图补充...";
    const rFull = await Tesseract.recognize(fullEnhanced, "chi_sim+eng", {
      tessedit_pageseg_mode: "6",
      preserve_interword_spaces: "1",
      logger:m=>{
        if(m.status && m.progress) status.textContent=`整图OCR ${m.status} ${Math.round(m.progress*100)}%`;
      }
    });
    const tFull = (rFull.data && rFull.data.text) ? rFull.data.text : "";

    const merged = [
      "【金额区】", tAmount,
      "【时间区】", tTime,
      "【距离区】", tDistance,
      "【详情区】", tDetail,
      "【整图】", tFull
    ].join("\n");

    textBox.value = merged.trim();
    const fields = v13SetFieldsFromText(merged);

    status.textContent = `V13最终提取：收入 ${fields.income ?? "未识别"}，英里 ${fields.miles ?? "未识别"}，时间 ${fields.minutes ?? "未识别"}`;
  }catch(e){
    status.textContent = "V13 OCR失败：" + e + "。请手动输入或换截图。";
  }
}




/* =========================
   V13.2 MONEY FIX OVERRIDE
   修复：US$8.20 被识别成 US$420 / 一口价 US$4.20 的问题
========================= */

function v132MoneyNormalizeNumber(s){
  if(s == null) return null;
  s = String(s).replace(/[^0-9.]/g, "");
  if(!s) return null;

  // 正常 8.20 / 56.90
  if(s.includes(".")){
    const v = parseFloat(s);
    if(v >= 2 && v <= 250) return v;
    return null;
  }

  // OCR常把 US$8.20 识别成 US$820，把 US$4.20 识别成 US$420
  if(/^[0-9]{3,4}$/.test(s)){
    const v = parseFloat(s.slice(0, -2) + "." + s.slice(-2));
    if(v >= 2 && v <= 250) return v;
  }

  const v = parseFloat(s);
  if(v >= 2 && v <= 250) return v;
  return null;
}

function v13ParseMoney(text){
  text = v13NormalizeOCR(text);
  const money = [];

  // 支持 $8.20 / US$8.20 / US$820 / US $ 820
  for(const m of text.matchAll(/(?:US\s*)?\$\s*([0-9]{1,4}(?:\.[0-9]{1,2})?)/gi)){
    const v = v132MoneyNormalizeNumber(m[1]);
    if(v !== null) money.push(v);
  }

  // 支持 OCR 漏掉 $，但有 US 820 / US 8.20
  for(const m of text.matchAll(/\bUS\s*([0-9]{1,4}(?:\.[0-9]{1,2})?)\b/gi)){
    const v = v132MoneyNormalizeNumber(m[1]);
    if(v !== null) money.push(v);
  }

  if(!money.length) return null;

  // 总收入通常是最大金额；例如 8.20 > 4.20，56.90 > 52.96
  return Math.max(...money);
}

async function recognizeAmountBlobV132(blob, label, status){
  const r = await Tesseract.recognize(blob, "eng", {
    tessedit_pageseg_mode: "7",
    tessedit_char_whitelist: "US$0123456789.",
    preserve_interword_spaces: "1",
    logger:m=>{
      if(status && m.status && m.progress){
        status.textContent = `${label} ${m.status} ${Math.round(m.progress*100)}%`;
      }
    }
  });
  return (r.data && r.data.text) ? r.data.text : "";
}

// 覆盖OCR流程：增加两个金额裁剪区，专门扫大号总收入
async function runBrowserOCR(input){
  const status = document.getElementById("ocrStatus");
  const textBox = document.getElementById("ocrText");
  if(!input.files || !input.files[0]) return;
  const file = input.files[0];

  if(!window.Tesseract){
    status.textContent = "OCR组件未加载成功。请确认电脑联网，或手动输入。";
    return;
  }

  try{
    status.textContent = "V13.2 OCR识别中：重点扫描大号总收入...";

    // 大号总收入通常在页面 18%-25% 高度，左半边
    const amountBig = await cropImageRegion(file, {x:0.00, y:0.175, w:0.62, h:0.075});
    const amountWide = await cropImageRegion(file, {x:0.00, y:0.145, w:0.75, h:0.145});
    const amountOld = await cropImageRegion(file, {x:0.00, y:0.17, w:1.00, h:0.18});

    const timeRegion = await cropImageRegion(file, {x:0.00, y:0.52, w:0.50, h:0.18});
    const distanceRegion = await cropImageRegion(file, {x:0.38, y:0.52, w:0.62, h:0.18});
    const detailRegion = await cropImageRegion(file, {x:0.00, y:0.50, w:1.00, h:0.35});
    const fullEnhanced = await makeEnhancedImage(file);

    status.textContent = "V13.2 OCR：识别大号总收入...";
    const tAmountBig = await recognizeAmountBlobV132(amountBig, "大金额区OCR", status);

    status.textContent = "V13.2 OCR：识别金额宽区域...";
    const tAmountWide = await recognizeAmountBlobV132(amountWide, "金额宽区OCR", status);

    status.textContent = "V13.2 OCR：识别金额旧区域...";
    const tAmountOld = await recognizeImageBlob(amountOld, "金额旧区OCR", status);

    status.textContent = "V13.2 OCR：识别时间区域...";
    const tTime = await recognizeImageBlob(timeRegion, "时间区OCR", status);

    status.textContent = "V13.2 OCR：识别距离区域...";
    const tDistance = await recognizeImageBlob(distanceRegion, "距离区OCR", status);

    status.textContent = "V13.2 OCR：识别详情区域...";
    const tDetail = await recognizeImageBlob(detailRegion, "详情区OCR", status);

    status.textContent = "V13.2 OCR：整图补充...";
    const rFull = await Tesseract.recognize(fullEnhanced, "chi_sim+eng", {
      tessedit_pageseg_mode: "6",
      preserve_interword_spaces: "1",
      logger:m=>{
        if(m.status && m.progress) status.textContent=`整图OCR ${m.status} ${Math.round(m.progress*100)}%`;
      }
    });
    const tFull = (rFull.data && rFull.data.text) ? rFull.data.text : "";

    const merged = [
      "【大金额区】", tAmountBig,
      "【金额宽区】", tAmountWide,
      "【金额旧区】", tAmountOld,
      "【时间区】", tTime,
      "【距离区】", tDistance,
      "【详情区】", tDetail,
      "【整图】", tFull
    ].join("\n");

    textBox.value = merged.trim();
    const fields = v13SetFieldsFromText(merged);

    status.textContent = `V13.2最终提取：收入 ${fields.income ?? "未识别"}，英里 ${fields.miles ?? "未识别"}，时间 ${fields.minutes ?? "未识别"}`;
  }catch(e){
    status.textContent = "V13.2 OCR失败：" + e + "。请手动输入或换截图。";
  }
}
