# PROTOTYPE — NOT ACTIVE
#
# Early exploration of PydanticAI + FTS5 chat before the agent was extracted
# into api_new/domain/chat/ and integrated into read_app.py.
# Uses pydantic-ai[google] + OpenRouter; hardcodes Gemini model names.
#
# Superseded by api_new/domain/chat/agent.py + marimo_new/read_app.py.
# Keep for reference: shows the original search_chunks tool SQL query and
# how PydanticAI message history was converted from marimo chat messages.
#
# /// script
# requires-python = ">=3.12"
# dependencies = [
#     "marimo",
#     "pydantic-ai[google]",
#     "python-dotenv",
#     "aiosqlite",
# ]
# ///

"""
Pydantic-AI Chat with Knowledge Vault
======================================

This notebook demonstrates how to use Pydantic-AI to create an AI agent that can
autonomously search a SQLite database to answer user questions.

Key Concepts:
-------------
1. **Agent**: The central component that orchestrates AI interactions
2. **Model**: The LLM backend (we use OpenRouter with Gemini)
3. **Tools**: Functions the agent can call to gather information
4. **Async/Await**: All operations are asynchronous for performance
5. **Marimo Cells**: Each @app.cell is a self-contained unit with inputs/outputs

How It Works:
-------------
1. User types a question in the chat interface
2. The agent receives the question and decides if it needs to search
3. If needed, the agent calls the search_chunks tool
4. The tool queries SQLite FTS5 and returns results
5. The agent uses the results to formulate an answer
6. The answer is displayed in the chat interface
"""

import marimo
__generated_with__ = "0.23.4"
app = marimo.App(width="full")


# ============================================================================
# CELL 1: Setup and Configuration
# ============================================================================
# This cell loads environment variables and sets up the paths we'll need.
# Marimo cells can return values that are passed to other cells as inputs.
@app.cell
def setup():
    import marimo as mo
    import os
    from pathlib import Path
    from dotenv import load_dotenv
    from pydantic_ai import Agent
    from pydantic_ai.models.openai import OpenAIModel
    from pydantic_ai.providers.openai import OpenAIProvider
    import aiosqlite

    # Load environment variables from .env file in the project root
    load_dotenv()

    # Get the wiki path from environment - this is where your documents are stored
    WIKI_PATH = Path(os.environ["WIKI_PATH"])

    # OpenRouter configuration
    OPENROUTER_API_KEY = os.environ["LLM_API_KEY"]
    OPENROUTER_BASE_URL = os.environ.get("LLM_BASE_URL", "https://openrouter.ai/api/v1")
    OPENROUTER_MODEL = os.environ.get("LLM_MODEL", "google/gemini-2.0-flash")

    # Path to the SQLite database
    DB_PATH = WIKI_PATH / ".llmwiki" / "index.db"

    # Return all values including the classes and modules needed by other cells
    return mo, WIKI_PATH, OPENROUTER_API_KEY, OPENROUTER_BASE_URL, OPENROUTER_MODEL, DB_PATH, Agent, OpenAIModel, OpenAIProvider, aiosqlite


# ============================================================================
# CELL 2: Search Tool Definition
# ============================================================================
# This cell defines a tool that the AI agent can call to search the database.
# Tools are just async functions that the agent can invoke when needed.
@app.cell
def search_tool(DB_PATH, aiosqlite):
    """
    TOOL CELL: Define the search_chunks tool.

    What is a Tool?
    --------------
    In Pydantic-AI, a tool is a function that the AI agent can call to perform
    actions or gather information. The agent decides WHEN to call tools based
    on the user's question and its own reasoning.

    How Tools Work:
    ---------------
    1. The agent receives a user question
    2. The agent analyzes the question and decides if it needs more information
    3. If needed, the agent calls a tool (like search_chunks)
    4. The tool executes and returns results
    5. The agent uses the results to answer the user

    Tool Requirements:
    ------------------
    - Must be an async function (use 'async def')
    - Should have type hints for parameters
    - Should have a docstring explaining what it does
    - Returns a string that the agent can use in its response
    """
    # This is the actual tool function that the agent will call
    async def search_chunks(query: str, limit: int = 15) -> str:
        """
        Search document chunks in the knowledge vault using FTS5.

        Args:
            query: The search query - what we're looking for
            limit: Maximum number of results to return (default: 5)

        Returns:
            A formatted string with search results including:
            - Number of results found
            - File path and page number for each result
            - Relevance score
            - A snippet of the matching content

        How FTS5 Search Works:
        ---------------------
        SQLite FTS5 (Full-Text Search) is a virtual table that provides
        fast text search capabilities. It uses:
        - Tokenization: Breaks text into words
        - Indexing: Creates an inverted index of words to documents
        - Ranking: Scores results by relevance (BM25 algorithm)

        The MATCH operator performs the actual search against the index.
        """
        # First, check if the database exists
        if not DB_PATH.exists():
            return f"Database not found at {DB_PATH}"

        # Connect to the database asynchronously
        # 'async with' ensures the connection is properly closed when done
        async with aiosqlite.connect(DB_PATH) as db:
            # Enable WAL mode for better concurrency
            # WAL = Write-Ahead Logging, allows readers and writers to work simultaneously
            await db.execute("PRAGMA journal_mode=WAL")

            # This SQL query does a full-text search using FTS5
            # It joins three tables:
            # 1. document_chunks (dc) - the actual chunk content
            # 2. chunks_fts (fts) - the full-text search index
            # 3. documents (d) - metadata about the source documents
            sql = """
                SELECT dc.content, dc.page, dc.header_breadcrumb,
                       d.filename, d.title, d.path, d.file_type,
                       rank as score
                FROM document_chunks dc
                JOIN chunks_fts fts ON dc.rowid = fts.rowid
                JOIN documents d ON dc.document_id = d.id
                WHERE chunks_fts MATCH ? AND d.status != 'failed'
                ORDER BY rank LIMIT ?
            """

            # Execute the query with parameters
            # The '?' are placeholders that prevent SQL injection
            cursor = await db.execute(sql, (query, limit))
            rows = await cursor.fetchall()

            # If no results, return a helpful message
            if not rows:
                return f"No results found for query: '{query}'"

            # Format the results for the AI to read
            # We use markdown formatting for readability
            lines = [f"**{len(rows)} result(s)** for '{query}':\n"]

            for row in rows:
                # Unpack the row into named variables for clarity
                content, page, breadcrumb, filename, title, path, file_type, score = row

                # Build the file path string
                filepath = f"{path}{filename}"

                # Add page number if available
                page_str = f" (p.{page})" if page else ""

                # Add breadcrumb (section path) if available
                breadcrumb_str = f"\n  {breadcrumb}" if breadcrumb else ""

                # Extract a longer snippet from the content
                snippet = content[:400].strip()
                if len(content) > 400:
                    snippet += "..."

                # Format each result with markdown
                lines.append(f"**{filepath}**{page_str} [{score:.1f}]{breadcrumb_str}")
                lines.append(f"```\n{snippet}\n```")

            # Join all lines into a single string to return
            return "\n".join(lines)

    # Return the function as a tuple (Marimo convention for single returns)
    # The agent will receive this as a callable tool
    return search_chunks,


# ============================================================================
# CELL 3: Agent Creation
# ============================================================================
# This cell creates the Pydantic-AI agent with the model and tools.
# The agent is the "brain" that decides what to do.
@app.cell
def create_agent(OPENROUTER_API_KEY, OPENROUTER_BASE_URL, OPENROUTER_MODEL, search_chunks, Agent, OpenAIModel, OpenAIProvider):
    """
    AGENT CELL: Create and configure the Pydantic-AI agent.

    What is an Agent?
    ----------------
    An Agent in Pydantic-AI is the main orchestrator that:
    1. Receives user messages
    2. Decides which tools to call (if any)
    3. Calls tools and gets results
    4. Synthesizes a final response

    Agent Components:
    ----------------
    - Model: The LLM that does the thinking (Gemini via OpenRouter)
    - System Prompt: Instructions for how the agent should behave
    - Tools: Functions the agent can call to gather information
    - Name/Description: Metadata for the agent

    How the Agent Makes Decisions:
    -----------------------------
    The agent uses the LLM's reasoning to:
    1. Analyze the user's question
    2. Determine if it needs more information
    3. Choose which tool(s) to call
    4. Interpret tool results
    5. Formulate a helpful response

    This is "agentic" behavior - the AI autonomously decides what to do!
    """
    # Create the model instance
    model = OpenAIModel(
        OPENROUTER_MODEL,
        provider=OpenAIProvider(
            base_url=OPENROUTER_BASE_URL,
            api_key=OPENROUTER_API_KEY,
        ),
    )

    # Create the agent with all its configuration
    agent = Agent(
        model,  # The LLM to use for reasoning

        # A friendly name for the agent (useful for logging/debugging)
        name="wiki_assistant",

        # Description of what this agent does
        description="Assistant that answers questions by searching the knowledge vault",

        # The system prompt is CRITICAL - it tells the agent how to behave
        # This is like giving instructions to a human assistant
        system_prompt=(
            "You are a helpful assistant that answers questions about the user's knowledge vault. "
            "The vault contains documents about Argentinian financial instruments and investments. "
            "ALWAYS use the search_chunks tool to find relevant content before answering. "
            "When searching, try multiple different queries if the first one doesn't return enough results. "
            "For broad questions like 'what investments do you know about', search for several types individually (e.g. 'acciones', 'bonos', 'FCI', 'plazo fijo', 'cedears'). "
            "Use the conversation history to maintain context across multiple turns. "
            "'Esas' or 'ellas' refers to the items mentioned in the previous message — look them up again if needed. "
            "Synthesize all search results into a clear, comprehensive answer. "
            "If you truly don't find relevant information after multiple searches, say so clearly."
        ),

        # The tools the agent can call
        # Pydantic-AI automatically handles tool calling - we just provide the functions
        tools=[search_chunks],  # search_chunks is the function returned from the previous cell
    )

    # Return the configured agent for use in other cells
    return agent


# ============================================================================
# CELL 4: Chat Interface
# ============================================================================
# This cell creates the Marimo chat widget and connects it to the agent.
@app.cell
def chat_panel(agent, mo):
    """
    CHAT PANEL CELL: Create the user interface and connect it to the agent.

    Marimo UI Components:
    --------------------
    Marimo provides reactive UI components that automatically update
    when their dependencies change. The chat widget is one such component.

    How the Chat Flow Works:
    ------------------------
    1. User types a message in the chat widget
    2. Marimo calls the 'respond' function with the message history
    3. The respond function calls agent.run() with the latest message
    4. The agent processes the message (may call tools internally)
    5. The agent returns a result
    6. We yield the result to stream it back to the UI

    Streaming:
    ----------
    Using 'yield' allows us to stream responses character by character,
    giving the user immediate feedback as the answer is generated.
    """
    async def respond(messages, config):
        """
        Handle incoming chat messages and get responses from the agent.

        Args:
            messages: List of previous messages in the conversation
            config: Configuration for the chat widget (not used here)

        Returns:
            Yields the agent's response as it's generated

        Note:
        -----
        The 'config' parameter is required by Marimo but we don't use it.
        This is why we see an "unused parameter" warning - it's normal.
        """
        # Build message history from previous turns for the agent
        # Pydantic-AI uses its own message types, so we convert from Marimo's format
        from pydantic_ai.messages import ModelRequest, UserPromptPart, ModelResponse, TextPart

        history = []
        # Convert all messages except the last one (current user message) to pydantic-ai history
        for msg in messages[:-1]:
            if msg.role == "user":
                history.append(ModelRequest(parts=[UserPromptPart(content=msg.content)]))
            elif msg.role == "assistant":
                history.append(ModelResponse(parts=[TextPart(content=msg.content)]))

        # Get the latest user message
        user_message = messages[-1].content

        # Run the agent with user message AND full conversation history
        result = await agent.run(user_message, message_history=history)

        # Yield the result to the chat widget
        # The 'yield' keyword makes this a generator function
        # Marimo streams the output as it's generated
        yield result.output

    # Create the chat widget with Marimo
    # mo.ui.chat is a pre-built chat interface component
    chat_widget = mo.ui.chat(
        respond,  # The function to call when user sends a message

        # Suggested prompts that appear as clickable buttons
        # These help users get started with example questions
        prompts=[
            "What documents are in my knowledge vault?",
            "Search for information about investment strategies",
            "What are the main topics covered?",
        ],

        # Maximum height of the chat widget in pixels
        max_height=600,
    )

    # Return the chat widget for the layout cell
    return (chat_widget,)


# ============================================================================
# CELL 5: Layout
# ============================================================================
# This cell arranges all the UI components in a nice layout.
@app.cell
def main_layout(chat_widget, mo):
    """
    LAYOUT CELL: Arrange the UI components on the page.

    Marimo Layout Components:
    ------------------------
    - mo.vstack: Stack components vertically
    - mo.hstack: Stack components horizontally
    - mo.md: Render markdown text
    - gap: Spacing between components

    Layout Philosophy:
    ------------------
    We use a vertical stack (vstack) to organize the page:
    1. Title/heading
    2. Description/instructions
    3. The main chat widget

    This creates a clean, focused interface for the user.
    """
    # Create a vertical stack of components
    # Each item in the list is stacked on top of the previous one
    mo.vstack(
        [
            # Page title using markdown
            mo.md("### Chat with your Knowledge Vault"),

            # Instructions for the user
            mo.md("Ask questions and the AI will search your documents for answers."),

            # The main chat widget
            chat_widget,
        ],
        gap=2,  # Add some spacing between components
    )
    return


# ============================================================================
# MAIN ENTRY POINT
# ============================================================================
# This runs the app when the file is executed directly.
if __name__ == "__main__":
    app.run()
