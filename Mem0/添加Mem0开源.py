import os
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

messages = [
    {"role": "user", "content": "I'm planning to watch a movie tonight. Any recommendations?"},
    {"role": "assistant", "content": "How about thriller movies? They can be quite engaging."},
    {"role": "user", "content": "I'm not a big fan of thriller movies but I love sci-fi movies."},
    {"role": "assistant", "content": "Got it! I'll avoid thriller recommendations and suggest sci-fi movies in the future."}
]

# Store inferred memories (default behavior)
result = m.add(
    messages,
    user_id="alice",
    metadata={"category": "movie_recommendations"}
)

# Optionally store raw messages without inference
result = m.add(
    messages,
    user_id="alice",
    metadata={"category": "movie_recommendations"},
    infer=False
)