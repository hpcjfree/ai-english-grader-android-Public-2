from __future__ import annotations

import base64
import io
import json
import os
import re
import sqlite3
import threading
import uuid
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from urllib.parse import urlparse

import requests
from flask import Flask, jsonify, request, send_from_directory
from PIL import Image, ImageDraw, ImageOps, UnidentifiedImageError

BASE: Path | None = None
WEB: Path | None = None
UPLOAD: Path | None = None
DB_PATH: Path | None = None
_server_started = False
_server_lock = threading.Lock()

SCHEMA = """
PRAGMA journal_mode=WAL;
CREATE TABLE IF NOT EXISTS settings (
  id INTEGER PRIMARY KEY CHECK(id=1), provider TEXT NOT NULL, base_url TEXT NOT NULL,
  api_key TEXT NOT NULL DEFAULT '', model TEXT NOT NULL, temperature REAL NOT NULL DEFAULT 0.2,
  max_tokens INTEGER NOT NULL DEFAULT 4000, request_timeout INTEGER NOT NULL DEFAULT 120,
  proxy_mode TEXT NOT NULL DEFAULT 'direct', proxy_url TEXT NOT NULL DEFAULT '',
  created_at TEXT NOT NULL, updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS schemes (
  id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT NOT NULL, rubric_text TEXT NOT NULL DEFAULT '',
  strictness TEXT NOT NULL DEFAULT 'default', allow_zero INTEGER NOT NULL DEFAULT 1,
  rubric_adherence INTEGER NOT NULL DEFAULT 8, extra_requirements TEXT NOT NULL DEFAULT '',
  created_at TEXT NOT NULL, updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS styles (
  id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT NOT NULL, full_score REAL NOT NULL,
  summary_json TEXT NOT NULL DEFAULT '{}', status TEXT NOT NULL DEFAULT 'learning',
  created_at TEXT NOT NULL, updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS style_examples (
  id INTEGER PRIMARY KEY AUTOINCREMENT, style_id INTEGER NOT NULL, image_path TEXT NOT NULL,
  user_score REAL NOT NULL, ai_observation TEXT NOT NULL DEFAULT '', created_at TEXT NOT NULL,
  FOREIGN KEY(style_id) REFERENCES styles(id) ON DELETE CASCADE
);
CREATE TABLE IF NOT EXISTS groups (
  id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT NOT NULL, question_text TEXT NOT NULL DEFAULT '',
  sample_text TEXT NOT NULL DEFAULT '', scheme_id INTEGER, style_id INTEGER,
  status TEXT NOT NULL DEFAULT 'draft', created_at TEXT NOT NULL, ended_at TEXT,
  FOREIGN KEY(scheme_id) REFERENCES schemes(id), FOREIGN KEY(style_id) REFERENCES styles(id)
);
CREATE TABLE IF NOT EXISTS group_source_images (
  id INTEGER PRIMARY KEY AUTOINCREMENT, group_id INTEGER NOT NULL, kind TEXT NOT NULL,
  image_path TEXT NOT NULL, created_at TEXT NOT NULL,
  FOREIGN KEY(group_id) REFERENCES groups(id) ON DELETE CASCADE
);
CREATE TABLE IF NOT EXISTS submissions (
  id INTEGER PRIMARY KEY AUTOINCREMENT, group_id INTEGER NOT NULL,
  image_paths_json TEXT NOT NULL, result_json TEXT NOT NULL, prompt_tokens INTEGER NOT NULL DEFAULT 0,
  completion_tokens INTEGER NOT NULL DEFAULT 0, total_tokens INTEGER NOT NULL DEFAULT 0,
  created_at TEXT NOT NULL,
  FOREIGN KEY(group_id) REFERENCES groups(id) ON DELETE CASCADE
);
CREATE TABLE IF NOT EXISTS usage_log (
  id INTEGER PRIMARY KEY AUTOINCREMENT, action TEXT NOT NULL, model TEXT NOT NULL,
  prompt_tokens INTEGER NOT NULL DEFAULT 0, completion_tokens INTEGER NOT NULL DEFAULT 0,
  total_tokens INTEGER NOT NULL DEFAULT 0, created_at TEXT NOT NULL
);
"""


def now():
    return datetime.now().astimezone().isoformat(timespec="seconds")


@contextmanager
def connect():
    assert DB_PATH is not None
    con = sqlite3.connect(DB_PATH, timeout=20)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA foreign_keys=ON")
    try:
        yield con
        con.commit()
    finally:
        con.close()


def rows(sql, params=()):
    with connect() as con:
        return [dict(x) for x in con.execute(sql, params).fetchall()]


def row(sql, params=()):
    with connect() as con:
        r = con.execute(sql, params).fetchone()
        return dict(r) if r else None


def execute(sql, params=()):
    with connect() as con:
        cur = con.execute(sql, params)
        return cur.lastrowid


def _ensure_column(con, table, column, definition):
    cols = {r[1] for r in con.execute(f"PRAGMA table_info({table})").fetchall()}
    if column not in cols:
        con.execute(f"ALTER TABLE {table} ADD COLUMN {column} {definition}")


def init_db():
    with connect() as con:
        con.executescript(SCHEMA)
        _ensure_column(con, "settings", "proxy_mode", "TEXT NOT NULL DEFAULT 'direct'")
        _ensure_column(con, "settings", "proxy_url", "TEXT NOT NULL DEFAULT ''")
        if not con.execute("SELECT id FROM settings WHERE id=1").fetchone():
            t = now()
            con.execute(
                "INSERT INTO settings(id,provider,base_url,api_key,model,temperature,max_tokens,request_timeout,proxy_mode,proxy_url,created_at,updated_at) VALUES(1,?,?,?,?,?,?,?,?,?,?,?)",
                ("DeepSeek", "https://api.deepseek.com", "", "deepseek-flash", 0.2, 4000, 120, "direct", "", t, t),
            )
        if con.execute("SELECT COUNT(*) FROM schemes").fetchone()[0] == 0:
            t = now()
            con.execute(
                "INSERT INTO schemes(name,rubric_text,strictness,allow_zero,rubric_adherence,extra_requirements,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?)",
                ("默认方案", "", "default", 1, 8, "优先指出影响表达准确性和得分的修改；不要过度改写学生原意。", t, t),
            )


def api_error(message, status=400):
    return jsonify({"detail": str(message)}), status


def save_upload(storage, prefix="img"):
    assert UPLOAD is not None
    raw = storage.read()
    if not raw:
        raise RuntimeError("上传的图片为空，请重新拍照或选择文件")
    if len(raw) > 80 * 1024 * 1024:
        raise RuntimeError("单张原图超过 80 MiB，请降低相机分辨率后重试")
    try:
        with Image.open(io.BytesIO(raw)) as opened:
            opened.seek(0)
            image = ImageOps.exif_transpose(opened).copy()
    except UnidentifiedImageError as e:
        raise RuntimeError("无法识别这张图片。Android 版会自动把相机/相册图片转换为 JPEG；请重新选择图片。") from e
    except Exception as e:
        raise RuntimeError(f"图片读取失败：{e}") from e

    max_side = 3200
    if max(image.size) > max_side:
        image.thumbnail((max_side, max_side), Image.Resampling.LANCZOS)
    if image.mode in ("RGBA", "LA") or "transparency" in image.info:
        rgba = image.convert("RGBA")
        bg = Image.new("RGB", rgba.size, "white")
        bg.paste(rgba, mask=rgba.getchannel("A"))
        image = bg
    elif image.mode != "RGB":
        image = image.convert("RGB")
    name = f"{prefix}_{uuid.uuid4().hex}.jpg"
    p = UPLOAD / name
    image.save(p, format="JPEG", quality=92, optimize=True, progressive=True)
    image.close()
    return str(p), "/uploads/" + name


def _settings():
    s = row("SELECT * FROM settings WHERE id=1")
    if not s or not s.get("api_key"):
        raise RuntimeError("请先在设置中填写 API Key")
    return s


def _img_part(path: str, detail="original"):
    data = base64.b64encode(Path(path).read_bytes()).decode("ascii")
    return {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{data}", "detail": detail}}


def _parse_json(text: str):
    text = (text or "").strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*", "", text)
        text = re.sub(r"\s*```$", "", text)
    try:
        return json.loads(text)
    except Exception:
        m = re.search(r"\{.*\}", text, re.S)
        if not m:
            raise RuntimeError("模型没有返回可解析 JSON：" + text[:500])
        return json.loads(m.group(0))


def _normalize_proxy_url(value: str):
    value = (value or "").strip()
    if not value:
        return ""
    if value.lower().startswith("socks://"):
        value = "socks5://" + value[8:]
    parsed = urlparse(value)
    if parsed.scheme.lower() not in {"http", "https", "socks5", "socks5h"}:
        raise RuntimeError("代理地址仅支持 http://、https://、socks5:// 或 socks5h://")
    if not parsed.hostname:
        raise RuntimeError("代理地址格式无效，例如：http://192.168.1.2:7890")
    return value


def _system_proxy():
    for key in ("HTTPS_PROXY", "https_proxy", "ALL_PROXY", "all_proxy", "HTTP_PROXY", "http_proxy"):
        if os.environ.get(key):
            return _normalize_proxy_url(os.environ[key])
    return ""


def proxy_info(settings=None):
    s = settings or row("SELECT * FROM settings WHERE id=1") or {}
    mode = (s.get("proxy_mode") or "direct").lower()
    if mode == "direct":
        return {"mode": "direct", "effective_proxy": "", "description": "直连（忽略系统代理）"}
    if mode == "system":
        proxy = _system_proxy()
        return {"mode": "system", "effective_proxy": proxy, "description": f"系统代理：{proxy}" if proxy else "系统代理模式（当前未检测到代理环境变量）"}
    if mode == "custom":
        proxy = _normalize_proxy_url(s.get("proxy_url") or "")
        return {"mode": "custom", "effective_proxy": proxy, "description": f"自定义代理：{proxy}" if proxy else "自定义代理（尚未填写地址）"}
    raise RuntimeError("未知代理模式，请重新保存设置")


def _is_deepseek(s):
    return "deepseek" in (s.get("provider") or "").lower() or "api.deepseek.com" in (s.get("base_url") or "").lower()


def _session_for(s):
    sess = requests.Session()
    mode = (s.get("proxy_mode") or "direct").lower()
    if mode == "direct":
        sess.trust_env = False
    elif mode == "system":
        sess.trust_env = True
    elif mode == "custom":
        sess.trust_env = False
        proxy = _normalize_proxy_url(s.get("proxy_url") or "")
        if not proxy:
            raise RuntimeError("自定义代理模式下请填写代理地址")
        sess.proxies.update({"http": proxy, "https": proxy})
    return sess


def _extract_api_error(resp):
    detail = ""
    try:
        data = resp.json()
        err = data.get("error") if isinstance(data, dict) else None
        if isinstance(err, dict):
            detail = str(err.get("message") or err.get("code") or err.get("type") or err)
        elif err:
            detail = str(err)
        elif isinstance(data, dict):
            detail = str(data.get("message") or data.get("detail") or data)
        else:
            detail = str(data)
    except Exception:
        detail = (resp.text or "").strip()
    detail = re.sub(r"\s+", " ", detail).strip()[:1200]
    return f"模型 API 返回 {resp.status_code}：{detail or resp.reason or '未知错误'}"


def _validate_visual_model(s, content):
    has_image = isinstance(content, list) and any(isinstance(x, dict) and x.get("type") == "image_url" for x in content)
    if has_image and _is_deepseek(s) and (s.get("model") or "").strip() != "deepseek-flash":
        raise RuntimeError(f"当前 DeepSeek 视觉请求需要模型 deepseek-flash；你保存的是 {(s.get('model') or '空')}。请在设置中改为 deepseek-flash。")


def chat(content, action, system, json_mode=False):
    s = _settings()
    _validate_visual_model(s, content)
    payload = {
        "model": s["model"],
        "messages": [{"role": "system", "content": system}, {"role": "user", "content": content}],
        "temperature": float(s["temperature"]),
        "max_tokens": int(s["max_tokens"]),
    }
    if _is_deepseek(s):
        payload["thinking"] = {"type": "disabled"}
    if json_mode:
        payload["response_format"] = {"type": "json_object"}
    headers = {"Authorization": f"Bearer {s['api_key']}", "Content-Type": "application/json"}
    url = s["base_url"].rstrip("/") + "/chat/completions"
    sess = _session_for(s)
    try:
        try:
            r = sess.post(url, headers=headers, json=payload, timeout=float(s["request_timeout"]))
            if r.status_code >= 400 and json_mode and r.status_code in (400, 422):
                fallback = dict(payload)
                fallback.pop("response_format", None)
                r = sess.post(url, headers=headers, json=fallback, timeout=float(s["request_timeout"]))
            if r.status_code >= 400:
                raise RuntimeError(_extract_api_error(r))
            data = r.json()
        except requests.exceptions.ProxyError as e:
            raise RuntimeError(f"代理连接失败：{e}。请检查设置中的网络与代理。") from e
        except requests.exceptions.Timeout as e:
            raise RuntimeError(f"模型 API 请求超时。当前超时设置为 {s['request_timeout']} 秒。") from e
        except requests.exceptions.ConnectionError as e:
            raise RuntimeError(f"无法连接模型 API：{e}。请确认手机已联网。") from e
    finally:
        sess.close()

    usage = data.get("usage") or {}
    pt = int(usage.get("prompt_tokens") or usage.get("input_tokens") or 0)
    ct = int(usage.get("completion_tokens") or usage.get("output_tokens") or 0)
    tt = int(usage.get("total_tokens") or (pt + ct))
    execute("INSERT INTO usage_log(action,model,prompt_tokens,completion_tokens,total_tokens,created_at) VALUES(?,?,?,?,?,?)",
            (action, s["model"], pt, ct, tt, now()))
    try:
        text = data["choices"][0]["message"].get("content") or ""
    except Exception as e:
        raise RuntimeError("模型 API 返回成功，但响应结构不是兼容的 Chat Completions 格式。") from e
    return text, {"prompt_tokens": pt, "completion_tokens": ct, "total_tokens": tt}


def extract_text(paths, label="材料"):
    parts = [{"type": "text", "text": f"请准确提取这些图片中的{label}文字。多张图按顺序合并。保留英文原文、标点、段落和题目要求；不要点评，不要改写。只返回提取后的纯文本。"}]
    parts += [_img_part(p) for p in paths]
    return chat(parts, "ocr", "你是严谨的英语教学材料OCR助手。")


def grade(paths, question, sample, scheme, style):
    strict_map = {"strict": "偏严格", "default": "默认", "lenient": "偏宽松"}
    style_text = json.dumps(style.get("summary_json", {}), ensure_ascii=False) if style else "无已学习风格"
    rubric = scheme.get("rubric_text", "") if scheme else ""
    prompt = f'''你正在批改一份英语作文。先识别学生作文的可见行，并从上到下按 L1、L2……编号，再批改。
题目：{question or '未提供'}
范文：{sample or '未提供'}
评分标准：{rubric or '未提供，按常规英语作文评分'}
给分倾向：{strict_map.get((scheme or {}).get('strictness'), '默认')}
允许0分：{'是' if (scheme or {}).get('allow_zero', 1) else '否'}
遵守评分标准程度：{(scheme or {}).get('rubric_adherence', 8)}/10
其他要求：{(scheme or {}).get('extra_requirements', '') or '无'}
已学习批改风格：{style_text}

必须只返回 JSON，结构：
{{
 "score": 数字,
 "full_score": 数字或null,
 "short_review": "非常简短、1-2句",
 "language_issues": [{{"line":"L3","severity":3,"original":"...","issue":"...","suggestion":"..."}}],
 "improvements": [{{"line":"L5","original":"...","suggestion":"...","reason":"..."}}],
 "recognized_text": "按原行保留的作文文本",
 "confidence": 0到1
}}
severity：3=严重（明显影响理解/核心语法），2=中等，1=轻微。language_issues 必须 severity 从高到低排列。同一错误不要重复。improvements 只写评分标准/用户要求范围内值得修改的内容，不要把整篇重写。'''
    parts = [{"type": "text", "text": prompt}] + [_img_part(p) for p in paths]
    text, usage = chat(parts, "grade", "你是经验丰富、稳定、一致的英语作文阅卷教师。必须忠实依据给定评分标准；看不清时明确降低confidence，不要臆造。请输出 JSON。", True)
    return _parse_json(text), usage


def learn_style(style_row, examples):
    parts = [{"type": "text", "text": f'''根据以下老师人工打分样例，归纳可复用的评分风格。满分为 {style_row['full_score']}。
请只返回 JSON：{{"summary":"...","score_tendency":"...","error_tolerance":"...","strengths_rewarded":[...],"penalties":[...],"calibration_notes":[...]}}。
重点学习“同等质量作文老师通常给多少分”以及对语法、内容、结构、词汇的容忍度，不要记住学生个人身份。'''}]
    for i, e in enumerate(examples, 1):
        parts.append({"type": "text", "text": f"样例 {i}：老师给分 {e['user_score']} / {style_row['full_score']}"})
        parts.append(_img_part(e["image_path"]))
    text, usage = chat(parts, "learn_style", "你是英语阅卷标定助手，负责从人工评分样例中提炼稳定评分风格。只输出 JSON。", True)
    return _parse_json(text), usage


def test_vision():
    assert UPLOAD is not None
    p = UPLOAD / ("vision_test_" + uuid.uuid4().hex + ".jpg")
    im = Image.new("RGB", (640, 360), "white")
    d = ImageDraw.Draw(im)
    d.rectangle((45, 45, 595, 315), outline="black", width=4)
    d.text((85, 150), "VISION TEST 9449", fill="black")
    im.save(p, "JPEG", quality=90)
    try:
        parts = [{"type": "text", "text": "读取图片中的英文测试文字，只回复你看到的文字。"}, _img_part(str(p))]
        return chat(parts, "test_vision", "你是视觉模型连通性测试助手。")
    finally:
        try: p.unlink()
        except Exception: pass


def create_app():
    assert WEB is not None and UPLOAD is not None
    app = Flask(__name__, static_folder=None)
    app.config["MAX_CONTENT_LENGTH"] = 180 * 1024 * 1024

    @app.errorhandler(413)
    def too_large(_):
        return api_error("一次上传的数据过大，请减少图片数量或降低图片分辨率", 413)

    @app.get("/")
    def home(): return send_from_directory(WEB, "index.html")

    @app.get("/static/<path:name>")
    def static_file(name): return send_from_directory(WEB / "static", name)

    @app.get("/uploads/<path:name>")
    def upload_file(name): return send_from_directory(UPLOAD, name)

    @app.get("/api/device")
    def device(): return jsonify({"device": "mobile"})

    @app.get("/api/settings")
    def get_settings():
        s = row("SELECT * FROM settings WHERE id=1") or {}
        s["api_key"] = "********" if s.get("api_key") else ""
        try: s["proxy_status"] = proxy_info(s)
        except Exception as e: s["proxy_status"] = {"mode": s.get("proxy_mode", "direct"), "effective_proxy": "", "description": str(e)}
        return jsonify(s)

    @app.put("/api/settings")
    def put_settings():
        try:
            x = request.get_json(force=True) or {}
            old = row("SELECT * FROM settings WHERE id=1") or {}
            key = old.get("api_key", "") if x.get("api_key") in (None, "", "********") else x.get("api_key", "")
            mode = x.get("proxy_mode", "direct")
            if mode not in ("direct", "system", "custom"): raise RuntimeError("代理模式无效")
            if mode == "custom" and not (x.get("proxy_url") or "").strip(): raise RuntimeError("自定义代理模式下请填写代理地址")
            with connect() as con:
                con.execute("UPDATE settings SET provider=?,base_url=?,api_key=?,model=?,temperature=?,max_tokens=?,request_timeout=?,proxy_mode=?,proxy_url=?,updated_at=? WHERE id=1",
                    (x.get("provider", "DeepSeek"), (x.get("base_url") or "https://api.deepseek.com").rstrip("/"), key, x.get("model", "deepseek-flash"), float(x.get("temperature", .2)), int(x.get("max_tokens", 4000)), int(x.get("request_timeout", 120)), mode, (x.get("proxy_url") or "").strip(), now()))
            return jsonify({"ok": True})
        except Exception as e: return api_error(e)

    @app.post("/api/settings/test")
    def settings_test():
        try:
            text, usage = test_vision(); return jsonify({"ok": True, "reply": text, "usage": usage})
        except Exception as e: return api_error(e)

    @app.get("/api/schemes")
    def list_schemes(): return jsonify(rows("SELECT * FROM schemes ORDER BY id DESC"))

    @app.post("/api/schemes")
    def create_scheme():
        try:
            x = request.get_json(force=True) or {}; t = now()
            i = execute("INSERT INTO schemes(name,rubric_text,strictness,allow_zero,rubric_adherence,extra_requirements,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?)",
                (x.get("name", ""), x.get("rubric_text", ""), x.get("strictness", "default"), int(bool(x.get("allow_zero", True))), int(x.get("rubric_adherence", 8)), x.get("extra_requirements", ""), t, t))
            return jsonify(row("SELECT * FROM schemes WHERE id=?", (i,)))
        except Exception as e: return api_error(e)

    @app.put("/api/schemes/<int:sid>")
    def update_scheme(sid):
        try:
            x = request.get_json(force=True) or {}
            with connect() as con:
                con.execute("UPDATE schemes SET name=?,rubric_text=?,strictness=?,allow_zero=?,rubric_adherence=?,extra_requirements=?,updated_at=? WHERE id=?",
                    (x.get("name", ""), x.get("rubric_text", ""), x.get("strictness", "default"), int(bool(x.get("allow_zero", True))), int(x.get("rubric_adherence", 8)), x.get("extra_requirements", ""), now(), sid))
            return jsonify({"ok": True})
        except Exception as e: return api_error(e)

    @app.delete("/api/schemes/<int:sid>")
    def delete_scheme(sid):
        with connect() as con: con.execute("DELETE FROM schemes WHERE id=?", (sid,))
        return jsonify({"ok": True})

    @app.post("/api/ocr")
    def ocr():
        try:
            files = request.files.getlist("files")
            if not files: raise RuntimeError("请先选择图片")
            paths = [save_upload(f, "ocr")[0] for f in files]
            text, usage = extract_text(paths, request.form.get("label", "材料"))
            return jsonify({"text": text, "usage": usage})
        except Exception as e: return api_error(e)

    @app.get("/api/groups")
    def groups(): return jsonify(rows("SELECT * FROM groups ORDER BY id DESC"))

    @app.post("/api/groups")
    def make_group():
        try:
            x = request.get_json(force=True) or {}
            i = execute("INSERT INTO groups(name,question_text,sample_text,scheme_id,style_id,status,created_at) VALUES(?,?,?,?,?,'active',?)",
                (x.get("name", ""), x.get("question_text", ""), x.get("sample_text", ""), x.get("scheme_id"), x.get("style_id"), now()))
            return jsonify(row("SELECT * FROM groups WHERE id=?", (i,)))
        except Exception as e: return api_error(e)

    @app.put("/api/groups/<int:gid>")
    def edit_group(gid):
        try:
            x = request.get_json(force=True) or {}
            with connect() as con:
                con.execute("UPDATE groups SET name=?,question_text=?,sample_text=?,scheme_id=?,style_id=? WHERE id=?",
                    (x.get("name", ""), x.get("question_text", ""), x.get("sample_text", ""), x.get("scheme_id"), x.get("style_id"), gid))
            return jsonify({"ok": True})
        except Exception as e: return api_error(e)

    @app.post("/api/groups/<int:gid>/end")
    def end_group(gid):
        with connect() as con: con.execute("UPDATE groups SET status='ended', ended_at=? WHERE id=?", (now(), gid))
        return jsonify({"ok": True})

    @app.get("/api/groups/<int:gid>")
    def get_group(gid):
        g = row("SELECT * FROM groups WHERE id=?", (gid,))
        if not g: return api_error("批改组不存在", 404)
        g["submissions"] = rows("SELECT * FROM submissions WHERE group_id=? ORDER BY id", (gid,))
        for s in g["submissions"]:
            s["result"] = json.loads(s["result_json"]); s["image_paths"] = json.loads(s["image_paths_json"])
        return jsonify(g)

    @app.post("/api/groups/<int:gid>/grade")
    def grade_submission(gid):
        try:
            g = row("SELECT * FROM groups WHERE id=?", (gid,))
            if not g: return api_error("批改组不存在", 404)
            scheme = row("SELECT * FROM schemes WHERE id=?", (g["scheme_id"],)) if g.get("scheme_id") else None
            style = row("SELECT * FROM styles WHERE id=?", (g["style_id"],)) if g.get("style_id") else None
            if style:
                try: style["summary_json"] = json.loads(style["summary_json"])
                except Exception: style["summary_json"] = {}
            files = request.files.getlist("files")
            if not files: raise RuntimeError("请拍照或选择作文图片")
            disk, urls = [], []
            for f in files:
                p, u = save_upload(f, f"g{gid}"); disk.append(p); urls.append(u)
            result, usage = grade(disk, g.get("question_text", ""), g.get("sample_text", ""), scheme, style)
            i = execute("INSERT INTO submissions(group_id,image_paths_json,result_json,prompt_tokens,completion_tokens,total_tokens,created_at) VALUES(?,?,?,?,?,?,?)",
                (gid, json.dumps(urls, ensure_ascii=False), json.dumps(result, ensure_ascii=False), usage["prompt_tokens"], usage["completion_tokens"], usage["total_tokens"], now()))
            return jsonify({"id": i, "result": result, "image_paths": urls, "usage": usage})
        except Exception as e: return api_error(e)

    @app.get("/api/styles")
    def list_styles():
        out = rows("SELECT * FROM styles ORDER BY id DESC")
        for s in out:
            try: s["summary_json"] = json.loads(s["summary_json"])
            except Exception: s["summary_json"] = {}
            s["example_count"] = row("SELECT COUNT(*) c FROM style_examples WHERE style_id=?", (s["id"],))["c"]
        return jsonify(out)

    @app.post("/api/styles")
    def create_style():
        try:
            x = request.get_json(force=True) or {}; t = now()
            i = execute("INSERT INTO styles(name,full_score,summary_json,status,created_at,updated_at) VALUES(?,?,'{}','learning',?,?)",
                (x.get("name", ""), float(x.get("full_score", 20)), t, t))
            return jsonify(row("SELECT * FROM styles WHERE id=?", (i,)))
        except Exception as e: return api_error(e)

    @app.post("/api/styles/<int:sid>/examples")
    def style_example(sid):
        try:
            f = request.files.get("file")
            if not f: raise RuntimeError("请选择作文图片")
            score = float(request.form.get("score"))
            p, u = save_upload(f, f"style{sid}")
            i = execute("INSERT INTO style_examples(style_id,image_path,user_score,created_at) VALUES(?,?,?,?)", (sid, p, score, now()))
            return jsonify({"id": i, "image_path": u})
        except Exception as e: return api_error(e)

    @app.post("/api/styles/<int:sid>/finalize")
    def finalize_style(sid):
        try:
            st = row("SELECT * FROM styles WHERE id=?", (sid,)); ex = rows("SELECT * FROM style_examples WHERE style_id=? ORDER BY id", (sid,))
            if not st or not ex: raise RuntimeError("至少需要1个评分样例")
            summary, usage = learn_style(st, ex)
            with connect() as con:
                con.execute("UPDATE styles SET summary_json=?,status='ready',updated_at=? WHERE id=?", (json.dumps(summary, ensure_ascii=False), now(), sid))
            return jsonify({"summary": summary, "usage": usage})
        except Exception as e: return api_error(e)

    @app.get("/api/history")
    def history():
        out = rows("SELECT s.*, g.name group_name FROM submissions s JOIN groups g ON g.id=s.group_id ORDER BY s.id DESC")
        for s in out:
            imgs = json.loads(s["image_paths_json"]); res = json.loads(s["result_json"])
            s["preview"] = imgs[0] if imgs else None; s["result"] = res; s.pop("result_json", None); s.pop("image_paths_json", None)
        return jsonify(out)

    @app.get("/api/history/<int:sid>")
    def history_one(sid):
        s = row("SELECT s.*, g.name group_name, g.question_text FROM submissions s JOIN groups g ON g.id=s.group_id WHERE s.id=?", (sid,))
        if not s: return api_error("记录不存在", 404)
        s["image_paths"] = json.loads(s["image_paths_json"]); s["result"] = json.loads(s["result_json"])
        return jsonify(s)

    @app.get("/api/usage")
    def usage():
        total = row("SELECT COALESCE(SUM(prompt_tokens),0) prompt_tokens,COALESCE(SUM(completion_tokens),0) completion_tokens,COALESCE(SUM(total_tokens),0) total_tokens,COUNT(*) calls FROM usage_log")
        total["recent"] = rows("SELECT * FROM usage_log ORDER BY id DESC LIMIT 20")
        return jsonify(total)

    return app


def _serve():
    app = create_app()
    app.run(host="127.0.0.1", port=9449, debug=False, use_reloader=False, threaded=True)


def start_server(base_dir: str):
    global BASE, WEB, UPLOAD, DB_PATH, _server_started
    with _server_lock:
        if _server_started:
            return True
        BASE = Path(base_dir)
        WEB = BASE / "web"
        UPLOAD = BASE / "uploads"
        UPLOAD.mkdir(parents=True, exist_ok=True)
        DB_PATH = BASE / "grader.db"
        init_db()
        t = threading.Thread(target=_serve, name="grader-http", daemon=True)
        t.start()
        _server_started = True
        return True
