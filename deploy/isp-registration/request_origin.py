"""Same-origin checks for the configured private retro portal aliases."""
from urllib.parse import urlsplit

HOSTS={'10.150.150.157','eicon420.net.cryan.nz','eicon420'}


def same_origin(origin,host,port):
    if not origin:
        # Legacy browsers omit Origin; one-use/session CSRF tokens still apply.
        return True
    try:
        source=urlsplit(origin)
        target=urlsplit('http://'+host)
        source_host=(source.hostname or '').lower().rstrip('.')
        target_host=(target.hostname or '').lower().rstrip('.')
        return (source.scheme=='http' and source_host in HOSTS and source_host==target_host and
                (source.port or 80)==port and (target.port or 80)==port and
                not source.username and not source.password and source.path in ('','/') and
                not source.query and not source.fragment)
    except ValueError:
        return False
