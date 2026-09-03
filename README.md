# RotaLog Backend

API FastAPI do RotaLog organizada como monólito modular, conforme as seções 9.3 e 9.4 do documento técnico.

## Preparação do ambiente

O projeto requer Python 3.13; o patch adotado está em `.python-version`.

```bash
python3.13 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

## Executar a API

```bash
source .venv/bin/activate
fastapi dev src/rotalog/api/main.py
```

A documentação OpenAPI fica disponível em `http://127.0.0.1:8000/docs` e a verificação de saúde em `http://127.0.0.1:8000/health`.

## Estrutura dos módulos

Cada módulo de negócio em `src/rotalog` possui as camadas:

- `api`: routers e schemas HTTP;
- `application`: comandos, consultas, DTOs e orquestração;
- `domain`: estados, regras e exceções de negócio;
- `infrastructure`: modelos SQLAlchemy, repositórios e adaptadores.
