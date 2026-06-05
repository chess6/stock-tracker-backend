from pathlib import Path

from flask import Flask
from flask_cors import CORS

from .config import Config
from .db import ensure_parent_dir, init_app as init_db_app
from .routes.api import api_bp


def create_app(config: Config | None = None) -> Flask:
    app = Flask(__name__)
    app.config.from_mapping((config or Config()).to_flask_config())
    CORS(app)

    ensure_parent_dir(Path(app.config["DATABASE_PATH"]))
    init_db_app(app)
    app.register_blueprint(api_bp)
    return app
