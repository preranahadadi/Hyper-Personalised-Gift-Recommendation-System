from __future__ import annotations

from enum import Enum
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field


# ─── Input models ────────────────────────────────────────────────────────────

class Experience(BaseModel):
    title: str
    company: str
    description: str


class LinkedInProfile(BaseModel):
    headline: str
    about: str
    experience: List[Experience] = []
    recent_posts: List[str] = []
    recent_comments: List[str] = []
    engaged_topics: List[str] = []


class RelationshipContext(BaseModel):
    relationship_type: str
    last_interaction: str
    business_goal: str


class GiftContext(BaseModel):
    occasion: str
    budget_min: float
    budget_max: float
    currency: str
    country: str


class Contact(BaseModel):
    name: str
    role: str
    company: str
    location: str
    linkedin_profile: LinkedInProfile
    relationship_context: RelationshipContext
    gift_context: GiftContext


# ─── Workflow intermediate models ─────────────────────────────────────────────

class ProfileSignals(BaseModel):
    strong_signals: List[str] = []
    weak_signals: List[str] = []
    signals_to_avoid: List[str] = []


class ProductCandidate(BaseModel):
    title: str
    url: str
    snippet: str
    source_query: str
    estimated_price: Optional[str] = None
    domain: Optional[str] = None
    is_valid: bool = False
    validation_reason: str = ""


class SearchTrace(BaseModel):
    queries_used: List[str] = []
    products_considered_count: int = 0


# ─── Output models ────────────────────────────────────────────────────────────

class GiftRecommendation(BaseModel):
    rank: int
    gift_name: str
    product_url: str
    store: str
    estimated_price: str
    why_this_gift: str
    personalisation_reasoning: str
    personalised_message: str
    confidence_score: float = Field(default=0.5, ge=0.0, le=1.0)
    risk_level: str = "medium"  # low | medium | high
    assumptions: List[str] = []


class ReviewStatus(str, Enum):
    PENDING = "pending_review"
    APPROVED = "approved"
    REJECTED = "rejected"
    REGENERATING = "regenerating"
    COMPLETED = "completed"
    FAILED = "failed"


class HumanReview(BaseModel):
    status: ReviewStatus = ReviewStatus.PENDING
    available_actions: List[str] = ["approve", "reject", "edit", "regenerate"]


class WorkflowResult(BaseModel):
    contact_name: str
    profile_signals: ProfileSignals
    search_trace: SearchTrace
    recommended_gifts: List[GiftRecommendation]
    human_review: HumanReview


# ─── API request / response models ───────────────────────────────────────────

class RunWorkflowRequest(BaseModel):
    contacts: List[Contact]


class ReviewAction(BaseModel):
    action: str  # approve | reject | edit | regenerate
    edited_gifts: Optional[List[Dict[str, Any]]] = None
    feedback: Optional[str] = None
    tone: Optional[str] = "professional"  # professional | warm | formal


class WorkflowRunInfo(BaseModel):
    thread_id: str
    contact_name: str
    status: str
    result: Optional[Dict[str, Any]] = None
    error: Optional[str] = None
