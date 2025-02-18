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

                dex_link = f"https://dexscreener.com/{chain_id.lower()}/{token_addr}"
                # Using a divider made of Unicode block elements for a clean look.
                divider = "\n━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"

                msg = (
                    "<b>✨ NEW TOKEN ALERT ✨</b>\n" + 
                    f"<b>Chain:</b> <code>{chain_id}</code>\n" +
                    f"<b>Token Address:</b> <code>{token_addr}</code>\n" +
                    f"<b>Website:</b> {website_url if website_url else '<i>N/A</i>'}\n" +
                    f"<b>Telegram:</b> " +
                    (f"<a href='{telegram_url}'>{telegram_url}</a>" if telegram_url and telegram_url != 'N/A' else "<i>N/A</i>") +
                    f" ({telegram_members} Members)\n" +
                    f"<b>Twitter:</b> " +
                    (f"<a href='{twitter_url}'>{twitter_url}</a>" if twitter_url and twitter_url != 'N/A' else "<i>N/A</i>") +
                    f" ({followers} Followers)\n" +
                    f"<b>Dexscreener:</b> <a href='{dex_link}'>{dex_link}</a>" +
                    divider +
                    "<i>Stay updated with the latest tokens!</i>"
                )

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
        divider = "\n━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"

        for t in new_tokens:
            # Build Dexscreener link based on chain and token address
            dex_link = f"https://dexscreener.com/{t['chain_id'].lower()}/{t['token_address']}"
            
            msg = (
                f"<b>🔍 FILTERED TOKEN</b>{divider}"
                f"<b>Chain:</b> <code>{t['chain_id']}</code>\n"
                f"<b>Token Address:</b> <code>{t['token_address']}</code>\n"
                f"<b>Website:</b> {t['website_url'] if t['website_url'] != 'N/A' else '<i>N/A</i>'}\n"
                f"<b>Telegram:</b> " +
                    (f"<a href='{t['telegram_url']}'>{t['telegram_url']}</a>" if t['telegram_url'] != 'N/A' else "<i>N/A</i>") +
                f" ({t['telegram_members']} Members)\n"
                f"<b>Twitter:</b> " +
                    (f"<a href='{t['twitter_url']}'>{t['twitter_url']}</a>" if t['twitter_url'] != 'N/A' else "<i>N/A</i>") +
                f" ({t['followers']} Followers)\n"
                f"<b>Dexscreener:</b> <a href='{dex_link}'>{dex_link}</a>"
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
    start_msg = (
    "<b>🚀 Welcome to New Pairs Bot! 🚀</b>\n"
    "\n━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
    "I continuously scan multiple blockchains to fetch <b>new tokens</b> in real-time. \n"
    "All tokens are stored securely and can be filtered later using the /filter command.\n"
    "\n"
    "• <b>Real-Time Updates:</b> Get instant alerts for tokens that pass your custom filters.\n"
    "• <b>Historical Data:</b> Review stored tokens at any time.\n"
    "\n"
    "Press /filter anytime to refine your view by chain or Twitter followers.\n"
    "\n━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
    "<i>Stay ahead with the latest token trends!</i>"
)


    await update.message.reply_text(
        text=start_msg,
        parse_mode="HTML",
        reply_markup=ReplyKeyboardRemove()
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
    filter_msg = (
    "<b>🔧 Token Filter Options</b>\n"
    "\n━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
    "Use the buttons below to customize your token view:\n"
    "\n"
    "• <b>Chain Filter:</b> Select a specific blockchain (e.g. <code>Solana</code>, <code>Ethereum</code>, etc.) to show tokens only from that network.\n"
    "• <b>Twitter Follower Filter:</b> Choose a minimum follower threshold (e.g. <code>Followers > 500</code>) so only tokens with enough social traction are displayed.\n"
    "\n"
    "• <i>Clear Filters</i> will reset all settings.\n"
    "• <i>Show Current Filtered</i> will display all tokens stored that match your current criteria.\n"
    "• <i>Done</i> hides this filter menu (you can type <code>/filter</code> to open it again).\n"
    "\n━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
    "<i>All new tokens are automatically filtered in real-time based on your current settings.</i>"
)

    await update.message.reply_text(filter_msg, parse_mode='HTML', reply_markup=reply_markup)


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
