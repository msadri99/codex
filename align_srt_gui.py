import os
import sys
import shutil
import threading
import queue
import subprocess
import tkinter as tk
from tkinter import ttk, filedialog, messagebox
from contextlib import redirect_stdout, redirect_stderr


# Ensure we can import the core aligner from this same folder regardless of CWD
THIS_DIR = os.path.dirname(os.path.abspath(__file__))
if THIS_DIR not in sys.path:
    sys.path.insert(0, THIS_DIR)

from align_srt_dubs import align_dubs_to_srt  # noqa: E402


class QueueWriter:
    def __init__(self, q: queue.Queue):
        self.q = q

    def write(self, s: str):
        if s:
            self.q.put(s)

    def flush(self):
        pass


class AlignApp(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("SRT Dub Aligner")
        self.geometry("720x520")
        self.minsize(680, 480)

        # State
        self._log_queue = queue.Queue()
        self._worker = None

        # Inputs
        self.var_srt = tk.StringVar()
        self.var_audio_dir = tk.StringVar()
        self.var_out = tk.StringVar()
        self.var_sr = tk.StringVar(value="Auto")
        self.var_fade = tk.DoubleVar(value=8.0)
        self.var_fade_text = tk.StringVar(value=f"{int(self.var_fade.get())} ms")
        # Video dubbing
        self.var_video = tk.StringVar()
        self.var_video_out = tk.StringVar()
        self.var_audio_mode = tk.StringVar(value="replace")  # replace | mix
        self.var_bg_vol = tk.DoubleVar(value=30.0)  # percent for mix mode
        self.var_bg_vol_text = tk.StringVar(value=f"{int(self.var_bg_vol.get())}%")

        self._build_ui()
        self.after(100, self._drain_log)

    def _build_ui(self):
        pad = {"padx": 10, "pady": 6}

        frm = ttk.Frame(self)
        frm.pack(fill=tk.BOTH, expand=True)

        # SRT
        row = 0
        ttk.Label(frm, text="Subtitle (.srt)").grid(row=row, column=0, sticky="w", **pad)
        e_srt = ttk.Entry(frm, textvariable=self.var_srt)
        e_srt.grid(row=row, column=1, sticky="ew", **pad)
        ttk.Button(frm, text="Browse...", command=self._choose_srt).grid(row=row, column=2, **pad)

        # Audio dir
        row += 1
        ttk.Label(frm, text="Dubs Folder (WAVs)").grid(row=row, column=0, sticky="w", **pad)
        e_dir = ttk.Entry(frm, textvariable=self.var_audio_dir)
        e_dir.grid(row=row, column=1, sticky="ew", **pad)
        ttk.Button(frm, text="Browse...", command=self._choose_audio_dir).grid(row=row, column=2, **pad)

        # Output wav
        row += 1
        ttk.Label(frm, text="Output WAV").grid(row=row, column=0, sticky="w", **pad)
        e_out = ttk.Entry(frm, textvariable=self.var_out)
        e_out.grid(row=row, column=1, sticky="ew", **pad)
        ttk.Button(frm, text="Save As...", command=self._choose_out).grid(row=row, column=2, **pad)

        # Sample rate and fade
        row += 1
        ttk.Label(frm, text="Sample Rate").grid(row=row, column=0, sticky="w", **pad)
        sr_values = ["Auto", "16000", "22050", "24000", "32000", "44100", "48000"]
        cb_sr = ttk.Combobox(frm, textvariable=self.var_sr, values=sr_values, state="readonly")
        cb_sr.grid(row=row, column=1, sticky="w", **pad)
        cb_sr.current(0)

        row += 1
        ttk.Label(frm, text="Fade (ms)").grid(row=row, column=0, sticky="w", **pad)
        fade = ttk.Scale(frm, from_=0, to=50, orient=tk.HORIZONTAL, variable=self.var_fade,
                         command=lambda val: self.var_fade_text.set(f"{int(float(val))} ms"))
        fade.grid(row=row, column=1, sticky="ew", **pad)
        ttk.Label(frm, textvariable=self.var_fade_text).grid(row=row, column=2, sticky="w", **pad)

        # Video dubbing section
        row += 1
        sep = ttk.Separator(frm)
        sep.grid(row=row, column=0, columnspan=3, sticky="ew", padx=10, pady=(10, 2))

        row += 1
        ttk.Label(frm, text="Main Video (English)").grid(row=row, column=0, sticky="w", **pad)
        e_vid = ttk.Entry(frm, textvariable=self.var_video)
        e_vid.grid(row=row, column=1, sticky="ew", **pad)
        ttk.Button(frm, text="Browse...", command=self._choose_video).grid(row=row, column=2, **pad)

        row += 1
        ttk.Label(frm, text="Dubbed Video Output").grid(row=row, column=0, sticky="w", **pad)
        e_vout = ttk.Entry(frm, textvariable=self.var_video_out)
        e_vout.grid(row=row, column=1, sticky="ew", **pad)
        ttk.Button(frm, text="Save As...", command=self._choose_video_out).grid(row=row, column=2, **pad)

        row += 1
        ttk.Label(frm, text="Audio Mode").grid(row=row, column=0, sticky="w", **pad)
        mode_frame = ttk.Frame(frm)
        mode_frame.grid(row=row, column=1, columnspan=2, sticky="w", **pad)
        ttk.Radiobutton(mode_frame, text="Replace original audio", value="replace", variable=self.var_audio_mode).pack(side=tk.LEFT)
        ttk.Radiobutton(mode_frame, text="Mix with original (background)", value="mix", variable=self.var_audio_mode).pack(side=tk.LEFT, padx=(10,0))

        row += 1
        ttk.Label(frm, text="BG volume (mix)").grid(row=row, column=0, sticky="w", **pad)
        bg = ttk.Scale(frm, from_=0, to=100, orient=tk.HORIZONTAL, variable=self.var_bg_vol,
                       command=lambda val: self.var_bg_vol_text.set(f"{int(float(val))}%"))
        bg.grid(row=row, column=1, sticky="ew", **pad)
        ttk.Label(frm, textvariable=self.var_bg_vol_text).grid(row=row, column=2, sticky="w", **pad)

        # Actions
        row += 1
        btn_frame = ttk.Frame(frm)
        btn_frame.grid(row=row, column=0, columnspan=3, sticky="ew", **pad)
        btn_frame.columnconfigure(0, weight=1)
        self.btn_run = ttk.Button(btn_frame, text="Align", command=self._on_run)
        self.btn_run.grid(row=0, column=0, sticky="w")
        ttk.Button(btn_frame, text="Dub Video", command=self._on_dub_video).grid(row=0, column=1, sticky="w", padx=10)
        ttk.Button(btn_frame, text="Open Output Folder", command=self._open_out_folder).grid(row=0, column=2, sticky="w", padx=10)
        ttk.Button(btn_frame, text="Quit", command=self.destroy).grid(row=0, column=3, sticky="e")

        # Log area
        row += 1
        ttk.Label(frm, text="Log").grid(row=row, column=0, sticky="w", **pad)
        self.txt = tk.Text(frm, height=16, wrap="word")
        self.txt.grid(row=row, column=1, columnspan=2, sticky="nsew", **pad)
        self.txt.configure(state=tk.DISABLED)

        # Layout stretch
        frm.columnconfigure(1, weight=1)
        frm.rowconfigure(row, weight=1)

    def _append_log(self, text: str):
        self.txt.configure(state=tk.NORMAL)
        self.txt.insert(tk.END, text)
        self.txt.see(tk.END)
        self.txt.configure(state=tk.DISABLED)

    def _drain_log(self):
        try:
            while True:
                msg = self._log_queue.get_nowait()
                self._append_log(msg)
        except queue.Empty:
            pass
        finally:
            self.after(100, self._drain_log)

    def _choose_srt(self):
        path = filedialog.askopenfilename(
            title="Choose subtitle (.srt)",
            filetypes=(("SRT files", "*.srt"), ("All files", "*.*")),
        )
        if path:
            self.var_srt.set(path)
            # Suggest default output next to SRT
            out_dir = os.path.dirname(path)
            self.var_out.set(os.path.join(out_dir, "aligned_dub.wav"))

    def _choose_audio_dir(self):
        path = filedialog.askdirectory(title="Choose dubs folder (WAVs)")
        if path:
            self.var_audio_dir.set(path)

    def _choose_out(self):
        initial = self.var_out.get() or "aligned_dub.wav"
        path = filedialog.asksaveasfilename(
            title="Save output WAV",
            defaultextension=".wav",
            initialfile=os.path.basename(initial),
            filetypes=(("WAV files", "*.wav"), ("All files", "*.*")),
        )
        if path:
            self.var_out.set(path)

    def _choose_video(self):
        path = filedialog.askopenfilename(
            title="Choose main English video",
            filetypes=(("Video", "*.mp4;*.mkv;*.mov;*.m4v;*.avi;*.webm"), ("All files", "*.*")),
        )
        if path:
            self.var_video.set(path)
            # Suggest default dubbed output next to video
            base, ext = os.path.splitext(path)
            self.var_video_out.set(base + "_dubbed" + (ext if ext else ".mp4"))

    def _choose_video_out(self):
        initial = self.var_video_out.get() or "dubbed.mp4"
        path = filedialog.asksaveasfilename(
            title="Save dubbed video",
            defaultextension=".mp4",
            initialfile=os.path.basename(initial),
            filetypes=(("MP4", "*.mp4"), ("MKV", "*.mkv"), ("All files", "*.*")),
        )
        if path:
            self.var_video_out.set(path)

    def _open_out_folder(self):
        out = self.var_out.get().strip()
        folder = os.path.dirname(out) if out else None
        if folder and os.path.isdir(folder):
            try:
                if sys.platform.startswith("win"):
                    os.startfile(folder)  # type: ignore[attr-defined]
                elif sys.platform == "darwin":
                    os.system(f'open "{folder}"')
                else:
                    os.system(f'xdg-open "{folder}"')
            except Exception:
                pass

    def _on_run(self):
        if self._worker and self._worker.is_alive():
            return

        srt = self.var_srt.get().strip()
        audio_dir = self.var_audio_dir.get().strip()
        out = self.var_out.get().strip()
        sr_raw = self.var_sr.get().strip()
        sr = None if sr_raw.lower() == "auto" else int(sr_raw)
        fade_ms = float(self.var_fade.get())

        # Validate
        if not srt or not os.path.isfile(srt):
            messagebox.showerror("Missing SRT", "Please select a valid .srt file.")
            return
        if not audio_dir or not os.path.isdir(audio_dir):
            messagebox.showerror("Missing Dubs Folder", "Please select a valid folder with WAV clips.")
            return
        if not out:
            messagebox.showerror("Missing Output", "Please choose an output WAV path.")
            return

        os.makedirs(os.path.dirname(out) or ".", exist_ok=True)

        self.btn_run.configure(state=tk.DISABLED)
        self._append_log("\n=== Alignment started ===\n")

        def work():
            writer = QueueWriter(self._log_queue)
            ok = True
            try:
                with redirect_stdout(writer), redirect_stderr(writer):
                    align_dubs_to_srt(
                        srt_path=srt,
                        audio_dir=audio_dir,
                        output_wav=out,
                        sample_rate=sr,
                        fade_ms=fade_ms,
                    )
            except Exception as e:
                ok = False
                self._log_queue.put(f"\n[ERROR] {e}\n")
            finally:
                def done():
                    self.btn_run.configure(state=tk.NORMAL)
                    if ok:
                        messagebox.showinfo("Done", f"Aligned track saved to:\n{out}")
                    else:
                        messagebox.showerror("Failed", "Alignment failed. See log for details.")
                self.after(0, done)

        self._worker = threading.Thread(target=work, daemon=True)
        self._worker.start()

    def _on_dub_video(self):
        if self._worker and self._worker.is_alive():
            return

        # Validate basics
        srt = self.var_srt.get().strip()
        audio_dir = self.var_audio_dir.get().strip()
        out_wav = self.var_out.get().strip()
        video_in = self.var_video.get().strip()
        video_out = self.var_video_out.get().strip()
        mode = self.var_audio_mode.get().strip()
        bg_pct = max(0.0, min(100.0, float(self.var_bg_vol.get())))
        bg_vol = bg_pct / 100.0

        if not video_in or not os.path.isfile(video_in):
            messagebox.showerror("Missing Video", "Please select the main English video file.")
            return
        if not video_out:
            messagebox.showerror("Missing Output", "Please choose a dubbed video output path.")
            return
        if not shutil.which("ffmpeg") and not shutil.which("ffmpeg.exe"):
            messagebox.showerror("FFmpeg Required", "Please install FFmpeg and ensure 'ffmpeg' is on PATH.")
            return

        # If aligned WAV is missing, require SRT and dubs and produce it first
        need_align = not out_wav or not os.path.isfile(out_wav)
        if need_align:
            if not srt or not os.path.isfile(srt):
                messagebox.showerror("Missing SRT", "Aligned track not found. Please select a valid .srt or run Align first.")
                return
            if not audio_dir or not os.path.isdir(audio_dir):
                messagebox.showerror("Missing Dubs Folder", "Aligned track not found. Please select the dubs folder or run Align first.")
                return

        self.btn_run.configure(state=tk.DISABLED)
        self._append_log("\n=== Dubbing started ===\n")

        def work():
            ok = True
            try:
                # 1) Align if needed
                if need_align:
                    self._log_queue.put("[INFO] Aligned WAV not found — generating...\n")
                    sr_raw = self.var_sr.get().strip()
                    sr = None if sr_raw.lower() == "auto" else int(sr_raw)
                    fade_ms = float(self.var_fade.get())
                    try:
                        align_dubs_to_srt(
                            srt_path=srt,
                            audio_dir=audio_dir,
                            output_wav=out_wav,
                            sample_rate=sr,
                            fade_ms=fade_ms,
                        )
                    except Exception as e:
                        self._log_queue.put(f"\n[ERROR] Alignment failed: {e}\n")
                        return

                # 2) Mux/mix with ffmpeg
                ff = shutil.which("ffmpeg") or shutil.which("ffmpeg.exe")
                assert ff
                cmd = [ff, "-y", "-hide_banner", "-i", video_in, "-i", out_wav]
                if mode == "replace":
                    cmd += [
                        "-map", "0:v:0",
                        "-map", "1:a:0",
                        "-c:v", "copy",
                        "-c:a", "aac",
                        "-shortest",
                        video_out,
                    ]
                else:  # mix
                    # Lower original audio volume, mix with dub
                    vol_expr = f"volume={bg_vol}"
                    filter_complex = f"[0:a]{vol_expr}[bg];[bg][1:a]amix=inputs=2:duration=longest:normalize=0[aout]"
                    cmd += [
                        "-filter_complex", filter_complex,
                        "-map", "0:v:0",
                        "-map", "[aout]",
                        "-c:v", "copy",
                        "-c:a", "aac",
                        "-shortest",
                        video_out,
                    ]

                res = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
                self._log_queue.put(res.stdout or "")
                if res.returncode != 0:
                    ok = False
                    self._log_queue.put("\n[ERROR] FFmpeg failed.\n")
                else:
                    self._log_queue.put(f"\n[OK] Dubbed video written: {video_out}\n")
            except Exception as e:
                ok = False
                self._log_queue.put(f"\n[ERROR] {e}\n")
            finally:
                def done():
                    self.btn_run.configure(state=tk.NORMAL)
                    if ok:
                        messagebox.showinfo("Done", f"Dubbed video saved to:\n{video_out}")
                    else:
                        messagebox.showerror("Failed", "Dubbing failed. See log for details.")
                self.after(0, done)

        self._worker = threading.Thread(target=work, daemon=True)
        self._worker.start()


def main():
    app = AlignApp()
    app.mainloop()


if __name__ == "__main__":
    main()
