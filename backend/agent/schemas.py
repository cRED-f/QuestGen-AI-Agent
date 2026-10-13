"""Typed request and response contracts for interactive agent conversations."""
from pydantic import BaseModel, Field


class AgentTurnInput(BaseModel):
    content: str = Field(min_length=1, max_length=12000)
    document_ids: list[str] | None = Field(default=None, max_length=30)


class PendingQuestion(BaseModel):
    question: str
    context: str = ""
    topic_ids: list[str] = Field(default_factory=list)
    options: list[str] = Field(default_factory=list)


class AgentActivity(BaseModel):
    tool: str
    detail: str = ""
