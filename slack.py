import os
from slack_bolt import App
from slack_bolt.adapter.socket_mode import SocketModeHandler
from slack_sdk import WebClient
import logging
from chat import Chat
from dotenv import load_dotenv

load_dotenv()
# logging.basicConfig(level=logging.DEBUG)

if os.getenv("SLACK_BOT_TOKEN") is None or os.getenv("SLACK_BOT_TOKEN") == "":
    raise Exception("SLACK_BOT_TOKEN is not set")

if os.getenv("SLACK_APP_TOKEN") is None or os.getenv("SLACK_APP_TOKEN") == "":
    raise Exception("SLACK_APP_TOKEN is not set")

if os.getenv("SLACK_SIGNING_SECRET") is None or os.getenv("SLACK_SIGNING_SECRET") == "":
    raise Exception("SLACK_SIGNING_SECRET is not set")

SLACK_BOT_TOKEN = os.getenv("SLACK_BOT_TOKEN")
SLACK_APP_TOKEN = os.getenv("SLACK_APP_TOKEN")
SLACK_SIGNING_SECRET = os.getenv("SLACK_SIGNING_SECRET")

chat = Chat('slack')

# Initialize your app with your bot token and socket mode handler
app = App(
    token=SLACK_BOT_TOKEN,
    signing_secret=SLACK_SIGNING_SECRET
)


def get_new_bot_token():
    client = WebClient(token=SLACK_APP_TOKEN)
    try:
        response = client.auth_test()
        bot_token = response['bot_user_id']
        return bot_token
    except Exception as e:
        print(f"Error fetching new bot token: {e.response['error']}")
        return None

# app mention from another channel
@app.event("app_mention")
def handle_app_mention_events(body, say, logger):
    logger.info(body)
    event = body.get("event", {})
    text = event.get("text", "")
    user = event.get("user", "")
    channel = event.get("channel", "")

    if text:
        response = chat.query(text)
        say(response)

# direct message to the app
@app.event("message")
def handle_message_events(body, logger, say):
    logger.info(body)
    event = body.get("event", {})
    text = event.get("text", "")
    user = event.get("user", "")
    channel = event.get("channel", "")

    if text:
        response = chat.query(text)
        say(response)

if __name__ == "__main__":
    # Run your app in Socket Mode
    handler = SocketModeHandler(app, SLACK_APP_TOKEN)
    handler.start()