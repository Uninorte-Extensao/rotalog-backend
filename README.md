# RotaLog Backend — V5

API do marketplace B2B de alimentos secos industrializados. Esta versão é um
**recorte funcional da V5**, usado para testes com frontend e mobile. Cadastro,
login e sessões também funcionam em produção com emails reais. O fluxo
financeiro continua **simulado**, sem movimentação de dinheiro real.
O conjunto de demonstração é preservado e pode ser compartilhado com essas contas.

## Backend online

O backend está disponível online. Acesse a
[documentação interativa da API (Swagger)](https://api.rotalog.madebyhermes.com/docs).

Funciona: cadastro, login, refresh/revogação de sessão, catálogo canônico,
depósitos, ofertas, estoque/capacidade manual, reserva de checkout, compra com
pagamento simulado, consulta/aceite/recusa de pedidos, separação, prontidão e
rastreamento sintético de rota por GPS. O fluxo canônico da demo acrescenta
planejamento manual aprovado/publicado, coleta com despacho/custódia, recebimento
integral comprovado e consulta do progresso/timeline pelo pedido.
O incremento mobile inclui manifesto, conferência global, divergências pré-coleta
resolvidas pelo fornecedor e rastreamento canônico em lotes, sem misturar os IDs
das rotas/GPS legados.
O backend continua sendo a única porta de acesso aos dados para web/mobile.

Imagens do catálogo: [contratos, upload privado, worker e galeria](docs/imagens-produtos-demo-v5.md).
São imagens do produto canônico, distintas das provas de entrega. O processamento
da demo é simplificado e aceita somente arquivos sintéticos; não é antivírus do piloto.

Mobile: [manifesto, conferência, divergências, tracking canônico e compatibilidade](docs/mobile-integracao-v5.md).

Frontend: [levantamento, novos endpoints, isolamento por fornecedor e convites](docs/frontend-pendencias-v5.md).

Produção: [configuração, primeiro administrador, cadastro e dados de teste existentes](docs/autenticacao-producao.md).

Não funciona ainda: entregas com divergências/retornos, disputa, auditoria global,
relógio controlado/reset de cenários, MFA/recuperação de senha,
integrações financeiras reais. Não confundir encerramento da rota
legada de GPS com confirmação de entrega. Web/mobile podem usar os novos contratos
para o recebimento integral; fluxos ainda ausentes devem ser claramente identificados,
sem misturar mocks com pedidos reais da API. Veja o [guia de cadastro e entrega](docs/demo-produtos-entrega.md).

## Stack e módulos

Python 3.13, FastAPI, Pydantic 2, SQLAlchemy 2 síncrono, psycopg 3, Alembic,
PostgreSQL 17, Uvicorn. Dependências fixadas em `uv.lock`; `requirements.txt` é
exportado desse lock. Routers só traduzem HTTP; serviços autorizam e transacionam.

```text
src/rotalog/
  api/              # bootstrap, OpenAPI, erros, CORS, request ID e health
  core/             # settings, banco, contratos e dinheiro em centavos
  identity/         # usuário, sessão, refresh rotativo e senha
  organizations/    # organizações, Membership e consulta de distribuidoras
  catalog/          # variante canônica, publicação, oferta e histórico de preços
  inventory/        # depósito, projeção, ajuste nominal, reserva e capacidade
  ordering/         # CheckoutSession, Purchase, SupplierOrder, OrderItem, Fulfillment
  finance/          # Payment e PaymentAllocation exclusivamente simulados
  operations/       # idempotência e arquivos privados; auditoria global pendente
  demo/             # seed idempotente e worker de expiração/validação de arquivos
  logistics/        # rotas, manifesto, conferência, custódia, prova e tracking
  disputes/         # reservado
  reviews/          # reservado
  integrations/     # reservado; sem chamadas a ERP/PSP/mapas reais
```

Dinheiro é `BIGINT amount_minor` e `currency CHAR(3)` no banco. A API usa strings,
por exemplo `"29.90"` com `"currency": "BRL"`; números JSON para dinheiro são
rejeitados. UUID4, TIMESTAMPTZ e quantidades inteiras são usados no domínio.

## Executar localmente com PostgreSQL 17

Instale Python 3.13 e uv. Crie dois bancos exclusivos no PostgreSQL 17: um para
a demo (`rotalog_demo`) e outro para testes (`rotalog_test`). Não aponte testes a
um banco com dados importantes.

```bash
cp .env.example .env
chmod 600 .env
# Preencha os campos vazios. Gere os secrets separadamente com:
python3.13 -c "import secrets; print(secrets.token_urlsafe(48))"

uv sync --locked
uv run --locked alembic upgrade head
uv run --locked python -m rotalog.demo.seed
uv run --locked uvicorn rotalog.api.main:app --host 127.0.0.1 --port 8000
```

Em outro terminal, com o mesmo ambiente/arquivo `.env`:

```bash
uv run --locked python -m rotalog.demo.worker
```

O worker também processa arquivos em quarentena, libera reservas de checkouts vencidos e expira pedidos ainda sem decisão,
fazendo o reembolso **simulado**. O relógio é real nesta versão; não há integração
com serviço financeiro ou matriz viária. A confirmação de pagamento também verifica
expiração sob lock, sem depender do worker para impedir captura de reserva vencida.

Abra [Swagger](http://127.0.0.1:8000/docs),
[OpenAPI](http://127.0.0.1:8000/openapi.json) e
[healthcheck](http://127.0.0.1:8000/health).

`ROTALOG_DATABASE_URL` é obrigatória inclusive no import. Use somente
`postgresql+psycopg://...`; credenciais de URL precisam de percent-encoding.
O seed lê `ROTALOG_DEMO_PASSWORD` do ambiente ou `.env`, ou pede senha se ausente. Não há senha
ou chave de assinatura padrão. Reexecutar o seed preserva IDs, senhas, preços e
estoque existentes; ele completa o dataset conhecido da demo após migração.

### Docker Compose

Preencha `.env`, incluindo `POSTGRES_PASSWORD`, `ROTALOG_JWT_SECRET` e
`ROTALOG_DEMO_PASSWORD`. Para Compose a URL do banco é construída internamente;
para comandos fora dos containers é necessário preencher a URL local separadamente.
Use senha PostgreSQL URL-safe no Compose.

```bash
docker compose up --build -d
docker compose logs api worker
```

O banco não é exposto à rede do host. A API fica em `127.0.0.1:8000`. Migração e
seed são executados antes de iniciar a API. Os dados ficam em volume persistente;
`docker compose down` preserva esse volume. Não use `down -v` para atualizar.

## Contas do seed e autenticação

Todas usam a senha fornecida por você ao seed:

| Papel | Email sintético |
|---|---|
| `ROTALOG_ADMIN` | `admin@demo.rotalog.invalid` |
| `BUYER_ADMIN` | `comprador@demo.rotalog.invalid` |
| `SUPPLIER_OPERATOR` | `fornecedor@demo.rotalog.invalid` |
| `DRIVER` | `motorista@demo.rotalog.invalid` |
| `ROUTE_PLANNER` | `planejador@demo.rotalog.invalid` |

Swagger **Authorize** usa `POST /api/v1/auth/login` com formulário `username`
(email) e `password`. O modo padrão, `client_type=native`, devolve access/refresh
no JSON. Use o Bearer nas rotas protegidas. JWT dura 10 minutos; refresh é opaco,
tem somente seu hash persistido, expira com a sessão em 30 dias e gira a cada uso.
Reutilização revoga a família. Logout, revogação de dispositivo e troca de senha
invalidam sessões imediatamente no PostgreSQL. Senhas usam Argon2id.

Navegador: use `/auth/login?client_type=browser` a partir de uma origem da allowlist.
O refresh fica em cookie host-only `HttpOnly`, `SameSite=Lax` por padrão, caminho `/api/v1/auth`;
não aparece no JSON. Access token fica em memória. Refresh/logout por cookie exigem
`Origin` autorizado e `X-CSRF-Token` recebido no login. Envie `credentials: include`.
O cliente deve serializar refreshes: repetir refresh antigo revoga a sessão.
Android usa Bearer/refresh no protocolo, sem cookie e sem depender de CORS.
No navegador local, use `localhost` para API e portal, ou `127.0.0.1` para ambos;
misturar os dois hosts torna o cookie cross-site e `SameSite=Lax` impede seu envio.
Para portal/API em sites distintos, configure `ROTALOG_COOKIE_SAMESITE=none`
com cookies seguros e HTTPS. A proteção por Origin e CSRF permanece obrigatória.

`GET /api/v1/me` lista organizações e capacidades. `X-Organization-Id` escolhe um
vínculo autorizado; se omitido, usa a organização inicial da conta. Cada caso de
uso revalida sessão e `Membership` na transação. UUID não é prova de acesso.

O autocadastro em `POST /api/v1/auth/register` aceita somente comprador e fornecedor.
Administradores podem provisionar administradores/planejadores com vínculo
organizacional em `POST /api/v1/admin/users` ou emitir convites de uso único
em `POST /api/v1/admin/invitations`. Veja os contratos no guia do frontend.
Motoristas são provisionados pelo planejador em `POST /api/v1/drivers`, com email,
nome e senha inicial de pelo menos 12 caracteres. Somente o modo de demonstração
exige domínio `.invalid`; produção aceita emails reais. A resposta
não inclui senha; compartilhe a senha inicial por canal privado e o motorista deve
alterá-la após entrar.

## Fluxo de teste comprador/distribuidora

Todos os caminhos abaixo têm prefixo `/api/v1` e constam no OpenAPI.

1. **Administrador:** login → `POST /admin/products` com variante completa →
   `POST /admin/products/{id}/publish` com `expected_version`.
2. **Distribuidora:** `POST /auth/register` com papel `SUPPLIER_OPERATOR` → login →
   `POST /suppliers/{organization_id}/warehouses`.
3. **Administrador:** `POST /warehouses/{id}/geocoding-review` confirma coordenadas
   sintéticas com versão e motivo. Não há geocodificador externo nesta demo.
4. **Distribuidora:** `POST /suppliers/{id}/offers` informa produto publicado,
   depósito confirmado, preço decimal e moeda. `POST /inventory/manual-adjustments`
   declara saldo/segurança com versão e motivo. Reservas não são editáveis.
5. **Distribuidora:** `POST /demo/capacity-slots` cria janela manual sintética.
6. **Comprador:** cadastro com `BUYER_ADMIN` → login → `GET /suppliers`,
   `GET /products`, `GET /offers` e `GET /capacity-slots`.
7. **Comprador:** `POST /checkouts` informa ofertas, quantidades e janela, com
   `Idempotency-Key`. A demo admite **um fornecedor e um depósito** por checkout.
8. **Comprador:** `POST /checkouts/{id}/confirm-payment` com `expected_version` e
   outra `Idempotency-Key`. Cria `Purchase`, `SupplierOrder`, `OrderItem`, pagamento
   e alocações em uma transação. O adaptador simula captura; nenhum PSP é chamado.
9. **Distribuidora:** `GET /supplier-orders` e detalhe mostram seus pedidos pagos.
   `POST /supplier-orders/{id}/acceptance` decide todas as linhas com versão e chave.
10. **Distribuidora:** `start-separation` → criação de `fulfillments` →
    `POST /fulfillments/{id}/separation-results` → `mark-ready`.
    Com um pedido pronto, fornecedor ou operação pode criar uma rota e atribuir o
    motorista. O motorista inicia a rota, envia pontos GPS sintéticos e a operação
    ou comprador consulta a última localização. A conclusão da rota encerra o envio;
    entrega, prova e retorno continuam fora do escopo.

Consulta de compras: `GET /purchases`, `/purchases/{id}` e
`/purchases/{id}/payments`. Resumo financeiro:
`GET /supplier-orders/{id}/financial-summary`.

O estoque disponível é `max(0, saldo vendável - reservas não refletidas - segurança)`.
O aceite nesta demo é integral (ou recusa total); `accepts_partial` deve ser `false`.
As projeções quantitativas já usam os conceitos da V5, mas parcialidade fica para o piloto.
Locks e constraints impedem overselling. Reserva tem TTL de 15 minutos; após
pagamento continua comprometida com o pedido. Recusa/expiração/cancelamento
elegível liberam saldo e capacidade, com reembolso simulado. Preço e embalagem
ficam em snapshot; mudanças posteriores na oferta não reprecificam uma compra.

Paginação: `page >= 1`, `page_size` padrão 25 e máximo 100. Percorra todas as páginas
para listar todas as distribuidoras. Erros usam `error.code/message/details/request_id`.
Versão desatualizada, estoque insuficiente ou chave reutilizada com outro payload:
409. Acesso sem permissão: 403; recurso privado de outra organização: 404.

### Teste por HTTP com o servidor rodando

```bash
# Usa a senha do administrador definida em ROTALOG_DEMO_PASSWORD.
uv run --locked python scripts/smoke_demo.py
```

Cria contas novas, a variante seca de **Pizza**, depósito/oferta/estoque e compra
de 3 unidades a R$ 29,90. Verifica pedido, preparação, saldo 20/3/17, idempotência,
isolamento e revogação, sem imprimir senha ou tokens. Cada execução preserva suas
fixtures sintéticas. Demo remota exige `--base-url https://... --allow-remote`.
Use `--browser-origin https://portal.SEUDOMINIO` para testar uma allowlist diferente.

## Testes e migrações

```bash
uv run --locked ruff check src tests migrations scripts
uv run --locked pytest
uv run --locked alembic check
```

Forneça `ROTALOG_TEST_DATABASE_URL` para banco exclusivo com `test` no nome; cada
teste cria/remove somente seu próprio schema. Sem URL, integração é ignorada
localmente e falha no CI. Testes incluem banco vazio, snapshots V4.1/ROT-004,
centavos sem perda, sessões, CSRF/CORS, permissões, concorrência, checkout,
pagamento repetido e pedido até prontidão. Workflows existentes não foram alterados.

A revisão `20261019_0004` converte preços e renomeia o saldo preservando dados.
Produtos históricos incompletos permanecem `DRAFT`; não são publicados inventando
atributos. Dados sem fornecedor conhecido não recebem depósito fictício nem ficam
vendáveis. Faça backup antes de migrar dados importantes. Downgrade após criação de
contas administrativas/sessões/compras V5 é bloqueado: use migração compensatória,
nunca apague transações para voltar uma versão.

Saldo reservado legado fica `RECONCILING` até reconciliação: não é zerado nem
transformado em pedido fictício. Novas compras desse saldo ficam bloqueadas.

## Demo online — não é homologação de produção

Use HTTPS com proxy reverso, mantendo banco e porta interna privados. Configure:

- `ROTALOG_ENVIRONMENT=demo`;
- `ROTALOG_COOKIE_SECURE=true`;
- `ROTALOG_DEBUG=false`;
- `ROTALOG_CORS_ORIGINS=["https://portal.SEUDOMINIO"]`, sem caminhos/curingas;
- secrets próprios protegidos, senha administrativa não distribuída ao cliente,
  backup e limites de requisição no proxy.

Exemplo de proxy está em `deploy/Caddyfile.example`. Cookies exigem portal/API no
mesmo site registrável nesta baseline (`SameSite=Lax`). Não troque para cookie
cross-site sem projetar/testar o protocolo CSRF correspondente. `staging` e
`production` continuam bloqueando a demo; o ambiente `demo` permite publicar apenas
esta demonstração sintética. Não há publicação automática nem garantia de prontidão
para dinheiro/dados reais: MFA, auditoria, recuperação de senha e outros controles
do piloto permanecem pendentes.

## Compatibilidade e clientes

Para testar manualmente, importe a [coleção e o ambiente Postman](docs/postman/LEIA-ME_POSTMAN.txt)
ou siga o [guia do Swagger](docs/postman/GUIA_SWAGGER_FASTAPI.txt). Os templates não contêm secrets preenchidos.

Veja [a matriz V5](docs/arquitetura/compatibilidade-v5-demo.md). Esta atualização
substitui as rotas V4.1 `/auth/me`, `/ofertas`, criação livre em `/products` e
`PATCH /offers/{id}/inventory` pelos comandos canônicos. Web/mobile devem atualizar
seus tipos e chamadas pelo `/openapi.json`; mocks devem ficar limitados às
funcionalidades explicitamente não implementadas.

O [registro de camadas globais](docs/arquitetura/2026-09-09-proposta-camadas-globais.md)
é histórico, não a arquitetura atual. Não existem entidades `Order`/`OrderLine`;
a demonstração já usa `Purchase`, `SupplierOrder`, `OrderItem` e `Fulfillment`.

## Repositórios relacionados

- Backend: https://github.com/Uninorte-Extensao/rotalog-backend
- Frontend: https://github.com/Uninorte-Extensao/rotalog-frontend
- Mobile: https://github.com/Uninorte-Extensao/rotalog-mobile
- Board: https://trello.com/b/4NmXZdXn/rotalog
