import os

assert "NOTION_TOKEN" in os.environ, "NOTION_TOKEN env variable not found!"
NOTION_TOKEN = os.environ["NOTION_TOKEN"]
NOTION_DATABASE_ID = os.environ["NOTION_DATABASE_ID"]