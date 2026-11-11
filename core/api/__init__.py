from fastapi import FastAPI

from core.api.authentication import build_auth_router
from core._version import __version__
from core.websocket import asgi
from core.api.middlewares import SPAStaticFiles, setup_cors
from core.api.routers import pipelines, runs


API_PREFIX = "/api"

app = FastAPI(title="GD Backup", version=__version__, redirect_slashes=False)

# Mount the websocket before the other middlewares
# to avoid conflicts
app.mount("/ws", asgi, name="socket")

setup_cors(app)

app.include_router(pipelines.router, prefix=API_PREFIX)
app.include_router(runs.router, prefix=API_PREFIX)
app.include_router(build_auth_router(app), prefix=API_PREFIX)

app.mount("/", SPAStaticFiles(api_prefix=API_PREFIX))
