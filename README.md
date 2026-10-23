# EdgeTool

Check góc cạnh vật thể, label OBB, auto-label.

## Cài lần đầu (clone về máy mới)
```
pip install -r requirements.txt
```
(torch cần cài đúng bản CUDA cho GPU máy bạn - xem ghi chú trong `requirements.txt`). Model weights (SAM3,
YOLOE, Grounding DINO...) KHÔNG nằm trong git vì quá nặng - tool tự tải về `models\` ở lần auto-label đầu tiên
cho mỗi model, chạy offline từ lần sau.

Chạy: double-click `run_edgetool.bat` (hoặc `python edgetool.py ["D:\folder ảnh"]`), trình duyệt mở
http://127.0.0.1:8765.

Tool có thể di chuyển/đổi tên nguyên thư mục `edgetool\` đi đâu cũng được (mọi đường dẫn tính tương đối theo vị trí
file, không hardcode) — chỉ cần giữ `data-labeled\` làm thư mục CẠNH nó (sibling), không tách rời 2 thư mục này.

## Thư mục tạo ra (tập trung 1 chỗ, KHÔNG nằm trong folder ảnh gốc)
```
<thư mục chứa edgetool>\
  edgetool\            ← code app (thư mục này)
  data-labeled\<tên dataset>\        ← tự tạo khi mở 1 folder ảnh lần đầu (trùng tên với dataset KHÁC -> _2, _3...)
    images\ + labels\                    ← DATASET ĐỦ BỘ: mọi ảnh có ≥1 box (images\ là hardlink, không tốn dung lượng)
    good\ bad\  images\ + labels\         ← tập con theo đánh giá của bạn (ảnh gốc không bị sửa)
    labels\*.txt                         ← nhãn YOLO-OBB: cls x1 y1 x2 y2 x3 y3 x4 y4 (chuẩn hoá 0-1)
    data.yaml                            ← danh sách class (chỉ thêm / đổi tên)
    proposals\                           ← đề xuất auto-label chưa duyệt
    report\                              ← số liệu + thumbnail (cache, xoá thoải mái)
    state.json                           ← đánh giá, prompt, cài đặt, source_folder (đánh dấu thuộc folder ảnh nào)
```
Tên dataset bỏ qua tên chung chung (`images`, `data`...) và ghép split: `D:\x\train_sung_mask_hat\train\images` →
`train_sung_mask_hat-train`, `...\open-images-v7\train\data` → `open-images-v7-train`. Project được nhận diện bằng
`source_folder` trong state.json nên đổi tên thư mục trong `data-labeled\` bằng tay cũng không mất liên kết.
`data.yaml`: `train/val: images` (toàn bộ ảnh đã label) — muốn chỉ train ảnh đã duyệt thì sửa thành `good/images`.

Dự án cũ còn `<folder ảnh>\<tên>_checked\` kiểu cũ (nằm cạnh/trong folder ảnh) sẽ **tự động được chuyển** sang
`data-labeled\<tên>\` ngay lần đầu mở lại sau khi cập nhật tool — không cần làm gì thêm, dữ liệu giữ nguyên.

## Nguồn nhãn có sẵn (ưu tiên từ trên xuống)
1. `_checked\labels\` (nhãn đã sửa trong tool)
2. YOLO / YOLO-OBB / polygon: cạnh ảnh, `images→labels`, hoặc `..\labels\`
3. Open Images: `..\labels\detections.csv` (+ `classes.csv` của bất kỳ split nào), lọc class ở trang Folder

Ảnh không có nhãn → báo đỏ "KHÔNG CÓ NHÃN" (lọc được trong trang Check).

## Phím tắt
Check: ←/→ chọn ảnh · 1 good · 2 bad · 0 bỏ · Enter mở label

Label: V chọn/sửa · F 4 điểm · R 3 điểm · 1-9 class · Delete xoá · [ ] xoay 1° (Shift 5°) · Ctrl+Z/Y · Ctrl+S lưu ·
A/D ảnh trước/sau (tự lưu) · L / Shift+L đổi lớp ảnh · O gốc↔Sobel · H ẩn nhãn · Z vừa khung · P giữ bảng pixel ·
Shift+1/2 good/bad · Enter nhận đề xuất · chuột phải/giữa hoặc Space+kéo: pan · lăn: zoom

## Auto-label
Model trong `models.json` (nút "+ Thêm" trong tab Auto-label). Weights tải 1 lần về `models\`, sau đó chạy offline.

| Loại | Ghi chú |
|---|---|
| `yoloe` | YOLOE-26L-seg, nhanh, theo text prompt |
| `sam3` | SAM 3 (facebook/sam3, cần quyền HF), text → mask → OBB, chính xác nhất |
| `gdino_sam2` | Grounding DINO base → box → SAM 2.1 → mask → OBB |
| `yolo` | `.pt` tự train (detect / obb / seg), class ghép theo tên với data.yaml |

Prompt mỗi class đặt ở tab Class, hoặc ngay trong panel "Auto-label trước cả thư mục" ở trang Folder (có cả nút
"+ Thêm class" và "+ Gợi ý PPE" cho Hat/Mask/Backpack/Gloves/Goggles) — nhiều từ cách nhau dấu phẩy, nên viết tiếng
Anh (vd `pistol, handgun`).

Panel "Auto-label trước cả thư mục" ở trang Folder chạy model lên MỌI ảnh, ghi thẳng thành nhãn (không cần duyệt
từng đề xuất), có chọn "thêm vào nhãn hiện có" (an toàn, bỏ qua trùng) hoặc "ghi đè toàn bộ". Xong thì sang tab
Check, lọc "tự động - chưa duyệt" (huy hiệu AUTO) để xem lại.

Điểm cạnh là heuristic CHƯA hiệu chỉnh — chỉ để sắp xếp tham khảo.
