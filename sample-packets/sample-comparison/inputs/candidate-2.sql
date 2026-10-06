BEGIN;
CREATE TABLE replacement(id INTEGER PRIMARY KEY, sku TEXT NOT NULL, quantity INTEGER NOT NULL CHECK(quantity >= 0));
INSERT INTO replacement SELECT * FROM inventory;
DROP TABLE inventory;
ALTER TABLE replacement RENAME TO inventory;
CREATE INDEX inventory_sku ON inventory(sku);
CREATE TRIGGER inventory_update AFTER UPDATE OF quantity ON inventory BEGIN
  INSERT INTO audit VALUES(new.id, new.quantity);
END;
COMMIT;
