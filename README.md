# Kubernetes Log Analysis AIOps System

Intelligent log analysis system for Kubernetes clusters using troubleshoot.sh support bundles.

## Features

- **Log Bundle Analysis**: Extract and parse troubleshoot.sh support bundles
- **Intelligent Noise Reduction**: Filter redundant logs and irrelevant warnings
- **Root Cause Analysis**: Identify root causes and cascading failures
- **LLM Integration**: Natural language queries and advanced log analysis
- **Web UI**: Visual cluster representation with timelines and graphs

## Installation

```bash
pip install -r requirements.txt
```

### LLM Model Setup

Download a GGUF model (recommended: Nemotron-Mini-4B-Instruct-Q4_K_M):

```bash
mkdir -p models
cd models
# Download from https://huggingface.co/bartowski/Nemotron-Mini-4B-Instruct-GGUF
# Recommended: Nemotron-Mini-4B-Instruct-Q4_K_M.gguf (~2.7GB)
```

## Usage

### Phase 1: CLI Analysis

Analyze a log bundle:

```bash
python -m src.cli.main bundle.tgz -o ./output -j analysis_result.json
```

Options:
- `-o, --output`: Output directory for extracted bundle
- `-j, --json`: Path to output JSON report
- `--min-warning-frequency`: Minimum frequency for warnings (default: 3)

### Phase 2: Interactive Queries

Query analysis results using natural language:

```bash
python -m src.cli.interactive analysis_result.json models/Nemotron-Mini-4B-Instruct-Q4_K_M.gguf
```

Example queries:
- "Which pods are not working properly?"
- "What is the root cause of the db pod crashing?"
- "Show me connectivity issues"

### Phase 3: Web UI

Start the FastAPI server:

```bash
uvicorn src.api.main:app --reload --host 0.0.0.0 --port 8000
```

Open http://localhost:8000 in your browser.

Features:
- Upload and analyze bundles
- Visual cluster dependency graph (Mermaid)
- Error timeline visualization
- Pod status overview
- Natural language query interface

## Architecture

### Phase 1: CLI Analysis
- `src/bundle/`: Bundle extraction and parsing
- `src/analysis/`: Log analysis, noise reduction, root cause detection
- `src/cli/main.py`: CLI orchestration

### Phase 2: LLM Integration
- `src/llm/model_loader.py`: GGUF model loading
- `src/llm/log_analyzer.py`: LLM-based log analysis
- `src/llm/query_interface.py`: Natural language query processing
- `src/cli/interactive.py`: Interactive REPL

### Phase 3: Web UI
- `src/api/main.py`: FastAPI backend
- `frontend/index.html`: Web interface

## Model Recommendations

For CPU inference, recommended models:
- **Nemotron-Mini-4B-Instruct-Q4_K_M** (~2.7GB) - Recommended
- **Qwen2.5-3B-Instruct-GGUF** - Alternative
- **Phi-3-mini-4k-instruct-GGUF** - Alternative

## License

MIT

