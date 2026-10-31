# Integração mobile — demo online V5

Incremento exclusivo do backend. Base: `ANALISE-BACKEND-INTEGRACAO-ROTAS.md`, revisada contra a PR #13 e V5 §§9, 10, 14–16, 20–24 e 26. O diagnóstico analisava `cf58f7d`; rotas canônicas e prova integral já existiam em `2b05966` e foram reaproveitadas, não recriadas.

## Compatibilidade e limites

- Nenhum endpoint anterior foi removido nem recebeu novos campos obrigatórios. Login, dinheiro, catálogo, estoque, compra e pedidos mantêm seus contratos. Respostas recebem campos adicionais; clientes não devem rejeitar propriedades desconhecidas.
- `/driver/delivery-routes/*` continua sendo GPS legado. Não é alias de `/routes/*`, não se devem misturar IDs ou usar seu `complete` como comprovante de entrega. Não houve migração automática de rotas/GPS legados para rotas canônicas.
- O mobile novo deve usar `/routes` e iniciar explicitamente a conferência por `/cargo-inspection`. A partir disso, inclusive `/pickup` verifica a declaração global atual e divergências. A inscrição também acontece ao registrar uma ocorrência ou declaração global válida.
- Para clientes anteriores, a sequência `/accept → /pickup → /start` e o body `{"expected_version": n}` permanecem válidos em rotas não inscritas na conferência. Não se fabrica uma declaração de conferência em nome desses usuários. É uma transição de contrato explícita; não alegar que o fluxo legado comprova conferência global.
- `/cargo-receipts` sempre exige conferência válida. Não existe opção de ignorar divergência; nenhuma regra crítica depende apenas de um checkbox no Android.
- Uma origem/fornecedor por rota, várias entregas, motorista e veículo exclusivos enquanto a operação não encerra. Mantido planejamento manual com aprovação/publicação separadas. Mudança da carga invalida o fingerprint aprovado e exige restaurar a carga ou cancelar/replanejar/reaprovar, não editar o plano em uso.
- Demo online com recebimento integral. Retornos, reentrega, disputa, parcialidade operacional completa, otimização viária e offline Android completo continuam fora deste incremento. Não se cria parada de retorno fictícia quando não há mercadoria retornando. Casos não suportados ficam pendentes, nunca concluídos artificialmente.
- APIs e armazenamento destinam-se a dados sintéticos. Este recorte não certifica prontidão do piloto V5.

## Contratos e identidade

Todos os caminhos abaixo têm prefixo `/api/v1`. Usar `/docs` ou `/openapi.json` da versão nova; manifesto, linhas, divergências e tracking têm schemas de resposta explícitos.

Login nativo existente: `POST /auth/login?client_type=native`, formulário `username=<email>&password=<senha>`. Utilizar bearer token e refresh rotativo; serializar renovações no cliente. Não há autenticação por matrícula/telefone nem cadastro público de motorista. O planejador provisiona motoristas sintéticos por `POST /drivers`; o seed também provisiona o motorista e o planejador da demo com a senha definida pelo responsável, sem defaults.

`GET /me` passa a publicar capacidades logísticas do `DRIVER` e `cargo:read/resolve` do fornecedor. Elas ajudam a interface, mas a API continua revalidando sessão, organização e atribuição em cada comando. Motorista não resolve a própria divergência; o planejador não recebe essa permissão por conveniência.

## Rotas, detalhe e histórico

- `GET /routes?page=1&page_size=25`: página com `items`, `total`, `page`, `page_size`, não uma lista simples.
- Filtros aditivos: `active=true` para rotas não encerradas; `active=false` para `OPERATIONALLY_COMPLETED/CANCELLED`; `state=STARTED` ou outro estado canônico para um filtro exato.
- `GET /routes/{id}`: atribuição, código de exibição derivado do UUID, data programada em Manaus, veículo/capacidades para atores operacionais, paradas ordenadas e seus itens físicos, resumo de conferência, ações disponíveis e horários de início/fim.
- Consultas de listagem/detalhe retornam tracking resumido para motorista, fornecedor e planejador. No comprador, utilizar a consulta específica de posição, que aplica a janela de visibilidade. Respostas de comandos podem não incluir esse resumo; consultar novamente para reconciliar.
- `GET /routes/{id}/manifest`: linhas por `fulfillment_line_id`, não somente `product_id`; quantidades e embalagens, versão/fingerprint, conferência, ocorrências e recebimento. Somente motorista atribuído, fornecedor participante e planejador. Sem preços/alocações comerciais.
- O mesmo produto em dois pedidos continua sendo duas linhas. `quantity_unit=SELLABLE_PACKAGE` significa embalagem comercial vendável; conteúdo interno, peso e volume são dimensões distintas. Não contar coleta e entrega como duas cargas.
- Destinos aceitam opcionalmente `recipient_name`, `contact_name`, `contact_phone`, `reference`, `instructions`. Dados ausentes permanecem ausentes; não gerar telefone, matrícula ou destinatário fictício como se viessem do cadastro. Endereço/coordenadas/janela continuam snapshots da proposta.
- Abrir tela, navegar para uma etapa anterior ou selecionar parada é apenas leitura: nenhum GET altera progresso.

Equivalências para o adaptador Android: `PUBLISHED` corresponde à atribuição; `ACCEPTED` ao aceite; `READY_TO_START` à carga recebida; `STARTED` à rota ativa. `EXECUTION_ENDED` não é operação concluída; `OPERATIONALLY_COMPLETED` é o encerramento reconciliado. Não renomear estados no servidor para coincidir com enums locais.

## Conferência, divergências e recebimento

Comandos exigem `Idempotency-Key` de 8–200 caracteres. Guardar a chave e o mesmo payload até reconciliar a resposta; não gerar outra chave automaticamente após timeout. `expected_version` é a versão da rota; `manifest_version` é a versão da carga/conferência. Usar os valores recebidos, sem incrementá-los manualmente no aplicativo.

1. `POST /routes/{id}/accept`, body `{"expected_version": 1}`. Aceita a atribuição, sem coletar/iniciar.
2. `POST /routes/{id}/cargo-inspection`, body com `expected_version` atual. Inscreve no fluxo estrito; recebe o manifesto. Não conclui a coleta.
3. Conferir **todas** as linhas e enviar `POST /routes/{id}/cargo-checks`:

```json
{
  "expected_version": 3,
  "manifest_version": 1,
  "declaration": true,
  "checked_line_ids": ["UUID_DE_CADA_LINHA_DO_MANIFESTO"]
}
```

Valores de versão são exemplos, não constantes. IDs devem conter exatamente todas as linhas atuais, sem duplicação; não enviar apenas o produto selecionado. `declaration` precisa ser booleano `true`. Manifesto vazio, incompleto, alterado ou com divergência aberta não permite declarar/receber.

4. Se encontrar problema, `POST /routes/{id}/cargo-issues` (`201`):

```json
{
  "expected_version": 4,
  "manifest_version": 1,
  "fulfillment_line_id": "UUID_DA_LINHA",
  "expected_quantity": 3,
  "observed_quantity": 2,
  "category": "MISSING",
  "reason": "Uma embalagem faltante na conferência",
  "photo_ids": []
}
```

Categorias: `MISSING`, `EXCESS`, `DAMAGED`, `WRONG_PRODUCT`, `OTHER`. Esperado vem do manifesto, não do cliente; falta/sobra exigem comparação coerente. Foto é opcional nesta etapa, não na prova de entrega. Fotos informadas precisam estar `AVAILABLE`, ter finalidade `CARGO_ISSUE_PHOTO`, pertencer à rota e ao motorista e não estar associadas a outra ocorrência.

5. **Fornecedor responsável**, após corrigir fisicamente a carga: `POST /routes/{id}/cargo-issues/{issue_id}/resolve`, com versões atuais e `correction` descrevendo a correção. Nesta demo, resolver significa restaurar a carga ao manifesto aprovado: não autoriza substituir produto, reduzir quantidade, reescrever estoque ou ignorar ocorrência. Correção diferente exige novo planejamento/aprovação.
6. Registrar **ou resolver** uma ocorrência incrementa a versão do manifesto e invalida toda a declaração anterior. O motorista precisa conferir novamente todas as linhas, inclusive a afetada, e reenviar `/cargo-checks`.
7. `POST /routes/{id}/cargo-receipts`, body `expected_version`, `manifest_version`, `declaration: true`. Valida conferência atual do motorista e ausência de pendências, registra recebimento/custódia, consome as reservas correspondentes e conclui somente `PICKUP`. Estado fica `READY_TO_START`; não inicia tracking.
8. `POST /routes/{id}/start`, com versão atual. Só então inicia deslocamento/tracking.

Depois de receber, manifesto/ocorrências são somente leitura, inclusive para fornecedor. Declarações e resoluções têm trilha transacional com ator, papel, organização, motivo, versões, antes/depois essencial, quantidades, request ID e chave. Estado de envio/salvamento no Android continua separado do estado operacional confirmado pelo servidor.

## GPS canônico

- `POST /routes/{id}/location-batches` identifica a repetição por `client_batch_id`.
- `GET /routes/{id}/tracking/current` retorna estado, precisão/horário do ponto e `is_stale`.
- `GET /routes/{id}/tracking/history?page_size=50&cursor=...` retorna histórico por cursor, para motorista/fornecedor/planejador autorizados; não expõe a trajetória completa de vários compradores ao comprador.
- `POST /routes/{id}/tracking-status`, com `status` igual a `DEGRADED_PERMISSION`, `DEGRADED_OFFLINE` ou `ACTIVE`, sinaliza condição do cliente sem inventar GPS. Declarar `ACTIVE` não torna um ponto antigo atual.

```json
{
  "client_batch_id": "UUID_UNICO_DO_LOTE",
  "device_id": "identificador-opaco-do-dispositivo",
  "points": [{
    "sequence_no": 1,
    "captured_at_device": "2026-10-23T12:00:00-04:00",
    "latitude": -3.100000123,
    "longitude": -60.000000123,
    "accuracy_meters": 10.123,
    "is_mocked_by_os": false
  }]
}
```

Substituir horário/UUID pelos reais do cenário. `route_id` e `driver_id` opcionais, se enviados, devem coincidir com URL/sessão. Até cem pontos por lote; vinte lotes/minuto por rota/motorista por padrão. Coordenadas/precisão de double Android são normalizadas para a persistência. Respostas representam coordenadas/medidas decimais como strings documentadas no OpenAPI.

O lote e cada ponto têm unicidade/hash. Mesmo lote/conteúdo retorna o resultado anterior; reutilização com conteúdo diferente gera conflito. Resultados por ponto: `STORED`, `DUPLICATE`, `REJECTED`; analisar também `flags`, não tratar HTTP 200 como aceitação de todos os pontos.

Pontos fora de ordem não regridem a posição. A sequência é monotônica por rota/dispositivo; a projeção escolhe o maior número válido recebido (desempate por captura). Troca de dispositivo não reinicia silenciosamente essa referência: o cliente deve preservar a continuidade da numeração da rota. Os limites de estado, sessão e atribuição continuam sendo conferidos em todo reenvio.

Baixa precisão, mock, relógio incompatível e salto/speed implausível são sinalizados e preservados, sem sanção automática e sem substituir a posição confiável. A projeção atual não utiliza pontos sinalizados. Nenhum GPS altera `Route.version`.

Só se aceita captura no período ativo registrado. Após `EXECUTION_ENDED`, pontos capturados antes do fim podem chegar durante a janela técnica de até 24 horas. Novas capturas após o fim são rejeitadas. O estado final continua `ENDED`, mesmo recebendo backlog. Comprador só consulta posição durante rota ativa e enquanto sua entrega está pendente; não acompanha outras entregas após seu recebimento.

Estados: `NOT_STARTED`, `ACTIVE`, `STALE`, `DEGRADED_OFFLINE`, `DEGRADED_PERMISSION`, `ENDED`. Sem ponto confiável, não existe marcador inventado. O portal pode usar polling; não há WebSocket, cálculo de rua ou ETA.

Configurações validadas: `ROTALOG_TRACKING_STALE_SECONDS=120`, `ROTALOG_TRACKING_SYNC_WINDOW_SECONDS=86400` (teto de 24 h), `ROTALOG_TRACKING_ACCURACY_WARNING_METERS=100`, `ROTALOG_TRACKING_BATCHES_PER_MINUTE=20`. Compose compartilha as mesmas configurações entre API/worker.

## Prova de entrega e arquivos

Reutilizar `/route-stops/{id}/delivery-attempts`, autorização do recebedor, conclusão e consulta de prova existentes. Os itens físicos nas paradas fornecem os `fulfillment_line_id` necessários, sem liberar pedidos comerciais ao motorista.

Novos clientes enviam arquivos pelo ciclo comum:

1. `POST /files/upload-intents`: finalidade `DELIVERY_PHOTO` ou `DELIVERY_SIGNATURE`, `attempt_id`, tamanho, MIME e SHA-256; para foto de conferência, finalidade `CARGO_ISSUE_PHOTO` e `route_id`.
2. `PUT` binário no destino retornado; depois `POST /files/{id}/confirm-upload`.
3. Consultar `GET /files/{id}` até `AVAILABLE`. O worker valida fora da transação, com quarentena e lease recuperável. `REJECTED` não pode ser associado.
4. Enviar os IDs disponíveis na conclusão da tentativa ou em `photo_ids` da ocorrência. Não reutilizar `PRODUCT_IMAGE` como evidência.

Produtos continuam usando `product_id`; cada finalidade exige exatamente um contexto. Os limites e o volume privado usam as variáveis `ROTALOG_PRODUCT_IMAGE_*`/`ROTALOG_PRODUCT_IMAGES_ROOT` existentes por compatibilidade, agora também para mídias operacionais.

O multipart anterior `/delivery-attempts/{id}/evidence` mantém o `201` e os campos anteriores: é adaptador síncrono da demo que aguarda o mesmo processador sintético para seu arquivo, sem manter SQL aberto durante envio/decodificação. Novos registros usam volume privado. Blobs legados existentes permanecem acessíveis e não são apagados nem declarados retroativamente escaneados. Os downloads antigos continuam autenticados.

Prova completa exige autorização válida do recebedor, foto, assinatura, localização e todas as linhas integralmente recebidas. Prova pendente não entrega o pedido nem libera dinheiro. `/end-execution` encerra deslocamento; `/complete-operation` exige todas as paradas reconciliadas. Não converter exceções/parcialidade em entrega integral para fazer a interface avançar.

O scanner continua `SYNTHETIC_IMAGE_VALIDATION_ONLY`, não antivírus. Storage S3, scanner real, retenção/hold completos, política LGPD e demais gates continuam obrigatórios antes de dados reais.

## Preparar e testar localmente

Aplicar `alembic upgrade head` (incluindo a revisão de isolamento `20261031_0010`), executar o seed configurado e iniciar API e worker. Não apagar volumes, não editar dados operacionais diretamente no banco e não executar testes sobre o banco da demo.

```bash
# Com API e worker disponíveis e secrets configurados no ambiente:
.venv/bin/python scripts/smoke_demo.py --with-mobile

# Para preparar uma rota publicada e deixá-la para o app, sem aceitar/coletar:
.venv/bin/python scripts/smoke_demo.py --prepare-mobile
```

Esses comandos são opt-in e **criam dados sintéticos persistentes**. O segundo mantém o motorista ocupado nessa rota até encerramento/cancelamento autorizado. Cada cenário agora provisiona planejador e motorista vinculados ao seu fornecedor. Configure previamente `ROTALOG_SMOKE_ACCOUNT_PASSWORD` (12–1024 caracteres) para entrar nas contas de comprador, fornecedor, planejador e motorista. Sem isso, a senha dessas contas é aleatória e não exportada. O relatório de `--prepare-mobile` mostra seus emails em `synthetic_accounts`, nunca senhas/tokens. `ROTALOG_DEMO_PASSWORD` autentica a conta administrativa do seed, sem ser copiada para essas contas. Não commitar esses valores. Em destino remoto, é exigido `--allow-remote` explícito.

`--with-mobile` percorre cadastro, compra simulada, preparação, publicação/aprovação, conferência, falta sintética, resolução pelo fornecedor, reconferência, recebimento, início, lote GPS idempotente, prova processada pelo worker e encerramento. Falta de worker não é tratada como sucesso. `--with-delivery` continua disponível para validar o adaptador anterior.

No emulador/dispositivo, `127.0.0.1` não é o host. É possível usar `adb reverse tcp:8000 tcp:8000` no ambiente de desenvolvimento, mantendo a API local. HTTP sem TLS deve ficar restrito ao desenvolvimento; online exige HTTPS. CORS do navegador não é controle de acesso do Android.

Integração Android ainda depende da equipe mobile: cliente HTTP/DTOs, login real, refresh serializado, substituição do repositório mock, permissões/contexto de GPS, cache, reconciliação e mensagens de erro. Não houve alteração nesse repositório.

## Verificação, migração e pendências

- Testes HTTP usam PostgreSQL/PostGIS dedicado e schemas descartáveis; verificam permissões, manifesto, conferência, ocorrências, concorrência, GPS, arquivos e entrega integral.
- Snapshot estrutural das 82 operações de `2b05966` verifica preservação de caminhos/métodos, parâmetros e corpos obrigatórios anteriores e campos de resposta. Isso complementa os testes funcionais, mas não certifica clientes externos não executados aqui.
- Migration é aditiva, preserva imagens de produtos e blobs de prova existentes e recupera horários de rotas anteriores a partir dos eventos registrados. Sem evidência de início anterior, não inventa período ativo para aceitar GPS.
- Downgrade com conferências, eventos, telemetria ou arquivos operacionais é bloqueado para preservar dados.
- Não houve mudança em workflows ou repositórios de frontend/mobile.
- Retenção e expurgo automáticos de GPS, reconstrução operacional da projeção, dispositivos com relógio muito incorreto, reatribuição em execução, offline Android, retornos/disputas e auditoria global permanecem evolução do piloto. Não usar o banco sintético como repositório de trajetórias de pessoas reais.

Erros principais: `401/403` para sessão/capacidade; `404` para recurso alheio; `409` para versões, pendências, carga alterada ou repetição incompatível; `422` para payload inválido; `429` para limite. Em conflito real, reler o recurso e pedir nova ação, sem retry cego nem sucesso simulado.

Para executar a suíte, em terminal com `ROTALOG_TEST_DATABASE_URL` definida para um PostgreSQL/PostGIS dedicado cujo nome contenha `test`:

```bash
ROTALOG_DATABASE_URL="$ROTALOG_TEST_DATABASE_URL" .venv/bin/pytest --tb=short
.venv/bin/ruff check src tests migrations scripts
```

### Resultado local — 23/10/2026

- Suíte completa: **93 testes aprovados**, sem skips; um aviso de depreciação do TestClient/httpx, sem falhas. A condição de somente leitura antes do aceite também foi validada em execução focada.
- API Uvicorn e worker em processos reais: **108 verificações HTTP** até entrega comprovada e operação encerrada, incluindo o novo fluxo de conferência e GPS.
- Modo `--prepare-mobile`: **51 verificações HTTP**, mais consulta autenticada da rota/manifesto; cenário ficou `PUBLISHED`, sem coleta/início automáticos.
- `alembic check` sem novas operações, upgrades a partir de snapshots e preservação de blobs/arquivos anteriores; SQL offline, compilação, lint, formatação dos arquivos alterados e `docker compose config --quiet` aprovados.
- A execução real identificou e corrigiu o registro incompleto de modelos no bootstrap do worker; há regressão que inicia `python -m rotalog.demo.worker --once` em subprocesso separado.
- Somente banco dedicado/schema e arquivos temporários foram usados. Não houve atualização/restart da demo existente, alteração de workflows ou repositórios externos, commit, push ou mudança de PR. Aplicar a revisão `20261023_0009` e reiniciar API/worker é necessário para ativar este incremento no ambiente de uso.
