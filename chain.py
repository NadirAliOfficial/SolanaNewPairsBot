#!/usr/bin/env python3
import asyncio
import re
import os
import requests

from ntscraper import Nitter
from telethon import TelegramClient
from telethon.tl.functions.channels import GetFullChannelRequest
from telethon.errors import ChannelInvalidError, ChannelPrivateError, UsernameNotOccupiedError

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    ApplicationBuilder,
    CommandHandler,
    ContextTypes,
    CallbackQueryHandler,
)
from dotenv import load_dotenv

# ------------------------------------------------------------------------------
# Environment Setup and Asynchronous Telethon Client Initialization
# ------------------------------------------------------------------------------

load_dotenv()

# Telegram API credentials (for Telethon) from .env file
API_ID = int(os.getenv('API_ID'))
API_HASH = os.getenv('API_HASH')
SESSION_PATH = 'session_name'  # Name of your pre-saved session file

# Initialize the asynchronous Telethon client
telethon_client = TelegramClient(SESSION_PATH, API_ID, API_HASH)

async def start_telethon():
    await telethon_client.start()
    print("Telethon client started successfully!")

# Start Telethon before the bot starts (blocking until connected)
asyncio.get_event_loop().run_until_complete(start_telethon())

# Create a global Nitter client (for Twitter data)
nitter_client = Nitter("https://nitter.privacydev.net")

# API endpoint for token profiles
TOKEN_PROFILES_URL = "https://api.dexscreener.com/token-profiles/latest/v1"

# ------------------------------------------------------------------------------
# Global Variables for Dynamic Chain Filtering
# ------------------------------------------------------------------------------

# We store two pieces of data per chat:
#  - chat_all_chains: the list of dynamic chain IDs (fetched from the API)
#  - chat_chain_filters: the set of chains that are enabled for that chat.
#    When a chain is “set” via the /chain command, it becomes the only enabled chain.
chat_all_chains = {}      # { chat_id: [chain1, chain2, ...] }
chat_chain_filters = {}   # { chat_id: {chain1, chain2, ...} }

# ------------------------------------------------------------------------------
# Helper Function: Fetch Available Chains from the API
# ------------------------------------------------------------------------------

async def fetch_available_chains() -> list:
    """
    Fetch a list of available chains dynamically from the API.
    We do this by fetching token profiles and extracting unique chain IDs.
    """
    try:
        # Run blocking requests.get in a background thread
        response = await asyncio.to_thread(requests.get, TOKEN_PROFILES_URL, timeout=10)
        response.raise_for_status()
        profiles = response.json()
        chains = set()
        for profile in profiles:
            chain_id = profile.get("chainId", "").upper()
            if chain_id:
                chains.add(chain_id)
        return sorted(chains)
    except Exception as e:
        print(f"[Error fetching chains] {e}")
        return []

# ------------------------------------------------------------------------------
# Inline Keyboard Builders
# ------------------------------------------------------------------------------

def build_toggle_keyboard(chat_id: int) -> InlineKeyboardMarkup:
    """
    Build an inline keyboard that lets users toggle chains (multi-select).
    Each button shows a check (✅) if the chain is enabled, otherwise a cross (❌).
    This is used by the /start command.
    """
    available_chains = chat_all_chains.get(chat_id, [])
    # Default to all chains enabled if not already set.
    enabled_chains = chat_chain_filters.get(chat_id, set(available_chains))
    buttons = []
    row = []
    for chain in available_chains:
        text = f"{chain} {'✅' if chain in enabled_chains else '❌'}"
        row.append(InlineKeyboardButton(text=text, callback_data=f"toggle:{chain}"))
        if len(row) == 2:
            buttons.append(row)
            row = []
    if row:
        buttons.append(row)
    buttons.append([InlineKeyboardButton(text="Done", callback_data="done")])
    return InlineKeyboardMarkup(buttons)

def build_chain_selection_keyboard(chat_id: int) -> InlineKeyboardMarkup:
    """
    Build an inline keyboard for exclusive chain selection.
    Each button represents one chain (fetched dynamically) so that when the user
    selects it, that chain becomes the only one enabled.
    """
    available_chains = chat_all_chains.get(chat_id, [])
    buttons = []
    row = []
    for chain in available_chains:
        row.append(InlineKeyboardButton(text=chain, callback_data=f"select:{chain}"))
        if len(row) == 2:
            buttons.append(row)
            row = []
    if row:
        buttons.append(row)
    # Optionally add a cancel button.
    buttons.append([InlineKeyboardButton(text="Cancel", callback_data="cancel_select")])
    return InlineKeyboardMarkup(buttons)

# ------------------------------------------------------------------------------
# Helper Functions: Twitter & Telegram Data Retrieval
# ------------------------------------------------------------------------------

def parse_twitter_handle(twitter_url: str) -> str:
    """
    Extract the Twitter handle from a given URL.
    """
    base_url = twitter_url.split('/status/')[0]
    pattern = r"https?://(x\.com|twitter\.com)/([^/]+)"
    match = re.match(pattern, base_url)
    return match.group(2) if match else None

def get_twitter_followers(twitter_url: str) -> int:
    """
    Retrieve Twitter follower count using Nitter.
    This blocking function is run via asyncio.to_thread.
    """
    handle = parse_twitter_handle(twitter_url)
    if not handle:
        return 0
    try:
        profile = nitter_client.get_profile_info(handle + "/")
        if profile and 'stats' in profile:
            return profile['stats'].get('followers', 0)
    except Exception as e:
        print(f"[Nitter Error] {e}")
    return 0

async def get_telegram_member_count(telegram_url: str) -> int:
    """
    Retrieve the Telegram member count for a given channel/group URL.
    """
    pattern = r"https?://t\.me/([^/?]+)"
    match = re.match(pattern, telegram_url)
    if not match:
        print(f"[Error] Invalid Telegram URL: {telegram_url}")
        return 0
    username = match.group(1)
    try:
        entity = await telethon_client.get_entity(username)
        if hasattr(entity, 'broadcast') or hasattr(entity, 'megagroup'):
            full_channel = await telethon_client(GetFullChannelRequest(entity))
            if hasattr(full_channel.full_chat, 'participants_count'):
                return full_channel.full_chat.participants_count
            else:
                print(f"[Warning] 'participants_count' not found in full_chat for {telegram_url}")
                return 0
        if hasattr(entity, 'participants_count'):
            return entity.participants_count
        else:
            print(f"[Warning] 'participants_count' not found for {telegram_url}")
            return 0
    except Exception as e:
        print(f"[Telethon Error] {e} for URL: {telegram_url}")
        return 0

# ------------------------------------------------------------------------------
# Asynchronous Monitoring Function (Using Chain Filter)
# ------------------------------------------------------------------------------

async def monitor_new_pairs(app, chat_id: int):
    """
    Continuously polls the TOKEN_PROFILES_URL endpoint for new token profiles.
    Only sends tokens whose chain is in the chat's current chain filter.
    """
    seen_addresses = set()
    while True:
        try:
            response = await asyncio.to_thread(requests.get, TOKEN_PROFILES_URL, timeout=10)
            response.raise_for_status()
            profiles = response.json()

            for profile in profiles:
                token_address = profile.get("tokenAddress", "")
                if token_address not in seen_addresses:
                    seen_addresses.add(token_address)
                    chain_id = profile.get("chainId", "").upper()

                    # Check against the chat's filter (if set)
                    if chat_id in chat_chain_filters and chain_id not in chat_chain_filters[chat_id]:
                        continue

                    links = profile.get("links", [])
                    website_url  = None
                    twitter_url  = None
                    telegram_url = None

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

                    followers = await asyncio.to_thread(get_twitter_followers, twitter_url) if twitter_url else 0
                    telegram_members = await get_telegram_member_count(telegram_url) if telegram_url else 0

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

                    await app.bot.send_message(chat_id=chat_id, text=message, parse_mode="HTML")
            await asyncio.sleep(3)
        except Exception as e:
            print(f"[Unexpected Error] {e}")
            await asyncio.sleep(5)

# ------------------------------------------------------------------------------
# Callback Query Handler for Inline Buttons
# ------------------------------------------------------------------------------

async def chain_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    Handle inline keyboard callbacks.
    Depending on the callback data prefix:
      - "toggle:" handles multi-select toggling (from /start)
      - "select:" handles exclusive chain selection (from /chain)
      - "done" or "cancel_select" dismiss the keyboard.
    """
    query = update.callback_query
    await query.answer()
    chat_id = query.message.chat.id
    data = query.data

    if data.startswith("toggle:"):
        chain = data.split(":", 1)[1]
        # Initialize filter if needed (default to all chains)
        if chat_id not in chat_chain_filters:
            chat_chain_filters[chat_id] = set(chat_all_chains.get(chat_id, []))
        if chain in chat_chain_filters[chat_id]:
            chat_chain_filters[chat_id].remove(chain)
        else:
            chat_chain_filters[chat_id].add(chain)
        keyboard = build_toggle_keyboard(chat_id)
        await query.edit_message_reply_markup(reply_markup=keyboard)

    elif data.startswith("select:"):
        # Exclusive chain selection from /chain command.
        chain = data.split(":", 1)[1]
        chat_chain_filters[chat_id] = {chain}
        await query.edit_message_text(text=f"Chain set to {chain}. You will now receive updates for {chain} only.")

    elif data == "cancel_select":
        await query.edit_message_text(text="Chain selection cancelled.")

    elif data == "done":
        await query.edit_message_reply_markup(reply_markup=None)

# ------------------------------------------------------------------------------
# Telegram Bot Command Handlers
# ------------------------------------------------------------------------------

async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    /start command:
      - Fetches the available chains,
      - Initializes the chain filter (defaulting to all chains),
      - Sends an inline keyboard that lets the user toggle chains (multi-select),
      - And starts the monitoring task.
    """
    chat_id = update.effective_chat.id
    available_chains = await fetch_available_chains()
    if not available_chains:
        await update.message.reply_text("Could not fetch available chains. Please try again later.")
        return
    chat_all_chains[chat_id] = available_chains
    # By default, enable all chains.
    chat_chain_filters[chat_id] = set(available_chains)

    # keyboard = build_toggle_keyboard(chat_id)
    # await update.message.reply_text("Select the chains you want to receive updates for:", reply_markup=keyboard)
    await update.message.reply_text("Monitoring started! You will receive new token alerts here. Press Command /chain To filter new pairs ")
    context.application.create_task(monitor_new_pairs(context.application, chat_id))
 
async def chain_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    /chain command:
      - Fetches the available chains,
      - Sends an inline keyboard where each button represents one chain.
        When a user taps a button, that chain becomes the sole chain for alerts.
    """
    chat_id = update.effective_chat.id
    available_chains = await fetch_available_chains()
    if not available_chains:
        await update.message.reply_text("Could not fetch available chains. Please try again later.")
        return
    chat_all_chains[chat_id] = available_chains
    # (Optional) Set a default if desired; here we wait for the user to select.
    keyboard = build_chain_selection_keyboard(chat_id)
    await update.message.reply_text("Select the chain for updates:", reply_markup=keyboard)

# ------------------------------------------------------------------------------
# Main: Run the Telegram Bot
# ------------------------------------------------------------------------------

def main():
    token = os.getenv('BOT_TOKEN')
    app = ApplicationBuilder().token(token).build()

    # Register command and callback query handlers.
    app.add_handler(CommandHandler("start", start_command))
    app.add_handler(CommandHandler("chain", chain_command))
    app.add_handler(CallbackQueryHandler(chain_callback))

    app.run_polling()

if __name__ == "__main__":
    main()
