import requests

response = requests.get(
    "https://api.dexscreener.com/token-profiles/latest/v1/",
    headers={},
)
data = response.json()
for token in data:
    print(token["chainId"])