"""Marcadores de resposta do contexto autenticado, isolados por requisição."""

from contextvars import ContextVar

synthetic_data: ContextVar[bool] = ContextVar("rotalog_synthetic_data", default=True)


def is_synthetic_data() -> bool:
    return synthetic_data.get()


def data_label() -> str:
    return (
        "SIMULAÇÃO — dados sintéticos" if is_synthetic_data() else "Dados operacionais"
    )
