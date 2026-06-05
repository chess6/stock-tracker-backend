import os

from dotenv import load_dotenv

from app import create_app


load_dotenv()
app = create_app()


if __name__ == "__main__":
    debug = os.getenv("FLASK_DEBUG", "1").lower() in {"1", "true", "yes"}
    app.run(debug=debug, use_reloader=debug)
