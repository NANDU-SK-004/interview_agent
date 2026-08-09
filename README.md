# SkillHire

SkillHire is an AI-powered technical interview simulator designed to conduct and grade conversational engineering interviews.

## Live Demo URL
🔗 Deployed Live Demo: [https://skillhire.up.railway.app/](https://skillhire.up.railway.app/)

---

## 1. What It Does
SkillHire conducts adaptive technical assessments by matching questions against a candidate's historical cohort performance. The simulator analyzes candidate answers in real-time, routes to deeper follow-ups when responses are vague or shallow, tracks factually incorrect responses, and generates a structured competency mapping report detailing strengths, gaps, and recommendations.

---

## 2. Technical Stack
* **Backend**: FastAPI (Python 3.8+), Uvicorn server
* **AI Model Engine**: Google Gemini API (`gemini-1.5-flash` & `gemini-3.1-flash-lite`) via `google-generativeai` SDK
* **Frontend**: Single-Page App (SPA) built using vanilla HTML5, CSS3, and JavaScript
* **State Management**: Key-isolated in-memory dictionary store

---

## 3. How to Run Locally

### Prerequisites
* Python 3.8+ installed on your system.
* A Google Gemini API key.

### Setup Steps
1. **Clone & Navigate**: Clone the repository and enter the directory.
2. **Install Dependencies**:
   ```bash
   pip install -r requirements.txt
   ```
3. **Environment Variables**:
   Create a `.env` file in the root folder containing your Gemini API key:
   ```text
   GEMINI_API_KEY=your_gemini_api_key_here
   ALLOWED_ORIGINS=http://localhost:8000
   ```
4. **Start local Server**:
   ```bash
   uvicorn src.main:app --reload
   ```
   The application will run locally at [http://127.0.0.1:8000/](http://127.0.0.1:8000/). The interactive API docs will be available at `http://127.0.0.1:8000/docs`.

---

## 4. API Contract Summary
The service complies with the request/response shape contract specified in `data/spec.md`:

### POST `/api/interview`
Handles all session starts, interview turns, hint requests, and early terminations.

* **Start Session Request**:
  ```json
  {
    "sessionId": "session-unique-id",
    "candidate": {
      "member": {
        "id": "CAND-001",
        "name": "Sarah Johnson",
        "jobRole": "Senior Data Engineer",
        "yearsExperience": 9.0,
        "education": "MS Computer Science",
        "status": "COMPLETED"
      }
    }
  }
  ```
* **Conversation Turn / Hint Request**:
  ```json
  {
    "sessionId": "session-unique-id",
    "message": "sentence embedding maps semantic tokens to vectors.",
    "elapsedTime": 14.5,
    "requestHint": false
  }
  ```
* **Turn Response Structure**:
  ```json
  {
    "reply": "Question or feedback text...",
    "done": false,
    "feedback": null,
    "progress": {
      "current": 1,
      "total": 10,
      "day": 7,
      "topicTitle": "Embeddings Explained"
    },
    "answerQuality": "strong"
  }
  ```

---

## 5. Security & Synthetic Data Compliance
* **CORS Exclusions**: Restricts browser origin scripts utilizing the `ALLOWED_ORIGINS` variable.
* **Error Masking**: Wraps route calls inside a global exception interceptor, shielding internal file paths.
* **Synthetic Data Note**: In compliance with hackathon rules, all curriculum timelines, student cohorts, profiles, and performance history metrics inside `data/` are completely synthetic.