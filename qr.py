

import json
import os
import re
import threading
import urllib.parse
import urllib.request
import webbrowser
import customtkinter as ctk
from tkinter import filedialog, messagebox
import qrcode
from PIL import Image

from reportlab.pdfgen import canvas
from reportlab.lib.pagesizes import A4
from reportlab.lib.utils import ImageReader

APP_TITLE = "Parça Listesi → QR"
APP_VERSION = "1.1"
PROGRAMS_URL = "https://www.saydut.com/static/programs.json"
PROGRAM_ID = "qr-kod-olusturucu"

DEFAULT_SCALE = 14
DEFAULT_BORDER = 6
DEFAULT_ERR = "Q (yüksek)"


def _semver_tuple(v: str) -> tuple[int, int, int]:
    v = (v or "").strip()
    if v.startswith("v"):
        v = v[1:]
    parts = v.split(".")
    out = []
    for i in range(3):
        try:
            out.append(int(parts[i]))
        except Exception:
            out.append(0)
    return tuple(out)


def _http_get_json(url: str, timeout: int = 15) -> dict:
    with urllib.request.urlopen(url, timeout=timeout) as response:
        data = response.read().decode("utf-8")
    return json.loads(data)


def split_pasted_text(text: str, split_commas: bool) -> list[str]:
    raw = text.replace("\r\n", "\n").replace("\r", "\n")
    lines: list[str] = []
    for ln in raw.split("\n"):
        ln = ln.strip()
        if not ln:
            continue
        if split_commas:
            parts = re.split(r"[;,]+", ln)
            parts = [p.strip() for p in parts if p.strip()]
            lines.extend(parts)
        else:
            lines.append(ln)
    return lines


def build_plaintext_payload(location: str, items: list[str], numbering: bool, include_location: bool) -> str:
    if not items:
        raise ValueError("Parça listesi boş olamaz.")

    location = (location or "").strip()

    if numbering:
        body_lines = [f"{i+1}. {x}" for i, x in enumerate(items)]
    else:
        body_lines = items

    header = ""
    if include_location and location:
        header = f"{location}\n"

    return header + "\n".join(body_lines)



def make_qr_image(data: str, box_size: int, border: int, err_level: str) -> Image.Image:
    err_map = {
        "L (düşük)": qrcode.constants.ERROR_CORRECT_L,
        "M (orta)":  qrcode.constants.ERROR_CORRECT_M,
        "Q (yüksek)": qrcode.constants.ERROR_CORRECT_Q,
        "H (çok yüksek)": qrcode.constants.ERROR_CORRECT_H,
    }
    qr = qrcode.QRCode(
        version=None,
        error_correction=err_map.get(err_level, qrcode.constants.ERROR_CORRECT_Q),
        box_size=max(2, int(box_size)),
        border=max(4, int(border)),
    )
    qr.add_data(data)
    qr.make(fit=True)
    img = qr.make_image(fill_color="black", back_color="white")
    return img.convert("RGB")


def save_payload_txt(base_path_no_ext: str, payload: str) -> str:
    txt_path = base_path_no_ext + ".txt"
    with open(txt_path, "w", encoding="utf-8") as f:
        f.write(payload)
    return txt_path


def pdf_grid_config(layout: str):
    if layout == "4 (2x2)":
        return 2, 2
    if layout == "6 (3x2)":
        return 3, 2
    if layout == "8 (4x2)":
        return 4, 2
    return 2, 2


def create_labels_pdf(pdf_path: str, qr_img: Image.Image, payload: str,
                      layout: str, show_text: bool, title_text: str):
    c = canvas.Canvas(pdf_path, pagesize=A4)
    page_w, page_h = A4

    rows, cols = pdf_grid_config(layout)
    margin = 28
    gap = 14

    usable_w = page_w - 2 * margin - (cols - 1) * gap
    usable_h = page_h - 2 * margin - (rows - 1) * gap

    cell_w = usable_w / cols
    cell_h = usable_h / rows

    text_h = 34 if show_text else 0
    qr_size = min(cell_w, cell_h - text_h)

    qr_for_pdf = qr_img.copy().resize((1000, 1000), Image.NEAREST)
    qr_reader = ImageReader(qr_for_pdf)

    lines = [ln.strip() for ln in payload.splitlines() if ln.strip()]
    short_lines = lines[:4]
    short_text = "\n".join(short_lines)
    if len(lines) > 4:
        short_text += "\n..."

    for r in range(rows):
        for col in range(cols):
            x = margin + col * (cell_w + gap)
            y = page_h - margin - (r + 1) * cell_h - r * gap

            qr_x = x + (cell_w - qr_size) / 2
            qr_y = y + (cell_h - text_h - qr_size) / 2 + (text_h if show_text else 0)

            c.drawImage(
                qr_reader, qr_x, qr_y,
                width=qr_size, height=qr_size,
                preserveAspectRatio=True, mask='auto'
            )

            if show_text:
                tx = x + 6
                ty = y + 6

                if title_text.strip():
                    c.setFont("Helvetica-Bold", 8)
                    c.drawString(tx, ty + 22, title_text.strip()[:55])

                c.setFont("Helvetica", 7.5)
                yy = ty + 10
                for ln in short_text.splitlines():
                    c.drawString(tx, yy, ln[:70])
                    yy -= 9

            c.setLineWidth(0.3)
            c.rect(x, y, cell_w, cell_h)

    c.showPage()
    c.save()


class App(ctk.CTk):
    def __init__(self):
        super().__init__()

        ctk.set_appearance_mode("System")
        ctk.set_default_color_theme("blue")

        self.title(APP_TITLE)
        self.geometry("1220x720")
        self.minsize(1020, 640)
        self._update_prompted = False

        self.current_img: Image.Image | None = None
        self.current_payload: str = ""
        self.preview_ctk_img = None

        # Layout
        self.grid_columnconfigure(0, weight=2)
        self.grid_columnconfigure(1, weight=1)
        self.grid_rowconfigure(0, weight=1)

        left = ctk.CTkFrame(self, corner_radius=16)
        right = ctk.CTkFrame(self, corner_radius=16)
        left.grid(row=0, column=0, padx=14, pady=14, sticky="nsew")
        right.grid(row=0, column=1, padx=(0, 14), pady=14, sticky="nsew")

        left.grid_columnconfigure(0, weight=1)
        left.grid_rowconfigure(6, weight=1)

        # --- Title ---
        title = ctk.CTkLabel(left, text="Parça Listesi → QR", font=ctk.CTkFont(size=22, weight="bold"))
        title.grid(row=0, column=0, padx=14, pady=(14, 8), sticky="w")

        # --- Location row ---
        row_loc = ctk.CTkFrame(left, corner_radius=12)
        row_loc.grid(row=1, column=0, padx=14, pady=8, sticky="ew")
        row_loc.grid_columnconfigure(1, weight=1)

        ctk.CTkLabel(row_loc, text="Lokasyon / Raf:", width=120).grid(row=0, column=0, padx=10, pady=10, sticky="w")
        self.location_entry = ctk.CTkEntry(row_loc, placeholder_text="Örn: PALET 2 MALZEMELER VE ADETLERİ")
        self.location_entry.grid(row=0, column=1, padx=10, pady=10, sticky="ew")

        # --- Settings rows (split) ---
        # Row A: QR Ayarları + Genel seçenekler
        row_a = ctk.CTkFrame(left, corner_radius=12)
        row_a.grid(row=2, column=0, padx=14, pady=8, sticky="ew")
        for i in range(12):
            row_a.grid_columnconfigure(i, weight=1)

        ctk.CTkLabel(row_a, text="Hata:", width=45).grid(row=0, column=0, padx=(10, 6), pady=10, sticky="w")
        self.err_opt = ctk.CTkOptionMenu(row_a, values=["L (düşük)", "M (orta)", "Q (yüksek)", "H (çok yüksek)"])
        self.err_opt.set(DEFAULT_ERR)
        self.err_opt.grid(row=0, column=1, padx=(0, 10), pady=10, sticky="ew")

        ctk.CTkLabel(row_a, text="Ölçek:", width=50).grid(row=0, column=2, padx=(10, 6), pady=10, sticky="w")
        self.scale_entry = ctk.CTkEntry(row_a, width=70)
        self.scale_entry.insert(0, str(DEFAULT_SCALE))
        self.scale_entry.grid(row=0, column=3, padx=(0, 10), pady=10, sticky="w")

        ctk.CTkLabel(row_a, text="Border:", width=55).grid(row=0, column=4, padx=(10, 6), pady=10, sticky="w")
        self.border_entry = ctk.CTkEntry(row_a, width=70)
        self.border_entry.insert(0, str(DEFAULT_BORDER))
        self.border_entry.grid(row=0, column=5, padx=(0, 10), pady=10, sticky="w")

        self.numbering_var = ctk.BooleanVar(value=False)
        self.split_commas_var = ctk.BooleanVar(value=False)
        self.include_location_var = ctk.BooleanVar(value=True)

        self.chk_numbering = ctk.CTkCheckBox(row_a, text="Numaralandır", variable=self.numbering_var)
        self.chk_numbering.grid(row=0, column=6, padx=10, pady=10, sticky="w")

        self.chk_split = ctk.CTkCheckBox(row_a, text="Virgülle böl", variable=self.split_commas_var)
        self.chk_split.grid(row=0, column=7, padx=10, pady=10, sticky="w")

        self.chk_loc = ctk.CTkCheckBox(row_a, text="Lokasyon QR'a", variable=self.include_location_var)
        self.chk_loc.grid(row=0, column=8, padx=10, pady=10, sticky="w")

        # Hint
        hint = ctk.CTkLabel(left, text="Parçaları satır satır yaz (istersen tek satırda virgülle ayır).",
                            font=ctk.CTkFont(size=12))
        hint.grid(row=3, column=0, padx=14, pady=(6, 0), sticky="w")

        # Text area
        self.textbox = ctk.CTkTextbox(left, corner_radius=12)
        self.textbox.grid(row=6, column=0, padx=14, pady=10, sticky="nsew")
        self.textbox.insert(
            "1.0",
            "0010-01 1 ADET\n0107-00 2 ADET\nMAKAS_KAYNAK_12 1 ADET\n0103-01 1 ADET\n"
        )

        # Buttons row
        btn_row = ctk.CTkFrame(left, corner_radius=12)
        btn_row.grid(row=7, column=0, padx=14, pady=(0, 14), sticky="ew")
        for i in range(6):
            btn_row.grid_columnconfigure(i, weight=1)

        self.save_txt_var = ctk.BooleanVar(value=True)

        self.gen_btn = ctk.CTkButton(btn_row, text="QR Oluştur / Güncelle", command=self.on_generate)
        self.gen_btn.grid(row=0, column=0, padx=10, pady=10, sticky="ew")

        self.save_btn = ctk.CTkButton(btn_row, text="PNG/JPG Kaydet", state="disabled", command=self.on_save_image)
        self.save_btn.grid(row=0, column=1, padx=10, pady=10, sticky="ew")

        self.pdf_btn = ctk.CTkButton(btn_row, text="PDF Etiket Bas", state="disabled", command=self.on_save_pdf)
        self.pdf_btn.grid(row=0, column=2, padx=10, pady=10, sticky="ew")

        self.chk_save_txt = ctk.CTkCheckBox(btn_row, text="Yanına .txt", variable=self.save_txt_var)
        self.chk_save_txt.grid(row=0, column=3, padx=10, pady=10, sticky="w")

        self.clear_btn = ctk.CTkButton(btn_row, text="Temizle", fg_color="#444444", hover_color="#333333", command=self.on_clear)
        self.clear_btn.grid(row=0, column=4, padx=10, pady=10, sticky="ew")

        # --- Right panel ---
        right.grid_rowconfigure(2, weight=1)
        right.grid_columnconfigure(0, weight=1)

        prev_title = ctk.CTkLabel(right, text="Önizleme", font=ctk.CTkFont(size=18, weight="bold"))
        prev_title.grid(row=0, column=0, padx=14, pady=(14, 6), sticky="w")

        self.preview_label = ctk.CTkLabel(right, text="Henüz QR yok", corner_radius=12)
        self.preview_label.grid(row=1, column=0, padx=14, pady=(6, 10), sticky="nsew")

        pdf_opts = ctk.CTkFrame(right, corner_radius=12)
        pdf_opts.grid(row=2, column=0, padx=14, pady=10, sticky="nsew")
        pdf_opts.grid_columnconfigure(1, weight=1)

        ctk.CTkLabel(pdf_opts, text="PDF Yerleşim:").grid(row=0, column=0, padx=10, pady=(10, 6), sticky="w")
        self.pdf_layout_opt = ctk.CTkOptionMenu(pdf_opts, values=["4 (2x2)", "6 (3x2)", "8 (4x2)"])
        self.pdf_layout_opt.set("6 (3x2)")
        self.pdf_layout_opt.grid(row=0, column=1, padx=10, pady=(10, 6), sticky="ew")

        self.pdf_text_var = ctk.BooleanVar(value=True)
        self.pdf_text_chk = ctk.CTkCheckBox(pdf_opts, text="QR altına kısa metin yaz", variable=self.pdf_text_var)
        self.pdf_text_chk.grid(row=1, column=0, columnspan=2, padx=10, pady=(4, 8), sticky="w")

        self.info_label = ctk.CTkLabel(right, text="", justify="left", wraplength=360)
        self.info_label.grid(row=3, column=0, padx=14, pady=(0, 14), sticky="ew")

        self.after(1500, self.check_launcher_update_background)

    def on_clear(self):
        self.location_entry.delete(0, "end")
        self.textbox.delete("1.0", "end")
        self.current_img = None
        self.current_payload = ""
        self.preview_ctk_img = None
        self.preview_label.configure(image=None, text="Henüz QR yok")
        self.info_label.configure(text="")
        self.save_btn.configure(state="disabled")
        self.pdf_btn.configure(state="disabled")

    def on_generate(self):
        try:
            location = self.location_entry.get()
            items_text = self.textbox.get("1.0", "end-1c")
            items = split_pasted_text(items_text, self.split_commas_var.get())

            payload_text = build_plaintext_payload(
                location=location,
                items=items,
                numbering=self.numbering_var.get(),
                include_location=self.include_location_var.get(),
            )

            qr_data = payload_text

            self.current_payload = payload_text  # .txt kaydetmek için gerçek metni sakla

            # QR ayarları
            try:
                box_size = int(self.scale_entry.get().strip())
            except:
                box_size = DEFAULT_SCALE
                self.scale_entry.delete(0, "end")
                self.scale_entry.insert(0, str(DEFAULT_SCALE))

            try:
                border = int(self.border_entry.get().strip())
            except:
                border = DEFAULT_BORDER
                self.border_entry.delete(0, "end")
                self.border_entry.insert(0, str(DEFAULT_BORDER))

            err_level = self.err_opt.get()

            img = make_qr_image(qr_data, box_size=box_size, border=border, err_level=err_level)
            self.current_img = img

            preview = img.copy()
            preview.thumbnail((430, 430))
            self.preview_ctk_img = ctk.CTkImage(light_image=preview, dark_image=preview, size=preview.size)
            self.preview_label.configure(image=self.preview_ctk_img, text="")

            self.save_btn.configure(state="normal")
            self.pdf_btn.configure(state="normal")

            data_len = len(payload_text.encode("utf-8"))
            self.info_label.configure(
                text=f"UTF-8 byte: {data_len}\nHata: {err_level}\nÖlçek: {box_size}  Border: {border}"
            )

        except Exception as e:
            messagebox.showerror("Hata", str(e))

    def on_save_image(self):
        if self.current_img is None:
            return

        file_path = filedialog.asksaveasfilename(
            title="QR Kaydet",
            defaultextension=".png",
            filetypes=[("PNG", "*.png"), ("JPEG", "*.jpg"), ("JPEG", "*.jpeg")],
        )
        if not file_path:
            return

        try:
            ext = file_path.lower().split(".")[-1]
            img = self.current_img

            if ext in ("jpg", "jpeg"):
                img = img.convert("RGB")
                img.save(file_path, format="JPEG", quality=95, optimize=True)
            else:
                img.save(file_path, format="PNG", optimize=True)

            saved_txt = None
            if self.save_txt_var.get():
                base_no_ext = os.path.splitext(file_path)[0]
                saved_txt = save_payload_txt(base_no_ext, self.current_payload)

            if saved_txt:
                messagebox.showinfo("Kaydedildi", f"QR kaydedildi:\n{file_path}\n\nMetin txt:\n{saved_txt}")
            else:
                messagebox.showinfo("Kaydedildi", f"QR kaydedildi:\n{file_path}")

        except Exception as e:
            messagebox.showerror("Hata", f"Kaydederken hata:\n{e}")

    def on_save_pdf(self):
        if self.current_img is None:
            return

        pdf_path = filedialog.asksaveasfilename(
            title="PDF Etiket Kaydet",
            defaultextension=".pdf",
            filetypes=[("PDF", "*.pdf")],
        )
        if not pdf_path:
            return

        try:
            layout = self.pdf_layout_opt.get()
            show_text = self.pdf_text_var.get()
            title_text = (self.location_entry.get() or "").strip()

            create_labels_pdf(
                pdf_path=pdf_path,
                qr_img=self.current_img,
                payload=self.current_payload,
                layout=layout,
                show_text=show_text,
                title_text=title_text
            )
            messagebox.showinfo("Kaydedildi", f"PDF kaydedildi:\n{pdf_path}")

        except Exception as e:
            messagebox.showerror("Hata", f"PDF oluştururken hata:\n{e}")


    def check_launcher_update_background(self) -> None:
        if self._update_prompted:
            return

        def worker() -> None:
            try:
                payload = _http_get_json(PROGRAMS_URL)
                latest = ""
                for item in payload.get("programs", []):
                    if item.get("id") == PROGRAM_ID:
                        latest = str(item.get("version", "")).strip()
                        break
                if latest and _semver_tuple(latest) > _semver_tuple(APP_VERSION):
                    self._update_prompted = True
                    self._show_update_prompt(latest)
            except Exception:
                pass

        threading.Thread(target=worker, daemon=True).start()

    def _show_update_prompt(self, latest: str) -> None:
        if messagebox.askyesno(
            "Guncelleme mevcut",
            f"Mevcut surum: {APP_VERSION}\nYeni surum: {latest}\n\n"
            "Guncellemeyi Saydut Launcher ile yapmak ister misin?",
        ):
            self.open_launcher_update()

    def open_launcher_update(self) -> None:
        launcher_hint_path = r"C:\Saydut\launcher_path.txt"
        launcher_candidates = [
            r"C:\Saydut\SaydutLauncher\SaydutLauncher.exe",
            r"C:\Saydut\Saydut Launcher\SaydutLauncher.exe",
            r"C:\Saydut\SaydutLauncher\Saydut Launcher.exe",
        ]

        launcher_path = None
        if os.path.exists(launcher_hint_path):
            try:
                with open(launcher_hint_path, "r", encoding="utf-8") as handle:
                    candidate = handle.read().strip()
                if candidate and os.path.exists(candidate):
                    launcher_path = candidate
            except OSError:
                launcher_path = None

        if not launcher_path:
            for candidate in launcher_candidates:
                if os.path.exists(candidate):
                    launcher_path = candidate
                    break

        if launcher_path:
            os.startfile(launcher_path)  # type: ignore[attr-defined]
            return

        messagebox.showinfo(
            "Launcher gerekli",
            "Guncelleme icin Saydut Launcher gerekli. Launcher sayfasini aciyorum.",
        )
        webbrowser.open("https://www.saydut.com")


if __name__ == "__main__":
    app = App()
    app.mainloop()
