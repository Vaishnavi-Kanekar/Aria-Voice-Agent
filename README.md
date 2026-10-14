# Aria: AI Voice Support Agent for Aura Skincare

A browser-based voice agent. Click **Start Call**, talk to Aria, hear her reply, and get a transcript plus a structured JSON outcome when you hang up.

- **Live app:** `https://aria-voice-agent-1ezk.onrender.com/`
- **Stack:** React (Vite) + FastAPI + WebSockets. Deepgram Nova-3 (speech-to-text) -> Groq `openai/gpt-oss-120b` (LLM + tools) -> ElevenLabs Flash (text-to-speech).

## What it does

- Natural voice conversation as **Aria**, a short, friendly, professional support specialist.
- Answers from Aura's shipping, return, cancellation and COD policies, and refuses anything outside them.
- Looks up orders with tool calls and handles invalid, missing and ineligible cases.
- Says so when it doesn't know something, instead of guessing.
- Shows the transcript and a JSON summary after the call.

## Run locally

Needs Python 3.10+, Node 18+ and three API keys: [Groq](https://console.groq.com/keys), [Deepgram](https://console.deepgram.com), [ElevenLabs](https://elevenlabs.io).

```bash
# Backend
cd backend
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env        # fill in the three keys
uvicorn app.main:app --port 8000

# Frontend (new terminal)
cd frontend
npm install
npm run dev                 # open http://localhost:5173
```

Single-link mode: run `npm run build` in `frontend/` and FastAPI serves the built app at `http://localhost:8000`. The microphone only works on `localhost` or **HTTPS**.

## How it works

```
Browser mic -> 16 kHz audio -> FastAPI WebSocket -> Deepgram (live speech-to-text)
                                                        | customer finished a sentence
                                                        v
                                         Groq LLM  --- get_order_details
                                                   --- cancel_order
                                                   --- escalate_to_human
                                                        | sentence by sentence
                                                        v
Browser speaker <- audio stream <-------- ElevenLabs (text-to-speech)
```

- **Streaming everywhere.** The reply is split into sentences as the LLM writes it, and each sentence goes to the voice immediately. The UI shows "ms to first audio" for each reply.
- **Tool use.** The model decides when to call a tool. `get_order_details` is for any question about a specific order. `cancel_order` runs only after the customer confirms. `escalate_to_human` logs a ticket for things Aria can't handle, such as damaged items.
- **Guardrails.** The system prompt holds the brand facts and rules (no promises outside policy, Aura topics only, ask when unsure).
- **Graceful degradation.** Low speech-recognition confidence makes Aria ask the customer to repeat, without calling the LLM. Unknown, missing or invalid order IDs return a clear tool error that Aria relays. If the LLM or voice service fails, she apologises out loud instead of crashing.
- **Summary.** After the call, a forced tool call returns JSON with `customer_intent`, `order_id`, `resolution_status`, `call_summary`, `actions_taken` and `follow_up_required`, based on the real tool log. A code-only fallback is used if the LLM call fails.

## Beyond the basics

- **Barge-in:** two or more recognised words while Aria speaks cancels her reply and clears the audio. One-word replies like "okay" are ignored.
- **Three tools** instead of one.
- **Ambiguity handling:** asks for a missing order ID, reads back unclear IDs digit by digit, and normalises spoken IDs ("ord one oh one" -> `ORD-101`).
- **Call experience:** order helper panel, live state indicator, latency readout, and a "are you still there?" prompt after 25 seconds of silence.

## Test it

Use the order panel on the page.

| Say | Expected |
|---|---|
| "Where is my order ORD-101?" | Out for delivery with BlueDart, by 6 PM today |
| "Can I cancel ORD-101?" | No, it is out for delivery; she may mention refusing at the doorstep |
| "Cancel ORD-103" | Asks to confirm, then cancels |
| "Return ORD-102" | Declined: delivered 14 days ago, outside the 7-day window |
| "I bought this 20 days ago and opened it, can I return it?" | Politely declined per policy |
| "What about ORD-555?" | Can't find it, asks to repeat or verify |
| "Where is my order?" | Asks for the order ID |
| "Can you book me a flight to Goa?" | Only helps with Aura Skincare |

## How I think

**1. Why this architecture and stack?**
I chose a modular pipeline (speech-to-text, then an LLM with tools, then text-to-speech) because I can see exactly what each stage is doing, which makes the system easy to debug and to improve one part at a time. Deepgram gives fast live transcripts with sentence-end detection, Groq gives very quick LLM replies with reliable tool calling, and ElevenLabs Flash gives a natural voice with low delay. FastAPI with one WebSocket per call keeps streaming simple, and React provides the testing page.

**2. Hardest part and how I solved it.**
Turn-taking: making the conversation feel natural. Customers interrupt, say "okay" while the agent talks, mumble, and the speaker can leak into the mic. I handled this by streaming sentence by sentence, treating two or more recognised words as a barge-in while ignoring one-word replies, cancelling the LLM and voice cleanly on barge-in, and asking the customer to repeat when speech confidence is low.

**3. One more week: what first?**
First, I would make the agent multi-brand. Each brand's name, policies, persona, voice and orders would move into a config file, and the brand would be chosen by URL (for example `/?brand=aura`), so adding a brand takes only ten to fifteen lines of configuration. Second, I would improve Hinglish, since customers often mix Hindi and English, by testing the speech recognition and voice on real Hinglish calls and tuning them. Third, I would improve how the agent behaves when speech confidence is low, so it asks smarter clarifying questions instead of only asking the customer to repeat.

**4. At 1,000 conversations a day.**
I would analyze conversation patterns and handle frequently asked questions using predefined responses or caching to reduce unnecessary LLM calls, cost, and latency. I would optimize the database and add monitoring and error handling to ensure the agent remains reliable as usage grows.