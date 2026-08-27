"""
GUI: paste screenshots (Ctrl+V) straight into a capture folder, one after
another, then decode them without leaving the window. Also has a global
hotkey mode: press one key (works even while the remote-viewer window
has focus, not this one) to grab a screenshot of whatever window is
currently in the foreground and save it — no PrtScn/Snipping Tool/
Alt-Tab/Ctrl+V dance needed per page.

Skips the "save each screenshot as a file by hand" step — take a
screenshot/snip on the remote machine (or wherever), switch to this
window, Ctrl+V, repeat. Files are named capture_0000.png, capture_0001.png,
... continuing from whatever's already in the folder, so closing and
reopening this tool mid-session doesn't overwrite earlier captures.

Usage:
    python capture_gui.py
"""
import ctypes
import glob
import os
import re
import sys
import tkinter as tk
from tkinter import filedialog, messagebox, scrolledtext

from PIL import Image, ImageGrab, ImageTk

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import codec
import glyph_match as gm

HERE = os.path.dirname(os.path.abspath(__file__))
DEFAULT_DIR = os.path.join(HERE, "captures")
DEFAULT_REFERENCE = os.path.join(HERE, "glyph_reference.png")

# ---- Windows API helpers (ctypes, stdlib only) for the hotkey capture mode ----

_user32 = ctypes.windll.user32


class _RECT(ctypes.Structure):
    _fields_ = [("left", ctypes.c_long), ("top", ctypes.c_long),
                ("right", ctypes.c_long), ("bottom", ctypes.c_long)]


class _POINT(ctypes.Structure):
    _fields_ = [("x", ctypes.c_long), ("y", ctypes.c_long)]


def _make_process_dpi_aware():
    # Without this, GetWindowRect/ClientToScreen and what ImageGrab actually
    # captures can disagree on a scaled (>100%) display, shifting the crop.
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(2)  # PER_MONITOR_AWARE_V2-ish
    except Exception:
        try:
            _user32.SetProcessDPIAware()
        except Exception:
            pass


def get_foreground_client_rect():
    """Returns ((left, top, right, bottom) in screen coords, window title)
    for the current foreground window's CLIENT area (no title bar/borders),
    or (None, None) if unavailable."""
    hwnd = _user32.GetForegroundWindow()
    if not hwnd:
        return None, None
    rect = _RECT()
    if not _user32.GetClientRect(hwnd, ctypes.byref(rect)):
        return None, None
    top_left = _POINT(rect.left, rect.top)
    bottom_right = _POINT(rect.right, rect.bottom)
    _user32.ClientToScreen(hwnd, ctypes.byref(top_left))
    _user32.ClientToScreen(hwnd, ctypes.byref(bottom_right))
    title_buf = ctypes.create_unicode_buffer(256)
    _user32.GetWindowTextW(hwnd, title_buf, 256)
    bbox = (top_left.x, top_left.y, bottom_right.x, bottom_right.y)
    return bbox, (title_buf.value or "(không có tiêu đề)")


def is_key_down(vk_code):
    return bool(_user32.GetAsyncKeyState(vk_code) & 0x8000)


HOTKEY_OPTIONS = {
    "F9": 0x78,
    "F10": 0x79,
    "F11": 0x7A,
    "F12": 0x7B,
    "Scroll Lock": 0x91,
    "Pause": 0x13,
}


class CaptureApp:
    def __init__(self, root):
        self.root = root
        root.title("Paste captures -> decode")
        root.geometry("680x600")
        self._tkimg = None  # keep a reference so Tk doesn't garbage-collect it

        self.folder = tk.StringVar(value=DEFAULT_DIR)
        self.reference_path = tk.StringVar(value=DEFAULT_REFERENCE)
        self.hotkey_enabled = tk.BooleanVar(value=False)
        self.hotkey_name = tk.StringVar(value="F9")
        self._hotkey_was_down = False

        row1 = tk.Frame(root)
        row1.pack(fill="x", padx=8, pady=(8, 2))
        tk.Label(row1, text="Thư mục lưu ảnh:").pack(side="left")
        tk.Entry(row1, textvariable=self.folder).pack(side="left", fill="x", expand=True, padx=6)
        tk.Button(row1, text="Đổi...", command=self.choose_folder).pack(side="left")

        row2 = tk.Frame(root)
        row2.pack(fill="x", padx=8, pady=2)
        tk.Label(row2, text="glyph_reference.png:").pack(side="left")
        tk.Entry(row2, textvariable=self.reference_path).pack(side="left", fill="x", expand=True, padx=6)
        tk.Button(row2, text="Chọn...", command=self.choose_reference).pack(side="left")

        self.paste_btn = tk.Button(
            root,
            text="Dán ảnh từ clipboard  (hoặc bấm Ctrl+V ở bất kỳ đâu trong cửa sổ này)",
            font=("Segoe UI", 13),
            height=3,
            command=self.paste_from_clipboard,
        )
        self.paste_btn.pack(fill="x", padx=8, pady=(8, 2))

        row_hotkey = tk.Frame(root)
        row_hotkey.pack(fill="x", padx=8, pady=(0, 8))
        tk.Checkbutton(
            row_hotkey, text="Bật phím tắt chụp nhanh:",
            variable=self.hotkey_enabled, command=self._on_hotkey_toggle,
        ).pack(side="left")
        tk.OptionMenu(row_hotkey, self.hotkey_name, *HOTKEY_OPTIONS.keys()).pack(side="left", padx=4)
        tk.Label(
            row_hotkey,
            text="(bấm phím này ở BẤT KỲ cửa sổ nào đang active để chụp + lưu ngay)",
            font=("Segoe UI", 9), fg="#555",
        ).pack(side="left", padx=6)
        self.hotkey_status = tk.Label(row_hotkey, text="(tắt)", font=("Segoe UI", 9, "bold"))
        self.hotkey_status.pack(side="left", padx=6)

        self.status_var = tk.StringVar(value="Chưa có ảnh nào.")
        tk.Label(root, textvariable=self.status_var, font=("Segoe UI", 10)).pack(pady=(0, 4))

        self.preview_label = tk.Label(root, text="(chưa có ảnh)", relief="sunken", height=8)
        self.preview_label.pack(fill="x", padx=8, pady=4)

        row3 = tk.Frame(root)
        row3.pack(fill="x", padx=8, pady=6)
        tk.Button(row3, text="Giải mã ngay", command=self.decode_now, bg="#dfffe0").pack(side="left")
        tk.Button(row3, text="Mở thư mục", command=self.open_folder).pack(side="left", padx=6)
        tk.Button(row3, text="Xoá log", command=self.clear_log).pack(side="left", padx=(0, 6))
        tk.Button(row3, text="Xoá hết ảnh (Reset)", command=self.reset_captures, bg="#ffe0e0").pack(side="left")

        self.log = scrolledtext.ScrolledText(root, height=16, font=("Consolas", 9))
        self.log.pack(fill="both", expand=True, padx=8, pady=(0, 8))

        root.bind_all("<Control-v>", lambda e: self.paste_from_clipboard())
        root.bind_all("<Control-V>", lambda e: self.paste_from_clipboard())

        self._refresh_count()

    # ---- helpers ----

    def _log(self, text):
        self.log.insert("end", text + "\n")
        self.log.see("end")
        self.root.update_idletasks()

    def _refresh_count(self):
        folder = self.folder.get()
        if os.path.isdir(folder):
            n = len([
                p for p in glob.glob(os.path.join(folder, "*"))
                if p.lower().endswith((".png", ".jpg", ".jpeg", ".bmp"))
            ])
            self.status_var.set(f"Đang có {n} ảnh trong thư mục.")
        else:
            self.status_var.set("Thư mục chưa tồn tại — sẽ tự tạo khi dán ảnh đầu tiên.")

    def _next_index(self, folder):
        nums = []
        for p in glob.glob(os.path.join(folder, "capture_*.*")):
            m = re.search(r"capture_(\d+)\.\w+$", os.path.basename(p))
            if m:
                nums.append(int(m.group(1)))
        return (max(nums) + 1) if nums else 0

    def _show_preview(self, path):
        try:
            img = Image.open(path)
            img.thumbnail((640, 200))
            self._tkimg = ImageTk.PhotoImage(img)
            self.preview_label.configure(image=self._tkimg, text="")
        except Exception:
            pass

    # ---- actions ----

    def choose_folder(self):
        d = filedialog.askdirectory(initialdir=self.folder.get() or HERE)
        if d:
            self.folder.set(d)
            self._refresh_count()

    def choose_reference(self):
        f = filedialog.askopenfilename(filetypes=[("PNG", "*.png")], initialdir=HERE)
        if f:
            self.reference_path.set(f)

    def open_folder(self):
        folder = self.folder.get()
        os.makedirs(folder, exist_ok=True)
        os.startfile(folder)

    def clear_log(self):
        self.log.delete("1.0", "end")

    def reset_captures(self):
        folder = self.folder.get()
        if not os.path.isdir(folder):
            messagebox.showinfo("Không có gì để xoá", "Thư mục chưa tồn tại.")
            return
        images = [
            p for p in glob.glob(os.path.join(folder, "*"))
            if p.lower().endswith((".png", ".jpg", ".jpeg", ".bmp"))
        ]
        if not images:
            messagebox.showinfo("Không có gì để xoá", "Thư mục hiện không có ảnh nào.")
            return
        if not messagebox.askyesno(
            "Xác nhận xoá",
            f"Xoá vĩnh viễn {len(images)} ảnh trong:\n{folder}\n\nKhông thể hoàn tác. Tiếp tục?",
        ):
            return
        deleted = 0
        for p in images:
            try:
                os.remove(p)
                deleted += 1
            except OSError as e:
                self._log(f"Không xoá được {os.path.basename(p)}: {e}")
        self._log(f"Đã xoá {deleted}/{len(images)} ảnh trong thư mục.")
        self.preview_label.configure(image="", text="(chưa có ảnh)")
        self._tkimg = None
        self._refresh_count()

    def _on_hotkey_toggle(self):
        if self.hotkey_enabled.get():
            self.hotkey_status.configure(text=f"Đang chờ phím {self.hotkey_name.get()}...", fg="#0a0")
            self._hotkey_was_down = True  # ignore a key already held down at the moment of enabling
            self._hotkey_poll()
        else:
            self.hotkey_status.configure(text="(tắt)", fg="black")

    def _hotkey_poll(self):
        if not self.hotkey_enabled.get():
            return
        vk = HOTKEY_OPTIONS.get(self.hotkey_name.get(), 0x78)
        down = is_key_down(vk)
        if down and not self._hotkey_was_down:
            self._capture_foreground_window()
        self._hotkey_was_down = down
        self.root.after(50, self._hotkey_poll)

    def _capture_foreground_window(self):
        bbox, title = get_foreground_client_rect()
        if not bbox or bbox[2] <= bbox[0] or bbox[3] <= bbox[1]:
            self._log("[Phím tắt] Không lấy được vùng cửa sổ hợp lệ, bỏ qua.")
            return
        try:
            img = ImageGrab.grab(bbox=bbox)
        except Exception as e:
            self._log(f"[Phím tắt] Lỗi chụp màn hình: {e}")
            return

        folder = self.folder.get()
        os.makedirs(folder, exist_ok=True)
        idx = self._next_index(folder)
        path = os.path.join(folder, f"capture_{idx:04d}.png")
        img.save(path, "PNG")
        self._log(f"[Phím tắt] Chụp cửa sổ '{title}' ({bbox[2]-bbox[0]}x{bbox[3]-bbox[1]}) -> {os.path.basename(path)}")
        self._show_preview(path)
        self._refresh_count()

    def paste_from_clipboard(self):
        folder = self.folder.get()
        os.makedirs(folder, exist_ok=True)
        try:
            data = ImageGrab.grabclipboard()
        except Exception as e:
            messagebox.showerror("Lỗi đọc clipboard", str(e))
            return

        saved = []
        if isinstance(data, Image.Image):
            idx = self._next_index(folder)
            path = os.path.join(folder, f"capture_{idx:04d}.png")
            data.save(path, "PNG")
            saved.append(path)
        elif isinstance(data, list):
            for src in data:
                if os.path.isfile(src) and src.lower().endswith((".png", ".jpg", ".jpeg", ".bmp")):
                    idx = self._next_index(folder)
                    dst = os.path.join(folder, f"capture_{idx:04d}{os.path.splitext(src)[1].lower()}")
                    Image.open(src).save(dst)
                    saved.append(dst)

        if not saved:
            self._log("(Clipboard không có ảnh — chụp/snip rồi thử lại.)")
            return

        for p in saved:
            self._log(f"Đã lưu: {os.path.basename(p)}")
            self._show_preview(p)

        self._refresh_count()

    def decode_now(self):
        folder = self.folder.get()
        ref = self.reference_path.get()
        if not os.path.isdir(folder):
            messagebox.showwarning("Chưa có ảnh", "Thư mục chưa có ảnh nào.")
            return
        if not os.path.isfile(ref):
            messagebox.showwarning("Thiếu reference", f"Không tìm thấy:\n{ref}")
            return

        self._log("\n--- Giải mã ---")
        try:
            reference = gm.GlyphReference(ref)
        except Exception as e:
            self._log(f"Lỗi đọc reference: {e}")
            return

        ref_abspath = os.path.abspath(ref)
        image_paths = sorted(
            p for p in glob.glob(os.path.join(folder, "*"))
            if p.lower().endswith((".png", ".bmp", ".jpg", ".jpeg", ".tif", ".tiff"))
            and os.path.abspath(p) != ref_abspath
        )

        pages = {}
        for path in image_paths:
            name = os.path.basename(path)
            try:
                page_str, worst = gm.decode_page_image(path, reference)
                info = codec.parse_page_string(page_str)
            except Exception as e:
                self._log(f"  {name}: LỖI ({e})")
                continue
            status = "OK" if info.crc_ok else "CRC SAI"
            self._log(f"  {name}: trang {info.index + 1}/{info.total} - {status} (score {worst:.1f})")
            existing = pages.get(info.index)
            if existing is None or (not existing.crc_ok and info.crc_ok):
                pages[info.index] = info

        try:
            result = codec.assemble(pages)
        except ValueError as e:
            self._log(f"\nChưa xong: {e}")
            return

        out_path = filedialog.asksaveasfilename(title="Lưu file kết quả")
        if out_path:
            with open(out_path, "wb") as f:
                f.write(result)
            self._log(f"\nTHÀNH CÔNG: ghi {len(result)} byte vào {out_path}")
        else:
            self._log(f"\nTHÀNH CÔNG: ráp đủ {len(result)} byte (chưa lưu file — bấm Giải mã ngay lần nữa để lưu).")


def main():
    _make_process_dpi_aware()
    root = tk.Tk()
    CaptureApp(root)
    root.mainloop()


if __name__ == "__main__":
    main()
