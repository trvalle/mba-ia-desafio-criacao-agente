from __future__ import annotations

from typing import Any

from google.adk.tools.tool_context import ToolContext

from .domain import Domain, Regulation


class AuroraTools:
    """Tools receive trusted session context, not an apartment selected by the model."""

    def __init__(self, domain: Domain, regulation: Regulation):
        self.domain = domain
        self.regulation = regulation

    def _trusted_apartment(self, tool_context: ToolContext) -> str:
        apartment = tool_context.state.get("apartment")
        if not isinstance(apartment, str) or not apartment:
            raise ValueError("Sessão sem apartamento autenticado")
        return apartment

    def reservations(self, tool_context: ToolContext) -> list[dict[str, str]]:
        return self.domain.list_reservations(self._trusted_apartment(tool_context))

    def visitors(self, tool_context: ToolContext) -> list[dict[str, str]]:
        return self.domain.list_visitors(self._trusted_apartment(tool_context))

    def regulation_lookup(self, question: str) -> dict[str, str]:
        return {"resposta": self.regulation.answer(question) or "Não encontrei essa regra no regulamento."}

    def request_paid_reservation(self, area: str, data: str, tool_context: ToolContext) -> dict[str, Any]:
        """ADK-native confirmation hook used by the paid-reservation specialist."""
        if tool_context.tool_confirmation is None:
            tool_context.request_confirmation(
                hint="Confirme a reserva da área com cobrança.",
                payload={"area": area, "data": data},
            )
            return {"status": "PENDING_CONFIRMATION", "area": area, "data": data}
        if not tool_context.tool_confirmation.confirmed:
            return {"status": "DECLINED"}
        return {"status": "CONFIRMED", "area": area, "data": data}

    def request_visitor_authorization(self, nome: str, data: str, tool_context: ToolContext) -> dict[str, Any]:
        """ADK-native confirmation hook used by the visitor specialist."""
        if tool_context.tool_confirmation is None:
            tool_context.request_confirmation(
                hint="Confirme a autorização de entrada do visitante.",
                payload={"nome": nome, "data": data},
            )
            return {"status": "PENDING_CONFIRMATION", "nome": nome, "data": data}
        if not tool_context.tool_confirmation.confirmed:
            return {"status": "DECLINED"}
        return {"status": "CONFIRMED", "nome": nome, "data": data}
