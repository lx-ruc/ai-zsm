#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""火山引擎·人像融合 3.6 (face_swap3_6) 命令行工具(纯标准库,零依赖)

用法:
  facefusion-volc.py swap 素材图1 [素材图2 素材图3] 模板图 [-o out.png] [选项]
  facefusion-volc.py ping    # 只验证密钥签名/服务开通,不消耗有效调用

密钥(~/.volc-key,两行: AccessKeyID / SecretAccessKey,权限600)或环境变量
VOLC_ACCESS_KEY / VOLC_SECRET_KEY。文档:
https://docs.volcengine.com/docs/Largemodelofimagegeneration/PortraitFusion36LatestVersion
"""
import argparse, base64, datetime, hashlib, hmac, json, os, sys, time, urllib.error, urllib.request

HOST, REGION, SERVICE = "visual.volcengineapi.com", "cn-north-1", "cv"
ACTION, VERSION = "CVProcess", "2022-08-31"
REQ_KEY = "face_swap3_6"
KEY_FILE = os.path.expanduser("~/.volc-key")
MAX_IMG_BYTES = 5 * 1024 * 1024
IMG_MAGICS = (b"\xff\xd8\xff", b"\x89PNG", b"BM")  # jpg / png / bmp(文档仅支持三种)


def die(msg):
    print(f"✗ {msg}", file=sys.stderr)
    sys.exit(1)


def load_credentials():
    ak = os.environ.get("VOLC_ACCESS_KEY", "")
    sk = os.environ.get("VOLC_SECRET_KEY", "")
    if ak and sk:
        return ak, sk
    if not os.path.isfile(KEY_FILE):
        die(f"未找到密钥: 写 {KEY_FILE}(两行 AKID/SECRET) 或 export VOLC_ACCESS_KEY/VOLC_SECRET_KEY")
    lines = [l.strip() for l in open(KEY_FILE, encoding="utf-8") if l.strip()]
    if len(lines) < 2:
        die(f"{KEY_FILE} 需要 AKID、SECRET 两行")
    return lines[0], lines[1]


def _hmac(key, msg):
    return hmac.new(key, msg.encode(), hashlib.sha256).digest()


def sign(ak, sk, body, x_date, short_date):
    """火山 V4 签名: HMAC-SHA256 Credential={AK}/{Date}/{Region}/{Service}/request"""
    payload_hash = hashlib.sha256(body).hexdigest()
    query = f"Action={ACTION}&Version={VERSION}"
    canon_headers = f"content-type:application/json\nhost:{HOST}\nx-date:{x_date}\n"
    signed_headers = "content-type;host;x-date"
    canonical = "\n".join(["POST", "/", query, canon_headers, signed_headers, payload_hash])
    credential_scope = f"{short_date}/{REGION}/{SERVICE}/request"
    sts = "\n".join(["HMAC-SHA256", x_date, credential_scope,
                     hashlib.sha256(canonical.encode()).hexdigest()])
    key = _hmac(sk.encode(), short_date)
    for step in (REGION, SERVICE, "request"):
        key = _hmac(key, step)
    sig = hmac.new(key, sts.encode(), hashlib.sha256).hexdigest()
    return (f"HMAC-SHA256 Credential={ak}/{credential_scope}, "
            f"SignedHeaders={signed_headers}, Signature={sig}")


def call_api(payload, timeout=120):
    """返回 (http_status, resp_dict, 耗时秒); 网络级错误直接退出"""
    ak, sk = load_credentials()
    body = json.dumps(payload, ensure_ascii=False).encode()
    now = datetime.datetime.now(datetime.timezone.utc)
    x_date, short = now.strftime("%Y%m%dT%H%M%SZ"), now.strftime("%Y%m%d")
    req = urllib.request.Request(
        f"https://{HOST}/?{query_string()}", data=body, method="POST",
        headers={"Content-Type": "application/json", "X-Date": x_date,
                 "Authorization": sign(ak, sk, body, x_date, short)})
    t0 = time.time()
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status, json.loads(r.read()), time.time() - t0
    except urllib.error.HTTPError as e:
        raw = e.read()
        try:
            return e.code, json.loads(raw), time.time() - t0
        except Exception:
            die(f"HTTP {e.code} 非JSON响应: {raw[:300]!r}")
    except Exception as e:
        die(f"网络错误: {e}")


def query_string():
    return f"Action={ACTION}&Version={VERSION}"


def api_error(status, resp):
    """无错返回 None; 兼容三种信封: 网关 ResponseMetadata.Error / CV 顶层 code / 算法层 algorithm_base_resp"""
    meta = resp.get("ResponseMetadata", {})
    err = meta.get("Error") or {}
    if err:
        return f"HTTP {status} [{err.get('Code', '?')}] {err.get('Message', '')} (RequestId={meta.get('RequestId', '?')})"
    code = resp.get("code", resp.get("status"))  # 10000=成功
    if code is not None and code != 10000:
        return f"[{code}] {resp.get('message', '')} (request_id={resp.get('request_id', '?')})"
    alg = ((resp.get("data") or {}).get("algorithm_base_resp")) or {}
    if alg.get("status_code") not in (None, 0):
        return f"[算法层{alg.get('status_code')}] {alg.get('status_message', '')}"
    if status != 200:
        return f"HTTP {status} {json.dumps(resp, ensure_ascii=False)[:300]}"
    return None


def read_image(path):
    if not os.path.isfile(path):
        die(f"文件不存在: {path}")
    data = open(path, "rb").read()
    if len(data) > MAX_IMG_BYTES:
        die(f"{path} 超过 5MB 上限")
    if looks_like_webp_or_heic(data):
        conv = convert_to_jpeg(data)
        if conv is None:
            die(f"{path}: webp/heic 自动转 jpg 失败,请先另存为 jpg")
        data = conv
    data, derr = fit_dimensions(data)
    if derr:
        die(f"{path}: {derr}")
    if not data.startswith(IMG_MAGICS):
        die(f"{path} 不是 jpg/png/bmp(webp/heic 自动转码)")
    return base64.b64encode(data).decode()


def cmd_swap(a):
    sources = [read_image(p) for p in a.sources]
    template = read_image(a.template)
    payload = {"req_key": REQ_KEY, "binary_data_base64": sources + [template],
               "return_url": bool(a.url)}
    if a.face_type:
        payload["face_type"] = a.face_type
    if a.similarity is not None:
        payload["source_similarity"] = str(a.similarity)  # 文档类型为字符串
    if a.location or a.template_location:
        payload["merge_infos"] = [{"location": a.location or 1,
                                   "template_location": a.template_location or 1}]
    if a.logo:
        payload["logo_info"] = {"add_logo": True, "position": 0, "language": 0}
    status, resp, dt = call_api(payload)
    err = api_error(status, resp)
    if err:
        die(err)
    # 实测信封在顶层 data,文档写的是 Result,两者都兼容
    result = resp.get("data") or resp.get("Result") or {}
    if a.url and result.get("image_urls"):
        for u in result["image_urls"]:
            print(f"✓ 图链(24h有效): {u}")
        return
    bins = result.get("binary_data_base64") or []
    if not bins:
        die(f"无返回图, Result: {json.dumps(result, ensure_ascii=False)[:400]}")
    out = a.output or f"/tmp/faceswap_{time.strftime('%Y%m%d_%H%M%S')}.png"
    for i, b in enumerate(bins):
        path = out if len(bins) == 1 else out.replace(".", f"_{i+1}.", 1)
        with open(path, "wb") as f:
            f.write(base64.b64decode(b))
        print(f"✓ 已保存 {path} (耗时 {dt:.1f}s)")


def cmd_ping(a):
    # 1x1 PNG: 签名/开通通过即可,业务层必然报错(图太小/无人脸)——那正是链路通的证明
    tiny = ("iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJ"
            "AAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg==")
    status, resp, _ = call_api({"req_key": REQ_KEY, "binary_data_base64": [tiny]})
    err = api_error(status, resp)
    low = (err or "").lower()
    if any(k in low for k in ("signature", "签名", "accesskey", "invalidauthorization", "unauthorized")):
        die(f"认证/签名失败: {err}")
    if "access denied" in low or "50400" in low:
        print(f"✓ 密钥与签名已通过网关校验; 但服务未开通: {err}")
        print("  → 控制台「AI 能力」开通「人像融合(3.6)」后重试本命令")
        return
    print("✓ 链路通: 密钥签名有效、服务已开通" + (f"; 业务层返回(预期): {err}" if err else ""))


# ---- 本地网页界面(web 子命令):上传素材图+模板图 → 一键换脸(全参数暴露) ----
import threading
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

PAGE_HTML = """<!doctype html>
<html lang="zh"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>人像融合 3.6</title><style>
:root{--bg:#f5f6f8;--card:#fff;--line:#e5e7eb;--tx:#1f2328;--sub:#6b7280;--acc:#2563eb}
*{box-sizing:border-box}input[type=file]{display:none}
body{margin:0;min-height:100vh;display:flex;justify-content:center;padding:32px 16px;background:var(--bg);color:var(--tx);font:14px/1.6 -apple-system,"PingFang SC","Microsoft YaHei",sans-serif}
.wrap{width:100%;max-width:920px}h1{font-size:20px;margin:0 0 4px}
.sub{color:var(--sub);margin:0 0 24px;font-size:13px}
.grid{display:grid;grid-template-columns:1fr 1fr;gap:16px}
@media(max-width:640px){.grid{grid-template-columns:1fr}}
.card{background:var(--card);border:1px solid var(--line);border-radius:12px;padding:16px}
.card h2{font-size:14px;margin:0 0 10px;display:flex;justify-content:space-between;align-items:center}
.tag{font-weight:400;color:var(--sub);font-size:12px}
.drop{border:1.5px dashed #cbd2dc;border-radius:10px;min-height:180px;display:flex;align-items:center;justify-content:center;cursor:pointer;transition:.15s;text-align:center;color:var(--sub);padding:12px;flex-wrap:wrap;gap:8px}
.drop:hover,.drop.over{border-color:var(--acc);color:var(--acc)}
.drop img{max-width:100%;max-height:200px;border-radius:8px;display:block}
.thumbs{display:flex;gap:8px;flex-wrap:wrap}
.thumb{position:relative}
.thumb img{width:86px;height:86px;object-fit:cover;border-radius:8px;display:block}
.thumb .n{position:absolute;top:2px;left:2px;background:rgba(0,0,0,.55);color:#fff;font-size:11px;border-radius:6px;padding:0 6px}
.thumb .x{position:absolute;top:2px;right:2px;background:rgba(0,0,0,.55);color:#fff;border:0;border-radius:50%;width:18px;height:18px;cursor:pointer;font-size:11px;line-height:1}
.addtile{width:86px;height:86px;border:1.5px dashed #cbd2dc;border-radius:8px;display:flex;align-items:center;justify-content:center;font-size:22px;color:#9aa3af;cursor:pointer}
.pdesc{color:var(--sub);font-size:12px;margin:6px 2px 0}
.opts{display:flex;gap:20px;align-items:center;margin:16px 2px;flex-wrap:wrap;color:var(--sub);font-size:13px}
.opts label{display:flex;align-items:center;gap:8px}input[type=range]{accent-color:var(--acc)}
button.go{width:100%;padding:13px;border:0;border-radius:10px;background:var(--acc);color:#fff;font-size:15px;font-weight:600;cursor:pointer}
button.go:hover{filter:brightness(1.06)}button.go:disabled{opacity:.55;cursor:wait}
details.adv{background:var(--card);border:1px solid var(--line);border-radius:12px;padding:12px 16px;margin-top:16px}
details.adv summary{cursor:pointer;font-weight:600;font-size:14px;color:var(--tx)}
.frow{display:flex;gap:16px;flex-wrap:wrap;margin-top:14px}
.frow>div{flex:1;min-width:200px}
.frow label.top{display:block;font-size:13px;color:var(--tx);font-weight:600;margin-bottom:6px}
select,input[type=number],input[type=text]{width:100%;padding:8px 10px;border:1px solid var(--line);border-radius:8px;font-size:13px;background:#fff;color:var(--tx)}
.sub-opts{display:flex;gap:12px;flex-wrap:wrap;margin-top:8px}
.sub-opts>div{flex:1;min-width:140px}
.sub-opts .lb{font-size:12px;color:var(--sub);margin-bottom:4px}
.err{background:#fef2f2;border:1px solid #fecaca;color:#b91c1c;border-radius:10px;padding:10px 14px;margin-top:14px;display:none;font-size:13px;white-space:pre-wrap}
#resultCard{display:none;margin-top:16px}#resultImg{max-width:100%;border-radius:8px;display:block}
.meta{color:var(--sub);font-size:12px;margin-top:8px;display:flex;justify-content:space-between;align-items:center}
.meta a{color:var(--acc);text-decoration:none;font-size:13px}
table{width:100%;border-collapse:collapse;margin-top:10px;font-size:12.5px}
th,td{border:1px solid var(--line);padding:6px 10px;text-align:left;vertical-align:top}
th{background:#f9fafb;color:var(--sub);font-weight:600}
td b{color:var(--acc)}
</style></head><body><div class="wrap">
<input type="file" id="fileSrc" multiple accept="image/jpeg,image/png,image/bmp,image/webp,image/heic,image/heif">
<input type="file" id="fileTpl" accept="image/jpeg,image/png,image/bmp,image/webp,image/heic,image/heif">
<h1>人像融合 3.6</h1>
<p class="sub">火山引擎 face_swap3_6 · 素材图的脸 → 模板图的人 · 仅监听本机 · 名人/政要照片会被上游风控拦截(50411)</p>
<div class="grid">
<div class="card"><h2>素材图（提供人脸，1~3 张）<span class="tag">jpg/png/bmp/webp/heic 自动转码 · 每张≤5MB · 超4096px自动缩小</span></h2>
<div class="drop" id="dropSrc"><span>点击或拖入图片（可多选）</span></div>
<p class="pdesc">多张素材 = 多个「脸来源」。单脸换脸只传 1 张；多素材多目标的高级映射用命令行 merge_infos。</p></div>
<div class="card"><h2>模板图（被换脸）<span class="tag">jpg/png/bmp/webp/heic 自动转码 · ≤5MB · 超4096px自动缩小</span></h2>
<div class="drop" id="dropTpl"><span>点击或拖入图片</span></div>
<p class="pdesc">换脸发生在模板图的人脸上；建议正脸、光线充足、人脸边长≥200px。</p></div>
</div>
<div class="opts">
<label>相似度 <input type="range" id="sim" min="0" max="1" step="0.1" value="1"> <b id="simV">1.0</b></label>
<span style="font-size:12px">0=完全保持模板原脸 · 1=最大程度像素材脸（默认1）</span>
</div>
<button class="go" id="go" disabled>换 脸</button>
<div class="err" id="err"></div>
<details class="adv"><summary>高级参数（多人脸 / 水印）</summary>
<div class="frow">
<div><label class="top">人脸定位方式 face_type</label>
<select id="faceType"><option value="">area（默认·按面积从大到小）</option><option value="l2r">l2r（从左到右）</option><option value="t2b">t2b（从上到下）</option></select>
<p class="pdesc">图里有多张脸时，序号怎么数：area=脸大的排前，l2r=靠左的排前，t2b=靠上的排前。</p></div>
<div><label class="top">素材图人脸序号 location</label>
<input type="number" id="loc" min="1" max="9" value="1">
<p class="pdesc">用素材图里第几张脸（按上面的定位方式数）。单人脸保持 1。</p></div>
<div><label class="top">模板图人脸序号 template_location</label>
<input type="number" id="tloc" min="1" max="9" value="1">
<p class="pdesc">换模板图里第几张脸。多人合照想换右边的人，就改这里。</p></div>
</div>
<div class="frow"><div>
<label class="top"><input type="checkbox" id="logo" style="margin-right:6px">添加水印 logo_info</label>
<div class="sub-opts">
<div><div class="lb">位置</div><select id="logoPos"><option value="0">右下角（默认）</option><option value="1">左下角</option><option value="2">左上角</option><option value="3">右上角</option></select></div>
<div><div class="lb">语言</div><select id="logoLang"><option value="0">中文「AI生成」</option><option value="1">英文 Generated by AI</option></select></div>
<div><div class="lb">自定义文字（≤50字，留空用默认）</div><input type="text" id="logoText" maxlength="50" placeholder="例：慧沐引擎 Demo"></div>
</div>
<p class="pdesc">合规提示：按《AI生成合成内容标识办法》，对外发布的生成图应带标识；上游还会写入隐式 aigc 元数据。</p>
</div></div>
</details>
<details class="adv"><summary>参数速查表</summary>
<table>
<tr><th>参数</th><th>取值</th><th>作用</th></tr>
<tr><td><b>source_similarity</b></td><td>字符串 "0"~"1"，默认 "1"</td><td>结果脸的倾向：0=与原模板一致，1=最大相似素材脸。想「像但保留模板气质」用 0.6~0.8</td></tr>
<tr><td><b>face_type</b></td><td>area / l2r / t2b</td><td>多人脸图的序号规则（面积 / 左→右 / 上→下）</td></tr>
<tr><td><b>merge_infos.location</b></td><td>≥1</td><td>素材图第几张脸</td></tr>
<tr><td><b>merge_infos.template_location</b></td><td>≥1</td><td>模板图第几张脸</td></tr>
<tr><td><b>logo_info</b></td><td>position 0~3 / language 0~1 / 文字≤50字</td><td>明水印位置、语言、自定义文案</td></tr>
<tr><td><b>素材图数量</b></td><td>1~3 张（换脸图在前，模板图在后）</td><td>多个脸来源；最多同时换三张脸（完整映射走 CLI --location/--template-location 或 merge_infos JSON）</td></tr>
</table>
</details>
<div class="card" id="resultCard">
<h2>结果<span class="tag" id="elapsed"></span></h2>
<img id="resultImg" alt="换脸结果">
<div class="meta"><span id="note">结果为 JPEG · 免费额度 1000 次</span><a id="dl" download="faceswap.jpg" href="#">下载图片</a></div>
</div>
</div><script>
var $=function(id){return document.getElementById(id)};
var state={srcs:[],tpl:null};
function hint(m){var e=$('err');e.style.display='block';e.textContent=m;setTimeout(function(){e.style.display='none'},3500)}
function check(){$('go').disabled=!(state.srcs.length>=1&&state.tpl)}
function okType(t){return ['image/jpeg','image/png','image/bmp','image/webp','image/heic','image/heif'].indexOf(t)>=0}
function readOne(file,cb){
 if(!okType(file.type))return hint('仅支持 jpg / png / bmp / webp / heic(自动转jpg)');
 if(file.size>5*1024*1024)return hint('单图不能超过 5MB');
 var r=new FileReader();
 r.onload=function(){cb(r.result.split(',')[1],r.result)};
 r.readAsDataURL(file);
}
function renderSrcs(){
 var d=$('dropSrc');d.innerHTML='';
 state.srcs.forEach(function(s,i){
  var t=document.createElement('div');t.className='thumb';
  t.innerHTML='<span class="n">'+(i+1)+'</span><img src="'+s.url+'"><button class="x">×</button>';
  t.querySelector('.x').onclick=function(e){e.stopPropagation();state.srcs.splice(i,1);renderSrcs();check()};
  d.appendChild(t);
 });
 if(state.srcs.length<3){
  var a=document.createElement('div');a.className='addtile';a.textContent='+';a.title='添加素材图';
  a.onclick=function(e){e.stopPropagation();$('fileSrc').click()};
  d.appendChild(a);
 }
 if(!state.srcs.length){var s=document.createElement('span');s.textContent='点击或拖入图片（可多选）';d.appendChild(s)}
}
function bindTpl(){
 var d=$('dropTpl'),f=$('fileTpl');
 d.onclick=function(){f.click()};
 f.onchange=function(){if(f.files[0])readOne(f.files[0],function(b,u){state.tpl=b;d.innerHTML='';var i=new Image();i.src=u;d.appendChild(i);check()});f.value=''};
 d.ondragover=function(e){e.preventDefault();d.classList.add('over')};
 d.ondragleave=function(){d.classList.remove('over')};
 d.ondrop=function(e){e.preventDefault();d.classList.remove('over');if(e.dataTransfer.files[0])readOne(e.dataTransfer.files[0],function(b,u){state.tpl=b;d.innerHTML='';var i=new Image();i.src=u;d.appendChild(i);check()})};
}
$('fileSrc').onchange=function(){
 var fs=[].slice.call(this.files).slice(0,3-state.srcs.length);
 fs.forEach(function(f){readOne(f,function(b,u){state.srcs.push({b64:b,url:u});renderSrcs();check()})});
 this.value='';
};
var ds=$('dropSrc');
ds.onclick=function(){$('fileSrc').click()};
ds.ondragover=function(e){e.preventDefault();ds.classList.add('over')};
ds.ondragleave=function(){ds.classList.remove('over')};
ds.ondrop=function(e){e.preventDefault();ds.classList.remove('over');
 var fs=[].slice.call(e.dataTransfer.files).slice(0,3-state.srcs.length);
 fs.forEach(function(f){readOne(f,function(b,u){state.srcs.push({b64:b,url:u});renderSrcs();check()})});
};
bindTpl();
renderSrcs();
$('sim').oninput=function(e){$('simV').textContent=(+e.target.value).toFixed(1)};
$('go').onclick=function(){
 var b=$('go');b.disabled=true;b.textContent='融合中…（约2~10s）';$('err').style.display='none';
 fetch('/api/swap',{method:'POST',headers:{'Content-Type':'application/json'},
  body:JSON.stringify({sources:state.srcs.map(function(s){return s.b64}),template:state.tpl,
   similarity:$('sim').value,face_type:$('faceType').value,
   location:+$('loc').value||1,template_location:+$('tloc').value||1,
   logo:{add:$('logo').checked,position:+$('logoPos').value,language:+$('logoLang').value,text:$('logoText').value}})})
 .then(function(r){return r.json()}).then(function(j){
  if(!j.ok)throw new Error(j.error||'未知错误');
  $('resultCard').style.display='block';
  $('resultImg').src='data:image/jpeg;base64,'+j.image;
  $('dl').href=$('resultImg').src;$('elapsed').textContent='耗时 '+j.elapsed;
  $('resultCard').scrollIntoView({behavior:'smooth'});
 }).catch(function(e){var el=$('err');el.style.display='block';el.textContent='✗ '+e.message})
 .then(function(){b.disabled=false;b.textContent='换 脸';check()});
};
</script></body></html>"""


def looks_like_webp_or_heic(data):
    return (data[:4] == b"RIFF" and data[8:12] == b"WEBP") or data[4:8] == b"ftyp"


MAX_DIM = 4096  # 火山限制: 图片宽、高均不得超过 4096px(最小128)


def sips_transform(data, in_suffix, args):
    """写临时文件执行 sips(args 不含源/目标), 输出为 jpeg bytes; 失败返回 None"""
    import subprocess
    import tempfile
    src = dst = None
    try:
        with tempfile.NamedTemporaryFile(suffix=in_suffix, delete=False) as f:
            f.write(data)
            src = f.name
        dst = src + ".out.jpg"
        subprocess.run(["sips"] + args + [src, "--out", dst],
                       capture_output=True, timeout=25, check=True)
        return open(dst, "rb").read()
    except Exception:
        return None
    finally:
        for path in (src, dst):
            if path:
                try:
                    os.unlink(path)
                except OSError:
                    pass


def image_dims(path):
    """sips 读宽高; 失败返回 (0, 0)"""
    import re
    import subprocess
    try:
        r = subprocess.run(["sips", "-g", "pixelWidth", "-g", "pixelHeight", path],
                           capture_output=True, timeout=10, check=True)
        txt = r.stdout.decode(errors="ignore")
        return (int(re.search(r"pixelWidth: (\d+)", txt).group(1)),
                int(re.search(r"pixelHeight: (\d+)", txt).group(1)))
    except Exception:
        return 0, 0


def convert_to_jpeg(data):
    """webp/heic 用 macOS sips 转 jpeg(火山 API 只收 jpg/png/bmp); 返回 jpeg bytes 或 None"""
    ext = ".webp" if data[8:12] == b"WEBP" else ".heic"
    return sips_transform(data, ext, ["-s", "format", "jpeg"])


def fit_dimensions(data):
    """宽或高超过 4096 时等比缩小(sips -Z); 返回 (bytes, err) —— 未超限原样返回零损耗"""
    import tempfile
    if not data.startswith(IMG_MAGICS):
        return data, None
    src = None
    try:
        with tempfile.NamedTemporaryFile(suffix=".jpg", delete=False) as f:
            f.write(data)
            src = f.name
        w, h = image_dims(src)
        if w == 0 or (w <= MAX_DIM and h <= MAX_DIM):
            return data, None
        out = sips_transform(data, ".jpg", ["-Z", str(MAX_DIM)])
        if out is None:
            return None, "宽或高超过 4096px 且自动缩小失败,请手动缩小后重试"
        return out, None
    finally:
        if src:
            try:
                os.unlink(src)
            except OSError:
                pass


def validate_b64_image(b64s, name):
    """校验并归一化客户端传来的 base64 图(兼容 dataURL 前缀); 返回 (b64|None, 错误|None)"""
    try:
        data = base64.b64decode(b64s.split(",")[-1])
    except Exception:
        return None, f"{name}: base64 解码失败"
    if len(data) > MAX_IMG_BYTES:
        return None, f"{name}: 超过 5MB 上限"
    if looks_like_webp_or_heic(data):
        conv = convert_to_jpeg(data)
        if conv is None:
            return None, f"{name}: webp/heic 自动转 jpg 失败,请先另存为 jpg"
        data = conv
    data, derr = fit_dimensions(data)
    if derr:
        return None, f"{name}: {derr}"
    if not data.startswith(IMG_MAGICS):
        return None, f"{name}: 仅支持 jpg/png/bmp(webp/heic 会自动转码)"
    return base64.b64encode(data).decode(), None


class SwapHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        body = PAGE_HTML.encode()
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_POST(self):
        if self.path != "/api/swap":
            return self._json(404, {"ok": False, "error": "not found"})
        try:
            n = int(self.headers.get("Content-Length") or 0)
            if n > 40 * 1024 * 1024:
                return self._json(413, {"ok": False, "error": "请求体过大"})
            req = json.loads(self.rfile.read(n) or b"{}")
        except Exception:
            return self._json(400, {"ok": False, "error": "请求体不是合法 JSON"})
        raw_srcs = req.get("sources") or ([req["source"]] if req.get("source") else [])
        if not (1 <= len(raw_srcs) <= 3):
            return self._json(400, {"ok": False, "error": "素材图须 1~3 张"})
        srcs = []
        for i, s in enumerate(raw_srcs, 1):
            v, err = validate_b64_image(s, f"素材图{i}")
            if err:
                return self._json(400, {"ok": False, "error": err})
            srcs.append(v)
        tpl, err = validate_b64_image(req.get("template", ""), "模板图")
        if err:
            return self._json(400, {"ok": False, "error": err})
        payload = {"req_key": REQ_KEY, "binary_data_base64": srcs + [tpl], "return_url": False}
        if "similarity" in req and req["similarity"] != "":
            payload["source_similarity"] = str(req["similarity"])  # API 要求字符串
        ft = req.get("face_type")
        if ft in ("l2r", "t2b", "area"):
            payload["face_type"] = ft
        loc, tloc = int(req.get("location") or 1), int(req.get("template_location") or 1)
        if loc > 1 or tloc > 1:
            payload["merge_infos"] = [{"location": loc, "template_location": tloc}]
        logo = req.get("logo") or {}
        if logo.get("add"):
            info = {"add_logo": True, "position": int(logo.get("position") or 0),
                    "language": int(logo.get("language") or 0)}
            text = (logo.get("text") or "").strip()
            if text:
                info["logo_text_content"] = text[:50]
            payload["logo_info"] = info
        try:
            status, resp, dt = call_api(payload)
        except SystemExit:
            return self._json(502, {"ok": False, "error": "调用上游失败,见服务端日志 /tmp/facefusion-web.log"})
        err = api_error(status, resp)
        if err:
            return self._json(200, {"ok": False, "error": err})
        data = resp.get("data") or resp.get("Result") or {}
        bins = data.get("binary_data_base64") or []
        if not bins:
            return self._json(200, {"ok": False, "error": "上游无返回图(大概率未检出人脸,换正脸高清图试试)"})
        self._json(200, {"ok": True, "image": bins[0], "elapsed": f"{dt:.1f}s"})

    def _json(self, code, obj):
        body = json.dumps(obj, ensure_ascii=False).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, fmt, *args):
        print(f"[{time.strftime('%H:%M:%S')}] {self.address_string()} {fmt % args}", flush=True)


def cmd_web(a):
    srv = ThreadingHTTPServer((a.host, a.port), SwapHandler)
    url = f"http://{a.host}:{a.port}/"
    print(f"✓ 人像融合本地界面: {url} (Ctrl+C 停止)")
    threading.Timer(0.5, lambda: webbrowser.open(url)).start()
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        print("已停止")

def main():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("swap", help="换脸: 素材图(1-3张,脸在前) + 模板图")
    s.add_argument("sources", nargs="+", help="换脸素材图路径,1-3张")
    s.add_argument("template", help="模板图路径,1张")
    s.add_argument("-o", "--output", help="输出图路径(默认 /tmp/faceswap_时间戳.png)")
    s.add_argument("--similarity", type=float, help="人脸相似度 0~1, 越大越像素材脸, 默认1")
    s.add_argument("--face-type", choices=("l2r", "t2b", "area"), help="多人脸时定位方式, 默认 area")
    s.add_argument("--location", type=int, help="素材图人脸序号(从1起,多人脸时用)")
    s.add_argument("--template-location", type=int, help="模板图人脸序号(从1起)")
    s.add_argument("--logo", action="store_true", help="添加'AI生成'水印")
    s.add_argument("--url", action="store_true", help="返回图链(24h)而非文件")
    s.set_defaults(func=cmd_swap)
    g = sub.add_parser("ping", help="验证密钥/签名/服务开通")
    g.set_defaults(func=cmd_ping)
    w = sub.add_parser("web", help="本地网页界面(上传两图→一键换脸)")
    w.add_argument("--host", default="127.0.0.1", help="默认仅本机")
    w.add_argument("--port", type=int, default=8788)
    w.set_defaults(func=cmd_web)
    a = p.parse_args()
    if a.cmd == "swap" and not (1 <= len(a.sources) <= 3):
        die("素材图须 1-3 张")
    a.func(a)


if __name__ == "__main__":
    main()
