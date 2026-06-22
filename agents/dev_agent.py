"""
Development Agent
=================
Reads all service source files and uses Claude to:
  - Audit error handling and logging coverage
  - Identify missing auditing / observability gaps
  - Suggest meaningful git commit messages for staged changes
  - Propose improvements with concrete code snippets

Usage:
    export ANTHROPIC_API_KEY=sk-ant-...
    python agents/dev_agent.py

Output:
    agents/output/dev_output.md
"""

import os
import pathlib
import anthropic

ROOT = pathlib.Path(__file__).parent.parent
OUTPUT_DIR = pathlib.Path(__file__).parent / "output"
OUTPUT_FILE = OUTPUT_DIR / "dev_output.md"

SOURCE_FILES = [
    ROOT / "event-gateway" / "app" / "main.py",
    ROOT / "event-gateway" / "app" / "logging_config.py",
    ROOT / "event-gateway" / "app" / "circuit_breaker.py",
    ROOT / "event-gateway" / "app" / "metrics.py",
    ROOT / "event-gateway" / "app" / "routes" / "events.py",
    ROOT / "event-gateway" / "app" / "routes" / "health.py",
    ROOT / "event-gateway" / "app" / "services" / "account_client.py",
    ROOT / "account-service" / "app" / "main.py",
    ROOT / "account-service" / "app" / "logging_config.py",
    ROOT / "account-service" / "app" / "routes" / "accounts.py",
    ROOT / "account-service" / "app" / "routes" / "health.py",
    ROOT / "docker-compose.yml",
]


def read_file(path: pathlib.Path) -> str:
    try:
        return path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return f"(file not found: {path})"


def build_prompt(sources: dict[str, str]) -> str:
    source_block = "\n\n".join(
        f"### {name}\n```python\n{content}\n```" for name, content in sources.items()
    )
    return f"""You are a senior software engineer reviewing a Python / FastAPI microservices project.

Your tasks:
1. **Error Handling Audit** — Review every route and service method. List which paths handle errors well and which are missing try/except or proper HTTP status codes.
2. **Logging & Auditing Audit** — Assess structured JSON logging coverage. Identify any code paths that are silent (no log on entry, success, or failure). Note any missing audit trails for financial events.
3. **Observability Gaps** — What metrics, traces, or alerts are missing that would matter in production?
4. **Git Commit Message Suggestions** — Based on the code, propose 5 conventional-commit-style commit messages that would accurately describe the key development milestones (feat, fix, refactor, test, chore).
5. **Top 3 Improvement Suggestions** — Concrete, actionable improvements with code snippets.

Format your response as a structured Markdown report with clear sections for each task.

=== SOURCE FILES ===
{source_block}

Produce the development audit report now:"""


def main():
    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        raise SystemExit("ERROR: ANTHROPIC_API_KEY environment variable is not set.")

    print("[Dev Agent] Reading source files...")
    sources = {
        str(f.relative_to(ROOT)): read_file(f) for f in SOURCE_FILES
    }

    print("[Dev Agent] Calling Claude (claude-sonnet-4-6)...")
    client = anthropic.Anthropic(api_key=api_key)

    message = client.messages.create(
        model="claude-sonnet-4-6",
        max_tokens=4096,
        messages=[
            {"role": "user", "content": build_prompt(sources)}
        ],
    )

    report = message.content[0].text

    OUTPUT_DIR.mkdir(exist_ok=True)
    OUTPUT_FILE.write_text(report, encoding="utf-8")

    print(f"[Dev Agent] Development audit written to: {OUTPUT_FILE}")
    print(f"[Dev Agent] Input tokens : {message.usage.input_tokens}")
    print(f"[Dev Agent] Output tokens: {message.usage.output_tokens}")
    print("\n--- PREVIEW (first 500 chars) ---")
    print(report[:500])


if __name__ == "__main__":
    main()
