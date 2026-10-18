"""Contract 4 LLM call: generate(prompt) -> iterator of text chunks, from IBM Granite on the same box.

Tries, in order:
  1. GRANITE_URL  - a llama.cpp `llama-server` (e.g. http://127.0.0.1:8080) serving a Granite GGUF
  2. GRANITE_GGUF - path to a big-endian Granite GGUF, loaded in-process with llama-cpp-python
  3. stub        - echoes the text after the last "LETTER:" marker, so the demo never hangs
"""
import json
import os
import time
import urllib.request

_llm = None


def backend():
    if os.environ.get("GRANITE_URL"):
        return "granite-server"
    if os.environ.get("GRANITE_GGUF"):
        return "granite-inprocess"
    return "stub"


def _server(prompt, max_tokens):
    body = json.dumps({"prompt": prompt, "n_predict": max_tokens, "temperature": 0.2, "stream": True}).encode()
    req = urllib.request.Request(os.environ["GRANITE_URL"].rstrip("/") + "/completion", body, {"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=120) as resp:
        for line in resp:  # server-sent events: b'data: {"content": "...", "stop": false}'
            if line.startswith(b"data: "):
                event = json.loads(line[6:])
                if event.get("content"):
                    yield event["content"]
                if event.get("stop"):
                    return


def _inprocess(prompt, max_tokens):
    global _llm
    if _llm is None:
        from llama_cpp import Llama
        _llm = Llama(model_path=os.environ["GRANITE_GGUF"], n_ctx=2048, n_threads=os.cpu_count(), verbose=False)
    for part in _llm(prompt, max_tokens=max_tokens, temperature=0.2, stream=True):
        yield part["choices"][0]["text"]


def _stub(prompt, _max_tokens):
    for word in prompt.rsplit("LETTER:", 1)[-1].strip().split(" "):
        time.sleep(0.01)
        yield word + " "


def generate(prompt, max_tokens=450):
    return {"granite-server": _server, "granite-inprocess": _inprocess, "stub": _stub}[backend()](prompt, max_tokens)


if __name__ == "__main__":
    # Timing check for the plan's "time one 150-token letter" step.
    start = time.perf_counter()
    n = sum(1 for _ in generate("Write one friendly sentence about filing a workers' compensation claim.\nLETTER: Hello there.", 150))
    print(f"{backend()}: {n} chunks in {time.perf_counter() - start:.1f}s")
