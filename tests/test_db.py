from app.database.session import client


try:
    client.admin.command("ping")
    print("MongoDB connected successfully!")
except Exception as e:
    print(f"MongoDB connection failed: {e}")