This is a hackathon project called Interview Agent. My folder structure is:
interview_agent/ (root)
  data/candidates.json
  data/curriculum.json
  data/spec.md
  PROMPTS.md
  README.md

Read data/spec.md carefully — it defines the exact API contract I must follow.

Set up a Python FastAPI backend inside a new src/ folder. Create a single endpoint POST /api/interview that matches the request/response shapes in data/spec.md exactly. For now, just make it return a hardcoded stub response so I can confirm it runs. Add requirements.txt and a README section explaining how to run it locally with uvicorn.


Run the FastAPI server locally, then send a test request to POST /api/interview yourself and show me the response.

## Prompt 2 - Topic selection + session state
Read data/curriculum.json and data/candidates.json.

Implement session state: when a request comes in with a new sessionId and a candidate object, look up that candidate's missions and select 8-10 interview topics from curriculum.json, covering at least 4 different days. Prioritize:
- days the candidate passed with high attempt counts (they struggled, worth probing)
- a couple of skipped days (ask something lighter/foundational)
- a couple of easy first-try passes (confirm real understanding)

Store per-session state (sessionId -> candidate, ordered topic queue, current index, answers collected so far) in an in-memory Python dict. Don't generate question wording yet — just wire up the topic selection logic and print/log the selected topics so I can verify it.

Result: 10 topics selected per candidate, session state tracks queue progression, basic end-of-interview response.

 ## Prompt 3 — real LLM-generated questions

Now connect the Anthropic API (Claude) to generate the actual interview question text, replacing the templated "Let's discuss Day X..." strings.

For the current topic in a session, send the day's title, objectives, and tools from curriculum.json to the LLM with a system prompt establishing it as a senior technical interviewer conducting a real interview. Have it phrase one natural, conversational interview question about that topic — not a template, an actual question a human interviewer would ask. Return this as the "reply" field per data/spec.md's format, with done: false.

Use an environment variable for the Anthropic API key, and add a .env.example file. Make sure the app doesn't crash if the API key is missing — show a clear error instead. 


## changing to gemini api

Switch the LLM calls in this project from the Anthropic API to Google's Gemini API. Use the google-generativeai Python SDK. Replace ANTHROPIC_API_KEY with GEMINI_API_KEY as the environment variable. Use the gemini-1.5-flash model since it's fast and free-tier friendly. Update .env.example and requirements.txt accordingly. Keep the response format (reply, done, feedback) exactly the same as before — only the LLM provider should change.

## Adaptive follow-ups
Now handle the candidate's answer. When a message comes in for an existing session, send the candidate's answer plus the current topic's objectives to the Gemini API and ask it to judge whether the answer is strong/complete or shallow/vague.

- If shallow, generate ONE deeper follow-up question on the same topic (only once per topic, don't loop forever — track whether a follow-up was already asked for that topic in the session state).
- If strong, or a follow-up was already asked, move to the next topic in the queue and generate that question instead.

Store all Q&A pairs (including follow-ups) in the session state so we have a full transcript by the end.

## testing

Run a full simulated interview test for me from start to finish using candidate CAND-002 (Alex Turner) from data/candidates.json. Do this:
1. Start a new session (send the candidate object with a fresh sessionId).
2. Print the full response.
3. Send 3 more turns with realistic sample answers (make up believable technical answers based on the topic asked).
4. Print each response.
5. Send "end" as the final message.
6. Print the final feedback response.

Show me all 5 responses in order so I can verify the full flow works.


## real LLM generated feedback

The current end-of-interview feedback is too generic/templated (e.g. "Demonstrated baseline participation"). Fix this by sending the FULL transcript (every topic, question, follow-up, and the candidate's actual answers) to the Gemini API at the end of the interview, and ask it to generate genuinely specific feedback based on what the candidate actually said — not generic filler.

Return strictly this JSON structure (matching data/spec.md):
{
  "summary": string,
  "strengths": string[],
  "gaps": string[],
  "next": string[]
}

The strengths and gaps should reference specific things the candidate said or topics they handled well/poorly, not boilerplate sentences. Make sure the response is valid JSON even if the Gemini output needs cleanup (e.g. strip markdown code fences before parsing).


## Robustness pass
Review the whole /api/interview flow for remaining edge cases:
- Missing or malformed sessionId
- Unknown sessionId (message sent without a valid prior session)
- A candidate object with fewer than 4 eligible days of missions in candidates.json
- Empty or whitespace-only answer messages
- Gemini API failures beyond rate limits (network errors, malformed responses)
- What happens if the same sessionId is used to "start" a second time (should it reset, or reject?)

Add sensible error handling and clear fallback responses for each case so the endpoint never crashes mid-interview. Keep the response schema from data/spec.md exactly the same — errors should still return valid reply/done/feedback shaped JSON, not raw stack traces or 500 errors where avoidable.

## Backend: expose extra metadata (send this first)
Extend the /api/interview response to include additional optional metadata fields, WITHOUT changing or removing any of the required fields (reply, done, feedback) from data/spec.md. Add:

1. "progress": { "current": N, "total": M, "day": X, "topicTitle": "..." } — showing which topic number out of the total we're on, and the current day/title
2. "answerQuality": "strong" | "shallow" | null — the evaluation result for the most recent answer (null on the very first "start" request since there's no answer yet)

These are additive fields only — the core required response shape must remain fully intact and backward compatible with the spec.