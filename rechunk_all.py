import asyncio
import sys
from pathlib import Path

# Add 'api' directory to sys.path so we can import services
sys.path.append(str(Path(__file__).parent / "api"))

import aiosqlite
from services.chunker import chunk_text
from infra.db.sqlite import SQLiteChunkRepository, SQLiteDocumentRepository

async def rechunk_all():
    # Use the path from .env if available, otherwise prompt
    wiki_path = "/Users/claudiograsso/Documents/finanzas/ai_fin_pers"
    db_path = Path(wiki_path) / ".llmwiki" / "index.db"

    if not db_path.exists():
        print(f"Error: Database not found at {db_path}")
        return

    print(f"Connecting to {db_path}...")
    async with aiosqlite.connect(db_path) as db:
        doc_repo = SQLiteDocumentRepository(db)
        chunk_repo = SQLiteChunkRepository(db)

        # Get the first workspace ID
        cursor = await db.execute("SELECT id, user_id FROM workspace LIMIT 1")
        row = await cursor.fetchone()
        if not row:
            print("Error: No workspace found in database.")
            return
        kb_id, user_id = row

        # Get all ready documents
        cursor = await db.execute("SELECT id, content, filename FROM documents WHERE status = 'ready'")
        docs = await cursor.fetchall()

        print(f"Found {len(docs)} documents to chunk.")

        for doc_id, content, filename in docs:
            if not content:
                print(f"Skipping {filename} (no content)")
                continue

            print(f"Chunking {filename}...")
            chunks = chunk_text(content)
            await chunk_repo.store(doc_id, user_id, kb_id, chunks)
            print(f"  Stored {len(chunks)} chunks.")

        await db.commit()
    print("Done!")

if __name__ == "__main__":
    asyncio.run(rechunk_all())
