"""Deterministic static HTML: all input strings are escaped, no remote assets."""
import html
import json

MAX_HTML = 64*1024*1024


def render(packet):
    def e(value):return html.escape(str(value),quote=True)
    def pre(value):return '<pre>'+e(json.dumps(value,sort_keys=True,ensure_ascii=True,indent=2,allow_nan=False))+'</pre>'
    parts=['<!doctype html><html lang="en"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">',
      '<meta http-equiv="Content-Security-Policy" content="default-src &apos;none&apos;; style-src &apos;unsafe-inline&apos;">',
      '<title>SQLiteFolio migration review</title><style>body{font:16px/1.5 system-ui,sans-serif;max-width:1150px;margin:40px auto;padding:0 24px;color:#192b38;background:#faf9f5}h1{font-size:2.6rem;letter-spacing:-.06em}h2{margin-top:3rem}a{color:#155f78}table{border-collapse:collapse;width:100%;background:white}th,td{text-align:left;padding:10px;border:1px solid #ccd4d7;vertical-align:top}pre{white-space:pre-wrap;overflow-wrap:anywhere;background:#edf1f0;padding:16px;font-size:12px}summary{cursor:pointer;font-weight:600}.notice{padding:16px;border-left:5px solid #ba6818;background:#fff1d9}.label{font-size:.8rem;text-transform:uppercase;letter-spacing:.1em}li{margin:.4rem 0}</style>',
      '<main><p class="label">Trusted sample migration evidence</p><h1>SQLiteFolio</h1>',
      '<p>Scenario: <strong>'+e(packet['input']['scenario'])+'</strong></p>',
      '<p class="notice">These are finite sample observations. A passing packet is not a production-safety verdict. Structural verification checks consistency; source-bound replay is a separate operation.</p>',
      '<h2>Comparison under one source and profile</h2>',pre({'profile':packet['input']['profile'],'source':packet['source'],'input_sha256':packet['input_sha256']}),
      '<table><thead><tr><th>Candidate</th><th>Execution</th><th>Review status</th><th>Snapshot completeness</th></tr></thead><tbody>']
    for n,summary in enumerate(packet['summary']):
        parts.append('<tr><td><a href="#candidate-'+str(n)+'">'+e(summary['name'])+'</a></td><td>'+e(summary['execution'])+'</td><td>'+e(summary['status'])+'</td><td>'+e(summary['complete'])+'</td></tr>')
    parts.append('</tbody></table><p>No ranking or automatic candidate selection. Changes can be intentional; review the exact SQL and your named expectations.</p>')
    for n,(raw,summary) in enumerate(zip(packet['candidates'],packet['summary'])):
        ident='candidate-'+str(n)
        parts += ['<section id="'+ident+'"><h2>'+e(raw['name'])+'</h2><h3>Execution and runtime</h3>',pre({'execution':raw['execution'],'runtime':raw['runtime']}),
          '<p>Each change count below is a common-column multiset projection. Added and removed columns are shown separately; no row correspondence is inferred.</p>']
        for phase,label in (('observed','Observed in-session'),('reopened','Reopened after controlled close')):
            parts += ['<h3>'+label+'</h3><p><a href="#'+ident+'-'+phase+'">Underlying raw facts and diagnostics</a></p>',
                      '<h4>Named checks</h4>',pre(summary[phase+'_checks']),'<h4>Schema and projected typed-row changes</h4>',pre(summary[phase+'_changes']),
                      '<details id="'+ident+'-'+phase+'"><summary>Raw '+phase+' snapshot</summary>',pre(raw[phase]),'</details>']
        parts += ['<details><summary>Baseline snapshot</summary>',pre(raw['before']),'</details>',
                  '<details><summary>Raw invariant queries in all phases</summary>',pre(raw['queries']),'</details></section>']
    parts += ['<h2>Declared contract and input identities</h2>',pre(packet['input']),pre(packet['files']),
              '<h2>Limits of interpretation</h2><ul>']
    parts += ['<li>'+e(item)+'</li>' for item in packet['limitations']]
    parts += ['</ul><p>Atlas migration tests, Alembic assertions, sqlite-utils schema diffs and capable SQLite inspection recipes deserve full capability credit. This packet integrates review evidence; no detection, performance or adoption superiority is claimed.</p></main></html>']
    result='\n'.join(parts)
    if len(result.encode('utf-8'))>MAX_HTML:raise ValueError('rendered HTML exceeds byte cap')
    return result
