from pymongo import MongoClient

from app.config import settings


client = MongoClient(settings.database_url)

db = client["kitsu"]