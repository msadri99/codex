# Dubbing Alignment Toolkit

This project provides utilities for aligning dubbed audio with subtitle timings and ships with a Tkinter desktop interface for interactive use.

## Requirements
- Python 3.10 or later (Tkinter is part of the standard library on most distributions; install `python3-tk` on Linux if it is missing).
- 16-bit PCM WAV files for each dubbed dialogue segment.
- A subtitle file in `.srt` format whose entries match the order of the dubbed clips.

## Step-by-step: run the desktop app
1. **Clone the repository**
   ```bash
   git clone <repo-url>
   cd codex
   ```
2. **Create and activate a virtual environment (recommended)**
   ```bash
   python -m venv .venv
   source .venv/bin/activate  # On Windows use: .venv\Scripts\activate
   ```
3. **Install the project into your environment**
   ```bash
   pip install -e .
   ```
   This step is required so that `python -m dubbing.gui` can find the package modules.
4. **Install Tkinter if necessary**
   - macOS: already bundled with the system Python.
   - Windows: included with the official python.org installer.
   - Ubuntu/Debian: `sudo apt-get install python3-tk`.
5. **Prepare your inputs**
   - Place your translated subtitle file (UTF-8 `.srt`).
   - Gather each dubbed dialogue clip as an individual `.wav` file in a single folder. Name the files in the same order as the subtitle lines so they line up correctly.
   - Ensure all clips share the **same sample rate**, **channel count**, and are **16-bit PCM**.
6. **Launch the GUI**
   Choose one of the following options:

   ```bash
   # Recommended when you completed step 3
   python -m dubbing.gui

   # Alternative when you prefer not to install the package
   python launch_gui.py
   ```
7. **Select your files in the window**
   - Click **Browse** next to “Subtitle (.srt) file” and choose your subtitle file.
   - Click **Browse** next to “Dubbed segments folder” and select the folder containing your WAV clips.
   - Click **Browse** next to “Output WAV file” to pick where the aligned mix should be written.
8. **Align and export**
   - Press **Align and Export**. The application validates your selections, stretches each clip to fill the subtitle timing, and writes the combined WAV file to the output path.
   - The status bar shows progress. A confirmation dialog appears when the export succeeds; otherwise an error dialog explains what to fix.

## Optional: run the automated tests
```bash
pytest
```

The tests exercise the alignment helpers and confirm the GUI loader/writer behave as expected.
