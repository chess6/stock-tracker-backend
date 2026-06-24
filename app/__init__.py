from pathlib import Path

from flask import Flask
from flask_cors import CORS

from .config import Config
from .db import ensure_parent_dir, init_app as init_db_app
from .logging_config import setup_logging
from .middleware import register_request_logging
from .routes.api import api_bp
from .routes.research import research_bp
from .routes.signals import signals_bp


def create_app(config: Config | None = None) -> Flask:
    setup_logging()

    app = Flask(__name__)
    app.config.from_mapping((config or Config()).to_flask_config())
    CORS(app)

    register_request_logging(app)

    ensure_parent_dir(Path(app.config["DATABASE_PATH"]))
    init_db_app(app)
    app.register_blueprint(api_bp)
    app.register_blueprint(research_bp)
    app.register_blueprint(signals_bp)
    return app
