"""
Design Agent
============
Reads the project specification and existing source files, then uses Claude
to produce a design document covering architecture, sequence diagrams, data
models, API contracts, and key design decisions.

Usage:
    export ANTHROPIC_API_KEY=sk-ant-...
    python agents/design_agent.py

Output:
    agents/output/design_output.md
"""

import os
import pathlib
import anthropic

ROOT = pathlib.Path(__file__).parent.parent
SPEC_FILE = ROOT / "event-ledger-candidate-handout.md"
OUTPUT_DIR = pathlib.Path(__file__).parent / "output"
OUTPUT_FILE = OUTPUT_DIR / "design_output.md"

# Files to include as context so the agent understands what was built
CONTEXT_FILES = [
    ROOT / "event-gateway" / "app" / "main.py",
    ROOT / "event-gateway" / "app" / "routes" / "events.py",
    ROOT / "event-gateway" / "app" / "circuit_breaker.py",
    ROOT / "account-service" / "app" / "routes" / "accounts.py",
    ROOT / "docker-compose.yml",
]


def read_file(path: pathlib.Path) -> str:
    try:
        return path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return f"(file not found: {path})"


def build_prompt(spec: str, context: dict[str, str]) -> str:
    context_block = "\n\n".join(
        f"### {name}\n```\n{content}\n```" for name, content in context.items()
    )
    return f"""You are a senior software architect. You have been given:
1. A project specification
2. The key source files of the implemented solution

Your task is to produce a comprehensive design document in Markdown that includes:
- Executive summary (2-3 sentences)
- System architecture overview with a Mermaid diagram
- Sequence diagram for the main POST /events flow using Mermaid
- Data model (ER diagram in Mermaid)
- API contract table for all endpoints
- Resiliency patterns (Circuit Breaker, Retry, Timeout) with a Mermaid state diagram
- Key design decisions table (decision, choice, rationale)
- Constraints and trade-offs

Use Mermaid syntax for all diagrams (GitHub renders them natively).

=== SPECIFICATION ===
{spec}

=== IMPLEMENTED SOURCE FILES ===
{context_block}

Now produce the full design document:"""


def main():
    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        raise SystemExit("ERROR: ANTHROPIC_API_KEY environment variable is not set.")

    print("[Design Agent] Reading specification and source files...")
    spec = read_file(SPEC_FILE)
    context = {
        str(f.relative_to(ROOT)): read_file(f) for f in CONTEXT_FILES
    }

    print("[Design Agent] Calling Claude (claude-sonnet-4-6)...")
    client = anthropic.Anthropic(api_key=api_key)

    message = client.messages.create(
        model="claude-sonnet-4-6",
        max_tokens=4096,
        messages=[
            {"role": "user", "content": build_prompt(spec, context)}
        ],
    )

    design_doc = message.content[0].text

    OUTPUT_DIR.mkdir(exist_ok=True)
    OUTPUT_FILE.write_text(design_doc, encoding="utf-8")

    print(f"[Design Agent] Design document written to: {OUTPUT_FILE}")
    print(f"[Design Agent] Input tokens : {message.usage.input_tokens}")
    print(f"[Design Agent] Output tokens: {message.usage.output_tokens}")
    print("\n--- PREVIEW (first 500 chars) ---")
    print(design_doc[:500])


if __name__ == "__main__":
    main()
