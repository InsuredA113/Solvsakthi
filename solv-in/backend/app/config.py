import os
from pathlib import Path
from dotenv import load_dotenv

load_dotenv(Path(__file__).resolve().parents[1] / ".env")
MONGODB_URI = os.getenv("MONGODB_URI", "mongodb://localhost:27017")
MONGODB_DATABASE = os.getenv("MONGODB_DATABASE", "solvin_claims")
CORS_ORIGINS = [value.strip() for value in os.getenv("CORS_ORIGINS", "http://localhost:4200").split(",") if value.strip()]
