from collections import Counter
from app.database.session import db

GUILD_ID = "1546948804123697212"

history = db["ghosty_gamble_history"]
stats = db["ghosty_channel_stats"]

print("\n=== HISTORY BY SOURCE ===")
pipeline = [
    {"$match": {"guild_id": GUILD_ID}},
    {"$group": {
        "_id": {"source": "$source", "outcome": "$outcome"},
        "count": {"$sum": 1}
    }},
    {"$sort": {"_id.source": 1, "_id.outcome": 1}},
]
for row in history.aggregate(pipeline):
    print(row)

print("\n=== HISTORY TOTALS ===")
print("Total history records:", history.count_documents({"guild_id": GUILD_ID}))
print("Distinct result IDs:", len(history.distinct("result_message_id", {"guild_id": GUILD_ID})))

print("\n=== CHANNEL STATISTICS VS HISTORY ===")
for saved in stats.find({"guild_id": GUILD_ID}).sort("channel_id", 1):
    channel_id = saved["channel_id"]
    match = {"guild_id": GUILD_ID, "channel_id": channel_id}

    hits = history.count_documents({**match, "outcome": "hit"})
    misses = history.count_documents({**match, "outcome": "miss"})
    total = history.count_documents(match)

    print({
        "channel_id": channel_id,
        "history_hits": hits,
        "stats_hits": saved.get("hits", 0),
        "history_misses": misses,
        "stats_misses": saved.get("misses", 0),
        "history_total": total,
        "stats_total": saved.get("total_gambles", 0),
        "history_net_pl": sum(
            doc.get("net_pl_delta", 0)
            for doc in history.find(match, {"net_pl_delta": 1})
        ),
        "stats_net_pl": saved.get("net_pl", 0),
        "current_streak": saved.get("streak", 0),
        "current_streak_loss": saved.get("streak_loss", 0),
    })
