# PROTOTYPE — NOT ACTIVE
#
# Standalone chat testbed for the PydanticAI FTS5 agent.
# Superseded by the chat panel embedded in marimo_new/read_app.py, which uses
# the same agent (api_new/domain/chat/) but integrated into the 3-column layout.
#
# Useful to run in isolation when debugging the agent or the FTS5 search tool
# without starting the full read app.
#
# /// script
# requires-python = ">=3.12"
# dependencies = [
#     "marimo",
#     "pydantic-ai",
#     "aiosqlite",
#     "python-dotenv",
#     "pydantic-settings",
# ]
# ///
"""
Standalone wiki chat — PydanticAI agent with FTS5 chunk retrieval.
Use this as a testbed for the agent before integrating into read_app.py.
"""

import marimo

__generated_with = "0.23.6"
app = marimo.App(width="full")

with app.setup:
    import sys
    import marimo as mo
    from pathlib import Path
    from dotenv import load_dotenv

    load_dotenv()

    _project_root = Path(__file__).parent.parent
    _api_new = str(_project_root / "api_new")
    if _api_new not in sys.path:
        sys.path.insert(0, _api_new)
    sys.modules.pop("config", None)

    from config import settings
    from domain.chat.agent import create_agent
    from domain.chat.config import load_config

    wiki_db_path = str(Path(settings.WIKI_PATH) / ".llmwiki" / "index.db")
    wiki_chat_config = load_config(Path(settings.WIKI_PATH))
    wiki_agent = create_agent(
        settings.LLM_BASE_URL, settings.LLM_API_KEY, settings.LLM_MODEL,
        system_prompt=wiki_chat_config.system_prompt,
    )


@app.cell
def chat_panel():
    """Chat interface backed by PydanticAI agent with FTS5 retrieval."""
    from pydantic_ai.messages import ModelRequest, UserPromptPart, ModelResponse, TextPart

    async def respond(messages, config):
        history = []
        for msg in messages[:-1]:
            if msg.role == "user":
                history.append(ModelRequest(parts=[UserPromptPart(content=msg.content)]))
            elif msg.role == "assistant":
                history.append(ModelResponse(parts=[TextPart(content=msg.content)]))

        user_message = messages[-1].content

        async with wiki_agent.run_stream(
            user_message, deps=wiki_db_path, message_history=history
        ) as result:
            async for chunk in result.stream_text(delta=True):
                yield chunk

    _chat = mo.ui.chat(
        respond,
        prompts=wiki_chat_config.suggested_prompts,
        max_height=700,
    )
    mo.vstack([
        mo.md("## Chat with your Knowledge Base"),
        mo.md("*Searches source document chunks via FTS5. Cites document and page.*"),
        _chat,
    ], gap=2)
    return


if __name__ == "__main__":
    app.run()
