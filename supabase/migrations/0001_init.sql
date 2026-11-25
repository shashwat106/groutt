-- Grout schema (generated from db/models.py). Do not edit by hand.

CREATE TABLE businesses (
	id SERIAL NOT NULL, 
	name VARCHAR(200) NOT NULL, 
	industry VARCHAR(50), 
	city VARCHAR(100), 
	created_at TIMESTAMP WITH TIME ZONE NOT NULL, 
	PRIMARY KEY (id)
);

CREATE TABLE actions (
	id SERIAL NOT NULL, 
	type VARCHAR(50) NOT NULL, 
	summary TEXT NOT NULL, 
	payload_json JSON NOT NULL, 
	status VARCHAR(20) NOT NULL, 
	approval_code VARCHAR(10) NOT NULL, 
	requested_by_role VARCHAR(20) NOT NULL, 
	approved_by_role VARCHAR(50), 
	created_at TIMESTAMP WITH TIME ZONE NOT NULL, 
	decided_at TIMESTAMP WITH TIME ZONE, 
	business_id INTEGER NOT NULL, 
	PRIMARY KEY (id), 
	FOREIGN KEY(business_id) REFERENCES businesses (id)
);

CREATE INDEX ix_actions_business_id ON actions (business_id);

CREATE TABLE audit_log (
	id SERIAL NOT NULL, 
	tool VARCHAR(100) NOT NULL, 
	args_json JSON NOT NULL, 
	role VARCHAR(20) NOT NULL, 
	result_summary TEXT NOT NULL, 
	ok BOOLEAN NOT NULL, 
	created_at TIMESTAMP WITH TIME ZONE NOT NULL, 
	business_id INTEGER NOT NULL, 
	PRIMARY KEY (id), 
	FOREIGN KEY(business_id) REFERENCES businesses (id)
);

CREATE INDEX ix_audit_log_business_id ON audit_log (business_id);

CREATE TABLE business_profile (
	id SERIAL NOT NULL, 
	key VARCHAR(100) NOT NULL, 
	value_json JSON NOT NULL, 
	source VARCHAR(200) NOT NULL, 
	confidence FLOAT NOT NULL, 
	updated_at TIMESTAMP WITH TIME ZONE NOT NULL, 
	business_id INTEGER NOT NULL, 
	PRIMARY KEY (id), 
	FOREIGN KEY(business_id) REFERENCES businesses (id)
);

CREATE INDEX ix_business_profile_business_id ON business_profile (business_id);

CREATE TABLE customers (
	id SERIAL NOT NULL, 
	name VARCHAR(200) NOT NULL, 
	phone VARCHAR(50), 
	email VARCHAR(200), 
	city VARCHAR(100), 
	gstin VARCHAR(20), 
	payment_terms_days INTEGER NOT NULL, 
	source VARCHAR(200) NOT NULL, 
	business_id INTEGER NOT NULL, 
	PRIMARY KEY (id), 
	FOREIGN KEY(business_id) REFERENCES businesses (id)
);

CREATE INDEX ix_customers_business_id ON customers (business_id);

CREATE TABLE labour (
	id SERIAL NOT NULL, 
	role VARCHAR(100) NOT NULL, 
	headcount INTEGER NOT NULL, 
	cost_per_hour FLOAT NOT NULL, 
	business_id INTEGER NOT NULL, 
	PRIMARY KEY (id), 
	FOREIGN KEY(business_id) REFERENCES businesses (id)
);

CREATE INDEX ix_labour_business_id ON labour (business_id);

CREATE TABLE machines (
	id SERIAL NOT NULL, 
	machine_type VARCHAR(50) NOT NULL, 
	count INTEGER NOT NULL, 
	hours_per_shift FLOAT NOT NULL, 
	shifts_per_day INTEGER NOT NULL, 
	efficiency_pct FLOAT NOT NULL, 
	business_id INTEGER NOT NULL, 
	PRIMARY KEY (id), 
	FOREIGN KEY(business_id) REFERENCES businesses (id)
);

CREATE INDEX ix_machines_business_id ON machines (business_id);

CREATE TABLE overheads (
	id SERIAL NOT NULL, 
	name VARCHAR(100) NOT NULL, 
	monthly_amount FLOAT NOT NULL, 
	business_id INTEGER NOT NULL, 
	PRIMARY KEY (id), 
	FOREIGN KEY(business_id) REFERENCES businesses (id)
);

CREATE INDEX ix_overheads_business_id ON overheads (business_id);

CREATE TABLE products (
	id SERIAL NOT NULL, 
	name VARCHAR(200) NOT NULL, 
	unit VARCHAR(20) NOT NULL, 
	selling_price FLOAT NOT NULL, 
	product_type VARCHAR(50), 
	source VARCHAR(200) NOT NULL, 
	business_id INTEGER NOT NULL, 
	PRIMARY KEY (id), 
	FOREIGN KEY(business_id) REFERENCES businesses (id)
);

CREATE INDEX ix_products_business_id ON products (business_id);

CREATE TABLE suppliers (
	id SERIAL NOT NULL, 
	name VARCHAR(200) NOT NULL, 
	phone VARCHAR(50), 
	email VARCHAR(200), 
	city VARCHAR(100), 
	gstin VARCHAR(20), 
	payment_terms_days INTEGER NOT NULL, 
	lead_time_days INTEGER NOT NULL, 
	source VARCHAR(200) NOT NULL, 
	business_id INTEGER NOT NULL, 
	PRIMARY KEY (id), 
	FOREIGN KEY(business_id) REFERENCES businesses (id)
);

CREATE INDEX ix_suppliers_business_id ON suppliers (business_id);

CREATE TABLE materials (
	id SERIAL NOT NULL, 
	name VARCHAR(200) NOT NULL, 
	unit VARCHAR(20) NOT NULL, 
	on_hand FLOAT NOT NULL, 
	reorder_level FLOAT NOT NULL, 
	unit_cost FLOAT NOT NULL, 
	preferred_supplier_id INTEGER, 
	source VARCHAR(200) NOT NULL, 
	business_id INTEGER NOT NULL, 
	PRIMARY KEY (id), 
	FOREIGN KEY(preferred_supplier_id) REFERENCES suppliers (id), 
	FOREIGN KEY(business_id) REFERENCES businesses (id)
);

CREATE INDEX ix_materials_business_id ON materials (business_id);

CREATE TABLE orders (
	id SERIAL NOT NULL, 
	customer_id INTEGER NOT NULL, 
	product_id INTEGER NOT NULL, 
	qty FLOAT NOT NULL, 
	due_date DATE NOT NULL, 
	status VARCHAR(20) NOT NULL, 
	price FLOAT NOT NULL, 
	business_id INTEGER NOT NULL, 
	PRIMARY KEY (id), 
	FOREIGN KEY(customer_id) REFERENCES customers (id), 
	FOREIGN KEY(product_id) REFERENCES products (id), 
	FOREIGN KEY(business_id) REFERENCES businesses (id)
);

CREATE INDEX ix_orders_business_id ON orders (business_id);

CREATE TABLE outbox (
	id SERIAL NOT NULL, 
	action_id INTEGER, 
	channel VARCHAR(20) NOT NULL, 
	recipient VARCHAR(200) NOT NULL, 
	subject VARCHAR(300) NOT NULL, 
	body TEXT NOT NULL, 
	status VARCHAR(20) NOT NULL, 
	created_at TIMESTAMP WITH TIME ZONE NOT NULL, 
	business_id INTEGER NOT NULL, 
	PRIMARY KEY (id), 
	FOREIGN KEY(action_id) REFERENCES actions (id), 
	FOREIGN KEY(business_id) REFERENCES businesses (id)
);

CREATE INDEX ix_outbox_business_id ON outbox (business_id);

CREATE TABLE purchase_orders (
	id SERIAL NOT NULL, 
	supplier_id INTEGER NOT NULL, 
	items_json JSON NOT NULL, 
	total FLOAT NOT NULL, 
	status VARCHAR(20) NOT NULL, 
	expected_on DATE, 
	due_on DATE, 
	created_at TIMESTAMP WITH TIME ZONE NOT NULL, 
	business_id INTEGER NOT NULL, 
	PRIMARY KEY (id), 
	FOREIGN KEY(supplier_id) REFERENCES suppliers (id), 
	FOREIGN KEY(business_id) REFERENCES businesses (id)
);

CREATE INDEX ix_purchase_orders_business_id ON purchase_orders (business_id);

CREATE TABLE routing_steps (
	id SERIAL NOT NULL, 
	product_id INTEGER NOT NULL, 
	seq INTEGER NOT NULL, 
	stage VARCHAR(50) NOT NULL, 
	machine_type VARCHAR(50) NOT NULL, 
	minutes_per_unit FLOAT NOT NULL, 
	business_id INTEGER NOT NULL, 
	PRIMARY KEY (id), 
	FOREIGN KEY(product_id) REFERENCES products (id), 
	FOREIGN KEY(business_id) REFERENCES businesses (id)
);

CREATE INDEX ix_routing_steps_business_id ON routing_steps (business_id);

CREATE TABLE bom_items (
	id SERIAL NOT NULL, 
	product_id INTEGER NOT NULL, 
	material_id INTEGER NOT NULL, 
	qty_per_unit FLOAT NOT NULL, 
	wastage_pct FLOAT, 
	business_id INTEGER NOT NULL, 
	PRIMARY KEY (id), 
	FOREIGN KEY(product_id) REFERENCES products (id), 
	FOREIGN KEY(material_id) REFERENCES materials (id), 
	FOREIGN KEY(business_id) REFERENCES businesses (id)
);

CREATE INDEX ix_bom_items_business_id ON bom_items (business_id);

CREATE TABLE invoices (
	id SERIAL NOT NULL, 
	number VARCHAR(50), 
	customer_id INTEGER NOT NULL, 
	order_id INTEGER, 
	amount FLOAT NOT NULL, 
	gst_amount FLOAT NOT NULL, 
	issued_on DATE NOT NULL, 
	due_on DATE NOT NULL, 
	paid_amount FLOAT NOT NULL, 
	status VARCHAR(20) NOT NULL, 
	source VARCHAR(200) NOT NULL, 
	business_id INTEGER NOT NULL, 
	PRIMARY KEY (id), 
	FOREIGN KEY(customer_id) REFERENCES customers (id), 
	FOREIGN KEY(order_id) REFERENCES orders (id), 
	FOREIGN KEY(business_id) REFERENCES businesses (id)
);

CREATE INDEX ix_invoices_business_id ON invoices (business_id);


-- Row-level security: enable before more than one business shares this database.
-- Example (requires Supabase Auth with a business_id claim):
--   alter table orders enable row level security;
--   create policy biz_isolation on orders
--     using (business_id = (auth.jwt() ->> 'business_id')::int);
