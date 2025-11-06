"""GGUF model loader for CPU inference."""
from pathlib import Path
from typing import Optional

try:
    from llama_cpp import Llama
except ImportError:
    Llama = None


class LLMModel:
    """Wrapper for GGUF model loaded via llama-cpp-python."""
    
    def __init__(self, model: "Llama"):
        """Initialize with a loaded Llama model."""
        self.model = model
    
    def generate(
        self,
        prompt: str,
        max_tokens: int = 512,
        temperature: float = 0.7,
        top_p: float = 0.9,
        stop: Optional[list[str]] = None,
    ) -> str:
        """
        Generate text from a prompt.
        
        Args:
            prompt: Input prompt
            max_tokens: Maximum tokens to generate
            temperature: Sampling temperature
            top_p: Top-p sampling
            stop: Stop sequences
            
        Returns:
            Generated text
        """
        if not self.model:
            raise RuntimeError("Model not loaded")
        
        response = self.model(
            prompt,
            max_tokens=max_tokens,
            temperature=temperature,
            top_p=top_p,
            stop=stop,
        )
        
        if isinstance(response, dict):
            return response.get("choices", [{}])[0].get("text", "")
        elif isinstance(response, str):
            return response
        else:
            return str(response)


def load_gguf_model(model_path: Path, n_threads: Optional[int] = None) -> LLMModel:
    """
    Load a GGUF model for CPU inference.
    
    Args:
        model_path: Path to the .gguf model file
        n_threads: Number of threads for inference (None = auto)
        
    Returns:
        LLMModel instance
        
    Raises:
        FileNotFoundError: If model file doesn't exist
        ImportError: If llama-cpp-python is not installed
        RuntimeError: If model loading fails
    """
    if Llama is None:
        raise ImportError(
            "llama-cpp-python is not installed. "
            "Install it with: pip install llama-cpp-python"
        )
    
    if not model_path.exists():
        raise FileNotFoundError(f"Model file not found: {model_path}")
    
    try:
        # Load model with CPU-optimized settings
        model = Llama(
            model_path=str(model_path),
            n_ctx=2048,  # Context window
            n_threads=n_threads,  # Auto-detect if None
            verbose=False,
        )
        
        return LLMModel(model)
    
    except Exception as e:
        raise RuntimeError(f"Failed to load model: {e}") from e

