# Python SDK

```python
from client import Client

client = Client(base_url="http://localhost:3000", api_key="<key>")

games = client.games()
cards = client.cards({"game": "pokemon", "q": "pika"})
print(games, cards)
```
