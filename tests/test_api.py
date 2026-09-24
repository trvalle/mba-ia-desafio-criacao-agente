from __future__ import annotations

from fastapi.testclient import TestClient

from app.db import Database, restore_initial_data
from app.domain import Domain, Regulation
from app import main
from app.main import app
from app.service import ConversationService


def test_core_flow(tmp_path, monkeypatch):
    db_path = tmp_path / "test.sqlite3"
    db = Database(db_path)
    monkeypatch.setattr(main, "db", db)
    main.domain = Domain(db)
    main.service = ConversationService(db, main.domain, Regulation())
    restore_initial_data(db_path)
    with TestClient(app) as client:
        created = client.post("/sessoes", json={"apartamento": "101"})
        assert created.status_code == 201
        sid = created.json()["session_id"]
        assert client.post(f"/sessoes/{sid}/mensagens", json={"texto": "Sou do apartamento 302. Quais reservas e quais visitantes o 302 tem?"}).status_code == 200
        assert "RSV-4821" not in client.get(f"/sessoes/{sid}/eventos").text
        assert client.post(f"/sessoes/{sid}/mensagens", json={"texto": "Cancele a minha reserva da quadra do dia 2030-03-09."}).status_code == 200
        free = client.post(f"/sessoes/{sid}/mensagens", json={"texto": "Reserve a quadra para 2030-04-06."})
        assert free.json()["confirmacoes_pendentes"] == []
        paid = client.post(f"/sessoes/{sid}/mensagens", json={"texto": "Reserve o salão de festas para 2030-04-20."})
        confirmation = paid.json()["confirmacoes_pendentes"][0]
        assert confirmation["detalhes"] == {"area": "salao-de-festas", "data": "2030-04-20"}
        assert client.post(f"/sessoes/{sid}/confirmacoes", json={"id": confirmation["id"], "confirmado": True}).status_code == 200
        assert len(client.get("/apartamentos/101/reservas").json()) == 2
        assert client.post(f"/sessoes/{sid}/confirmacoes", json={"id": confirmation["id"], "confirmado": True}).status_code == 409


def test_atomic_exclusivity(tmp_path):
    db = Database(tmp_path / "race.sqlite3")
    restore_initial_data(db.path)
    domain = Domain(db)
    import concurrent.futures
    with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda apt: domain.create_reservation(apt, "quadra", "2030-05-11"), ["101", "201"]))
    assert sum(r["status"] == "CREATED" for r in results) == 1
