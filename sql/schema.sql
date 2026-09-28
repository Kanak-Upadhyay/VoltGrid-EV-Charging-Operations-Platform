-- VoltGrid relational model for Azure Database for MySQL 8.
-- The API also creates these tables on startup from the SQLAlchemy models.
-- Apply this file yourself only on an empty database.

CREATE TABLE networks (
  id INT AUTO_INCREMENT PRIMARY KEY,
  name VARCHAR(120) NOT NULL UNIQUE,
  created_at DATETIME NOT NULL
);

CREATE TABLE users (
  id INT AUTO_INCREMENT PRIMARY KEY,
  email VARCHAR(255) NOT NULL UNIQUE,
  password_hash VARCHAR(255) NOT NULL,
  full_name VARCHAR(120) NOT NULL,
  role VARCHAR(20) NOT NULL,
  network_id INT NULL,
  created_at DATETIME NOT NULL,
  CONSTRAINT ck_user_role CHECK (role IN ('admin', 'operator', 'driver')),
  CONSTRAINT fk_users_network FOREIGN KEY (network_id) REFERENCES networks (id)
);

CREATE TABLE sites (
  id INT AUTO_INCREMENT PRIMARY KEY,
  network_id INT NOT NULL,
  name VARCHAR(160) NOT NULL,
  city VARCHAR(80) NOT NULL,
  address VARCHAR(255) NOT NULL,
  latitude DECIMAL(9, 6) NOT NULL,
  longitude DECIMAL(9, 6) NOT NULL,
  created_at DATETIME NOT NULL,
  CONSTRAINT fk_sites_network FOREIGN KEY (network_id) REFERENCES networks (id),
  INDEX ix_sites_city (city),
  INDEX ix_sites_lat_lng (latitude, longitude)
);

CREATE TABLE chargers (
  id INT AUTO_INCREMENT PRIMARY KEY,
  site_id INT NOT NULL,
  serial_number VARCHAR(64) NOT NULL UNIQUE,
  vendor VARCHAR(80) NOT NULL,
  max_kw DECIMAL(8, 2) NOT NULL,
  status VARCHAR(20) NOT NULL,
  device_key_hash VARCHAR(64) NOT NULL,
  last_heartbeat_at DATETIME NULL,
  created_at DATETIME NOT NULL,
  CONSTRAINT ck_charger_status CHECK (status IN ('online', 'offline', 'maintenance')),
  CONSTRAINT fk_chargers_site FOREIGN KEY (site_id) REFERENCES sites (id)
);

CREATE TABLE connectors (
  id INT AUTO_INCREMENT PRIMARY KEY,
  charger_id INT NOT NULL,
  connector_type VARCHAR(20) NOT NULL,
  power_kw DECIMAL(8, 2) NOT NULL,
  status VARCHAR(20) NOT NULL,
  meter_kwh DECIMAL(14, 3) NOT NULL,
  CONSTRAINT ck_connector_status CHECK (status IN ('available', 'occupied', 'faulted')),
  CONSTRAINT uq_connector_type UNIQUE (charger_id, connector_type),
  CONSTRAINT fk_connectors_charger FOREIGN KEY (charger_id) REFERENCES chargers (id),
  INDEX ix_connectors_status (status)
);

CREATE TABLE tariffs (
  id INT AUTO_INCREMENT PRIMARY KEY,
  site_id INT NOT NULL,
  name VARCHAR(120) NOT NULL,
  energy_rate DECIMAL(10, 4) NOT NULL,
  time_rate DECIMAL(10, 4) NOT NULL,
  idle_rate DECIMAL(10, 4) NOT NULL,
  grace_minutes INT NOT NULL,
  gst_percent DECIMAL(5, 2) NOT NULL,
  windows JSON NOT NULL,
  active TINYINT(1) NOT NULL,
  created_at DATETIME NOT NULL,
  CONSTRAINT fk_tariffs_site FOREIGN KEY (site_id) REFERENCES sites (id)
);

CREATE TABLE vehicles (
  id INT AUTO_INCREMENT PRIMARY KEY,
  user_id INT NOT NULL,
  registration VARCHAR(20) NOT NULL,
  battery_kwh DECIMAL(8, 2) NOT NULL,
  created_at DATETIME NOT NULL,
  CONSTRAINT uq_vehicle_reg UNIQUE (user_id, registration),
  CONSTRAINT fk_vehicles_user FOREIGN KEY (user_id) REFERENCES users (id)
);

CREATE TABLE charging_sessions (
  id INT AUTO_INCREMENT PRIMARY KEY,
  user_id INT NOT NULL,
  connector_id INT NOT NULL,
  site_id INT NOT NULL,
  vehicle_id INT NULL,
  status VARCHAR(20) NOT NULL,
  started_at DATETIME NOT NULL,
  ended_at DATETIME NULL,
  unplugged_at DATETIME NULL,
  energy_kwh DECIMAL(12, 3) NOT NULL,
  meter_start DECIMAL(14, 3) NOT NULL,
  meter_end DECIMAL(14, 3) NULL,
  tariff_snapshot JSON NOT NULL,
  idempotency_key VARCHAR(80) NULL,
  estimated TINYINT(1) NOT NULL,
  created_at DATETIME NOT NULL,
  CONSTRAINT ck_session_status CHECK (status IN ('active', 'completed', 'cancelled')),
  CONSTRAINT uq_session_idempotency UNIQUE (user_id, idempotency_key),
  CONSTRAINT fk_sessions_user FOREIGN KEY (user_id) REFERENCES users (id),
  CONSTRAINT fk_sessions_connector FOREIGN KEY (connector_id) REFERENCES connectors (id),
  CONSTRAINT fk_sessions_site FOREIGN KEY (site_id) REFERENCES sites (id),
  CONSTRAINT fk_sessions_vehicle FOREIGN KEY (vehicle_id) REFERENCES vehicles (id),
  INDEX ix_sessions_started (started_at)
);

CREATE TABLE invoices (
  id INT AUTO_INCREMENT PRIMARY KEY,
  session_id INT NOT NULL UNIQUE,
  energy_amount DECIMAL(12, 2) NOT NULL,
  time_amount DECIMAL(12, 2) NOT NULL,
  idle_amount DECIMAL(12, 2) NOT NULL,
  subtotal DECIMAL(12, 2) NOT NULL,
  gst_amount DECIMAL(12, 2) NOT NULL,
  total DECIMAL(12, 2) NOT NULL,
  currency VARCHAR(3) NOT NULL,
  status VARCHAR(20) NOT NULL,
  issued_at DATETIME NOT NULL,
  CONSTRAINT ck_invoice_status CHECK (status IN ('issued', 'paid', 'void')),
  CONSTRAINT fk_invoices_session FOREIGN KEY (session_id) REFERENCES charging_sessions (id),
  INDEX ix_invoices_status (status)
);

CREATE TABLE payments (
  id INT AUTO_INCREMENT PRIMARY KEY,
  invoice_id INT NOT NULL,
  amount DECIMAL(12, 2) NOT NULL,
  method VARCHAR(20) NOT NULL,
  reference VARCHAR(80) NOT NULL,
  status VARCHAR(20) NOT NULL,
  paid_at DATETIME NOT NULL,
  CONSTRAINT fk_payments_invoice FOREIGN KEY (invoice_id) REFERENCES invoices (id)
);

CREATE TABLE audit_logs (
  id INT AUTO_INCREMENT PRIMARY KEY,
  actor_id INT NULL,
  action VARCHAR(80) NOT NULL,
  entity VARCHAR(40) NOT NULL,
  entity_id VARCHAR(40) NOT NULL,
  detail JSON NOT NULL,
  created_at DATETIME NOT NULL,
  CONSTRAINT fk_audit_user FOREIGN KEY (actor_id) REFERENCES users (id),
  INDEX ix_audit_created (created_at)
);
