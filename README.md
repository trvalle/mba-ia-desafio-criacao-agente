# Residencial Aurora

API do assistente virtual do Residencial Aurora. A entrega usa Python 3.12+, FastAPI, SQLite e Google ADK 2.2.0.

## Arquitetura

O agente principal `residencial_aurora_root` é criado em `app/agents.py` e coordena três especialistas ADK:

- `reservation_specialist`: consulta, cria e cancela reservas. É acionado quando a mensagem trata de uma área e data.
- `visitor_specialist`: consulta visitantes e prepara autorizações. É acionado quando a mensagem trata de liberar a entrada de alguém.
- `regulation_specialist`: consulta trechos curtos do regulamento. É acionado por dúvidas normativas; o arquivo completo nunca entra nas instruções do agente principal.

Os especialistas usam as tools de `app/tools.py`. O apartamento efetivo vem da sessão persistida e não é um argumento controlado pelo modelo. A camada `app/domain.py` é a fronteira de autoridade: toda leitura ou gravação de reservas e visitantes passa por ela.

`app/adk_runtime.py` fixa o `App`, o `Runner` e o `DatabaseSessionService` do ADK. As tools de ações sensíveis são `FunctionTool(..., require_confirmation=True)` e também usam `ToolContext.request_confirmation`, com o payload mínimo da ação. A API traduz a pendência para o contrato HTTP; a execução de domínio só ocorre depois da transição atômica da confirmação.

O SQLite é usado tanto para o domínio quanto para a persistência local da API: sessões, eventos, reservas históricas, visitantes e confirmações sobrevivem ao restart. O journal HTTP expõe eventos completos e ordenados em `GET /sessoes/{session_id}/eventos`.

## Garantias

1. **Cobrança ou acesso só com confirmação** — `app/service.py`, métodos `message` e `confirm`, e `app/tools.py`, métodos `request_paid_reservation` e `request_visitor_authorization`. A reserva paga e a autorização criam apenas uma linha `pending`; a execução acontece apenas no endpoint de confirmações. A busca da confirmação exige `status='pending'`, por isso replay e ids estrangeiros retornam 409.

2. **Isolamento por apartamento** — `app/db.py`, tabela `sessions`, e `app/domain.py`, métodos `list_reservations`, `list_visitors`, `cancel_reservation` e `create_reservation`. O serviço obtém o apartamento uma única vez da sessão e passa esse valor confiável à camada de domínio; textos como “sou do 302” não alteram o escopo. A disponibilidade devolve somente `AVAILABLE` ou `OCCUPIED`.

3. **Persistência e restart** — `app/db.py`, `Database`, `session_events`, `reservations`, `visitors` e `confirmations`. O banco SQLite fica em `runtime/aurora.sqlite3`; não há estado essencial em memória. O restore documentado limpa o estado mutável e repõe os dados protegidos do starter.

4. **Regulamento consultado, não carregado** — `app/domain.py`, classe `Regulation`, e `app/tools.py`, `regulation_lookup`. A recuperação devolve somente a resposta/parágrafo relevante. O agente principal não contém `dados/regulamento.md` em suas instruções, e os eventos registram apenas o resultado delimitado da tool.

5. **Uma reserva por área/data** — `app/db.py`, índice único parcial `uq_active_area_date`, e `app/domain.py`, `create_reservation`. A exclusividade é aplicada pelo banco no instante do `INSERT`; a rota de confirmação converte a colisão em resultado normal `OCCUPIED`, sem 500. Códigos usam contador persistente e a coluna `code` é única, inclusive para reservas canceladas.

## Como rodar

Pré-requisitos: Python 3.12 ou superior, `uv` e uma chave do Google AI Studio.

```powershell
Copy-Item .env.example .env
# preencha GOOGLE_API_KEY no .env; nunca versione esse arquivo
uv sync
uv run python -m app.main restore
uv run uvicorn app.main:app --host 0.0.0.0 --port 8000
```

A API fica em `http://localhost:8000`. O modelo padrão é `gemini-2.5-flash` e pode ser alterado por `GEMINI_MODEL`. O banco pode ser movido com `DATABASE_PATH`.

Para restaurar reservas e visitantes ao estado original e limpar sessões/eventos:

```powershell
uv run python -m app.main restore
```

As rotas implementadas são `POST /sessoes`, `POST /sessoes/{session_id}/mensagens`, `POST /sessoes/{session_id}/confirmacoes`, `GET /sessoes/{session_id}/eventos`, `GET /apartamentos/{apartamento}/reservas` e `GET /apartamentos/{apartamento}/visitantes`.
