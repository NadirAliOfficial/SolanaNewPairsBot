import time
import re
import os
import requests
from ntscraper import Nitter
from telethon.sync import TelegramClient
from telethon.tl.functions.channels import GetFullChannelRequest
from telethon.errors import ChannelInvalidError, ChannelPrivateError, UsernameNotOccupiedError
from dotenv import load_dotenv

# Load environment variables from .env file
load_dotenv()

# Telegram API credentials (make sure these are set in your .env file)
API_ID = int(os.getenv('API_ID'))      # Your API ID
API_HASH = os.getenv('API_HASH')         # Your API Hash
SESSION_PATH = 'session_name'            # Pre-saved session file

# Initialize Telethon client with the pre-saved session
telethon_client = TelegramClient(SESSION_PATH, API_ID, API_HASH)
telethon_client.start()
print("Telethon client started successfully!")

# URL for fetching the token profiles
TOKEN_PROFILES_URL = "https://api.dexscreener.com/token-profiles/latest/v1"

# Create a global Nitter client to reduce repeated instance checks
nitter_client = Nitter("https://nitter.privacydev.net")  # or your preferred instance

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
    Fetch the follower count from a Twitter URL using Nitter.
    """
    handle = parse_twitter_handle(twitter_url)
    if not handle:
        return 0
    try:
        # Append a trailing slash if necessary
        profile = nitter_client.get_profile_info(handle + "/")
        if profile and 'stats' in profile:
            return profile['stats'].get('followers', 0)
    except Exception as e:
        print(f"[Nitter Error] {e}")
    return 0

def get_telegram_member_count(telegram_url: str) -> int:
    """
    Retrieve the Telegram member count for a given Telegram channel URL.
    Uses GetFullChannelRequest for channels/supergroups.
    """
    pattern = r"https?://t\.me/([^/?]+)"
    match = re.match(pattern, telegram_url)
    if not match:
        print(f"[Error] Invalid Telegram URL: {telegram_url}")
        return 0
    username = match.group(1)
    try:
        # Get the base entity from the username
        entity = telethon_client.get_entity(username)
        
        # If the entity is a channel or supergroup, fetch the full channel info
        if hasattr(entity, 'broadcast') or hasattr(entity, 'megagroup'):
            full_channel = telethon_client(GetFullChannelRequest(entity))
            if hasattr(full_channel.full_chat, 'participants_count'):
                return full_channel.full_chat.participants_count
            else:
                print(f"[Warning] 'participants_count' not found in full_chat for {telegram_url}")
                return 0
        
        # Fallback: check if participants_count is directly available
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

def monitor_new_pairs():
    """
    Monitor the TOKEN_PROFILES_URL for new tokens and display their details,
    including website, Twitter (with followers), and Telegram (with member count).
    """
    seen_addresses = set()

    while True:
        try:
            response = requests.get(TOKEN_PROFILES_URL, timeout=10)
            response.raise_for_status()

            profiles = response.json()
            for profile in profiles:
                chain_id      = profile.get("chainId", "")
                token_address = profile.get("tokenAddress", "")
                description   = profile.get("description", "")
                links         = profile.get("links", [])

                # Process only new tokens
                if token_address not in seen_addresses:
                    seen_addresses.add(token_address)

                    website_url = None
                    twitter_url = None
                    telegram_url = None
                    telegram_member_count = None

                    # Extract website, twitter, and telegram URLs
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

                    # Get Twitter followers if a Twitter URL is provided
                    followers = get_twitter_followers(twitter_url) if twitter_url else 0

                    # Get Telegram member count if a Telegram URL is provided
                    if telegram_url:
                        telegram_member_count = get_telegram_member_count(telegram_url)

                    # Print token information
                    print(f"--- NEW TOKEN FOUND ---")
                    print(f"Chain:        {chain_id}")
                    print(f"TokenAddress: {token_address}")
                    print(f"Description:  {description}")
                    print(f"Website:      {website_url or 'N/A'}")
                    if telegram_url:
                        member_count_display = f"({telegram_member_count} members) " if telegram_member_count is not None else ""
                        print(f"Telegram:     {member_count_display}{telegram_url}")
                    else:
                        print("Telegram:     N/A")
                    if twitter_url:
                        print(f"Twitter:      {twitter_url} (Followers: {followers})")
                    else:
                        print("Twitter:      N/A")
                    print("-" * 60)

            time.sleep(3)

        except requests.exceptions.RequestException as e:
            print(f"[ERROR] {e}")
            time.sleep(5)
        except Exception as e:
            print(f"[Unexpected Error] {e}")
            time.sleep(5)

if __name__ == "__main__":
    try:
        monitor_new_pairs()
    except KeyboardInterrupt:
        print("Shutting down...")
    finally:
        telethon_client.disconnect()
        print("Telethon client disconnected.")
