"""Pydantic models for API requests and responses."""
from pathlib import Path
from typing import Optional

from pydantic import BaseModel, Field


class AnalyzeRequest(BaseModel):
    """Request model for bundle analysis."""
    
    bundle_path: Optional[Path] = None
    min_warning_frequency: int = Field(default=3, ge=1, le=100)


class QueryRequest(BaseModel):
    """Request model for LLM queries."""
    
    query: str = Field(..., min_length=1, max_length=1000)
    analysis_id: Optional[str] = None


class AnalyzeResponse(BaseModel):
    """Response model for bundle analysis."""
    
    analysis_id: str
    status: str
    summary: dict
    message: Optional[str] = None


class QueryResponse(BaseModel):
    """Response model for LLM queries."""
    
    answer: str
    query: str


class ClusterGraphResponse(BaseModel):
    """Response model for cluster dependency graph."""
    
    mermaid_diagram: str
    nodes: list[dict]
    edges: list[dict]


class TimelineResponse(BaseModel):
    """Response model for error timeline."""
    
    timeline: list[dict]
    error_counts: dict[str, int]


class PodStatusResponse(BaseModel):
    """Response model for pod statuses."""
    
    pods: list[dict]
    summary: dict

