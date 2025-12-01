import requests
import os
import sys
from datetime import datetime

# Get the directory of the calling script
caller_dir = os.path.dirname(os.path.abspath(sys.argv[0]))

# Attempt to import Telegram credentials from the caller's directory
try:
    # Temporarily add caller's directory to sys.path
    sys.path.append(caller_dir)
    from telegram_credentials import TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID
except ImportError as e:
    print(f"[{datetime.now()}] Failed to import from {caller_dir}/telegram_credentials: {e}")
    TELEGRAM_BOT_TOKEN = None
    TELEGRAM_CHAT_ID = None
finally:
    # Clean up sys.path to avoid side effects
    if caller_dir in sys.path:
        sys.path.remove(caller_dir)

def send_telegram_alert(message, bot_token=None, chat_id=None):
    """
    Send a message to a Telegram chat using a bot.
    
    Args:
        message (str): The message to send.
        bot_token (str, optional): Telegram bot token. Defaults to TELEGRAM_BOT_TOKEN from telegram_credentials.
        chat_id (str, optional): Telegram chat ID. Defaults to TELEGRAM_CHAT_ID from telegram_credentials.
    
    Returns:
        bool: True if the message was sent successfully, False otherwise.
    """
    # Use provided credentials or fall back to imported ones
    bot_token = bot_token or TELEGRAM_BOT_TOKEN
    chat_id = chat_id or TELEGRAM_CHAT_ID
    
    # Check if credentials are available
    if not bot_token or not chat_id:
        print(f"[{datetime.now()}] Telegram alert failed: Missing TELEGRAM_BOT_TOKEN or TELEGRAM_CHAT_ID in {caller_dir}/telegram_credentials.py.")
        return False
    
    # Telegram API endpoint
    url = f"https://api.telegram.org/bot{bot_token}/sendMessage"
    
    # Payload for the POST request
    payload = {
        "chat_id": chat_id,
        "text": message,
        "parse_mode": "Markdown"  # Optional: for formatting
    }
    
    try:
        response = requests.post(url, json=payload, timeout=10)
        response.raise_for_status()  # Raise an exception for 4xx/5xx errors
        if response.json().get("ok"):
            print(f"[{datetime.now()}] Telegram alert sent successfully: {message}")
            return True
        else:
            print(f"[{datetime.now()}] Telegram alert failed: {response.json().get('description', 'Unknown error')}")
            return False
    except requests.RequestException as e:
        print(f"[{datetime.now()}] Telegram alert failed due to network error: {e}")
        return False