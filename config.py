import os
from dotenv import load_dotenv

load_dotenv()


class config:
    DATABASE_URL = os.getenv(
        "DATABASE_CONNECTION_STRING",
        "postgresql+psycopg2://handloombazaar:StrongPassword123!@handloombazaar-dev-server.postgres.database.azure.com:5432/handloombazaar?sslmode=require",
    )
    JWT_SECRET_KEY = os.getenv("JWT_SECRET_KEY", os.getenv("JWT_SECRET", "your-secret-key-change-in-production"))
    JWT_ALGORITHM = os.getenv("JWT_ALGORITHM", "HS256")