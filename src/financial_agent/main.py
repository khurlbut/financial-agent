import os

import uvicorn


def main() -> None:
	host = os.getenv("FINAGENT_HOST", "127.0.0.1")
	port = int(os.getenv("FINAGENT_PORT", "8000"))
	reload = os.getenv("FINAGENT_RELOAD", "false").strip().lower() in {"1", "true", "yes"}

	uvicorn.run(
		"financial_agent.agent_api:app",
		host=host,
		port=port,
		reload=reload,
	)


if __name__ == "__main__":
	main()
