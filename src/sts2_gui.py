from __future__ import annotations

import queue
import threading
import time
import tkinter as tk
from dataclasses import dataclass
from pathlib import Path
from tkinter import filedialog, messagebox, ttk

import requests
from PIL import Image, ImageTk

import sts2_drawbot as bot


@dataclass
class Candidate:
    title: str
    path: Path
    source_url: str
    score: float
    mime: str
    built_in: bool = False
    preview_path: Path | None = None
    strokes: list[bot.Stroke] | None = None
    source_size: tuple[int, int] | None = None


class DrawbotApp(tk.Tk):
    def __init__(self) -> None:
        super().__init__()
        self.title("STS2 Drawbot")
        self.geometry("1180x760")
        self.minsize(980, 640)

        self.root_dir = bot.project_root()
        self.downloads_dir = self.root_dir / "downloads"
        self.previews_dir = self.root_dir / "previews"
        self.downloads_dir.mkdir(exist_ok=True)
        self.previews_dir.mkdir(exist_ok=True)

        self.events: queue.Queue[tuple[str, object]] = queue.Queue()
        self.candidates: list[Candidate] = []
        self.selected: Candidate | None = None
        self.source_photo = None
        self.preview_photo = None

        self.prompt_var = tk.StringVar(value="simple star")
        self.status_var = tk.StringVar(value="Type a prompt, then search or preview.")
        self.mode_var = tk.StringVar(value="auto")
        self.no_builtins_var = tk.BooleanVar(value=False)
        self.countdown_var = tk.IntVar(value=5)
        self.progress_var = tk.StringVar(value="")

        self._build_style()
        self._build_layout()
        self.after(100, self._poll_events)

    def _build_style(self) -> None:
        style = ttk.Style(self)
        if "vista" in style.theme_names():
            style.theme_use("vista")
        style.configure("Header.TLabel", font=("Segoe UI", 16, "bold"))
        style.configure("Subtle.TLabel", foreground="#5f6b7a")
        style.configure("Action.TButton", padding=(12, 8))
        style.configure("Danger.TButton", padding=(12, 8))

    def _build_layout(self) -> None:
        root = ttk.Frame(self, padding=14)
        root.pack(fill=tk.BOTH, expand=True)
        root.columnconfigure(1, weight=1)
        root.rowconfigure(1, weight=1)

        header = ttk.Frame(root)
        header.grid(row=0, column=0, columnspan=2, sticky="ew", pady=(0, 12))
        header.columnconfigure(0, weight=1)
        ttk.Label(header, text="STS2 Drawbot", style="Header.TLabel").grid(row=0, column=0, sticky="w")
        ttk.Label(
            header,
            text="Preview candidates, sketchify the best one, then draw into Slay the Spire 2.",
            style="Subtle.TLabel",
        ).grid(row=1, column=0, sticky="w", pady=(3, 0))

        controls = ttk.Frame(header)
        controls.grid(row=0, column=1, rowspan=2, sticky="e")
        ttk.Button(controls, text="Open Image", command=self._choose_image).grid(row=0, column=0, padx=(0, 8))
        ttk.Button(controls, text="Advanced Help", command=self._show_advanced_help).grid(row=0, column=1)

        sidebar = ttk.Frame(root, width=330)
        sidebar.grid(row=1, column=0, sticky="nsw", padx=(0, 12))
        sidebar.rowconfigure(3, weight=1)

        prompt_box = ttk.LabelFrame(sidebar, text="Prompt", padding=10)
        prompt_box.grid(row=0, column=0, sticky="ew")
        prompt_box.columnconfigure(0, weight=1)
        prompt_entry = ttk.Entry(prompt_box, textvariable=self.prompt_var)
        prompt_entry.grid(row=0, column=0, sticky="ew", pady=(0, 8))
        prompt_entry.bind("<Return>", lambda _event: self.search_candidates())

        buttons = ttk.Frame(prompt_box)
        buttons.grid(row=1, column=0, sticky="ew")
        buttons.columnconfigure((0, 1), weight=1)
        ttk.Button(buttons, text="Search", command=self.search_candidates, style="Action.TButton").grid(
            row=0, column=0, sticky="ew", padx=(0, 6)
        )
        ttk.Button(buttons, text="Preview", command=self.preview_selected, style="Action.TButton").grid(
            row=0, column=1, sticky="ew"
        )

        options = ttk.LabelFrame(sidebar, text="Defaults", padding=10)
        options.grid(row=1, column=0, sticky="ew", pady=(12, 0))
        options.columnconfigure(1, weight=1)
        ttk.Label(options, text="Trace").grid(row=0, column=0, sticky="w")
        ttk.Combobox(
            options,
            values=("auto", "sketch", "dark", "edges"),
            textvariable=self.mode_var,
            state="readonly",
            width=10,
        ).grid(row=0, column=1, sticky="ew", padx=(8, 0))
        ttk.Label(options, text="Countdown").grid(row=1, column=0, sticky="w", pady=(8, 0))
        ttk.Spinbox(options, from_=1, to=15, textvariable=self.countdown_var, width=6).grid(
            row=1, column=1, sticky="w", padx=(8, 0), pady=(8, 0)
        )
        ttk.Checkbutton(options, text="Force web search", variable=self.no_builtins_var).grid(
            row=2, column=0, columnspan=2, sticky="w", pady=(8, 0)
        )

        ttk.Label(sidebar, text="Candidates").grid(row=2, column=0, sticky="w", pady=(14, 4))
        list_frame = ttk.Frame(sidebar)
        list_frame.grid(row=3, column=0, sticky="nsew")
        list_frame.rowconfigure(0, weight=1)
        list_frame.columnconfigure(0, weight=1)
        self.candidate_list = tk.Listbox(list_frame, height=18, activestyle="dotbox")
        self.candidate_list.grid(row=0, column=0, sticky="nsew")
        self.candidate_list.bind("<<ListboxSelect>>", self._on_candidate_select)
        scrollbar = ttk.Scrollbar(list_frame, command=self.candidate_list.yview)
        scrollbar.grid(row=0, column=1, sticky="ns")
        self.candidate_list.configure(yscrollcommand=scrollbar.set)

        draw_box = ttk.Frame(sidebar)
        draw_box.grid(row=4, column=0, sticky="ew", pady=(12, 0))
        draw_box.columnconfigure(0, weight=1)
        ttk.Button(draw_box, text="Draw Selected", command=self.draw_selected, style="Action.TButton").grid(
            row=0, column=0, sticky="ew"
        )
        ttk.Label(
            draw_box,
            text="During countdown, move your mouse to the first line. Press Esc to stop.",
            style="Subtle.TLabel",
            wraplength=300,
        ).grid(row=1, column=0, sticky="ew", pady=(8, 0))

        main = ttk.Frame(root)
        main.grid(row=1, column=1, sticky="nsew")
        main.columnconfigure((0, 1), weight=1)
        main.rowconfigure(1, weight=1)

        ttk.Label(main, text="Downloaded Image").grid(row=0, column=0, sticky="w")
        ttk.Label(main, text="Sketch Preview").grid(row=0, column=1, sticky="w", padx=(12, 0))

        self.source_canvas = tk.Label(main, bg="#f4f6f8", relief=tk.SOLID, borderwidth=1)
        self.source_canvas.grid(row=1, column=0, sticky="nsew", padx=(0, 6))
        self.preview_canvas = tk.Label(main, bg="#f4f6f8", relief=tk.SOLID, borderwidth=1)
        self.preview_canvas.grid(row=1, column=1, sticky="nsew", padx=(6, 0))

        bottom = ttk.Frame(main)
        bottom.grid(row=2, column=0, columnspan=2, sticky="ew", pady=(10, 0))
        bottom.columnconfigure(0, weight=1)
        ttk.Label(bottom, textvariable=self.status_var).grid(row=0, column=0, sticky="w")
        ttk.Label(bottom, textvariable=self.progress_var, style="Subtle.TLabel").grid(row=0, column=1, sticky="e")

    def search_candidates(self) -> None:
        prompt = self.prompt_var.get().strip()
        if not prompt:
            messagebox.showinfo("Prompt needed", "Type what you want to draw first.")
            return
        self._set_busy("Searching candidates...")
        self.candidates = []
        self.selected = None
        self.candidate_list.delete(0, tk.END)
        self._clear_images()
        threading.Thread(target=self._search_worker, args=(prompt,), daemon=True).start()

    def _search_worker(self, prompt: str) -> None:
        try:
            results: list[Candidate] = []
            if not self.no_builtins_var.get():
                built_in = bot.generate_builtin_prompt_image(prompt, self.downloads_dir)
                if built_in:
                    results.append(
                        Candidate(
                            title=f"Built-in: {prompt}",
                            path=built_in,
                            source_url="built-in",
                            score=9999,
                            mime="image/png",
                            built_in=True,
                        )
                    )

            if results and not self.no_builtins_var.get():
                self.events.put(("status", "Built-in candidate ready. Searching web candidates..."))

            session = requests.Session()
            session.headers.update({"User-Agent": "sts2-drawbot/1.0"})
            try:
                candidates = bot.collect_search_candidates(session, prompt)
                downloaded = bot.download_ranked_candidates(
                    session,
                    prompt,
                    candidates,
                    self.downloads_dir,
                    max_downloads=10,
                    max_candidates=50,
                )
                for score, path, title, mime, url in downloaded:
                    results.append(Candidate(title=title, path=path, source_url=url, score=score, mime=mime))
            except Exception:
                if not results:
                    raise

            self.events.put(("candidates", results))
        except Exception as exc:
            self.events.put(("error", str(exc)))

    def preview_selected(self) -> None:
        candidate = self.selected
        if not candidate:
            if self.candidates:
                self.candidate_list.selection_clear(0, tk.END)
                self.candidate_list.selection_set(0)
                self._on_candidate_select()
                candidate = self.selected
            else:
                self.search_candidates()
                return
        if not candidate:
            return
        self._set_busy("Generating sketch preview...")
        threading.Thread(target=self._preview_worker, args=(candidate,), daemon=True).start()

    def _preview_worker(self, candidate: Candidate) -> None:
        try:
            sidecar = bot.load_sidecar_strokes(candidate.path)
            if sidecar:
                strokes, source_size = sidecar
            else:
                strokes, source_size = bot.image_to_strokes(
                    image_path=candidate.path,
                    threshold=125,
                    fit_size=610,
                    max_strokes=1450,
                    mode=self.mode_var.get(),
                    connect_gap=2,
                )
            preview_path = self.previews_dir / f"{bot.slugify(candidate.path.stem)}-gui-preview.png"
            bot.save_preview(strokes, source_size, preview_path)
            candidate.strokes = strokes
            candidate.source_size = source_size
            candidate.preview_path = preview_path
            self.events.put(("preview", candidate))
        except Exception as exc:
            self.events.put(("error", str(exc)))

    def draw_selected(self) -> None:
        candidate = self.selected
        if not candidate:
            messagebox.showinfo("Select candidate", "Select and preview a candidate first.")
            return
        if candidate.strokes is None or candidate.source_size is None:
            self.preview_selected()
            messagebox.showinfo("Preview first", "I am generating the preview. Press Draw again when it is ready.")
            return
        if not messagebox.askokcancel(
            "Draw into Slay the Spire 2",
            "Open the drawing UI, keep the game visible, then press OK.\n\n"
            "Move your mouse to the first line during the countdown. Press Esc to stop.",
        ):
            return
        self._set_busy("Drawing...")
        threading.Thread(target=self._draw_worker, args=(candidate,), daemon=True).start()

    def _draw_worker(self, candidate: Candidate) -> None:
        try:
            window = bot.find_game_window("slaythespire2.exe", "Slay the Spire 2")
            canvas = bot.default_canvas_for_window(window)
            countdown = max(1, int(self.countdown_var.get()))
            for remaining in range(countdown, 0, -1):
                self.events.put(("status", f"Drawing in {remaining}... move mouse to start point."))
                time.sleep(1)
            import pyautogui

            pos = pyautogui.position()
            anchor = bot.Point(pos.x, pos.y)
            bot.draw_strokes(
                candidate.strokes or [],
                candidate.source_size or (1, 1),
                canvas,
                speed=0.01,
                pause=0.01,
                backend="win32",
                abort_key="esc",
                anchor_target=anchor,
                fit_padding=35,
            )
            self.events.put(("status", "Done drawing."))
        except KeyboardInterrupt:
            self.events.put(("status", "Drawing stopped."))
        except Exception as exc:
            self.events.put(("error", str(exc)))

    def _choose_image(self) -> None:
        filename = filedialog.askopenfilename(
            title="Choose image",
            filetypes=[
                ("Images", "*.png;*.jpg;*.jpeg;*.webp;*.bmp;*.gif;*.svg"),
                ("All files", "*.*"),
            ],
        )
        if not filename:
            return
        path = Path(filename)
        candidate = Candidate(title=f"Local: {path.name}", path=path, source_url=str(path), score=0, mime="")
        self.candidates = [candidate]
        self._populate_candidates()
        self.candidate_list.selection_set(0)
        self._on_candidate_select()
        self.preview_selected()

    def _on_candidate_select(self, _event=None) -> None:
        selection = self.candidate_list.curselection()
        if not selection:
            return
        self.selected = self.candidates[selection[0]]
        self.status_var.set(self.selected.source_url)
        self._show_image(self.selected.path, self.source_canvas, "source")
        if self.selected.preview_path:
            self._show_image(self.selected.preview_path, self.preview_canvas, "preview")
        else:
            self.preview_canvas.configure(image="", text="Press Preview", compound=tk.CENTER)

    def _populate_candidates(self) -> None:
        self.candidate_list.delete(0, tk.END)
        for candidate in self.candidates:
            label = f"{candidate.score:6.1f}  {candidate.title}"
            if candidate.built_in:
                label = f"built-in  {candidate.title}"
            self.candidate_list.insert(tk.END, label[:110])

    def _show_image(self, path: Path, widget: tk.Label, slot: str) -> None:
        try:
            img_path = bot.rasterize_if_needed(path)
            image = Image.open(img_path).convert("RGB")
            image.thumbnail((520, 560), Image.Resampling.LANCZOS)
            photo = ImageTk.PhotoImage(image)
            if slot == "source":
                self.source_photo = photo
            else:
                self.preview_photo = photo
            widget.configure(image=photo, text="")
        except Exception as exc:
            widget.configure(image="", text=f"Could not load image:\n{exc}", compound=tk.CENTER)

    def _clear_images(self) -> None:
        self.source_canvas.configure(image="", text="No candidate selected", compound=tk.CENTER)
        self.preview_canvas.configure(image="", text="No preview yet", compound=tk.CENTER)
        self.source_photo = None
        self.preview_photo = None

    def _show_advanced_help(self) -> None:
        messagebox.showinfo(
            "Advanced CLI",
            "The GUI uses the tuned defaults.\n\n"
            "CLI advanced help is available with:\n"
            ".\\run.ps1 --advanced-help",
        )

    def _set_busy(self, text: str) -> None:
        self.status_var.set(text)
        self.progress_var.set("Working...")

    def _poll_events(self) -> None:
        try:
            while True:
                event, payload = self.events.get_nowait()
                if event == "candidates":
                    self.candidates = payload  # type: ignore[assignment]
                    self._populate_candidates()
                    self.progress_var.set("")
                    self.status_var.set(f"Found {len(self.candidates)} candidate(s).")
                    if self.candidates:
                        self.candidate_list.selection_set(0)
                        self._on_candidate_select()
                        self.preview_selected()
                elif event == "preview":
                    candidate = payload  # type: ignore[assignment]
                    self.progress_var.set("")
                    self.status_var.set(f"Preview ready: {candidate.title}")
                    self._show_image(candidate.preview_path, self.preview_canvas, "preview")
                elif event == "status":
                    self.progress_var.set("")
                    self.status_var.set(str(payload))
                elif event == "error":
                    self.progress_var.set("")
                    self.status_var.set("Error")
                    messagebox.showerror("STS2 Drawbot", str(payload))
        except queue.Empty:
            pass
        self.after(100, self._poll_events)


if __name__ == "__main__":
    bot.ensure_runtime_imports()
    DrawbotApp().mainloop()
