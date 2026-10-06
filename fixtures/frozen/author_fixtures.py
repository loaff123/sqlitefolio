"""Original, hand-authored benign fixtures and expected facts; no prototype import.

This authoring record is retained to distinguish predictions from measurements.
Run only before freezing. It refuses to replace an existing expected.json.
"""
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent
CASES = []
EXPECTED = {}


def check(name, sql, rows):
    return {"name": name, "sql": sql, "expect_rows": rows}


def observation(name, sql, rows=None, error_contains=None, connection="live"):
    result = {"name": name, "sql": sql, "connection": connection}
    if error_contains is not None:
        result["error_contains"] = error_contains
    else:
        result["expect_rows"] = rows
    return result


def add(case_id, scenario, description, schema, seed, migration, checks,
        actual_checks, observations, *, foreign_keys=True, status="applied",
        in_transaction=False, error_contains=None, foreign_keys_end=None,
        diagnoses=None):
    CASES.append({
        "id": case_id, "scenario": scenario, "description": description,
        "schema_sql": schema.strip() + "\n", "seed_sql": seed.strip() + "\n",
        "migration_sql": migration.strip() + "\n", "foreign_keys": foreign_keys,
        "checks": checks,
    })
    outcomes = []
    for c, actual in zip(checks, actual_checks, strict=True):
        if isinstance(actual, dict):
            outcomes.append({"name": c["name"], "passed": False, **actual})
        else:
            outcomes.append({"name": c["name"], "actual_rows": actual,
                             "passed": actual == c["expect_rows"]})
    EXPECTED[case_id] = {
        "status": status, "in_transaction": in_transaction,
        "error_contains": error_contains,
        "foreign_keys_end": foreign_keys if foreign_keys_end is None else foreign_keys_end,
        "diagnoses": diagnoses or [], "check_outcomes": outcomes,
        "observations": observations,
    }


add("01_safe_additive", "safe_evolution", "Add a non-null column with default and an index without losing rows.",
    "CREATE TABLE projects (id INTEGER PRIMARY KEY, title TEXT NOT NULL);",
    "INSERT INTO projects VALUES (1, 'Orchid'), (2, 'Maple');",
    """ALTER TABLE projects ADD COLUMN archived INTEGER NOT NULL DEFAULT 0;
CREATE INDEX projects_title_idx ON projects(title);""",
    [check("project_values", "SELECT id, title, archived FROM projects ORDER BY id", [[1,"Orchid",0],[2,"Maple",0]]),
     check("title_index", "SELECT name FROM sqlite_schema WHERE type='index' AND name='projects_title_idx'", [["projects_title_idx"]])],
    [[[1,"Orchid",0],[2,"Maple",0]], [["projects_title_idx"]]],
    [observation("committed_defaults", "SELECT id, archived FROM projects ORDER BY id", [[1,0],[2,0]], connection="persisted")])


add("02_rebuild_drops_objects", "rebuild_preservation", "A row-preserving rebuild silently loses a CHECK constraint, index, and audit trigger.",
    """CREATE TABLE inventory (id INTEGER PRIMARY KEY, sku TEXT NOT NULL, quantity INTEGER NOT NULL CHECK(quantity >= 0));
CREATE INDEX inventory_sku_idx ON inventory(sku);
CREATE TABLE audit (item_id INTEGER, old_quantity INTEGER, new_quantity INTEGER);
CREATE TRIGGER inventory_quantity_audit AFTER UPDATE OF quantity ON inventory
BEGIN INSERT INTO audit VALUES(OLD.id, OLD.quantity, NEW.quantity); END;""",
    "INSERT INTO inventory VALUES (1, 'PN-8', 7), (2, 'PN-9', 12);",
    """BEGIN;
CREATE TABLE inventory_new (id INTEGER PRIMARY KEY, sku TEXT NOT NULL, quantity INTEGER NOT NULL);
INSERT INTO inventory_new SELECT * FROM inventory;
DROP TABLE inventory;
ALTER TABLE inventory_new RENAME TO inventory;
COMMIT;""",
    [check("inventory_rows", "SELECT * FROM inventory ORDER BY id", [[1,"PN-8",7],[2,"PN-9",12]]),
     check("secondary_objects_preserved", "SELECT type, name FROM sqlite_schema WHERE tbl_name='inventory' AND type IN ('index','trigger') ORDER BY type, name", [["index","inventory_sku_idx"],["trigger","inventory_quantity_audit"]]),
     check("quantity_check_preserved", "SELECT instr(upper(sql), 'CHECK') > 0 FROM sqlite_schema WHERE type='table' AND name='inventory'", [[1]])],
    [[[1,"PN-8",7],[2,"PN-9",12]], [], [[0]]],
    [observation("audit_unchanged", "SELECT count(*) FROM audit", [[0]])],
    diagnoses=["Index dropped", "Audit trigger dropped", "CHECK constraint dropped despite unchanged rows"])


FK_SCHEMA = """CREATE TABLE customers (id INTEGER PRIMARY KEY, name TEXT NOT NULL);
CREATE TABLE orders (id INTEGER PRIMARY KEY, customer_id INTEGER NOT NULL REFERENCES customers(id) ON DELETE CASCADE, total INTEGER NOT NULL);"""
FK_SEED = "INSERT INTO customers VALUES (1, 'Iris'), (2, 'Fern'); INSERT INTO orders VALUES (11, 1, 80), (12, 2, 45);"
FK_CHECKS = [check("customer_rows", "SELECT id, name FROM customers ORDER BY id", [[1,"Iris"],[2,"Fern"]]),
             check("order_rows", "SELECT * FROM orders ORDER BY id", [[11,1,80],[12,2,45]]),
             check("foreign_key_clean", "PRAGMA foreign_key_check", [])]
REBUILD_PARENT = """BEGIN;
CREATE TABLE customers_new (id INTEGER PRIMARY KEY, name TEXT NOT NULL, active INTEGER NOT NULL DEFAULT 1);
INSERT INTO customers_new(id, name) SELECT id, name FROM customers;
DROP TABLE customers;
ALTER TABLE customers_new RENAME TO customers;
COMMIT;"""
add("03_fk_cascade_loss", "referential_integrity", "Dropping a referenced parent with foreign_keys on deletes child rows even when parent rows are copied back.",
    FK_SCHEMA, FK_SEED, REBUILD_PARENT, FK_CHECKS,
    [[[1,"Iris"],[2,"Fern"]], [], []],
    [observation("persisted_child_loss", "SELECT count(*) FROM orders", [[0]], connection="persisted")],
    diagnoses=["Two child rows lost to ON DELETE CASCADE", "foreign_key_check is clean and does not prove row preservation"])
add("04_fk_off_copy_correct", "referential_integrity", "Explicitly disable FK enforcement outside the rebuild transaction and re-enable after commit; preserve children.",
    FK_SCHEMA, FK_SEED, "PRAGMA foreign_keys=OFF;\n" + REBUILD_PARENT + "\nPRAGMA foreign_keys=ON;", FK_CHECKS,
    [[[1,"Iris"],[2,"Fern"]], [[11,1,80],[12,2,45]], []],
    [observation("persisted_children", "SELECT * FROM orders ORDER BY id", [[11,1,80],[12,2,45]], connection="persisted"),
     observation("new_default", "SELECT id, active FROM customers ORDER BY id", [[1,1],[2,1]])])
add("05_fk_off_orphan", "referential_integrity", "A migration under FK-off execution deletes a parent without cascading, creating an orphan.",
    FK_SCHEMA, FK_SEED, "DELETE FROM customers WHERE id=2;",
    [check("foreign_key_clean", "PRAGMA foreign_key_check", []),
     check("order_rows", "SELECT * FROM orders ORDER BY id", [[11,1,80],[12,2,45]])],
    [[["orders",12,"customers",0]], [[11,1,80],[12,2,45]]],
    [observation("remaining_parent", "SELECT * FROM customers ORDER BY id", [[1,"Iris"]]),
     observation("orphan_row", "SELECT o.id FROM orders o LEFT JOIN customers c ON c.id=o.customer_id WHERE c.id IS NULL", [[12]])],
    foreign_keys=False, diagnoses=["Orphan order 12 references missing customer 2"])


add("06_lossy_cast", "value_preservation", "Explicit text-to-integer casts erase formatting, truncate fractions, and turn nonnumeric text into zero.",
    "CREATE TABLE samples (id INTEGER PRIMARY KEY, reading TEXT NOT NULL);",
    "INSERT INTO samples VALUES (1,'007'),(2,'19.75'),(3,'unknown'),(4,'-3');",
    """BEGIN;
CREATE TABLE samples_new (id INTEGER PRIMARY KEY, reading INTEGER NOT NULL);
INSERT INTO samples_new SELECT id, CAST(reading AS INTEGER) FROM samples;
DROP TABLE samples;
ALTER TABLE samples_new RENAME TO samples;
COMMIT;""",
    [check("readings_preserved", "SELECT id, CAST(reading AS TEXT) FROM samples ORDER BY id", [[1,"007"],[2,"19.75"],[3,"unknown"],[4,"-3"]]),
     check("row_count", "SELECT count(*) FROM samples", [[4]])],
    [[[1,"7"],[2,"19"],[3,"0"],[4,"-3"]], [[4]]],
    [observation("storage_class", "SELECT id, reading, typeof(reading) FROM samples ORDER BY id", [[1,7,"integer"],[2,19,"integer"],[3,0,"integer"],[4,-3,"integer"]])],
    diagnoses=["Three textual readings changed", "Equal row counts do not prove value preservation"])


EMAIL_SCHEMA = "CREATE TABLE contacts (id INTEGER PRIMARY KEY, email TEXT NOT NULL);"
EMAIL_SEED = "INSERT INTO contacts VALUES (1,'Sam@example.test'),(2,'sam@example.test');"
COLLISION = """ALTER TABLE contacts ADD COLUMN normalized TEXT;
UPDATE contacts SET normalized=lower(email);
CREATE UNIQUE INDEX contacts_normalized_uq ON contacts(normalized);
CREATE TABLE migration_finished (done INTEGER);"""
COLLISION_CHECKS = [check("unique_index_exists", "SELECT name FROM sqlite_schema WHERE type='index' AND name='contacts_normalized_uq'", [["contacts_normalized_uq"]]),
                    check("no_duplicate_normalized", "SELECT normalized, count(*) FROM contacts GROUP BY normalized HAVING count(*)>1", []),
                    check("finished_marker", "SELECT name FROM sqlite_schema WHERE type='table' AND name='migration_finished'", [["migration_finished"]])]
COLLISION_ACTUAL = [[], [["sam@example.test",2]], []]
add("07_unique_collision_partial", "failure_transaction", "Without a script transaction, preceding ALTER and UPDATE persist when CREATE UNIQUE INDEX fails.",
    EMAIL_SCHEMA, EMAIL_SEED, COLLISION, COLLISION_CHECKS, COLLISION_ACTUAL,
    [observation("persisted_partial_rows", "SELECT * FROM contacts ORDER BY id", [[1,"Sam@example.test","sam@example.test"],[2,"sam@example.test","sam@example.test"]], connection="persisted")],
    status="error", error_contains="UNIQUE constraint failed: contacts.normalized",
    diagnoses=["Application failure", "Earlier statements persist", "Later statements do not execute"])
add("08_unique_collision_transaction", "failure_transaction", "An explicit transaction remains open on uniqueness failure; local uncommitted changes differ from persistent state.",
    EMAIL_SCHEMA, EMAIL_SEED, "BEGIN;\n" + COLLISION + "\nCOMMIT;", COLLISION_CHECKS, COLLISION_ACTUAL,
    [observation("persisted_original_rows", "SELECT * FROM contacts ORDER BY id", [[1,"Sam@example.test"],[2,"sam@example.test"]], connection="persisted"),
     observation("live_uncommitted_rows", "SELECT * FROM contacts ORDER BY id", [[1,"Sam@example.test","sam@example.test"],[2,"sam@example.test","sam@example.test"]])],
    status="error", in_transaction=True, error_contains="UNIQUE constraint failed: contacts.normalized",
    diagnoses=["Application failure", "Transaction remains open", "Do not call local observations committed; closing rolls back"])
add("09_uncommitted_additive", "failure_transaction", "A syntactically successful script forgets COMMIT; its apparent success is uncommitted.",
    "CREATE TABLE notes (id INTEGER PRIMARY KEY, body TEXT);", "INSERT INTO notes VALUES(1,'draft');",
    "BEGIN; ALTER TABLE notes ADD COLUMN reviewed INTEGER NOT NULL DEFAULT 0; UPDATE notes SET reviewed=1 WHERE id=1;",
    [check("reviewed_note", "SELECT * FROM notes ORDER BY id", [[1,"draft",1]])],
    [[[1,"draft",1]]],
    [observation("persisted_original_rows", "SELECT * FROM notes ORDER BY id", [[1,"draft"]], connection="persisted")],
    status="open_transaction", in_transaction=True,
    diagnoses=["No SQL exception, but transaction remains open", "Local passing checks do not establish durable application"])


add("10_trigger_semicolons", "safe_evolution", "A trigger body has multiple statements and a string literal containing a semicolon; execute the entire script.",
    "CREATE TABLE counters (id INTEGER PRIMARY KEY, value INTEGER NOT NULL); CREATE TABLE changes (counter_id INTEGER, detail TEXT);",
    "INSERT INTO counters VALUES(1,10);",
    """CREATE TRIGGER log_counter AFTER UPDATE OF value ON counters
BEGIN
  INSERT INTO changes VALUES(NEW.id, 'first;entry');
  INSERT INTO changes VALUES(NEW.id, 'value=' || NEW.value);
END;
UPDATE counters SET value=value+1 WHERE id=1;""",
    [check("counter_value", "SELECT * FROM counters", [[1,11]]),
     check("trigger_statements", "SELECT counter_id, detail FROM changes ORDER BY rowid", [[1,"first;entry"],[1,"value=11"]]),
     check("trigger_exists", "SELECT name FROM sqlite_schema WHERE type='trigger'", [["log_counter"]])],
    [[[1,11]], [[1,"first;entry"],[1,"value=11"]], [["log_counter"]]],
    [observation("persisted_trigger_rows", "SELECT * FROM changes ORDER BY rowid", [[1,"first;entry"],[1,"value=11"]], connection="persisted")])


add("11_invalid_view", "dependent_schema", "Recreate the source table under the same name but omit the column referenced by a surviving view.",
    "CREATE TABLE shipments (id INTEGER PRIMARY KEY, old_code TEXT); CREATE VIEW shipping_codes AS SELECT id, old_code FROM shipments;",
    "INSERT INTO shipments VALUES(1,'BOX-A');",
    "DROP TABLE shipments; CREATE TABLE shipments(id INTEGER PRIMARY KEY, new_code TEXT); INSERT INTO shipments VALUES(1,'BOX-A');",
    [check("shipping_view_usable", "SELECT * FROM shipping_codes ORDER BY id", [[1,"BOX-A"]]),
     check("source_row_preserved", "SELECT * FROM shipments ORDER BY id", [[1,"BOX-A"]])],
    [{"error_contains":"no such column: old_code"}, [[1,"BOX-A"]]],
    [observation("view_object_survives", "SELECT type,name FROM sqlite_schema WHERE name='shipping_codes'", [["view","shipping_codes"]]),
     observation("persisted_view_invalid", "SELECT * FROM shipping_codes", error_contains="no such column: old_code", connection="persisted")],
    diagnoses=["SQL application succeeded", "Surviving view cannot be prepared"])


add("12_without_rowid_composite", "safe_evolution", "Composite primary keys in a WITHOUT ROWID table remain usable after additive evolution.",
    "CREATE TABLE settings (tenant TEXT NOT NULL, key TEXT NOT NULL, value TEXT, PRIMARY KEY(tenant,key)) WITHOUT ROWID;",
    "INSERT INTO settings VALUES('north','theme','dark'),('north','units','metric'),('south','theme','light');",
    "ALTER TABLE settings ADD COLUMN revision INTEGER NOT NULL DEFAULT 1;",
    [check("composite_rows", "SELECT tenant,key,value,revision FROM settings ORDER BY tenant,key", [["north","theme","dark",1],["north","units","metric",1],["south","theme","light",1]]),
     check("composite_key", "SELECT name,pk FROM pragma_table_info('settings') WHERE pk>0 ORDER BY pk", [["tenant",1],["key",2]]),
     check("without_rowid_preserved", "SELECT instr(upper(sql),'WITHOUT ROWID')>0 FROM sqlite_schema WHERE name='settings'", [[1]])],
    [[["north","theme","dark",1],["north","units","metric",1],["south","theme","light",1]], [["tenant",1],["key",2]], [[1]]],
    [observation("no_rowid", "SELECT rowid FROM settings", error_contains="no such column: rowid")])


if __name__ == "__main__":
    if (ROOT / "expected.json").exists():
        raise SystemExit("Refusing to replace existing predictions; preserve a correction record explicitly.")
    for case in CASES:
        (ROOT / (case["id"] + ".json")).write_text(json.dumps(case, indent=2) + "\n")
    (ROOT / "expected.json").write_text(json.dumps({
        "provenance": "Handwritten predictions authored before running the independent oracle or any prototype.",
        "status_rule": "A migration exception gives status error. With no exception, an open transaction gives open_transaction, otherwise applied. in_transaction is independently reported even on error.",
        "check_rule": "Fixture expect_rows are desired invariants, including deliberately failing invariants; check_outcomes below predict the actually observed rows or error.",
        "cases": EXPECTED,
    }, indent=2) + "\n")
    print(f"Wrote {len(CASES)} original cases and handwritten predictions")
