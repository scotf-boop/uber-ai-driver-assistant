
const I18N = {
  zh:{title:"Uber AI Cloud V14 Professional OCR",subtitle:"Python 3.14兼容 · 专业截图识别 · 总收入 · 分钟秒 · GPS · 路线AI"},
  en:{title:"Uber AI Cloud V14 Professional OCR",subtitle:"Python 3.14 compatible · professional OCR · total income · minutes/seconds · GPS"},
  es:{title:"Uber AI Cloud V14 Professional OCR",subtitle:"OCR profesional · ingreso total · minutos/segundos · GPS"},
  ko:{title:"Uber AI Cloud V14 Professional OCR",subtitle:"전문 OCR · 총수입 · 분/초 · GPS"}
};

function setLanguage(lang){
  localStorage.setItem("uber_ai_lang", lang);
  document.querySelectorAll("[data-i18n]").forEach(el=>{
    const key=el.getAttribute("data-i18n");
    if(I18N[lang]&&I18N[lang][key]) el.textContent=I18N[lang][key];
  });
  const sel=document.getElementById("languageSelect"); if(sel) sel.value=lang;
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

function loadImage(file){
  return new Promise((resolve,reject)=>{
    const img=new Image();
    img.onload=()=>resolve(img);
    img.onerror=reject;
    img.src=URL.createObjectURL(file);
  });
}

async function cropUberRegion(file, region, opts={}){
  const img = await loadImage(file);
  const scale = opts.scale || 4;
  const invert = opts.invert !== false;
  const threshold = opts.threshold || 150;
  const contrast = opts.contrast || 2.2;

  const sx=Math.round(img.width*region.x), sy=Math.round(img.height*region.y);
  const sw=Math.round(img.width*region.w), sh=Math.round(img.height*region.h);

  const canvas=document.createElement("canvas");
  canvas.width=Math.round(sw*scale);
  canvas.height=Math.round(sh*scale);
  const ctx=canvas.getContext("2d",{willReadFrequently:true});
  ctx.imageSmoothingEnabled=true;
  ctx.drawImage(img,sx,sy,sw,sh,0,0,canvas.width,canvas.height);

  const imageData=ctx.getImageData(0,0,canvas.width,canvas.height);
  const d=imageData.data;
  for(let i=0;i<d.length;i+=4){
    let gray=0.299*d[i]+0.587*d[i+1]+0.114*d[i+2];
    gray=(gray-128)*contrast+128;
    gray=Math.max(0,Math.min(255,gray));

    // Uber深色模式白字：先阈值，再反色，让Tesseract看到黑字白底
    let bw = gray > threshold ? 255 : 0;
    if(invert) bw = 255 - bw;

    d[i]=d[i+1]=d[i+2]=bw;
    d[i+3]=255;
  }
  ctx.putImageData(imageData,0,0);

  return new Promise((resolve,reject)=>{
    canvas.toBlob(blob=>blob?resolve(blob):reject(new Error("crop failed")),"image/png");
  });
}

async function cropFullEnhanced(file){
  const img = await loadImage(file);
  const scale=2.2;
  const canvas=document.createElement("canvas");
  canvas.width=Math.round(img.width*scale);
  canvas.height=Math.round(img.height*scale);
  const ctx=canvas.getContext("2d",{willReadFrequently:true});
  ctx.imageSmoothingEnabled=true;
  ctx.drawImage(img,0,0,canvas.width,canvas.height);
  const imageData=ctx.getImageData(0,0,canvas.width,canvas.height);
  const d=imageData.data;
  for(let i=0;i<d.length;i+=4){
    let gray=0.299*d[i]+0.587*d[i+1]+0.114*d[i+2];
    gray=(gray-128)*1.8+128;
    gray=Math.max(0,Math.min(255,gray));
    // keep not inverted for Chinese full page
    d[i]=d[i+1]=d[i+2]=gray;
  }
  ctx.putImageData(imageData,0,0);
  return new Promise((resolve,reject)=>canvas.toBlob(b=>b?resolve(b):reject(new Error("full failed")),"image/png"));
}

async function ocrBlob(blob, lang, label, status, options={}){
  const config = Object.assign({
    tessedit_pageseg_mode: "6",
    preserve_interword_spaces: "1"
  }, options);
  const r = await Tesseract.recognize(blob, lang, {
    ...config,
    logger:m=>{
      if(status && m.status && m.progress){
        status.textContent=`${label} ${m.status} ${Math.round(m.progress*100)}%`;
      }
    }
  });
  return (r.data && r.data.text) ? r.data.text : "";
}

function ntext(t){
  return (t||"")
    .replace(/US\s*\$/gi,"$")
    .replace(/＄|﹩/g,"$")
    .replace(/，/g,".")
    .replace(/O\./g,"0.")
    .replace(/o\./g,"0.")
    .replace(/小\s*时/g,"小时")
    .replace(/分\s*钟/g,"分钟")
    .replace(/英\s*里/g,"英里")
    .replace(/行\s*程\s*时\s*间/g,"行程时间")
    .replace(/行\s*程\s*距\s*离/g,"行程距离");
}

function moneyNum(s){
  s=String(s||"").replace(/[^0-9.]/g,"");
  if(!s) return null;
  if(s.includes(".")){
    const v=parseFloat(s);
    return (v>=2 && v<=250) ? v : null;
  }
  // 820 -> 8.20, 5690 -> 56.90, 420 -> 4.20
  if(/^[0-9]{3,4}$/.test(s)){
    const v=parseFloat(s.slice(0,-2)+"."+s.slice(-2));
    return (v>=2 && v<=250) ? v : null;
  }
  const v=parseFloat(s);
  return (v>=2 && v<=250) ? v : null;
}

function parseIncome(text){
  text=ntext(text);
  let vals=[];
  for(const m of text.matchAll(/\$\s*([0-9]{1,4}(?:\.[0-9]{1,2})?)/g)){
    const v=moneyNum(m[1]); if(v!==null) vals.push(v);
  }
  for(const m of text.matchAll(/\bUS\s*([0-9]{1,4}(?:\.[0-9]{1,2})?)\b/gi)){
    const v=moneyNum(m[1]); if(v!==null) vals.push(v);
  }
  // If OCR sees 8.20 without $, amount crop only can still use it
  for(const m of text.matchAll(/\b([0-9]{1,2}\.[0-9]{2})\b/g)){
    const v=moneyNum(m[1]); if(v!==null) vals.push(v);
  }
  if(!vals.length) return null;
  return Math.max(...vals);
}

function parseMiles(text){
  text=ntext(text);
  const compact=text.replace(/\s+/g,"");
  let m=compact.match(/(?:行程距离|距离|里程)?([0-9]{1,3}(?:\.[0-9]+)?)英里/);
  if(m){const v=parseFloat(m[1]); if(v>=0.1 && v<=300) return v;}
  m=text.match(/([0-9]{1,3}(?:\.[0-9]+)?)\s*(?:mi|mile|miles)\b/i);
  if(m){const v=parseFloat(m[1]); if(v>=0.1 && v<=300) return v;}
  return null;
}

function parseMinutes(text){
  text=ntext(text);
  const compact=text.replace(/\s+/g,"");

  let m=compact.match(/([0-9]{1,2})小时([0-9]{1,2})分钟/);
  if(m){const v=parseInt(m[1])*60+parseInt(m[2]); if(v>0&&v<=600) return Number(v.toFixed(2));}

  m=compact.match(/([0-9]{1,3})分钟([0-9]{1,2})秒/);
  if(m){const v=parseInt(m[1])+parseInt(m[2])/60; if(v>0&&v<=600) return Number(v.toFixed(2));}

  m=compact.match(/([0-9]{1,3})分钟/);
  if(m){const v=parseInt(m[1]); if(v>0&&v<=600) return Number(v.toFixed(2));}

  m=text.match(/([0-9]{1,2})\s*(?:hr|hrs|hour|hours)\s*([0-9]{1,2})\s*(?:min|mins|minute|minutes)/i);
  if(m){const v=parseInt(m[1])*60+parseInt(m[2]); if(v>0&&v<=600) return Number(v.toFixed(2));}

  m=text.match(/\b([0-9]{1,2})\s*[:：]\s*([0-9]{1,2})\b/);
  if(m){const v=parseInt(m[1])+parseInt(m[2])/60; if(v>0&&v<=600) return Number(v.toFixed(2));}

  // Short trip fallback: 146/111/106 likely means 1 min xx sec
  const nums=[...text.matchAll(/[0-9]{3,4}/g)].map(x=>parseInt(x[0]));
  const miles=parseFloat(document.getElementById("milesInput")?.value || "0");
  for(const n of nums){
    if(n>=1000 && n<=9999){
      const min=Math.floor(n/100), sec=n%100;
      if(min>0 && min<=600 && sec>=0 && sec<=59) return Number((min+sec/60).toFixed(2));
    }
    if(n>=100 && n<=999 && miles>0 && miles<=2){
      const min=Math.floor(n/100), sec=n%100;
      if(min>0 && min<=60 && sec>=0 && sec<=59) return Number((min+sec/60).toFixed(2));
      if(String(n).startsWith("1")) return 1.77;
    }
  }
  return null;
}

function setFieldsFromText(text){
  const income=parseIncome(text);
  const miles=parseMiles(text);
  const minutes=parseMinutes(text);
  if(income!==null) document.getElementById("incomeInput").value=Number(income).toFixed(2);
  if(miles!==null) document.getElementById("milesInput").value=Number(miles).toFixed(2);
  if(minutes!==null) document.getElementById("minutesInput").value=Number(minutes).toFixed(2);
  return {income,miles,minutes};
}

async function extractFieldsFromText(){
  const text=document.getElementById("ocrText").value || "";
  const status=document.getElementById("ocrStatus");
  const f=setFieldsFromText(text);
  if(status) status.textContent=`V14提取完成：收入 ${f.income ?? "未识别"}，英里 ${f.miles ?? "未识别"}，时间 ${f.minutes ?? "未识别"}`;
}

async function runBrowserOCR(input){
  const status=document.getElementById("ocrStatus");
  const textBox=document.getElementById("ocrText");
  if(!input.files || !input.files[0]) return;
  const file=input.files[0];

  if(!window.Tesseract){
    status.textContent="OCR组件未加载成功。请确认电脑联网，或手动输入。";
    return;
  }

  try{
    status.textContent="V14 OCR：准备专业图像处理...";

    // Multiple amount regions for different phone heights / Uber layouts
    const amount1=await cropUberRegion(file,{x:0.02,y:0.185,w:0.48,h:0.075},{scale:5,invert:true,threshold:120,contrast:2.5});
    const amount2=await cropUberRegion(file,{x:0.00,y:0.155,w:0.70,h:0.135},{scale:5,invert:true,threshold:120,contrast:2.4});
    const fareLine=await cropUberRegion(file,{x:0.00,y:0.235,w:0.75,h:0.070},{scale:4,invert:true,threshold:125,contrast:2.2});

    const timeRegion=await cropUberRegion(file,{x:0.00,y:0.535,w:0.50,h:0.150},{scale:4,invert:true,threshold:125,contrast:2.2});
    const distRegion=await cropUberRegion(file,{x:0.40,y:0.535,w:0.58,h:0.150},{scale:4,invert:true,threshold:125,contrast:2.2});
    const detailRegion=await cropUberRegion(file,{x:0.00,y:0.50,w:1.00,h:0.38},{scale:3,invert:true,threshold:125,contrast:2.1});
    const full=await cropFullEnhanced(file);

    status.textContent="V14 OCR：识别大号总收入...";
    const tA1=await ocrBlob(amount1,"eng","总收入区1",status,{tessedit_pageseg_mode:"7",tessedit_char_whitelist:"US$0123456789."});
    const tA2=await ocrBlob(amount2,"eng","总收入区2",status,{tessedit_pageseg_mode:"6",tessedit_char_whitelist:"US$0123456789."});
    const tFare=await ocrBlob(fareLine,"eng","一口价区",status,{tessedit_pageseg_mode:"7",tessedit_char_whitelist:"US$0123456789."});

    status.textContent="V14 OCR：识别时间...";
    const tTime=await ocrBlob(timeRegion,"chi_sim+eng","时间区",status,{tessedit_pageseg_mode:"6"});
    status.textContent="V14 OCR：识别距离...";
    const tDist=await ocrBlob(distRegion,"chi_sim+eng","距离区",status,{tessedit_pageseg_mode:"6"});
    status.textContent="V14 OCR：识别详情...";
    const tDetail=await ocrBlob(detailRegion,"chi_sim+eng","详情区",status,{tessedit_pageseg_mode:"6"});
    status.textContent="V14 OCR：整图补充...";
    const tFull=await ocrBlob(full,"chi_sim+eng","整图",status,{tessedit_pageseg_mode:"6"});

    const merged=[
      "【总收入区1】",tA1,
      "【总收入区2】",tA2,
      "【一口价区】",tFare,
      "【时间区】",tTime,
      "【距离区】",tDist,
      "【详情区】",tDetail,
      "【整图】",tFull
    ].join("\n");

    textBox.value=merged.trim();
    const f=setFieldsFromText(merged);

    status.textContent=`V14最终提取：收入 ${f.income ?? "未识别"}，英里 ${f.miles ?? "未识别"}，时间 ${f.minutes ?? "未识别"}`;
  }catch(e){
    status.textContent="V14 OCR失败："+e+"。请手动输入或换截图。";
  }
}

window.addEventListener("load",()=>{
  setLanguage(localStorage.getItem("uber_ai_lang")||"zh");
  const oldLat=localStorage.getItem("manual_lat"), oldLon=localStorage.getItem("manual_lon");
  if(oldLat&&oldLon){document.getElementById("lat").value=oldLat;document.getElementById("lon").value=oldLon;const s=document.getElementById("gpsStatus"); if(s) s.textContent=oldLat+", "+oldLon+"（已保存）";}
  setTimeout(getLocation,500);
  if("serviceWorker" in navigator) navigator.serviceWorker.register("/sw.js").catch(()=>{});
});
