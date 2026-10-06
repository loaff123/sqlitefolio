CREATE TABLE inventory(id INTEGER PRIMARY KEY, sku TEXT NOT NULL, quantity INTEGER NOT NULL CHECK(quantity >= 0));
CREATE INDEX inventory_sku ON inventory(sku);
CREATE TABLE audit(item_id INTEGER, quantity INTEGER);
CREATE TRIGGER inventory_update AFTER UPDATE OF quantity ON inventory BEGIN
  INSERT INTO audit VALUES(new.id, new.quantity);
END;
