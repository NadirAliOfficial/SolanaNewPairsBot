import asyncio
import re
import requests
import json
import os

from telegram import (
    Update,
    ReplyKeyboardMarkup,
    KeyboardButton,
    ReplyKeyboardRemove,
)
from telegram.ext import (
    ApplicationBuilder,
    CommandHandler,
    MessageHandler,
    filters,
    ContextTypes,
)
from ntscraper import Nitter
from telethon import TelegramClient
from telethon.tl.functions.channels import GetFullChannelRequest
from telethon.errors import ChannelInvalidError, ChannelPrivateError, UsernameNotOccupiedError
from dotenv import load_dotenv

# ------------------------------------------------------------------------------
# Environment Setup
# ------------------------------------------------------------------------------
load_dotenv()

API_ID = 23029837
API_HASH = "2cd4f5ead73424ff9a02da5f66de76eb"
SESSION_PATH = "session_name"  # Name of your pre-saved session file
TOKEN_FILE = "tokens.json"     # File where tokens are stored

# Initialize Telethon client
telethon_client = TelegramClient(SESSION_PATH, API_ID, API_HASH)

async def start_telethon():
    await telethon_client.start()
    print("Telethon client started successfully!")

asyncio.get_event_loop().run_until_complete(start_telethon())

# Nitter for Twitter stats
nitter_client = Nitter("https://nitter.privacydev.net")

# DexScreener endpoint
TOKEN_PROFILES_URL = "https://api.dexscreener.com/token-profiles/latest/v1"

# ------------------------------------------------------------------------------
# JSON File Helpers
# ------------------------------------------------------------------------------
def load_tokens_from_file() -> list:
    if os.path.exists(TOKEN_FILE):
        with open(TOKEN_FILE, "r") as f:
            try:
                return json.load(f)
            except json.JSONDecodeError:
                return []
    return []

def save_tokens_to_file(tokens: list):
    with open(TOKEN_FILE, "w") as f:
        json.dump(tokens, f, indent=2)

def append_token_to_file(token: dict):
    tokens = load_tokens_from_file()
    # Check if token_address already exists
    if any(t.get("token_address") == token.get("token_address") for t in tokens):
        return
    tokens.append(token)
    save_tokens_to_file(tokens)

# ------------------------------------------------------------------------------
# Helper Functions for Data Extraction
# ------------------------------------------------------------------------------
def parse_twitter_handle(twitter_url: str) -> str:
    if not twitter_url:
        return None
    base_url = twitter_url.split('/status/')[0]
    pattern = r"https?://(x\.com|twitter\.com)/([^/]+)"
    match = re.match(pattern, base_url)
    return match.group(2) if match else None

def get_twitter_followers(twitter_url: str) -> int:
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
    if not telegram_url:
        return 0
    pattern = r"https?://t\.me/([^/?]+)"
    match = re.match(pattern, telegram_url)
    if not match:
        return 0
    username = match.group(1)
    try:
        entity = await telethon_client.get_entity(username)
        if hasattr(entity, "broadcast") or hasattr(entity, "megagroup"):
            full_channel = await telethon_client(GetFullChannelRequest(entity))
            if hasattr(full_channel.full_chat, "participants_count"):
                return full_channel.full_chat.participants_count
            else:
                return 0
        if hasattr(entity, "participants_count"):
            return entity.participants_count
        return 0
    except (ChannelInvalidError, ChannelPrivateError, UsernameNotOccupiedError) as e:
        print(f"[Telethon Error] {e}")
    except Exception as e:
        print(f"[Unexpected Telethon Error] {e}")
    return 0

# ------------------------------------------------------------------------------
# Monitoring: Continuously Poll DexScreener
# ------------------------------------------------------------------------------
async def monitor_new_pairs(app, chat_id: int, context: ContextTypes.DEFAULT_TYPE):
    """
    Continuously poll DexScreener for new tokens.
    Store each new token uniquely (in memory and JSON file).
    Send the token if it matches the current filters.
    """
    seen_addresses = set()
    while True:
        try:
            resp = await asyncio.to_thread(requests.get, TOKEN_PROFILES_URL, timeout=10)
            resp.raise_for_status()
            profiles = resp.json()

            for p in profiles:
                token_addr = p.get("tokenAddress", "")
                if not token_addr or token_addr in seen_addresses:
                    continue

                seen_addresses.add(token_addr)
                chain_id = p.get("chainId", "").lower()  # Ensure lowercase for consistency
                links = p.get("links", [])

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

                telegram_members = await get_telegram_member_count(telegram_url)
                followers = await asyncio.to_thread(get_twitter_followers, twitter_url) if twitter_url else 0

                token_data = {
                    "chain_id": chain_id,
                    "token_address": token_addr,
                    "followers": followers,
                    "telegram_url": telegram_url or "N/A",
                    "telegram_members": telegram_members,
                    "twitter_url": twitter_url or "N/A",
                    "website_url": website_url or "N/A"
                }

                # Store token in memory and append to JSON file if new
                if "all_tokens" not in context.chat_data:
                    context.chat_data["all_tokens"] = []
                context.chat_data["all_tokens"].append(token_data)
                append_token_to_file(token_data)

                # Check filters (if any)
                cfilter = context.chat_data.get("chain_filter", None)
                ffilter = context.chat_data.get("follower_filter", 0)

                if cfilter and chain_id != cfilter:
                    continue
                if ffilter > 0 and followers < ffilter:
                    continue

                # Build message
                msg = (
                    f"<b>NEW TOKEN FOUND</b>\n\n"
                    f"• <b>Chain (ID):</b> {chain_id}\n"
                    f"• <b>Token Address:</b> <code>{token_addr}</code>\n"
                    f"• <b>Website:</b> {website_url or 'N/A'}\n"
                )
                if telegram_url:
                    msg += f"• <b>Telegram:</b> {telegram_url} (Members: {telegram_members})\n"
                else:
                    msg += "• <b>Telegram:</b> N/A\n"
                if twitter_url:
                    msg += f"• <b>Twitter:</b> <a href='{twitter_url}'>{twitter_url}</a> (Followers: {followers})\n"
                else:
                    msg += "• <b>Twitter:</b> N/A\n"
                msg += "\n----------------------------------------------"

                await app.bot.send_message(chat_id=chat_id, text=msg, parse_mode="HTML")

            await asyncio.sleep(2)
        except requests.exceptions.RequestException as e:
            print(f"[Request Error] {e}")
            await asyncio.sleep(2)
        except Exception as e:
            print(f"[Unexpected Error] {e}")
            await asyncio.sleep(3)

# ------------------------------------------------------------------------------
# Filtering Helpers
# ------------------------------------------------------------------------------
def apply_filter_to_tokens(tokens, chain_filter=None, follower_filter=0):
    result = tokens
    if chain_filter:
        result = [t for t in result if t["chain_id"] == chain_filter]
    if follower_filter > 0:
        result = [t for t in result if t["followers"] >= follower_filter]
    return result

async def resend_filtered_tokens(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    Load tokens from the JSON file, apply the current filters,
    and re-send only tokens that have not been re-sent previously.
    """
    all_tokens = load_tokens_from_file()
    cfilter = context.chat_data.get("chain_filter", None)
    ffilter = context.chat_data.get("follower_filter", 0)

    matched = apply_filter_to_tokens(all_tokens, cfilter, ffilter)

    # Use a set to track already-sent tokens in filtering
    if "sent_filtered" not in context.chat_data:
        context.chat_data["sent_filtered"] = set()

    new_tokens = [t for t in matched if t["token_address"] not in context.chat_data["sent_filtered"]]

    if not new_tokens:
        await update.message.reply_text("No new tokens match the current filter.")
    else:
        await update.message.reply_text(f"Found {len(new_tokens)} tokens matching your filters:")
        for t in new_tokens:
            msg = (
                f"• <b>Chain (ID):</b> {t['chain_id']}\n"
                f"• <b>Token Address:</b> {t['token_address']}\n"
                f"• <b>Website:</b> {t['website_url']}\n"
                f"• <b>Telegram:</b> {t['telegram_url']} (Members: {t['telegram_members']})\n"
                f"• <b>Twitter:</b> {t['twitter_url']} (Followers: {t['followers']})\n"
                "----------------------------------------------"
            )
            await update.message.reply_text(msg, parse_mode="HTML")
            context.chat_data["sent_filtered"].add(t["token_address"])

# ------------------------------------------------------------------------------
# Commands
# ------------------------------------------------------------------------------
async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    /start -> Initialize filters and start background monitoring.
    """
    chat_id = update.effective_chat.id

    context.chat_data["chain_filter"] = None
    context.chat_data["follower_filter"] = 0
    context.chat_data["all_tokens"] = []
    context.chat_data["sent_filtered"] = set()

    await update.message.reply_text(
        "Welcome! I will continuously fetch new tokens from multiple chains.\n"
        "All tokens will be stored and shown if they pass your filters.\n"
        "Use /filter any time to adjust filters (by chain or Twitter followers)."
    )

    # Start background monitoring as a task
    context.application.create_task(monitor_new_pairs(context.application, chat_id, context))

async def filter_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    /filter -> Show a permanent keyboard with chain/follower options.
    """
    chain_buttons = [
        KeyboardButton("ETH"), KeyboardButton("BSC"), KeyboardButton("Polygon"),
        KeyboardButton("Solana"), KeyboardButton("SUI"), KeyboardButton("ADA"),
        KeyboardButton("Ink"), KeyboardButton("Avalanche")
    ]
    follower_buttons = [
        KeyboardButton("Followers > 100"),
        KeyboardButton("Followers > 500"),
        KeyboardButton("Followers > 1000")
    ]
    utility_buttons = [
        KeyboardButton("Clear Filters"),
        KeyboardButton("Show Current Filtered"),
        KeyboardButton("Done")
    ]
    keyboard_layout = [
        chain_buttons,
        follower_buttons,
        utility_buttons
    ]
    reply_markup = ReplyKeyboardMarkup(keyboard_layout, resize_keyboard=True)
    explanation = (
        "Use these buttons to filter the tokens stored:\n"
        "- Pick a chain (e.g. Solana) to filter only that chain.\n"
        "- Pick a follower filter (e.g. Followers > 500) to see tokens with at least that many followers.\n"
        "- Press 'Clear Filters' to remove all filters.\n"
        "- Press 'Show Current Filtered' to re-send tokens that match the current filters.\n"
        "- Press 'Done' to hide the keyboard.\n"
        "All future tokens are also filtered in real-time based on your settings."
    )
    await update.message.reply_text(explanation, reply_markup=reply_markup)

# ------------------------------------------------------------------------------
# Filter Selection Handler
# ------------------------------------------------------------------------------
async def filter_selection_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_text = update.message.text.strip().lower()

    if "chain_filter" not in context.chat_data:
        context.chat_data["chain_filter"] = None
    if "follower_filter" not in context.chat_data:
        context.chat_data["follower_filter"] = 0

    # Chain selection using textual chain names
    chain_options = {
        "eth": "ethereum",
        "ethereum": "ethereum",
        "bsc": "bsc",
        "polygon": "polygon",
        "solana": "solana",
        "sui": "sui",
        "ada": "ada",
        "avalanche": "avalanche",
        "ink": "ink"
    }
    if user_text in chain_options:
        selected_chain = chain_options[user_text]
        context.chat_data["chain_filter"] = selected_chain
        await update.message.reply_text(f"Chain filter set to {selected_chain.upper()}.")
    elif user_text.startswith("followers >"):
        try:
            threshold = int(user_text.split(">")[1].strip())
            context.chat_data["follower_filter"] = threshold
            await update.message.reply_text(f"Twitter follower filter set to > {threshold}.")
        except ValueError:
            await update.message.reply_text("Could not parse follower filter. Try again.")
    elif user_text == "clear filters":
        context.chat_data["chain_filter"] = None
        context.chat_data["follower_filter"] = 0
        context.chat_data["sent_filtered"] = set()  # Reset already sent filtered tokens
        await update.message.reply_text("All filters cleared.")
    elif user_text == "show current filtered":
        await resend_filtered_tokens(update, context)
    elif user_text == "done":
        await update.message.reply_text(
            "Filter menu hidden. Type /filter to open again.",
            reply_markup=ReplyKeyboardRemove()
        )
    else:
        await update.message.reply_text("Please choose a valid filter option or press 'Done'.")

# ------------------------------------------------------------------------------
# Main
# ------------------------------------------------------------------------------
def main():
    import os   
    token = os.getenv("BOT_TOKEN")
    app = ApplicationBuilder().token(token).build()

    app.add_handler(CommandHandler("start", start_command))
    app.add_handler(CommandHandler("filter", filter_command))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, filter_selection_handler))

    print("Bot is running...")
    app.run_polling()

if __name__ == "__main__":
    main()
