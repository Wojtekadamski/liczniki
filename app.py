from flask import Flask, flash, redirect, url_for
from flask_migrate import Migrate
from werkzeug.exceptions import InternalServerError, NotFound, Forbidden, Unauthorized

from src.config import Config
from src.error_handlers import handle_internal_server_error, handle_not_found_error, handle_forbidden_error, \
    handle_unauthorized_error
from src.models import db,  login_manager,create_admin, create_user_test
from src.routes import main_routes


app = Flask(__name__, static_url_path='', static_folder='static', template_folder='templates')
app.config.from_object(Config)
migrate = Migrate(app, db)
db.init_app(app)
with app.app_context():
    db.create_all()
    create_admin()
    create_user_test()
    # create_app()
app.register_blueprint(main_routes, url_prefix='/')
login_manager.init_app(app)

app.register_error_handler(InternalServerError, handle_internal_server_error)
app.register_error_handler(NotFound, handle_not_found_error)
app.register_error_handler(Forbidden, handle_forbidden_error)
app.register_error_handler(Unauthorized, handle_unauthorized_error)

if __name__ == '__main__':
    app.run()
