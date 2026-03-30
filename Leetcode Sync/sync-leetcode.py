"""
sync_leetcode.py
Fetches accepted submissions from LeetCode and writes them to the repo.

Requirements:
    pip install requests python-slugify

Environment variables (set as GitHub Secrets):
    LEETCODE_SESSION  - value of the 'LEETCODE_SESSION' cookie
    LEETCODE_CSRF     - value of the 'csrftoken' cookie
"""

import os
import json
import time
import requests
from pathlib import Path
from datetime import datetime, timezone
from slugify import slugify

# ── Config ────────────────────────────────────────────────────────────────────
SESSION    = os.environ["LEETCODE_SESSION"]
CSRF_TOKEN = os.environ["LEETCODE_CSRF"]
OUTPUT_DIR = Path("solutions")
README     = Path("README.md")

HEADERS = {
    "Content-Type": "application/json",
    "Referer":      "https://leetcode.com",
    "x-csrftoken":  CSRF_TOKEN,
    "Cookie":       f"LEETCODE_SESSION={SESSION}; csrftoken={CSRF_TOKEN}",
}

LANG_EXT = {
    "python":     "py",  "python3":   "py",
    "java":       "java","cpp":       "cpp",
    "c":          "c",   "javascript":"js",
    "typescript": "ts",  "go":        "go",
    "rust":       "rs",  "kotlin":    "kt",
    "swift":      "swift","scala":    "scala",
    "ruby":       "rb",  "php":       "php",
    "csharp":     "cs",  "mysql":     "sql",
    "bash":       "sh",
}

# ── Helpers ───────────────────────────────────────────────────────────────────

def gql(query: str, variables: dict) -> dict:
    resp = requests.post(
        "https://leetcode.com/graphql",
        headers=HEADERS,
        json={"query": query, "variables": variables},
        timeout=30,
    )
    resp.raise_for_status()
    return resp.json()


def fetch_all_accepted() -> list[dict]:
    """Returns a list of accepted submissions, newest first, deduplicated by slug."""
    Q = """
    query submissionList($offset: Int!, $limit: Int!, $lastKey: String) {
      submissionList(offset: $offset, limit: $limit, lastKey: $lastKey) {
        lastKey
        hasNext
        submissions {
          id
          title
          titleSlug
          statusDisplay
          lang
          timestamp
          runtime
          memory
        }
      }
    }
    """
    seen_slugs: set[str] = set()
    results: list[dict] = []
    offset, limit, last_key = 0, 20, None

    while True:
        data = gql(Q, {"offset": offset, "limit": limit, "lastKey": last_key})
        page = data["data"]["submissionList"]
        for sub in page["submissions"]:
            if sub["statusDisplay"] == "Accepted" and sub["titleSlug"] not in seen_slugs:
                seen_slugs.add(sub["titleSlug"])
                results.append(sub)
        if not page["hasNext"]:
            break
        last_key = page["lastKey"]
        offset += limit
        time.sleep(0.5)   # be polite

    return results


def fetch_code(submission_id: str) -> str:
    Q = """
    query submissionDetails($submissionId: Int!) {
      submissionDetails(submissionId: $submissionId) {
        code
      }
    }
    """
    data = gql(Q, {"submissionId": int(submission_id)})
    return data["data"]["submissionDetails"]["code"]


def fetch_problem_meta(slug: str) -> dict:
    Q = """
    query questionData($titleSlug: String!) {
      question(titleSlug: $titleSlug) {
        questionFrontendId
        title
        difficulty
        topicTags { name }
        content
      }
    }
    """
    data = gql(Q, {"titleSlug": slug})
    return data["data"]["question"]


# ── Core logic ────────────────────────────────────────────────────────────────

def write_solution(sub: dict) -> dict | None:
    """Write solution file + meta.json. Returns row dict for README, or None if skipped."""
    slug      = sub["titleSlug"]
    lang      = sub["lang"].lower()
    ext       = LANG_EXT.get(lang, "txt")
    folder    = OUTPUT_DIR / slug
    code_file = folder / f"solution.{ext}"
    meta_file = folder / "meta.json"

    # Skip if already exists (avoids redundant API calls on re-runs)
    if code_file.exists() and meta_file.exists():
        existing = json.loads(meta_file.read_text())
        return existing

    problem = fetch_problem_meta(slug)
    time.sleep(0.3)
    code = fetch_code(sub["id"])
    time.sleep(0.3)

    folder.mkdir(parents=True, exist_ok=True)
    code_file.write_text(code, encoding="utf-8")

    ts      = int(sub["timestamp"])
    dt      = datetime.fromtimestamp(ts, tz=timezone.utc).strftime("%Y-%m-%d")
    row = {
        "id":         problem["questionFrontendId"],
        "title":      sub["title"],
        "slug":       slug,
        "difficulty": problem["difficulty"],
        "language":   lang,
        "runtime":    sub["runtime"],
        "memory":     sub["memory"],
        "date":       dt,
        "tags":       [t["name"] for t in problem["topicTags"]],
    }
    meta_file.write_text(json.dumps(row, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"  ✓ {row['id']:>4}. {sub['title']} ({lang}) — {dt}")
    return row


def build_readme(rows: list[dict]) -> None:
    rows_sorted = sorted(rows, key=lambda r: int(r["id"]))
    lines = [
        "# LeetCode Solutions\n",
        "Auto-synced from LeetCode via GitHub Actions.\n",
        f"> Last updated: {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}\n",
        "",
        f"**{len(rows_sorted)} problems solved**\n",
        "",
        "| # | Title | Difficulty | Language | Runtime | Memory | Date |",
        "|---|-------|-----------|----------|---------|--------|------|",
    ]
    for r in rows_sorted:
        diff_badge = {"Easy": "🟢", "Medium": "🟡", "Hard": "🔴"}.get(r["difficulty"], "")
        link = f"[{r['title']}](solutions/{r['slug']}/solution.{LANG_EXT.get(r['language'], 'txt')})"
        lines.append(
            f"| {r['id']} | {link} | {diff_badge} {r['difficulty']} "
            f"| {r['language']} | {r['runtime']} | {r['memory']} | {r['date']} |"
        )

    README.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"\n README updated — {len(rows_sorted)} entries")


# ── Entry point ───────────────────────────────────────────────────────────────

def main():
    OUTPUT_DIR.mkdir(exist_ok=True)
    print("Fetching accepted submissions…")
    submissions = fetch_all_accepted()
    print(f"   Found {len(submissions)} unique accepted problems\n")

    rows = []
    for sub in submissions:
        row = write_solution(sub)
        if row:
            rows.append(row)

    build_readme(rows)
    print("\n Sync complete")


if __name__ == "__main__":
    main()
