"""Simple Tkinter GUI for aligning dubbed dialogue to subtitle timings."""

from __future__ import annotations

import struct
import threading
import tkinter as tk
from dataclasses import dataclass
from pathlib import Path
from tkinter import filedialog, messagebox
from typing import Callable, List, Sequence
import wave

from .aligner import SubtitleInterval, align_segments_to_subtitles, parse_srt


@dataclass
class LoadedSegment:
    """Container for audio frames loaded from disk."""

    path: Path
    sample_rate: int
    channels: int
    frames: List[List[float]]


def _read_pcm16_wave(path: Path) -> LoadedSegment:
    """Load a PCM16 wave file into floating point samples."""

    with wave.open(str(path), "rb") as wav_file:
        sample_width = wav_file.getsampwidth()
        if sample_width != 2:
            raise ValueError(f"Only 16-bit PCM WAV files are supported ({path.name})")
        num_channels = wav_file.getnchannels()
        sample_rate = wav_file.getframerate()
        num_frames = wav_file.getnframes()
        raw = wav_file.readframes(num_frames)

    total_samples = num_frames * num_channels
    if total_samples == 0:
        frames: List[List[float]] = []
    else:
        fmt = "<" + "h" * total_samples
        pcm_values = struct.unpack(fmt, raw)
        frames = []
        for frame_index in range(num_frames):
            start = frame_index * num_channels
            frame_values = []
            for channel_index in range(num_channels):
                value = pcm_values[start + channel_index]
                if value >= 0:
                    frame_values.append(value / 32767.0)
                else:
                    frame_values.append(value / 32768.0)
            frames.append(frame_values)

    if num_channels == 1:
        mono_frames = [[frame[0]] if frame else [0.0] for frame in frames]
        return LoadedSegment(path=path, sample_rate=sample_rate, channels=1, frames=mono_frames)
    return LoadedSegment(path=path, sample_rate=sample_rate, channels=num_channels, frames=frames)


def _write_pcm16_wave(path: Path, sample_rate: int, frames: Sequence[Sequence[float]]) -> None:
    """Write floating point frames to a PCM16 wave file."""

    if not frames:
        num_channels = 1
    else:
        num_channels = len(frames[0])

    with wave.open(str(path), "wb") as wav_file:
        wav_file.setnchannels(num_channels)
        wav_file.setsampwidth(2)
        wav_file.setframerate(sample_rate)

        buffer = bytearray()
        for frame in frames:
            if len(frame) != num_channels:
                raise ValueError("Inconsistent channel count in output frames")
            for value in frame:
                float_value = float(value)
                clamped = max(-1.0, min(1.0, float_value))
                if clamped >= 0:
                    pcm = int(round(clamped * 32767.0))
                else:
                    pcm = int(round(clamped * 32768.0))
                buffer.extend(struct.pack("<h", pcm))
        wav_file.writeframes(buffer)


def _normalise_aligned_frames(aligned: List[Sequence[float]] | List[float]) -> List[List[float]]:
    if not aligned:
        return []
    first = aligned[0]
    if isinstance(first, Sequence) and not isinstance(first, (float, int)):
        return [list(frame) for frame in aligned]  # type: ignore[arg-type]
    return [[float(sample)] for sample in aligned]  # type: ignore[union-attr]


def _load_subtitles(path: Path) -> List[SubtitleInterval]:
    content = path.read_text(encoding="utf-8-sig")
    return parse_srt(content)


def _load_segments(directory: Path) -> List[LoadedSegment]:
    wave_files = sorted(directory.glob("*.wav"))
    if not wave_files:
        raise FileNotFoundError("No .wav files found in the selected directory")
    loaded: List[LoadedSegment] = []
    expected_rate: int | None = None
    for path in wave_files:
        segment = _read_pcm16_wave(path)
        if expected_rate is None:
            expected_rate = segment.sample_rate
        elif segment.sample_rate != expected_rate:
            raise ValueError("All segments must share the same sample rate")
        loaded.append(segment)
    return loaded


SegmentInput = Sequence[float] | Sequence[Sequence[float]]


class AlignmentApp:
    """Tkinter-based GUI for aligning dubbed audio files."""

    def __init__(self) -> None:
        self.root = tk.Tk()
        self.root.title("Dubbing Alignment")
        self.root.resizable(False, False)

        self.subtitle_path_var = tk.StringVar()
        self.segment_dir_var = tk.StringVar()
        self.output_path_var = tk.StringVar()
        self.status_var = tk.StringVar(value="Select files to begin.")

        self._build_ui()

    def _build_ui(self) -> None:
        padding = {"padx": 10, "pady": 5}

        tk.Label(self.root, text="Subtitle (.srt) file:").grid(row=0, column=0, sticky="w", **padding)
        subtitle_entry = tk.Entry(self.root, textvariable=self.subtitle_path_var, width=50)
        subtitle_entry.grid(row=0, column=1, **padding)
        tk.Button(self.root, text="Browse", command=self._select_subtitle).grid(row=0, column=2, **padding)

        tk.Label(self.root, text="Dubbed segments folder:").grid(row=1, column=0, sticky="w", **padding)
        segment_entry = tk.Entry(self.root, textvariable=self.segment_dir_var, width=50)
        segment_entry.grid(row=1, column=1, **padding)
        tk.Button(self.root, text="Browse", command=self._select_segment_dir).grid(row=1, column=2, **padding)

        tk.Label(self.root, text="Output WAV file:").grid(row=2, column=0, sticky="w", **padding)
        output_entry = tk.Entry(self.root, textvariable=self.output_path_var, width=50)
        output_entry.grid(row=2, column=1, **padding)
        tk.Button(self.root, text="Browse", command=self._select_output).grid(row=2, column=2, **padding)

        action_button = tk.Button(self.root, text="Align and Export", command=self._on_align_clicked)
        action_button.grid(row=3, column=0, columnspan=3, pady=15)

        status_label = tk.Label(self.root, textvariable=self.status_var, anchor="w", fg="gray")
        status_label.grid(row=4, column=0, columnspan=3, sticky="we", padx=10, pady=(0, 10))

    def _select_subtitle(self) -> None:
        path = filedialog.askopenfilename(filetypes=[("Subtitle files", "*.srt"), ("All files", "*.*")])
        if path:
            self.subtitle_path_var.set(path)

    def _select_segment_dir(self) -> None:
        path = filedialog.askdirectory()
        if path:
            self.segment_dir_var.set(path)

    def _select_output(self) -> None:
        path = filedialog.asksaveasfilename(defaultextension=".wav", filetypes=[("WAV files", "*.wav")])
        if path:
            self.output_path_var.set(path)

    def _on_align_clicked(self) -> None:
        threading.Thread(target=self._process_alignment, daemon=True).start()

    def _process_alignment(self) -> None:
        try:
            subtitle_path = Path(self.subtitle_path_var.get()).expanduser()
            segment_dir = Path(self.segment_dir_var.get()).expanduser()
            output_path = Path(self.output_path_var.get()).expanduser()

            if not subtitle_path.exists():
                raise FileNotFoundError("Subtitle file not found")
            if not segment_dir.exists() or not segment_dir.is_dir():
                raise FileNotFoundError("Segment folder not found")
            if not output_path.parent.exists():
                raise FileNotFoundError("Output directory does not exist")

            self._update_status("Loading subtitles...")
            subtitles = _load_subtitles(subtitle_path)
            if not subtitles:
                raise ValueError("The subtitle file does not contain any intervals")

            self._update_status("Loading audio segments...")
            segments = _load_segments(segment_dir)
            if len(subtitles) != len(segments):
                raise ValueError("The number of WAV files must match the subtitle entries")

            sample_rate = segments[0].sample_rate
            channel_count = segments[0].channels
            sequence: List[SegmentInput] = []
            for segment in segments:
                if segment.sample_rate != sample_rate:
                    raise ValueError("All audio files must use the same sample rate")
                if segment.channels != channel_count:
                    raise ValueError("All audio files must have the same channel count")
                if channel_count == 1:
                    sequence.append([frame[0] for frame in segment.frames])
                else:
                    sequence.append([tuple(frame) for frame in segment.frames])

            self._update_status("Aligning audio...")
            aligned = align_segments_to_subtitles(subtitles, sequence, sample_rate)
            frames = _normalise_aligned_frames(aligned)

            self._update_status("Writing output...")
            _write_pcm16_wave(output_path, sample_rate, frames)

            self._update_status("Alignment complete!")
            self._notify(lambda: messagebox.showinfo("Success", f"Aligned audio exported to {output_path}"))
        except Exception as exc:  # noqa: BLE001 - show error to user
            self._update_status("An error occurred")
            self._notify(lambda: messagebox.showerror("Alignment failed", str(exc)))

    def _update_status(self, message: str) -> None:
        self.root.after(0, self.status_var.set, message)

    def _notify(self, callback: Callable[[], None]) -> None:
        self.root.after(0, callback)

    def run(self) -> None:
        self.root.mainloop()


def launch() -> None:
    """Launch the GUI application."""

    app = AlignmentApp()
    app.run()


__all__ = ["launch", "AlignmentApp"]


if __name__ == "__main__":
    launch()
