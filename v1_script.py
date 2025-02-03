import time
import re
import requests
from ntscraper import Nitter

TOKEN_PROFILES_URL = "https://api.dexscreener.com/token-profiles/latest/v1"

# Create one global Nitter client to reduce repeated instance checks
nitter_client = Nitter("https://nitter.privacydev.net")  # or your preferred stable instance

def parse_twitter_handle(twitter_url: str) -> str:
    # Remove '/status/...'
    base_url = twitter_url.split('/status/')[0]  
    pattern = r"https?://(x\.com|twitter\.com)/([^/]+)"
    match = re.match(pattern, base_url)
    return match.group(2) if match else None

def get_twitter_followers(twitter_url: str) -> int:
    handle = parse_twitter_handle(twitter_url) + str("/")
    print(handle)
    if not handle:
        return 0
    try:
        profile = nitter_client.get_profile_info(handle)
        # 'stats' might look like {'tweets': ..., 'following': ..., 'followers': ..., 'likes': ..., 'media': ...}
        if profile and 'stats' in profile:
            return profile['stats'].get('followers', 0)
    except Nitter.NotFoundError as e:
        print(f"[Nitter Error] {e}")
    except Exception as e:
        print(f"[Unexpected Error] {e}")
    return 0

def monitor_new_pairs():
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

                if token_address not in seen_addresses:
                    seen_addresses.add(token_address)

                    website_url = None
                    twitter_url = None
                    telegram_url = None

                    # Grab website + twitter
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

                    # If Twitter link is found, fetch followers
                    followers = 0
                    if twitter_url:
                        followers = get_twitter_followers(twitter_url)

                    print(f"--- NEW TOKEN FOUND ---")
                    print(f"Chain:        {chain_id}")
                    print(f"TokenAddress: {token_address}")
                    print(f"Description:  {description}")
                    print(f"Website:      {website_url or 'N/A'}")
                    print(f"Telegram:     {telegram_url or 'N/A'}")
                    if twitter_url:
                        print(f"Twitter:      {twitter_url} (Followers: {followers})")
                    else:
                        print("Twitter:      N/A")
                    print("-" * 60)

            time.sleep(3)

        except requests.exceptions.RequestException as e:
            print(f"[ERROR] {e}")
            time.sleep(5)

if __name__ == "__main__":
    monitor_new_pairs()