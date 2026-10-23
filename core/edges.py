"""Chấm cạnh trên nhãn OBB (đa giác 4 điểm): Sobel, letterbox kiểu Ultralytics, cạnh màu, feature map YOLO, các lớp hiển thị."""
import math
import os
import threading
import urllib.request

import cv2
import numpy as np

NAN = float("nan")
HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


class CFG:
    MODEL_SIZE = 640
    IMGSZ_SWEEP = [320, 416, 640, 960]
    RESIZE_INTERP = "linear"         # giống Ultralytics khi train (augment) và LetterBox khi infer
    LETTERBOX_SQUARE = False
    PAD_VALUE = 114
    SOBEL_KSIZE = 3
    RING = 0.25                      # vòng nền quanh nhãn: phóng đa giác (1 + 2*RING) quanh tâm
    USE_MODEL_FEATURES = True
    FEATURE_WEIGHTS = None           # None -> yolo11n.pt (COCO) làm đại diện
    FEATURE_LAYERS = [0, 2, 4]
    MODELS_DIR = os.path.join(HERE, "models")
    YOLO_URL = "https://github.com/ultralytics/assets/releases/download/v8.3.0/yolo11n.pt"


TORCH_LOCK = threading.Lock()        # dùng chung cho mọi suy luận torch (scan nền + request)


# ------------------------------------------------------------------ cơ bản
def load_image(path):
    bgr = cv2.imdecode(np.fromfile(path, np.uint8), cv2.IMREAD_COLOR)
    if bgr is None:
        raise ValueError(f"Không đọc được ảnh: {path}")
    return bgr


def sobel(gray, k=None):
    f = gray.astype(np.float32)
    k = k or CFG.SOBEL_KSIZE
    gx = cv2.Sobel(f, cv2.CV_32F, 1, 0, ksize=k)
    gy = cv2.Sobel(f, cv2.CV_32F, 0, 1, ksize=k)
    return gx, gy, cv2.magnitude(gx, gy)


def scale_ref(a):
    return max(float(np.percentile(a, 99.5)), 1e-6) if a.size else 1.0


def to_u8(a, ref):
    return (np.clip(a / ref, 0, 1) * 255).astype(np.uint8)


def otsu_thr(mag, ref):
    t, _ = cv2.threshold(to_u8(mag, ref), 0, 255, cv2.THRESH_BINARY | cv2.THRESH_OTSU)
    return t / 255.0 * ref


def auto_canny(gray, sigma=0.33):
    med = float(np.median(gray))
    return cv2.Canny(cv2.GaussianBlur(gray, (3, 3), 0), int(max(0, (1 - sigma) * med)), int(min(255, (1 + sigma) * med)))


def letterbox(img, imgsz=None):
    """Resize cạnh dài về imgsz, pad 114 tới bội số 32 (hoặc vuông). Trả (ảnh, scale, (left, top, nw, nh))."""
    imgsz = imgsz or CFG.MODEL_SIZE
    h, w = img.shape[:2]
    s = imgsz / max(h, w)
    nh, nw = max(1, round(h * s)), max(1, round(w * s))
    interp = cv2.INTER_AREA if CFG.RESIZE_INTERP == "area" else cv2.INTER_LINEAR
    r = cv2.resize(img, (nw, nh), interpolation=interp) if (nw, nh) != (w, h) else img
    th, tw = (imgsz, imgsz) if CFG.LETTERBOX_SQUARE else (math.ceil(nh / 32) * 32, math.ceil(nw / 32) * 32)
    top, left = (th - nh) // 2, (tw - nw) // 2
    out = cv2.copyMakeBorder(r, top, th - nh - top, left, tw - nw - left, cv2.BORDER_CONSTANT, value=(CFG.PAD_VALUE,) * 3)
    return out, s, (left, top, nw, nh)


def color_sobel(bgr):
    """Di Zenzo (structure tensor 3 kênh), chia sqrt(3) để cùng thang Sobel gray."""
    f = bgr.astype(np.float32)
    k = CFG.SOBEL_KSIZE
    gxs = [cv2.Sobel(f[..., c], cv2.CV_32F, 1, 0, ksize=k) for c in range(3)]
    gys = [cv2.Sobel(f[..., c], cv2.CV_32F, 0, 1, ksize=k) for c in range(3)]
    jxx, jyy = sum(g * g for g in gxs), sum(g * g for g in gys)
    jxy = sum(a * b for a, b in zip(gxs, gys))
    lam = 0.5 * (jxx + jyy + np.sqrt((jxx - jyy) ** 2 + 4 * jxy ** 2))
    return np.sqrt(np.maximum(lam, 0)) / math.sqrt(3)


def chroma_sobel(bgr):
    lab = cv2.cvtColor(bgr, cv2.COLOR_BGR2LAB).astype(np.float32)
    k = CFG.SOBEL_KSIZE
    tot = 0
    for c in (1, 2):
        tot = tot + cv2.Sobel(lab[..., c], cv2.CV_32F, 1, 0, ksize=k) ** 2 + cv2.Sobel(lab[..., c], cv2.CV_32F, 0, 1, ksize=k) ** 2
    return np.sqrt(tot)


# ------------------------------------------------------------------ đa giác
def poly_mask(shape, pts):
    m = np.zeros(shape[:2], np.uint8)
    p = np.round(np.asarray(pts, np.float32)).astype(np.int32)
    cv2.fillPoly(m, [p], 1)
    if not m.any():                               # đa giác nhỏ hơn 1 pixel -> lấy pixel tâm
        cx, cy = np.clip(np.asarray(pts, np.float32).mean(0).round().astype(int), 0, [shape[1] - 1, shape[0] - 1])
        m[cy, cx] = 1
    return m.astype(bool)


def scale_poly(pts, f):
    p = np.asarray(pts, np.float32)
    c = p.mean(0)
    return (p - c) * f + c


def rect_sides(pts):
    (_, _), (w, h), _ = cv2.minAreaRect(np.asarray(pts, np.float32))
    return max(w, h), min(w, h)                  # (cạnh dài, cạnh ngắn)


def obb_iou(a, b):
    """IoU giữa 2 tứ giác (dùng để chặn trùng khi auto-label 'thêm vào nhãn hiện có')."""
    a, b = np.asarray(a, np.float32), np.asarray(b, np.float32)
    inter, _ = cv2.intersectConvexConvex(a, b)
    union = cv2.contourArea(a) + cv2.contourArea(b) - inter
    return inter / union if union > 0 else 0.0


def rot_crop(img, pts, ctx=0.15, interp=cv2.INTER_LINEAR):
    """Cắt vùng OBB và xoay thẳng (cạnh dài nằm ngang), thêm ctx phần nền mỗi phía."""
    (cx, cy), (w, h), ang = cv2.minAreaRect(np.asarray(pts, np.float32))
    if w < h:
        w, h, ang = h, w, ang + 90
    W, H = max(2, int(round(w * (1 + 2 * ctx)))), max(2, int(round(h * (1 + 2 * ctx))))
    M = cv2.getRotationMatrix2D((cx, cy), ang, 1.0)
    M[0, 2] += W / 2 - cx
    M[1, 2] += H / 2 - cy
    return cv2.warpAffine(img, M, (W, H), flags=interp, borderMode=cv2.BORDER_CONSTANT, borderValue=0)


def masked_mean(a, m):
    v = a[m]
    return float(v.mean()) if v.size else NAN


# ------------------------------------------------------------------ model feature (đại diện)
_cache = {}


def ensure_file(name, url):
    os.makedirs(CFG.MODELS_DIR, exist_ok=True)
    dest = os.path.join(CFG.MODELS_DIR, name)
    if os.path.exists(dest) and os.path.getsize(dest) > 0:
        return dest
    try:
        urllib.request.urlretrieve(url, dest + ".part")
        os.replace(dest + ".part", dest)
        return dest
    except Exception as e:
        print(f"!! Không tải được {name}: {e}")
        return None


def get_feature_model():
    key = ("feat", CFG.FEATURE_WEIGHTS)
    if key not in _cache:
        m = None
        if CFG.USE_MODEL_FEATURES:
            try:
                from ultralytics import YOLO
                w = CFG.FEATURE_WEIGHTS or ensure_file("yolo11n.pt", CFG.YOLO_URL)
                if w:
                    m = YOLO(w).model.float().eval().cpu()
            except Exception as e:
                print(f"!! Không nạp được model feature: {e}")
        _cache[key] = m
    return _cache[key]


def feature_energy(lb_bgr):
    m = get_feature_model()
    if m is None:
        return None
    import torch
    x = torch.from_numpy(cv2.cvtColor(lb_bgr, cv2.COLOR_BGR2RGB)).permute(2, 0, 1)[None].float() / 255.0
    out, last = {}, max(CFG.FEATURE_LAYERS)
    with TORCH_LOCK, torch.no_grad():
        for i, layer in enumerate(m.model):
            if getattr(layer, "f", -1) != -1:
                break
            x = layer(x)
            if i in CFG.FEATURE_LAYERS:
                f = x[0].numpy()
                out[i] = np.sqrt((f ** 2).mean(0))
            if i >= last:
                break
    return out


# ------------------------------------------------------------------ các lớp hiển thị
LAYERS = [
    ("original", "Ảnh gốc"), ("gray", "Grayscale"), ("equalize", "equalizeHist"), ("clahe", "CLAHE"),
    ("sobel", "Sobel"), ("gx", "Sobel Gx (cạnh dọc)"), ("gy", "Sobel Gy (cạnh ngang)"), ("orient", "Hướng cạnh"),
    ("sobel_blur", "Gaussian σ1 → Sobel"), ("clahe_sobel", "CLAHE → Sobel"), ("scharr", "Scharr"),
    ("laplacian", "|Laplacian|"), ("canny", "Canny"), ("morph", "Morph gradient"), ("dog", "DoG σ1-σ2"),
    ("color_sobel", "Sobel MÀU (Di Zenzo)"), ("lost_gray", "Cạnh mất khi sang gray"), ("chroma", "Chroma Sobel (Lab a,b)"),
    ("model_gray", "Gray @ đầu vào model"), ("model_sobel", "Sobel @ đầu vào model"),
    ("feat0", "YOLO lớp 0 (stride 2)"), ("feat2", "YOLO lớp 2 (stride 4)"), ("feat4", "YOLO lớp 4 (stride 8)"),
]
LAYER_NAMES = dict(LAYERS)


class Analysis:
    """Toàn bộ map của 1 ảnh, tính lười (chỉ khi cần) và cache."""

    def __init__(self, bgr):
        self.bgr = bgr
        self.gray = cv2.cvtColor(bgr, cv2.COLOR_BGR2GRAY)
        self.H, self.W = self.gray.shape
        self._c = {}
        self._lock = threading.RLock()

    def _get(self, key, fn):
        with self._lock:
            if key not in self._c:
                self._c[key] = fn()
            return self._c[key]

    # --- map gốc
    @property
    def grad(self):
        return self._get("grad", lambda: sobel(self.gray))

    @property
    def mag(self):
        return self.grad[2]

    @property
    def ref(self):
        return self._get("ref", lambda: scale_ref(self.mag))

    @property
    def thr(self):
        return self._get("thr", lambda: otsu_thr(self.mag, self.ref))

    @property
    def cmag(self):
        return self._get("cmag", lambda: color_sobel(self.bgr))

    @property
    def chroma(self):
        return self._get("chroma", lambda: chroma_sobel(self.bgr))

    @property
    def lap(self):
        return self._get("lap", lambda: cv2.Laplacian(self.gray.astype(np.float32), cv2.CV_32F, ksize=3))

    @property
    def canny(self):
        return self._get("canny", lambda: auto_canny(self.gray))

    @property
    def lb(self):
        def f():
            lbi, s, pad = letterbox(self.bgr)
            g = cv2.cvtColor(lbi, cv2.COLOR_BGR2GRAY)
            _, _, m = sobel(g)
            l, t, nw, nh = pad
            content = m[t:t + nh, l:l + nw]
            ref = scale_ref(content)
            return {"bgr": lbi, "gray": g, "mag": m, "s": s, "pad": pad, "ref": ref, "thr": otsu_thr(content, ref)}
        return self._get("lb", f)

    @property
    def feats(self):
        return self._get("feats", lambda: feature_energy(self.lb["bgr"]))

    def to_model(self, pts):
        L = self.lb
        return np.asarray(pts, np.float32) * L["s"] + np.array(L["pad"][:2], np.float32)

    def _back_to_orig(self, arr, stride=1.0, interp=cv2.INTER_NEAREST):
        """Map ở toạ độ letterbox (hoặc feature, chia stride) -> cắt bỏ pad -> phóng về kích thước ảnh gốc."""
        l, t, nw, nh = self.lb["pad"]
        x0, y0 = int(round(l / stride)), int(round(t / stride))
        x1, y1 = max(x0 + 1, int(round((l + nw) / stride))), max(y0 + 1, int(round((t + nh) / stride)))
        return cv2.resize(arr[y0:y1, x0:x1], (self.W, self.H), interpolation=interp)

    # --- lớp hiển thị (uint8, gray hoặc RGB, cùng kích thước ảnh gốc)
    def layer(self, name):
        return self._get("L:" + name, lambda: self._layer(name))

    def _layer(self, name):
        g, gx, gy = self.gray, self.grad[0], self.grad[1]
        if name == "original":
            return cv2.cvtColor(self.bgr, cv2.COLOR_BGR2RGB)
        if name == "gray":
            return g
        if name == "equalize":
            return cv2.equalizeHist(g)
        if name == "clahe":
            return cv2.createCLAHE(3.0, (8, 8)).apply(g)
        if name == "sobel":
            return to_u8(self.mag, self.ref)
        if name in ("gx", "gy"):
            v = np.clip((gx if name == "gx" else gy) / self.ref, -1, 1)
            return cv2.cvtColor(cv2.applyColorMap(((v + 1) * 127.5).astype(np.uint8), cv2.COLORMAP_TWILIGHT_SHIFTED), cv2.COLOR_BGR2RGB)
        if name == "orient":
            ang = np.mod(np.arctan2(gy, gx), np.pi)
            hsv = np.dstack([(ang / np.pi * 179).astype(np.uint8), np.full(ang.shape, 255, np.uint8), to_u8(self.mag, self.ref)])
            return cv2.cvtColor(hsv, cv2.COLOR_HSV2RGB)
        if name == "sobel_blur":
            _, _, m = sobel(cv2.GaussianBlur(g, (0, 0), 1.0))
            return to_u8(m, scale_ref(m))
        if name == "clahe_sobel":
            _, _, m = sobel(self.layer("clahe"))
            return to_u8(m, scale_ref(m))
        if name == "scharr":
            f = g.astype(np.float32)
            m = cv2.magnitude(cv2.Scharr(f, cv2.CV_32F, 1, 0), cv2.Scharr(f, cv2.CV_32F, 0, 1))
            return to_u8(m, scale_ref(m))
        if name == "laplacian":
            a = np.abs(self.lap)
            return to_u8(a, scale_ref(a))
        if name == "canny":
            return self.canny
        if name == "morph":
            m = cv2.morphologyEx(g, cv2.MORPH_GRADIENT, np.ones((3, 3), np.uint8))
            return to_u8(m.astype(np.float32), scale_ref(m.astype(np.float32)))
        if name == "dog":
            f = g.astype(np.float32)
            d = cv2.GaussianBlur(f, (0, 0), 1.0) - cv2.GaussianBlur(f, (0, 0), 2.0)
            v = np.clip(d / scale_ref(np.abs(d)), -1, 1)
            return cv2.cvtColor(cv2.applyColorMap(((v + 1) * 127.5).astype(np.uint8), cv2.COLORMAP_TWILIGHT_SHIFTED), cv2.COLOR_BGR2RGB)
        if name == "color_sobel":
            return to_u8(self.cmag, self.ref)
        if name == "lost_gray":
            return to_u8(np.clip(self.cmag - self.mag, 0, None), self.ref)
        if name == "chroma":
            return to_u8(self.chroma, scale_ref(self.chroma))
        if name == "model_gray":
            return self._back_to_orig(self.lb["gray"])
        if name == "model_sobel":
            return self._back_to_orig(to_u8(self.lb["mag"], self.lb["ref"]))
        if name.startswith("feat"):
            L = int(name[4:])
            if not self.feats or L not in self.feats:
                return None
            e = self.feats[L]
            st = self.lb["gray"].shape[1] / e.shape[1]
            u8 = to_u8(e, scale_ref(e))
            return cv2.cvtColor(cv2.applyColorMap(self._back_to_orig(u8, st), cv2.COLORMAP_MAGMA), cv2.COLOR_BGR2RGB)
        raise KeyError(name)

    # --- số liệu cho 1 nhãn
    def metrics(self, pts):
        pts = np.asarray(pts, np.float32)
        m_in = poly_mask(self.gray.shape, pts)
        ring = poly_mask(self.gray.shape, scale_poly(pts, 1 + 2 * CFG.RING)) & ~m_in
        g = self.gray[m_in]
        w, h = rect_sides(pts)
        L = self.lb
        lpts = self.to_model(pts)
        lm = poly_mask(L["gray"].shape, lpts)
        sin, sring = masked_mean(self.mag, m_in), masked_mean(self.mag, ring)
        strong = self.mag[m_in] > self.thr
        cstrong = self.cmag[m_in] > self.thr
        iso = (cstrong & (self.mag[m_in] < 0.5 * self.thr)).sum() / max(int(cstrong.sum()), 1)
        r = {
            "w_px": round(float(w), 1), "h_px": round(float(h), 1),
            "angle": round(float(cv2.minAreaRect(pts)[2]), 1),
            "min_side_model_px": float(min(w, h) * L["s"]),
            "area_pct": 100.0 * float(m_in.sum()) / (self.W * self.H),
            "gray_mean": float(g.mean()), "gray_std": float(g.std()),
            "gray_min": int(g.min()), "gray_max": int(g.max()),
            "dyn_range": float(np.percentile(g, 99) - np.percentile(g, 1)),
            "sobel_in": sin, "sobel_ring": sring,
            "sobel_ratio": sin / max(sring, 1e-6) if not math.isnan(sring) else NAN,
            "sobel_in_model": masked_mean(L["mag"], lm),
            "strong_pct": 100.0 * float(strong.mean()),
            "canny_pct": 100.0 * float((self.canny[m_in] > 0).mean()),
            "lap_var": float(self.lap[m_in].var()),
            "color_in": masked_mean(self.cmag, m_in),
            "iso_pct": 100.0 * float(iso),
            "chroma_in": masked_mean(self.chroma, m_in),
        }
        r["color_gray_ratio"] = r["color_in"] / max(sin, 1e-6)
        for Ly in CFG.FEATURE_LAYERS:
            r[f"f{Ly}_ratio"] = NAN
            if self.feats and Ly in self.feats:
                e = self.feats[Ly]
                st = L["gray"].shape[1] / e.shape[1]
                fp = lpts / st
                fi = poly_mask(e.shape, fp)
                fr = poly_mask(e.shape, scale_poly(fp, 1 + 2 * CFG.RING)) & ~fi
                a, b = masked_mean(e, fi), masked_mean(e, fr)
                r[f"f{Ly}_ratio"] = a / max(b, 1e-6) if not math.isnan(b) else NAN
        r["score"] = edge_score(r)
        return {k: (None if isinstance(v, float) and math.isnan(v) else v) for k, v in r.items()}

    def sweep(self, pts):
        out = []
        for sz in CFG.IMGSZ_SWEEP:
            lbi, s, pad = letterbox(self.gray, sz)
            _, _, m = sobel(lbi)
            l, t, nw, nh = pad
            content = m[t:t + nh, l:l + nw]
            ref = scale_ref(content)
            thr = otsu_thr(content, ref)
            p = np.asarray(pts, np.float32) * s + np.array([l, t], np.float32)
            mk = poly_mask(lbi.shape, p)
            w, h = rect_sides(p)
            out.append({"imgsz": sz, "w": round(float(w), 1), "h": round(float(h), 1),
                        "gray": rot_crop(lbi, p, 0.15, cv2.INTER_NEAREST),
                        "sobel": rot_crop(to_u8(m, ref), p, 0.15, cv2.INTER_NEAREST),
                        "mean": masked_mean(m, mk), "strong_pct": 100.0 * float((m[mk] > thr).mean())})
        return out


# ------------------------------------------------------------------ điểm + cảnh báo
def _c01(x):
    return float(min(max(x, 0.0), 1.0))


def edge_score(m):
    """Heuristic CHƯA hiệu chỉnh (0-100): kích thước sau letterbox, tương phản, độ nét, cạnh trong/nền, mật độ cạnh."""
    r = m["sobel_ratio"]
    parts = [(0.30, _c01((m["min_side_model_px"] - 8) / 40.0)), (0.20, _c01(m["dyn_range"] / 120.0)),
             (0.20, _c01(math.log10(max(m["lap_var"], 1.0)) / 3.0)),
             (0.15, 0.5 if (r is None or math.isnan(r)) else _c01((r - 0.8) / 0.7)), (0.15, _c01(m["strong_pct"] / 15.0))]
    return round(100.0 * sum(w * v for w, v in parts), 1)


def hints(m):
    out = []
    ms = m["min_side_model_px"]
    if ms < 16:
        out.append("Cạnh ngắn < 16px sau letterbox: chi tiết cạnh gần như mất")
    elif ms < 32:
        out.append("Cạnh ngắn < 32px sau letterbox: vật nhỏ, cạnh mỏng dễ mất")
    if m["dyn_range"] < 60:
        out.append("Dải xám p1-p99 < 60: tương phản thấp")
    if m["lap_var"] < 100:
        out.append("Laplacian variance < 100: vùng nhãn khá mờ")
    if m["sobel_ratio"] is not None and m["sobel_ratio"] < 1.0:
        out.append("Cạnh trong nhãn yếu hơn nền xung quanh")
    if m["iso_pct"] > 25:
        out.append("Nhiều cạnh chỉ thấy khi xét màu, mất khi sang gray")
    return out
