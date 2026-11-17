import logging
import re

from pymongo import MongoClient
from pymongo.errors import PyMongoError

from .config import MONGODB_URI, MONGODB_DATABASE

logger = logging.getLogger(__name__)
_client = None


def get_database():
    global _client
    if _client is None:
        _client = MongoClient(MONGODB_URI, serverSelectionTimeoutMS=10000)
    return _client[MONGODB_DATABASE]


def database_is_available() -> bool:
    try:
        get_database().command("ping")
        return True
    except PyMongoError as exc:
        # Keep credentials/connection strings out of application logs.
        safe_message = re.sub(
            r"mongodb(?:\+srv)?://[^\s]+",
            "[REDACTED_MONGODB_URI]",
            str(exc),
            flags=re.IGNORECASE,
        )
        logger.warning(
            "MongoDB health check failed (%s): %s",
            type(exc).__name__,
            safe_message[:1200],
        )
        return False
