# Agent Mode error troubleshooting

Agent Mode requires a selected model that can call tools and reliably return a final text answer. When course materials are selected, it also attempts a source-grounded, JSON-formatted topic scan.

## If an agent turn fails

The UI error now contains a **stage** and a **reference**. Search the FastAPI terminal output for the reference ID. This shows the underlying Python traceback without disclosing API keys or model request bodies to the browser.

Common error codes are `missing_dependencies`, `provider_unreachable`, `provider_auth`, `model_not_found`, `provider_rejected_request`, `tool_calls_unsupported`, `invalid_topic_analysis`, `empty_answer`, `timeout`, and `agent_failure`.

1. Confirm the model selected in **Model settings** is available and supports tool/function calling. A model that works in Chat Mode may not support Agent Mode.
2. Confirm your model provider connection or local service (Ollama or 9router) is reachable from the machine running FastAPI.
3. Install project Python dependencies into the same Python environment used to start FastAPI:

   ```bash
   python -m pip install -r backend/requirements.txt
   ```

4. Restart the FastAPI backend to load installed packages and provider settings. Retry your unsent Agent Mode draft (the composer restores it after a failed request).
5. For slow, complex research requests, set `QUESTGEN_AGENT_TURN_TIMEOUT=300` (seconds; bounded to 60–600). The default is 240 seconds. Longer runs may increase API cost and do not guarantee a provider will respond.

A malformed JSON-only course scan no longer automatically fails the entire turn: the supervisor proceeds with the existing permitted materials, but must disclose incomplete analysis and must not fabricate topics. Failures of provider connectivity, authorization, scope validation, or actual agent execution still surface as errors.

## Verifying the underlying problem

The error reference is logged alongside the execution stage and selected provider/model (never the API key). For example, search terminal output for:

```text
Academic agent turn failed [ref=<reference-from-browser>]
```

Do not paste an entire backend log publicly unless you have checked it for personal data or credentials. Share the error code and a redacted traceback when seeking support.

This error reporting does **not** verify that a selected model supports agent tool calling; that requires a real provider test with the locally configured credentials.
