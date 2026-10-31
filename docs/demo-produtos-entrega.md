# Cadastro e acompanhamento até a entrega — demo V5

Atualização mobile: para conferência global, ocorrências pré-coleta, GPS canônico e
novos uploads privados, usar o [guia de integração mobile](mobile-integracao-v5.md).
Os contratos básicos abaixo permanecem como referência do fluxo anterior compatível.

Implementação nas branches `feat/demo-produtos-acompanhamento-entrega` de backend e frontend. Não altera workflows e não fecha ou mergeia PRs.

## Recorte e regras

- Produto canônico é uma variante de alimento seco industrializado, lacrado e com embalagem fixa. Administrador cadastra/publica; fornecedor cria sua oferta e informa saldo no inventário. Não há um segundo CRUD genérico de produtos do fornecedor.
- Oferta criada não implica saldo positivo. O saldo inicial continua sendo um ajuste versionado com justificativa. O inventário agora inclui nome/ID do produto e situação da oferta, inclusive para oferta pausada.
- Reserva acontece no checkout, não no aceite. Na coleta, a reserva correspondente é consumida e o estoque vendável declarado diminui; não se cria disponibilidade artificial. `Reservation.consumed_quantity` é separado de quantidade liberada/cancelada.
- Pedido e rota têm estados independentes. Despacho deriva das linhas coletadas; recebimento deriva de resultados com prova válida. Finalizar deslocamento não entrega o pedido nem libera pagamento.
- O recorte conclui recebimento **integral**. Recusas, ausências, falhas, retorno e reentrega não são encerrados como sucesso: permanecem para futura implementação operacional. Janela de contestação é registrada, mas o fluxo de disputa/liberação financeira ainda não está implementado.
- Planejamento é manual e validado, sem solver/matriz viária. A sequência e janelas são informadas pelo planejador; não há promessa de cálculo de tempo de viagem. Endereço de destino é um snapshot sintético declarado nessa proposta, não uma integração de geocodificação real.

## Preparação local

1. Use as branches correspondentes nos dois repositórios e mantenha os secrets no `.env`/ambiente.
2. Instale as dependências com `uv sync --locked` (Python 3.13).
3. Com PostgreSQL/PostGIS 17 disponível, execute `uv run --locked alembic upgrade head` e `uv run --locked python -m rotalog.demo.seed`.
4. O seed continua idempotente e passa a criar também `planejador@demo.rotalog.invalid`, papel `ROUTE_PLANNER`, com a senha sintética configurada no ambiente. Não sobrescreve senhas de usuários existentes.
5. Inicie API e worker conforme o README. No Compose, `docker compose up --build -d` aplica a migration e o seed pelo bootstrap existente. **Não execute `down -v` para atualizar.**
6. No frontend: `npm ci`, `VITE_AUTH_MODE=api`, `VITE_API_URL=http://localhost:8000` e `npm run dev`. O backend deve permitir exatamente a origem do portal no CORS.

A migration `20261023_0007` acrescenta tabelas sem converter/excluir rotas e pontos GPS antigos. Estados legados não são provas. Downgrade com dados operacionais ou planejador é bloqueado para evitar perda silenciosa. Não execute migrações de teste sobre o banco da demo.

## Roteiro pelo Swagger/Postman

Prefixo de todos os caminhos abaixo: `/api/v1`. Entre em `/docs`, faça login OAuth2 e use **Authorize** com cada ator. Para alternar ator, substitua a autorização; não reutilize o bearer de outra conta. Comandos críticos exigem `Idempotency-Key` (8–200 caracteres) e `expected_version` obtido da última consulta. Reenvio com mesma chave/conteúdo retorna a resposta anterior; mesma chave/conteúdo diferente gera conflito.

### 1. Administrador e fornecedor: produto, oferta e saldo

1. Administrador: `POST /admin/products` com nome, marca, categoria `ALIMENTO_SECO_INDUSTRIALIZADO`, conteúdo/unidade, embalagem, quantidade por embalagem, peso bruto em gramas, volume em cm³ e atributos. Exemplo: arroz lacrado de marca sintética.
2. Administrador: `POST /admin/products/{id}/publish`, com versão do produto.
3. Fornecedor: `GET /products` e `GET /warehouses`. Se necessário, criar depósito em `POST /suppliers/{organization_id}/warehouses` e pedir revisão administrativa por `POST /warehouses/{id}/geocoding-review`.
4. Fornecedor: `POST /suppliers/{organization_id}/offers` com `product_id`, `warehouse_id`, `unit_price` string (`"29.90"`), `currency: "BRL"` e quantidade mínima inteira.
5. Fornecedor: `POST /inventory/manual-adjustments` com saldo vendável, estoque de segurança, motivo e versão. Depois consultar `GET /inventory` para verificar.
6. Preparar janela/capacidade sintética pelos mecanismos existentes (`/demo/capacity-slots`, `/capacity-slots`). Sem capacidade válida, a compra não deve prosseguir.

Se a oferta for criada e o ajuste falhar, corrija pelo inventário: não repita automaticamente o cadastro da oferta. Criação de oferta continua não idempotente.

### 2. Comprador e fornecedor: gerar e preparar o pedido

1. Comprador: criar checkout com oferta/quantidade e capacidade, confirmar pagamento **simulado** e guardar `supplier_order_ids` da resposta.
2. Fornecedor: consultar `/supplier-orders/{id}`, aceitar todas as linhas, iniciar separação, criar fulfillment, registrar separação e marcar pronto pelos endpoints existentes.
3. Consultar novamente e verificar `READY_TO_DISPATCH`, quantidades e versões. Não montar rota de pedido que ainda não foi preparado.

### 3. Planejador: rota manual

O planejador e o motorista do seed operam apenas para o fornecedor do seed.
Para uma distribuidora nova, o administrador deve criar um planejador vinculado
a ela por `POST /admin/users` ou convite; esse planejador cria seu motorista em
`POST /drivers`. Use essas contas no fluxo abaixo. Consulte também
`GET /route-planning/eligible-fulfillments` para obter os lotes prontos da origem.
Veja [os contratos e a migração de isolamento](frontend-pendencias-v5.md).

1. Login `planejador@demo.rotalog.invalid`; consultar `GET /drivers` para o motorista sintético ativo.
2. `POST /vehicles` com identificação sintética única e capacidades inteiras `package_units`, `weight_grams`, `volume_cm3`; consultar `/vehicles` para reutilizar veículo.
3. `POST /route-plan-versions` é uma extensão explícita da demo para criar a proposta manual, sem chamar um otimizador:

```json
{
  "warehouse_id": "UUID_DO_DEPOSITO",
  "driver_id": "UUID_DO_MOTORISTA",
  "vehicle_id": "UUID_DO_VEICULO",
  "fulfillment_ids": ["UUID_DO_LOTE_PRONTO"],
  "reason": "Rota manual sintética para demonstração",
  "deliveries": [{
    "supplier_order_id": "UUID_DO_PEDIDO",
    "address": "Destino fictício de demonstração, Manaus",
    "latitude": "-3.100000",
    "longitude": "-60.000000",
    "starts_at": "SUBSTITUIR_POR_INSTANTE_COM_FUSO",
    "ends_at": "SUBSTITUIR_POR_INSTANTE_POSTERIOR_COM_FUSO",
    "time_zone": "America/Manaus",
    "service_seconds": 300
  }]
}
```

4. `POST /route-plan-versions/{id}/approve`; depois `/publish`, com versões atualizadas e chaves de idempotência diferentes por comando. Aprovação e publicação são ações separadas de `ROUTE_PLANNER`; administrador/fornecedor não recebem essa capacidade por conveniência.
5. A API valida origem única, lote pronto, ausência de outra alocação, janelas e capacidade. Motorista/veículo ocupados impedem publicar outra rota ativa. A publicação cria coleta inicial, entregas, atribuição e vínculos aos lotes.

### 4. Motorista: aceite, coleta e transporte

1. Login `motorista@demo.rotalog.invalid`.
2. `GET /routes` e `GET /routes/{id}`; copiar a versão atual.
3. `POST /routes/{id}/accept`, depois `/pickup` e `/start`. Atualizar a versão a cada resposta.
4. A coleta registra custódia e despacho das quantidades; o pedido passa a `DISPATCHED` ou `PARTIALLY_DISPATCHED`. O fornecedor acompanha em `/supplier-orders/{id}`, que contém `delivery_routes`, e na timeline.

O tracking GPS legado continua disponível, sem associação automática às rotas canônicas. O incremento mobile oferece `/routes/{id}/location-batches` e `/tracking/current`/`history` para as rotas novas. Não usar a localização de uma rota legada como prova de outra; consultar o guia mobile para os contratos e permissões.

### 5. Motorista e comprador: recebimento integral comprovado

1. Motorista inicia `POST /route-stops/{id}/delivery-attempts` na parada `DELIVERY`, com versão da rota e `device_id`. Consultar a rota revela `attempt_id`, estado e versão da tentativa.
2. Comprador/recebedor autenticado da organização do pedido chama `POST /delivery-attempts/{id}/receiver-authorizations` com sua função no recebimento. Este endpoint é uma extensão da demo para emissão do código exigido pelo domínio V5. Recebe código de seis dígitos, expira em dez minutos; compartilha-o com o motorista somente após conferir a mercadoria. O código não é público, não fica em timeline e é armazenado apenas como HMAC.
3. Motorista envia foto(s) e assinatura em `POST /delivery-attempts/{id}/evidence?kind=PHOTO` ou `kind=SIGNATURE`, multipart `file`. São imagens PNG/JPEG reais do cenário sintético, não paths/URLs informados pelo cliente.
4. Motorista chama `POST /delivery-attempts/{id}/complete` com versão da tentativa, horário de captura com fuso, coordenadas, precisão, código, IDs dos arquivos, condição visível, volumes, observações e **todas** as linhas do lote/parada com `buyer_accepted_quantity` igual ao despachado e recusa zero.
5. Prova incompleta entra em `PROOF_PENDING`, sem quantidade entregue ou início de contestação. Completar depois exige nova versão e nova chave. Código errado/expirado não confirma entrega; cinco falhas bloqueiam até nova emissão após expiração.
6. Prova válida grava resultados por linha, vínculos de evidência e custódia do comprador; atualiza o pedido para `DELIVERED` quando todas as quantidades aceitas foram entregues. Registra janela de contestação de 24 horas; **não libera pagamento**.
7. Fornecedor/comprador consultam `GET /delivery-attempts/{id}/proof` e arquivos autenticados por `/delivery-evidence/{id}`. Não há link público permanente.
8. Motorista encerra deslocamento por `/routes/{id}/end-execution`. Só depois de todas as paradas comprovadas pode chamar `/complete-operation`. Encerrar deslocamento antes da prova é permitido; encerrar a operação com prova pendente é recusado.

## Dashboard do fornecedor

O quadro reconhece `PARTIALLY_DISPATCHED`, `DISPATCHED`, `PARTIALLY_DELIVERED` e `DELIVERED`. O detalhe mostra rotas, eventos, quantidades, recebedor e downloads autenticados de prova. O polling usa 15 segundos com aba visível, recuo após erro, atualização ao voltar ao foco e limpeza no logout. Falha de rede preserva dados anteriores com alerta de desatualização; não simula entrega.

A área web do comprador e o app Android não foram implementados neste incremento. Seus atores podem testar os contratos pelo Swagger/Postman. Isso não comprova a integração dessas interfaces. A prova apresentada deve continuar marcada como sintética.

## Segurança dos arquivos da demo

Blobs antigos continuam privados no PostgreSQL. Novos arquivos usam o volume privado compartilhado com o worker; o multipart mantém o contrato como adaptador de compatibilidade. Mantêm-se limites de dez associações por tentativa, 8 MiB por arquivo e 16 megapixels, validação real de formato, hashes e download autenticado. O ciclo assíncrono para novos clientes está no guia mobile. Referência: [segurança do Pillow](https://pillow.readthedocs.io/en/stable/handbook/security.html).

O processamento é para a demo: **não é object storage com scanner de malware do piloto**. Não usar fotos/assinaturas de pessoas reais nem publicar como solução pronta para produção. Há trilha transacional destes eventos, não auditoria global do projeto.

## Verificação e pendências conhecidas

```bash
uv run --locked ruff check src tests migrations scripts
# Defina ROTALOG_TEST_DATABASE_URL com banco dedicado contendo 'test' no nome.
uv run --locked pytest
# Smoke HTTP opt-in, contra API já iniciada e apenas com dados sintéticos:
uv run --locked python scripts/smoke_demo.py --with-delivery
```

Os testes novos exercitam cadastro/compra/preparação, coleta, consumo da reserva, entrega, idempotência, prova pendente, limite do código e acesso privado. Cada integração usa schema isolado e não apaga `public`. Sem banco configurado são ignorados, portanto uma execução com skips não valida o fluxo de entrega.

Permanecem fora da conformidade integral V5: relógio controlado e jobs de prova pendente, prazo de aceite atual de 24 horas versus 2 horas úteis, calendário operacional estruturado, perfil/habilitações avançados de motorista, retornos/disputas/reentrega, liberação financeira e storage/scanning do piloto. O fluxo normal integral usa os conceitos canônicos, mas a V5 completa não está concluída.

## Resultado da validação em 23/10/2026

- Backend: **65 testes passaram, sem skips**, em PostgreSQL/PostGIS 17 dedicado. Incluem migração de banco vazio e snapshots anteriores, inclusive preservação de rotas GPS/localizações/reservas da revisão `20261022_0006`.
- Smoke em Uvicorn real: **65 verificações HTTP passaram**, incluindo cadastro de distribuidora/comprador, produto/oferta/saldo, compra de R$ 89,70, preparo, rota manual, coleta, entrega comprovada e encerramento operacional. Saldo inicial 20; três unidades entregues; saldo final 17; reservado final zero.
- Frontend: typecheck, testes, lint e build passaram. Verificação em Chromium confirmou login, pedido entregue, prova válida, download privado autenticado e persistência após reload, sem erros de JavaScript.
- Avisos não bloqueantes: depreciação de httpx pelo TestClient da versão instalada de Starlette; build do frontend informa chunks maiores que 500 kB. Não foram alterados workflows para ocultar esses avisos.
- Foi usado somente um banco de teste separado, com schemas isolados. A base da demo existente não foi migrada nem alterada nesta validação. O teste não comprova infraestrutura de produção ou integração das interfaces de comprador/Android.

## Comparação do recorte com a V5

| Contraparte V5 | Resultado neste incremento |
|---|---|
| Produto-variante, oferta e inventário separados (§12) | Preservados; portal orienta o cadastro e lê a identificação do produto mesmo com oferta pausada. |
| Dinheiro em centavos e strings no JSON (§11) | Preservado; não foi introduzido ponto flutuante monetário. |
| Despacho por quantidades e custódia (§10) | Coleta consome reserva e registra quantidades/eventos; não é somente uma troca de status. |
| Rota manual aprovada/publicada e execução separada (§14) | Implementadas para a demo. Frota/perfil operacional completos, solver, matriz viária e habilitações continuam fora deste recorte. |
| Entrega integral com autorização, evidência privada e linhas (§16) | Implementada; prova incompleta não entrega/libera recursos. Recusa/ausência/retorno não são encerrados automaticamente. |
| Consulta e timeline autorizadas (§21) | Implementadas e consumidas no dashboard do fornecedor; comprador/motorista podem testar via Swagger. |
| Storage e segurança do piloto | Parcial: adapter PostgreSQL privado da demo, sem scanner/URLs assinadas/MFA de produção. |

Portanto, o fluxo normal foi implementado sem trocar os conceitos comerciais por estados logísticos improvisados. Isso **não** declara atendidos todos os critérios da demonstração integral ou do piloto V5.
