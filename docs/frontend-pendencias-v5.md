# Demandas do frontend V5 — levantamento e contratos

Origem: `rotalog_v5_endpoints_pendentes_backend.pdf`, recebido em 31/10/2026.
O PDF enumera prioridades e caminhos sugeridos, deixando payloads, erros,
casos especiais e autenticação de produção para definição posterior.
Este documento registra as decisões implementadas. O OpenAPI contém os schemas.

## Levantamento

| Prioridade | Demanda | Situação anterior | Alteração |
|---|---|---|---|
| P0 | Lotes elegíveis | Sem consulta para o planejador | Consulta paginada de lotes prontos, não alocados, do fornecedor |
| P0 | Isolamento | Planejador acessava rotas, propostas e frota da execução inteira | Propriedade por fornecedor e autorização transacional |
| P0 | Aprovar/publicar | Comandos existentes sem isolamento do planejador | Contratos preservados, com validação do fornecedor |
| P1 | Categorias | Literal fixo no schema | Cadastro administrativo e consulta de códigos |
| P1 | Produtos | Criar/publicar, sem gestão posterior | PATCH versionado, UNPUBLISHED e lista administrativa |
| P1 | Usuários/convites | Autocadastro comprador/fornecedor e criação de motorista | Provisionamento de administrador/planejador e convite de uso único |
| P1 | Autenticação de produção | Identidade exclusiva da demo | Cadastro/login/sessões com emails reais; bootstrap e compatibilidade com dados de teste |

## Convenções

Prefixo `/api/v1`. Bearer obrigatório, exceto no aceite de convite, que exige
seu segredo. `X-Organization-Id` seleciona um Membership ativo; sua omissão usa
a organização inicial. O corpo não escolhe o fornecedor de planejamento/frota.

Paginação: `page >= 1`, `page_size` de 1 a 100, padrão 25. Resposta:
`{items, total, page, page_size, is_synthetic, label}`. Envelope de erros:
`error.code/message/details/request_id`. Credencial inválida: 401; papel/vínculo
inadequado: 403; recurso privado de outro fornecedor: 404; conflito de versão,
estado/duplicidade: 409; payload inválido: 422.

## Planejamento e frota

`GET /route-planning/eligible-fulfillments` exige ROUTE_PLANNER em organização
SUPPLIER. Aceita `warehouse_id` opcional e paginação. Cada item contém `id`,
`supplier_order_id`, `supplier_organization_id`, `buyer_organization_id`,
`warehouse_id`, `state`, `version`, `package_units`, `weight_grams`, `volume_cm3`,
`is_synthetic` e `lines`. Linha: `id`, `order_item_id`, `separated_quantity`,
`product_snapshot`.

Critérios: fornecedor do planejador; pedido pronto ou parcialmente despachado;
lote pronto; origem ativa e geocodificada do mesmo fornecedor; pelo menos uma
linha; todas com quantidade separada positiva, sem despacho e com dimensões
logísticas no snapshot. Lotes vinculados a rota não cancelada não aparecem.
Cancelar uma rota antes da coleta permite nova alocação. Propostas não publicadas
não reservam lotes. Os comandos revalidam também veículo, escala e janelas.

Lista/detalhe de rotas, propostas, manifesto, tracking atual/histórico,
aprovação, publicação e cancelamento respeitam o fornecedor do planejador.
Motoristas seguem limitados às rotas atribuídas; compradores só consultam
suas entregas e fornecedores seus pedidos. Os quatro caminhos de propostas
citados no PDF foram reutilizados sem duplicação.

`GET /drivers`: usuários ativos com vínculo DRIVER ativo ao fornecedor.
`POST /drivers`: cria a conta nesse fornecedor. `GET/POST /vehicles`: frota
exclusiva dele. Uma proposta não aceita depósito, motorista, veículo ou lote
de outro fornecedor. A criação legada de rota GPS também exige motorista
vinculado ao fornecedor do pedido.

## Categorias

- `GET /categories`: autenticado, paginado, ordenado por nome/ID.
- `POST /admin/categories`: administrador; 201 com `id`, `code`, `name`.

```json
{"code": "GRAOS", "name": "Grãos secos"}
```

Código único por execução, até 200 caracteres, iniciando com letra maiúscula e
usando letras maiúsculas, números e `_`. Duplicidade: CATEGORY_ALREADY_EXISTS
(409). Produto novo/editado deve usar categoria existente: CATEGORY_NOT_FOUND
(422). `product.category` continua string, incluindo o código anterior
ALIMENTO_SECO_INDUSTRIALIZADO, preservando os clientes existentes.

## Produtos administrativos

`GET /admin/products`: administrador, paginado, incluindo DRAFT, PUBLISHED e
UNPUBLISHED; filtros opcionais `search` e `publication_state`. `GET /products`
segue listando só publicados. O detalhe existente permite ao administrador
consultar qualquer estado. Legados incompletos retornam atributos ausentes
como null e podem ser completados pelo PATCH.

`PATCH /admin/products/{id}` exige `expected_version` e pelo menos um campo:
`name`, `brand`, `category`, `gtin`, `net_content`, `net_content_unit`,
`packaging_type`, `packaging_quantity`, `gross_weight_grams`, `volume_cm3`,
`variant_attributes`. Mesmas validações do cadastro; somente gtin aceita null
explícito. Omissão preserva o campo. variant_attributes substitui o mapa inteiro.

```json
{"expected_version": 2, "name": "Arroz — nova descrição", "gtin": null}
```

IDs, publicação, versão, execução, imagens e dados de ofertas não são editáveis
nesse comando. A edição incrementa a versão e mantém o estado. Snapshots já
salvos nos checkouts/pedidos preservam os dados e dimensões anteriores.
Edição/retirada compartilham o lock/versionamento de imagens/publicação.

`POST /admin/products/{id}/unpublish` recebe `{"expected_version": 3}`.
Somente PUBLISHED passa a UNPUBLISHED; produto, ofertas, estoque, imagens e
pedidos permanecem persistidos. Catálogo/ofertas deixam de oferecê-lo; novos
checkouts retornam OFFER_CONDITIONS_INVALID (422). Reservas/pedidos anteriores
mantêm seu contrato. Republicação usa o publish existente com versão atual e
dados completos. Ofertas ativas voltam ao catálogo; as pausadas seguem pausadas.

## Provisionamento e convites

Rotas definidas neste incremento, pois o PDF não fixa seus caminhos:

| Método/caminho | Permissão | Resultado |
|---|---|---|
| POST /admin/users | Administrador | Conta com senha inicial e Membership, 201 |
| POST /admin/invitations | Administrador | Convite e segredo retornado uma vez, 201 |
| DELETE /admin/invitations/{id} | Administrador | Revogação, 204 |
| POST /auth/invitations/accept | Segredo do convite | Conta/Membership, 201 |

Provisionamento direto:

```json
{
  "email": "planejador@empresa.invalid",
  "name": "Planejador sintético",
  "password": "senha-inicial-sintetica-com-12-caracteres",
  "organization_id": "UUID do fornecedor existente",
  "role": "ROUTE_PLANNER"
}
```

Papéis: ROUTE_PLANNER em SUPPLIER; ROTALOG_ADMIN em INTERNAL, na mesma execução.
Resposta UserResponse, sem senha/hash. Email normalizado e único; duplicidade:
EMAIL_ALREADY_REGISTERED (409).

Convite recebe `email`, `organization_id`, `role` e `expires_in_hours` opcional
(padrão 48, entre 1 e 168). Retorna `id`, `email`, `role`, `organization_id`,
`expires_at`, `invitation_token`, com Cache-Control: no-store. Só o hash do token
é persistido. Reemissão para mesmo email/organização revoga convites anteriores
não aceitos; não altera contas existentes.

Aceite: `token`, `name`, `password`; email/papel/organização vêm do convite.
Expirado, revogado ou consumido: INVITATION_INVALID (410). Usuário, vínculo e
consumo são atômicos, inclusive com aceites concorrentes. Depois, o cliente
faz login pelo contrato existente. Revogar convite aceito retorna 409.

O backend entrega o segredo ao administrador e não envia email. A distribuição
fica com a operação. Somente o modo demo exige emails sintéticos (.invalid);
produção aceita emails reais. Senhas de 12 a
1024 caracteres. Após provisionamento direto, usam-se os comandos existentes
de troca de senha e revogação de sessões.

## Autenticação fora da demonstração

Identidade local com senha, JWT, refresh rotativo, logout, troca de senha,
sessões persistentes e Membership funciona em staging/production, sem habilitar
o seed de demonstração. Administradores e planejadores são provisionados ou
convidados; o primeiro administrador é criado por comando local no servidor.
O uso atual continua destinado a testes de frontend/mobile, com pagamento
simulado. Contas reais podem compartilhar os dados de teste existentes sem
alterar pedidos, senhas ou classificá-los como transações reais. Veja
[a configuração de produção e os contratos](autenticacao-producao.md).

## Migração e integração

Aplicar `alembic upgrade head` (20261031_0011) e reexecutar o seed somente na
demonstração. A migração cria categorias preservando códigos históricos e a
categoria padrão, amplia o estado do produto sem apagar dados, recupera o
fornecedor das propostas pela origem e cria convites sem persistir segredos.

Veículos históricos só são associados se suas rotas comprovarem um único
fornecedor. Sem associação inequívoca, ficam com fornecedor null, ocultos da
frota, aguardando reconciliação operacional. Não se concede acesso global nem
se inventa propriedade. O downgrade bloqueia a perda de convites, categorias
novas ou estados retirados; nesses casos use migração compensatória.

O seed vincula somente suas contas conhecidas de planejador/motorista ao
fornecedor sintético. Para outros fornecedores, use admin/users ou convite
e depois drivers. Os smokes criam essas contas explícitas para cada cenário.
Para acessá-las no app após `--prepare-mobile`, configure previamente
`ROTALOG_SMOKE_ACCOUNT_PASSWORD`; o relatório fornece os emails das quatro
contas em `synthetic_accounts`. A senha administrativa não é copiada para elas.
Motoristas históricos sem Membership de fornecedor também precisam de
reconciliação de vínculo antes de serem usados em novas propostas; continuam
podendo executar suas rotas já atribuídas.

Testes cobrem permissões, isolamento entre fornecedores, paginação, lotes
inelegíveis/alocados/cancelados, edição/retirada com histórico, complemento de
legado, convites concorrentes/expirados/revogados e preservação nas migrações.
A regressão estrutural verifica as 82 operações do contrato anterior.

Validação local em PostgreSQL 17 com PostGIS, em bancos temporários separados
dos dados do projeto:

- `ruff check src tests migrations scripts`: aprovado.
- `pytest -q`: **134 testes aprovados**, sem skips; inclui `alembic check` nos
  cenários de migração e preservação de dados.
- `smoke_demo.py --with-delivery`: **68 verificações HTTP**, pedido DELIVERED.
- `smoke_demo.py --prepare-mobile`: **54 verificações HTTP**; login das contas
  geradas de planejador/motorista e acesso exclusivo à rota também conferidos.
- `git diff --check`: aprovado.
- Autenticação em production: startup sem seed, bootstrap e worker aprovados,
  com mais **30 verificações HTTP** de contas reais e dados de teste preservados.

A suíte emite um aviso de depreciação do transporte httpx no TestClient do
Starlette; ele não impede os testes.
