-- Migración Versión 2 (idempotente, preserva todos los datos de Fase 1)

-- 1. Usuarios y Clientes: estado activo, cambio obligatorio de contraseña y número de cliente
ALTER TABLE usuarios ADD COLUMN IF NOT EXISTS activo BOOLEAN NOT NULL DEFAULT TRUE;
ALTER TABLE usuarios ADD COLUMN IF NOT EXISTS forzar_cambio_password BOOLEAN NOT NULL DEFAULT FALSE;
ALTER TABLE usuarios ADD COLUMN IF NOT EXISTS creado TIMESTAMPTZ DEFAULT now();

CREATE SEQUENCE IF NOT EXISTS cliente_seq;
ALTER TABLE clientes ADD COLUMN IF NOT EXISTS numero_cliente TEXT UNIQUE;
UPDATE clientes
SET numero_cliente = 'CLI-' || lpad(nextval('cliente_seq')::text, 5, '0')
WHERE numero_cliente IS NULL;

-- 2. Órdenes: contraseña cifrada, campos de entrega y clasificación Bajos/Medios/Altos + iniciales de recepción
ALTER TABLE ordenes ADD COLUMN IF NOT EXISTS password_equipo_cifrado TEXT;
ALTER TABLE ordenes ADD COLUMN IF NOT EXISTS entregado_a TEXT;
ALTER TABLE ordenes ADD COLUMN IF NOT EXISTS fecha_entrega TIMESTAMPTZ;
ALTER TABLE ordenes ADD COLUMN IF NOT EXISTS firma_url TEXT;
ALTER TABLE ordenes ADD COLUMN IF NOT EXISTS saldo_confirmado BOOLEAN NOT NULL DEFAULT FALSE;
ALTER TABLE ordenes ADD COLUMN IF NOT EXISTS iniciales_tecnicos TEXT;
ALTER TABLE ordenes ADD COLUMN IF NOT EXISTS seccion_equipo TEXT;
ALTER TABLE ordenes ADD COLUMN IF NOT EXISTS seccion_bajos TEXT;
ALTER TABLE ordenes ADD COLUMN IF NOT EXISTS seccion_medios TEXT;
ALTER TABLE ordenes ADD COLUMN IF NOT EXISTS seccion_altos TEXT;

-- Secuencias independientes de folios por sección:
-- Bajos (Software): inicia en 20000
-- Medios (Impresoras, discos, memorias, cargadores): inicia en 2000
-- Altos (Hardware): inicia en 3000
CREATE SEQUENCE IF NOT EXISTS folio_bajos_seq START WITH 20000;
CREATE SEQUENCE IF NOT EXISTS folio_medios_seq START WITH 2000;
CREATE SEQUENCE IF NOT EXISTS folio_altos_seq START WITH 3000;

-- 3. Bitácora de edición de órdenes
CREATE TABLE IF NOT EXISTS bitacora_ordenes (
    id SERIAL PRIMARY KEY,
    orden_id INT NOT NULL REFERENCES ordenes(id) ON DELETE CASCADE,
    usuario_id INT REFERENCES usuarios(id),
    usuario_nombre TEXT,
    campo TEXT NOT NULL,
    valor_anterior TEXT,
    valor_nuevo TEXT,
    fecha TIMESTAMPTZ DEFAULT now()
);
CREATE INDEX IF NOT EXISTS bitacora_ordenes_orden_id_idx ON bitacora_ordenes(orden_id);

-- 4. Cotizaciones e ítems: tipo (refacción/mano de obra), subtotal, IVA configurable, versión e IP de respuesta
ALTER TABLE cotizaciones ADD COLUMN IF NOT EXISTS iva_porcentaje NUMERIC(5,2) NOT NULL DEFAULT 16.00;
ALTER TABLE cotizaciones ADD COLUMN IF NOT EXISTS subtotal NUMERIC(10,2) NOT NULL DEFAULT 0.00;
ALTER TABLE cotizaciones ADD COLUMN IF NOT EXISTS iva_monto NUMERIC(10,2) NOT NULL DEFAULT 0.00;
ALTER TABLE cotizaciones ADD COLUMN IF NOT EXISTS total NUMERIC(10,2) NOT NULL DEFAULT 0.00;
ALTER TABLE cotizaciones ADD COLUMN IF NOT EXISTS version INT NOT NULL DEFAULT 1;
ALTER TABLE cotizaciones ADD COLUMN IF NOT EXISTS ip_respuesta TEXT;
ALTER TABLE cotizaciones ADD COLUMN IF NOT EXISTS actualizada TIMESTAMPTZ DEFAULT now();

ALTER TABLE cotizacion_items ADD COLUMN IF NOT EXISTS tipo TEXT NOT NULL DEFAULT 'refaccion'
    CHECK (tipo IN ('refaccion','mano_obra'));

-- 5. Mensajes y Fotos
ALTER TABLE mensajes ADD COLUMN IF NOT EXISTS archivo_url TEXT;
ALTER TABLE mensajes ADD COLUMN IF NOT EXISTS leido_por_tecnico BOOLEAN NOT NULL DEFAULT FALSE;
ALTER TABLE mensajes ADD COLUMN IF NOT EXISTS leido_por_cliente BOOLEAN NOT NULL DEFAULT FALSE;

CREATE TABLE IF NOT EXISTS fotos_orden (
    id SERIAL PRIMARY KEY,
    orden_id INT NOT NULL REFERENCES ordenes(id) ON DELETE CASCADE,
    etapa TEXT NOT NULL CHECK (etapa IN ('recepcion','proceso','entrega')),
    archivo_url TEXT NOT NULL,
    descripcion TEXT,
    visible_cliente BOOLEAN NOT NULL DEFAULT TRUE,
    subido_por INT REFERENCES usuarios(id),
    fecha TIMESTAMPTZ DEFAULT now()
);
ALTER TABLE fotos_orden ADD COLUMN IF NOT EXISTS visible_cliente BOOLEAN NOT NULL DEFAULT TRUE;
CREATE INDEX IF NOT EXISTS fotos_orden_orden_id_idx ON fotos_orden(orden_id);

-- 6. Pagos y anticipos (preparado para Bloque 2/3)
CREATE TABLE IF NOT EXISTS pagos_orden (
    id SERIAL PRIMARY KEY,
    orden_id INT NOT NULL REFERENCES ordenes(id) ON DELETE CASCADE,
    monto NUMERIC(10,2) NOT NULL CHECK (monto > 0),
    metodo TEXT NOT NULL CHECK (metodo IN ('efectivo','transferencia','tarjeta')),
    concepto TEXT DEFAULT 'Anticipo / Abono',
    usuario_id INT REFERENCES usuarios(id),
    fecha TIMESTAMPTZ DEFAULT now()
);
CREATE INDEX IF NOT EXISTS pagos_orden_orden_id_idx ON pagos_orden(orden_id);

-- 7. Respuestas rápidas y configuración del taller
CREATE TABLE IF NOT EXISTS respuestas_rapidas (
    id SERIAL PRIMARY KEY,
    titulo TEXT NOT NULL,
    texto TEXT NOT NULL,
    activo BOOLEAN NOT NULL DEFAULT TRUE,
    orden INT NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS configuracion_taller (
    clave TEXT PRIMARY KEY,
    valor TEXT NOT NULL,
    actualizado TIMESTAMPTZ DEFAULT now()
);

INSERT INTO configuracion_taller (clave, valor) VALUES
    ('iva_porcentaje', '16'),
    ('nombre_taller', 'Taller de Cómputo'),
    ('aviso_privacidad', 'Tus datos personales (nombre, teléfono y correo) se recaban y utilizan exclusivamente para la identificación, diagnóstico, cotización, notificación de avance y entrega de tu equipo de cómputo, conforme a la Ley Federal de Protección de Datos Personales en Posesión de los Particulares (México).')
ON CONFLICT (clave) DO NOTHING;
