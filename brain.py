"""
brain.py — DEUS 3.0 LLM Abstraction Layer
==========================================
Primary: Groq (fast, free, reliable)
Backup:  Google Gemini (when Groq fails)

Both are optional — if GROQ_API_KEY is missing, only Gemini works.
If GEMINI_API_KEY is missing, only Groq works.
If neither is set, all LLM calls return error messages.

Uses google-genai (modern package), NOT google-generativeai (deprecated).
"""

import os
import time
import json
import importlib
import logging
from dotenv import load_dotenv

from rules_engine import get_rules_context

if not os.getenv("GROQ_API_KEY"):
    load_dotenv()

logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
logger = logging.getLogger(__name__)

GROQ_API_KEY = os.getenv("GROQ_API_KEY", "").strip()
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "").strip()

groq_client = None
gemini_client = None

# --- Groq init (optional) ---
if GROQ_API_KEY:
    try:
        from groq import Groq
        groq_client = Groq(api_key=GROQ_API_KEY)
        logger.info("Groq client initialized.")
    except Exception as e:
        logger.warning("Groq init failed: %s", e)

# --- Gemini init (optional) ---
if GEMINI_API_KEY:
    try:
        from google import genai
        gemini_client = genai.Client(api_key=GEMINI_API_KEY)
        logger.info("Gemini client initialized.")
    except Exception as e:
        logger.warning("Gemini init failed: %s", e)

if not groq_client and not gemini_client:
    logger.warning("No LLM configured. Set GROQ_API_KEY or GEMINI_API_KEY in .env")

# --- Model lists (current as of July 2026) ---
GROQ_MODELS = [
    "llama-3.3-70b-versatile",
    "llama-3.1-8b-instant",
    "gemma2-9b-it",
]

GEMINI_MODELS = [
    "gemini-2.0-flash",
    "gemini-2.0-flash-lite",
]


def ask_groq(prompt: str) -> str:
    """Primary brain — Groq is fast, reliable, free.
    Tries every available model until one works."""
    if not groq_client:
        return "[ERROR: GROQ_API_KEY not configured. Set it in .env]"

    for model in GROQ_MODELS:
        try:
            response = groq_client.chat.completions.create(
                model=model,
                messages=[{"role": "user", "content": prompt}],
                max_tokens=2048,
            )
            return response.choices[0].message.content.strip()
        except Exception as e:
            error = str(e).lower()
            if "decommissioned" in error or "not found" in error or "invalid" in error:
                logger.warning("Groq: %s unavailable, trying next...", model)
                continue
            elif "rate" in error or "quota" in error:
                logger.warning("Groq: rate limit on %s, waiting 10s then retrying...", model)
                time.sleep(10)
                try:
                    response = groq_client.chat.completions.create(
                        model=model,
                        messages=[{"role": "user", "content": prompt}],
                        max_tokens=2048,
                    )
                    return response.choices[0].message.content.strip()
                except Exception:
                    logger.warning("Groq: %s still failing, trying next model...", model)
                    continue
            else:
                logger.warning("Groq error on %s: %s, trying next...", model, e)
                continue

    # All Groq models failed — fall back to Gemini
    logger.warning("All Groq models failed — switching to Gemini backup...")
    return ask_gemini(prompt)


def ask_gemini(prompt: str) -> str:
    """Backup brain — Gemini kicks in when Groq is unavailable.
    Tries every available model until one works."""
    if not gemini_client:
        return "[ERROR: GEMINI_API_KEY not configured. Set it in .env]"

    for model_name in GEMINI_MODELS:
        try:
            time.sleep(4)
            response = gemini_client.models.generate_content(
                model=model_name,
                contents=prompt,
            )
            return response.text.strip()
        except Exception as e:
            error = str(e)
            if "404" in error or "not found" in error.lower():
                logger.warning("Gemini: %s not found, trying next...", model_name)
                continue
            elif "429" in error or "quota" in error.lower() or "rate" in error.lower():
                logger.warning("Gemini: quota hit on %s, waiting 15s then retrying...", model_name)
                time.sleep(15)
                try:
                    response = gemini_client.models.generate_content(
                        model=model_name,
                        contents=prompt,
                    )
                    return response.text.strip()
                except Exception:
                    logger.warning("Gemini: %s still failing, trying next...", model_name)
                    continue
            else:
                logger.warning("Gemini error on %s: %s, trying next...", model_name, e)
                continue

    return "[ERROR: All AI models exhausted. Please check your API keys and model availability.]"


def think(prompt: str) -> str:
    """Main entry point — tries Groq first, falls back to Gemini.
    Injects rules/regulations context from PDF if available."""
    rules = get_rules_context()
    if rules:
        prompt = (
            f"Company rules and regulations that must be followed:\n"
            f"{rules}\n\n"
            f"---\n\n{prompt}"
        )
    return ask_groq(prompt)


def analyze_intent(user_answers: dict) -> str:
    """Send user answers to AI and get back a full system design plan."""
    prompt = f"""
You are DEUS, an expert AI system architect.
A user has completed an onboarding interview.
Based on their answers, design their complete AI agent system.

Be specific about:
1. CEO/Orchestrator agent — name, role, responsibilities
2. Each specialist agent — name, role, which LLM, which tools
3. Tools needed per agent (trading APIs, web search, email, files, etc.)
4. LLM assignment per agent (use only: Groq/Llama3 or Gemini — both free)
5. How agents communicate with each other
6. Special features based on their exact described purpose

User Answers:
{user_answers}

Format your response clearly with sections and bullet points.
Be detailed and tailored exactly to what they described.
"""
    return think(prompt)


def generate_smart_questions(purpose: str) -> list:
    """Generate custom follow-up questions based on user's described purpose."""
    prompt = f"""
You are DEUS, an AI system architect conducting an onboarding interview.
The user described what they want to build:

"{purpose}"

Generate exactly 5 smart follow-up questions specific to their purpose.
Do NOT ask generic questions — make them directly relevant to what they described.

Return ONLY a valid JSON array of 5 strings. No explanation, no markdown.
Example: ["Question 1?", "Question 2?", "Question 3?", "Question 4?", "Question 5?"]
"""
    raw = think(prompt)

    try:
        raw = raw.strip()
        if raw.startswith("```"):
            raw = raw.split("```")[1]
            if raw.startswith("json"):
                raw = raw[4:]
        questions = json.loads(raw.strip())
        if isinstance(questions, list) and len(questions) == 5:
            return questions
        else:
            logger.warning("Parsed JSON was not a 5-item list, using fallback questions.")
    except Exception as e:
        logger.warning("Failed to parse smart questions JSON (%s), using fallback questions.", e)

    return [
        "What is the main goal you want to achieve with this system?",
        "Who will be using this system and how often?",
        "What kind of data or information will the agents work with?",
        "Do you need real-time actions or scheduled tasks?",
        "What does success look like for you with this system?",
    ]


# ===========================================================================
# REPL — Interactive Brain CLI
# ===========================================================================

_AGENT_REGISTRY = {
    "lead_scout": ("lead_scout_agent", "LeadScoutAgent"),
    "outreach": ("outreach_agent", "OutreachAgent"),
    "followup": ("followup_agent", "FollowupAgent"),
    "appointment": ("appointment_agent", "AppointmentAgent"),
    "deal_closer": ("deal_closer_agent", "DealCloserAgent"),
    "report": ("report_agent", "ReportAgent"),
    "system_checker": ("system_checker_agent", "SystemCheckerAgent"),
}

_INTENT_KEYWORDS = {
    "lead_scout": ("scout", "find lead", "search lead", "discover", "find business",
                   "hvac", "contractor", "generate lead"),
    "outreach": ("outreach", "email", "contact", "send message", "reach out"),
    "followup": ("follow", "followup", "re-engage", "reengage"),
    "appointment": ("appointment", "calendly", "book", "schedule", "meeting"),
    "deal_closer": ("deal", "close", "closer"),
    "report": ("report", "summary", "stats", "statistics"),
    "system_checker": ("check", "health", "system status"),
}


def _import_agent(module_name: str, class_name: str):
    try:
        mod = importlib.import_module(module_name)
        return getattr(mod, class_name)
    except Exception as e:
        logger.debug("Failed to import %s.%s: %s", module_name, class_name, e)
        return None


def _detect_intent(text: str) -> str:
    tl = text.lower()
    for intent, keywords in _INTENT_KEYWORDS.items():
        if any(k in tl for k in keywords):
            return intent
    return "general"


def _execute_agent(intent: str, user_input: str) -> str:
    if intent not in _AGENT_REGISTRY:
        return f"No agent registered for '{intent}'."
    mod_name, class_name = _AGENT_REGISTRY[intent]
    cls = _import_agent(mod_name, class_name)
    if cls is None:
        return f"[{class_name}] Agent not available (import failed). Check dependencies."
    try:
        agent = cls()
        result = agent.run(user_input=user_input)
        if hasattr(result, "message"):
            return result.message
        if hasattr(result, "data") and result.data:
            return f"Found {len(result.data)} leads."
        return str(result)
    except TypeError:
        try:
            agent = cls()
            result = agent.run()
            return str(result)
        except Exception as e:
            return f"[{class_name}] Error: {e}"
    except Exception as e:
        return f"[{class_name}] Error: {e}"


SYSTEM_PROMPT = """You are Brain, the central AI assistant for the DEUS 3.0 system.
You have access to the following agents that can be executed on demand:
- lead_scout: Searches for business leads based on a niche/query
- outreach: Sends email outreach to leads
- followup: Sends follow-up emails
- appointment: Manages appointments via Calendly
- deal_closer: Generates closing messages
- report: Generates reports
- system_checker: Checks system health

When the user asks you to DO something (find leads, send emails, check status, etc.),
you should respond by executing the appropriate agent and reporting the result.
When the user is just chatting, respond conversationally.

Keep responses concise and helpful. You operate WITHOUT the daemon — 
agents run immediately when you command them."""


def repl():
    """Interactive chat REPL."""
    history = [{"role": "system", "content": SYSTEM_PROMPT}]

    print()
    print("=" * 55)
    print("  Brain CLI  —  Chat with your AI")
    print("=" * 55)
    print("  Commands:  /help  /agents  /clear  /status  /quit")
    print("  Just type naturally to chat or give commands.")
    print("  Examples:")
    print("    > find 50 HVAC leads in Texas")
    print("    > what's the weather like?")
    print("    > run system health check")
    print("=" * 55)

    while True:
        try:
            text = input("\nYou: ").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            break

        if not text:
            continue

        if text.startswith("/"):
            cmd = text[1:].lower().split()[0] if text[1:] else ""
            if cmd in ("quit", "exit", "q"):
                break
            elif cmd in ("help", "?"):
                print("\nBrain: Commands —")
                print("  /help      Show this help")
                print("  /agents    List available agents")
                print("  /clear     Clear conversation history")
                print("  /status    Show system configuration")
                print("  /quit      Exit Brain CLI")
                print("\n  Or just chat naturally. Brain will detect when to run an agent.")
            elif cmd == "agents":
                print("\nBrain: Available agents —")
                for intent in _AGENT_REGISTRY:
                    cls = _import_agent(*_AGENT_REGISTRY[intent])
                    status = "Ready" if cls else "Unavailable"
                    print(f"  {intent}: {status}")
                print("  general: Chat/direct LLM response")
            elif cmd == "clear":
                history = [history[0]]
                print("\nBrain: History cleared.")
            elif cmd == "status":
                print(f"\nBrain: System Status —")
                print(f"  Groq: {'configured' if groq_client else 'NOT configured'}")
                print(f"  Gemini: {'configured' if gemini_client else 'NOT configured'}")
                print(f"  Rules PDF: {'loaded' if get_rules_context() else 'not loaded'}")
            else:
                print(f"\nBrain: Unknown command '{text}'. Try /help")
            continue

        intent = _detect_intent(text)
        history.append({"role": "user", "content": text})

        if intent != "general":
            print(f"\nBrain: Running {intent}...")
            result = _execute_agent(intent, text)
            response = result
        else:
            # Chat with LLM using conversation history
            messages = list(history)
            try:
                rules = get_rules_context()
                if rules:
                    messages.insert(1, {
                        "role": "system",
                        "content": f"Company rules context:\n{rules[:1500]}"
                    })
            except Exception:
                pass

            if groq_client:
                try:
                    response_obj = groq_client.chat.completions.create(
                        model=GROQ_MODELS[0],
                        messages=messages,
                        max_tokens=2048,
                    )
                    response = response_obj.choices[0].message.content.strip()
                except Exception as e:
                    logger.warning("Groq chat failed, trying Gemini: %s", e)
                    try:
                        prompt = text
                        for m in reversed(messages[:-1]):
                            if m["role"] in ("user", "assistant"):
                                prompt = m["content"] + "\n" + prompt
                        response = ask_gemini(prompt)
                    except Exception as e2:
                        response = f"[Error: {e2}]"
            elif gemini_client:
                prompt = text
                for m in reversed(messages[:-1]):
                    if m["role"] in ("user", "assistant"):
                        prompt = m["content"] + "\n" + prompt
                response = ask_gemini(prompt)
            else:
                response = "[ERROR: No LLM configured. Set GROQ_API_KEY or GEMINI_API_KEY in .env]"

        print(f"\nBrain: {response}")
        history.append({"role": "assistant", "content": response})
        if len(history) > 30:
            history = [history[0]] + history[-28:]

    print("Goodbye.")


if __name__ == "__main__":
    repl()
