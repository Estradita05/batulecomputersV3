"""Bloque 1: Fotos y evidencia en ordenes y chat

Revision ID: 0001_bloque1_fotos
Revises: 
Create Date: 2026-10-31
"""
from alembic import op

revision = "0001_bloque1_fotos"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
    CREATE TABLE IF NOT EXISTS fotos_orden (
        id SERIAL PRIMARY KEY,
        orden_id INT NOT NULL REFERENCES ordenes(id) ON DELETE CASCADE,
        etapa TEXT NOT NULL CHECK (etapa IN ('recepcion','proceso','entrega')),
        archivo_url TEXT NOT NULL,
        descripcion TEXT,
        subido_por INT REFERENCES usuarios(id),
        fecha TIMESTAMPTZ DEFAULT now()
    );
    CREATE INDEX IF NOT EXISTS fotos_orden_orden_id_idx ON fotos_orden(orden_id);
    ALTER TABLE mensajes ADD COLUMN IF NOT EXISTS archivo_url TEXT;
    """)


def downgrade() -> None:
    op.execute("""
    ALTER TABLE mensajes DROP COLUMN IF EXISTS archivo_url;
    DROP TABLE IF EXISTS fotos_orden;
    """)
