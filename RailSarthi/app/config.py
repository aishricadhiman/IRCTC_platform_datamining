import os
from pydantic import BaseModel

class Settings(BaseModel):
    DATABASE_URL: str = os.getenv("DATABASE_URL", "sqlite:///./railsarthi.db")
    APP_NAME: str = "RailSarthi - Seat Allocation & Waitlist Service"
    DEBUG: bool = True

settings = Settings()
