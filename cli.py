"""Harness de test pentru etapa 1: rulează agentul direct din terminal,
fără FastAPI/SSE/webui. Confirmările pentru comenzi distructive se cer
interactiv (y/n în terminal) — simulare temporară a dialogului de aprobare
din UI (etapa 2).

Rulare: .venv/bin/python cli.py "listează workdir și scrie un fișier test.txt"
"""

import asyncio
import sys

from agent import Agent, AgentError


async def _confirm(tool_name: str, raw_arguments: str) -> bool:
    print(f"\n[APROBARE NECESARĂ] tool='{tool_name}' argumente={raw_arguments}")
    answer = input("Aprobi execuția? [y/N] ").strip().lower()
    return answer == "y"


async def main() -> None:
    if len(sys.argv) < 2:
        print('Utilizare: cli.py "<mesaj pentru agent>"')
        sys.exit(1)

    user_message = " ".join(sys.argv[1:])
    agent = Agent(confirm=_confirm)

    try:
        final_text = await agent.run(user_message)
    except AgentError as e:
        print(f"\n[EROARE AGENT] {e}")
        sys.exit(1)

    print("\n--- Răspuns final ---")
    print(final_text)


if __name__ == "__main__":
    asyncio.run(main())
