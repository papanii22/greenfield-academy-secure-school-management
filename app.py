from flask import Flask

from config import Config


def create_app() -> Flask:
    app = Flask(__name__)
    app.config.from_object(Config)

    @app.get("/")
    def index() -> str:
        return "Greenfield Academy Secure School Management System"

    return app


app = create_app()


if __name__ == "__main__":
    app.run(debug=app.config["FLASK_DEBUG"])
