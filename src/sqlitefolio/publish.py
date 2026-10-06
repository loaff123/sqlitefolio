"""Linux atomic directory publication. There is no replace fallback."""
import ctypes
import errno
import os
from pathlib import Path
import secrets
import shutil
from .paths import no_symlinks


class PublicationError(OSError):
    def __init__(self, message, published=False):
        super().__init__(message)
        self.published = published


def _rename_noreplace(parent_fd, old, new):
    libc = ctypes.CDLL(None, use_errno=True)
    try:
        rename = libc.renameat2
    except AttributeError as exc:
        raise OSError(errno.ENOSYS, 'renameat2 unavailable; no unsafe fallback') from exc
    rename.argtypes = [ctypes.c_int,ctypes.c_char_p,ctypes.c_int,ctypes.c_char_p,ctypes.c_uint]
    rename.restype = ctypes.c_int
    if rename(parent_fd,os.fsencode(old),parent_fd,os.fsencode(new),1) != 0:
        error = ctypes.get_errno()
        raise OSError(error, os.strerror(error))


def publish(files, output):
    """Publish bytes exactly once; failures carry truthful post-rename state."""
    output = no_symlinks(Path(output))
    parent_fd = None; stage_fd = None; stage_name = None; published = False; stage_owned = False
    try:
        parent_fd = os.open(output.parent,os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW)
        stage_name = '.sqlitefolio-stage-' + secrets.token_hex(16)
        os.mkdir(stage_name,mode=0o700,dir_fd=parent_fd)
        stage_owned = True
        stage_fd = os.open(stage_name,os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW,dir_fd=parent_fd)
        subdirs = {}
        try:
            for name, data in sorted(files.items()):
                pieces = name.split('/')
                if len(pieces)>2 or any(p in ('','.','..') for p in pieces) or '\\' in name or type(data) is not bytes:
                    raise ValueError('invalid owned packet file')
                dest_fd=stage_fd
                if len(pieces)==2:
                    directory=pieces[0]
                    if directory not in subdirs:
                        os.mkdir(directory,mode=0o700,dir_fd=stage_fd)
                        subdirs[directory]=os.open(directory,os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW,dir_fd=stage_fd)
                    dest_fd=subdirs[directory]
                fd=os.open(pieces[-1],os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW,0o600,dir_fd=dest_fd)
                try:
                    view=memoryview(data)
                    while view:
                        count=os.write(fd,view)
                        if count<=0:raise OSError('short packet write')
                        view=view[count:]
                    os.fsync(fd)
                finally:os.close(fd)
            for fd in subdirs.values():os.fsync(fd)
            os.fsync(stage_fd)
        finally:
            for fd in subdirs.values():os.close(fd)
        _rename_noreplace(parent_fd,stage_name,output.name)
        published=True
        os.fsync(parent_fd)
    except (OSError, ValueError) as exc:
        suffix = '; packet directory was published and retained' if published else '; no packet published'
        raise PublicationError('packet publication failed: '+str(exc)+suffix,published) from exc
    finally:
        if stage_fd is not None:os.close(stage_fd)
        if parent_fd is not None:
            if stage_owned and not published:
                try:shutil.rmtree(stage_name,dir_fd=parent_fd)
                except FileNotFoundError:pass
            os.close(parent_fd)
