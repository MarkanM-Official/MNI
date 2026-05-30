"""
MNI Automation Manager - Database Initialization
Run: python database/init_db.py
"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from backend.app import create_app, db
from backend.models.user import User
from backend.models.message import Message
from backend.models.config_model import BotConfig, ApiKey

def init_database():
    app = create_app()
    with app.app_context():
        db.create_all()
        print("✅ All tables created.")

        # Check if default config exists
        existing = BotConfig.query.filter_by(key='personality').first()
        if not existing:
            defaults = [
                BotConfig(key='personality',      value='confident, attitude-driven, slightly flirty, smart and witty'),
                BotConfig(key='rag_prompt',        value=open(os.path.join(os.path.dirname(__file__), '../config/default_rag.txt')).read()),
                BotConfig(key='tone',              value='sharp, smart, engaging'),
                BotConfig(key='text_enabled',      value='true'),
                BotConfig(key='image_enabled',     value='true'),
                BotConfig(key='video_enabled',     value='true'),
                BotConfig(key='voice_enabled',     value='true'),
                BotConfig(key='telegram_enabled',  value='true'),
                BotConfig(key='discord_enabled',   value='true'),
                BotConfig(key='whatsapp_enabled',  value='true'),
                BotConfig(key='instagram_enabled', value='true'),
                BotConfig(key='api_usage',         value='true'),
                BotConfig(key='load_balancing',    value='round_robin'),
            ]
            for d in defaults:
                db.session.add(d)
            db.session.commit()
            print("✅ Default config seeded.")
        else:
            print("ℹ️  Config already exists, skipping seed.")

        print("\nMNI Automation Manager database ready!")
        print("   Run: python backend/app.py")

if __name__ == '__main__':
    init_database()
