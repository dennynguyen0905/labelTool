"""Auto-label: YOLOE, SAM 3, Grounding DINO + SAM 2, YOLO tự train. Kết quả là ĐỀ XUẤT OBB (cls, score, 4 điểm).
Weights được tải 1 lần vào edgetool/models/ rồi dùng local."""
import gc
import json
import os
import threading

import cv2
import numpy as np

from . import edges as E

REGISTRY = os.path.join(E.HERE, "models.json")
MODELS_DIR = E.CFG.MODELS_DIR
TYPES = {
    "yoloe": "YOLOE (Ultralytics, open-vocab theo text)",
    "sam3": "SAM 3 (HuggingFace, text -> mask)",
    "gdino_sam2": "Grounding DINO (HF) + SAM 2 (Ultralytics)",
    "yolo": "YOLO tự train (.pt: detect / obb / seg)",
}
DEFAULT_MODELS = [
    {"id": "yoloe26l", "name": "YOLOE-26L-seg (nhanh)", "type": "yoloe", "weights": "yoloe-26l-seg.pt"},
    {"id": "sam3", "name": "SAM 3 (chính xác nhất, cần quyền HF)", "type": "sam3", "weights": "facebook/sam3"},
    {"id": "gdino_sam2", "name": "Grounding DINO base + SAM 2.1-B", "type": "gdino_sam2",
     "weights": "IDEA-Research/grounding-dino-base", "sam": "sam2.1_b.pt"},
]
_lock = threading.Lock()
_current = {"key": None, "backend": None}


# ------------------------------------------------------------------ registry
def load_registry():
    if not os.path.exists(REGISTRY):
        save_registry(DEFAULT_MODELS)
    return json.load(open(REGISTRY, encoding="utf-8"))


def save_registry(models):
    json.dump(models, open(REGISTRY, "w", encoding="utf-8"), ensure_ascii=False, indent=1)


def add_model(spec):
    if spec.get("type") not in TYPES:
        raise ValueError("type không hợp lệ")
    if not spec.get("weights"):
        raise ValueError("Thiếu weights (đường dẫn .pt hoặc tên repo HuggingFace)")
    models = load_registry()
    base = "".join(ch for ch in (spec.get("name") or spec["type"]).lower() if ch.isalnum())[:20] or "model"
    mid, k = base, 1
    while any(m["id"] == mid for m in models):
        k += 1
        mid = f"{base}{k}"
    m = {"id": mid, "name": spec.get("name") or os.path.basename(spec["weights"]), "type": spec["type"], "weights": spec["weights"]}
    if spec["type"] == "gdino_sam2":
        m["sam"] = spec.get("sam") or "sam2.1_b.pt"
    models.append(m)
    save_registry(models)
    return m


def remove_model(mid):
    save_registry([m for m in load_registry() if m["id"] != mid])
    if _current["key"] and _current["key"][0] == mid:
        unload()


# ------------------------------------------------------------------ tiện ích
def device():
    import torch
    return "cuda" if torch.cuda.is_available() else "cpu"


def local_ultra(name):
    """Weights Ultralytics: nếu chỉ là tên file -> đặt trong models/ (Ultralytics tự tải vào đúng chỗ đó)."""
    if os.path.isabs(name) or os.path.dirname(name):
        if not os.path.exists(name):
            raise FileNotFoundError(f"Không thấy weights: {name}")
        return name
    os.makedirs(MODELS_DIR, exist_ok=True)
    return os.path.join(MODELS_DIR, name)


def local_hf(repo, patterns=None):
    """Repo HuggingFace -> tải 1 lần vào models/<repo> rồi dùng local (không cần mạng các lần sau)."""
    if os.path.isdir(repo):
        return repo
    from huggingface_hub import snapshot_download
    dest = os.path.join(MODELS_DIR, repo.replace("/", "__"))
    if os.path.isdir(dest) and any(f.endswith(".safetensors") for f in os.listdir(dest)):
        return dest                                # đã tải đủ (snapshot_download chỉ đổi tên file khi tải xong)
    try:
        return snapshot_download(repo, local_dir=dest, allow_patterns=patterns)
    except Exception as e:
        if "gated" in type(e).__name__.lower() or "403" in str(e) or "401" in str(e):
            raise RuntimeError(f"Repo {repo} cần quyền truy cập: vào https://huggingface.co/{repo} bấm xin quyền, "
                               "đăng nhập `hf auth login` rồi thử lại.") from e
        raise


def mask_poly_to_obb(poly):
    p = np.asarray(poly, np.float32).reshape(-1, 2)
    if len(p) < 3:
        return None
    return cv2.boxPoints(cv2.minAreaRect(p)).tolist()


def mask_to_obb(mask):
    m = (np.asarray(mask) > 0).astype(np.uint8)
    cs, _ = cv2.findContours(m, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    if not cs:
        return None
    return cv2.boxPoints(cv2.minAreaRect(max(cs, key=cv2.contourArea).astype(np.float32))).tolist()


def box_to_pts(b):
    x0, y0, x1, y1 = [float(v) for v in b]
    return [[x0, y0], [x1, y0], [x1, y1], [x0, y1]]


# ------------------------------------------------------------------ backends
class YOLOEBackend:
    def __init__(self, spec):
        from ultralytics import YOLOE
        self.m = YOLOE(local_ultra(spec["weights"]))
        self.texts = None

    def predict(self, bgr, prompts, conf):
        texts = [t for t, _ in prompts]
        if texts != self.texts:
            self.m.set_classes(texts)
            self.texts = texts
        r = self.m.predict(bgr, conf=conf, verbose=False)[0]
        out = []
        for j in range(len(r.boxes)):
            k = int(r.boxes.cls[j])
            pts = None
            if r.masks is not None and len(r.masks.xy[j]) >= 3:
                pts = mask_poly_to_obb(r.masks.xy[j])
            out.append({"cls": prompts[k][1], "score": float(r.boxes.conf[j]), "pts": pts or box_to_pts(r.boxes.xyxy[j].tolist()),
                        "text": texts[k]})
        return out


class YOLOBackend:
    def __init__(self, spec):
        from ultralytics import YOLO
        self.m = YOLO(local_ultra(spec["weights"]))

    def predict(self, bgr, prompts, conf):
        lut = {t.lower(): c for t, c in prompts}
        r = self.m.predict(bgr, conf=conf, verbose=False)[0]
        out, skipped = [], set()
        if r.obb is not None:
            items = [(int(c), float(s), p.tolist()) for c, s, p in zip(r.obb.cls, r.obb.conf, r.obb.xyxyxyxy)]
        else:
            items = []
            for j in range(len(r.boxes)):
                pts = mask_poly_to_obb(r.masks.xy[j]) if r.masks is not None and len(r.masks.xy[j]) >= 3 else None
                items.append((int(r.boxes.cls[j]), float(r.boxes.conf[j]), pts or box_to_pts(r.boxes.xyxy[j].tolist())))
        for c, s, pts in items:
            name = r.names[c]
            if name.lower() in lut:
                out.append({"cls": lut[name.lower()], "score": s, "pts": pts, "text": name})
            else:
                skipped.add(name)
        if skipped:
            out.append({"skipped": sorted(skipped)})
        return out


class SAM3Backend:
    def __init__(self, spec):
        import torch
        from transformers import Sam3Model, Sam3Processor
        path = local_hf(spec["weights"], ["*.json", "*.safetensors", "*.txt", "*.model"])
        self.dev = device()
        self.dtype = torch.float16 if self.dev == "cuda" else torch.float32
        self.m = Sam3Model.from_pretrained(path, torch_dtype=self.dtype).to(self.dev).eval()
        self.p = Sam3Processor.from_pretrained(path)

    def predict(self, bgr, prompts, conf):
        import torch
        from PIL import Image
        img = Image.fromarray(cv2.cvtColor(bgr, cv2.COLOR_BGR2RGB))
        H, W = bgr.shape[:2]
        out = []
        with torch.no_grad():
            ii = self.p(images=img, return_tensors="pt").to(self.dev)
            vis = self.m.get_vision_features(pixel_values=ii.pixel_values.to(self.dtype))
            for text, cls in prompts:
                ti = self.p(text=text, return_tensors="pt").to(self.dev)
                o = self.m(vision_embeds=vis, input_ids=ti.input_ids)
                res = self.p.post_process_instance_segmentation(o, threshold=conf, mask_threshold=0.5, target_sizes=[(H, W)])[0]
                for mk, s, b in zip(res["masks"], res["scores"], res["boxes"]):
                    pts = mask_to_obb(mk.cpu().numpy()) or box_to_pts(b.tolist())
                    out.append({"cls": cls, "score": float(s), "pts": pts, "text": text})
        return out


class GDinoSAM2Backend:
    def __init__(self, spec):
        from transformers import AutoModelForZeroShotObjectDetection, AutoProcessor
        from ultralytics import SAM
        path = local_hf(spec["weights"], ["*.json", "*.safetensors", "*.txt"])
        self.dev = device()
        self.p = AutoProcessor.from_pretrained(path)
        self.m = AutoModelForZeroShotObjectDetection.from_pretrained(path).to(self.dev).eval()
        self.sam = SAM(local_ultra(spec.get("sam") or "sam2.1_b.pt"))

    def predict(self, bgr, prompts, conf):
        import torch
        from PIL import Image
        img = Image.fromarray(cv2.cvtColor(bgr, cv2.COLOR_BGR2RGB))
        H, W = bgr.shape[:2]
        texts = [t.lower().strip() for t, _ in prompts]
        with torch.no_grad():
            inp = self.p(images=img, text=". ".join(texts) + ".", return_tensors="pt").to(self.dev)
            o = self.m(**inp)
        res = self.p.post_process_grounded_object_detection(o, inp.input_ids, threshold=conf, text_threshold=conf, target_sizes=[(H, W)])[0]
        dets = []
        for b, s, lab in zip(res["boxes"].tolist(), res["scores"].tolist(), res.get("text_labels") or res["labels"]):
            lab = str(lab).lower().strip()
            k = texts.index(lab) if lab in texts else next((i for i, t in enumerate(texts) if t in lab or lab in t), None)
            if k is not None:
                dets.append((b, s, k))
        if not dets:
            return []
        r = self.sam(bgr, bboxes=[d[0] for d in dets], verbose=False)[0]
        out = []
        for j, (b, s, k) in enumerate(dets):
            pts = None
            if r.masks is not None and j < len(r.masks.xy) and len(r.masks.xy[j]) >= 3:
                pts = mask_poly_to_obb(r.masks.xy[j])
            out.append({"cls": prompts[k][1], "score": float(s), "pts": pts or box_to_pts(b), "text": texts[k]})
        return out


BACKENDS = {"yoloe": YOLOEBackend, "yolo": YOLOBackend, "sam3": SAM3Backend, "gdino_sam2": GDinoSAM2Backend}


def unload():
    _current["backend"] = None
    _current["key"] = None
    gc.collect()
    try:
        import torch
        torch.cuda.empty_cache()
    except Exception:
        pass


def free_vram_cache():
    """Giải phóng cache VRAM không dùng tới, KHÔNG unload model đang chạy.
    Gọi định kỳ giữa 1 batch auto-label dài để giảm phân mảnh bộ nhớ (nghi ngờ là nguyên nhân app bị crash)."""
    gc.collect()
    try:
        import torch
        if torch.cuda.is_available():
            torch.cuda.empty_cache()
    except Exception:
        pass


def get_backend(mid):
    spec = next((m for m in load_registry() if m["id"] == mid), None)
    if spec is None:
        raise KeyError(f"Không có model {mid}")
    key = (mid, json.dumps(spec, sort_keys=True))
    if _current["key"] != key:
        unload()                                   # chỉ giữ 1 model trên GPU
        _current["backend"] = BACKENDS[spec["type"]](spec)
        _current["key"] = key
    return _current["backend"]


def nms_obb(props, iou=0.6):
    """Bỏ đề xuất trùng (cùng class, giao nhau nhiều) - giữ cái điểm cao hơn."""
    props = sorted(props, key=lambda p: -p["score"])
    keep = []
    for p in props:
        a = np.asarray(p["pts"], np.float32)
        dup = False
        for q in keep:
            if q["cls"] != p["cls"]:
                continue
            b = np.asarray(q["pts"], np.float32)
            inter, _ = cv2.intersectConvexConvex(a, b)
            union = cv2.contourArea(a) + cv2.contourArea(b) - inter
            if union > 0 and inter / union > iou:
                dup = True
                break
        if not dup:
            keep.append(p)
    return keep


def run(mid, bgr, prompts, conf):
    """prompts: [(text, cls_idx)]. Trả (proposals, info)."""
    with _lock, E.TORCH_LOCK:
        res = get_backend(mid).predict(bgr, prompts, conf)
    skipped = [x for r in res if "skipped" in r for x in r["skipped"]]
    props = [r for r in res if "pts" in r]
    for p in props:
        p["pts"] = [[float(x), float(y)] for x, y in p["pts"]]
    return nms_obb(props), {"skipped_classes": skipped}
