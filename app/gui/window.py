"""CustomTkinter main window for concert multi-cam sync."""

from __future__ import annotations

import threading
import traceback
from pathlib import Path
from tkinter import filedialog, messagebox

import customtkinter as ctk

from app.media.probe import FFmpegNotFoundError, is_video_file, require_ffmpeg_tools
from app.premiere.fcp7_xml import ExportOptions, write_fcp7_xml
from app.sync.match import MatchResult, MatchSettings, match_all
from app.sync.timeline import MainTimeline


def _sec_to_tc(seconds: float, fps: float = 25.0) -> str:
    if seconds < 0:
        seconds = 0.0
    total_frames = int(round(seconds * fps))
    f = total_frames % int(round(fps))
    total_sec = total_frames // int(round(fps))
    s = total_sec % 60
    m = (total_sec // 60) % 60
    h = total_sec // 3600
    return f"{h:02d}:{m:02d}:{s:02d}:{f:02d}"


class App(ctk.CTk):
    def __init__(self) -> None:
        super().__init__()
        self.title("Concert Multi-Cam Audio Sync v1.01")
        self.geometry("1100x760")
        self.minsize(900, 640)

        ctk.set_appearance_mode("System")
        ctk.set_default_color_theme("blue")

        self.main_paths: list[Path] = []
        # (path, camera_label)
        self.other_items: list[tuple[Path, str]] = []
        self.timeline: MainTimeline | None = None
        self.results: list[MatchResult] = []
        self._busy = False

        self._build_ui()
        self.after(100, self._check_ffmpeg)

    def _build_ui(self) -> None:
        self.grid_columnconfigure(0, weight=1)
        self.grid_columnconfigure(1, weight=1)
        self.grid_rowconfigure(1, weight=1)
        self.grid_rowconfigure(3, weight=1)

        header = ctk.CTkLabel(
            self,
            text="Match other cameras to an ordered Main timeline, then export Premiere FCP7 XML",
            anchor="w",
        )
        header.grid(row=0, column=0, columnspan=2, sticky="ew", padx=16, pady=(12, 4))

        # Main panel
        main_frame = ctk.CTkFrame(self)
        main_frame.grid(row=1, column=0, sticky="nsew", padx=(16, 8), pady=8)
        main_frame.grid_rowconfigure(1, weight=1)
        main_frame.grid_columnconfigure(0, weight=1)

        ctk.CTkLabel(main_frame, text="Main clips (timeline order)", font=ctk.CTkFont(weight="bold")).grid(
            row=0, column=0, sticky="w", padx=10, pady=(10, 4)
        )
        self.main_list = ctk.CTkTextbox(main_frame, height=180, activate_scrollbars=True)
        self.main_list.grid(row=1, column=0, sticky="nsew", padx=10, pady=4)
        self.main_list.configure(state="disabled")

        main_btns = ctk.CTkFrame(main_frame, fg_color="transparent")
        main_btns.grid(row=2, column=0, sticky="ew", padx=10, pady=(4, 10))
        ctk.CTkButton(main_btns, text="Add files…", width=100, command=self._add_main_files).pack(
            side="left", padx=(0, 6)
        )
        ctk.CTkButton(main_btns, text="Add folder…", width=100, command=self._add_main_folder).pack(
            side="left", padx=(0, 6)
        )
        ctk.CTkButton(main_btns, text="Up", width=60, command=lambda: self._move_main(-1)).pack(
            side="left", padx=(0, 6)
        )
        ctk.CTkButton(main_btns, text="Down", width=60, command=lambda: self._move_main(1)).pack(
            side="left", padx=(0, 6)
        )
        ctk.CTkButton(main_btns, text="Remove", width=80, command=self._remove_main).pack(
            side="left", padx=(0, 6)
        )
        ctk.CTkButton(main_btns, text="Clear", width=70, command=self._clear_main).pack(side="left")

        # Other panel
        other_frame = ctk.CTkFrame(self)
        other_frame.grid(row=1, column=1, sticky="nsew", padx=(8, 16), pady=8)
        other_frame.grid_rowconfigure(1, weight=1)
        other_frame.grid_columnconfigure(0, weight=1)

        ctk.CTkLabel(
            other_frame, text="Other clips (folder = camera)", font=ctk.CTkFont(weight="bold")
        ).grid(row=0, column=0, sticky="w", padx=10, pady=(10, 4))
        self.other_list = ctk.CTkTextbox(other_frame, height=180, activate_scrollbars=True)
        self.other_list.grid(row=1, column=0, sticky="nsew", padx=10, pady=4)
        self.other_list.configure(state="disabled")

        other_btns = ctk.CTkFrame(other_frame, fg_color="transparent")
        other_btns.grid(row=2, column=0, sticky="ew", padx=10, pady=(4, 10))
        ctk.CTkButton(other_btns, text="Add files…", width=100, command=self._add_other_files).pack(
            side="left", padx=(0, 6)
        )
        ctk.CTkButton(other_btns, text="Add folder…", width=100, command=self._add_other_folder).pack(
            side="left", padx=(0, 6)
        )
        ctk.CTkButton(other_btns, text="Clear", width=70, command=self._clear_other).pack(side="left")

        # Settings + actions
        settings = ctk.CTkFrame(self)
        settings.grid(row=2, column=0, columnspan=2, sticky="ew", padx=16, pady=4)
        for i in range(8):
            settings.grid_columnconfigure(i, weight=0)
        settings.grid_columnconfigure(7, weight=1)

        ctk.CTkLabel(settings, text="Confidence ≥").grid(row=0, column=0, padx=(10, 4), pady=(10, 4))
        self.conf_entry = ctk.CTkEntry(settings, width=60)
        self.conf_entry.insert(0, "1.8")
        self.conf_entry.grid(row=0, column=1, padx=(0, 12), pady=(10, 4))

        ctk.CTkLabel(settings, text="Sample rate").grid(row=0, column=2, padx=(0, 4), pady=(10, 4))
        self.sr_entry = ctk.CTkEntry(settings, width=70)
        self.sr_entry.insert(0, "22050")
        self.sr_entry.grid(row=0, column=3, padx=(0, 12), pady=(10, 4))

        self.include_low = ctk.CTkCheckBox(settings, text="Include low-confidence in XML")
        self.include_low.grid(row=0, column=4, padx=(0, 12), pady=(10, 4))

        self.match_btn = ctk.CTkButton(settings, text="Match", width=110, command=self._start_match)
        self.match_btn.grid(row=0, column=5, padx=(0, 8), pady=(10, 4))
        self.export_btn = ctk.CTkButton(
            settings, text="Export XML…", width=120, command=self._export_xml, state="disabled"
        )
        self.export_btn.grid(row=0, column=6, padx=(0, 10), pady=(10, 4))

        self.correct_drift = ctk.CTkCheckBox(
            settings,
            text="Correct drift (Cam 2/3 clock / sample-rate mismatch)",
        )
        self.correct_drift.grid(row=1, column=0, columnspan=4, sticky="w", padx=(10, 8), pady=(0, 10))

        ctk.CTkLabel(settings, text="Anchors").grid(row=1, column=4, padx=(0, 4), pady=(0, 10), sticky="e")
        self.anchors_entry = ctk.CTkEntry(settings, width=50)
        self.anchors_entry.insert(0, "5")
        self.anchors_entry.grid(row=1, column=5, padx=(0, 8), pady=(0, 10), sticky="w")

        # Results
        results_frame = ctk.CTkFrame(self)
        results_frame.grid(row=3, column=0, columnspan=2, sticky="nsew", padx=16, pady=8)
        results_frame.grid_rowconfigure(1, weight=1)
        results_frame.grid_columnconfigure(0, weight=1)

        ctk.CTkLabel(results_frame, text="Results", font=ctk.CTkFont(weight="bold")).grid(
            row=0, column=0, sticky="w", padx=10, pady=(10, 4)
        )
        self.results_box = ctk.CTkTextbox(results_frame, activate_scrollbars=True)
        self.results_box.grid(row=1, column=0, sticky="nsew", padx=10, pady=4)
        self.results_box.configure(state="disabled")

        self.progress = ctk.CTkProgressBar(results_frame)
        self.progress.grid(row=2, column=0, sticky="ew", padx=10, pady=(4, 4))
        self.progress.set(0)

        self.status = ctk.CTkLabel(results_frame, text="Ready", anchor="w")
        self.status.grid(row=3, column=0, sticky="ew", padx=10, pady=(0, 10))

        # Selection helpers for main list reorder (line index via entry)
        sel_row = ctk.CTkFrame(main_frame, fg_color="transparent")
        sel_row.grid(row=3, column=0, sticky="ew", padx=10, pady=(0, 10))
        ctk.CTkLabel(sel_row, text="Selected Main index (1-based):").pack(side="left")
        self.main_sel = ctk.CTkEntry(sel_row, width=50)
        self.main_sel.insert(0, "1")
        self.main_sel.pack(side="left", padx=6)

    def _check_ffmpeg(self) -> None:
        try:
            require_ffmpeg_tools()
            self._log_status("ffmpeg/ffprobe found")
        except FFmpegNotFoundError as exc:
            self._log_status(str(exc))
            messagebox.showwarning("ffmpeg required", str(exc))

    def _log_status(self, text: str) -> None:
        self.status.configure(text=text)

    def _set_textbox(self, box: ctk.CTkTextbox, lines: list[str]) -> None:
        box.configure(state="normal")
        box.delete("1.0", "end")
        if lines:
            box.insert("1.0", "\n".join(lines))
        box.configure(state="disabled")

    def _refresh_main_list(self) -> None:
        lines = [f"{i+1}. {p.name}" for i, p in enumerate(self.main_paths)]
        self._set_textbox(self.main_list, lines)

    def _refresh_other_list(self) -> None:
        lines = [f"[{cam}] {p.name}" for p, cam in self.other_items]
        self._set_textbox(self.other_list, lines)

    def _refresh_results(self) -> None:
        lines = [
            "Camera | Clip | Offset | Timecode | Speed | Drift ppm | Confidence | Status | Note",
            "-" * 110,
        ]
        for r in self.results:
            tc = _sec_to_tc(r.offset_sec)
            note = r.message or (
                r.matched_main_clip.name if r.matched_main_clip else ""
            )
            lines.append(
                f"{r.camera} | {r.path.name} | {r.offset_sec:8.3f}s | {tc} | "
                f"{r.speed:.6f}x | {r.drift_ppm:+.0f} | {r.confidence:6.2f} | {r.status} | {note}"
            )
        self._set_textbox(self.results_box, lines)

    def _selected_main_index(self) -> int | None:
        try:
            idx = int(self.main_sel.get().strip()) - 1
        except ValueError:
            return None
        if 0 <= idx < len(self.main_paths):
            return idx
        return None

    def _add_main_files(self) -> None:
        paths = filedialog.askopenfilenames(
            title="Select Main clips",
            filetypes=[
                ("Video", "*.mp4 *.mov *.mxf *.mkv *.avi *.m4v *.mts *.m2ts *.webm"),
                ("All", "*.*"),
            ],
        )
        for p in paths:
            path = Path(p)
            if is_video_file(path) and path not in self.main_paths:
                self.main_paths.append(path.resolve())
        self._refresh_main_list()

    def _add_main_folder(self) -> None:
        folder = filedialog.askdirectory(title="Select Main folder")
        if not folder:
            return
        files = sorted(
            [p for p in Path(folder).iterdir() if is_video_file(p)],
            key=lambda p: p.name.lower(),
        )
        for path in files:
            if path.resolve() not in self.main_paths:
                self.main_paths.append(path.resolve())
        self._refresh_main_list()

    def _move_main(self, delta: int) -> None:
        idx = self._selected_main_index()
        if idx is None:
            messagebox.showinfo("Select clip", "Enter a valid Main index (1-based).")
            return
        new_idx = idx + delta
        if not (0 <= new_idx < len(self.main_paths)):
            return
        self.main_paths[idx], self.main_paths[new_idx] = (
            self.main_paths[new_idx],
            self.main_paths[idx],
        )
        self.main_sel.delete(0, "end")
        self.main_sel.insert(0, str(new_idx + 1))
        self._refresh_main_list()

    def _remove_main(self) -> None:
        idx = self._selected_main_index()
        if idx is None:
            return
        del self.main_paths[idx]
        self._refresh_main_list()

    def _clear_main(self) -> None:
        self.main_paths.clear()
        self._refresh_main_list()

    def _add_other_files(self) -> None:
        paths = filedialog.askopenfilenames(
            title="Select other camera clips",
            filetypes=[
                ("Video", "*.mp4 *.mov *.mxf *.mkv *.avi *.m4v *.mts *.m2ts *.webm"),
                ("All", "*.*"),
            ],
        )
        for p in paths:
            path = Path(p).resolve()
            if not is_video_file(path):
                continue
            camera = path.parent.name or "Camera"
            if (path, camera) not in self.other_items and all(
                path != existing for existing, _ in self.other_items
            ):
                self.other_items.append((path, camera))
        self._refresh_other_list()

    def _add_other_folder(self) -> None:
        folder = filedialog.askdirectory(title="Select camera folder")
        if not folder:
            return
        root = Path(folder)
        camera = root.name or "Camera"
        files = sorted(
            [p for p in root.rglob("*") if is_video_file(p)],
            key=lambda p: p.name.lower(),
        )
        existing = {p for p, _ in self.other_items}
        for path in files:
            resolved = path.resolve()
            if resolved in existing:
                continue
            # Prefer immediate parent as camera if nested.
            cam = path.parent.name if path.parent != root else camera
            if path.parent == root:
                cam = camera
            else:
                cam = f"{camera}/{path.parent.name}"
            self.other_items.append((resolved, cam))
            existing.add(resolved)
        self._refresh_other_list()

    def _clear_other(self) -> None:
        self.other_items.clear()
        self._refresh_other_list()

    def _parse_settings(self) -> MatchSettings:
        try:
            conf = float(self.conf_entry.get().strip())
        except ValueError:
            conf = 1.8
        try:
            sr = int(self.sr_entry.get().strip())
        except ValueError:
            sr = 22050
        try:
            anchors = int(self.anchors_entry.get().strip())
        except ValueError:
            anchors = 5
        anchors = max(2, min(anchors, 20))
        return MatchSettings(
            sample_rate=sr,
            confidence_threshold=conf,
            correct_drift=bool(self.correct_drift.get()),
            drift_anchors=anchors,
        )

    def _start_match(self) -> None:
        if self._busy:
            return
        if not self.main_paths:
            messagebox.showerror("Main clips", "Add at least one Main clip.")
            return
        if not self.other_items:
            messagebox.showerror("Other clips", "Add at least one other clip.")
            return
        try:
            require_ffmpeg_tools()
        except FFmpegNotFoundError as exc:
            messagebox.showerror("ffmpeg required", str(exc))
            return

        settings = self._parse_settings()
        self._busy = True
        self.match_btn.configure(state="disabled")
        self.export_btn.configure(state="disabled")
        self.progress.set(0)
        self._log_status("Matching…")

        main_paths = list(self.main_paths)
        other_items = list(self.other_items)

        def worker() -> None:
            try:

                def on_progress(msg: str, frac: float) -> None:
                    self.after(0, lambda: self._ui_progress(msg, frac))

                timeline, results = match_all(
                    main_paths,
                    other_items,
                    settings=settings,
                    progress=on_progress,
                )
                self.after(0, lambda: self._match_done(timeline, results, None))
            except Exception as exc:  # noqa: BLE001
                err = f"{exc}\n{traceback.format_exc()}"
                self.after(0, lambda: self._match_done(None, [], err))

        threading.Thread(target=worker, daemon=True).start()

    def _ui_progress(self, msg: str, frac: float) -> None:
        self.progress.set(max(0.0, min(1.0, frac)))
        self._log_status(msg)

    def _match_done(
        self,
        timeline: MainTimeline | None,
        results: list[MatchResult],
        error: str | None,
    ) -> None:
        self._busy = False
        self.match_btn.configure(state="normal")
        if error:
            self._log_status("Match failed")
            messagebox.showerror("Match failed", error[:2000])
            return
        self.timeline = timeline
        self.results = results
        self._refresh_results()
        ok = sum(1 for r in results if r.status == "OK")
        low = sum(1 for r in results if r.status == "Low")
        failed = sum(1 for r in results if r.status == "Failed")
        self._log_status(f"Done — OK: {ok}, Low: {low}, Failed: {failed}")
        self.export_btn.configure(state="normal")
        self.progress.set(1.0)

    def _export_xml(self) -> None:
        if not self.timeline or not self.results:
            messagebox.showerror("Export", "Run Match first.")
            return
        path = filedialog.asksaveasfilename(
            title="Export FCP7 XML",
            defaultextension=".xml",
            filetypes=[("FCP XML", "*.xml"), ("All", "*.*")],
            initialfile="concert_sync.xml",
        )
        if not path:
            return
        options = ExportOptions(
            sequence_name="Concert Sync",
            include_low_confidence=bool(self.include_low.get()),
            include_failed=False,
        )
        try:
            write_fcp7_xml(Path(path), self.timeline, self.results, options=options)
        except Exception as exc:  # noqa: BLE001
            messagebox.showerror("Export failed", str(exc))
            return
        self._log_status(f"Exported: {path}")
        messagebox.showinfo(
            "Exported",
            "XML written.\n\nIn Premiere: File → Import → select the XML file.",
        )


def run_app() -> None:
    app = App()
    app.mainloop()
