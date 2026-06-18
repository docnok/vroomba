"""Entry point: python -m vroomba"""

import logging
import webbrowser

import uvicorn
from dotenv import load_dotenv

load_dotenv()

from vroomba.config import settings

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
    datefmt="%H:%M:%S",
)


def main():
    url = f"http://{settings.server_host}:{settings.server_port}"
    print(f"\n  🚗 Vroomba starting at {url}\n")

    # Open browser after a short delay (server needs to start first)
    import threading

    threading.Timer(1.5, lambda: webbrowser.open(url)).start()

    uvicorn.run(
        "vroomba.server:app",
        host=settings.server_host,
        port=settings.server_port,
        log_level="info",
    )


if __name__ == "__main__":
    main()
