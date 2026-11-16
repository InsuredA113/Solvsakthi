from pymongo import MongoClient
from pymongo.errors import PyMongoError
from .config import MONGODB_URI, MONGODB_DATABASE

_client = None

def get_database():
    global _client
    if _client is None:
        _client = MongoClient(MONGODB_URI, serverSelectionTimeoutMS=2000)
    return _client[MONGODB_DATABASE]

def database_is_available() -> bool:
    try:
        get_database().command("ping")
        return True
    except PyMongoError:
        return False
