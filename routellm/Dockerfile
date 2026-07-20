# RouteLLM OpenAI-compatible router server.
# There is no official RouteLLM image, so we build a tiny one on python-slim.
# It routes each request to a strong or weak model based on prompt complexity.
FROM python:3.11-slim

# "serve" extra installs the OpenAI-compatible server (uvicorn/fastapi) + router deps.
# pandarallel is needed by `python -m routellm.calibrate_threshold` (not pulled by [serve]).
RUN pip install --no-cache-dir "routellm[serve]" pandarallel

# Patch RouteLLM's request schema (ChatCompletionRequest) so real AnythingLLM traffic validates:
# temperature/top_p defaults, and permissive messages/tools/tool_choice for tool-calling. Details
# and rationale live in patch_schema.py (it edits the file as text; does not import the module,
# which would need an API key at build time).
COPY patch_schema.py /tmp/patch_schema.py
RUN python /tmp/patch_schema.py

WORKDIR /app
COPY config.yaml /app/config.yaml

# Internal port; not published to the host (only AnythingLLM reaches it on the compose network).
EXPOSE 6060

# Shell-form CMD so the ${VARS} (set in .env, passed by docker-compose) expand at runtime.
#   ROUTELLM_ROUTER        router algorithm: bert (local) | mf (needs OPENAI_API_KEY) | sw_ranking | causal_llm
#   ROUTELLM_STRONG_MODEL  litellm id for COMPLEX prompts (e.g. anthropic/claude-sonnet-4-6)
#   ROUTELLM_WEAK_MODEL    litellm id for SIMPLE prompts  (e.g. anthropic/claude-haiku-4-5)
# Provider keys (ANTHROPIC_API_KEY, and OPENAI_API_KEY for mf embeddings) are read from the env by litellm.
# --verbose: log the routing decision (strong vs weak + chosen model) per request.
# NOTE: do NOT pass --api-key. In RouteLLM that flag is "the API key used for ALL LLM requests"
# and would OVERRIDE the per-provider env keys, breaking Anthropic auth. The server does not
# authenticate incoming requests; it is safe only because it is internal-only (not published).
CMD python -m routellm.openai_server \
    --verbose \
    --config /app/config.yaml \
    --routers "${ROUTELLM_ROUTER:-bert}" \
    --strong-model "${ROUTELLM_STRONG_MODEL}" \
    --weak-model "${ROUTELLM_WEAK_MODEL}" \
    --port 6060
