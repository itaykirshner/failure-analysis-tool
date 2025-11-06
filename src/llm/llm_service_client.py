"""Client for llama.cpp server API (when LLM runs as separate service)."""
import os
from typing import Optional

import httpx


class LLMServiceClient:
    """Client for communicating with llama.cpp server API."""
    
    def __init__(self, base_url: Optional[str] = None):
        """
        Initialize LLM service client.
        
        Args:
            base_url: Base URL of llama.cpp server (defaults to LLM_SERVICE_URL env var)
        """
        self.base_url = base_url or os.getenv("LLM_SERVICE_URL", "http://llm-service:8000")
        self.client = httpx.AsyncClient(timeout=300.0)  # 5 minute timeout for long generations
    
    async def generate(
        self,
        prompt: str,
        max_tokens: int = 512,
        temperature: float = 0.7,
        top_p: float = 0.9,
        stop: Optional[list[str]] = None,
    ) -> str:
        """
        Generate text from a prompt using llama.cpp server API.
        
        Args:
            prompt: Input prompt
            max_tokens: Maximum tokens to generate
            temperature: Sampling temperature
            top_p: Top-p sampling
            stop: Stop sequences
            
        Returns:
            Generated text
        """
        try:
            response = await self.client.post(
                f"{self.base_url}/completion",
                json={
                    "prompt": prompt,
                    "n_predict": max_tokens,
                    "temperature": temperature,
                    "top_p": top_p,
                    "top_k": 40,
                    "stop": stop or [],
                    "stream": False,
                },
            )
            response.raise_for_status()
            data = response.json()
            return data.get("content", "")
        except Exception as e:
            raise RuntimeError(f"LLM service request failed: {e}") from e
    
    async def health_check(self) -> bool:
        """Check if LLM service is healthy."""
        try:
            response = await self.client.get(f"{self.base_url}/health", timeout=5.0)
            return response.status_code == 200
        except Exception:
            return False
    
    async def close(self):
        """Close the HTTP client."""
        await self.client.aclose()

