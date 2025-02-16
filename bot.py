#!/usr/bin/env python3
import asyncio
import re
import os
import requests

from ntscraper import Nitter
from telethon import TelegramClient
from telethon.tl.functions.channels import GetFullChannelRequest
from telethon.errors import ChannelInvalidError, ChannelPrivateError, UsernameNotOccupiedError

from telegram import Update
from telegram.ext import ApplicationBuilder, CommandHandler, ContextTypes
from dotenv import load_dotenv

# ------------------------------------------------------------------------------
# Environment Setup and Asynchronous Telethon Client Initialization
# ------------------------------------------------------------------------------

load_dotenv()

# Telegram API credentials (for Telethon) from .env file
API_ID = 23029837
API_HASH = "2cd4f5ead73424ff9a02da5f66de76eb"
SESSION_PATH = 'session_name'  # Name of your pre-saved session file

# Initialize the asynchronous Telethon client
telethon_client = TelegramClient(SESSION_PATH, API_ID, API_HASH)

async def start_telethon():
    await telethon_client.start()
    print("Telethon client started successfully!")

# Start Telethon before the bot starts (blocking until connected)
asyncio.get_event_loop().run_until_complete(start_telethon())

# Create a global Nitter client (for Twitter data)
# ⚠️ Try changing the instance URL if you experience rate limiting.
nitter_client = Nitter("https://nitter.privacydev.net")

# API endpoint for token profiles
TOKEN_PROFILES_URL = "https://api.dexscreener.com/token-profiles/latest/v1"

# ------------------------------------------------------------------------------
# Helper Functions
# ------------------------------------------------------------------------------

def parse_twitter_handle(twitter_url: str) -> str:
    """
    Extract the Twitter handle from a given URL.
    For example: "https://twitter.com/example/status/123" returns "example".
    """
    base_url = twitter_url.split('/status/')[0]
    pattern = r"https?://(x\.com|twitter\.com)/([^/]+)"
    match = re.match(pattern, base_url)
    return match.group(2) if match else None

def get_twitter_followers(twitter_url: str) -> int:
    """
    Retrieve the Twitter follower count using Nitter.
    This function is blocking so we run it via asyncio.to_thread.
    """
    handle = parse_twitter_handle(twitter_url)
    if not handle:
        return 0
    try:
        # Ensure a trailing slash for consistency
        profile = nitter_client.get_profile_info(handle + "/")
        if profile and 'stats' in profile:
            return profile['stats'].get('followers', 0)
    except Exception as e:
        print(f"[Nitter Error] {e}")
    return 0

async def get_telegram_member_count(telegram_url: str) -> int:
    """
    Retrieve the Telegram member count for a given Telegram channel/group URL.
    Uses GetFullChannelRequest for channels/supergroups.
    This function is fully asynchronous.
    """
    pattern = r"https?://t\.me/([^/?]+)"
    match = re.match(pattern, telegram_url)
    if not match:
        print(f"[Error] Invalid Telegram URL: {telegram_url}")
        return 0
    username = match.group(1)
    try:
        # Use Telethon's async API to get the entity
        entity = await telethon_client.get_entity(username)
        
        # For channels or supergroups, request full details
        if hasattr(entity, 'broadcast') or hasattr(entity, 'megagroup'):
            full_channel = await telethon_client(GetFullChannelRequest(entity))
            if hasattr(full_channel.full_chat, 'participants_count'):
                return full_channel.full_chat.participants_count
            else:
                print(f"[Warning] 'participants_count' not found in full_chat for {telegram_url}")
                return 0
        # Fallback: if the entity itself has a participants_count attribute
        if hasattr(entity, 'participants_count'):
            return entity.participants_count
        else:
            print(f"[Warning] 'participants_count' not found for {telegram_url}")
            return 0

    except (ChannelInvalidError, ChannelPrivateError, UsernameNotOccupiedError) as e:
        print(f"[Telethon Error] {e} for URL: {telegram_url}")
    except Exception as e:
        print(f"[Unexpected Telethon Error] {e} for URL: {telegram_url}")
    return 0

# ------------------------------------------------------------------------------
# Asynchronous Monitoring Function
# ------------------------------------------------------------------------------

async def monitor_new_pairs(app, chat_id: int):
    """
    Continuously polls the TOKEN_PROFILES_URL endpoint for new token profiles.
    When a new token is detected, it extracts the website, Twitter, and Telegram URLs,
    retrieves the Twitter follower count (via a background thread) and Telegram member count,
    and then sends a formatted alert message to the specified Telegram chat.
    """
    seen_addresses = set()

    while True:
        try:
            # Run the blocking HTTP request in a background thread
            response = await asyncio.to_thread(requests.get, TOKEN_PROFILES_URL, timeout=10)
            response.raise_for_status()
            profiles = response.json()

            for profile in profiles:
                token_address = profile.get("tokenAddress", "")
                if token_address not in seen_addresses:
                    seen_addresses.add(token_address)
                    
                    chain_id    = profile.get("chainId", "")
                    description = profile.get("description", "")
                    links       = profile.get("links", [])

                    website_url  = None
                    twitter_url  = None
                    telegram_url = None

                    # Extract URLs from the token profile links
                    for link in links:
                        link_type  = link.get("type", "").lower()
                        link_label = link.get("label", "").lower()
                        url        = link.get("url", "")
                        if "website" in link_type or "website" in link_label:
                            website_url = url
                        elif "twitter" in link_type or "twitter" in link_label:
                            twitter_url = url
                        elif "telegram" in link_type or "telegram" in link_label:
                            telegram_url = url

                    # Retrieve Twitter follower count (run blocking call in a thread)
                    followers = await asyncio.to_thread(get_twitter_followers, twitter_url) if twitter_url else 0

                    # Retrieve Telegram member count using the async Telethon function
                    telegram_members = 0
                    if telegram_url:
                        telegram_members = await get_telegram_member_count(telegram_url)

                    # Build the alert message using HTML formatting
                    message = (
                        f"<b>NEW TOKEN FOUND</b>\n\n"
                        f"• <b>Chain:</b> {chain_id}\n"
                        f"• <b>Token Address:</b> <code>{token_address}</code>\n"
                        f"• <b>Website:</b> {website_url or 'N/A'}\n"
                    )
                    if telegram_url:
                        message += f"• <b>Telegram:</b> {telegram_url} (Members: {telegram_members})\n"
                    else:
                        message += "• <b>Telegram:</b> N/A\n"
                    if twitter_url:
                        message += f"• <b>Twitter:</b> <a href='{twitter_url}'>{twitter_url}</a> (Followers: {followers})\n"
                    else:
                        message += "• <b>Twitter:</b> N/A\n"
                    message += "\n----------------------------------------------"

                    # Send the alert message to the chat
                    await app.bot.send_message(chat_id=chat_id, text=message, parse_mode="HTML")

            await asyncio.sleep(3)

        except requests.exceptions.RequestException as e:
            print(f"[Request Error] {e}")
            await asyncio.sleep(5)
        except Exception as e:
            print(f"[Unexpected Error] {e}")
            await asyncio.sleep(5)

# ------------------------------------------------------------------------------
# Telegram Bot Command Handler
# ------------------------------------------------------------------------------

async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    Handles the /start command.
    Replies to the user and starts the background monitoring task.
    """
    chat_id = update.effective_chat.id
    await update.message.reply_text("Monitoring started! You will receive new token alerts here.")
    context.application.create_task(monitor_new_pairs(context.application, chat_id))

# ------------------------------------------------------------------------------
# Main: Run the Telegram Bot
# ------------------------------------------------------------------------------

def main():
    token = "8141585234:AAF7SpJPDpvmQGmkRmhUhxCIpxNs0soQQYI"
    app = ApplicationBuilder().token(token).build()
    app.add_handler(CommandHandler("start", start_command))
    app.run_polling()

if __name__ == "__main__":
    main()
