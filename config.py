import os

class config:
    DATABASE_URL = "postgresql+psycopg2://postgres:password@localhost:5432/handloombazaar" #os.getenv("DATABASE_CONNECTION_STRING","postgresql+psycopg://admin:password@localhost:5432/handloombazaar")