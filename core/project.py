"""Project = 1 folder ảnh + folder <tên>_checked: nhãn OBB, data.yaml, state, báo cáo, phân loại good/bad."""
import json
import math
import os
import shutil
import threading
import time

import cv2
import numpy as np
import yaml

from . import edges as E

IMG_EXT = (".jpg", ".jpeg", ".png", ".bmp", ".webp", ".tif", ".tiff")
RATINGS = ("good", "bad")
DEFAULT_OI_CLASSES = ["Handgun", "Rifle", "Shotgun", "Weapon"]

APP_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))   # .../edgetool
DATA_ROOT = os.path.join(os.path.dirname(APP_ROOT), "data-labeled")      # nằm cạnh edgetool/, không theo app đi đâu


GENERIC_DIRS = {"images", "image", "imgs", "img", "jpegimages", "data", "pictures", "photos"}
SPLIT_DIRS = {"train", "valid", "val", "test"}


def dataset_name(folder):
    """Tên dễ nhận ra cho data-labeled/<tên>: bỏ qua tên chung chung (images, data...) và ghép tên split.
    D:\\x\\train_sung_mask_hat\\train\\images -> "train_sung_mask_hat-train"
    ...\\open-images-v7\\train\\data          -> "open-images-v7-train"
    D:\\anh\\sung_ngoai_troi                   -> "sung_ngoai_troi" """
    p = os.path.abspath(folder).rstrip("\\/")
    name = os.path.basename(p)
    while name.lower() in GENERIC_DIRS and os.path.dirname(p) != p:
        p = os.path.dirname(p)
        name = os.path.basename(p)
    split = None
    if name.lower() in SPLIT_DIRS and os.path.dirname(p) != p:
        split = name
        p = os.path.dirname(p)
        name = os.path.basename(p)
        while name.lower() in GENERIC_DIRS and os.path.dirname(p) != p:
            p = os.path.dirname(p)
            name = os.path.basename(p)
    if not name:                                    # leo tới gốc ổ đĩa (vd C:\images) -> dùng luôn tên gốc
        name = os.path.basename(os.path.abspath(folder).rstrip("\\/")) or "root"
    name = "".join("_" if ch in '<>:"/\\|?*' else ch for ch in name).strip(" .") or "root"
    return f"{name}-{split}" if split else name


def _owner(cand):
    """Đọc source_folder ghi trong state.json của 1 thư mục data-labeled/<tên>; None nếu không đọc được."""
    try:
        return json.load(open(os.path.join(cand, "state.json"), encoding="utf-8")).get("source_folder")
    except Exception:
        return None


def _owned_dir(folder):
    """Thư mục data-labeled/* nào đang ghi source_folder == folder (nhận diện theo marker, không phụ thuộc tên)."""
    if not os.path.isdir(DATA_ROOT):
        return None
    for d in sorted(os.listdir(DATA_ROOT)):
        cand = os.path.join(DATA_ROOT, d)
        owner = _owner(cand) if os.path.isdir(cand) else None
        if owner and os.path.normcase(os.path.abspath(owner)) == os.path.normcase(folder):
            return cand
    return None


def _legacy_dir(folder):
    base = os.path.basename(folder.rstrip("\\/")) or "root"
    legacy = os.path.join(folder, f"{base}_checked")
    return legacy if os.path.isdir(legacy) else None


def find_checked_dir(folder):
    """CHỈ ĐỌC: tìm thư mục nhãn hiện có của 1 folder ảnh (data-labeled/* theo marker, hoặc <tên>_checked kiểu cũ
    nằm trong folder gốc nếu chưa từng mở lại). Không tạo, không di chuyển gì - dùng cho bảng tóm tắt ở trang Home."""
    folder = os.path.abspath(folder)
    return _owned_dir(folder) or _legacy_dir(folder)


def resolve_checked_dir(folder):
    """Dùng khi THỰC SỰ MỞ project: trả về (tạo nếu chưa có) thư mục data-labeled/<tên> cho folder ảnh này.
    1. Đã có thư mục nào ghi source_folder == folder -> dùng lại (đổi tên thư mục cũng không sao).
    2. Chưa có -> lấy tên dataset_name(); trùng tên đang có thì thêm _2, _3...
       Còn <tên>_checked kiểu cũ trong folder gốc -> MOVE sang thư mục mới đó (os.rename, tức thời cùng ổ đĩa)."""
    folder = os.path.abspath(folder)
    os.makedirs(DATA_ROOT, exist_ok=True)
    owned = _owned_dir(folder)
    if owned:
        return owned
    base = dataset_name(folder)
    i = 0
    while True:
        cand = os.path.join(DATA_ROOT, base if i == 0 else f"{base}_{i + 1}")
        if not os.path.exists(cand):
            break
        i += 1
    legacy = _legacy_dir(folder)
    if legacy:
        shutil.move(legacy, cand)
        try:
            print(f"[EdgeTool] Đã chuyển {legacy} -> {cand}")
        except Exception:
            pass   # console không hỗ trợ UTF-8 (vd cp1252) - chỉ là log, đã move xong rồi
    return cand


_SUMMARY_CACHE = {}


def folder_summary(folder):
    """Tóm tắt nhẹ 1 folder cho trang Home (danh sách 'thư mục đã check'): KHÔNG load Project đầy đủ
    (data.yaml, chỉ mục Open Images...) để liệt kê nhiều folder vẫn nhanh. Cache theo mtime của report/state.
    Chỉ ĐỌC (find_checked_dir), không bao giờ tạo hay di chuyển thư mục chỉ vì hiển thị danh sách."""
    folder = os.path.abspath(folder)
    if not os.path.isdir(folder):
        return {"folder": folder, "exists": False}
    checked = find_checked_dir(folder) or ""
    report_path = os.path.join(checked, "report", "report.json") if checked else ""
    state_path = os.path.join(checked, "state.json") if checked else ""
    r_mtime = os.path.getmtime(report_path) if os.path.isfile(report_path) else 0
    s_mtime = os.path.getmtime(state_path) if os.path.isfile(state_path) else 0
    key = (r_mtime, s_mtime)
    cached = _SUMMARY_CACHE.get(folder)
    if cached and cached[0] == key:
        return cached[1]

    n_images = sum(1 for f in os.listdir(folder) if f.lower().endswith(IMG_EXT) and os.path.isfile(os.path.join(folder, f)))
    out = {"folder": folder, "exists": True, "checked": os.path.isdir(checked), "n_images": n_images,
           "checked_dir": checked or None,
           "planned_dir": None if checked else os.path.join(DATA_ROOT, dataset_name(folder)),   # chưa mở lần nào
           "n_scanned": 0, "n_labeled": 0, "n_auto": 0, "n_manual": 0, "n_imported": 0, "n_nolabel": 0,
           "n_good": 0, "n_bad": 0, "n_unrated": 0, "done": False, "updated": r_mtime or None}
    if os.path.isfile(report_path):
        try:
            report = json.load(open(report_path, encoding="utf-8"))
        except Exception:
            report = {}
        out["n_scanned"] = len(report)
        for e in report.values():
            if e.get("labels"):
                out["n_labeled"] += 1
                src = e.get("src") or ""
                if src.startswith("auto:"):
                    out["n_auto"] += 1
                elif src == "tool":
                    out["n_manual"] += 1
                else:
                    out["n_imported"] += 1          # openimages / yolo:<path> (nhãn có sẵn, chưa chắc đã người xem)
            else:
                out["n_nolabel"] += 1
    if os.path.isfile(state_path):
        try:
            st = json.load(open(state_path, encoding="utf-8"))
        except Exception:
            st = {}
        for r in (st.get("ratings") or {}).values():
            if r in ("good", "bad"):
                out[f"n_{r}"] += 1
        out["done"] = bool(st.get("done"))
    out["n_unrated"] = max(0, out["n_scanned"] - out["n_good"] - out["n_bad"])
    _SUMMARY_CACHE[folder] = (key, out)
    return out


def link_or_copy(src, dst):
    """Hardlink (cùng ổ đĩa: tức thời, không tốn thêm dung lượng, ảnh gốc không bị ảnh hưởng); khác ổ thì copy."""
    try:
        os.link(src, dst)
    except OSError:
        shutil.copy2(src, dst)


def imwrite(path, img, q=88):
    ext = os.path.splitext(path)[1]
    ok, buf = cv2.imencode(ext, img, [cv2.IMWRITE_JPEG_QUALITY, q] if ext.lower() in (".jpg", ".jpeg") else [])
    if ok:
        buf.tofile(path)


def atomic_json(path, obj, indent=None):
    """Ghi tmp rồi thay thế; Windows đôi khi khoá file (antivirus/indexer) -> thử lại vài lần."""
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(obj, fh, ensure_ascii=False, indent=indent)
    for i in range(20):
        try:
            os.replace(tmp, path)
            return
        except PermissionError:
            time.sleep(0.1 * (i + 1))
    raise PermissionError(f"Không ghi được {path} (file đang bị khoá)")


def order_pts(p):
    """Sắp 4 điểm theo chiều kim đồng hồ bắt đầu từ điểm trên-trái (ổn định khi lưu)."""
    p = np.asarray(p, np.float32)
    c = p.mean(0)
    a = np.arctan2(p[:, 1] - c[1], p[:, 0] - c[0])
    p = p[np.argsort(a)]
    start = int(np.argmin(p.sum(1)))
    return np.roll(p, -start, axis=0)


def rect_from_poly(p):
    return order_pts(cv2.boxPoints(cv2.minAreaRect(np.asarray(p, np.float32))))


# ------------------------------------------------------------------ đọc nhãn nguồn
def parse_yolo_txt(path, W, H):
    """YOLO (5 số) / YOLO-OBB (9 số) / polygon seg (lẻ, >= 7 số) -> [(cls, pts)], errors."""
    labels, errors = [], []
    with open(path, encoding="utf-8", errors="replace") as f:
        for ln, line in enumerate(f, 1):
            s = line.split()
            if not s:
                continue
            try:
                v = [float(x) for x in s]
            except ValueError:
                errors.append(f"{os.path.basename(path)} dòng {ln}: không phải số")
                continue
            cls, c = int(v[0]), v[1:]
            if len(c) == 4:
                cx, cy, w, h = c
                pts = [[(cx - w / 2) * W, (cy - h / 2) * H], [(cx + w / 2) * W, (cy - h / 2) * H],
                       [(cx + w / 2) * W, (cy + h / 2) * H], [(cx - w / 2) * W, (cy + h / 2) * H]]
            elif len(c) == 8:
                pts = [[c[i] * W, c[i + 1] * H] for i in range(0, 8, 2)]
            elif len(c) >= 6 and len(c) % 2 == 0:
                pts = rect_from_poly([[c[i] * W, c[i + 1] * H] for i in range(0, len(c), 2)]).tolist()
            else:
                errors.append(f"{os.path.basename(path)} dòng {ln}: {len(c)} toạ độ (cần 4, 8 hoặc polygon)")
                continue
            if any(x < -0.02 * W or x > 1.02 * W or y < -0.02 * H or y > 1.02 * H for x, y in pts):
                errors.append(f"{os.path.basename(path)} dòng {ln}: toạ độ nằm ngoài ảnh")
            labels.append((cls, [[float(x), float(y)] for x, y in pts]))
    return labels, errors


def find_yaml_names(folder):
    for d in (folder, os.path.dirname(folder), os.path.dirname(os.path.dirname(folder))):
        for fn in ("data.yaml", "dataset.yaml"):
            p = os.path.join(d, fn)
            if os.path.isfile(p):
                try:
                    names = yaml.safe_load(open(p, encoding="utf-8")).get("names")
                    if isinstance(names, dict):
                        return [names[k] for k in sorted(names)], p
                    if isinstance(names, list):
                        return names, p
                except Exception:
                    pass
    return None, None


# ------------------------------------------------------------------ project
class Project:
    def __init__(self, folder):
        self.folder = os.path.abspath(folder)
        self.checked = resolve_checked_dir(self.folder)
        self.labels_dir = os.path.join(self.checked, "labels")
        self.images_dir = os.path.join(self.checked, "images")   # ảnh tương ứng 1-1 với labels/ (hardlink) -> dataset đủ bộ
        self.report_dir = os.path.join(self.checked, "report")
        self.thumbs_dir = os.path.join(self.report_dir, "thumbs")
        self.prop_dir = os.path.join(self.checked, "proposals")
        for d in (self.labels_dir, self.images_dir, self.thumbs_dir, self.prop_dir):
            os.makedirs(d, exist_ok=True)
        self.lock = threading.RLock()
        self.state_path = os.path.join(self.checked, "state.json")
        self.state = {"ratings": {}, "settings": {"oi_classes": DEFAULT_OI_CLASSES}, "prompts": {}, "label_src": {}, "done": False}
        loaded_source = None   # giá trị THẬT trên đĩa (trước khi default đè lên) - so sánh bằng cái này, không phải
        if os.path.exists(self.state_path):                                      # bằng self.state sau khi đã gán mặc định
            on_disk = json.load(open(self.state_path, encoding="utf-8"))
            loaded_source = on_disk.get("source_folder")
            self.state.update(on_disk)
        self.state["source_folder"] = self.folder   # marker để resolve_checked_dir/find_checked_dir nhận đúng project
        if loaded_source != self.folder:             # chưa có / sai (vd vừa migrate từ state.json kiểu cũ) -> ghi lại ngay
            self.save_state()
        self.yaml_path = os.path.join(self.checked, "data.yaml")
        self.report_path = os.path.join(self.report_dir, "report.json")
        self.report = json.load(open(self.report_path, encoding="utf-8")) if os.path.exists(self.report_path) else {}
        self._oi = None
        self.names = self._load_names()
        self._write_names(self.names)   # luôn làm mới "path:" trong data.yaml theo vị trí checked hiện tại
        self.sync_images()               # bù ảnh còn thiếu trong images/ (project cũ chưa có thư mục này)

    # --- files
    def images(self):
        return sorted(f for f in os.listdir(self.folder)
                      if f.lower().endswith(IMG_EXT) and os.path.isfile(os.path.join(self.folder, f)))

    def img_path(self, f):
        p = os.path.join(self.folder, os.path.basename(f))
        for attempt in range(3):
            if os.path.isfile(p):
                return p
            if attempt < 2:
                time.sleep(0.05)
        raise FileNotFoundError(f)

    def save_state(self):
        with self.lock:
            atomic_json(self.state_path, self.state, indent=1)

    def save_report(self):
        with self.lock:
            atomic_json(self.report_path, self.report)

    # --- classes (data.yaml): chỉ thêm / đổi tên, không xoá để không lệch id
    def _load_names(self):
        if os.path.exists(self.yaml_path):
            d = yaml.safe_load(open(self.yaml_path, encoding="utf-8")) or {}
            n = d.get("names", {})
            return [n[k] for k in sorted(n)] if isinstance(n, dict) else list(n)
        names, _ = find_yaml_names(self.folder)
        if names is None and self._oi_paths():
            names = list(self.state["settings"].get("oi_classes") or [])
        self._write_names(names or [])
        return names or []

    def _write_names(self, names):
        # images/ + labels/ = mọi ảnh đã có nhãn. Chỉ muốn train trên ảnh đã duyệt "good" thì đổi thành good/images.
        d = {"path": self.checked, "train": "images", "val": "images",
             "nc": len(names), "names": {i: n for i, n in enumerate(names)}}
        yaml.safe_dump(d, open(self.yaml_path, "w", encoding="utf-8"), allow_unicode=True, sort_keys=False)

    def add_class(self, name):
        name = name.strip()
        with self.lock:
            if not name:
                raise ValueError("Tên class rỗng")
            if name in self.names:
                return self.names.index(name)
            self.names.append(name)
            self._write_names(self.names)
            return len(self.names) - 1

    def rename_class(self, idx, name):
        with self.lock:
            if not name.strip() or name.strip() in self.names:
                raise ValueError("Tên rỗng hoặc đã tồn tại")
            self.names[idx] = name.strip()
            self._write_names(self.names)

    def class_id(self, name):
        return self.names.index(name) if name in self.names else self.add_class(name)

    # --- nguồn nhãn
    def _oi_paths(self):
        root = os.path.dirname(self.folder)
        csvp = os.path.join(root, "labels", "detections.csv")
        if not os.path.isfile(csvp):
            return None
        # classes.csv giống nhau giữa các split (train/validation/test) -> tìm ở split bên cạnh nếu thiếu
        cands = [os.path.join(root, "metadata", "classes.csv")]
        up = os.path.dirname(root)
        cands += [os.path.join(up, d, "metadata", "classes.csv") for d in sorted(os.listdir(up))]
        clsp = next((c for c in cands if os.path.isfile(c)), None)
        return (csvp, clsp) if clsp else None

    def _oi_index(self):
        """Một lần quét detections.csv cho các ảnh trong folder; cache ra report/oi_index.json."""
        if self._oi is not None:
            return self._oi
        cache = os.path.join(self.report_dir, "oi_index.json")
        want = self.state["settings"].get("oi_classes") or []
        if os.path.exists(cache):
            c = json.load(open(cache, encoding="utf-8"))
            if c.get("classes") == want:
                self._oi = c["index"]
                return self._oi
        paths = self._oi_paths()
        idx = {}
        if paths:
            import csv
            mid2name = {r[0]: r[1] for r in csv.reader(open(paths[1], encoding="utf-8"))}
            ids = {os.path.splitext(f)[0] for f in self.images()}
            with open(paths[0], encoding="utf-8") as f:
                next(f)
                for line in f:
                    i = line.find(",")
                    iid = line[:i]
                    if iid not in ids:
                        continue
                    c = line.split(",")
                    nm = mid2name.get(c[2], c[2])
                    if want and nm not in want:
                        continue
                    idx.setdefault(iid, []).append([nm, float(c[4]), float(c[5]), float(c[6]), float(c[7])])
        json.dump({"classes": want, "index": idx}, open(cache, "w", encoding="utf-8"))
        self._oi = idx
        return idx

    def label_path(self, f):
        return os.path.join(self.labels_dir, os.path.splitext(f)[0] + ".txt")

    def source_label_files(self, f):
        stem = os.path.splitext(f)[0]
        c = [os.path.join(self.folder, stem + ".txt")]
        parts = self.folder.replace("/", "\\").split("\\")
        if "images" in parts:
            k = len(parts) - 1 - parts[::-1].index("images")
            c.append("\\".join(parts[:k] + ["labels"] + parts[k + 1:] + [stem + ".txt"]))
        c.append(os.path.join(os.path.dirname(self.folder), "labels", stem + ".txt"))
        return [p for p in c if os.path.isfile(p)]

    def get_labels(self, f, W, H):
        """Trả (labels [{cls, pts}], errors, nguồn). Nhãn của tool được ưu tiên; nếu chưa có thì nhập từ nguồn sẵn có."""
        own = self.label_path(f)
        if os.path.exists(own):
            labs, errs = parse_yolo_txt(own, W, H)
            src = self.state["label_src"].get(f, "tool")
        else:
            labs, errs, src = [], [], None
            srcs = self.source_label_files(f)
            if srcs:
                labs, errs = parse_yolo_txt(srcs[0], W, H)
                src = "yolo:" + srcs[0]
            elif self._oi_paths():
                for nm, x0, x1, y0, y1 in self._oi_index().get(os.path.splitext(f)[0], []):
                    labs.append((self.class_id(nm), [[x0 * W, y0 * H], [x1 * W, y0 * H], [x1 * W, y1 * H], [x0 * W, y1 * H]]))
                src = "openimages" if labs else None
            if labs:
                self.write_labels(f, [{"cls": c, "pts": p} for c, p in labs], W, H)
                self.state["label_src"][f] = src
                self.save_state()
        for c, _ in labs:
            if c >= len(self.names):
                errs.append(f"class id {c} không có trong data.yaml ({len(self.names)} class)")
        return [{"cls": c, "pts": p} for c, p in labs], errs, src

    def write_labels(self, f, labels, W, H):
        lines = []
        for L in labels:
            p = np.asarray(L["pts"], np.float32)
            p[:, 0] = np.clip(p[:, 0], 0, W)
            p[:, 1] = np.clip(p[:, 1], 0, H)
            p = order_pts(p)
            lines.append(f"{int(L['cls'])} " + " ".join(f"{x / W:.6f} {y / H:.6f}" for x, y in p))
        with open(self.label_path(f), "w", encoding="utf-8") as fh:
            fh.write("\n".join(lines) + ("\n" if lines else ""))
        self._mirror(f, bool(lines))
        r = self.state["ratings"].get(f)
        if r:
            self._copy_to(f, r)

    # --- images/: ảnh đi kèm labels/, để data-labeled/<tên>/ tự nó là 1 dataset YOLO đủ bộ
    def _mirror(self, f, has_labels):
        dst = os.path.join(self.images_dir, os.path.basename(f))
        if has_labels:
            if not os.path.exists(dst):
                link_or_copy(self.img_path(f), dst)
        elif os.path.exists(dst):
            os.remove(dst)              # nhãn rỗng (vd auto-label không thấy gì) -> không tính là ảnh đã label

    def sync_images(self):
        """Đồng bộ images/ theo labels/: thêm ảnh còn thiếu, bỏ ảnh mà nhãn đã rỗng/đã xoá. Nhanh (hardlink)."""
        stems = {}
        for f in os.listdir(self.folder):
            if f.lower().endswith(IMG_EXT):
                stems[os.path.splitext(f)[0]] = f
        want = set()
        for lf in os.listdir(self.labels_dir):
            stem, ext = os.path.splitext(lf)
            if ext != ".txt" or stem not in stems or os.path.getsize(os.path.join(self.labels_dir, lf)) == 0:
                continue
            want.add(stems[stem])
            dst = os.path.join(self.images_dir, stems[stem])
            if not os.path.exists(dst):
                try:
                    link_or_copy(os.path.join(self.folder, stems[stem]), dst)
                except OSError:
                    pass
        for f in os.listdir(self.images_dir):
            if f not in want:
                try:
                    os.remove(os.path.join(self.images_dir, f))
                except OSError:
                    pass

    # --- phân loại (copy, không đụng ảnh gốc)
    def _copy_to(self, f, rating):
        for d in ("images", "labels"):
            os.makedirs(os.path.join(self.checked, rating, d), exist_ok=True)
        dst_img = os.path.join(self.checked, rating, "images", f)
        if not os.path.exists(dst_img):
            link_or_copy(self.img_path(f), dst_img)
        lp = self.label_path(f)
        dst = os.path.join(self.checked, rating, "labels", os.path.splitext(f)[0] + ".txt")
        if os.path.exists(lp):
            shutil.copy2(lp, dst)
        elif os.path.exists(dst):
            os.remove(dst)

    def rate(self, f, rating):
        with self.lock:
            stem = os.path.splitext(f)[0]
            for r in RATINGS:
                for p in (os.path.join(self.checked, r, "images", f), os.path.join(self.checked, r, "labels", stem + ".txt")):
                    if r != rating and os.path.exists(p):
                        os.remove(p)
            if rating in RATINGS:
                self._copy_to(f, rating)
                self.state["ratings"][f] = rating
            else:
                self.state["ratings"].pop(f, None)
            self.save_state()

    # --- đề xuất auto-label
    def prop_path(self, f):
        return os.path.join(self.prop_dir, os.path.splitext(f)[0] + ".json")

    def get_proposals(self, f):
        p = self.prop_path(f)
        return json.load(open(p, encoding="utf-8")) if os.path.exists(p) else []

    def set_proposals(self, f, props):
        p = self.prop_path(f)
        if props:
            json.dump(props, open(p, "w", encoding="utf-8"))
        elif os.path.exists(p):
            os.remove(p)

    # --- phân tích 1 ảnh cho báo cáo (số liệu + thumbnail)
    def analyze(self, f, A=None):
        path = self.img_path(f)
        st = os.stat(path)
        entry = {"file": f, "mtime": st.st_mtime, "size": st.st_size, "errors": [], "labels": [], "t": time.time()}
        try:
            if A is None:
                A = E.Analysis(E.load_image(path))
        except Exception as e:
            entry["errors"].append(str(e))
            return entry
        entry["W"], entry["H"] = A.W, A.H
        labs, errs, src = self.get_labels(f, A.W, A.H)
        entry["errors"] += errs
        entry["src"] = src
        if not labs:
            entry["errors"].insert(0, "KHÔNG CÓ NHÃN: không tìm thấy file nhãn YOLO/OBB hay Open Images cho ảnh này")
        stem = os.path.splitext(f)[0]
        thumb = cv2.cvtColor(A.bgr, cv2.COLOR_BGR2RGB).copy()
        sc = 360 / max(A.W, A.H)
        thumb = cv2.resize(thumb, (max(1, round(A.W * sc)), max(1, round(A.H * sc))), interpolation=cv2.INTER_AREA)
        for i, L in enumerate(labs):
            pts = np.asarray(L["pts"], np.float32)
            try:
                m = A.metrics(pts)
            except Exception as e:
                entry["errors"].append(f"nhãn {i}: {e}")
                continue
            th = {}
            for k in ("original", "gray", "sobel", "color_sobel"):
                crop = E.rot_crop(A.layer(k), pts, 0.15)
                s2 = 150 / max(crop.shape[:2])
                crop = cv2.resize(crop, (max(1, round(crop.shape[1] * s2)), max(1, round(crop.shape[0] * s2))),
                                  interpolation=cv2.INTER_AREA if s2 < 1 else cv2.INTER_NEAREST)
                fn = f"{stem}_L{i}_{k}.jpg"
                imwrite(os.path.join(self.thumbs_dir, fn), cv2.cvtColor(crop, cv2.COLOR_RGB2BGR) if crop.ndim == 3 else crop)
                th[k] = fn
            cls = L["cls"]
            entry["labels"].append({"cls": cls, "name": self.names[cls] if cls < len(self.names) else f"#{cls}",
                                    "pts": L["pts"], "metrics": m, "hints": E.hints(m), "thumbs": th})
            cv2.polylines(thumb, [np.round(pts * sc).astype(np.int32)], True, (0, 255, 0), 2)
        fn = f"{stem}_full.jpg"
        imwrite(os.path.join(self.thumbs_dir, fn), cv2.cvtColor(thumb, cv2.COLOR_RGB2BGR))
        entry["thumb"] = fn
        sc_ = [L["metrics"]["score"] for L in entry["labels"]]
        entry["score"] = min(sc_) if sc_ else None
        entry["label_mtime"] = os.path.getmtime(self.label_path(f)) if os.path.exists(self.label_path(f)) else None
        entry["n_props"] = len(self.get_proposals(f))
        with self.lock:
            self.report[f] = entry
        return entry

    def is_fresh(self, f):
        e = self.report.get(f)
        if not e:
            return False
        try:
            st = os.stat(self.img_path(f))
        except FileNotFoundError:
            return False
        lp = self.label_path(f)
        lm = os.path.getmtime(lp) if os.path.exists(lp) else None
        return e.get("mtime") == st.st_mtime and e.get("size") == st.st_size and e.get("label_mtime") == lm


def clean(o):
    """NaN -> None để JSON hợp lệ."""
    if isinstance(o, float) and math.isnan(o):
        return None
    if isinstance(o, dict):
        return {k: clean(v) for k, v in o.items()}
    if isinstance(o, (list, tuple)):
        return [clean(v) for v in o]
    return o
