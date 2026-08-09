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


## Frontend: single-page HTML/JS chat interface
Build a single-page HTML/JS frontend (one file, no build step) for the Interview Agent. Requirements:

FUNCTIONALITY:
1. On load, fetch candidates from data/candidates.json and show them in a selectable list (name + jobRole).
2. "Start Interview" button sends the selected candidate to /api/interview with a generated sessionId.
3. Chat interface: agent questions on one side, my typed answers on the other, auto-scrolling.
4. Text input + send button for answers, calling /api/interview with the same sessionId each turn.
5. Progress bar at the top showing "Topic {progress.current}/{progress.total}: Day {progress.day} - {progress.topicTitle}" using the progress field from the API.
6. After each of my answers, briefly show a small tag near my message using answerQuality: "✓ Strong answer" (green) or "→ Follow-up incoming" (amber), then transition to the agent's next message.
7. Typing animation: while waiting for the agent's response, show an animated "..." typing indicator bubble before the real message appears. When the question arrives, reveal it with a smooth fade/slide-in effect (keep it lightweight, don't overdo character-by-character rendering).
8. When done: true arrives, hide the chat input and show:
   a. The feedback (summary, strengths, gaps, next) as a clean formatted report
   b. A simple radar or bar chart (use plain SVG or Canvas, no external chart library) visualizing performance across the topics/days covered — derive rough per-topic scores from how many topics had answerQuality "strong" vs "shallow" during the session
   c. A "Download Report as PDF" button that generates a clean printable PDF of the feedback (use window.print() with print-specific CSS, or a lightweight approach — no heavy PDF library needed)

DESIGN — use this exact color palette:
- #031716 - darkest background (page background)
- #032F30 - secondary dark surface (sidebar/header)
- #0A7075 - primary accent (buttons, active states, progress bar fill)
- #0C969C - secondary accent (agent message bubbles, chart highlights)
- #6BA3BE - muted light accent (candidate answer bubbles, borders)
- #274D60 - mid-tone (cards, containers)

Clean modern sans-serif font, generous spacing, rounded corners on chat bubbles/cards, subtle shadows and smooth transitions. One HTML file with embedded CSS/JS, no external frameworks required except vanilla fetch() calls to the backend.

## Fix several issues in the interview evaluation and flow logic:

1. EVALUATION BUG: Gibberish/nonsense answers (e.g. "ccc", "asdf") are incorrectly being classified as "strong". Fix the evaluation prompt sent to Gemini so it explicitly checks whether the answer contains genuine, relevant technical content. Gibberish, off-topic, or empty-of-substance answers must be classified as "shallow" and trigger a follow-up.

2. WRONG ANSWERS SHOULD SHOW AS GAPS: Currently, even factually incorrect technical answers can end up looking fine in the final feedback. The evaluation must distinguish between "shallow" (vague/incomplete) and factually WRONG answers. Track this distinction in session state, and make sure the final feedback report explicitly and precisely calls out factually incorrect answers as gaps — not just vague ones. Feedback should be clear and precise about WHAT was wrong, not generic.

3. FRIENDLY OPENING: The interview should start with 1-2 light, friendly, non-technical questions first (e.g. "Tell me a bit about your background and what you enjoyed most in the cohort") before transitioning into the technical curriculum questions. Only after this warm-up should it move into the Day-by-Day technical topics.

4. HINTS ON DELAY: If the candidate takes unusually long to respond (implement this as: if the frontend reports elapsed time since the question was shown, pass that to the backend), OR if requested via a "hint" flag from the frontend, generate a helpful hint related to the current question (not the answer itself, just a nudge in the right direction) instead of leaving them stuck. Add support for this via an optional "requestHint": true field in the request.

5. GRACEFUL EXIT: Detect when a candidate's message expresses intent to quit/end the interview (phrases like "quit", "end interview", "stop", "I want to leave"). When detected, immediately end the session gracefully: return done: true with feedback generated from the transcript so far (even if only 1-2 topics were covered), clearly noting in the summary that the interview was ended early. Do not evaluate the exit message itself as a technical answer.

After implementing all of this, test and show me results for:
- A nonsense answer ("ccc") → should be shallow
- A confidently WRONG technical answer → should show as a specific gap in feedback, not pass as strong
- The interview opening — confirm it starts friendly before going technical
- Typing "I want to end the interview" mid-session → should exit gracefully with partial feedback
- A hint request on a question → should return a helpful nudge, not the answer

Rebrand the frontend from "Assessment Hub" to "SkillHire".

SPLASH SCREEN (new):
1. On page load, show a full-screen splash first: the SkillHire logo (located at src/static/logo.png — check the actual path used in this project and adjust if needed) centered on the dark background (#031716).
2. Animate it: start blurred and slightly scaled down/faded, then smoothly transition to sharp, full-opacity, normal scale over about 1-1.5 seconds.
3. Since the logo has a white background, either:
   a. Make the white background transparent (treat pure white as transparent if possible), OR
   b. Place the logo inside a soft rounded white/light card with padding so it doesn't look like a floating white box on the dark page.
4. After the animation completes, hold briefly (~0.5s) then smoothly transition/fade into the candidate selection screen (the existing candidate list UI).
5. This splash should only show once per page load, not on every screen transition after that.

## Frontend issues resolving


REBRAND:
1. Replace "Assessment Hub" text and the "IA" icon with the SkillHire logo (small version) in the header/sidebar, consistent with the splash branding.
2. Update any other visible "Assessment Hub" references to "SkillHire".

OTHER UI ADDITIONS:
1. Small "End Interview" button/link near the chat input for graceful early exit — wire it to the exit handling already implemented in the backend.
2. Small "Need a hint?" button near the input, sends requestHint: true, displays the hint as a distinct message bubble (dashed border, labeled "💡 Hint").
3. Make sure "wrong" answerQuality renders with a distinct red "✗ Incorrect answer" tag, separate from the amber "→ Follow-up incoming" (shallow) tag.
4. Keep the progress bar, chat bubbles, typing animation, radar chart, and PDF export exactly as-is — this is a polish/rebrand pass, not a redesign.

Keep everything minimal, clean, and consistent with the existing dark teal color palette (#031716, #032F30, #0A7075, #0C969C, #6BA3BE, #274D60).


There's a critical bug: when a candidate says "I want to leave" or similar exit phrases immediately at the start of the interview (before answering ANY real technical question), the final feedback report is still generating detailed, specific technical strengths/gaps (e.g. about LSM-trees, concurrency control) that have NO basis in the actual transcript — this looks like Gemini is hallucinating content instead of acknowledging an empty/near-empty transcript.

Fix this by:
1. Before generating final feedback, check the actual transcript length. If zero or near-zero real technical answers were given (e.g. only the friendly opening was answered, or nothing at all), do NOT call Gemini to generate detailed strengths/gaps about specific technical topics.
2. In this case, return an honest, minimal feedback object instead: summary should clearly state the interview was ended before any meaningful technical assessment could occur, strengths and gaps should be empty arrays or contain a single honest note like "Insufficient responses to assess", and next should suggest retaking the interview.
3. Also double check: is session state being properly isolated per sessionId, or could a new session accidentally reuse/leak data from a previous session? Verify and fix if there's any cross-contamination.
4. Show me the exact transcript data being sent to Gemini for feedback generation in this early-exit scenario so I can confirm what's actually happening.

Test this exact scenario: start a new session, immediately send "I want to leave" as the very first message (before answering the friendly opening question), and show me the resulting feedback response.



Switch the entire frontend UI from the current dark theme to a light, plain, professional theme — think clean enterprise SaaS, not glassy/dark. Remove any glassmorphism, heavy shadows, or glow effects — keep it flat and minimal.

New color palette:
- Background: #FFFFFF (main), #F7F9FA (page background if needed)
- Card/sidebar surface: #F1F4F6
- Primary text: #0F1B24
- Secondary/muted text: #5B6B76
- Primary accent (buttons, active states, progress bar fill): #0A7075
- Secondary accent (agent message bubbles): #0C969C (use as a light tint background, e.g. #0C969C at 10-15% opacity, with dark text — not solid teal with white text, for readability on light backgrounds)
- Candidate answer bubbles: light gray #E9EDEF with dark text
- Borders/dividers: #E1E6E9
- Strong answer tag: green #1B8A5A
- Shallow/follow-up tag: amber #B8860B
- Wrong answer tag: red #C0392B

Requirements:
1. Update all existing screens (splash, candidate selection, chat interface, feedback report, radar chart) to this light theme.
2. Chat bubbles should use subtle background tints (not solid saturated color) so text stays easily readable — dark text on light bubble backgrounds throughout.
3. Buttons: solid #0A7075 background with white text for primary actions; outline/ghost style for secondary actions (hint, end interview).
4. Keep borders and dividers subtle (#E1E6E9), flat design — minimal shadow, no blur/glass effects.
5. The SkillHire logo should now sit on a light background — if it was previously placed on a dark card, adjust so it displays cleanly on white/light gray without needing a special contrast card.
6. Keep all existing functionality exactly as-is (progress bar, quality tags, typing animation, radar chart, PDF export, hint button, end interview button) — this is a visual theme change only, not a functional change.

Show me a screenshot or description of the candidate list screen and the feedback report screen after the change so I can review.



The written feedback report (summary/strengths/gaps/next) is accurate and correctly reflects the transcript. However, the Curriculum Competency Mapping chart is completely disconnected from reality:
- It's missing "Embeddings Explained" even though it was the first topic covered
- It shows "Vector Databases Overview" at 100% even though the candidate gave a factually WRONG answer there
- It shows "Prompt Engineering Fundamentals" at 100% even though that topic was never discussed in this session

Find where the chart's per-topic scores are calculated and show me that code. It's clearly not using the same answerQuality/transcript data that the written feedback correctly uses. Fix the chart to:
1. Only show topics that were actually covered in this session's transcript
2. Score each topic based on its actual answerQuality: strong = high score (e.g. 85-100%), shallow = medium score (e.g. 40-60%), wrong = low score (e.g. 10-25%)
3. Pull topic names directly from the same session data the feedback text generator uses, not a separate/stale source

Also fix the PDF export header to say "SkillHire" only, not "SkillHire - Candidate Assessment Hub" — match the current rebrand everywhere including this template.

After fixing, re-run the same test session data (or a new test) and show me the corrected chart alongside the written feedback so I can confirm they now agree with each other.


## Prompt — Security & exposure audit
Act as a security reviewer. Audit this entire codebase and git history for anything that should NOT be exposed in a PUBLIC GitHub repository or a public live demo. Specifically check for:

1. API KEYS / SECRETS:
   - Is GEMINI_API_KEY (or any other key) hardcoded anywhere in the source code instead of read from environment variables?
   - Is .env actually listed in .gitignore, and has it ever been committed in git history (check git log for it, not just the current state)?
   - Does .env.example contain a real key by mistake, or just a placeholder?

2. DEBUG/INTERNAL INFO LEAKAGE:
   - Do any error responses return raw stack traces, internal file paths, or Python exception details to the client instead of clean error messages?
   - Is DEBUG mode or verbose logging enabled in a way that would expose internals in production?
   - Are there any print()/console.log() statements left in that dump sensitive session data, candidate PII, or internal state to logs that might be publicly visible (e.g. Render's log viewer)?

3. CORS / ACCESS CONTROL:
   - Is CORS configured too permissively (e.g. allow_origins=["*"]) in a way that's riskier than necessary?
   - Is there any endpoint besides /api/interview that's unintentionally exposed (e.g. a debug/test route left in)?

4. DATA EXPOSURE:
   - Does any response ever leak full session state, other candidates' data, or internal server config that a client shouldn't see?
   - Is candidates.json or curriculum.json served in a way that exposes more than intended (e.g. a raw static file listing)?

5. GIT HISTORY:
   - Search the full git history (not just current files) for any commit that ever included a real API key, even if it was later removed — this matters because it's still recoverable from history even after deletion.

Report EVERY finding clearly, even minor ones, and tell me exactly what to fix for each one before I deploy and make this repo public.

## Apply these security fixes from the audit:

1. Remove the static file mount that exposes the raw candidates.json file publicly:
   app.mount("/data", StaticFiles(directory=os.path.join(BASE_DIR, "data")), name="data")
   Replace it with a proper FastAPI route GET /api/candidates that returns ONLY safe public fields (id, name, jobRole) — not attempt counts, skipped days, or any struggle/performance data. Update the frontend to fetch candidates from this new /api/candidates endpoint instead of directly reading data/candidates.json.

2. Wrap the entire /api/interview endpoint logic in a try/except that catches any unhandled exception, logs it server-side, and returns a clean generic error response (reply: "An internal server error occurred", done: true) instead of ever leaking a raw stack trace or file path to the client.

3. Update CORS to read allowed origins from an environment variable (ALLOWED_ORIGINS) instead of allowing "*", defaulting to http://localhost:8000 for local dev. I'll set the real production origin once deployed.

4. Update .gitignore to include: __pycache__/, *.pyc, *.pyo, *.pyd, .pytest_cache/, .vscode/, .idea/ (in addition to the existing .env entry).

After making these changes, confirm:
- The frontend still works correctly using the new /api/candidates endpoint (no functionality broken)
- Deliberately trigger an unhandled error and confirm it returns a clean message, not a stack trace
- Show me the final CORS config



Add a lightweight entry screen before the candidate selection/interview flow. This is a simple "candidate portal entry," NOT a real authentication system — keep it minimal.

REQUIREMENTS:
1. Before the existing candidate list screen, add a new entry screen (after the splash screen finishes, if the splash still exists) with:
   - A "Your Name" text input
   - A "Candidate ID" text input (e.g. CAND-001)
   - An "Enter" button

2. On submit, call the existing GET /api/candidates endpoint (or a similar lookup) to check if the entered Candidate ID exists in candidates.json.
   - If it matches: greet the person using the name they typed (e.g. "Welcome, Sarah!") and proceed directly into the interview flow for that matched candidate — skip the old candidate-list-browsing screen entirely, since they've now identified themselves.
   - If it doesn't match: show a simple, friendly inline error like "Candidate ID not found. Please check and try again." — no complex retry logic, no lockouts, no password-style security theater.

3. Keep this extremely simple — no session tokens, no persistent login, no password field. This is just a lightweight identity lookup, since real authentication is explicitly out of scope for this project.

4. Preserve everything else exactly as-is: the splash screen animation, the light theme, progress bar, quality tags, typing animation, radar chart, PDF export, hint button, and end interview button. This is purely adding one new entry screen before the interview begins.

5. Use the existing color palette (#FFFFFF, #F7F9FA, #F1F4F6, #0F1B24, #5B6B76, #0A7075, #0C969C, #E9EDEF, #E1E6E9) and keep the same clean, professional, flat visual style as the rest of the app.

After implementing, test:
- Entering a valid name + a real Candidate ID (e.g. CAND-001) → should greet by name and start that candidate's interview
- Entering an invalid/nonexistent Candidate ID → should show the friendly error, not crash
- Confirm the rest of the interview flow (questions, evaluation, feedback, chart, PDF) still works exactly as before

Show me a screenshot of the new entry screen once built.