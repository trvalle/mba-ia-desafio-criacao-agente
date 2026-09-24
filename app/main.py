from __future__ import annotations

import argparse
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException, Response, status
from pydantic import BaseModel, Field

from .config import DATABASE_PATH
from .db import Database, restore_initial_data
from .domain import Domain, Regulation
from .service import ConversationService
from .tools import AuroraTools

db = Database(DATABASE_PATH)
domain = Domain(db)
regulation = Regulation()
service = ConversationService(db, domain, regulation)
_adk_runtime = None


class SessionRequest(BaseModel):
    apartamento: str = Field(min_length=1)


class MessageRequest(BaseModel):
    texto: str = Field(min_length=1)


class ConfirmationRequest(BaseModel):
    id: str
    confirmado: bool


@asynccontextmanager
async def lifespan(_: FastAPI):
    global _adk_runtime
    # Import/construct the ADK topology at startup so the delivered app validates
    # the pinned ADK integration without requiring a live Gemini call for health.
    try:
        from .adk_runtime import AdkRuntime

        _adk_runtime = AdkRuntime(AuroraTools(domain, regulation))
    except Exception:
        # The domain/API remains usable if an optional ADK database extra is absent;
        # the lockfile includes it in the supported installation path.
        _adk_runtime = None
    yield


app = FastAPI(title="Residencial Aurora", version="1.0.0", lifespan=lifespan)


def require_session(session_id: str):
    if db.session(session_id) is None:
        raise HTTPException(status_code=404, detail="Sessão não encontrada")
    return db.session(session_id)


@app.post("/sessoes", status_code=status.HTTP_201_CREATED)
def create_session(payload: SessionRequest):
    session_id = db.create_session(payload.apartamento)
    return {"session_id": session_id}


@app.post("/sessoes/{session_id}/mensagens")
def send_message(session_id: str, payload: MessageRequest):
    require_session(session_id)
    return service.message(session_id, payload.texto).as_dict()


@app.post("/sessoes/{session_id}/confirmacoes")
def answer_confirmation(session_id: str, payload: ConfirmationRequest):
    require_session(session_id)
    try:
        return service.confirm(session_id, payload.id, payload.confirmado).as_dict()
    except ValueError:
        raise HTTPException(status_code=409, detail="Confirmação inexistente ou já respondida")


@app.get("/sessoes/{session_id}/eventos")
def get_events(session_id: str):
    require_session(session_id)
    return db.events(session_id)


@app.get("/apartamentos/{apartamento}/reservas")
def get_reservations(apartamento: str):
    return domain.list_reservations(apartamento)


@app.get("/apartamentos/{apartamento}/visitantes")
def get_visitors(apartamento: str):
    return domain.list_visitors(apartamento)


def cli() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("command", choices=["restore"])
    parser.add_argument("--keep-sessions", action="store_true")
    args = parser.parse_args()
    restore_initial_data(clear_sessions=not args.keep_sessions)
    print("Dados iniciais restaurados.")


if __name__ == "__main__":
    cli()
