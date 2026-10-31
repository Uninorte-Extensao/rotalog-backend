# Autenticação operacional e testes online

A aplicação usa identidade local: senha Argon2id, access JWT de 10 minutos,
refresh rotativo e sessões persistentes de 30 dias. Cadastro, login, convites,
permissões, troca de senha e revogação funcionam com `ROTALOG_DEMO_ENABLED=false`,
inclusive em `production`. Não depende de provedor externo de identidade.

O uso atual continua sendo teste com frontend/mobile. Pedidos e pagamentos
permanecem simulados (`provider=SIMULATED`); esta mudança não integra um PSP nem
movimenta dinheiro real. Não há conversão de pedidos antigos em transações reais.

## Configurar o servidor existente

Mantenha o banco, os volumes, a chave JWT e as senhas já existentes. Aplique a
migração `20261031_0011` com `alembic upgrade head`. Ela adiciona classificações
sem apagar/recriar contas, organizações, vínculos, pedidos ou sessões.

Configuração do processo de API e worker:

```dotenv
ROTALOG_ENVIRONMENT=production
ROTALOG_DEMO_ENABLED=false
ROTALOG_DEBUG=false
ROTALOG_COOKIE_SECURE=true
ROTALOG_COOKIE_SAMESITE=lax
ROTALOG_CORS_ORIGINS=["https://portal.example.com"]
ROTALOG_DATA_CONTEXT_ID=02699c03-3036-43dc-83f0-32521d1b029b
```

Substitua a origem pelo endereço HTTPS real do frontend. `ROTALOG_JWT_SECRET`
continua obrigatória, com pelo menos 32 bytes; use o valor já configurado, sem
rotacioná-lo durante esta atualização. `ROTALOG_DATABASE_URL` continua apontando
para o mesmo banco. Segredos não devem entrar no Git.

`ROTALOG_DATA_CONTEXT_ID` seleciona o conjunto de dados usado por novos cadastros
públicos e pelo bootstrap. O UUID acima é o dataset conhecido do seed deste
repositório. Para outra execução, use seu `demo_run_id`, disponível em `/me`
para uma conta que já a acessa. Isso permite contas reais vinculadas ao mesmo
catálogo/pedidos de teste, preservando o isolamento por fornecedor.

Sem esse parâmetro, o bootstrap cria um contexto novo `rotalog-live`; novos
cadastros usam esse contexto, com catálogo inicialmente vazio. A escolha é
explícita: o código não move nem reclassifica os dados antigos automaticamente.

A instância configurada com um contexto recusa login, access e refresh de contas
de outro contexto. O modo operacional permite continuar usando as contas antigas
de teste desse contexto, além das novas contas com emails reais. O modo de demo
continua aceitando somente identidades sintéticas e exigindo domínio `.invalid`.

## Primeiro administrador

O autocadastro HTTP não concede `ROTALOG_ADMIN` nem permite escolher uma
organização existente. Inicialize a primeira conta administrativa operacional
por comando no servidor, com as configurações acima carregadas:

```bash
alembic upgrade head
python -m rotalog.identity.bootstrap \
  --email admin@empresa.com \
  --name "Administrador" \
  --organization-name "Operação RotaLog"
```

O comando pede e confirma a senha, sem exibi-la. A senha deve ter de 12 a 1024
caracteres. Para automação, `--password-stdin` lê uma linha de stdin; não há
argumento de senha e o comando não a imprime.

A transação cria a organização INTERNAL, a conta ROTALOG_ADMIN e seu Membership.
Só é permitida uma inicialização por contexto, inclusive com chamadas concorrentes.
A repetição retorna `ADMIN_ALREADY_INITIALIZED`, sem trocar credenciais. O
administrador sintético do seed é preservado e não impede o bootstrap da primeira
conta operacional. Se o email escolhido já existe, use seu login existente ou
escolha outro email para o primeiro administrador; não se sobrescreve a conta.

Com Docker Compose, use a mesma configuração no arquivo de ambiente do servidor:

```bash
docker compose build api worker
docker compose up -d db
docker compose run --rm api alembic upgrade head
docker compose run --rm api python -m rotalog.identity.bootstrap \
  --email admin@empresa.com --name "Administrador" \
  --organization-name "Operação RotaLog"
docker compose up -d api worker
```

Os nomes históricos de banco/volumes no Compose foram mantidos para reaproveitar
os dados atuais. A inicialização agora executa `rotalog.operations.startup`:
aplica migrações e só executa o seed se `ROTALOG_DEMO_ENABLED=true`. Produção não
exige `ROTALOG_DEMO_PASSWORD`. O worker também funciona sem habilitar a demo.

## Contratos para o frontend e mobile

Prefixo `/api/v1`. Os contratos existentes de autenticação foram preservados.

| Operação | Contrato |
|---|---|
| Autocadastro comprador/fornecedor | POST /auth/register, JSON com email, name, password, organization_name e role |
| Login | POST /auth/login, formulário username=email e password |
| Refresh | POST /auth/refresh, token no corpo OU cookie |
| Perfil/vínculos/capacidades | GET /me |
| Sessões/dispositivos | GET /me/sessions; DELETE /me/sessions/{id} |
| Logout atual/todos | POST /auth/logout; POST /auth/logout-all |
| Troca de senha | POST /auth/password/change; revoga todas as sessões |
| Cadastro administrativo | POST /admin/users, Bearer de administrador |
| Convite | POST /admin/invitations; DELETE /admin/invitations/{id} |
| Aceite de convite | POST /auth/invitations/accept, segredo do convite |
| Cadastro de motorista | POST /drivers, Bearer de planejador |

O autocadastro aceita BUYER_ADMIN ou SUPPLIER_OPERATOR e sempre cria organização
própria. Campos desconhecidos são rejeitados. Para cadastrar um planejador, o
administrador escolhe uma organização SUPPLIER existente, por exemplo da lista
`GET /suppliers`, e envia:

```json
{
  "email": "planejador@empresa.com",
  "name": "Planejador",
  "password": "senha-inicial-com-pelo-menos-12-caracteres",
  "organization_id": "UUID do fornecedor",
  "role": "ROUTE_PLANNER"
}
```

Para outro administrador, use role ROTALOG_ADMIN e uma organização INTERNAL
existente, como a organização do administrador em `/me`. O planejador cadastra
motoristas em `/drivers`; a conta é vinculada ao mesmo fornecedor.

Convite recebe email, role, organization_id e expires_in_hours opcional (padrão
48, máximo 168). Retorna o segredo uma única vez, com `Cache-Control: no-store`;
apenas seu hash é persistido. Aceite recebe token, name e password e retorna
UserResponse; depois o cliente faz login. O backend não envia email: a operação
pode distribuir o segredo retornado. Reemissão revoga convites pendentes anteriores.

`X-Organization-Id` seleciona um vínculo ativo autorizado. Cada transação revalida
usuário, sessão e Membership. Não é possível obter papel administrativo por
modificar o body do autocadastro ou selecionar UUID de outra organização.

## Navegador e origens diferentes

Use `/auth/login?client_type=browser` com Origin da allowlist. O refresh fica
somente em cookie HttpOnly/Secure; o access token fica em memória. Refresh e
logout com cookie exigem Origin autorizado e X-CSRF-Token recebido no login;
envie `credentials: include`. CORS inclui PATCH, necessário para editar produtos.

Se portal e API estiverem em sites distintos, configure
`ROTALOG_COOKIE_SAMESITE=none` com HTTPS e `ROTALOG_COOKIE_SECURE=true`.
Origin exato e CSRF continuam obrigatórios. Para o mesmo site, mantenha lax.
Políticas de bloqueio de cookies de terceiros do navegador ainda podem exigir
servir API e portal sob o mesmo site. Aplicativos nativos usam o modo native,
com access/refresh no JSON e sem depender de cookies ou CORS.

## Classificação e compatibilidade dos dados

No perfil, `is_synthetic` descreve a conta; novas contas fora da demo têm false.
`data_is_synthetic` descreve o conjunto de catálogo/pedidos selecionado. Uma conta
real usando o dataset de teste retorna `is_synthetic=false` e
`data_is_synthetic=true`. Respostas comerciais mantêm os marcadores do dataset.
O campo histórico `demo_run_id` permanece como identificador do contexto para
não quebrar os clientes e as FKs existentes. Não é necessário recriar usuários.

Respostas financeiras continuam indicando SIMULATED e is_synthetic=true,
inclusive fora da demo. O scanner privado continua sendo validação de imagem,
sem alegação de antivírus; seu scan_mode informa o tipo de dataset.
O downgrade é bloqueado se apagaria classificações de contas/contextos novos.

## Verificação e limites atuais

Testes automatizados cobrem configuração de produção, cadastro com email real,
provisionamento e convites, isolamento de organizações/contextos, login das contas
antigas, concorrência do bootstrap e aceite, refresh/reutilização, cookies/CSRF,
revogação e troca de senha. Também percorrem compra simulada, coleta, prova e
entrega em configuração production, preservando o dataset de teste.

Suíte completa: **134 testes aprovados**, sem skips. Validação de execução em
banco PostgreSQL/PostGIS temporário: startup sem seed,
bootstrap via stdin e worker com `--once` aprovados; o número de contas antigas
foi preservado. O smoke HTTP de produção passou por 30 verificações, incluindo
cadastro com email real, planejador/motorista, convites, catálogo e sessões.

Esta implementação não adiciona envio de email, recuperação de senha por email,
MFA ou pagamentos reais. São funcionalidades distintas do cadastro/login/sessão
solicitados. Nenhuma configuração ou conta do servidor remoto foi alterada por
esta implementação; os comandos acima são o procedimento para ativá-la lá.
