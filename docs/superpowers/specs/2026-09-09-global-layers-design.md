# Reorganização em camadas globais

## Objetivo

Reorganizar o pacote Python do RotaLog para usar camadas globais dentro de `src/rotalog`, preservando o monólito modular e os módulos de negócio existentes.

## Estrutura desejada

```text
src/rotalog/
├── api/
│   ├── main.py
│   └── schemas.py
├── application/<módulo>/
├── domain/<módulo>/
├── infrastructure/<módulo>/
└── shared/
```

Os módulos atuais (`admin`, `audit`, `catalog`, `checkout`, `delivery`, `disputes`, `files`, `identity`, `inventory`, `logistics`, `notifications`, `orders`, `organizations` e `payments`) serão distribuídos entre `application`, `domain` e `infrastructure`, mantendo seus nomes e responsabilidades.

## Regras arquiteturais

- `api` concentra a entrada HTTP da aplicação e schemas compartilhados.
- `application` concentra casos de uso e orquestração por módulo.
- `domain` concentra regras e modelos de negócio por módulo.
- `infrastructure` concentra persistência e integrações técnicas por módulo.
- `shared` permanece reservado a componentes transversais, como banco de dados.
- O pacote `src/rotalog` continua sendo a raiz importável; não haverá migração dos módulos para o root do repositório.
- A reorganização não introduzirá microserviços, Redis, Celery ou novas tecnologias fora da baseline da documentação técnica.

## Compatibilidade e escopo

- Atualizar todos os imports afetados pela movimentação.
- Preservar o ponto de entrada FastAPI e o comportamento existente da PR.
- Não alterar regras de negócio nem implementar funcionalidades novas.
- Não modificar o CI nesta etapa.
- Trabalhar exclusivamente na branch da PR, sem alterar `main`.

## Verificação

- Confirmar que todos os arquivos foram movidos para a camada correspondente.
- Procurar imports antigos e referências a caminhos removidos.
- Executar os testes disponíveis e uma verificação de import da aplicação.
- Confirmar que a árvore não contém um diretório `backend/` intermediário.
