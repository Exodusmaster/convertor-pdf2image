"""
Conversor PDF -> Imágenes (portable)
- Interfaz con ttkbootstrap (tema cyborg)
- Solución al bug de menús cortados usando Combobox
"""

import os
import sys
import json
import threading
import queue
from concurrent.futures import ThreadPoolExecutor, as_completed
from functools import partial
from pathlib import Path
from typing import Optional, List

import tkinter as tk
from tkinter import filedialog, messagebox

# GUI
try:
    import ttkbootstrap as tb
    from ttkbootstrap.constants import *
except Exception as e:
    raise RuntimeError("Instala 'ttkbootstrap' (pip install ttkbootstrap) antes de ejecutar.") from e

# pdf conversion
try:
    from pdf2image import convert_from_path, pdfinfo_from_path
except Exception as e:
    raise RuntimeError("Instala 'pdf2image' (pip install pdf2image) y coloca Poppler en poppler/bin.") from e

try:
    from PIL import Image
    import PIL
    PIL_VERSION = getattr(PIL, "__version__", "Unknown")
except Exception:
    raise RuntimeError("Instala 'Pillow' (pip install pillow) antes de ejecutar.")

# ------- Paths / config -------
BASE_DIR = getattr(sys, "_MEIPASS", os.path.dirname(os.path.abspath(__file__)))
POPPLER_PATH = os.path.join(BASE_DIR, "poppler", "bin")
CONFIG_PATH = os.path.join(BASE_DIR, "conversor_config.json")

DEFAULT_CONFIG = {
    "dpi": 120,
    "format": "WEBP",
    "quality": 60,
    "mode": "L",            # RGB, L, 1
    "workers": max(1, (os.cpu_count() or 2) // 2),
    "theme": "cyborg",
    "remember": True
}

ui_queue = queue.Queue()

# ------- util -------
def load_config():
    try:
        if os.path.exists(CONFIG_PATH):
            with open(CONFIG_PATH, "r", encoding="utf-8") as f:
                cfg = json.load(f)
                # ensure keys
                for k, v in DEFAULT_CONFIG.items():
                    cfg.setdefault(k, v)
                return cfg
    except Exception:
        pass
    return DEFAULT_CONFIG.copy()

def save_config(cfg):
    try:
        with open(CONFIG_PATH, "w", encoding="utf-8") as f:
            json.dump(cfg, f, indent=2)
    except Exception as e:
        print("No se pudo guardar config:", e)

def check_poppler():
    # detect a common poppler exe on Windows or unix
    candidates = ["pdftoppm.exe", "pdftoppm", "pdftocairo.exe", "pdftocairo"]
    for c in candidates:
        if os.path.exists(os.path.join(POPPLER_PATH, c)):
            return True
    return True  # leave True so pdf2image will try PATH

def safe_name(s: str) -> str:
    keep = (" ", ".", "_", "-")
    return "".join(c for c in s if c.isalnum() or c in keep).strip()[:200]

def get_pdf_pages(pdf_path: str) -> Optional[int]:
    try:
        info = pdfinfo_from_path(pdf_path, poppler_path=POPPLER_PATH)
        pages = info.get("Pages") or info.get("pages")
        return int(pages) if pages else None
    except Exception:
        return None

# ------- conversion worker -------
def convert_pdf_worker(pdf_path: str, out_dir: str, cfg: dict, progress_cb):
    try:
        total = get_pdf_pages(pdf_path) or 0
        pages_done = 0

        if total and total > 0:
            for p in range(1, total+1):
                imgs = convert_from_path(pdf_path, dpi=cfg["dpi"], first_page=p, last_page=p, poppler_path=POPPLER_PATH)
                if not imgs:
                    progress_cb(p, total, None)
                    continue
                img = imgs[0]
                
                # Conversión de modos
                if cfg["mode"] == "L":
                    img = img.convert("L")
                elif cfg["mode"] == "1":
                    img = img.convert("L").point(lambda x: 0 if x < 128 else 255, mode="1")
                else:
                    img = img.convert("RGB")

                base = safe_name(Path(pdf_path).stem)
                fmt = cfg["format"].upper()
                
                if fmt == "JPEG":
                    fname = f"{base}_{p}.jpg"
                    out_path = os.path.join(out_dir, fname)
                    img.save(out_path, "JPEG", quality=int(cfg["quality"]))
                elif fmt == "WEBP":
                    fname = f"{base}_{p}.webp"
                    out_path = os.path.join(out_dir, fname)
                    img.save(out_path, "WEBP", quality=int(cfg["quality"]))
                else:
                    fname = f"{base}_{p}.png"
                    out_path = os.path.join(out_dir, fname)
                    save_kwargs = {}
                    if cfg["mode"] == "1":
                        save_kwargs["optimize"] = True
                    img.save(out_path, "PNG", **save_kwargs)

                pages_done += 1
                progress_cb(pages_done, total, out_path)
        else:
            # Fallback si no se detectan páginas (lee todo en memoria)
            imgs = convert_from_path(pdf_path, dpi=cfg["dpi"], poppler_path=POPPLER_PATH)
            total = len(imgs)
            for i, img in enumerate(imgs, start=1):
                if cfg["mode"] == "L":
                    img = img.convert("L")
                elif cfg["mode"] == "1":
                    img = img.convert("L").point(lambda x: 0 if x < 128 else 255, mode="1")
                else:
                    img = img.convert("RGB")

                base = safe_name(Path(pdf_path).stem)
                fmt = cfg["format"].upper()
                
                if fmt == "JPEG":
                    fname = f"{base}_{i}.jpg"
                    out_path = os.path.join(out_dir, fname)
                    img.save(out_path, "JPEG", quality=int(cfg["quality"]))
                elif fmt == "WEBP":
                    fname = f"{base}_{i}.webp"
                    out_path = os.path.join(out_dir, fname)
                    img.save(out_path, "WEBP", quality=int(cfg["quality"]))
                else:
                    fname = f"{base}_{i}.png"
                    out_path = os.path.join(out_dir, fname)
                    img.save(out_path, "PNG")

                progress_cb(i, total, out_path)

        return {"pdf": pdf_path, "status": "ok", "pages": total}
    except Exception as e:
        return {"pdf": pdf_path, "status": "error", "error": str(e)}

# ------- GUI App -------
class App:
    def __init__(self, root):
        self.root = root
        self.cfg = load_config()
        self.tasks: List[str] = []
        self.output_dir: str = ""
        self.total_pages = 0
        self.completed_pages = 0
        self.executor: Optional[ThreadPoolExecutor] = None
        self.futures = []
        self.cancel_flag = threading.Event()
        self._build_ui()
        self.root.after(150, self._process_ui_queue)

    def _build_ui(self):
        self.root.title("Conversor PDF → Imágenes")
        tb.Style(self.cfg.get("theme", DEFAULT_CONFIG["theme"]))

        main = tb.Frame(self.root, padding=10)
        main.pack(fill="both", expand=True)

        # buttons row
        row = tb.Frame(main)
        row.pack(fill="x", pady=6)
        tb.Button(row, text="Seleccionar PDFs", bootstyle="info", command=self.select_pdfs).pack(side=LEFT, padx=6)
        tb.Button(row, text="Limpiar lista", bootstyle="warning-outline", command=self.clear_list).pack(side=LEFT, padx=6)
        tb.Button(row, text="Carpeta destino", bootstyle="secondary", command=self.select_output).pack(side=LEFT, padx=6)

        # --- OPCIONES (USANDO COMBOBOX PARA EVITAR ERRORES VISUALES) ---
        opts = tb.Labelframe(main, text="Opciones", padding=8)
        opts.pack(fill="x", pady=6)
        
        # DPI
        tb.Label(opts, text="DPI:").grid(row=0, column=0, sticky="w", padx=6, pady=4)
        self.var_dpi = tb.IntVar(value=self.cfg.get("dpi", DEFAULT_CONFIG["dpi"]))
        cb_dpi = tb.Combobox(opts, textvariable=self.var_dpi, values=[72, 100, 120, 150, 200, 300], state="readonly", width=5)
        cb_dpi.grid(row=0, column=1, sticky="w")

        # Formato
        tb.Label(opts, text="Formato:").grid(row=0, column=2, sticky="w", padx=6, pady=4)
        self.var_format = tb.StringVar(value=self.cfg.get("format", DEFAULT_CONFIG["format"]))
        cb_format = tb.Combobox(opts, textvariable=self.var_format, values=["WEBP", "JPEG", "PNG"], state="readonly", width=8)
        cb_format.grid(row=0, column=3, sticky="w")

        # Calidad
        tb.Label(opts, text="Calidad:").grid(row=0, column=4, sticky="w", padx=6, pady=4)
        self.var_quality = tb.IntVar(value=self.cfg.get("quality", DEFAULT_CONFIG["quality"]))
        tb.Spinbox(opts, from_=10, to=100, increment=5, textvariable=self.var_quality, width=6).grid(row=0, column=5, sticky="w")

        # Modo
        tb.Label(opts, text="Modo:").grid(row=1, column=0, sticky="w", padx=6, pady=6)
        self.var_mode = tb.StringVar(value=self.cfg.get("mode", DEFAULT_CONFIG["mode"]))
        # Usamos RGB, L, 1 explícitamente para coincidir con la lógica interna
        cb_mode = tb.Combobox(opts, textvariable=self.var_mode, values=["RGB", "L", "1"], state="readonly", width=8)
        cb_mode.grid(row=1, column=1, sticky="w")

        # Hilos
        tb.Label(opts, text="Hilos:").grid(row=1, column=2, sticky="w", padx=6, pady=6)
        self.var_workers = tb.IntVar(value=self.cfg.get("workers", DEFAULT_CONFIG["workers"]))
        tb.Spinbox(opts, from_=1, to=max(1, (os.cpu_count() or 2)), textvariable=self.var_workers, width=6).grid(row=1, column=3, sticky="w")

        # Recordar Checkbox
        self.var_remember = tb.BooleanVar(value=self.cfg.get("remember", True))
        tb.Checkbutton(opts, text="Recordar opciones", variable=self.var_remember).grid(row=1, column=4, padx=15, sticky="w")

        # listbox
        lf = tb.Labelframe(main, text="Archivos seleccionados", padding=6)
        lf.pack(fill="both", expand=True, pady=6)
        self.listbox = tk.Listbox(lf, height=8)
        self.listbox.pack(side=LEFT, fill="both", expand=True)
        scroll = tb.Scrollbar(lf, command=self.listbox.yview)
        scroll.pack(side=LEFT, fill="y")
        self.listbox.configure(yscrollcommand=scroll.set)

        # progress global + meter
        progress_row = tb.Frame(main)
        progress_row.pack(fill="x", pady=6)
        tb.Label(progress_row, text="Progreso total:").pack(side=LEFT, padx=6)
        self.global_pb = tb.Progressbar(progress_row, length=300)
        self.global_pb.pack(side=LEFT, padx=6)
        self.global_label = tb.Label(progress_row, text="0/0 páginas")
        self.global_label.pack(side=LEFT, padx=6)

        self.meter = tb.Meter(main, amounttotal=100, amountused=0, subtext="Archivo actual", metersize=120, bootstyle="primary")
        self.meter.pack(pady=8)

        # actions
        act = tb.Frame(main)
        act.pack(fill="x", pady=6)
        self.btn_convert = tb.Button(act, text="Convertir", bootstyle="success", command=self.start)
        self.btn_convert.pack(side=LEFT, padx=6)
        self.btn_cancel = tb.Button(act, text="Cancelar", bootstyle="danger", command=self.cancel, state="disabled")
        self.btn_cancel.pack(side=LEFT, padx=6)

        self.status = tb.Label(main, text=f"Pillow: {PIL_VERSION}")
        self.status.pack(fill="x", pady=4)

    # UI actions
    def select_pdfs(self):
        files = filedialog.askopenfilenames(title="Seleccionar PDF(s)", filetypes=[("PDF", "*.pdf")])
        if files:
            self.tasks = list(files)
            self.listbox.delete(0, tk.END)
            for f in self.tasks:
                self.listbox.insert(tk.END, f)
            self._estimate_pages()

    def clear_list(self):
        if self.executor:
            messagebox.showwarning("En proceso", "No puedes limpiar mientras se está convirtiendo.")
            return
        self.tasks = []
        self.listbox.delete(0, tk.END)
        self.total_pages = 0
        self.completed_pages = 0
        self._update_global(0, 0)

    def select_output(self):
        d = filedialog.askdirectory(title="Selecciona carpeta de salida")
        if d:
            self.output_dir = d

    def _estimate_pages(self):
        total = 0
        for f in self.tasks:
            p = get_pdf_pages(f) or 0
            total += p
        self.total_pages = total
        self.completed_pages = 0
        self._update_global(0, self.total_pages)

    def _update_global(self, done, total):
        if total <= 0:
            percent = 0
        else:
            percent = int((done / total) * 100)
        try:
            self.global_pb.configure(value=percent)
        except Exception:
            pass
        self.global_label.configure(text=f"{done}/{total} páginas")
        self.root.update_idletasks()

    # conversion control
    def start(self):
        if not getattr(self, "tasks", None):
            messagebox.showwarning("Sin archivos", "Selecciona al menos un PDF.")
            return
        if not getattr(self, "output_dir", ""):
            self.output_dir = os.path.dirname(self.tasks[0])

        # save config
        if self.var_remember.get():
            self.cfg.update({
                "dpi": int(self.var_dpi.get()),
                "format": self.var_format.get(),
                "quality": int(self.var_quality.get()),
                "mode": self.var_mode.get(),
                "workers": int(self.var_workers.get()),
                "theme": self.cfg.get("theme", DEFAULT_CONFIG["theme"]),
                "remember": True
            })
            save_config(self.cfg)

        cfg_run = {
            "dpi": int(self.var_dpi.get()),
            "format": self.var_format.get(),
            "quality": int(self.var_quality.get()),
            "mode": self.var_mode.get()
        }
        workers = int(self.var_workers.get())

        # prepare
        self._estimate_pages()
        self.completed_pages = 0
        self._update_global(0, self.total_pages)

        self.btn_convert.configure(state="disabled")
        self.btn_cancel.configure(state="normal")
        self.cancel_flag.clear()
        self.executor = ThreadPoolExecutor(max_workers=workers)
        self.futures = []

        # reset meter
        try:
            self.meter.configure(amountused=0)
        except Exception:
            pass

        for pdf in self.tasks:
            name = safe_name(Path(pdf).stem)
            out_dir = os.path.join(self.output_dir, name)
            os.makedirs(out_dir, exist_ok=True)
            fut = self.executor.submit(convert_pdf_worker, pdf, out_dir, cfg_run, partial(self._progress_cb, pdf_path=pdf))
            self.futures.append(fut)

        threading.Thread(target=self._monitor, daemon=True).start()

    def _progress_cb(self, per_page, total_pages, saved_path, pdf_path=None):
        ui_queue.put({
            "type": "page",
            "pdf": pdf_path,
            "per": per_page,
            "tot": total_pages,
            "saved": saved_path
        })

    def _monitor(self):
        results = []
        for fut in as_completed(self.futures):
            try:
                r = fut.result()
                results.append(r)
                ui_queue.put({"type": "finished_pdf", "result": r})
            except Exception as e:
                ui_queue.put({"type": "error", "error": str(e)})
        ui_queue.put({"type": "all_done", "results": results})

    def cancel(self):
        if self.executor:
            self.cancel_flag.set()
            try:
                self.executor.shutdown(wait=False)
            except Exception:
                pass
            self.executor = None
            self.btn_convert.configure(state="normal")
            self.btn_cancel.configure(state="disabled")
            messagebox.showinfo("Cancelado", "Se solicitó cancelación. Procesos activos terminarán.")

    def _process_ui_queue(self):
        while True:
            try:
                item = ui_queue.get_nowait()
            except queue.Empty:
                break

            t = item.get("type")
            if t == "page":
                per = item["per"]
                tot = item["tot"] or 1
                percent = min(100, int((per / tot) * 100))
                
                try:
                    self.meter.configure(amountused=percent)
                except Exception:
                    pass

                self.completed_pages += 1
                if self.completed_pages > self.total_pages:
                    self.completed_pages = self.total_pages
                self._update_global(self.completed_pages, self.total_pages)

            elif t == "finished_pdf":
                r = item["result"]
                pdfname = Path(r["pdf"]).stem
                messagebox.showinfo("PDF completado", f"{pdfname}\nPáginas: {r.get('pages')}")
                try:
                    self.meter.configure(amountused=0)
                except Exception:
                    pass

            elif t == "error":
                messagebox.showerror("Error", item.get("error"))

            elif t == "all_done":
                self.btn_convert.configure(state="normal")
                self.btn_cancel.configure(state="disabled")
                self._update_global(self.total_pages, self.total_pages)
                messagebox.showinfo("Completado", "Todas las conversiones han finalizado.")
                try:
                    self.meter.configure(amountused=0)
                except Exception:
                    pass

        self.root.after(150, self._process_ui_queue)

# ------- main -------
def main():
    global POPPLER_PATH

    if not check_poppler():
        root_tmp = tk.Tk()
        root_tmp.withdraw()
        if messagebox.askyesno(
            "Poppler no encontrado",
            f"No se detectó Poppler en la ruta esperada:\n{POPPLER_PATH}\n\n"
            "¿Quieres seleccionar la carpeta 'bin' de Poppler?"
        ):
            d = filedialog.askdirectory(title="Selecciona carpeta 'bin' de Poppler")
            if d:
                POPPLER_PATH = d
            else:
                messagebox.showerror("Cancelado", "Se requiere Poppler para continuar.")
                return
        else:
            messagebox.showerror("Poppler requerido", "Poppler es necesario. Saliendo.")
            return
        root_tmp.destroy()

    cfg = load_config()
    style = cfg.get("theme", DEFAULT_CONFIG["theme"])
    root = tb.Window(themename=style)
    app = App(root)
    root.geometry("920x680")
    root.mainloop()


if __name__ == "__main__":
    main()