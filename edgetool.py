"""
EdgeTool - check góc cạnh vật thể + label OBB + auto-label.

CHẠY:   python edgetool.py            (mở trình duyệt tại http://127.0.0.1:8765)
        python edgetool.py "D:\\anh"   (mở thẳng folder đó)
"""
import os
import sys
import threading
import webbrowser

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
os.chdir(HERE)                      # để Ultralytics tải file phụ (mobileclip...) vào thư mục tool, không rải ra nơi khác

PORT = 8765


def main():
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    import uvicorn
    import server
    if len(sys.argv) > 1:
        server.open_folder({"folder": sys.argv[1]})
    url = f"http://127.0.0.1:{PORT}/"
    threading.Timer(1.5, lambda: webbrowser.open(url)).start()
    print(f"EdgeTool: {url}   (Ctrl+C để tắt)")
    uvicorn.run(server.app, host="127.0.0.1", port=PORT, log_level="warning")


if __name__ == "__main__":
    main()
