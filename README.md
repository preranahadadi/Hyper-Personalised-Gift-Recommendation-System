# Gift Recommendation Agent

A hyper-personalised gift recommendation system built with **LangGraph**, **Ollama** (local LLM), **DuckDuckGo Search**, optional **Tavily Search**, **FastAPI**, and a React review UI.

---

## Architecture

```
contacts.json
     │
     ▼
┌─────────────────────────────────────────────────────────────────┐
│                      LangGraph Workflow                         │
│                                                                 │
│  extract_signals → search_products → rank_gifts               │
│                          ↕ (retry if <3 valid results)         │
│                   generate_messages                             │
│                          │                                      │
│              [interrupt_before human_review]                    │
│                          │                                      │
│                    human_review ←── API: approve/reject/edit    │
│                          │                                      │
│                       finalize                                  │
└─────────────────────────────────────────────────────────────────┘
     │
     ▼
  JSON output → FastAPI → Review UI
```

### Workflow steps

| Step | What it does |
|------|-------------|
| `extract_signals` | LLM extracts strong/weak gifting signals from LinkedIn profile; enforces sensitive-attribute guardrails |
| `search_products` | LLM generates targeted search queries; Tavily + DuckDuckGo return product candidates; filters to trusted exact product pages |
| `rank_gifts` | LLM ranks top-3 gifts by signal fit, budget, relationship type; assigns confidence scores and risk levels |
| `generate_messages` | LLM writes a short personalised note for each gift (tone-configurable: professional / warm / formal) |
| `human_review` | Graph pauses here; human can approve, reject, edit, or regenerate via API or UI |
| `finalize` | Marks run as complete |

---

## Setup

### 1. Install Ollama

Download from **https://ollama.com/download** and install.

Pull the model (choose one):
```bash
ollama pull llama3.2        # fast, 3B — good for demos
ollama pull llama3.1        # better quality, 8B
ollama pull qwen2.5:7b      # excellent JSON output
ollama pull mistral         # reliable instruction-following
```

Verify Ollama is running:
```bash
ollama serve                # or it starts automatically after install
curl http://localhost:11434/api/tags
```

### 2. Install Python dependencies

```bash
cd gift-agent/backend
pip install -r requirements.txt
```

### 3. Configure environment

Edit `backend/.env`:
```env
OLLAMA_BASE_URL=http://localhost:11434
OLLAMA_MODEL=llama3.2:latest
OLLAMA_TEMPERATURE=0.1
OLLAMA_NUM_CTX=4096
OLLAMA_NUM_PREDICT=700

# Optional. When set, search uses Tavily plus DuckDuckGo.
TAVILY_API_KEY=
TAVILY_SEARCH_DEPTH=basic
```

### 4. Start the API server

```bash
cd gift-agent/backend
python main.py
```

Server starts at **http://localhost:8000**

- Review UI: http://localhost:8000
- Swagger API docs: http://localhost:8000/docs

---

## Usage

### Option A — Web UI

1. Open http://localhost:8000 in your browser
2. Click **Run Workflow** tab
3. Upload `data/contacts.json` and click **Run All Contacts**
4. Wait ~30–90 seconds per contact (depends on Ollama model speed)
5. Switch to **Trace** tab to inspect intermediate outputs
6. Switch to **Review** tab to approve, reject, edit, or regenerate each set of recommendations

### Option B — cURL / API

```bash
# Run workflow for all 6 contacts
curl -X POST http://localhost:8000/api/workflow/run \
  -H "Content-Type: application/json" \
  -d @data/contacts.json_wrapped   # see sample below

# Or upload the file directly
curl -X POST http://localhost:8000/api/workflow/run-file \
  -F "file=@data/contacts.json"
```

Expected request body for `/api/workflow/run`:
```json
{
  "contacts": [ /* array of contact objects */ ]
}
```

```bash
# Get recommendations for a specific run
curl http://localhost:8000/api/workflow/{thread_id}

# Approve recommendations
curl -X POST http://localhost:8000/api/workflow/{thread_id}/review \
  -H "Content-Type: application/json" \
  -d '{"action": "approve"}'

# Regenerate with feedback
curl -X POST http://localhost:8000/api/workflow/{thread_id}/review \
  -H "Content-Type: application/json" \
  -d '{"action": "regenerate", "feedback": "Focus more on cricket-related gifts", "tone": "warm"}'
```

---

## Sample Output

```json
{
  "contact_name": "Aarav Mehta",
  "profile_signals": {
    "strong_signals": ["Interested in cricket", "Works in SaaS sales leadership"],
    "weak_signals": ["May appreciate leadership or business books"],
    "signals_to_avoid": ["Do not infer religion, politics, health, family status..."]
  },
  "search_trace": {
    "queries_used": ["premium cricket gift buy India amazon under 5000 INR"],
    "products_considered_count": 12
  },
  "recommended_gifts": [
    {
      "rank": 1,
      "gift_name": "SG Sunny Tonny Cricket Bat — Full Size",
      "product_url": "https://www.amazon.in/...",
      "store": "Amazon India",
      "estimated_price": "₹3,499",
      "why_this_gift": "Directly reflects his passion for cricket, mentioned in three recent posts.",
      "personalisation_reasoning": "Strong signal: cricket interest from posts and comments",
      "personalised_message": "Dear Aarav, it was a pleasure speaking with you last week. Wishing you as many wins off the field as on it — enjoy this small token. Looking forward to our follow-up!",
      "confidence_score": 0.88,
      "risk_level": "low",
      "assumptions": ["Cricket interest inferred from recent posts and comments"]
    }
  ],
  "human_review": {
    "status": "pending_review",
    "available_actions": ["approve", "reject", "edit", "regenerate"]
  }
}
```

---

## Project Structure

```
gift-agent/
├── backend/
│   ├── main.py               FastAPI app + API endpoints
│   ├── .env                  Local model/search configuration
│   ├── config.py             Loads .env, Ollama model, search settings, domain lists
│   ├── requirements.txt
│   ├── models/
│   │   └── schemas.py        Pydantic models for all inputs/outputs
│   ├── tools/
│   │   └── search.py         Tavily/DuckDuckGo search + exact product URL validation
│   └── workflow/
│       ├── state.py          LangGraph TypedDict state
│       ├── nodes.py          All 6 workflow node functions + LLM prompts
│       └── graph.py          StateGraph definition + interrupt_before
├── data/
│   └── contacts.json         6 diverse sample contacts
└── frontend/
    └── src/                  React run, trace, history, and review UI
```

---

## Evaluation Notes

I have not built a full evaluation system, but I would check the quality of the agent by reviewing each contact's final output and the trace shown in the UI.

First, I would check whether the gift actually matches the contact profile. The recommendation should be connected to clear profile signals such as posts, comments, job role, professional interests, or topics the contact engages with. If the gift could apply to anyone, I would score it lower.

Second, I would check the product links manually or with a link checker. Each link should open a real product page from a trusted store. It should not be a fake link, a broken page, an unrelated product, or only a general search results page.

Third, I would check budget and country fit. The product price should be inside the requested budget, or very close to it, and the store should be useful for the contact's country. For example, if the contact is in India, the recommendation should prefer India-friendly stores like Amazon India, Flipkart, Nykaa, Myntra, or similar options.

Fourth, I would check professional appropriateness. Since this is for business gifting, the gift should feel safe and respectful. It should not be romantic, too personal, medical, political, religious, or uncomfortable for a professional relationship.

Fifth, I would check that the system avoids sensitive or creepy personalisation. It should not guess religion, politics, health, ethnicity, gender, family status, private life details, or anything similar. The gift should feel thoughtful, but not intrusive.

Sixth, I would check the personalised message. A good message should be short, warm, professional, and related to the occasion. It should not invent facts about the contact or mention internal assumptions.

Finally, I would check how the system behaves when search results are poor. In that case, it should lower the confidence score, mention assumptions, mark the result for human review, or use fallback marketplace links carefully. It should not pretend that weak or unverified search results are perfect recommendations.

For manual testing, I would score each area from 1 to 5. A strong result should have relevant gifts, working links, correct budget and country fit, low professional risk, no sensitive guesses, a clear message, and honest handling of uncertainty.

---

## Guardrails

The system has guardrails to make sure the gift recommendations stay safe, professional, and respectful.

- It should not guess sensitive personal details such as religion, politics, health, ethnicity, gender, family status, or private life information.
- It shows a `signals_to_avoid` field so the reviewer can see what the agent was told not to use.
- It should not recommend gifts that are too personal, romantic, medical, political, religious, or uncomfortable for a business relationship.
- It should not use personalisation that feels creepy or intrusive.
- It should not invent unsupported claims about the contact.
- It should not create fake product links. If the system only finds a search page or weak product result, it lowers confidence and asks for human review.
- If the profile information is weak, the system should say what assumptions it made instead of acting fully certain.
- Risk level and confidence score help the reviewer quickly identify recommendations that need extra checking.

---

## Tradeoffs & Future Improvements

- **Search quality**: Tavily + DuckDuckGo improve discovery, but a dedicated shopping/product API would give higher-quality, price-verified product links.
- **Price validation**: Currently regex-based from search snippets; a price-scraping step or shopping API would be more reliable.
- **Model quality**: Larger open-source models such as Llama 3.1 70B, Qwen2.5 14B/32B, or Mixtral can produce better reasoning if the hardware supports them. For this local setup, `qwen2.5:7b` is a good choice because it is open-source, practical to run locally, and more reliable for JSON output than smaller models.
- **Persistence**: Workflow history is stored in local SQLite for development; production deployment could move this to Postgres and a persistent LangGraph checkpointer.
- **Bulk evaluation**: Add an automated LLM-judge eval that scores relevance, appropriateness, and message quality across all contacts.
- **Multi-turn refinement**: Allow iterative human feedback to progressively improve recommendations without full regeneration.
