# Typra Offline Speech to Text

Typra is a free, lightweight dictation app for Windows that transcribes your voice and types the result directly into any application.

Transcription runs locally on your device using [OpenAI Whisper](https://github.com/openai/whisper) via [faster-whisper](https://github.com/SYSTRAN/faster-whisper). No cloud, no subscription, and your audio and text never leave your machine.

> [!NOTE]
> This fork of [JoshuaTrapani/Typra](https://github.com/JoshuaTrapani/Typra) is a security and reliability update. The source code is now in the repository instead of ZIP files, and the installer script is included so every build can be reviewed. See [What changed in this fork](#what-changed-in-this-fork).

---

## Features

- **Offline**: audio and text stay on your device. The internet is only used to download Python, the Python packages and the models. Python and the packages are verified against SHA-256 hashes, and the models come from pinned versions.
- **Types anywhere**: works in any Windows application.
- **Global hotkey**: press `Ctrl + Shift + Space` (or your own combination) to start and stop dictation.
- **Local AI Cleanup (optional)**: a local LLM fixes grammar and removes filler words.
- **Tone modes**: Raw, Cleanup, Casual, Professional or Concise.
- **Live waveform**: a small overlay shows when Typra is listening.
- **20 languages**, including Spanish, Italian, Japanese and Arabic.
- **Session history (optional)**: each dictation session is saved to a searchable local database. You can turn this off.
- **Voice commands**: say "new line", "comma", "exclamation mark" or "delete that" as a separate phrase.
- **No GPU required**: runs on any modern CPU, with optional CUDA acceleration.
- **System tray**: runs quietly in the background.

---

## System requirements

- Windows 10 (version 1803 or later) or Windows 11, 64-bit
- About 1 GB of free disk space, plus the models you choose
- Python 3.11, 64-bit. The installer can install it for you.
- An internet connection during installation and for the first download of each model

---

## Installation

### Option 1: installer

Run `Typra_Setup_<version>.exe`, taken from this fork's Releases page or built by you (see [Building the installer](#building-the-installer)). If the release lists a SHA-256 checksum, compare it before running the file: `certutil -hashfile Typra_Setup_<version>.exe SHA256`.

The installer:

- installs Typra **for the current user only** in `%LOCALAPPDATA%\Programs\Typra`. It needs **no administrator rights** and does not change PATH or other system settings.
- uses your 64-bit **Python 3.11** if it is installed. If it is not, the installer explains what it will do and asks for your consent. It then downloads the official installer from python.org over HTTPS and checks its SHA-256 before running it. Python is installed for your user only.
- creates a virtual environment and installs the Python packages from `requirements.lock`, where each package has an exact version and SHA-256 hash. Only prebuilt wheels are used, never source builds.
- adds Start menu and, if you choose, desktop shortcuts that start `pythonw.exe` directly.

Do not use "Run as administrator": Setup warns you if you do, because Typra would be installed for the administrator account instead of yours.

To update Typra, run a newer Setup over the existing installation. The new environment is built next to the old one, so a failed update leaves the working installation in place. If you installed `llama-cpp-python` for AI Cleanup, install it again after each update.

When you uninstall Typra, the uninstaller asks whether to delete your data as well (history, settings, log and models). Your data is kept unless you say yes.

> [!WARNING]
> The original project's prebuilt `Typra_v2_Setup.exe` is flagged by some antivirus products. A static analysis found no malware in it. However, it is unsigned, and it downloads the Python installer over unencrypted HTTP and runs it with administrator rights, so anyone controlling the network could replace that download. Use the installer from this fork instead.

### Option 2: run from source

1. Install **Python 3.11 (64-bit)** from [python.org](https://www.python.org/downloads/release/python-3119/) ("Windows installer (64-bit)"). The Microsoft Store version is not supported.
2. Clone the repository and start Typra:

```bat
git clone https://github.com/BrisringDem93/Typra.git
cd Typra
run.bat
```

On the first run, `run.bat` calls `setup.bat`. It creates `.venv` and installs the exact packages listed in `requirements.lock`. Afterwards Typra starts without a console window. Look for its icon in the system tray. When `requirements.lock` changes, for example after a `git pull`, `run.bat` runs `setup.bat` again.

---

## How it works

1. Typra loads the Whisper model you selected (Base by default, about 145 MB). It is downloaded once, from a pinned Hugging Face commit.
2. Press the global hotkey. A small waveform overlay appears at the bottom of your screen.
3. Speak naturally. Every time you pause, the text is typed into the window you are working in.
4. Press the hotkey again to stop. Typra also stops by itself after 5 minutes without speech.

Typra cannot type into programs running as administrator (Windows blocks this). Do not run Typra itself as administrator to get around it; Typra warns you if it is started that way.

If the last words of a dictation are still being transcribed when you switch to another window, Typra copies them to the clipboard instead of typing them into the new window. This applies after you stop, or when you start a new dictation elsewhere. While you are dictating, text goes to the window that is in front when each phrase is ready. Typra never types into its own windows.

### Voice commands

A command works only when you say it **as its own phrase**: pause, say "new line", pause. A sentence that only *starts* with a command word, such as "Period pain…" or "Löschen Sie…", is typed as you said it. "Delete that" removes the last typed phrase. It does this only if the same window is still in front; otherwise nothing is deleted. Commands are available in English, German and French.

---

## AI Cleanup (optional)

AI Cleanup rewrites each phrase with a local language model. It needs the `llama-cpp-python` package, which is **not installed by default**. Install it into Typra's environment. Use version 0.3.9 or later: versions before 0.2.72 are vulnerable to [CVE-2024-34359](https://nvd.nist.gov/vuln/detail/CVE-2024-34359), and Typra refuses to use them.

```bat
cd %LOCALAPPDATA%\Programs\Typra
.venv\Scripts\python.exe -m pip install --use-feature=truststore "llama-cpp-python>=0.3.9" --only-binary=:all: --extra-index-url https://abetlen.github.io/llama-cpp-python/whl/cpu
```

Unlike the packages in `requirements.lock`, this optional package and its dependencies are not hash-pinned. The command downloads a prebuilt wheel from the llama-cpp-python project's own index, hosted on GitHub Pages. Make sure the version you install supports the model you choose.

Then go to **Settings → AI Cleanup**, download a model and turn the feature on. Models are downloaded from pinned commits, and their size and SHA-256 are checked once, when they are downloaded. The model is loaded once in a separate process, and your text is passed to it only in memory, never through files. The dictated text is clearly marked as text to edit, and the reply is cleaned of control characters and of line breaks you did not dictate.

| Model        | Size    | Description                                    |
| ------------ | ------- | ---------------------------------------------- |
| Gemma 4 E2B  | ~3.5 GB | Google's on-device model, best quality         |
| Llama 3.2 1B | ~0.8 GB | Meta's lightweight model for lower-RAM systems |

---

## Speech models

| Model          | Size    | Hardware  |
| -------------- | ------- | --------- |
| Tiny           | ~75 MB  | CPU / GPU |
| Base           | ~145 MB | CPU / GPU |
| Small          | ~465 MB | CPU / GPU |
| Medium         | ~1.5 GB | CPU / GPU |
| Large-v3-turbo | ~1.6 GB | CPU / GPU |

Choose the model in **Settings → Transcription**. Each model is downloaded the first time you use it, from a pinned commit of its Hugging Face repository. After that, Typra loads it from disk without contacting the internet.

---

## Privacy

Typra keeps all of its data in `%USERPROFILE%\.whisper_dictation`:

| File / folder     | Contents                                                                   |
| ----------------- | -------------------------------------------------------------------------- |
| `settings.json`   | your settings                                                              |
| `history.db`      | dictation sessions; turn off in Settings → Advanced → Privacy              |
| `typra.log`       | technical log of the current run: text lengths only, never what you said   |
| `whisper_models/` | downloaded speech models                                                   |
| `llm_models/`     | downloaded AI Cleanup models                                               |
| `hf_home/`        | Hugging Face download logs and cache                                       |

- Audio stays in memory. It is never written to disk and never sent anywhere.
- Deleted history entries are overwritten inside `history.db`, and "Clear all" rebuilds the file. Databases created by earlier versions are rebuilt once, so text deleted back then is also removed.
- Text that Typra copies to the clipboard is excluded from Windows clipboard history and cloud sync.
- Network contacts are limited to:
  - python.org: the Python installer, only with your consent;
  - PyPI: the packages, during setup;
  - Hugging Face: the first download of each model;
  - GitHub Pages: only if you install `llama-cpp-python` for AI Cleanup.
- Earlier versions of Typra stored speech models in `%USERPROFILE%\.cache\huggingface`. You can delete that folder if no other program uses it.

---

## What changed in this fork

**Installer and dependencies**

- The installer script is published, installs per user, needs no administrator rights, and asks before installing Python. Python is downloaded over HTTPS only and checked against a SHA-256 hash.
- Packages come from `requirements.lock`, with exact versions, hashes and wheels only. With Python 3.11.8 or later, `pip` uses the Windows certificate store, so installation works behind antivirus or corporate HTTPS inspection. The Python download also uses the Windows proxy settings.
- Updates build the new environment before replacing the old one, and Setup checks that Typra is not running before replacing files.
- The unused `pywin32` package, which added native code to every Python process, is no longer installed.
- Speech and AI models come from pinned commits, and AI models are checked for size and SHA-256. The 3.5 GB AI model is no longer downloaded during installation.

**Privacy**

- Dictated text is no longer written to the log file or to temporary `.py` files.
- History can be turned off, deleted entries are overwritten, and "Clear all" compacts the database.

**Typing safety**

- Voice commands work only as whole phrases.
- "Delete that" only acts in the window that received the text.
- Text from a dictation you have already stopped is copied to the clipboard instead of being typed if you have switched windows. This also applies when a new dictation starts before the old one is finished. Several late phrases are combined, not overwritten.
- Typra waits for you to release Ctrl, Shift, Alt and Win before typing, and never types into its own windows.
- Control characters in AI Cleanup output are removed instead of being sent as key presses.
- The History "Paste" button no longer types into its own window.
- If Whisper repeats your custom dictionary on near-silent audio, the repeated words are not typed.
- The hotkey must include Ctrl, Alt or Win, or be an F-key. Unsafe hotkeys saved by earlier versions are reset.

**Reliability fixes**

- A slow or failed transcription can no longer shift later results into the wrong dictation.
- After a GPU failure, Typra falls back to the CPU and keeps working.
- The first model download is no longer cut off after 120 seconds.
- The first word is no longer cut off after a pause, and the last words before you stop are no longer lost.
- The last phrase of a session is saved in the correct history entry.
- The AI model is loaded once instead of for every phrase, and the model you select is the one used.
- Norwegian works (language code `no`).
- Only one copy of Typra can run at a time.
- Microphones are remembered by name rather than by index.
- An error is logged instead of closing the whole app, and you are told if the speech model stops working.
- Accented characters in worker messages, for example in a user name, no longer break communication with the speech model.

**Known limitations**

- Python 3.11.9 is the last 3.11 release with Windows installers. Later security fixes for 3.11 are published only as source code. Typra does not handle untrusted archives or web content, so the exposure is low, but a future version should move to a newer Python.
- The installer is not code-signed (see below).

---

## Building the installer

1. Install [NSIS 3](https://nsis.sourceforge.io/Download).
2. Run `installer\build.bat`, or `installer\build.bat 2.1.0` to set the version.
3. Publish the SHA-256 checksum that the script prints together with the `.exe`.

To update dependencies, edit `requirements.txt` and regenerate the lock file with 64-bit Python 3.11 on Windows: `python tools\lock_requirements.py`.

The installer is not code-signed, so Windows SmartScreen and some antivirus products may warn about it until it builds a reputation. Signing it with a code-signing certificate, for example through SignPath for open-source projects, removes most of these warnings.

---

## Project structure

```
Typra/
├── assets/               # Icons and visual assets
├── audio/                # Microphone capture
├── config/               # Settings management and persistence
├── db/                   # SQLite transcription history
├── input/                # Hotkey listener and keyboard simulation
├── installer/            # NSIS installer script and build script
├── stt/                  # Whisper and LLM worker processes, voice commands
├── tools/                # Maintenance scripts (dependency lock)
├── ui/                   # PyQt6 interface, overlay and tray icon
├── download_models.py    # Optional: pre-download an AI Cleanup model
├── main.py               # Entry point
├── requirements.txt      # Top-level dependencies
├── requirements.lock     # Exact, hash-pinned dependencies used for installs
├── run.bat               # Run from source (calls setup.bat on first run)
├── setup.bat             # Create .venv and install requirements.lock
└── typra.ico             # Application icon
```

---

## Licence

MIT: free to use, share and modify. See [LICENSE](LICENSE) for details.

---

## Acknowledgements

- [OpenAI Whisper](https://github.com/openai/whisper): speech recognition model
- [faster-whisper](https://github.com/SYSTRAN/faster-whisper): optimised Whisper inference
- [Gemma](https://ai.google.dev/gemma): on-device language models by Google
- [Llama](https://ai.meta.com/llama/): lightweight language models by Meta
- [PyQt6](https://pypi.org/project/PyQt6/): user interface framework

---

> [!WARNING]
> Typra is an early-release tool provided as-is. It may contain bugs or behave unexpectedly in some environments. Use at your own discretion.
