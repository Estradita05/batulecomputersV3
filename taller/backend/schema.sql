CREATE TABLE IF NOT EXISTS usuarios (
  id SERIAL PRIMARY KEY,
  nombre TEXT NOT NULL,
  email TEXT UNIQUE NOT NULL,
  password_hash TEXT NOT NULL,
  rol TEXT NOT NULL DEFAULT 'tecnico' CHECK (rol IN ('admin','tecnico','recepcion')),
  activo BOOLEAN NOT NULL DEFAULT TRUE,
  forzar_cambio_password BOOLEAN NOT NULL DEFAULT FALSE,
  creado TIMESTAMPTZ DEFAULT now()
);

CREATE SEQUENCE IF NOT EXISTS cliente_seq;

CREATE TABLE IF NOT EXISTS clientes (
  id SERIAL PRIMARY KEY,
  numero_cliente TEXT UNIQUE,
  nombre TEXT NOT NULL,
  telefono TEXT UNIQUE NOT NULL,   -- solo dígitos, últimos 10
  correo TEXT,
  creado TIMESTAMPTZ DEFAULT now()
);

CREATE TABLE IF NOT EXISTS equipos (
  id SERIAL PRIMARY KEY,
  cliente_id INT NOT NULL REFERENCES clientes(id),
  tipo TEXT NOT NULL,              -- laptop, cpu, all in one, impresora, tablet, dd externo, cargador...
  marca TEXT,
  modelo TEXT,
  num_serie TEXT
);
CREATE INDEX IF NOT EXISTS equipos_num_serie_idx ON equipos(num_serie);

CREATE SEQUENCE IF NOT EXISTS folio_seq;

CREATE TABLE IF NOT EXISTS ordenes (
  id SERIAL PRIMARY KEY,
  folio TEXT UNIQUE NOT NULL,
  equipo_id INT NOT NULL REFERENCES equipos(id),
  falla TEXT NOT NULL,
  accesorios TEXT,
  password_equipo TEXT,
  password_equipo_cifrado TEXT,
  estatus TEXT NOT NULL DEFAULT 'Recibido',
  tecnico_id INT REFERENCES usuarios(id),
  fecha_ingreso TIMESTAMPTZ DEFAULT now(),
  fecha_estimada DATE,
  entregado_a TEXT,
  fecha_entrega TIMESTAMPTZ,
  firma_url TEXT,
  saldo_confirmado BOOLEAN NOT NULL DEFAULT FALSE
);

CREATE TABLE IF NOT EXISTS historial_estatus (
  id SERIAL PRIMARY KEY,
  orden_id INT NOT NULL REFERENCES ordenes(id) ON DELETE CASCADE,
  estatus TEXT NOT NULL,
  nota TEXT,
  usuario_id INT REFERENCES usuarios(id),
  fecha TIMESTAMPTZ DEFAULT now()
);

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

CREATE TABLE IF NOT EXISTS cotizaciones (
  id SERIAL PRIMARY KEY,
  orden_id INT NOT NULL REFERENCES ordenes(id) ON DELETE CASCADE,
  estatus TEXT NOT NULL DEFAULT 'pendiente' CHECK (estatus IN ('pendiente','aprobada','rechazada')),
  iva_porcentaje NUMERIC(5,2) NOT NULL DEFAULT 16.00,
  subtotal NUMERIC(10,2) NOT NULL DEFAULT 0.00,
  iva_monto NUMERIC(10,2) NOT NULL DEFAULT 0.00,
  total NUMERIC(10,2) NOT NULL DEFAULT 0.00,
  version INT NOT NULL DEFAULT 1,
  ip_respuesta TEXT,
  respondida TIMESTAMPTZ,
  creada TIMESTAMPTZ DEFAULT now(),
  actualizada TIMESTAMPTZ DEFAULT now()
);

CREATE TABLE IF NOT EXISTS cotizacion_items (
  id SERIAL PRIMARY KEY,
  cotizacion_id INT NOT NULL REFERENCES cotizaciones(id) ON DELETE CASCADE,
  descripcion TEXT NOT NULL,
  tipo TEXT NOT NULL DEFAULT 'refaccion' CHECK (tipo IN ('refaccion','mano_obra')),
  cantidad INT NOT NULL DEFAULT 1,
  precio NUMERIC(10,2) NOT NULL
);

CREATE TABLE IF NOT EXISTS mensajes (
  id SERIAL PRIMARY KEY,
  orden_id INT NOT NULL REFERENCES ordenes(id) ON DELETE CASCADE,
  emisor TEXT NOT NULL CHECK (emisor IN ('tecnico','cliente')),
  texto TEXT NOT NULL,
  archivo_url TEXT,
  leido_por_tecnico BOOLEAN NOT NULL DEFAULT FALSE,
  leido_por_cliente BOOLEAN NOT NULL DEFAULT FALSE,
  fecha TIMESTAMPTZ DEFAULT now()
);

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
CREATE INDEX IF NOT EXISTS fotos_orden_orden_id_idx ON fotos_orden(orden_id);

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
