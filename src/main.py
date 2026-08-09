import os
import json
import re
import logging
from typing import Any, Dict, List, Optional
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
from dotenv import load_dotenv
import google.generativeai as genai

# Load environment variables
load_dotenv()

# Set up logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("interview_agent")

app = FastAPI(title="Interview Agent API", version="1.8.0")

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
    "Based on the curriculum topic's objectives and the candidate's response, classify the answer into exactly one of these three categories:\n"
    "- STRONG: The answer is complete, accurate, demonstrates real understanding, or provides valid reasoning.\n"
    "- WRONG: The answer is factually incorrect, contains direct technical errors, or describes incorrect tools/concepts.\n"
    "- SHALLOW: The answer is extremely brief, vague, gibberish/nonsense (e.g., 'ccc', 'asdf'), off-topic, or avoids technical details.\n\n"
    "Your response must begin with either 'STRONG', 'WRONG', or 'SHALLOW' (case-insensitive) on the first line, followed by a new line with a brief, 1-sentence explanation of why you made this judgment.\n"
)

FEEDBACK_SYSTEM_PROMPT = (
    "You are a senior technical interviewer compiling a final evaluation report for an AI/software engineering candidate.\n"
    "Based on the full transcript of the interview (topics, questions asked, candidate answers, and their evaluations), you must produce a detailed, candidate-specific feedback report.\n"
    "Your response must be a single, valid JSON object containing exactly the following keys:\n"
    "{\n"
    "  \"summary\": \"A concise 2-3 sentence overview of their overall performance and suitability, referencing their actual answers. If they ended early, explicitly state that the interview was ended early by the candidate.\",\n"
    "  \"strengths\": [\"List of 2-3 specific technical areas or concepts they demonstrated solid mastery in, referencing details from their answers.\"],\n"
    "  \"gaps\": [\"List of 2-3 specific technical gaps, misconceptions, or areas they struggled to elaborate on, or got factually WRONG. For any factually WRONG answers in the transcript, explicitly and precisely describe what was incorrect in their response (do not use generic filler).\"],\n"
    "  \"next\": [\"List of 2-3 concrete, actionable next steps or learning suggestions tailored to their performance.\"]\n"
    "}\n\n"
    "Guidelines:\n"
    "1. Do NOT use boilerplate or generic sentences. Mention specific concepts discussed.\n"
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
    elapsedTime: Optional[float] = None
    requestHint: Optional[bool] = None

class Feedback(BaseModel):
    summary: str
    strengths: List[str]
    gaps: List[str]
    next: List[str]

class InterviewProgress(BaseModel):
    current: int
    total: int
    day: int
    topicTitle: str

class InterviewResponse(BaseModel):
    reply: str
    done: bool
    feedback: Optional[Feedback] = None
    progress: Optional[InterviewProgress] = None
    answerQuality: Optional[str] = None
    evaluations: Optional[List[Dict[str, Any]]] = None


# Session State Store
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
    If candidate has fewer than 4 eligible days of history, it backfills the queue using sorted curriculum days.
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
            
    # EDGE CASE: If the candidate has fewer than 4 eligible topics in their history,
    # fill with curriculum days to ensure we have a robust selection queue of at least 8 topics
    if len(selected) < 8:
        selected_days = {x["day"] for x in selected}
        for day_num in sorted(curriculum_days.keys()):
            if len(selected) >= 10:
                break
            if day_num not in selected_days:
                curr_day = curriculum_days[day_num]
                selected.append({
                    "day": day_num,
                    "title": curr_day.get("title", f"Day {day_num}"),
                    "objectives": curr_day.get("objectives", []),
                    "tools": curr_day.get("tools", []),
                    "attempts": 1,
                    "passed": False,
                    "skipped": False
                })

    # Sort chronologically by day
    selected.sort(key=lambda x: x["day"])
    
    return selected


def generate_interview_question(topic: Dict[str, Any], candidate: Candidate) -> str:
    """Uses Google's Gemini API to generate a targeted conversational interview question. Resilient to failures."""
    if not GEMINI_API_KEY or not gemini_model:
        tools_desc = f" using {', '.join(topic.get('tools', []))}" if topic.get("tools") else ""
        return f"Could you explain your experience working with {topic['title']}{tools_desc} and how you implement it in a project?"

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
        logger.error(f"Error calling Gemini API for question: {e}")
        tools_desc = f" using {', '.join(topic.get('tools', []))}" if topic.get("tools") else ""
        return f"Could you explain your experience working with {topic['title']}{tools_desc} and how you implement it in a project?"


def evaluate_candidate_answer(topic: Dict[str, Any], question: str, answer: str) -> Dict[str, str]:
    """Evaluates the candidate's answer against the topic objectives. Resilient to failures."""
    # Strict gibberish/nonsense checks before invoking API
    clean_ans = answer.strip().lower()
    if len(clean_ans) < 4 or clean_ans in ["ccc", "asdf", "qwer", "xyz", "none", "no idea", "dunno", "hello", "hi"]:
        return {
            "status": "SHALLOW",
            "reason": "Candidate provided a gibberish, empty-of-substance, or off-topic response."
        }

    if not GEMINI_API_KEY or not gemini_eval_model:
        return {"status": "STRONG", "reason": "API fallback evaluation due to missing configuration."}

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
        
        if "WRONG" in status:
            status = "WRONG"
        elif "SHALLOW" in status:
            status = "SHALLOW"
        else:
            status = "STRONG"
            
        return {"status": status, "reason": reason}
    except Exception as e:
        logger.error(f"Error calling Gemini API for evaluation: {e}")
        return {"status": "STRONG", "reason": f"API fallback evaluation due to connection error: {str(e)}"}


def generate_follow_up_question(topic: Dict[str, Any], candidate: Candidate, previous_question: str, previous_answer: str) -> str:
    """Generates a deeper, conversational follow-up question on the current topic. Resilient to failures."""
    if not GEMINI_API_KEY or not gemini_model:
        return f"Could you elaborate further on your experience with {topic['title']}?"

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
        f"Candidate's Answer: {previous_answer}\n\n"
        f"Please generate ONE targeted, conversational, deeper follow-up question about this topic. The question should probe their understanding further, address the gaps in their response, or ask them to elaborate on details."
    )

    try:
        response = gemini_model.generate_content(follow_up_prompt)
        return response.text.strip()
    except Exception as e:
        logger.error(f"Error calling Gemini API for follow-up question: {e}")
        return f"Could you elaborate further on your previous point regarding {topic['title']}?"


def generate_hint(topic: Dict[str, Any], question: str) -> str:
    """Uses Google's Gemini API to generate a helpful hint for the current question without giving away the answer."""
    if not GEMINI_API_KEY:
        return f"Hint: Think about how you would implement or use {', '.join(topic.get('tools', []))} for this topic."

    prompt = (
        f"Topic: {topic['title']}\n"
        f"Objectives: {', '.join(topic.get('objectives', []))}\n"
        f"Question Asked: {question}\n\n"
        f"The candidate is stuck and needs a hint. Generate a helpful, encouraging hint or nudge in the right direction. "
        f"Do NOT give away the direct answer. Keep it to 1-2 concise sentences."
    )
    try:
        model = genai.GenerativeModel(
            model_name="gemini-3.1-flash-lite",
            system_instruction="You are a helpful, encouraging technical interviewer. Provide a subtle hint to help the candidate answer the question without giving it away."
        )
        response = model.generate_content(prompt)
        return response.text.strip()
    except Exception as e:
        logger.error(f"Error generating hint from Gemini: {e}")
        return f"Hint: Think about how you would implement or use {', '.join(topic.get('tools', []))} for this topic."


def generate_dynamic_feedback(session_state: Dict[str, Any]) -> Feedback:
    """Uses Google's Gemini API to generate genuinely candidate-specific feedback based on the full transcript. Resilient to failures."""
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
            f"Evaluation: {ans['evaluation']['status']} (Reason: {ans['evaluation']['reason']})\n"
        )
    
    transcript_str = "\n".join(transcript_lines)
    
    # Print transcript details to the terminal console
    print("\n" + "=" * 20 + " TRANSCRIPT SENT TO GEMINI " + "=" * 20)
    print(f"Candidate: {candidate.member.name} ({candidate.member.jobRole})")
    print(f"Total Technical Answers: {len(answers)}")
    print("Transcript Content:")
    print(transcript_str if transcript_str else "(No technical answers recorded)")
    print("=" * 67 + "\n")

    # Bypass Gemini call if no technical answers were collected
    if len(answers) == 0:
        logger.info("Bypassing Gemini call: Candidate ended the session before any technical questions could be answered.")
        summary_txt = f"The interview was ended early by candidate {candidate.member.name} before any technical assessment could occur."
        return Feedback(
            summary=summary_txt,
            strengths=["Insufficient responses to assess strengths."],
            gaps=["Insufficient responses to assess gaps."],
            next=["Retake the technical interview and complete the curriculum topics."]
        )
    
    user_prompt = (
        f"Candidate Name: {candidate.member.name}\n"
        f"Job Role: {candidate.member.jobRole}\n"
        f"Years of Experience: {candidate.member.yearsExperience}\n\n"
        f"Interview Transcript:\n"
        f"{transcript_str}\n\n"
        f"Please generate the feedback JSON matching the requested keys."
    )
    
    if session_state.get("ended_early"):
        user_prompt += "\nIMPORTANT: The candidate chose to end the interview early. Please explicitly state that they chose to exit early in the feedback summary."

    text_response = ""
    if GEMINI_API_KEY:
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
                
            # Scrub trailing commas from JSON lists/objects (Gemini sometimes adds them)
            text_response = re.sub(r',\s*([\]}])', r'\1', text_response)
                
            feedback_data = json.loads(text_response)
            
            return Feedback(
                summary=feedback_data.get("summary", ""),
                strengths=feedback_data.get("strengths", []),
                gaps=feedback_data.get("gaps", []),
                next=feedback_data.get("next", [])
            )
        except Exception as e:
            logger.error(f"Error parsing Gemini feedback response: {e}. Raw: {text_response}")
            
    # Fallback response generator (used if API key is missing or call fails)
    strong_topics = list(set([a["title"] for a in answers if a["evaluation"]["status"] == "STRONG"]))
    shallow_topics = list(set([a["title"] for a in answers if a["evaluation"]["status"] == "SHALLOW"]))
    wrong_topics = list(set([a["title"] for a in answers if a["evaluation"]["status"] == "WRONG"]))
    
    summary_txt = f"The candidate {candidate.member.name} completed the interview."
    if session_state.get("ended_early"):
        summary_txt = f"The interview was ended early by candidate {candidate.member.name}."
        
    gaps_list = []
    for t in wrong_topics:
        # Find the wrong answer details in our logs
        wrong_ans = next((a for a in answers if a["title"] == t and a["evaluation"]["status"] == "WRONG"), None)
        details = f": '{wrong_ans['answer']}' (Factually incorrect)" if wrong_ans else ""
        gaps_list.append(f"Demonstrated factually WRONG understanding of {t}{details}.")
    for t in shallow_topics:
        gaps_list.append(f"Demonstrated shallower understanding of: {t}.")
        
    return Feedback(
        summary=summary_txt,
        strengths=[f"Demonstrated good baseline participation on: {t}" for t in strong_topics] if strong_topics else ["Walked through the technical topics."],
        gaps=gaps_list if gaps_list else ["No major gaps flagged."],
        next=[f"Deep dive into: {t}" for t in (shallow_topics + wrong_topics)] if (shallow_topics + wrong_topics) else ["Ready for review."]
    )


def is_exit_intent(message: str) -> bool:
    """Helper to detect if a candidate's message expresses an intent to quit/end the interview."""
    if not message:
        return False
    msg_lower = message.lower()
    exit_phrases = ["quit", "end interview", "stop", "leave", "exit", "end the interview", "stop the interview", "i want to quit", "i want to leave"]
    for phrase in exit_phrases:
        if phrase in msg_lower:
            return True
    return False


@app.post("/api/interview", response_model=InterviewResponse)
def handle_interview_turn(request: InterviewRequest):
    # EDGE CASE 1: Missing or malformed sessionId
    session_id = request.sessionId.strip() if request.sessionId else ""
    if not session_id:
        return InterviewResponse(
            reply="Error: Invalid or missing sessionId.",
            done=True
        )

    # 1. New Session (candidate details provided)
    if request.candidate is not None:
        if session_id in sessions:
            logger.warning(f"Session {session_id} already exists. Resetting session state for a new interview.")
            
        # Perform topic selection
        selected_topics = select_interview_topics(request.candidate.missions, curriculum_days)
        
        # Initialize session state
        sessions[session_id] = {
            "candidate": request.candidate,
            "topic_queue": selected_topics,
            "current_index": 0,
            "current_question_asked": None,
            "follow_up_asked": False,
            "answers_collected": [],
            "warmup_done": False,
            "ended_early": False
        }

        # Print/log the selected topics clearly
        print(f"\n[SESSION START] sessionId: {session_id}")
        print(f"Candidate: {request.candidate.member.name} ({request.candidate.member.jobRole})")
        print(f"Selected Topics ({len(selected_topics)}):")
        for idx, t in enumerate(selected_topics):
            status_desc = "SKIPPED" if t["skipped"] else f"PASSED ({t['attempts']} attempts)"
            print(f"  [{idx + 1}] Day {t['day']}: {t['title']} - {status_desc}")
        print("-" * 50 + "\n")

        logger.info(f"Session {session_id} initialized with {len(selected_topics)} topics.")

        first_topic = selected_topics[0]
        progress_info = InterviewProgress(
            current=1,
            total=len(selected_topics),
            day=first_topic["day"],
            topicTitle=first_topic["title"]
        )

        return InterviewResponse(
            reply=f"Welcome, {request.candidate.member.name}. Let's begin your interview.",
            done=False,
            progress=progress_info,
            answerQuality=None
        )

    # 2. Existing Session
    # EDGE CASE 2: Unknown sessionId
    if session_id not in sessions:
        return InterviewResponse(
            reply="Error: Unknown sessionId. Please start the interview session by providing candidate details first.",
            done=True
        )

    session_state = sessions[session_id]
    user_msg = request.message.strip() if request.message else ""
    topic_queue = session_state["topic_queue"]
    current_idx = session_state["current_index"]
    candidate = session_state["candidate"]

    # GRACEFUL EXIT: Check for quit intent in message
    if is_exit_intent(user_msg) or user_msg.lower() == "end":
        logger.info(f"Session {session_id} ended early via exit intent.")
        session_state["ended_early"] = True
        return generate_completion_response(session_state, answer_quality=None)

    # HINTS ON DELAY: check for explicit request or delay > 60 seconds
    is_hint_requested = request.requestHint is True or (request.elapsedTime is not None and request.elapsedTime > 60)
    if is_hint_requested and session_state["current_question_asked"] is not None:
        logger.info(f"Generating hint for session {session_id}.")
        current_topic = topic_queue[current_idx]
        hint = generate_hint(current_topic, session_state["current_question_asked"])
        
        progress_info = InterviewProgress(
            current=current_idx + 1,
            total=len(topic_queue),
            day=current_topic["day"],
            topicTitle=current_topic["title"]
        )
        return InterviewResponse(
            reply=hint,
            done=False,
            progress=progress_info,
            answerQuality=None
        )

    # FRIENDLY OPENING: Ask friendly warm-up before entering technical topics
    if not session_state.get("warmup_done"):
        if session_state["current_question_asked"] is None:
            # Ask the friendly warm-up question
            question = f"To start off, could you tell me a bit about your background and what you enjoyed most in the cohort?"
            session_state["current_question_asked"] = question
            
            current_topic = topic_queue[current_idx]
            progress_info = InterviewProgress(
                current=1,
                total=len(topic_queue),
                day=current_topic["day"],
                topicTitle=current_topic["title"]
            )
            return InterviewResponse(
                reply=question,
                done=False,
                progress=progress_info,
                answerQuality=None
            )
        else:
            # Candidate answered the warm-up question.
            # Record it (not graded) and transition to technical topic 1
            session_state["warmup_answer"] = user_msg
            session_state["warmup_done"] = True
            
            current_topic = topic_queue[current_idx]
            question = generate_interview_question(current_topic, candidate)
            session_state["current_question_asked"] = question
            session_state["follow_up_asked"] = False
            logger.info(f"Asked primary question for session {session_id}, Day {current_topic['day']}: {question[:60]}...")
            
            progress_info = InterviewProgress(
                current=current_idx + 1,
                total=len(topic_queue),
                day=current_topic["day"],
                topicTitle=current_topic["title"]
            )
            return InterviewResponse(
                reply=question,
                done=False,
                progress=progress_info,
                answerQuality=None
            )

    # Standard technical Q&A loop
    if session_state["current_question_asked"] is None:
        if current_idx < len(topic_queue):
            current_topic = topic_queue[current_idx]
            question = generate_interview_question(current_topic, candidate)
            session_state["current_question_asked"] = question
            session_state["follow_up_asked"] = False
            logger.info(f"Asked primary question for session {session_id}, Day {current_topic['day']}: {question[:60]}...")
            
            progress_info = InterviewProgress(
                current=current_idx + 1,
                total=len(topic_queue),
                day=current_topic["day"],
                topicTitle=current_topic["title"]
            )
            return InterviewResponse(
                reply=question,
                done=False,
                progress=progress_info,
                answerQuality=None
            )
        else:
            return generate_completion_response(session_state, answer_quality=None)
            
    else:
        current_topic = topic_queue[current_idx]
        asked_question = session_state["current_question_asked"]
        
        # EDGE CASE 4: Empty or whitespace-only answer messages
        if not user_msg:
            logger.info(f"Session {session_id}, Day {current_topic['day']}: Received empty or whitespace-only answer.")
            eval_result = {
                "status": "SHALLOW",
                "reason": "Candidate provided an empty or whitespace-only response."
            }
        else:
            # Evaluate the answer (includes nonsense/gibberish filter inside)
            eval_result = evaluate_candidate_answer(current_topic, asked_question, user_msg)
            
        status = eval_result["status"] # STRONG, SHALLOW, or WRONG
        reason = eval_result["reason"]
        
        logger.info(f"Evaluation for session {session_id}, Day {current_topic['day']}: {status}. Reason: {reason}")
        
        # Record the transaction
        is_follow_up = session_state.get("follow_up_asked", False)
        session_state["answers_collected"].append({
            "day": current_topic["day"],
            "title": current_topic["title"],
            "question": asked_question,
            "answer": user_msg if user_msg else "(No response)",
            "evaluation": {
                "status": status,
                "reason": reason
            },
            "type": "follow_up" if is_follow_up else "primary"
        })
        
        # Decide next step:
        # If shallow/wrong and no follow-up was asked yet on this topic, ask a follow-up
        if status in ["SHALLOW", "WRONG"] and not is_follow_up:
            session_state["follow_up_asked"] = True
            
            # Generate and ask the follow-up question
            follow_up_question = generate_follow_up_question(
                current_topic, candidate, asked_question, user_msg
            )
            session_state["current_question_asked"] = follow_up_question
            
            logger.info(f"Asked follow-up question for session {session_id}, Day {current_topic['day']}: {follow_up_question[:60]}...")
            
            progress_info = InterviewProgress(
                current=current_idx + 1,
                total=len(topic_queue),
                day=current_topic["day"],
                topicTitle=current_topic["title"]
            )
            return InterviewResponse(
                reply=follow_up_question,
                done=False,
                progress=progress_info,
                answerQuality=status.lower()
            )
            
        # If strong or a follow-up has already been asked, move to the next topic in queue
        else:
            session_state["current_index"] += 1
            current_idx = session_state["current_index"]
            session_state["current_question_asked"] = None
            session_state["follow_up_asked"] = False  # Reset for next topic
            
            # Check if queue is completed
            if current_idx >= len(topic_queue):
                logger.info(f"Session {session_id} completed (all topics covered).")
                return generate_completion_response(session_state, answer_quality=status.lower())
                
            # Ask the primary question for the next topic
            next_topic = topic_queue[current_idx]
            question = generate_interview_question(next_topic, candidate)
            session_state["current_question_asked"] = question
            logger.info(f"Asked primary question for session {session_id}, Day {next_topic['day']}: {question[:60]}...")
            
            progress_info = InterviewProgress(
                current=current_idx + 1,
                total=len(topic_queue),
                day=next_topic["day"],
                topicTitle=next_topic["title"]
            )
            return InterviewResponse(
                reply=question,
                done=False,
                progress=progress_info,
                answerQuality=status.lower()
            )


def generate_completion_response(session_state: Dict[str, Any], answer_quality: Optional[str] = None) -> InterviewResponse:
    """Helper to return the final feedback report when the interview finishes."""
    feedback = generate_dynamic_feedback(session_state)
    
    # Compile actual technical topic evaluations list
    topic_evals = []
    for ans in session_state.get("answers_collected", []):
        title = ans["title"]
        status = ans["evaluation"]["status"]
        day = ans["day"]
        
        # Check if already added
        existing = next((x for x in topic_evals if x["title"] == title), None)
        if existing:
            existing["evaluations"].append(status)
        else:
            topic_evals.append({
                "title": title,
                "day": day,
                "evaluations": [status]
            })
            
    return InterviewResponse(
        reply="Interview completed. Thank you for your time!",
        done=True,
        feedback=feedback,
        progress=None,
        answerQuality=answer_quality,
        evaluations=topic_evals
    )


# Serve static data folder
app.mount("/data", StaticFiles(directory=os.path.join(BASE_DIR, "data")), name="data")
app.mount("/static", StaticFiles(directory=os.path.join(BASE_DIR, "src", "static")), name="static")

# Serve index.html SPA
@app.get("/")
def get_index():
    index_path = os.path.join(BASE_DIR, "src", "index.html")
    if os.path.exists(index_path):
        return FileResponse(index_path)
    # Fallback to project root
    root_index = os.path.join(BASE_DIR, "index.html")
    if os.path.exists(root_index):
        return FileResponse(root_index)
    raise HTTPException(status_code=404, detail="index.html not found")
