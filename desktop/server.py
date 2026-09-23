"""A private process group owned by the native window; no browser or Terminal."""
import os,sys
from pathlib import Path

def main():
    if len(sys.argv)!=2 or not sys.argv[1].isdigit() or not 1024<int(sys.argv[1])<65536:
        raise SystemExit('A valid local TCP port is required')
    root=Path(__file__).resolve().parents[1]
    try:os.setsid()
    except PermissionError:pass  # Already the leader of an independent process group.
    os.chdir(root)
    os.execv('/bin/bash',['/bin/bash',str(root/'start.sh'),'--server.port='+sys.argv[1],'--server.headless=true','--server.fileWatcherType=none'])

if __name__=='__main__':main()
