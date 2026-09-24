"""Download one allowlisted checkpoint into a local directory.

Run as a child process so the server can pause it by terminating the process
group. Partial files stay on disk and the next download resumes them.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from reed.catalog import BY_ID


def main() -> int:
    if len(sys.argv) != 3:
        print("usage: download_worker.py <model-id> <dest>", file=sys.stderr)
        return 2
    model_id, dest = sys.argv[1], sys.argv[2]
    if model_id not in BY_ID:
        print("model is not allowlisted", file=sys.stderr)
        return 2
    from huggingface_hub import snapshot_download

    snapshot_download(
        repo_id=model_id,
        local_dir=dest,
        max_workers=4,
        ignore_patterns=["*.md", ".gitattributes"],
    )
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        print(str(exc), file=sys.stderr)
        raise SystemExit(1)
