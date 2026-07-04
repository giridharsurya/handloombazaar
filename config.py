import os

class config:
    DATABASE_URL = "postgresql+psycopg2://postgres:password@localhost:5432/handloombazaar" #os.getenv("DATABASE_CONNECTION_STRING","postgresql+psycop://admin:password@localhost:5432/handloombazaar")
    JWT_SECRET_KEY = os.getenv("JWT_SECRET_KEY", os.getenv("JWT_SECRET", "your-secret-key-change-in-production"))
    JWT_ALGORITHM = os.getenv("JWT_ALGORITHM", "HS256")