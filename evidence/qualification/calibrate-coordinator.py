import json,resource,tempfile,time
from pathlib import Path
from sqlitefolio.paths import load_inputs
from sqlitefolio.packet import build_packet,CapacityError
with tempfile.TemporaryDirectory() as d:
 p=Path(d)
 (p/'schema.sql').write_text('CREATE TABLE t(x);')
 (p/'seed.sql').write_text('WITH RECURSIVE c(n) AS(VALUES(1) UNION ALL SELECT n+1 FROM c WHERE n<40) INSERT INTO t SELECT zeroblob(65536) FROM c;')
 (p/'migration.sql').write_text('SELECT 1;')
 m={'format':'sqlitefolio.input.v1','scenario':'coordinator-cap','source':{'schema':'schema.sql','seed':'seed.sql'},'profile':{'foreign_keys':True,'transaction_mode':'autocommit'},'candidates':[{'name':f'copy{i}','sql':'migration.sql'} for i in range(8)],'invariants':[]}
 (p/'manifest.json').write_text(json.dumps(m));before={x.name:x.read_bytes() for x in p.iterdir()};start=time.monotonic()
 try:build_packet(load_inputs(p/'manifest.json'));out={'refused':False}
 except CapacityError as e:out={'refused':True,'message':str(e)}
 out.update(elapsed_seconds=time.monotonic()-start,parent_peak_rss_kib=resource.getrusage(resource.RUSAGE_SELF).ru_maxrss,worker_peak_rss_kib=resource.getrusage(resource.RUSAGE_CHILDREN).ru_maxrss,source_preserved=before=={x.name:x.read_bytes() for x in p.iterdir()})
 print(json.dumps(out,indent=2))
 assert out['refused'] and out['source_preserved']
