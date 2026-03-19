from mem0 import Memory
config = {
    "llm": {
        "provider": "openai",
        "config": {
            "model": "glm-4.7",
            "api_key": "51d9189503ad476fbba1c56e14e60826.Cckh0az1l4lljZNe",
            "openai_base_url": "https://open.bigmodel.cn/api/paas/v4/",
            "temperature": 0.1
        }
    },
    "embedder": {
        "provider": "openai",
        "config": {
            "model": "embedding-3",
            "api_key": "51d9189503ad476fbba1c56e14e60826.Cckh0az1l4lljZNe",
            "openai_base_url": "https://open.bigmodel.cn/api/paas/v4/"
        }
    }
}

m = Memory.from_config(config)


# Simple search
related_memories = m.search("Should I drink coffee or tea?", user_id="alice")

# Search with filters
memories = m.search(
    "food preferences",
    user_id="alice",
    filters={"categories": "diet"}
)