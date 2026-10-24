"""Trusted OS-owner password recovery. Stop the application before use."""
import argparse
import asyncio
import getpass
from pathlib import Path
from access_control import AccessControl
from runtime_lock import acquire

async def reset(folder,name):
    if not (folder/'security/users.sqlite3').is_file():raise ValueError('No security database at the selected data directory')
    with acquire(folder/'app.lock'):
        store=AccessControl(folder/'security')
        try:
            if not store.enabled:raise ValueError('Authentication is not enabled in this workspace')
            if not any(u['name']==name and u['role']=='Administrator' for u in store.users()):raise ValueError('Choose an existing administrator account')
            first=getpass.getpass('New password (12–128 characters): ')
            if first!=getpass.getpass('Confirm new password: '):raise ValueError('Passwords do not match')
            await store.put_user(name,'Administrator',first,True)
            # All server sessions are process-local and have already ended on shutdown.
            print('Administrator password reset. Start the application and sign in.')
        finally:store.close()

if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--data',type=Path,required=True);parser.add_argument('--username',required=True);args=parser.parse_args()
    try:asyncio.run(reset(args.data,args.username))
    except (ValueError,RuntimeError) as error:parser.exit(1,str(error)+'\n')
