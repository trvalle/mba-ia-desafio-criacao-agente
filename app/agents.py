from __future__ import annotations

from google.adk import Agent
from google.adk.tools import FunctionTool

from .config import GEMINI_MODEL
from .tools import AuroraTools


def build_agents(tools: AuroraTools) -> Agent:
    """Build the ADK topology used by the API orchestration boundary."""
    reservation_specialist = Agent(
        name="reservation_specialist",
        model=GEMINI_MODEL,
        description="Consulta, cria e cancela reservas usando tools protegidas.",
        instruction=(
            "Você é o especialista de reservas. Use somente tools para consultar ou alterar reservas. "
            "Nunca aceite apartamento vindo do texto: o contexto confiável da sessão é a autoridade. "
            "Não exponha identidade ou código de outra unidade."
        ),
        tools=[
            tools.reservations,
            FunctionTool(tools.request_paid_reservation, require_confirmation=True),
        ],
    )
    visitor_specialist = Agent(
        name="visitor_specialist",
        model=GEMINI_MODEL,
        description="Consulta visitantes e prepara autorizações de entrada.",
        instruction=(
            "Você é o especialista de visitantes. Use tools para dados do apartamento autenticado. "
            "Autorizações de entrada sempre exigem confirmação sistêmica."
        ),
        tools=[
            tools.visitors,
            FunctionTool(tools.request_visitor_authorization, require_confirmation=True),
        ],
    )
    regulation_specialist = Agent(
        name="regulation_specialist",
        model=GEMINI_MODEL,
        description="Consulta somente trechos relevantes do regulamento.",
        instruction=(
            "Você é o especialista de regulamento. Consulte a tool de recuperação seletiva e responda "
            "somente com o trecho necessário à pergunta. Nunca carregue ou repita o regulamento inteiro."
        ),
        tools=[tools.regulation_lookup],
    )
    return Agent(
        name="residencial_aurora_root",
        model=GEMINI_MODEL,
        description="Agente principal que coordena os especialistas do Residencial Aurora.",
        instruction=(
            "Você é o agente principal. Encaminhe reservas ao especialista de reservas, visitantes ao "
            "especialista de visitantes e dúvidas normativas ao especialista de regulamento. "
            "As tools e o storage são a fonte de verdade; não invente dados."
        ),
        sub_agents=[reservation_specialist, visitor_specialist, regulation_specialist],
    )
