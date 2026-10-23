"""FastAPI server cho EdgeTool. Chạy qua edgetool.py."""
import base64
import json
import os
import threading
import time
import traceback

import cv2
import numpy as np
from fastapi import Body, FastAPI, HTTPException
from fastapi.responses import FileResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles

from core import autolabel as AL
from core import edges as E
from core.project import DATA_ROOT, Project, RATINGS, atomic_json, clean, find_checked_dir, folder_summary, rect_from_poly

HERE = os.path.dirname(os.path.abspath(__file__))
RECENT = os.path.join(HERE, "recent.json")
app = FastAPI()
app.mount("/static", StaticFiles(directory=os.path.join(HERE, "static")), name="static")


@app.middleware("http")
async def no_cache_static(request, call_next):
    resp = await call_next(request)
    if request.url.path == "/" or request.url.path.startswith("/static/"):
        resp.headers["Cache-Control"] = "no-cache"       # sửa giao diện xong chỉ cần F5
    return resp

S = {"project": None}
JOB = {"kind": None, "running": False, "done": 0, "total": 0, "msg": "", "errors": [], "stop": False, "t0": 0}
_cache, _cache_order = {}, []
_cache_lock = threading.Lock()


def P():
    if S["project"] is None:
        raise HTTPException(400, "Chưa mở folder nào")
    return S["project"]


def ok(o=None):
    return JSONResponse(clean(o if o is not None else {"ok": True}))


def analysis(f):
    """Cache 4 ảnh gần nhất (mỗi ảnh có nhiều map nặng)."""
    p = P()
    path = p.img_path(f)
    key = (path, os.path.getmtime(path))
    with _cache_lock:
        if key in _cache:
            _cache_order.remove(key)
            _cache_order.append(key)
            return _cache[key]
    A = E.Analysis(E.load_image(path))
    with _cache_lock:
        _cache[key] = A
        _cache_order.append(key)
        while len(_cache_order) > 4:
            _cache.pop(_cache_order.pop(0), None)
    return A


def png(img):
    if img.ndim == 3:
        img = cv2.cvtColor(img, cv2.COLOR_RGB2BGR)
    return cv2.imencode(".png", img, [cv2.IMWRITE_PNG_COMPRESSION, 1])[1].tobytes()


def data_url(img):
    return "data:image/png;base64," + base64.b64encode(png(img)).decode()


# ------------------------------------------------------------------ trang
@app.get("/")
def index():
    return FileResponse(os.path.join(HERE, "static", "index.html"))


@app.get("/thumbs/{name}")
def thumb(name: str):
    path = os.path.join(P().thumbs_dir, os.path.basename(name))
    if not os.path.exists(path):
        raise HTTPException(404)
    return FileResponse(path, headers={"Cache-Control": "no-cache"})


# ------------------------------------------------------------------ project
def recent_list():
    try:
        return json.load(open(RECENT, encoding="utf-8"))
    except Exception:
        return []


@app.get("/api/state")
def state():
    p = S["project"]
    return ok({"project": None if p is None else {"folder": p.folder, "checked": p.checked, "n_images": len(p.images()),
                                                    "names": p.names, "prompts": p.state.get("prompts", {}),
                                                    "settings": p.state["settings"]},
               "recent": recent_list(), "layers": E.LAYERS, "job": job_status(),
               "model_types": AL.TYPES, "cfg": {"model_size": E.CFG.MODEL_SIZE, "sweep": E.CFG.IMGSZ_SWEEP}})


@app.post("/api/pick_folder")
def pick_folder():
    return ok({"path": _tk_dialog("dir")})


@app.post("/api/pick_file")
def pick_file():
    return ok({"path": _tk_dialog("file")})


def _tk_dialog(kind):
    import tkinter as tk
    from tkinter import filedialog
    root = tk.Tk()
    root.withdraw()
    root.attributes("-topmost", True)
    try:
        if kind == "dir":
            return filedialog.askdirectory(title="Chọn folder ảnh cần check") or None
        return filedialog.askopenfilename(title="Chọn weights", filetypes=[("Weights", "*.pt *.pth *.onnx *.safetensors"), ("All", "*.*")]) or None
    finally:
        root.destroy()


@app.post("/api/open")
def open_folder(body: dict = Body(...)):
    folder = (body.get("folder") or "").strip().strip('"')
    if not os.path.isdir(folder):
        raise HTTPException(400, f"Không phải folder: {folder}")
    if JOB["running"]:
        raise HTTPException(409, "Đang có tác vụ chạy, hãy dừng trước")
    S["project"] = Project(folder)
    with _cache_lock:
        _cache.clear()
        _cache_order.clear()
    r = [x for x in recent_list() if x != S["project"].folder]
    json.dump([S["project"].folder] + r[:99], open(RECENT, "w", encoding="utf-8"), ensure_ascii=False)
    return state()


@app.get("/api/projects")
def projects():
    """Danh sách thư mục đã từng mở + tóm tắt (bao nhiêu ảnh đã nhãn tự động / thủ công / nhập sẵn, đã đánh giá)."""
    cur = S["project"].folder if S["project"] else None
    rows = []
    for f in recent_list():
        s = folder_summary(f)
        s["is_current"] = (s.get("folder") == cur) if s.get("exists") else (os.path.abspath(f) == cur)
        rows.append(s)
    return ok({"projects": rows, "data_root": DATA_ROOT})


@app.post("/api/project_remove")
def project_remove(body: dict = Body(...)):
    """Xoá 1 thư mục khỏi danh sách 'Thư mục đã check' - chỉ bỏ khỏi danh sách (recent.json),
    KHÔNG xoá dữ liệu đã nhãn trong data-labeled/ (mở lại folder gốc sẽ tìm thấy nhãn cũ như bình thường)."""
    folder = os.path.abspath((body.get("folder") or "").strip())
    r = [x for x in recent_list() if os.path.abspath(x) != folder]
    json.dump(r, open(RECENT, "w", encoding="utf-8"), ensure_ascii=False)
    if S["project"] and S["project"].folder == folder:
        S["project"] = None
        with _cache_lock:
            _cache.clear()
            _cache_order.clear()
    return ok()


@app.post("/api/folder_done")
def folder_done(body: dict = Body(...)):
    """Tích/bỏ tích 1 thư mục trong danh sách 'Thư mục đã check' là đã xong - cờ thủ công, không tự suy từ rating."""
    folder = os.path.abspath((body.get("folder") or "").strip())
    done = bool(body.get("done"))
    p = S["project"]
    if p and p.folder == folder:
        p.state["done"] = done
        p.save_state()
        return ok()
    checked = find_checked_dir(folder)
    if not checked:
        raise HTTPException(400, "Thư mục này chưa có dữ liệu (chưa mở & lưu nhãn lần nào)")
    state_path = os.path.join(checked, "state.json")
    state = json.load(open(state_path, encoding="utf-8")) if os.path.isfile(state_path) else {}
    state["done"] = done
    atomic_json(state_path, state, indent=1)
    return ok()


@app.post("/api/reveal")
def reveal(body: dict = Body(...)):
    """Mở thư mục bằng Windows Explorer - chỉ cho phép thư mục trong data-labeled/ hoặc folder ảnh đã từng mở."""
    path = os.path.abspath((body.get("path") or "").strip())
    allowed = [os.path.normcase(DATA_ROOT)] + [os.path.normcase(os.path.abspath(x)) for x in recent_list()]
    if not os.path.isdir(path) or not any(os.path.normcase(path).startswith(a) for a in allowed):
        raise HTTPException(400, f"Không mở được: {path}")
    os.startfile(path)
    return ok()


@app.post("/api/settings")
def settings(body: dict = Body(...)):
    p = P()
    if "oi_classes" in body:
        p.state["settings"]["oi_classes"] = [s.strip() for s in body["oi_classes"] if s.strip()]
        p._oi = None
        for f in [f for f, e in p.report.items() if not e.get("labels")]:
            del p.report[f]                     # ảnh chưa có nhãn -> scan lại với bộ lọc mới
        p.save_report()
    p.save_state()
    return state()


# ------------------------------------------------------------------ job nền (scan / auto-label hàng loạt)
def job_status():
    j = {k: v for k, v in JOB.items() if k != "stop"}
    j["errors"] = JOB["errors"][-20:]
    if JOB["done"] and JOB["running"]:
        el = time.time() - JOB["t0"]
        j["eta_s"] = el / JOB["done"] * (JOB["total"] - JOB["done"])
    return j


def start_job(kind, items, fn):
    if JOB["running"]:
        raise HTTPException(409, "Đang có tác vụ chạy")
    JOB.update(kind=kind, running=True, done=0, total=len(items), msg="", errors=[], stop=False, t0=time.time())

    def worker():
        p = S["project"]
        try:
            for f in items:
                if JOB["stop"]:
                    JOB["msg"] = "Đã dừng"
                    break
                JOB["msg"] = f
                try:
                    fn(p, f)
                except Exception as e:
                    JOB["errors"].append(f"{f}: {e}")
                    traceback.print_exc()
                JOB["done"] += 1
                if JOB["done"] % 10 == 0:
                    try:
                        p.save_report()
                    except Exception as e:
                        JOB["errors"].append(f"lưu report: {e}")
                if JOB["done"] % 15 == 0:
                    AL.free_vram_cache()        # dọn cache VRAM giữa batch dài, nghi ngờ tràn/phân mảnh gây crash native
        finally:
            try:
                p.save_report()
            except Exception as e:
                JOB["errors"].append(f"lưu report: {e}")
            JOB["running"] = False
            if not JOB["stop"]:
                JOB["msg"] = "Xong"

    threading.Thread(target=worker, daemon=True).start()


@app.post("/api/scan")
def scan(body: dict = Body(default={})):
    p = P()
    files = p.images()
    if not body.get("force"):
        files = [f for f in files if not p.is_fresh(f)]
    start_job("scan", files, lambda p, f: p.analyze(f))
    return ok(job_status())


@app.post("/api/stop")
def stop():
    JOB["stop"] = True
    return ok()


@app.get("/api/job")
def job():
    return ok(job_status())


@app.get("/api/report")
def report():
    p = P()
    rows = []
    for f in p.images():
        e = p.report.get(f) or {"file": f, "pending": True, "errors": [], "labels": []}
        e = dict(e)
        e["rating"] = p.state["ratings"].get(f)
        rows.append(e)
    return ok({"rows": rows, "names": p.names})


@app.post("/api/rate")
def rate(body: dict = Body(...)):
    p = P()
    r = body.get("rating")
    if r not in RATINGS and r is not None:
        raise HTTPException(400, "rating sai")
    p.rate(os.path.basename(body["file"]), r)
    return ok({"file": body["file"], "rating": r})


# ------------------------------------------------------------------ ảnh, nhãn, kiểm tra
@app.get("/api/layer")
def layer(file: str, name: str = "original"):
    A = analysis(file)
    img = A.layer(name)
    if img is None:
        raise HTTPException(404, f"Lớp {name} không có (thiếu model feature?)")
    return Response(png(img), media_type="image/png", headers={"Cache-Control": "max-age=600"})


@app.get("/api/labels")
def get_labels(file: str):
    p = P()
    A = analysis(file)
    labs, errs, src = p.get_labels(file, A.W, A.H)
    if not labs:
        errs.insert(0, "KHÔNG CÓ NHÃN cho ảnh này - hãy vẽ nhãn hoặc kiểm tra file nhãn nguồn")
    return ok({"file": file, "W": A.W, "H": A.H, "labels": labs, "errors": errs, "src": src,
               "proposals": p.get_proposals(file), "rating": p.state["ratings"].get(file), "names": p.names})


@app.post("/api/labels")
def save_labels(body: dict = Body(...)):
    p = P()
    f = os.path.basename(body["file"])
    A = analysis(f)
    labs = []
    for L in body.get("labels", []):
        pts = L["pts"]
        if len(pts) != 4:
            raise HTTPException(400, "Mỗi nhãn phải có đúng 4 điểm")
        if not (0 <= int(L["cls"]) < len(p.names)):
            raise HTTPException(400, f"class {L['cls']} không có trong data.yaml")
        labs.append({"cls": int(L["cls"]), "pts": pts})
    p.write_labels(f, labs, A.W, A.H)
    p.state["label_src"][f] = "tool"
    if "proposals" in body:
        p.set_proposals(f, body["proposals"])
    p.save_state()
    e = p.analyze(f, A)
    p.save_report()
    return ok({"entry": e})


@app.post("/api/inspect")
def inspect(body: dict = Body(...)):
    """Crop xoay theo OBB cho nhiều lớp xử lí + số liệu + quét imgsz + histogram."""
    A = analysis(body["file"])
    pts = np.asarray(body["pts"], np.float32)
    if pts.shape != (4, 2) or cv2.contourArea(pts) < 4:
        raise HTTPException(400, "Nhãn quá nhỏ / không hợp lệ")
    crops = {}
    for name in body.get("layers") or [k for k, _ in E.LAYERS]:
        img = A.layer(name)
        if img is not None:
            crops[name] = data_url(E.rot_crop(img, pts, float(body.get("ctx", 0.15))))
    m = A.metrics(pts)
    mask = E.poly_mask(A.gray.shape, pts)
    hist_in = np.bincount(A.gray[mask], minlength=256).tolist()
    hist_all = np.bincount(A.gray.ravel(), minlength=256).tolist()
    sw = [{"imgsz": s["imgsz"], "w": s["w"], "h": s["h"], "mean": s["mean"], "strong_pct": s["strong_pct"],
           "gray": data_url(s["gray"]), "sobel": data_url(s["sobel"])} for s in A.sweep(pts)]
    return ok({"crops": crops, "metrics": m, "hints": E.hints(m), "hist_in": hist_in, "hist_all": hist_all, "sweep": sw})


@app.get("/api/pixels")
def pixels(file: str, x: int, y: int, n: int = 11):
    A = analysis(file)
    n = max(3, min(n, 21))
    x0 = min(max(x - n // 2, 0), A.W - n)
    y0 = min(max(y - n // 2, 0), A.H - n)
    g = A.gray[y0:y0 + n, x0:x0 + n]
    s = A.mag[y0:y0 + n, x0:x0 + n]
    b = A.bgr[min(max(y, 0), A.H - 1), min(max(x, 0), A.W - 1)]
    return ok({"x0": x0, "y0": y0, "gray": g.tolist(), "sobel": np.round(s).astype(int).tolist(), "ref": A.ref,
               "rgb": [int(b[2]), int(b[1]), int(b[0])]})


@app.post("/api/rectify")
def rectify(body: dict = Body(...)):
    return ok({"pts": rect_from_poly(body["pts"]).tolist()})


# ------------------------------------------------------------------ class (data.yaml) + prompt
@app.post("/api/classes")
def classes(body: dict = Body(...)):
    p = P()
    act = body.get("action")
    try:
        if act == "add":
            p.add_class(body["name"])
        elif act == "rename":
            p.rename_class(int(body["idx"]), body["name"])
        elif act == "prompt":
            p.state.setdefault("prompts", {})[p.names[int(body["idx"])]] = body.get("prompt", "")
            p.save_state()
    except ValueError as e:
        raise HTTPException(400, str(e))
    return ok({"names": p.names, "prompts": p.state.get("prompts", {})})


# ------------------------------------------------------------------ auto-label
@app.get("/api/models")
def models():
    return ok({"models": AL.load_registry(), "types": AL.TYPES})


@app.post("/api/models")
def add_model(body: dict = Body(...)):
    try:
        return ok({"model": AL.add_model(body), "models": AL.load_registry()})
    except ValueError as e:
        raise HTTPException(400, str(e))


@app.delete("/api/models/{mid}")
def del_model(mid: str):
    AL.remove_model(mid)
    return ok({"models": AL.load_registry()})


def build_prompts(p, cls_ids):
    out = []
    for c in cls_ids:
        name = p.names[c]
        txt = (p.state.get("prompts", {}).get(name) or name)
        for t in txt.split(","):
            if t.strip():
                out.append((t.strip(), c))
    return out


DUP_IOU = 0.5   # 2 nhãn cùng class trùng hơn ngưỡng này -> coi là 1 vật, không thêm nhãn mới đè lên


def _autolabel_one(p, f, mid, cls_ids, conf, commit=False, mode="append"):
    """mode (chỉ áp dụng khi commit=True):
       "append"    -> cộng vào nhãn đang có, bỏ qua đề xuất trùng (cùng class, IoU cao) với nhãn đã có
       "overwrite" -> xoá sạch nhãn cũ, ghi nhãn mới hoàn toàn"""
    A = analysis(f)
    props, info = AL.run(mid, A.bgr, build_prompts(p, cls_ids), conf)
    for q in props:
        q["model"] = mid
    if commit:
        existing = p.get_labels(f, A.W, A.H)[0] if mode == "append" else []
        added = [q for q in props if not any(e["cls"] == q["cls"] and E.obb_iou(e["pts"], q["pts"]) > DUP_IOU for e in existing)]
        labels = existing + [{"cls": q["cls"], "pts": q["pts"]} for q in added]
        if added or mode == "overwrite":       # "thêm vào" mà không thêm được box nào -> giữ nguyên file & nguồn nhãn
            p.write_labels(f, labels, A.W, A.H)
            if added:
                p.state["label_src"][f] = f"auto:{mid}"     # có box do model thêm -> cần người duyệt lại
            else:
                p.state["label_src"].pop(f, None)
            p.save_state()
        p.set_proposals(f, [])
        p.analyze(f, A)                         # làm mới thẻ ở trang Check ngay (nhãn mới, huy hiệu AUTO)
        info.update(committed=True, added=len(added), skipped_dup=len(props) - len(added), total_labels=len(labels))
    else:
        p.set_proposals(f, props)
    if f in p.report:
        p.report[f]["n_props"] = 0 if commit else len(props)
    return props, info


@app.post("/api/autolabel")
def autolabel(body: dict = Body(...)):
    p = P()
    cls_ids = [int(c) for c in body.get("classes", [])] or list(range(len(p.names)))
    if not cls_ids:
        raise HTTPException(400, "Chưa có class nào trong data.yaml")
    try:
        props, info = _autolabel_one(p, os.path.basename(body["file"]), body["model"], cls_ids, float(body.get("conf", 0.3)),
                                     commit=bool(body.get("commit")), mode=body.get("mode", "append"))
    except Exception as e:
        traceback.print_exc()
        raise HTTPException(500, f"{type(e).__name__}: {e}")
    return ok({"proposals": props, **info})


@app.post("/api/autolabel_batch")
def autolabel_batch(body: dict = Body(...)):
    p = P()
    cls_ids = [int(c) for c in body.get("classes", [])] or list(range(len(p.names)))
    if not cls_ids:
        raise HTTPException(400, "Chưa có class nào trong data.yaml")
    commit, mode = bool(body.get("commit")), body.get("mode", "append")
    files = p.images()
    if body.get("only_missing"):
        files = [f for f in files if not (p.report.get(f) or {}).get("labels")]  # chỉ ảnh chưa có nhãn
    mid, conf = body["model"], float(body.get("conf", 0.3))
    start_job("autolabel", files, lambda p, f: _autolabel_one(p, f, mid, cls_ids, conf, commit=commit, mode=mode))
    return ok(job_status())


@app.post("/api/unload_model")
def unload_model():
    AL.unload()
    return ok()
