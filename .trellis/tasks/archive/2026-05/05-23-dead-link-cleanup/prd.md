# Strip dead links from wiki pages when a page is deleted

## Goal
When `delete_page()` removes a wiki page, other wiki pages that link to it
are left with broken markdown links. Fix those references automatically as
part of the deletion.

## Requirements
- Before deleting a page, query `document_references` for all wiki pages
  that have the target as `target_document_id`.
- For each referencing page, strip the dead link from the markdown content:
  `[label](path)` → `label` (keep the label text, remove the link).
- Update the referencing page on disk and in the DB (content + chunks).
- No user confirmation needed — deletion is already an explicit action.

## Acceptance Criteria
- [ ] After deleting a page, no remaining wiki page contains a markdown link
  to the deleted page's path.
- [ ] The label text of the removed link is preserved (not silently dropped).
- [ ] The referencing page's DB record (content, chunks) is updated.
- [ ] Unit test: delete a page that is linked from another page; assert the
  link is gone from the referencing page's content.

## Technical Notes
- Implementation lives in `delete_page()` in `api_new/domain/tools/wiki_fs.py`.
- Use `document_references WHERE target_document_id = ?` to find referencing pages.
- Regex replacement: `re.sub(r'\[([^\]]+)\]\(relative_path\)', r'\1', content)`.
- Must run before the DELETE so the reference rows still exist.
