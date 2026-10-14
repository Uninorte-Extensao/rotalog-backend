# Migrações

As migrações usam a URL definida em `ROTALOG_DATABASE_URL` ou aquela injetada
explicitamente pelo Alembic. Execute-as com:

```bash
python -m alembic upgrade head
```

Nunca inclua credenciais reais no `alembic.ini`.
