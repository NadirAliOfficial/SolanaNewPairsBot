# New Pairs on Solana - Token Monitoring with Social Insights

## Overview
The **New Pairs on Solana** is a Python-based monitoring tool that fetches newly listed tokens from DEX Screener and extracts relevant social data, including:
- **Twitter Followers** (via Nitter)
- **Telegram Members Count** (via Telethon)
- **Website Presence**
- **Token Network Information**

This bot continuously scrapes token profiles and provides insights into their online presence, making it easier to track token popularity and social engagement.

## Features
✅ **Real-time Token Monitoring** - Fetches new token profiles from DEX Screener.
✅ **Twitter Data Scraper** - Extracts follower count from Twitter/X via Nitter.
✅ **Telegram Members Scraper** - Fetches the number of members in a Telegram channel using Telethon.
✅ **Formatted Output** - Displays token details, including links and social statistics.
✅ **Error Handling** - Catches API failures and ensures stability.

## Installation & Setup

### Prerequisites
Ensure you have **Python 3.8+** installed. Then, install the required dependencies:

```bash
pip install requests ntscraper telethon
```

### Telegram API Setup
To use Telethon, you need a **Telegram API ID** and **API Hash**. Follow these steps:
1. Go to [my.telegram.org](https://my.telegram.org/apps) and create a new application.
2. Note down your `API_ID` and `API_HASH`.
3. Replace the placeholder values in the script with your credentials.

### Running the Bot
Clone this repository and run the script:

```bash
git clone https://github.com/yourusername/new-pairs-solana.git
cd new-pairs-solana
python bot.py
```

## How It Works
1. The bot continuously monitors DEX Screener for newly listed tokens.
2. It extracts their social links (Twitter, Telegram, Website) from the API response.
3. Using **Nitter**, it fetches Twitter follower count.
4. Using **Telethon**, it fetches the Telegram channel members count.
5. It displays all gathered information in a readable format.

### Example Output:
```
--- NEW TOKEN FOUND ---
Chain:        Solana
TokenAddress: 0x123456abcdef...
Description:  A new meme token on Solana.
Website:      https://exampletoken.com
Telegram:     (10,500 members) https://t.me/exampletoken
Twitter:      https://x.com/exampletoken (Followers: 25,000)
------------------------------------------------------------
```

## Troubleshooting
- **Nitter not working?** Try using a different Nitter instance by modifying `Nitter("https://your-nitter-instance.com")`.
- **Telegram members not fetching?** Ensure the session file exists and your bot has access to the channel.
- **Rate limits?** Telegram and Twitter APIs may temporarily block frequent requests. Implement delays if needed.

## License
This project is licensed under the MIT License.

## Contributing
Pull requests are welcome! For major changes, please open an issue first to discuss what you’d like to improve.

## Contact
For support, reach out via GitHub issues or Telegram.

