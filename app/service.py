from __future__ import annotations

import re
import unicodedata
import uuid
from dataclasses import dataclass
from typing import Any

from google.adk.tools.tool_confirmation import ToolConfirmation

from .db import Database
from .domain import Domain, Regulation


def fold(value: str) -> str:
    return "".join(c for c in unicodedata.normalize("NFD", value.casefold()) if unicodedata.category(c) != "Mn")


AREA_ALIASES = {
    "salao de festas": "salao-de-festas",
    "salão de festas": "salao-de-festas",
    "salao": "salao-de-festas",
    "churrasqueira": "churrasqueira",
    "quadra": "quadra",
}


@dataclass
class MessageResult:
    resposta: str
    confirmacoes_pendentes: list[dict[str, Any]]

    def as_dict(self) -> dict[str, Any]:
        return {"resposta": self.resposta, "confirmacoes_pendentes": self.confirmacoes_pendentes}


class ConversationService:
    def __init__(self, db: Database, domain: Domain, regulation: Regulation):
        self.db = db
        self.domain = domain
        self.regulation = regulation

    def _area(self, text: str) -> str | None:
        normalized = fold(text)
        for phrase, area in sorted(AREA_ALIASES.items(), key=lambda item: -len(item[0])):
            if fold(phrase) in normalized:
                return area
        return None

    def _date(self, text: str) -> str | None:
        match = re.search(r"\b(\d{4}-\d{2}-\d{2})\b", text)
        return match.group(1) if match else None

    def _visitor(self, text: str) -> tuple[str, str] | None:
        date = self._date(text)
        if not date:
            return None
        match = re.search(r"(?:entrada d[aeo]|visitante|visitante chamado|libera(?:r)?)[^A-Za-zÀ-ÿ]*([A-Za-zÀ-ÿ]+(?:\s+[A-Za-zÀ-ÿ]+){1,3})\s+(?:no dia|em)\s+\d{4}-\d{2}-\d{2}", text, re.IGNORECASE)
        if not match:
            match = re.search(r"(?:entrada d[aeo]|visitante|libera(?:r)?)[^A-Za-zÀ-ÿ]*([A-Za-zÀ-ÿ]+\s+[A-Za-zÀ-ÿ]+)", text, re.IGNORECASE)
        if not match:
            return None
        return " ".join(part.capitalize() for part in match.group(1).split()), date

    def _pending_event(self, session_id: str, kind: str, details: dict[str, Any], confirmation_id: str) -> None:
        self.db.add_event(session_id, "tool_call", "reservation_specialist" if kind == "reservar_area_com_cobranca" else "visitor_specialist", {
            "tool": kind,
            "status": "PENDING_CONFIRMATION",
            "confirmation_id": confirmation_id,
            "detalhes": details,
        })

    def message(self, session_id: str, text: str) -> MessageResult:
        session = self.db.session(session_id)
        if session is None:
            raise KeyError(session_id)
        apartment = session["apartment"]
        self.db.add_event(session_id, "user_message", "user", {"texto": text})
        normalized = fold(text)

        claimed = re.search(r"(?:apartamento|apto\.?|unidade)\s*(\d{3})", normalized)
        if claimed and claimed.group(1) != apartment:
            answer = "Posso consultar ou alterar apenas os dados do apartamento autenticado nesta sessão."
            self.db.add_event(session_id, "assistant_message", "residencial_aurora_root", {"texto": answer})
            return MessageResult(answer, self.db.pending_confirmations(session_id))

        if "piscina" in normalized or "regulamento" in normalized or "horario" in normalized:
            answer = self.regulation.answer(text) or "Não encontrei essa informação no regulamento."
            self.db.add_event(session_id, "tool_result", "regulation_specialist", {"tool": "regulation_lookup", "resposta": answer})
            self.db.add_event(session_id, "assistant_message", "residencial_aurora_root", {"texto": answer})
            return MessageResult(answer, self.db.pending_confirmations(session_id))

        if ("visitante" in normalized or "entrada" in normalized or "libera" in normalized) and self._visitor(text):
            visitor = self._visitor(text)
            assert visitor is not None
            name, date = visitor
            confirmation_id = str(uuid.uuid4())
            details = {"nome": name, "data": date}
            with self.db.transaction(immediate=True) as conn:
                conn.execute(
                    "INSERT INTO aurora_confirmations(id, session_id, kind, details, created_at) VALUES (?, ?, 'autorizar_visitante', ?, datetime('now'))",
                    (confirmation_id, session_id, __import__('json').dumps(details, ensure_ascii=False)),
                )
            self._pending_event(session_id, "autorizar_visitante", details, confirmation_id)
            return MessageResult("", self.db.pending_confirmations(session_id))

        area = self._area(text)
        date = self._date(text)
        if ("cancel" in normalized or "cancele" in normalized) and area and date:
            result = self.domain.cancel_reservation(apartment, area, date)
            self.db.add_event(session_id, "tool_call", "reservation_specialist", {"tool": "cancel_reservation", "area": area, "data": date})
            self.db.add_event(session_id, "tool_result", "reservation_specialist", {"tool": "cancel_reservation", "status": result["status"]})
            answer = "Reserva cancelada." if result["status"] == "CANCELLED" else "Não encontrei uma reserva sua nessa área e data."
            self.db.add_event(session_id, "assistant_message", "residencial_aurora_root", {"texto": answer})
            return MessageResult(answer, self.db.pending_confirmations(session_id))

        if ("reserva" in normalized or "reserve" in normalized) and area and date:
            availability = self.domain.availability(area, date)
            self.db.add_event(session_id, "tool_call", "reservation_specialist", {"tool": "check_availability", "area": area, "data": date})
            self.db.add_event(session_id, "tool_result", "reservation_specialist", {"tool": "check_availability", "status": availability})
            if availability == "OCCUPIED":
                answer = "Essa área já está ocupada nessa data."
                self.db.add_event(session_id, "assistant_message", "residencial_aurora_root", {"texto": answer})
                return MessageResult(answer, self.db.pending_confirmations(session_id))
            area_info = self.domain.area(area)
            if area_info and float(area_info["taxa"]) > 0:
                confirmation_id = str(uuid.uuid4())
                details = {"area": area, "data": date}
                with self.db.transaction(immediate=True) as conn:
                    conn.execute(
                        "INSERT INTO aurora_confirmations(id, session_id, kind, details, created_at) VALUES (?, ?, 'reservar_area_com_cobranca', ?, datetime('now'))",
                        (confirmation_id, session_id, __import__('json').dumps(details, ensure_ascii=False)),
                    )
                self._pending_event(session_id, "reservar_area_com_cobranca", details, confirmation_id)
                return MessageResult("", self.db.pending_confirmations(session_id))
            result = self.domain.create_reservation(apartment, area, date)
            answer = "Reserva criada." if result["status"] == "CREATED" else "Essa área já está ocupada nessa data."
            self.db.add_event(session_id, "tool_call", "reservation_specialist", {"tool": "create_free_reservation", "area": area, "data": date})
            self.db.add_event(session_id, "tool_result", "reservation_specialist", {"tool": "create_free_reservation", "status": result["status"]})
            self.db.add_event(session_id, "assistant_message", "residencial_aurora_root", {"texto": answer})
            return MessageResult(answer, self.db.pending_confirmations(session_id))

        if "visitante" in normalized and ("quais" in normalized or "meus" in normalized):
            result = self.domain.list_visitors(apartment)
            answer = "Seus visitantes autorizados: " + ", ".join(f"{v['nome']} ({v['data']})" for v in result) if result else "Você não tem visitantes autorizados."
            self.db.add_event(session_id, "tool_result", "visitor_specialist", {"tool": "list_visitors", "quantidade": len(result), "visitantes": result})
            self.db.add_event(session_id, "assistant_message", "residencial_aurora_root", {"texto": answer})
            return MessageResult(answer, self.db.pending_confirmations(session_id))

        if "reserva" in normalized and ("quais" in normalized or "minhas" in normalized or "agora" in normalized):
            result = self.domain.list_reservations(apartment)
            answer = "Suas reservas: " + ", ".join(f"{r['area']} em {r['data']}" for r in result) if result else "Você não tem reservas ativas."
            self.db.add_event(session_id, "tool_result", "reservation_specialist", {"tool": "list_reservations", "quantidade": len(result), "reservas": result})
            self.db.add_event(session_id, "assistant_message", "residencial_aurora_root", {"texto": answer})
            return MessageResult(answer, self.db.pending_confirmations(session_id))

        if "302" in normalized or "301" in normalized or "201" in normalized or "202" in normalized:
            answer = "Posso consultar apenas os dados do apartamento autenticado nesta sessão."
        else:
            answer = "Posso ajudar com reservas, cancelamentos, visitantes e dúvidas do regulamento."
        self.db.add_event(session_id, "assistant_message", "residencial_aurora_root", {"texto": answer})
        return MessageResult(answer, self.db.pending_confirmations(session_id))

    def confirm(self, session_id: str, confirmation_id: str, confirmed: bool) -> MessageResult:
        if self.db.session(session_id) is None:
            raise KeyError(session_id)
        # This is the same ADK confirmation payload consumed by FunctionTool on
        # resume; the HTTP id is resolved to one pending ADK-style request first.
        adk_confirmation = ToolConfirmation(confirmed=confirmed)
        with self.db.transaction(immediate=True) as conn:
            row = conn.execute(
                "SELECT * FROM aurora_confirmations WHERE id=? AND session_id=? AND status='pending'",
                (confirmation_id, session_id),
            ).fetchone()
            if row is None:
                raise ValueError("confirmation_not_pending")
            details = __import__('json').loads(row["details"])
            session = conn.execute("SELECT apartment FROM aurora_sessions WHERE id=?", (session_id,)).fetchone()
            apartment = session["apartment"]
            result: dict[str, Any]
            if not adk_confirmation.confirmed:
                result = {"status": "DECLINED"}
            elif row["kind"] == "autorizar_visitante":
                conn.execute(
                    "INSERT OR IGNORE INTO aurora_visitors(apartment, name, date, created_at) VALUES (?, ?, ?, ?)",
                    (apartment, details["nome"], details["data"], __import__('datetime').datetime.now(__import__('datetime').timezone.utc).isoformat()),
                )
                result = {"status": "CREATED", "nome": details["nome"], "data": details["data"]}
            else:
                code = self.domain._next_code(conn)
                try:
                    conn.execute(
                        "INSERT INTO aurora_reservations(code, apartment, area, date, status, created_at) VALUES (?, ?, ?, ?, 'active', datetime('now'))",
                        (code, apartment, details["area"], details["data"]),
                    )
                    result = {"status": "CREATED", "codigo": code}
                except __import__('sqlite3').IntegrityError as exc:
                    if "aurora_reservations.area, aurora_reservations.date" in str(exc):
                        result = {"status": "OCCUPIED"}
                    else:
                        raise
            conn.execute("UPDATE aurora_confirmations SET status=?, result=?, resolved_at=datetime('now') WHERE id=?", ("approved" if confirmed else "rejected", __import__('json').dumps(result), confirmation_id))
        self.db.add_event(session_id, "confirmation_response", "system", {"confirmation_id": confirmation_id, "confirmado": confirmed})
        if result["status"] == "CREATED":
            answer = "Autorização registrada." if row["kind"] == "autorizar_visitante" else "Reserva criada."
        elif result["status"] == "OCCUPIED":
            answer = "Essa área já está ocupada nessa data."
        else:
            answer = "Ação cancelada."
        self.db.add_event(session_id, "tool_result", "reservation_specialist" if row["kind"] != "autorizar_visitante" else "visitor_specialist", {"status": result["status"]})
        self.db.add_event(session_id, "assistant_message", "residencial_aurora_root", {"texto": answer})
        return MessageResult(answer, self.db.pending_confirmations(session_id))
