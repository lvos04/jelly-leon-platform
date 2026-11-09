"""Hub-simulator voor de live ontwerpreview van de statuspagina (alleen test, nooit in een image).

Start ``e2e/simulators/hub/hub_sim.py`` als eigen proces en voegt één testroute toe:
``POST /_review/intrekken-na-koppelen`` zet de simulator zo dat hij de dienst direct na een geslaagde enrollment
intrekt. Zo bereikt de echte dienst de toestand "ingetrokken" via zijn eigen code (bevestiging met ``services/self``
→ 401), zonder iets in de dienst te faken.
"""

from __future__ import annotations

import argparse
import sys
from collections.abc import Awaitable, Callable
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "e2e" / "simulators" / "hub"))

import uvicorn
from hub_sim import HubState, create_hub_app
from starlette.requests import Request
from starlette.responses import Response


def main() -> None:
    parser = argparse.ArgumentParser(description="Hub-simulator met intrekken na koppelen (alleen test)")
    parser.add_argument("--port", type=int, required=True)
    args = parser.parse_args()
    state = HubState()
    app = create_hub_app(state)
    intrekken = {"aan": False}

    @app.post("/_review/intrekken-na-koppelen")
    def zet_intrekken() -> dict[str, bool]:
        intrekken["aan"] = True
        return {"intrekken_na_koppelen": True}

    @app.middleware("http")
    async def na_enroll(request: Request, call_next: Callable[[Request], Awaitable[Response]]) -> Response:
        response = await call_next(request)
        if intrekken["aan"] and request.url.path == "/api/v1/services/enroll" and response.status_code < 300:
            for app_id in list(state.services):
                state.revoke(app_id)
        return response

    print(f"hub-review: sleutelset-vingerafdruk {state.keyset_fingerprint()}", flush=True)
    uvicorn.run(app, host="127.0.0.1", port=args.port, log_level="warning")


if __name__ == "__main__":
    main()
