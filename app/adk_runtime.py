from __future__ import annotations

from google.adk.apps import App
from google.adk.runners import Runner
from google.adk.sessions import DatabaseSessionService

from .agents import build_agents
from .config import APP_NAME, DATABASE_PATH, USER_ID
from .tools import AuroraTools


class AdkRuntime:
    """ADK runtime objects kept in one place for inspection and future live turns."""

    def __init__(self, tools: AuroraTools):
        # ADK 2.2.0's DatabaseSessionService is async and therefore requires
        # the aiosqlite dialect for a local SQLite database.
        self.session_service = DatabaseSessionService(f"sqlite+aiosqlite:///{DATABASE_PATH.as_posix()}")
        self.app = App(name=APP_NAME, root_agent=build_agents(tools))
        self.runner = Runner(app=self.app, session_service=self.session_service, auto_create_session=False)

    async def create_session(self, session_id: str, apartment: str) -> None:
        await self.session_service.create_session(
            app_name=APP_NAME,
            user_id=USER_ID,
            session_id=session_id,
            state={"apartment": apartment},
        )
