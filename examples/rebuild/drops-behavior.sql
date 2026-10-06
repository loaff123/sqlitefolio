BEGIN;
CREATE TABLE replacement(id INTEGER PRIMARY KEY, sku TEXT NOT NULL, quantity INTEGER NOT NULL);
INSERT INTO replacement SELECT * FROM inventory;
DROP TABLE inventory;
ALTER TABLE replacement RENAME TO inventory;
COMMIT;
