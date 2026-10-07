import uvicorn

from council.config import settings


def main() -> None:
    uvicorn.run("council.webhook.handler:app", host="0.0.0.0", port=settings.webhook_port)


if __name__ == "__main__":
    main()
