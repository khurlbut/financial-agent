import uvicorn

from . import settings


def main() -> None:
	host = settings.get_finagent_host()
	port = settings.get_finagent_port()
	reload = settings.get_finagent_reload()

	uvicorn.run(
		"financial_agent.agent_api:app",
		host=host,
		port=port,
		reload=reload,
	)


if __name__ == "__main__":
	main()
