from mem0 import MemoryClient

client = MemoryClient(api_key="m0-mDY3b1BBG4tEYc6229LgCNSSTJuHCs1xZPX6MzIb")

messages = [
    {"role": "user", "content": "I'm planning a trip to Tokyo next month."},
    {"role": "assistant", "content": "Great! I’ll remember that for future suggestions."}
]

client.add(
    messages=messages,
    user_id="alice",
)