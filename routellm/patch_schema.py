"""Relax RouteLLM's ChatCompletionRequest schema so real AnythingLLM / OpenAI traffic validates.

RouteLLM's request model is too strict for production OpenAI clients. Left as-is it rejects
(HTTP 422) or mis-forwards several things AnythingLLM sends. This script rewrites the installed
openai_server.py as text (it does NOT import it — importing initializes the OpenAI client and
fails without a key at build time), then verifies with ast.parse.

Fixes:
  1. temperature / top_p default to 1.0 (both non-None) -> forwarded together on every request;
     newer Anthropic models reject temperature+top_p together. Default them to None.
  2. messages typed as unions of Dict[str, str] -> rejects tool-calling turns (assistant with
     `tool_calls`, `content: null`, role `tool`). Relax to List[Dict[str, Any]].
  3. tools / tool_choice typed too strictly -> nested OpenAI function schemas (agent mode) 422.
     Relax to List[Dict[str, Any]] / Union[str, Dict[str, Any]].
"""
import ast
import re

SF = "/usr/local/lib/python3.11/site-packages/routellm/openai_server.py"
src = open(SF).read()

# 0. Force a fixed sampling temperature on every request (env ROUTELLM_TEMPERATURE, default 0.3),
# regardless of what the client (AnythingLLM chat / agent) sends. top_p is cleared to avoid the
# "temperature and top_p cannot both be specified" error on newer Anthropic models.
# `os` is already imported by openai_server (it sets TOKENIZERS_PARALLELISM).
INJECT = (
    '    logging.info(f"Received request: {request}")\n'
    '    request.temperature = float(os.environ.get("ROUTELLM_TEMPERATURE", "0.3"))\n'
    "    request.top_p = None\n"
)
assert '    logging.info(f"Received request: {request}")\n' in src, "log anchor not found"
src = src.replace('    logging.info(f"Received request: {request}")\n', INJECT, 1)

# Ensure `Any` is importable from typing.
src = re.sub(
    r"^from typing import (.*)$",
    lambda m: m.group(0)
    if "Any" in [p.strip() for p in m.group(1).split(",")]
    else f"from typing import Any, {m.group(1)}",
    src,
    count=1,
    flags=re.M,
)

# 1. temperature / top_p: 1.0 -> None
src = re.sub(r"(temperature: Optional\[float\]) = 1\.0", r"\1 = None", src)
src = re.sub(r"(top_p: Optional\[float\]) = 1\.0", r"\1 = None", src)

# 2. messages: collapse the multi-line Union to a permissive type (closes on a 4-space "]").
src = re.sub(
    r"messages:\s*Union\[.*?\n {4}\]\n",
    "messages: Union[str, List[Dict[str, Any]]]\n",
    src,
    count=1,
    flags=re.S,
)

# 3. tools / tool_choice
src = re.sub(r"tools: Optional\[.*?\] = None", "tools: Optional[List[Dict[str, Any]]] = None", src)
src = re.sub(
    r"tool_choice: Optional\[.*?\] = None",
    "tool_choice: Optional[Union[str, Dict[str, Any]]] = None",
    src,
)

open(SF, "w").write(src)

# Verify: parses, and every intended change is present.
ast.parse(src)
assert "temperature: Optional[float] = None" in src, "temperature patch missing"
assert "top_p: Optional[float] = None" in src, "top_p patch missing"
assert "messages: Union[str, List[Dict[str, Any]]]" in src, "messages patch missing"
assert "tools: Optional[List[Dict[str, Any]]] = None" in src, "tools patch missing"
assert "tool_choice: Optional[Union[str, Dict[str, Any]]] = None" in src, "tool_choice patch missing"
assert 'request.temperature = float(os.environ.get("ROUTELLM_TEMPERATURE"' in src, "temperature override missing"
print("schema patched OK")
