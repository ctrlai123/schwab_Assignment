"""
QA Agent
========
Reads all test files and source files, then uses Claude to:
  - Generate a unit test coverage analysis
  - Identify missing test scenarios
  - Produce additional test cases as runnable pytest code
  - Generate a functional test plan covering all API endpoints

Usage:
    export ANTHROPIC_API_KEY=sk-ant-...
    python agents/qa_agent.py

Output:
    agents/output/qa_output.md
"""

import os
import pathlib
import anthropic

ROOT = pathlib.Path(__file__).parent.parent
OUTPUT_DIR = pathlib.Path(__file__).parent / "output"
OUTPUT_FILE = OUTPUT_DIR / "qa_output.md"

SOURCE_FILES = [
    ROOT / "event-gateway" / "app" / "routes" / "events.py",
    ROOT / "event-gateway" / "app" / "circuit_breaker.py",
    ROOT / "event-gateway" / "app" / "services" / "account_client.py",
    ROOT / "account-service" / "app" / "routes" / "accounts.py",
]

TEST_FILES = [
    ROOT / "event-gateway" / "tests" / "test_events.py",
    ROOT / "event-gateway" / "tests" / "test_resiliency.py",
    ROOT / "event-gateway" / "tests" / "conftest.py",
    ROOT / "account-service" / "tests" / "test_accounts.py",
    ROOT / "account-service" / "tests" / "conftest.py",
]

COVERAGE_SUMMARY = """
account-service : 95% overall
  app/routes/accounts.py : 100%   (all business logic)
  app/routes/health.py   : 85%
  app/database.py        : 67%    (startup path not hit in tests)

event-gateway   : 80% overall
  app/routes/events.py         : 100%   (all business logic)
  app/circuit_breaker.py       : 91%
  app/services/account_client.py : 23%  (HTTP calls mocked in unit tests)
  app/main.py                  : 87%
"""


def read_file(path: pathlib.Path) -> str:
    try:
        return path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return f"(file not found: {path})"


def build_prompt(sources: dict[str, str], tests: dict[str, str]) -> str:
    source_block = "\n\n".join(
        f"### {name}\n```python\n{content}\n```" for name, content in sources.items()
    )
    test_block = "\n\n".join(
        f"### {name}\n```python\n{content}\n```" for name, content in tests.items()
    )
    return f"""You are a senior QA engineer reviewing a Python / FastAPI financial microservices project.

You have been given:
1. The source code of both services
2. The existing test suites
3. A coverage summary

Your tasks:
1. **Coverage Analysis** — Based on the coverage summary and existing tests, explain which code paths are covered and which are not. Focus on business-critical gaps.
2. **Missing Test Scenarios** — List at least 8 test scenarios that are not yet covered. Include edge cases specific to a financial ledger (e.g., concurrent duplicate submissions, very large amounts, currency mismatches).
3. **New Test Cases** — Write 5 additional pytest test functions (runnable code) that cover the most critical missing scenarios. Follow the style of the existing tests.
4. **Functional Test Plan** — A Markdown table listing every API endpoint with: HTTP method, path, test scenario, expected status code, and pass/fail criteria. Cover happy path, error cases, and boundary conditions.
5. **QA Risk Assessment** — What are the top 3 quality risks in the current implementation from a financial system perspective?

=== COVERAGE SUMMARY ===
{COVERAGE_SUMMARY}

=== SOURCE FILES ===
{source_block}

=== EXISTING TEST FILES ===
{test_block}

Produce the full QA report now:"""


def main():
    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        raise SystemExit("ERROR: ANTHROPIC_API_KEY environment variable is not set.")

    print("[QA Agent] Reading source and test files...")
    sources = {str(f.relative_to(ROOT)): read_file(f) for f in SOURCE_FILES}
    tests = {str(f.relative_to(ROOT)): read_file(f) for f in TEST_FILES}

    print("[QA Agent] Calling Claude (claude-sonnet-4-6)...")
    client = anthropic.Anthropic(api_key=api_key)

    message = client.messages.create(
        model="claude-sonnet-4-6",
        max_tokens=4096,
        messages=[
            {"role": "user", "content": build_prompt(sources, tests)}
        ],
    )

    report = message.content[0].text

    OUTPUT_DIR.mkdir(exist_ok=True)
    OUTPUT_FILE.write_text(report, encoding="utf-8")

    print(f"[QA Agent] QA report written to: {OUTPUT_FILE}")
    print(f"[QA Agent] Input tokens : {message.usage.input_tokens}")
    print(f"[QA Agent] Output tokens: {message.usage.output_tokens}")
    print("\n--- PREVIEW (first 500 chars) ---")
    print(report[:500])


if __name__ == "__main__":
    main()
