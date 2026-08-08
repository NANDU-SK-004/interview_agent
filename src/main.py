from typing import Any, Dict, List, Optional
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field

app = FastAPI(title="Interview Agent API", version="1.0.0")

# Enable CORS for frontend integration
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Pydantic Schemas matching spec.md

class CandidateMember(BaseModel):
    id: str
    name: str
    jobRole: str
    yearsExperience: float
    education: str
    status: str

class CandidateMission(BaseModel):
    day: int
    title: str
    passed: Optional[bool] = None
    skipped: Optional[bool] = None
    attempts: Optional[int] = None

class CandidateSignals(BaseModel):
    commitDays: int
    missionsCompleted: int
    missionsFirstTry: int

class Candidate(BaseModel):
    member: CandidateMember
    missions: List[CandidateMission]
    signals: CandidateSignals

class InterviewRequest(BaseModel):
    sessionId: str
    candidate: Optional[Candidate] = None
    message: Optional[str] = None

class Feedback(BaseModel):
    summary: str
    strengths: List[str]
    gaps: List[str]
    next: List[str]

class InterviewResponse(BaseModel):
    reply: str
    done: bool
    feedback: Optional[Feedback] = None

@app.post("/api/interview", response_model=InterviewResponse)
def handle_interview_turn(request: InterviewRequest):
    # Validate request according to flow
    if not request.candidate and not request.message:
        raise HTTPException(
            status_code=400,
            detail="Either 'candidate' (to start) or 'message' (for conversation turn) must be provided."
        )

    # 1. Start Interview (candidate provided)
    if request.candidate is not None:
        return InterviewResponse(
            reply=f"Welcome, {request.candidate.member.name}. Let's begin your interview.",
            done=False
        )

    # 2 & 3. Conversation Turn or End Interview (message provided)
    user_msg = request.message.strip() if request.message else ""
    
    # Check if the user wants to end the interview (for testing/stub purposes)
    if user_msg.lower() == "end":
        return InterviewResponse(
            reply="Interview completed.",
            done=True,
            feedback=Feedback(
                summary="The candidate demonstrated strong software engineering foundations and completed relevant training modules successfully.",
                strengths=[
                    "Strong background in API development and vector databases.",
                    "High completion rate of curriculum missions."
                ],
                gaps=[
                    "Limited direct experience with Model Context Protocol (MCP) details."
                ],
                next=[
                    "Deep dive into MCP specification.",
                    "Practice designing multi-agent communication protocols."
                ]
            )
        )

    # Standard conversational turn response
    return InterviewResponse(
        reply=f"This is a stub conversation turn response. You said: '{user_msg}'. Type 'end' to finish the interview.",
        done=False
    )
