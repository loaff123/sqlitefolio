"""Local-only command line. No production or publication-to-network command."""
import argparse
import json
import os
from pathlib import Path
import sys
from .contract import InputError, canonical
from .packet import CapacityError, rehearse, replay
from .publish import PublicationError
from .paths import bounded_read, sha
from .runner import runtime_profile, runtime_supported


def emit(value, *, raw=False, fd=1):
    data=value.encode('utf-8') if raw else canonical(value)+b'\n'
    view=memoryview(data)
    while view:
        size=os.write(fd,view)
        if size<=0:raise OSError('short output write')
        view=view[size:]


def parser():
    p=argparse.ArgumentParser(prog='sqlitefolio',description='Review trusted sample SQLite migrations locally; never apply to production.')
    sub=p.add_subparsers(dest='command',required=True)
    sub.add_parser('profile',help='show actual runtime and qualification match')
    r=sub.add_parser('rehearse',help='execute trusted sample candidates in disposable copies')
    r.add_argument('manifest',type=Path);r.add_argument('--out',required=True,type=Path)
    r.add_argument('--trust-input',action='store_true');r.add_argument('--include-sample',action='store_true')
    for command in ('inspect','verify'):
        v=sub.add_parser(command);v.add_argument('packet',type=Path)
    r=sub.add_parser('replay',help='explicit source-bound rerun, distinct from structural verification')
    r.add_argument('packet',type=Path);r.add_argument('--manifest',required=True,type=Path);r.add_argument('--trust-input',action='store_true')
    return p


def main(argv=None):
    args=parser().parse_args(argv)
    try:
        if args.command=='profile':
            emit({'supported':runtime_supported(),'runtime':runtime_profile()});return 0 if runtime_supported() else 2
        if args.command=='rehearse':
            result=rehearse(args.manifest,args.out,trust_input=args.trust_input,include_sample=args.include_sample)
            emit(result);return result['verification']['exit_code']
        if args.command=='replay':
            result=replay(args.packet,args.manifest,trust_input=args.trust_input)
            emit(result);return result['exit_code']
        from .verify import verify_packet
        result=verify_packet(args.packet)
        if args.command=='verify' or not result['valid']:
            emit(result);return result['exit_code']
        text,_=bounded_read(args.packet/'report.html',64*1024*1024)
        if sha(text)!=result['report_sha256']:
            emit({'valid':False,'exit_code':6,'message':'report changed after verification'},fd=2);return 6
        emit(text.decode('utf-8'),raw=True);return result['exit_code']
    except InputError as exc:
        error={'error':'invalid_or_unsupported_input','message':str(exc)};code=2
    except CapacityError as exc:
        error={'error':'resource_limit','message':str(exc)};code=4
    except PublicationError as exc:
        error={'error':'publication_io','published':exc.published,'message':str(exc)};code=7
    except OSError as exc:
        error={'error':'io_or_closed_output','message':str(exc)};code=7
    except (ValueError,TypeError,KeyError,RuntimeError) as exc:
        error={'error':'internal_protocol_error','message':str(exc)};code=5
    try:emit(error,fd=2)
    except OSError:pass
    return code
