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

app = FastAPI(title="Interview Agent API", version="1.5.0")

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

# Initialize Gemini Clients
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

EVALUATION_SYSTEM_PROMPT = (
    "You are a senior technical interviewer evaluating a candidate's answer to an interview question.\n"
    "Based on the curriculum topic's objectives and the candidate's response, classify the answer as either:\n"
    "- STRONG: The answer is complete, accurate, demonstrates real understanding, or provides valid reasoning.\n"
    "- SHALLOW: The answer is extremely brief, vague, avoids details, or demonstrates a clear lack of depth/understanding.\n\n"
    "Your response must begin with either 'STRONG' or 'SHALLOW' (case-insensitive) on the first line, followed by a new line with a brief, 1-sentence explanation of why you made this judgment.\n"
)

FEEDBACK_SYSTEM_PROMPT = (
    "You are a senior technical interviewer compiling a final evaluation report for an AI/software engineering candidate.\n"
    "Based on the full transcript of the interview (topics, questions asked, and candidate answers), you must produce a detailed, candidate-specific feedback report.\n"
    "Your response must be a single, valid JSON object containing exactly the following keys:\n"
    "{\n"
    "  \"summary\": \"A concise 2-3 sentence overview of their overall performance and suitability, referencing their actual answers.\",\n"
    "  \"strengths\": [\"List of 2-3 specific technical areas or concepts they demonstrated solid mastery in, referencing details from their answers.\"],\n"
    "  \"gaps\": [\"List of 2-3 specific technical gaps, misconceptions, or areas they struggled to elaborate on, based on their answers.\"],\n"
    "  \"next\": [\"List of 2-3 concrete, actionable next steps or learning suggestions tailored to their performance.\"]\n"
    "}\n\n"
    "Guidelines:\n"
    "1. Do NOT use boilerplate or generic sentences like 'Demonstrated baseline participation'. Mention specific concepts they discussed (e.g. Reciprocal Rank Fusion, all-MiniLM-L6-v2, ChromaDB, etc.).\n"
    "2. Output ONLY the JSON object. Do not include any markdown fences, explanation text, or preambles. Output clean JSON."
)

if not GEMINI_API_KEY:
    logger.warning("WARNING: GEMINI_API_KEY environment variable is not set. API calls requiring LLM will fail with a configuration error.")
    gemini_model = None
    gemini_eval_model = None
else:
    genai.configure(api_key=GEMINI_API_KEY)
    gemini_model = genai.GenerativeModel(
        model_name="gemini-3.1-flash-lite",
        system_instruction=SYSTEM_PROMPT
    )
    gemini_eval_model = genai.GenerativeModel(
        model_name="gemini-3.1-flash-lite",
        system_instruction=EVALUATION_SYSTEM_PROMPT
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
#     "follow_up_asked": bool,
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
    """Uses Google's Gemini API (gemini-3.5-flash) to generate a targeted conversational interview question."""
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
            status_code=522,
            detail=f"Error communicating with Gemini API: {str(e)}"
        )


def evaluate_candidate_answer(topic: Dict[str, Any], question: str, answer: str) -> Dict[str, str]:
    """Evaluates the candidate's answer against the topic objectives. Returns a dict with status and reason."""
    if not GEMINI_API_KEY or not gemini_eval_model:
        raise HTTPException(
            status_code=500,
            detail="Gemini API key is not configured. Please add GEMINI_API_KEY to your .env file."
        )

    objectives_str = "\n".join(f"- {obj}" for obj in topic.get("objectives", []))
    
    eval_prompt = (
        f"Topic: {topic['title']}\n"
        f"Objectives:\n{objectives_str}\n\n"
        f"Question Asked: {question}\n"
        f"Candidate's Answer: {answer}\n"
    )

    try:
        response = gemini_eval_model.generate_content(eval_prompt)
        content = response.text.strip().split("\n")
        status = content[0].strip().upper()
        reason = "\n".join(content[1:]).strip() if len(content) > 1 else ""
        
        # Normalize status to STRONG or SHALLOW
        if "SHALLOW" in status:
            status = "SHALLOW"
        else:
            status = "STRONG"
            
        return {"status": status, "reason": reason}
    except Exception as e:
        logger.error(f"Error calling Gemini API for evaluation: {e}")
        # Default to STRONG on failure to avoid blocking the interview flow
        return {"status": "STRONG", "reason": f"API error: {str(e)}"}


def generate_follow_up_question(topic: Dict[str, Any], candidate: Candidate, previous_question: str, previous_answer: str) -> str:
    """Generates a deeper, conversational follow-up question on the current topic using context from the previous turn."""
    if not GEMINI_API_KEY or not gemini_model:
        raise HTTPException(
            status_code=500,
            detail="Gemini API key is not configured. Please add GEMINI_API_KEY to your .env file."
        )

    objectives_str = "\n".join(f"- {obj}" for obj in topic.get("objectives", []))
    tools_str = ", ".join(topic.get("tools", []))
    
    follow_up_prompt = (
        f"Candidate Name: {candidate.member.name}\n"
        f"Job Role: {candidate.member.jobRole}\n"
        f"Years of Experience: {candidate.member.yearsExperience}\n\n"
        f"Topic: Day {topic['day']} - {topic['title']}\n"
        f"Tools covered: {tools_str}\n"
        f"Objectives: \n{objectives_str}\n\n"
        f"Previously Asked Question: {previous_question}\n"
        f"Candidate's Shallow Answer: {previous_answer}\n\n"
        f"Please generate ONE targeted, conversational, deeper follow-up question about this topic. The question should probe their understanding further, address the gaps in their shallow response, or ask them to elaborate on details."
    )

    try:
        response = gemini_model.generate_content(follow_up_prompt)
        return response.text.strip()
    except Exception as e:
        logger.error(f"Error calling Gemini API for follow-up question: {e}")
        raise HTTPException(
            status_code=522,
            detail=f"Error communicating with Gemini API for follow-up: {str(e)}"
        )


def generate_dynamic_feedback(session_state: Dict[str, Any]) -> Feedback:
    """Uses Google's Gemini API to generate genuinely candidate-specific feedback based on the full transcript."""
    if not GEMINI_API_KEY:
        return Feedback(
            summary="Fallback summary: Gemini API key is missing.",
            strengths=["Completed the interview."],
            gaps=["Gemini key missing."],
            next=["Configure GEMINI_API_KEY."]
        )

    candidate = session_state["candidate"]
    answers = session_state["answers_collected"]
    
    # Format the transcript clearly
    transcript_lines = []
    for idx, ans in enumerate(answers):
        q_type = "Follow-up" if ans.get("type") == "follow_up" else "Primary"
        transcript_lines.append(
            f"Turn {idx + 1} [{ans['title']} - {q_type} Question]:\n"
            f"Question Asked: {ans['question']}\n"
            f"Candidate Answer: {ans['answer']}\n"
            f"Evaluation: {ans['evaluation']['status']}\n"
        )
    
    transcript_str = "\n".join(transcript_lines)
    
    user_prompt = (
        f"Candidate Name: {candidate.member.name}\n"
        f"Job Role: {candidate.member.jobRole}\n"
        f"Years of Experience: {candidate.member.yearsExperience}\n\n"
        f"Interview Transcript:\n"
        f"{transcript_str}\n\n"
        f"Please generate the feedback JSON matching the requested keys."
    )

    text_response = ""
    try:
        model = genai.GenerativeModel(
            model_name="gemini-3.1-flash-lite",
            system_instruction=FEEDBACK_SYSTEM_PROMPT
        )
        response = model.generate_content(user_prompt)
        text_response = response.text.strip()
        
        # Scrub code blocks
        if text_response.startswith("```"):
            lines = text_response.split("\n")
            if lines[0].startswith("```"):
                lines = lines[1:]
            if lines[-1].startswith("```"):
                lines = lines[:-1]
            text_response = "\n".join(lines).strip()
            
        feedback_data = json.loads(text_response)
        
        return Feedback(
            summary=feedback_data.get("summary", ""),
            strengths=feedback_data.get("strengths", []),
            gaps=feedback_data.get("gaps", []),
            next=feedback_data.get("next", [])
        )
    except Exception as e:
        logger.error(f"Error parsing Gemini feedback response: {e}. Raw: {text_response}")
        # Build a structured fallback based on our tracked topics
        strong_topics = list(set([a["title"] for a in answers if a["evaluation"]["status"] == "STRONG"]))
        shallow_topics = list(set([a["title"] for a in answers if a["evaluation"]["status"] == "SHALLOW"]))
        
        return Feedback(
            summary=f"The candidate {candidate.member.name} completed the interview answering {len(answers)} topics.",
            strengths=[f"Demonstrated good baseline participation on: {t}" for t in strong_topics] if strong_topics else ["Walked through the technical topics."],
            gaps=[f"Showed shallower understanding of: {t}" for t in shallow_topics] if shallow_topics else ["No major gaps flagged."],
            next=[f"Deep dive into: {t}" for t in shallow_topics] if shallow_topics else ["Ready for review."]
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
            "follow_up_asked": False,
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
            session_state["follow_up_asked"] = False
            logger.info(f"Asked primary question for session {session_id}, Day {current_topic['day']}: {question[:60]}...")
            return InterviewResponse(reply=question, done=False)
        else:
            return generate_completion_response(session_state)
            
    # If a question has already been asked, user_msg is the answer to that question
    else:
        current_topic = topic_queue[current_idx]
        asked_question = session_state["current_question_asked"]
        
        # Evaluate the answer
        eval_result = evaluate_candidate_answer(current_topic, asked_question, user_msg)
        status = eval_result["status"]
        reason = eval_result["reason"]
        
        logger.info(f"Evaluation for session {session_id}, Day {current_topic['day']}: {status}. Reason: {reason}")
        
        # Record the transaction
        is_follow_up = session_state.get("follow_up_asked", False)
        session_state["answers_collected"].append({
            "day": current_topic["day"],
            "title": current_topic["title"],
            "question": asked_question,
            "answer": user_msg,
            "evaluation": {
                "status": status,
                "reason": reason
            },
            "type": "follow_up" if is_follow_up else "primary"
        })
        
        # Decide next step:
        # If shallow and no follow-up was asked yet on this topic, ask a follow-up
        if status == "SHALLOW" and not is_follow_up:
            session_state["follow_up_asked"] = True
            
            # Generate and ask the follow-up question
            follow_up_question = generate_follow_up_question(
                current_topic, candidate, asked_question, user_msg
            )
            session_state["current_question_asked"] = follow_up_question
            
            logger.info(f"Asked follow-up question for session {session_id}, Day {current_topic['day']}: {follow_up_question[:60]}...")
            return InterviewResponse(reply=follow_up_question, done=False)
            
        # If strong or a follow-up has already been asked, move to the next topic in queue
        else:
            session_state["current_index"] += 1
            current_idx = session_state["current_index"]
            session_state["current_question_asked"] = None
            session_state["follow_up_asked"] = False  # Reset for next topic
            
            # Check if queue is completed
            if current_idx >= len(topic_queue):
                logger.info(f"Session {session_id} completed (all topics covered).")
                return generate_completion_response(session_state)
                
            # Ask the primary question for the next topic
            next_topic = topic_queue[current_idx]
            question = generate_interview_question(next_topic, candidate)
            session_state["current_question_asked"] = question
            logger.info(f"Asked primary question for session {session_id}, Day {next_topic['day']}: {question[:60]}...")
            return InterviewResponse(reply=question, done=False)


def generate_completion_response(session_state: Dict[str, Any]) -> InterviewResponse:
    """Helper to return the final feedback report when the interview finishes."""
    feedback = generate_dynamic_feedback(session_state)
    return InterviewResponse(
        reply="Interview completed. Thank you for your time!",
        done=True,
        feedback=feedback
    )
