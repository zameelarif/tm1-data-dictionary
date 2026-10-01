# internal/ — private, never published

This folder exists only in the private working repository. `scripts/export_public.ps1`
never copies it.

| File | Purpose |
|---|---|
| `BUILD_JOURNAL.md` | How the project was built, step by step; current state; backlog; how to resume |
| `LEARNING_LOG.md` | The project on one page, every concept used, and playbooks |
| `phase1_spec.docx` | Original design specification |
| `private_terms.txt` | Words that must never appear in the public copy (one per line) |

## Publishing rules

1. Never switch the private repository to public. Its history contains every file ever
   committed, including these notes and old configs.
2. Publish only through `scripts/export_public.ps1`, which copies an allow-list of files
   into a new folder and refuses to finish if a private term is found.
3. The public repository starts with one fresh commit. Later releases are exported the
   same way and copied over the public working copy.
4. Contributors' pull requests go to the public repository; merge them back into the
   private one by hand.
