from .api import create_app
from .links import add_link_routes

app = create_app()
add_link_routes(app)
