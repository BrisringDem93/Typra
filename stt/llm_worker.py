# stt/llm_worker.py
"""
LLM worker subprocess: loads a GGUF model once, then rewrites text on request.
Protocol: one JSON object per line.
  -> {"model_path": "...", "n_gpu_layers": 0}   first line: load the model
  <- {"ok": true}  |  {"error": "..."}
  -> {"system": "...", "text": "..."}           rewrite request
  <- {"text": "..."}  |  {"error": "..."}
Text only travels through the pipe; nothing is written to disk.
"""
import json
import sys


def _complete(model, system: str, text: str) -> str:
    try:
        messages = [{"role": "system", "content": system},
                    {"role": "user", "content": text}]
        reply = model.create_chat_completion(messages=messages,
                                             max_tokens=256, temperature=0.1)
    except Exception:
        # Some chat templates (e.g. Gemma's) reject the system role
        messages = [{"role": "user", "content": f"{system}\n\n{text}"}]
        reply = model.create_chat_completion(messages=messages,
                                             max_tokens=256, temperature=0.1)
    return reply["choices"][0]["message"]["content"].strip()


def main() -> None:
    # Only protocol replies may reach stdout: send library output to stderr.
    out = sys.stdout
    sys.stdout = sys.stderr

    def send(obj: dict) -> None:
        out.write(json.dumps(obj) + "\n")
        out.flush()

    try:
        cfg = json.loads(sys.stdin.readline())
        from llama_cpp import Llama
        model = Llama(model_path=cfg["model_path"], n_ctx=1024, n_threads=2,
                      n_gpu_layers=int(cfg.get("n_gpu_layers", 0)), verbose=False)
    except Exception as exc:
        send({"error": f"{type(exc).__name__}: {exc}"})
        return
    send({"ok": True})

    for line in sys.stdin:
        if not line.strip():
            continue
        try:
            req = json.loads(line)
            send({"text": _complete(model, req["system"], req["text"])})
        except Exception as exc:
            send({"error": f"{type(exc).__name__}: {exc}"})


if __name__ == "__main__":
    main()
