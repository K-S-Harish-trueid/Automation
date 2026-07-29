# LLMs — Local Model Setup (Ollama)

Local text-generation models running via [Ollama](https://ollama.com), used for fast, offline/on-prem inference — including Arabic-language processing.

## Prerequisites

- **Ollama** — already installed on this machine (`ollama version 0.32.5`). No reinstall needed.
  - Verify anytime: `ollama --version`
  - Ollama runs as a background service; `ollama serve` starts it manually if it's not already running.
- **Disk space** — models are multi-GB. Budget ~5–20 GB per model depending on size (see table below).
- **RAM/VRAM** — 7–8B models comfortably run on 8–16 GB RAM (CPU) or a GPU with ≥8 GB VRAM for good speed. Larger MoE models (30B) need more RAM but are fast per-token since only a fraction of params activate.

### Fresh-machine install (reference only — not needed here)

```powershell
winget install Ollama.Ollama
```
or download the installer from https://ollama.com/download. After install, confirm with `ollama --version` and pull a model with `ollama pull <model>`.

## Currently installed models

```
qwen2.5vl:3b    3.2 GB   (vision-language)
qwen2.5vl:7b    6.0 GB   (vision-language)
llama3.2:3b     2.0 GB   (general, small/fast)
qwen2.5:7b      4.7 GB   (general text)
```

## Recommendation: fast model with strong Arabic support

| Model | Pull tag | Size | Context | Why |
|---|---|---|---|---|
| **Command R7B Arabic** ⭐ | `command-r7b-arabic` | 5.1 GB | 128K | Purpose-built by Cohere specifically for Arabic (MSA + dialects) enterprise use cases — best raw Arabic quality at a 7B "fast" size, strong RAG/citation accuracy. **Best pick if Arabic quality is the priority.** |
| Qwen3 8B | `qwen3:8b` | 5.2 GB | 40K | Newer than the qwen2.5 already installed; 100+ language multilingual training (incl. Arabic); has a toggleable "thinking" mode for harder tasks. Good general-purpose upgrade. |
| Qwen3 4B | `qwen3:4b` | 2.5 GB | 256K | Smaller/faster than the 8B, huge 256K context, still strong multilingual — good if speed/latency matters more than a small quality gap. |
| Qwen3 30B-A3B | `qwen3:30b` | 19 GB | 256K | Mixture-of-experts — only ~3B params active per token, so it's fast despite the large download/RAM footprint, with higher ceiling quality than the dense 8B. |

**Pick:** `command-r7b-arabic` for the best Arabic-specific quality at a fast, similarly-sized footprint to the `qwen2.5:7b` already installed. Use `qwen3:4b` as a lightweight/faster fallback if latency is critical.

### Pull a model

```powershell
ollama pull command-r7b-arabic
ollama pull qwen3:4b   # optional fallback
```

### Run / test

```powershell
ollama run command-r7b-arabic "لخص هذه الفقرة بالعربية: ..."
```

### API usage (Python)

```python
import ollama

response = ollama.chat(
    model="command-r7b-arabic",
    messages=[{"role": "user", "content": "مرحبا، كيف حالك؟"}],
)
print(response["message"]["content"])
```

See [requirements.txt](requirements.txt) for the Python client dependency.
