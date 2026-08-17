"""Run the FastAPI local control plane through Uvicorn."""

from __future__ import annotations

import uvicorn

from src.config import get_settings


def main() -> None:
    """Launch the local API with the configured loopback host and port."""

    settings = get_settings()
    uvicorn.run(
        "apps.api.app:create_app",
        host=settings.web_api_host,
        port=settings.web_api_port,
        factory=True,
        reload=False,
        log_level=settings.log_level.lower(),
    )


if __name__ == "__main__":
    main()
