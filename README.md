# Interview Agent

This is a hackathon project for an AI-powered Interview Agent.

## Folder Structure

```
interview_agent/
├── data/
│   ├── candidates.json
│   ├── curriculum.json
│   └── spec.md
├── src/
│   └── main.py
├── PROMPTS.md
├── README.md
└── requirements.txt
```

## Backend Setup & Run Instructions

### Prerequisites
- Python 3.8+ installed on your system.

### 1. Install Dependencies
Navigate to the root directory and install the required packages:
```bash
pip install -r requirements.txt
```

### 2. Run the FastAPI Server
Start the Uvicorn development server:
```bash
uvicorn src.main:app --reload
```
The server will start running at `http://127.0.0.1:8000`. You can access the automatic documentation at `http://127.0.0.1:8000/docs`.

## API Spec & Testing

The backend exposes a single endpoint matching the API contract specified in `data/spec.md`:
`POST /api/interview`

### How to test the endpoints

#### 1. Start Interview
Send a POST request containing the `sessionId` and the `candidate` details (representing starting the interview):

**Using curl:**
```bash
curl -X POST "http://127.0.0.1:8000/api/interview" \
     -H "Content-Type: application/json" \
     -d '{"sessionId": "abc-123", "candidate": {"member": {"id": "CAND-001", "name": "Sarah Johnson", "jobRole": "Senior Data Engineer", "yearsExperience": 9, "education": "MS Computer Science", "status": "COMPLETED"}, "missions": [{"day": 7, "title": "Embeddings Explained", "passed": true, "attempts": 1}], "signals": {"commitDays": 28, "missionsCompleted": 30, "missionsFirstTry": 20}}}'
```

#### 2. Conversation Turn
Send a POST request containing the `sessionId` and the candidate's latest response:

**Using curl:**
```bash
curl -X POST "http://127.0.0.1:8000/api/interview" \
     -H "Content-Type: application/json" \
     -d '{"sessionId": "abc-123", "message": "Hi, I am ready."}'
```

#### 3. End Interview
To trigger the interview completion and receive the final feedback report, send the message `"end"`:

**Using curl:**
```bash
curl -X POST "http://127.0.0.1:8000/api/interview" \
     -H "Content-Type: application/json" \
     -d '{"sessionId": "abc-123", "message": "end"}'
```