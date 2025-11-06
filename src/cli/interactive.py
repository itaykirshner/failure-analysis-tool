"""Interactive REPL for querying analysis results."""
import json
import sys
from pathlib import Path
from typing import Optional

from src.llm import answer_query, load_gguf_model
from src.models.analysis import AnalysisResult
from src.llm.model_loader import LLMModel


def main() -> int:
    """Interactive REPL entry point."""
    if len(sys.argv) < 3:
        print("Usage: interactive.py <analysis_json> <model_path>")
        print("  analysis_json: Path to analysis_result.json from CLI")
        print("  model_path: Path to GGUF model file")
        return 1
    
    analysis_path = Path(sys.argv[1])
    model_path = Path(sys.argv[2])
    
    if not analysis_path.exists():
        print(f"Error: Analysis file not found: {analysis_path}", file=sys.stderr)
        return 1
    
    if not model_path.exists():
        print(f"Error: Model file not found: {model_path}", file=sys.stderr)
        return 1
    
    # Load analysis
    print(f"Loading analysis from: {analysis_path}")
    try:
        with open(analysis_path, "r") as f:
            data = json.load(f)
        analysis = AnalysisResult(**data)
        print(f"Loaded analysis with {len(analysis.pod_health)} pods")
    except Exception as e:
        print(f"Error loading analysis: {e}", file=sys.stderr)
        return 1
    
    # Load model
    print(f"Loading model: {model_path}")
    try:
        model = load_gguf_model(model_path)
        print("Model loaded successfully")
    except Exception as e:
        print(f"Error loading model: {e}", file=sys.stderr)
        return 1
    
    # Interactive loop
    print("\n" + "=" * 80)
    print("Interactive Query Interface")
    print("=" * 80)
    print("Ask questions about the cluster analysis.")
    print("Examples:")
    print("  - Which pods are not working properly?")
    print("  - What is the root cause of the db pod crashing?")
    print("  - Show me connectivity issues")
    print("Type 'quit' or 'exit' to exit.\n")
    
    while True:
        try:
            query = input("Query: ").strip()
            
            if not query:
                continue
            
            if query.lower() in ["quit", "exit", "q"]:
                print("Goodbye!")
                break
            
            print("\nThinking...")
            answer = answer_query(query, analysis, model)
            print(f"\nAnswer: {answer}\n")
            print("-" * 80)
        
        except KeyboardInterrupt:
            print("\n\nGoodbye!")
            break
        except Exception as e:
            print(f"Error: {e}", file=sys.stderr)
            import traceback
            traceback.print_exc()
    
    return 0


if __name__ == "__main__":
    sys.exit(main())

