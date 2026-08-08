import os
import json
import logging
from typing import Any, Dict, List, Optional
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from dotenv import load_dotenv
import google.generativeai as genai

# Load environment variables
load_dotenv()

# Set up logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("interview_agent")

app = FastAPI(title="Interview Agent API", version="1.3.0")

# Enable CORS for frontend integration
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Resolve path to curriculum.json
BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CURRICULUM_PATH = os.path.join(BASE_DIR, "data", "curriculum.json")

# Load curriculum days map at startup
try:
    with open(CURRICULUM_PATH, "r", encoding="utf-8") as f:
        curriculum_data = json.load(f)
    curriculum_days = {d["day"]: d for d in curriculum_data.get("days", [])}
    logger.info(f"Loaded {len(curriculum_days)} days from curriculum.json")
except Exception as e:
    logger.error(f"Failed to load curriculum.json from {CURRICULUM_PATH}: {e}")
    curriculum_days = {}

# Initialize Gemini Client
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")

SYSTEM_PROMPT = (
    "You are a senior technical interviewer conducting a professional interview for an AI/software engineering candidate.\n"
    "Your task is to ask a single, natural, conversational, and targeted question probing the candidate's understanding of a specific topic from their curriculum training.\n"
    "Guidelines:\n"
    "1. Ask exactly ONE question. Do not include any intro, outro, preamble, or conversational fluff like 'Great, let's move on to the next topic' or 'Welcome to the interview'. Just output the question itself.\n"
    "2. The question must be realistic, probing, and conversational (as if asked in a live interview).\n"
    "3. Do not use generic template phrasing. Incorporate details from the topic's objectives and tools to make it concrete and professional.\n"
    "4. Keep it concise, engaging, and focused.\n"
)

if not GEMINI_API_KEY:
    logger.warning("WARNING: GEMINI_API_KEY environment variable is not set. API calls requiring LLM will fail with a configuration error.")
    gemini_model = None
else:
    genai.configure(api_key=GEMINI_API_KEY)
    gemini_model = genai.GenerativeModel(
        model_name="gemini-3.5-flash",
        system_instruction=SYSTEM_PROMPT
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


# Session State Store
# sessionId -> {
#     "candidate": Candidate,
#     "topic_queue": List[Dict[str, Any]],
#     "current_index": int,
#     "current_question_asked": Optional[str],
#     "answers_collected": List[Dict[str, Any]]
# }
sessions: Dict[str, Dict[str, Any]] = {}


def select_interview_topics(
    candidate_missions: List[CandidateMission],
    curriculum_days: Dict[int, Any]
) -> List[Dict[str, Any]]:
    """
    Selects 8-10 topics from curriculum.json based on the candidate's history.
    Prioritizes:
    - Struggled: passed with high attempt counts (attempts > 1), sorted descending
    - Skipped: skipped is True
    - Easy: passed with attempts == 1
    """
    struggled = []
    skipped = []
    easy = []

    for mission in candidate_missions:
        day_num = mission.day
        if day_num not in curriculum_days:
            continue
        
        curr_day = curriculum_days[day_num]
        
        # Check if skipped or passed
        is_skipped = mission.skipped if mission.skipped is not None else False
        is_passed = mission.passed if mission.passed is not None else False
        attempts = mission.attempts if mission.attempts is not None else 1
        
        topic_info = {
            "day": day_num,
            "title": curr_day.get("title", mission.title),
            "objectives": curr_day.get("objectives", []),
            "tools": curr_day.get("tools", []),
            "attempts": attempts,
            "passed": is_passed,
            "skipped": is_skipped
        }
        
        if is_skipped:
            skipped.append(topic_info)
        elif is_passed:
            if attempts > 1:
                struggled.append(topic_info)
            else:
                easy.append(topic_info)

    # Sort struggled by attempts descending
    struggled.sort(key=lambda x: x["attempts"], reverse=True)
    
    # Target distribution:
    # - struggled: up to 4
    # - skipped: up to 3
    # - easy: up to 3
    selected_struggled = struggled[:4]
    selected_skipped = skipped[:3]
    selected_easy = easy[:3]
    
    selected = selected_struggled + selected_skipped + selected_easy
    
    # Remaining candidates to draw from to reach 10 if we have less
    remaining_struggled = struggled[4:]
    remaining_skipped = skipped[3:]
    remaining_easy = easy[3:]
    
    # Fill up to 10 if we have less
    while len(selected) < 10:
        if remaining_struggled:
            selected.append(remaining_struggled.pop(0))
        elif remaining_skipped:
            selected.append(remaining_skipped.pop(0))
        elif remaining_easy:
            selected.append(remaining_easy.pop(0))
        else:
            break
            
    # Slice to maximum 10 topics
    selected = selected[:10]
    
    # Sort chronologically by day
    selected.sort(key=lambda x: x["day"])
    
    return selected


def generate_interview_question(topic: Dict[str, Any], candidate: Candidate) -> str:
    """Uses Google's Gemini API (gemini-1.5-flash) to generate a targeted conversational interview question."""
    if not GEMINI_API_KEY or not gemini_model:
        raise HTTPException(
            status_code=500,
            detail="Gemini API key is not configured. Please add GEMINI_API_KEY to your .env file."
        )

    objectives_str = "\n".join(f"- {obj}" for obj in topic.get("objectives", []))
    tools_str = ", ".join(topic.get("tools", []))
    
    user_prompt = (
        f"Candidate Name: {candidate.member.name}\n"
        f"Job Role: {candidate.member.jobRole}\n"
        f"Years of Experience: {candidate.member.yearsExperience}\n\n"
        f"Topic details:\n"
        f"Day: {topic['day']}\n"
        f"Title: {topic['title']}\n"
        f"Tools covered: {tools_str}\n"
        f"Objectives covered:\n"
        f"{objectives_str}\n\n"
        f"Please generate ONE conversational, probing technical interview question about this topic. Do not include templates or greetings. Ask a direct question about one or more tools or objectives."
    )

    try:
        response = gemini_model.generate_content(user_prompt)
        return response.text.strip()
    except Exception as e:
        logger.error(f"Error calling Gemini API: {e}")
        raise HTTPException(
            status_code=502,
            detail=f"Error communicating with Gemini API: {str(e)}"
        )


@app.post("/api/interview", response_model=InterviewResponse)
def handle_interview_turn(request: InterviewRequest):
    session_id = request.sessionId

    # 1. New Session (candidate details provided)
    if request.candidate is not None:
        # Perform topic selection
        selected_topics = select_interview_topics(request.candidate.missions, curriculum_days)
        
        # Initialize session state
        sessions[session_id] = {
            "candidate": request.candidate,
            "topic_queue": selected_topics,
            "current_index": 0,
            "current_question_asked": None,
            "answers_collected": []
        }

        # Print/log the selected topics clearly
        print(f"\n[SESSION START] sessionId: {session_id}")
        print(f"Candidate: {request.candidate.member.name} ({request.candidate.member.jobRole})")
        print(f"Selected Topics ({len(selected_topics)}):")
        for idx, t in enumerate(selected_topics):
            status_desc = "SKIPPED" if t["skipped"] else f"PASSED ({t['attempts']} attempts)"
            print(f"  [{idx + 1}] Day {t['day']}: {t['title']} - {status_desc}")
        print("-" * 50 + "\n")

        # Also log via logger
        logger.info(f"Session {session_id} initialized with {len(selected_topics)} topics for candidate {request.candidate.member.name}.")

        return InterviewResponse(
            reply=f"Welcome, {request.candidate.member.name}. Let's begin your interview.",
            done=False
        )

    # 2. Existing Session
    if session_id not in sessions:
        raise HTTPException(
            status_code=400,
            detail="Session not found. Please initialize the interview by providing candidate details first."
        )

    session_state = sessions[session_id]
    user_msg = request.message.strip() if request.message else ""
    topic_queue = session_state["topic_queue"]
    current_idx = session_state["current_index"]
    candidate = session_state["candidate"]

    # Allow force-ending the interview via "end"
    if user_msg.lower() == "end":
        logger.info(f"Session {session_id} manually ended by candidate.")
        return generate_completion_response(session_state)

    # Check if we have not asked the question for the current topic yet
    if session_state["current_question_asked"] is None:
        # Generate and ask the question for topic_queue[current_idx]
        if current_idx < len(topic_queue):
            current_topic = topic_queue[current_idx]
            question = generate_interview_question(current_topic, candidate)
            session_state["current_question_asked"] = question
            logger.info(f"Asked question for session {session_id}, Day {current_topic['day']}: {question[:60]}...")
            return InterviewResponse(reply=question, done=False)
        else:
            return generate_completion_response(session_state)
            
    # If a question has already been asked, user_msg is the answer to that question
    else:
        current_topic = topic_queue[current_idx]
        asked_question = session_state["current_question_asked"]
        
        # Record the answer
        session_state["answers_collected"].append({
            "day": current_topic["day"],
            "title": current_topic["title"],
            "question": asked_question,
            "answer": user_msg
        })
        logger.info(f"Recorded answer for session {session_id}, Day {current_topic['day']}: {user_msg[:60]}...")
        
        # Progress the queue
        session_state["current_index"] += 1
        current_idx = session_state["current_index"]
        session_state["current_question_asked"] = None  # Reset for next topic
        
        # Check if queue is completed
        if current_idx >= len(topic_queue):
            logger.info(f"Session {session_id} completed (all topics covered).")
            return generate_completion_response(session_state)
            
        # Generate the next question
        next_topic = topic_queue[current_idx]
        question = generate_interview_question(next_topic, candidate)
        session_state["current_question_asked"] = question
        logger.info(f"Asked question for session {session_id}, Day {next_topic['day']}: {question[:60]}...")
        return InterviewResponse(reply=question, done=False)


def generate_completion_response(session_state: Dict[str, Any]) -> InterviewResponse:
    """Helper to return the final feedback report when the interview finishes."""
    candidate_name = session_state["candidate"].member.name
    answers = session_state["answers_collected"]
    num_answered = len(answers)
    
    return InterviewResponse(
        reply="Interview completed. Thank you for your time!",
        done=True,
        feedback=Feedback(
            summary=f"The candidate {candidate_name} completed the interview session answering {num_answered} topics.",
            strengths=[
                "Successfully walked through structured topics from the curriculum.",
                "Demonstrated consistency in discussing prior experience."
            ],
            gaps=[
                "Probing indicated areas of focus on curriculum days that required multiple attempts."
            ],
            next=[
                "Focus on deepening understanding of topics that required multiple attempts.",
                "Review the capstone project objectives."
            ]
        )
    )
