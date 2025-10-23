import argparse
import os
import re
import wave
import shutil
import subprocess
import tempfile
from array import array
from typing import List, Optional, Tuple


class SrtEntry:
    def __init__(self, index: int, start_ms: int, end_ms: int, text: str):
        self.index = index
        self.start_ms = start_ms
        self.end_ms = end_ms
        self.text = text

    @property
    def duration_ms(self) -> int:
        return max(0, self.end_ms - self.start_ms)


def _parse_time_to_ms(t: str) -> int:
    # Format: HH:MM:SS,mmm
    m = re.match(r"(\d{2}):(\d{2}):(\d{2}),(\d{3})$", t.strip())
    if not m:
        raise ValueError(f"Invalid SRT time: {t}")
    hh, mm, ss, ms = map(int, m.groups())
    return ((hh * 60 + mm) * 60 + ss) * 1000 + ms


def parse_srt(path: str) -> List[SrtEntry]:
    with open(path, 'r', encoding='utf-8-sig') as f:
        content = f.read()

    # Split on blank lines
    blocks = re.split(r"\r?\n\s*\r?\n", content.strip())
    entries: List[SrtEntry] = []
    for block in blocks:
        lines = [ln for ln in block.splitlines() if ln.strip() != ""]
        if len(lines) < 2:
            continue
        try:
            idx = int(lines[0].strip())
        except ValueError:
            # Some SRTs omit index; handle gracefully
            idx = len(entries) + 1
            lines.insert(0, str(idx))

        times = lines[1]
        # 00:00:05,000 --> 00:00:07,200
        tm = re.match(r"([^\-]+)-->\s*(.+)$", times)
        if not tm:
            raise ValueError(f"Invalid SRT timing line: {times}")
        start_s = tm.group(1).strip()
        end_s = tm.group(2).strip()
        start_ms = _parse_time_to_ms(start_s)
        end_ms = _parse_time_to_ms(end_s)
        text = "\n".join(lines[2:]) if len(lines) > 2 else ""
        entries.append(SrtEntry(idx, start_ms, end_ms, text))

    # Sort by start time to be safe
    entries.sort(key=lambda e: (e.start_ms, e.index))
    return entries


def _read_wav_mono_16bit(path: str) -> Tuple[array, int]:
    """
    Read a WAV as mono int16.
    Returns (samples, sample_rate).
    Requires 16-bit PCM input. Downmixes to mono if needed.
    """
    with wave.open(path, 'rb') as w:
        nch = w.getnchannels()
        sw = w.getsampwidth()
        sr = w.getframerate()
        n = w.getnframes()
        comptype = w.getcomptype()
        if comptype != 'NONE':
            raise ValueError(f"Only uncompressed PCM WAV supported, got {comptype}")
        if sw != 2:
            raise ValueError(f"Only 16-bit PCM WAV supported (sampwidth=2). Got sampwidth={sw}")
        raw = w.readframes(n)

    # Convert interleaved int16 to mono
    data = array('h')
    data.frombytes(raw)
    if nch == 1:
        return data, sr
    # Downmix: average channels per frame
    mono = array('h')
    frames = len(data) // nch
    for i in range(frames):
        s = 0
        base = i * nch
        for c in range(nch):
            s += data[base + c]
        s //= nch
        mono.append(int(s))
    return mono, sr


def _write_wav_mono_16bit(path: str, samples: array, sample_rate: int) -> None:
    with wave.open(path, 'wb') as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(sample_rate)
    w.writeframes(samples.tobytes())


def _apply_fade(samples: array, sample_rate: int, fade_ms: float) -> None:
    if fade_ms <= 0:
        return
    fade_len = max(1, int(sample_rate * (fade_ms / 1000.0)))
    n = len(samples)
    fade_len = min(fade_len, n // 2)  # ensure room for in+out
    if fade_len <= 0:
        return
    # Linear fade in
    for i in range(fade_len):
        samples[i] = int(samples[i] * (i / fade_len))
    # Linear fade out
    for i in range(fade_len):
        idx = n - 1 - i
        samples[idx] = int(samples[idx] * (i / fade_len))


def _saturating_add_to_accum(accum: array, clip: array, start_index: int) -> None:
    # accum: array('i') 32-bit; clip: array('h') 16-bit
    n = len(clip)
    end = min(len(accum), start_index + n)
    ci = 0
    for ai in range(start_index, end):
        accum[ai] += int(clip[ci])
        ci += 1


def _to_int16_saturated(accum: array) -> array:
    out = array('h')
    out.extend([0] * len(accum))
    for i, v in enumerate(accum):
        if v > 32767:
            v = 32767
        elif v < -32768:
            v = -32768
        out[i] = v
    return out


def _find_clip_file(audio_dir: str, index: int) -> Optional[str]:
    """Find a clip by SRT index with common name patterns and extensions."""
    # Supported extensions (case-insensitive)
    exts = [
        ".wav", ".mp3", ".m4a", ".aac", ".ogg", ".flac", ".opus", ".wma",
    ]
    bases = [
        f"{index}", f"{index:02}", f"{index:03}", f"{index:04}", f"line_{index}", f"clip_{index}"
    ]
    lower_map = {f.lower(): f for f in os.listdir(audio_dir)}
    for b in bases:
        for ext in exts:
            name = (b + ext).lower()
            if name in lower_map:
                return os.path.join(audio_dir, lower_map[name])
    return None


def _ensure_ffmpeg() -> str:
    ff = shutil.which("ffmpeg") or shutil.which("ffmpeg.exe")
    if not ff:
        raise FileNotFoundError(
            "ffmpeg not found. Please install FFmpeg and ensure 'ffmpeg' is on PATH."
        )
    return ff


def _decode_to_tmp_wav(src_path: str, target_sr: Optional[int]) -> Tuple[str, int, bool]:
    """
    Decode any audio file to a temporary mono 16-bit PCM WAV using ffmpeg.
    If target_sr is provided, resample to that rate; otherwise preserve source rate.
    Returns (tmp_wav_path, sample_rate, created_tmp_flag)
    If src_path already seems to be a WAV PCM file and target_sr is None, returns (src_path, sr, False).
    """
    # If it's a WAV and we don't need resampling/format changes, try to use directly
    ext = os.path.splitext(src_path)[1].lower()
    if ext == ".wav" and target_sr is None:
        # Detect WAV properties
        try:
            with wave.open(src_path, 'rb') as w:
                sr = w.getframerate()
                return src_path, sr, False
        except wave.Error:
            # Fall back to ffmpeg if reading fails
            pass

    ff = _ensure_ffmpeg()
    tmp_fd, tmp_path = tempfile.mkstemp(prefix="dub_clip_", suffix=".wav")
    os.close(tmp_fd)

    cmd = [ff, "-y", "-hide_banner", "-loglevel", "error", "-i", src_path, "-vn", "-ac", "1"]
    if target_sr is not None:
        cmd += ["-ar", str(target_sr)]
    cmd += ["-c:a", "pcm_s16le", tmp_path]

    res = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
    if res.returncode != 0:
        try:
            os.remove(tmp_path)
        except OSError:
            pass
        raise RuntimeError(f"ffmpeg decode failed for {src_path}:\n{res.stdout}")

    # Read the generated WAV to learn its sample rate
    with wave.open(tmp_path, 'rb') as w:
        sr = w.getframerate()
    return tmp_path, sr, True


def align_dubs_to_srt(
    srt_path: str,
    audio_dir: str,
    output_wav: str,
    sample_rate: Optional[int] = None,
    fade_ms: float = 8.0,
    write_wav_dir: Optional[str] = None,
) -> None:
    """
    Align dubbed audio clips to SRT timings.

    - Reads SRT, gets each segment start/end.
    - For each SRT index N, looks for an audio file in `audio_dir` named like: N.wav, 01.wav, 001.wav, 0001.wav, line_N.wav, clip_N.wav.
    - Each clip is trimmed or end-padded with silence to exactly match the SRT duration.
    - The processed clip is placed at SRT start on a silent timeline; silence remains before speech starts.
    - Outputs a single mono 16-bit PCM WAV at `output_wav`.

    Notes/assumptions:
    - Input clips must be WAV, 16-bit PCM. If stereo, they are downmixed to mono.
    - All clips must use the same sample rate. If `sample_rate` is None, the first clip defines it.
    - No time-stretching is applied (avoids artifacts and dependencies). We only pad or trim.
    """
    entries = parse_srt(srt_path)
    if not entries:
        raise ValueError("SRT has no entries")

    # Determine total timeline length from SRT end
    total_ms = max(e.end_ms for e in entries)

    # Discover first available clip to set sample rate if not provided
    clip_sr: Optional[int] = None
    temp_to_cleanup: list[str] = []
    for e in entries:
        p = _find_clip_file(audio_dir, e.index)
        if p:
            # Decode just enough to learn sample rate; avoid resample for discovery
            tmp, sr, created = _decode_to_tmp_wav(p, target_sr=None)
            if created:
                temp_to_cleanup.append(tmp)
            clip_sr = sr
            break
    if clip_sr is None:
        raise FileNotFoundError("No matching audio clips found for any SRT index in the provided audio_dir")
    if sample_rate is None:
        sample_rate = clip_sr

    total_samples = int(round(sample_rate * (total_ms / 1000.0)))
    accum = array('i', [0] * (total_samples + 1))  # +1 to be safe for boundary

    # If requested, prepare directory to persist converted WAV clips
    if write_wav_dir:
        os.makedirs(write_wav_dir, exist_ok=True)

    # Process each entry
    missing = 0
    for e in entries:
        clip_path = _find_clip_file(audio_dir, e.index)
        if not clip_path:
            print(f"[WARN] Missing clip for SRT index {e.index} — skipping")
            missing += 1
            continue
        # Decode/convert each clip to target sample_rate mono 16-bit PCM
        tmp, sr, created = _decode_to_tmp_wav(clip_path, target_sr=sample_rate)
        if created:
            temp_to_cleanup.append(tmp)
        # Optionally persist the converted source WAV for user
        if write_wav_dir:
            persist_path = os.path.join(write_wav_dir, f"{e.index}.wav")
            try:
                src_for_copy = tmp if created else clip_path
                # Overwrite if exists to keep consistent sample rate
                shutil.copyfile(src_for_copy, persist_path)
            except Exception as copy_err:
                print(f"[WARN] Could not write converted WAV for index {e.index}: {copy_err}")
        clip, sr = _read_wav_mono_16bit(tmp)
        if sr != sample_rate:
            raise ValueError(f"Unexpected sample rate after decode for {clip_path}: {sr} != {sample_rate}")

        target_len = int(round(sample_rate * (e.duration_ms / 1000.0)))
        # Trim or pad to exact length
        if len(clip) > target_len:
            clip = clip[:target_len]
        elif len(clip) < target_len:
            pad = array('h', [0] * (target_len - len(clip)))
            clip.extend(pad)

        # Optional gentle fade to avoid clicks at boundaries
        _apply_fade(clip, sample_rate, fade_ms)

        start_index = int(round(sample_rate * (e.start_ms / 1000.0)))
        if start_index < 0:
            start_index = 0
        if start_index >= len(accum):
            # Entire clip would be out of range
            print(f"[WARN] Clip for index {e.index} starts beyond timeline — skipping")
            continue
        _saturating_add_to_accum(accum, clip, start_index)

    if missing:
        print(f"[INFO] {missing} SRT entries had no matching clip files")

    out = _to_int16_saturated(accum)
    os.makedirs(os.path.dirname(output_wav) or '.', exist_ok=True)
    _write_wav_mono_16bit(output_wav, out, sample_rate)
    print(f"[OK] Wrote aligned dub track: {output_wav}")

    # Cleanup temporary files from ffmpeg decodes
    for t in temp_to_cleanup:
        try:
            os.remove(t)
        except OSError:
            pass


def main():
    p = argparse.ArgumentParser(description="Align dubbed audio clips (wav/mp3/...) to SRT timings and output a single WAV track.")
    p.add_argument("--srt", required=True, help="Path to subtitle .srt file")
    p.add_argument("--audio-dir", required=True, help="Directory with clips (wav/mp3/..., e.g., 1.wav or 1.mp3)")
    p.add_argument("--out", required=True, help="Output WAV path for aligned dub track")
    p.add_argument("--sr", type=int, default=None, help="Target sample rate; defaults to first found clip's rate")
    p.add_argument("--write-wav-dir", default=None, help="If set, save each decoded clip as WAV into this folder before aligning")
    p.add_argument("--fade-ms", type=float, default=8.0, help="Fade in/out per clip in milliseconds (default 8ms)")
    args = p.parse_args()

    align_dubs_to_srt(
        srt_path=args.srt,
        audio_dir=args.audio_dir,
        output_wav=args.out,
        sample_rate=args.sr,
        fade_ms=args.fade_ms,
        write_wav_dir=args.write_wav_dir,
    )


if __name__ == "__main__":
    main()
