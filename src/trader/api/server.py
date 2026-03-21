import uvicorn

from trader.api.app import create_app
from trader.config import Settings


def main() -> None:
    settings = Settings()
    application = create_app(settings)
    uvicorn.run(
        application,
        host=settings.api_host,
        port=settings.api_port,
    )


if __name__ == "__main__":
    main()
