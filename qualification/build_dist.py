"""Build with setuptools, then normalize sdist ownership/time for reproducibility."""
import gzip
import io
import os
from pathlib import Path
import tarfile

EPOCH = 1791158400  # 2026-10-05 00:00:00 UTC


def main():
    from setuptools.build_meta import build_wheel, build_sdist
    os.environ['SOURCE_DATE_EPOCH'] = str(EPOCH)
    destination = Path('dist');destination.mkdir(exist_ok=True)
    wheel=build_wheel(str(destination));sdist=build_sdist(str(destination))
    original=(destination/sdist).read_bytes()
    buffer=io.BytesIO()
    with tarfile.open(fileobj=io.BytesIO(original),mode='r:gz') as source:
        with gzip.GzipFile(fileobj=buffer,mode='wb',filename='',mtime=EPOCH,compresslevel=9) as compressed:
            with tarfile.open(fileobj=compressed,mode='w',format=tarfile.PAX_FORMAT) as target:
                for item in sorted(source.getmembers(),key=lambda item:item.name):
                    item.uid=item.gid=0;item.uname=item.gname='';item.mtime=EPOCH;item.pax_headers={}
                    item.mode=0o755 if item.isdir() else 0o644
                    target.addfile(item,source.extractfile(item) if item.isfile() else None)
    (destination/sdist).write_bytes(buffer.getvalue())
    print(wheel);print(sdist)


if __name__=='__main__':main()
