# stt/subprocess_worker.py
"""
Standalone transcription subprocess.
Protocol (one line per message):
  LOAD <params_b64>          base64 JSON: model, revision, download_root,
                             device, compute_type
  TRANSCRIBE <language> <prompt_b64> <audio_b64>
  QUIT
Replies: READY | TEXT <text_b64> | EMPTY | ERROR <single-line message>
Full tracebacks go to stderr, which the parent copies into the log.
"""
import sys, base64, json, traceback
from pathlib import Path


def main():
    # Pipes default to the ANSI code page on Windows; the parent reads UTF-8.
    sys.stdin.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8", errors="backslashreplace", line_buffering=True)
    # Only protocol replies may reach stdout: send library output to stderr.
    out = sys.stdout
    out.reconfigure(encoding="utf-8", line_buffering=True)
    sys.stdout = sys.stderr

    def reply(line: str) -> None:
        out.write(line + "\n")
        out.flush()

    def error(exc: BaseException) -> None:
        traceback.print_exc()
        reply("ERROR " + " ".join(f"{type(exc).__name__}: {exc}".split())[:500])

    model = None

    for raw in sys.stdin:
        line = raw.strip()
        if not line:
            continue

        if line == "QUIT":
            break

        elif line.startswith("LOAD "):
            try:
                params = json.loads(base64.b64decode(line[5:]).decode("utf-8"))
                from faster_whisper import WhisperModel
                kwargs = dict(
                    device=params.get("device", "cpu"),
                    compute_type=params.get("compute_type", "auto"),
                    cpu_threads=2, num_workers=1,
                    download_root=params.get("download_root"),
                    revision=params.get("revision"),
                )
                # Once the pinned snapshot is on disk, load it without
                # contacting Hugging Face (works fully offline).
                cached = bool(kwargs["download_root"] and kwargs["revision"]) and (
                    Path(kwargs["download_root"])
                    / ("models--" + params["model"].replace("/", "--"))
                    / "snapshots" / kwargs["revision"]).is_dir()
                try:
                    model = WhisperModel(params["model"], local_files_only=cached, **kwargs)
                except Exception:
                    if not cached:
                        raise
                    traceback.print_exc()   # incomplete cache: fetch the missing files
                    model = WhisperModel(params["model"], **kwargs)
                reply("READY")
            except Exception as exc:
                model = None
                error(exc)

        elif line.startswith("TRANSCRIBE "):
            if model is None:
                reply("ERROR model not loaded")
                continue
            try:
                parts    = line.split(" ", 3)
                language = parts[1]
                prompt   = base64.b64decode(parts[2]).decode("utf-8") if len(parts) > 2 else ""
                b64_data = parts[3] if len(parts) > 3 else ""

                import numpy as np
                audio = np.frombuffer(base64.b64decode(b64_data), dtype=np.float32)

                kwargs = dict(
                    language=language,
                    beam_size=1,
                    best_of=1,
                    temperature=0.0,
                    without_timestamps=True,
                    word_timestamps=False,
                )
                if prompt:
                    kwargs["initial_prompt"] = prompt

                # No VAD filter — the worker's silence detection gates input
                segs, _ = model.transcribe(audio, **kwargs)
                text = " ".join(s.text.strip() for s in segs).strip()

                if text:
                    reply(f"TEXT {base64.b64encode(text.encode()).decode()}")
                else:
                    reply("EMPTY")

            except Exception as exc:
                error(exc)

        else:
            reply("ERROR unknown command")

if __name__ == "__main__":
    main()
