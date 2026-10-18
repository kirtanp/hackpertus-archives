# Superset configuration for the Docker route (deploy/superset/docker-compose.yml).
#
# Committed on purpose: it holds no secret. The secret key comes from the environment, and the metadata
# database is the Postgres container the compose file starts.
import os

# Set SUPERSET_SECRET_KEY in the compose environment (or a .env file next to the compose file, never committed).
SECRET_KEY = os.environ["SUPERSET_SECRET_KEY"]

# Superset's own metadata: the Postgres service of the compose file, not the SQLite file of the local route.
SQLALCHEMY_DATABASE_URI = os.environ.get("SUPERSET_METADATA_DB", "postgresql+psycopg2://superset:superset@metadata-db:5432/superset")

# Celery/Redis for the compose stack; the prototype charts run synchronously.
REDIS_HOST = "redis"
REDIS_PORT = 6379
CELERY_BROKER_URL = "redis://redis:6379/0"
CELERY_RESULT_BACKEND = "redis://redis:6379/0"

# The prototype ships its own dashboard export; do not load Superset's examples, and no telemetry.
SUPERSET_LOAD_EXAMPLES = False
SCARF_ANALYTICS = False

# The compose stack binds 127.0.0.1 only and serves plain HTTP, so Talisman's HTTPS/CSP defaults are off here.
TALISMAN_ENABLED = False
