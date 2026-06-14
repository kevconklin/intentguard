"""One-shot bootstrap: create an OpenFGA store + write the authorization model.

Usage:
    docker compose up -d
    python -m engine.pdp.bootstrap            # uses http://localhost:8080
    python -m engine.pdp.bootstrap http://localhost:8080

Prints the store id and authorization model id. Export them so the engine uses
the OpenFGA backend:
    export INTENTGUARD_BACKEND=openfga
    export INTENTGUARD_OPENFGA_STORE_ID=<store_id>
    export INTENTGUARD_OPENFGA_MODEL_ID=<model_id>
"""

from __future__ import annotations

import asyncio
import sys

from engine.pdp.openfga import bootstrap


async def _main(api_url: str) -> None:
    store_id, model_id = await bootstrap(api_url)
    print(f"INTENTGUARD_OPENFGA_STORE_ID={store_id}")
    print(f"INTENTGUARD_OPENFGA_MODEL_ID={model_id}")


if __name__ == "__main__":
    url = sys.argv[1] if len(sys.argv) > 1 else "http://localhost:8080"
    asyncio.run(_main(url))
