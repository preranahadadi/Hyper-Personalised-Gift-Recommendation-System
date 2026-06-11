# Architecture Notes

## Short Architecture Note

The Gift Recommendation Agent takes contact profile data from `contacts.json` and creates gift recommendations that can be reviewed by a human before approval.

The backend is built with FastAPI and LangGraph. FastAPI exposes the API endpoints, and LangGraph controls the workflow steps.

The workflow has these main steps:

1. Extract profile signals from the contact profile.
2. Create search queries and product intents.
3. Search for real products using Tavily and DuckDuckGo.
4. Validate and score product links.
5. Rank the top 3 gifts.
6. Generate a short personalised message.
7. Pause for human review.

The frontend is a React app. It lets the user run the workflow, review recommendations, inspect traces, and see history.

The system also stores workflow history locally using SQLite, so previous runs can be loaded again after restarting the backend.

## Tradeoffs

The system uses public search instead of a dedicated shopping API. This makes it easier to run and avoids needing paid product APIs, but public search results are sometimes noisy. They may return search pages, category pages, or links without prices.

The app uses Ollama with a local model. This keeps the system private and cheap to run, but local models can be slower and may hallucinate more than stronger hosted models.

The backend validates product links and lowers confidence when links are weak. This makes the system safer, but it also means some recommendations may be marked as needing review even if they are still useful.

The system includes human review before final approval. This adds a manual step, but it is important because gift recommendations can be personal and should be checked before sending.

SQLite is used for local history. This is simple and good for development, but a production system should use a stronger database like Postgres.

## Future Improvements

Use a dedicated shopping or product API to get more reliable product links, prices, availability, and images.

Use a stronger hosted model such as Claude or another high-quality LLM for ranking and message generation, while still keeping backend validation for links and guardrails.

Improve product page checking by opening product URLs and reading page metadata, title, price, and availability when possible.

Add automatic evaluation to score gift relevance, link quality, budget fit, professional appropriateness, and message quality.

Add user feedback learning, so rejected or edited recommendations can improve future runs.

Move local SQLite storage to Postgres for production use.

Add authentication if this is used by multiple users or stores real customer/contact data.

Add better UI filters for risk level, confidence score, exact product links, and recommendations that need review.
