"""
Conversor & Editor PDF (Master Version)
- Logic Mode 1: Restaurado al algoritmo de umbral puro (Alpha).
- UI: Signo % ajustado, menús corregidos, progreso global.
- System: Parche CMD y multiprocesamiento.
"""

import os
import sys
import json
import threading
import queue
import subprocess
import re
from concurrent.futures import ThreadPoolExecutor, as_completed
from functools import partial
from pathlib import Path
from typing import Optional, List

import tkinter as tk
from tkinter import filedialog, messagebox

# --- PARCHE WINDOWS (Anti-Spam CMD) ---
if os.name == 'nt':
    original_popen = subprocess.Popen
    def new_popen(*args, **kwargs):
        startupinfo = kwargs.get('startupinfo', subprocess.STARTUPINFO())
        startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW
        startupinfo.wShowWindow = subprocess.SW_HIDE
        kwargs['startupinfo'] = startupinfo
        creationflags = kwargs.get('creationflags', 0)
        creationflags |= 0x08000000 
        kwargs['creationflags'] = creationflags
        return original_popen(*args, **kwargs)
    subprocess.Popen = new_popen

# --- LIBRERÍAS ---
try:
    import ttkbootstrap as tb
    from ttkbootstrap.constants import *
except: raise RuntimeError("Falta 'ttkbootstrap'")

try:
    from pdf2image import convert_from_path, pdfinfo_from_path
except: raise RuntimeError("Falta 'pdf2image'")

try:
    from PIL import Image
    import PIL
    PIL_VERSION = getattr(PIL, "__version__", "Unknown")
except: raise RuntimeError("Falta 'Pillow'")

# --- CONFIGURACIÓN ---
BASE_DIR = getattr(sys, "_MEIPASS", os.path.dirname(os.path.abspath(__file__)))
POPPLER_PATH = os.path.join(BASE_DIR, "poppler", "bin")
CONFIG_PATH = os.path.join(BASE_DIR, "conversor_config.json")

DEFAULT_CONFIG = {
    "dpi": 120, "format": "WEBP", "quality": 60, "mode": "L",
    "workers": 2, "theme": "cyborg", "remember": True,
    "merge_quality": 65, "merge_max_width": 1250
}

ui_queue = queue.Queue()

def load_config():
    try:
        if os.path.exists(CONFIG_PATH):
            with open(CONFIG_PATH, "r", encoding="utf-8") as f:
                cfg = json.load(f)
                for k, v in DEFAULT_CONFIG.items(): cfg.setdefault(k, v)
                return cfg
    except: pass
    return DEFAULT_CONFIG.copy()

def save_config(cfg):
    try:
        with open(CONFIG_PATH, "w", encoding="utf-8") as f: json.dump(cfg, f, indent=2)
    except: pass

def check_poppler():
    for c in ["pdftoppm.exe", "pdftoppm", "pdftocairo.exe"]:
        if os.path.exists(os.path.join(POPPLER_PATH, c)): return True
    return True

def safe_name(s: str) -> str:
    return "".join(c for c in s if c.isalnum() or c in (" ", ".", "_", "-")).strip()[:200]

def get_pdf_pages(pdf_path):
    try:
        info = pdfinfo_from_path(pdf_path, poppler_path=POPPLER_PATH)
        return int(info.get("Pages") or info.get("pages") or 0)
    except: return 0

def sort_key_last_number(filepath: str):
    nums = re.findall(r'\d+', Path(filepath).stem)
    return int(nums[-1]) if nums else 0

# --- WORKER TAB 1 (PDF -> IMAGEN) ---
def convert_pdf_worker(pdf_path, out_dir, cfg, progress_cb):
    try:
        total = get_pdf_pages(pdf_path) or 0
        pages_done = 0
        
        # Helper interno para procesar y guardar
        def save_img(img, idx, base):
            # --- LÓGICA DE CONVERSIÓN (RESTAURADA DEL ALPHA) ---
            if cfg["mode"] == "L":
                img = img.convert("L")
            elif cfg["mode"] == "1":
                # TÉCNICA DEL ALPHA: Umbral duro (Threshold)
                # Convierte a gris y luego fuerza a B/N (0 o 255) sin puntos intermedios
                img = img.convert("L").point(lambda x: 0 if x < 128 else 255, mode="1")
            else:
                img = img.convert("RGB")
            # --------------------------------------------------

            fmt = cfg["format"].upper()
            path = os.path.join(out_dir, f"{base}_{idx}.{fmt.lower()}")
            
            if fmt == "JPEG":
                # JPEG no soporta modo "1" real, se guardará como gris/RGB visualmente B/N
                img_save = img.convert("L") if img.mode == "1" else img
                img_save.save(path, "JPEG", quality=int(cfg["quality"]))
            elif fmt == "WEBP":
                img.save(path, "WEBP", quality=int(cfg["quality"]))
            else:
                # PNG soporta modo "1" real (1 bit por pixel)
                opts = {"optimize": True} if cfg["mode"] == "1" else {}
                img.save(path, "PNG", **opts)
            
            return path

        base = safe_name(Path(pdf_path).stem)
        
        if total > 0:
            for p in range(1, total+1):
                imgs = convert_from_path(pdf_path, dpi=cfg["dpi"], first_page=p, last_page=p, poppler_path=POPPLER_PATH)
                if imgs:
                    save_img(imgs[0], p, base)
                    pages_done += 1
                    progress_cb(pages_done, total, None)
                else:
                    progress_cb(p, total, None)
        else:
            # Fallback
            imgs = convert_from_path(pdf_path, dpi=cfg["dpi"], poppler_path=POPPLER_PATH)
            total = len(imgs)
            for i, img in enumerate(imgs, start=1):
                save_img(img, i, base)
                progress_cb(i, total, None)

        return {"pdf": pdf_path, "status": "ok", "pages": total}
    except Exception as e:
        return {"pdf": pdf_path, "status": "error", "error": str(e)}

# --- WORKER TAB 2 (CARPETAS -> PDF) ---
def process_folder_to_pdf_worker(folder_path, max_width, quality):
    try:
        folder = Path(folder_path)
        exts = {".jpg", ".jpeg", ".png", ".webp", ".bmp"}
        files = [str(f) for f in folder.iterdir() if f.suffix.lower() in exts]
        
        if not files: return {"status": "error", "error": f"Carpeta vacía: {folder.name}"}

        files.sort(key=sort_key_last_number)
        processed_imgs = []
        first_img = None

        for f in files:
            img = Image.open(f)
            if img.mode != "RGB": img = img.convert("RGB")
            if max_width > 0 and img.width > max_width:
                ratio = max_width / float(img.width)
                new_height = int(float(img.height) * ratio)
                img = img.resize((max_width, new_height), Image.Resampling.LANCZOS)
            
            if first_img is None: first_img = img
            else: processed_imgs.append(img)

        out_pdf = str(folder / f"{folder.name}.pdf")
        first_img.save(out_pdf, "PDF", resolution=100.0, save_all=True, append_images=processed_imgs, quality=quality, optimize=True)
        return {"status": "ok", "file": out_pdf, "count": len(files)}
    except Exception as e:
        return {"status": "error", "error": str(e)}

# --- APP ---
class App:
    def __init__(self, root):
        self.root = root
        self.cfg = load_config()
        
        self.tasks_convert = []
        self.out_dir_convert = ""
        self.tasks_merge = []
        
        self.total_pages_global = 0
        self.completed_pages_global = 0
        
        self.executor = None
        self.futures = []

        self._build_ui()
        self.root.after(150, self._process_ui_queue)

    def _build_ui(self):
        self.root.title("Toolbox PDF Master")
        tb.Style(self.cfg.get("theme", "cyborg"))

        self.notebook = tb.Notebook(self.root, bootstyle="dark")
        self.notebook.pack(fill="both", expand=True, padx=10, pady=10)

        self.tab_convert = tb.Frame(self.notebook)
        self.tab_merge = tb.Frame(self.notebook)

        self.notebook.add(self.tab_convert, text="PDF → Imágenes")
        self.notebook.add(self.tab_merge, text="Carpetas → Libros PDF")

        self._build_convert_tab(self.tab_convert)
        self._build_merge_tab(self.tab_merge)

        self.status_bar = tb.Label(self.root, text=f"Pillow: {PIL_VERSION}", anchor="e")
        self.status_bar.pack(fill="x", padx=10, pady=2)

    def _build_convert_tab(self, parent):
        # Buttons
        row = tb.Frame(parent)
        row.pack(fill="x", pady=10)
        tb.Button(row, text="Seleccionar PDFs", bootstyle="info", command=self.select_pdfs).pack(side=LEFT, padx=5)
        tb.Button(row, text="Limpiar", bootstyle="warning-outline", command=self.clear_list_convert).pack(side=LEFT, padx=5)
        tb.Button(row, text="Salida", bootstyle="secondary", command=self.select_output).pack(side=LEFT, padx=5)

        # Config
        opts = tb.Labelframe(parent, text="Configuración Extracción", padding=10)
        opts.pack(fill="x", pady=5)
        
        tb.Label(opts, text="DPI:").grid(row=0, column=0, sticky="w", padx=5)
        self.var_dpi = tb.IntVar(value=self.cfg.get("dpi", 120))
        tb.Combobox(opts, textvariable=self.var_dpi, values=[72, 100, 120, 150, 200, 300], state="readonly", width=5).grid(row=0, column=1, sticky="w")

        tb.Label(opts, text="Formato:").grid(row=0, column=2, sticky="w", padx=5)
        self.var_format = tb.StringVar(value=self.cfg.get("format", "WEBP"))
        tb.Combobox(opts, textvariable=self.var_format, values=["WEBP", "JPEG", "PNG"], state="readonly", width=8).grid(row=0, column=3, sticky="w")

        tb.Label(opts, text="Calidad:").grid(row=0, column=4, sticky="w", padx=5)
        self.var_quality = tb.IntVar(value=self.cfg.get("quality", 60))
        tb.Spinbox(opts, from_=10, to=100, increment=5, textvariable=self.var_quality, width=5).grid(row=0, column=5)

        tb.Label(opts, text="Modo:").grid(row=1, column=0, sticky="w", padx=5, pady=5)
        self.var_mode = tb.StringVar(value=self.cfg.get("mode", "L"))
        tb.Combobox(opts, textvariable=self.var_mode, values=["RGB", "L", "1"], state="readonly", width=8).grid(row=1, column=1, sticky="w")

        tb.Label(opts, text="Hilos:").grid(row=1, column=2, sticky="w", padx=5)
        self.var_workers = tb.IntVar(value=self.cfg.get("workers", 2))
        tb.Spinbox(opts, from_=1, to=16, textvariable=self.var_workers, width=5).grid(row=1, column=3)

        self.var_remember = tb.BooleanVar(value=self.cfg.get("remember", True))
        tb.Checkbutton(opts, text="Recordar config", variable=self.var_remember).grid(row=1, column=4, padx=15)

        # Listbox
        lf = tb.Labelframe(parent, text="Archivos a Convertir", padding=5)
        lf.pack(fill="both", expand=True, pady=5)
        self.listbox_convert = tk.Listbox(lf, height=6)
        self.listbox_convert.pack(side=LEFT, fill="both", expand=True)
        scr = tb.Scrollbar(lf, command=self.listbox_convert.yview)
        scr.pack(side=LEFT, fill="y")
        self.listbox_convert.configure(yscrollcommand=scr.set)

        # --- AREA DE PROGRESO ---
        meter_frame = tb.Frame(parent)
        meter_frame.pack(pady=5)
        
        self.lbl_global_progress = tb.Label(meter_frame, text="Total: 0/0 Páginas", bootstyle="primary", font=("Helvetica", 10, "bold"))
        self.lbl_global_progress.pack(side=TOP, pady=(0, 5))

        # Barra sin texto automático
        self.meter = tb.Meter(meter_frame, amounttotal=100, amountused=0, metersize=120, bootstyle="primary", showtext=True) 
        self.meter.pack(side=TOP)
        
        # Etiqueta manual para el % bajada 3px (rely=0.62)
        self.lbl_pct = tb.Label(self.meter, text="%", font=("Helvetica", 10), bootstyle="primary")
        self.lbl_pct.place(relx=0.5, rely=0.30, anchor="center") 
        
        self.status_label_v3 = tb.Label(meter_frame, text="Esperando...", bootstyle="secondary")
        self.status_label_v3.pack(side=TOP, pady=2)

        act = tb.Frame(parent)
        act.pack(fill="x", pady=5)
        self.btn_convert = tb.Button(act, text="INICIAR CONVERSIÓN", bootstyle="success", command=self.start_conversion)
        self.btn_convert.pack(side=LEFT, padx=5, fill="x", expand=True)
        self.btn_cancel = tb.Button(act, text="Cancelar", bootstyle="danger", command=self.cancel, state="disabled")
        self.btn_cancel.pack(side=LEFT, padx=5)

    def _build_merge_tab(self, parent):
        info = "Selecciona CARPETAS. Cada carpeta se convertirá en un archivo PDF independiente."
        tb.Label(parent, text=info, bootstyle="info", justify="center").pack(pady=10)
        
        fr = tb.Frame(parent); fr.pack(fill="x", pady=5)
        tb.Button(fr, text="Agregar Carpetas", bootstyle="info", command=self.sel_folders_v4).pack(side=LEFT, padx=5)
        tb.Button(fr, text="Limpiar", bootstyle="warning-outline", command=lambda: self.listbox_merge.delete(0, tk.END)).pack(side=LEFT, padx=5)
        
        c_frm = tb.Labelframe(parent, text="Opciones de Compresión", padding=10)
        c_frm.pack(fill="x", pady=5)
        tb.Label(c_frm, text="Ancho Máx (px):").pack(side=LEFT, padx=5)
        self.v_m_width = tb.IntVar(value=self.cfg.get("merge_max_width", 1250))
        tb.Spinbox(c_frm, from_=800, to=3000, increment=100, textvariable=self.v_m_width, width=6).pack(side=LEFT)
        tb.Label(c_frm, text="Calidad JPG:").pack(side=LEFT, padx=10)
        self.v_m_qual = tb.IntVar(value=self.cfg.get("merge_quality", 65))
        tb.Spinbox(c_frm, from_=30, to=90, increment=5, textvariable=self.v_m_qual, width=5).pack(side=LEFT)

        self.listbox_merge = tk.Listbox(parent, height=8)
        self.listbox_merge.pack(fill="both", expand=True, padx=5, pady=5)
        
        self.btn_merge = tb.Button(parent, text="PROCESAR CARPETAS", bootstyle="success", command=self.run_merge_v4)
        self.btn_merge.pack(fill="x", padx=5, pady=10)

    # --- LOGIC TAB 1 ---
    def select_pdfs(self):
        fs = filedialog.askopenfilenames(filetypes=[("PDF", "*.pdf")])
        if fs:
            self.tasks_convert = list(fs)
            self.listbox_convert.delete(0, tk.END)
            for f in self.tasks_convert: self.listbox_convert.insert(tk.END, f)
            self._estimate_pages()

    def _estimate_pages(self):
        total = 0
        for f in self.tasks_convert:
            total += get_pdf_pages(f)
        self.total_pages_global = total
        self.completed_pages_global = 0
        self.lbl_global_progress.configure(text=f"Total: 0/{total} Páginas")

    def clear_list_convert(self):
        if self.executor: return
        self.tasks_convert = []
        self.listbox_convert.delete(0, tk.END)
        self.lbl_global_progress.configure(text="Total: 0/0 Páginas")

    def select_output(self):
        d = filedialog.askdirectory()
        if d: self.out_dir_convert = d

    def start_conversion(self):
        if not self.tasks_convert: return
        if not self.out_dir_convert: self.out_dir_convert = os.path.dirname(self.tasks_convert[0])

        self.cfg.update({
            "dpi": self.var_dpi.get(), "format": self.var_format.get(),
            "quality": self.var_quality.get(), "mode": self.var_mode.get(),
            "workers": self.var_workers.get()
        })
        save_config(self.cfg)

        self.meter.configure(amountused=0)
        self.status_label_v3.configure(text="Preparando...")
        self.completed_pages_global = 0
        self._estimate_pages() 

        self.btn_convert.configure(state="disabled")
        self.btn_cancel.configure(state="normal")
        
        self.executor = ThreadPoolExecutor(max_workers=self.var_workers.get())
        self.futures = []
        cfg_run = self.cfg.copy()

        for pdf in self.tasks_convert:
            name = safe_name(Path(pdf).stem)
            out = os.path.join(self.out_dir_convert, name)
            os.makedirs(out, exist_ok=True)
            
            fut = self.executor.submit(convert_pdf_worker, pdf, out, cfg_run, partial(self._progress_cb, pdf_path=pdf))
            self.futures.append(fut)
        
        threading.Thread(target=self._monitor_conversion, daemon=True).start()

    def _monitor_conversion(self):
        for fut in as_completed(self.futures):
            try:
                res = fut.result()
                ui_queue.put({"type": "finished_pdf", "res": res})
            except Exception as e:
                ui_queue.put({"type": "error", "msg": str(e)})
        ui_queue.put({"type": "all_done"})

    # --- LOGIC TAB 2 ---
    def sel_folders_v4(self):
        d = filedialog.askdirectory(title="Selecciona carpeta")
        if d: self.listbox_merge.insert(tk.END, d)

    def run_merge_v4(self):
        folders = self.listbox_merge.get(0, tk.END)
        if not folders: return
        self.btn_merge.configure(state="disabled", text="Procesando...")
        self.cfg.update({"merge_max_width": self.v_m_width.get(), "merge_quality": self.v_m_qual.get()})
        save_config(self.cfg)
        
        self.executor = ThreadPoolExecutor(max_workers=1) 
        for folder in folders:
            self.executor.submit(self._merge_wrap, folder, self.v_m_width.get(), self.v_m_qual.get())

    def _merge_wrap(self, folder, w, q):
        res = process_folder_to_pdf_worker(folder, w, q)
        ui_queue.put({"type": "merge_done", "res": res})

    # --- COMMON ---
    def cancel(self):
        if self.executor: self.executor.shutdown(wait=False); self.executor = None
        self.btn_convert.configure(state="normal")
        self.btn_cancel.configure(state="disabled")
        self.status_label_v3.configure(text="Cancelado")

    def _progress_cb(self, p, t, s, pdf_path=None):
        ui_queue.put({"type": "page", "per": p, "tot": t, "pdf": pdf_path})

    def _process_ui_queue(self):
        while True:
            try: item = ui_queue.get_nowait()
            except queue.Empty: break

            t = item["type"]
            if t == "page":
                cur_pct = int((item["per"]/ (item["tot"] or 1))*100)
                try: self.meter.configure(amountused=cur_pct)
                except: pass
                
                pdf_name = Path(item.get("pdf", "")).stem
                self.status_label_v3.configure(text=f"Extrayendo: {pdf_name}")

                self.completed_pages_global += 1
                if self.completed_pages_global > self.total_pages_global:
                    self.total_pages_global = self.completed_pages_global 
                
                self.lbl_global_progress.configure(text=f"Total: {self.completed_pages_global}/{self.total_pages_global} Páginas")

            elif t == "finished_pdf":
                try: self.meter.configure(amountused=0)
                except: pass
                res = item["res"]
                self.status_label_v3.configure(text=f"¡Completado!: {Path(res['pdf']).stem}")

            elif t == "all_done":
                self.btn_convert.configure(state="normal")
                self.btn_cancel.configure(state="disabled")
                try: self.meter.configure(amountused=0)
                except: pass
                self.status_label_v3.configure(text="Lista finalizada.")
                messagebox.showinfo("Proceso Terminado", "Se han convertido todos los PDFs de la lista.")

            elif t == "merge_done":
                r = item["res"]
                if r["status"] == "ok":
                    self.status_bar.configure(text=f"Libro creado: {Path(r['file']).name}")
                else:
                    messagebox.showerror("Error", r["error"])
                self.btn_merge.configure(state="normal", text="PROCESAR CARPETAS")

        self.root.after(150, self._process_ui_queue)

def main():
    global POPPLER_PATH
    if not check_poppler():
        if messagebox.askyesno("Poppler", "Falta Poppler. ¿Buscar carpeta 'bin'?"):
            d = filedialog.askdirectory()
            if d: POPPLER_PATH = d
            else: return
        else: return

    cfg = load_config()
    root = tb.Window(themename=cfg.get("theme", "cyborg"))
    App(root)
    root.geometry("950x750")
    root.mainloop()

if __name__ == "__main__":
    main()
